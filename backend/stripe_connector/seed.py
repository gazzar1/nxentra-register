# stripe_connector/seed.py
"""Account + settlement-provider bootstrap for the Stripe platform connector
(ADR-0002 S1).

Mirrors the Shopify onboarding seed (``_setup_shopify_accounts`` +
``_bootstrap_shopify_settlement_providers``) but under the canonical
``platform_stripe`` ModuleAccountMapping key and ``external_system='stripe'``.

Per-provider isolation (ADR-0002): Stripe gets its OWN clearing + expected-bank-
deposit accounts (distinct codes 11510 / 11610) so its balances reconcile
independently of Shopify's. Company-wide accounts (revenue, tax, cash, chargeback,
returns) are shared via get_or_create on the standard codes. Gateway FEES get a
dedicated code (53100), not a generic admin-expense code — burying processing
fees in "Office & General" would defeat reconciliation reporting.

Idempotent: safe to call on every connect.
"""

from __future__ import annotations

import logging

from accounting.mappings import ModuleAccountMapping, module_key_for_provider

logger = logging.getLogger(__name__)

STRIPE_MODULE_KEY = module_key_for_provider("stripe")  # "platform_stripe"

# (role, code, name, account_type, account_role)
# Codes 11510 / 11610 are Stripe-specific so its clearing + EBD never collide
# with Shopify's 11500 / 11600 — per-provider reconciliation depends on it.
STRIPE_ACCOUNTS = [
    ("SALES_REVENUE", "41000", "Sales Revenue", "REVENUE", "SALES"),
    ("STRIPE_CLEARING", "11510", "Stripe Clearing", "ASSET", "LIQUIDITY"),
    # Dedicated fee account (53100), NOT a generic admin-expense code like 53000
    # (which many charts already use for "Office & General"). Processing fees are
    # a core ecommerce-reconciliation metric and must stand on their own line.
    ("PAYMENT_PROCESSING_FEES", "53100", "Payment Processing Fees", "EXPENSE", "OPERATING_EXPENSE"),
    ("SALES_TAX_PAYABLE", "22000", "Sales Tax Payable", "LIABILITY", "TAX_PAYABLE"),
    ("CASH_BANK", "11000", "Cash and Bank", "ASSET", "LIQUIDITY"),
    ("CHARGEBACK_EXPENSE", "52100", "Chargeback Expense", "EXPENSE", "OTHER_EXPENSE"),
    # Settlement-drain roles the PaymentSettlementProjection requires. EBD is
    # per-provider (distinct code) so the bank-match picker (PR-A union) can
    # surface a Stripe deposit independently of Shopify's.
    ("EXPECTED_BANK_DEPOSIT", "11610", "Expected Bank Deposit — Stripe", "ASSET", "LIQUIDITY"),
    ("SALES_RETURNS", "41200", "Sales Returns / Failed Delivery", "REVENUE", "CONTRA_REVENUE"),
]


def setup_stripe_accounts(company):
    """Create Stripe GL accounts + ModuleAccountMapping rows under platform_stripe.

    Returns the STRIPE_CLEARING Account (the settlement-provider bootstrap needs
    it as the clearing control account).
    """
    from accounting.models import Account
    from projections.write_barrier import projection_writes_allowed

    clearing = None
    with projection_writes_allowed():
        for role, code, name, acct_type, acct_role in STRIPE_ACCOUNTS:
            account, _ = Account.objects.get_or_create(
                company=company,
                code=code,
                defaults={
                    "name": name,
                    "account_type": acct_type,
                    "role": acct_role,
                    "ledger_domain": "FINANCIAL",
                    "status": "ACTIVE",
                    "normal_balance": "DEBIT" if acct_type in ("ASSET", "EXPENSE") else "CREDIT",
                },
            )
            ModuleAccountMapping.objects.update_or_create(
                company=company,
                module=STRIPE_MODULE_KEY,
                role=role,
                defaults={"account": account},
            )
            if role == "STRIPE_CLEARING":
                clearing = account
    return clearing


def bootstrap_stripe_settlement_provider(company, clearing_account):
    """Create the SettlementProvider(external_system='stripe', GATEWAY) + its
    PostingProfile (on the Stripe clearing account) + dimension value, so the
    PaymentSettlementProjection can resolve the provider and tag the clearing
    JE line. Idempotent.
    """
    from accounting.models import AccountDimensionRule
    from accounting.settlement_provider import (
        SettlementProvider,
        ensure_settlement_provider_dimension,
        ensure_settlement_provider_dimension_value,
        normalize_gateway_code,
    )
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from sales.models import PostingProfile

    dimension = ensure_settlement_provider_dimension(company)
    # Every JE line on the Stripe clearing account must carry the provider tag
    # so reconciliation pivots are complete.
    AccountDimensionRule.objects.update_or_create(
        company=company,
        account=clearing_account,
        dimension=dimension,
        defaults={"rule_type": AccountDimensionRule.RuleType.REQUIRED},
    )

    normalized = normalize_gateway_code("stripe")
    with command_writes_allowed(), projection_writes_allowed():
        profile, _ = PostingProfile.objects.get_or_create(
            company=company,
            code=f"PG-{normalized.upper()}"[:20],
            defaults={
                "name": "Stripe (Gateway)",
                "name_ar": "سترايب (بوابة دفع)",
                "profile_type": PostingProfile.ProfileType.CUSTOMER,
                "usage": PostingProfile.Usage.GATEWAY,
                "control_account": clearing_account,
                "is_active": True,
                "description": "Auto-created for Stripe payout settlement routing (ADR-0002).",
            },
        )
        dimension_value = ensure_settlement_provider_dimension_value(
            dimension=dimension,
            normalized_code=normalized,
            display_name="Stripe",
        )
        provider, _ = SettlementProvider.objects.get_or_create(
            company=company,
            external_system="stripe",
            normalized_code=normalized,
            defaults={
                "source_code": "stripe",
                "display_name": "Stripe",
                "provider_type": SettlementProvider.ProviderType.GATEWAY,
                "posting_profile": profile,
                "dimension_value": dimension_value,
                "is_active": True,
                "needs_review": False,
            },
        )
        # Backfill dimension_value / clear review on re-run, mirroring the
        # Shopify bootstrap's self-healing behavior.
        updates = []
        if provider.dimension_value_id != dimension_value.id:
            provider.dimension_value = dimension_value
            updates.append("dimension_value")
        if provider.needs_review:
            provider.needs_review = False
            updates.append("needs_review")
        if updates:
            provider.save(update_fields=[*updates, "updated_at"])
    return provider


def setup_stripe_platform(company):
    """One-shot Stripe onboarding seed: GL accounts + mappings + the
    SettlementProvider. Call from the connect flow. Idempotent."""
    clearing = setup_stripe_accounts(company)
    bootstrap_stripe_settlement_provider(company, clearing)
    logger.info("Stripe platform accounts + settlement provider seeded for company %s", company.id)
