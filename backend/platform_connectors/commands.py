# platform_connectors/commands.py
"""
Command layer for platform settlement operations.

Handles payouts, disputes, fees, and adjustments from any platform.
Each settlement creates a journal entry through the accounting commands.
"""

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounting.commands import (
    CommandResult,
    create_journal_entry,
    post_journal_entry,
    save_journal_entry_complete,
)
from accounting.mappings import ModuleAccountMapping
from accounts.authz import system_actor_for_company
from projections.write_barrier import command_writes_allowed

from .models import PlatformSettlement

logger = logging.getLogger(__name__)


# Settlement type → account role mapping
# Each settlement type defines which ModuleAccountMapping roles to use
# for debit, credit, and fee accounts.
SETTLEMENT_ACCOUNT_ROLES = {
    PlatformSettlement.SettlementType.PAYOUT: {
        "debit": "CASH_BANK",
        "credit": "SHOPIFY_CLEARING",  # Will be overridden per-platform
        "fee": "PAYMENT_PROCESSING_FEES",
    },
    PlatformSettlement.SettlementType.FEE: {
        "debit": "PAYMENT_PROCESSING_FEES",
        "credit": "SHOPIFY_CLEARING",
    },
    PlatformSettlement.SettlementType.DISPUTE: {
        "debit": "CHARGEBACK_EXPENSE",
        "credit": "SHOPIFY_CLEARING",
        "fee": "PAYMENT_PROCESSING_FEES",
    },
    PlatformSettlement.SettlementType.DISPUTE_WON: {
        "debit": "SHOPIFY_CLEARING",
        "credit": "CHARGEBACK_EXPENSE",
        "fee": "PAYMENT_PROCESSING_FEES",
    },
    PlatformSettlement.SettlementType.ADJUSTMENT: {
        "debit": "SHOPIFY_CLEARING",
        "credit": "SHOPIFY_CLEARING",
    },
}

# Platform → module name mapping for ModuleAccountMapping lookups
PLATFORM_MODULE_MAP = {
    "shopify": "shopify_connector",
    "stripe": "stripe_connector",
}


def _resolve_clearing_role(platform):
    """Get the clearing account role name for a platform."""
    role_map = {
        "shopify": "SHOPIFY_CLEARING",
        "stripe": "STRIPE_CLEARING",
    }
    return role_map.get(platform, f"{platform.upper()}_CLEARING")


@transaction.atomic
def create_and_post_settlement(
    company,
    platform: str,
    platform_document_id: str,
    settlement_type: str,
    gross_amount: Decimal,
    fees: Decimal,
    net_amount: Decimal,
    currency: str,
    settlement_date,
    reference: str = "",
    notes: str = "",
) -> CommandResult:
    """
    Create and post a PlatformSettlement with its journal entry.

    This is the single entry point for all platform financial transactions
    that aren't sales or purchases (payouts, disputes, fees, adjustments).

    The function:
    1. Creates the PlatformSettlement record
    2. Resolves accounts from ModuleAccountMapping
    3. Builds JE lines based on settlement type
    4. Creates and posts the JE via accounting commands
    5. Links the JE back to the settlement

    Args:
        company: Company instance
        platform: Platform identifier ("shopify", "stripe", etc.)
        platform_document_id: External ID for idempotency
        settlement_type: One of PlatformSettlement.SettlementType values
        gross_amount: Amount before fees
        fees: Platform fees
        net_amount: Amount after fees (gross - fees)
        currency: ISO currency code
        settlement_date: Date of the settlement
        reference: Human-readable reference
        notes: Additional notes

    Returns:
        CommandResult with {"settlement": PlatformSettlement, "journal_entry": JournalEntry}
    """
    # Idempotency
    existing = PlatformSettlement.objects.filter(
        company=company,
        platform=platform,
        platform_document_id=platform_document_id,
        settlement_type=settlement_type,
    ).first()
    if existing:
        return CommandResult.ok(
            data={"settlement": existing, "journal_entry": existing.posted_journal_entry},
        )

    # Resolve accounts from module mapping
    module_name = PLATFORM_MODULE_MAP.get(platform, f"{platform}_connector")
    mapping = ModuleAccountMapping.get_mapping(company, module_name)
    if not mapping:
        return CommandResult.fail(
            f"No account mapping found for {module_name}. Connect the {platform} store and complete onboarding first."
        )

    # Get role definitions for this settlement type
    roles = SETTLEMENT_ACCOUNT_ROLES.get(settlement_type)
    if not roles:
        return CommandResult.fail(f"Unknown settlement type: {settlement_type}")

    # Resolve the clearing role for this specific platform
    clearing_role = _resolve_clearing_role(platform)

    # Resolve each account
    debit_role = roles["debit"]
    credit_role = roles["credit"]
    fee_role = roles.get("fee")

    # Replace generic clearing role with platform-specific one
    if debit_role == "SHOPIFY_CLEARING":
        debit_role = clearing_role
    if credit_role == "SHOPIFY_CLEARING":
        credit_role = clearing_role

    debit_account = mapping.get(debit_role)
    credit_account = mapping.get(credit_role)
    fee_account = mapping.get(fee_role) if fee_role else None

    if not debit_account:
        return CommandResult.fail(f"Account mapping missing for role '{debit_role}' in {module_name}")
    if not credit_account:
        return CommandResult.fail(f"Account mapping missing for role '{credit_role}' in {module_name}")

    # Create the settlement record
    with command_writes_allowed():
        settlement = PlatformSettlement.objects.create(
            company=company,
            platform=platform,
            platform_document_id=platform_document_id,
            settlement_type=settlement_type,
            gross_amount=gross_amount,
            fees=fees,
            net_amount=net_amount,
            currency=currency,
            settlement_date=settlement_date,
            status=PlatformSettlement.Status.DRAFT,
            reference=reference,
            notes=notes,
            auto_created=True,
        )

    # Build JE lines
    je_lines = []
    type_label = PlatformSettlement.SettlementType(settlement_type).label
    memo = f"{platform.title()} {type_label}: {reference or platform_document_id}"

    if settlement_type == PlatformSettlement.SettlementType.PAYOUT:
        # DR Bank (net)  +  DR Fees (if any)  /  CR Clearing (gross)
        je_lines.append(
            {
                "account_id": debit_account.id,
                "description": f"{memo} — bank deposit",
                "debit": str(net_amount),
                "credit": "0",
            }
        )
        if fees > 0 and fee_account:
            je_lines.append(
                {
                    "account_id": fee_account.id,
                    "description": f"{memo} — processing fees",
                    "debit": str(fees),
                    "credit": "0",
                }
            )
        je_lines.append(
            {
                "account_id": credit_account.id,
                "description": f"{memo} — clearing",
                "debit": "0",
                "credit": str(gross_amount),
            }
        )

    elif settlement_type == PlatformSettlement.SettlementType.DISPUTE:
        # DR Chargeback Expense (amount)  +  DR Fees (if any)  /  CR Clearing (total)
        total = gross_amount + fees
        je_lines.append(
            {
                "account_id": debit_account.id,
                "description": f"{memo} — chargeback",
                "debit": str(gross_amount),
                "credit": "0",
            }
        )
        if fees > 0 and fee_account:
            je_lines.append(
                {
                    "account_id": fee_account.id,
                    "description": f"{memo} — chargeback fee",
                    "debit": str(fees),
                    "credit": "0",
                }
            )
        je_lines.append(
            {
                "account_id": credit_account.id,
                "description": f"{memo} — clearing",
                "debit": "0",
                "credit": str(total),
            }
        )

    elif settlement_type == PlatformSettlement.SettlementType.DISPUTE_WON:
        # DR Clearing (total)  /  CR Chargeback Expense (amount)  +  CR Fees (if any)
        total = gross_amount + fees
        je_lines.append(
            {
                "account_id": debit_account.id,
                "description": f"{memo} — clearing reversal",
                "debit": str(total),
                "credit": "0",
            }
        )
        je_lines.append(
            {
                "account_id": credit_account.id,
                "description": f"{memo} — chargeback reversal",
                "debit": "0",
                "credit": str(gross_amount),
            }
        )
        if fees > 0 and fee_account:
            je_lines.append(
                {
                    "account_id": fee_account.id,
                    "description": f"{memo} — fee reversal",
                    "debit": "0",
                    "credit": str(fees),
                }
            )

    elif settlement_type == PlatformSettlement.SettlementType.FEE:
        # DR Fee Expense  /  CR Clearing
        je_lines.append(
            {
                "account_id": debit_account.id,
                "description": memo,
                "debit": str(net_amount),
                "credit": "0",
            }
        )
        je_lines.append(
            {
                "account_id": credit_account.id,
                "description": memo,
                "debit": "0",
                "credit": str(net_amount),
            }
        )

    else:
        # ADJUSTMENT: generic debit/credit
        je_lines.append(
            {
                "account_id": debit_account.id,
                "description": memo,
                "debit": str(abs(net_amount)),
                "credit": "0",
            }
        )
        je_lines.append(
            {
                "account_id": credit_account.id,
                "description": memo,
                "debit": "0",
                "credit": str(abs(net_amount)),
            }
        )

    # Create and post the JE
    actor = system_actor_for_company(company)

    result = create_journal_entry(
        actor=actor,
        date=settlement_date,
        memo=memo,
        lines=je_lines,
        kind="NORMAL",
        currency=currency,
    )

    if not result.success:
        logger.error("Failed to create JE for %s settlement %s: %s", platform, platform_document_id, result.error)
        return result

    entry = result.data
    save_result = save_journal_entry_complete(actor, entry.id)
    if not save_result.success:
        return save_result

    entry = save_result.data
    post_result = post_journal_entry(actor, entry.id)
    if not post_result.success:
        return post_result

    journal_entry = post_result.data

    # Link JE to settlement and mark as POSTED
    with command_writes_allowed():
        settlement.posted_journal_entry = journal_entry
        settlement.posted_at = timezone.now()
        settlement.status = PlatformSettlement.Status.POSTED
        settlement.save(update_fields=["posted_journal_entry", "posted_at", "status"])

    logger.info(
        "Created %s %s settlement %s → JE %s",
        platform,
        type_label,
        platform_document_id,
        journal_entry.public_id,
    )

    return CommandResult.ok(
        data={"settlement": settlement, "journal_entry": journal_entry},
    )
