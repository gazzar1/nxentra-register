# accounting/payment_settlement_projection.py
"""
A14: PaymentSettlementProjection.

Consumes `PAYMENT_SETTLEMENT_RECEIVED` events (emitted by manual CSV
imports today; future automated Paymob/Bosta connectors will emit the
same event type) and posts the JE that drains the provider's clearing
balance:

    DR Expected Bank Deposit  net_amount
    DR Gateway/Courier Fees   fees
    DR Sales Returns          uncollected_amount (Bosta failed deliveries)
        CR <Provider> Clearing  gross_amount  [tagged with settlement_provider]

The clearing line is tagged with the provider's AnalysisDimensionValue —
the same tag the original order's clearing debit carries — so the
reconciliation engine sees the credit drain the right provider's balance.

Idempotency:
- Event idempotency_key is `payment.settlement.received:{provider}:{batch_id}`
  so re-importing the same CSV produces the same event (single row in the
  event store).
- This projection still checks if a JE with `source_module='payment_settlement'`
  and `source_document='{provider}:{batch_id}'` already exists, and skips
  if so. Defensive against replay outside the event-store idempotency
  guarantee (e.g. projection rebuild).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction

from accounting.commands import (
    create_journal_entry,
    post_journal_entry,
    save_journal_entry_complete,
)
from accounting.mappings import ModuleAccountMapping
from accounting.models import JournalEntry
from accounting.settlement_provider import SettlementProvider
from accounts.authz import system_actor_for_company
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection
from projections.write_barrier import command_writes_allowed

logger = logging.getLogger(__name__)


PROJECTION_NAME = "payment_settlement"

# Account-role lookups for the JE shape. EXPECTED_BANK_DEPOSIT and
# SALES_RETURNS are added to the chart in A14 (see _setup_shopify_accounts).
ROLE_EXPECTED_BANK_DEPOSIT = "EXPECTED_BANK_DEPOSIT"
ROLE_FEES = "PAYMENT_PROCESSING_FEES"
ROLE_SALES_RETURNS = "SALES_RETURNS"

# Module name used for the ModuleAccountMapping lookup. The settlement
# accounts are bootstrapped under the shopify_connector module today; when
# WooCommerce / Amazon connectors arrive they'll reuse the same role names
# under their own module key, and this projection will resolve via the
# provider's external_system.
_MODULE_BY_EXTERNAL_SYSTEM = {
    "shopify": "shopify_connector",
}


class PaymentSettlementProjection(BaseProjection):
    """Posts the GL entry for an imported settlement statement."""

    @property
    def name(self) -> str:
        return PROJECTION_NAME

    @property
    def consumes(self) -> list[str]:
        return [EventTypes.PAYMENT_SETTLEMENT_RECEIVED]

    def handle(self, event: BusinessEvent) -> None:
        data = event.get_data()
        company = event.company

        provider_code = data.get("provider_normalized_code") or ""
        external_system = data.get("external_system") or "shopify"
        payout_batch_id = data.get("payout_batch_id") or ""

        if not provider_code or not payout_batch_id:
            logger.warning(
                "PaymentSettlementReceived event %s missing required fields (provider=%r, batch=%r) — skipping",
                event.id,
                provider_code,
                payout_batch_id,
            )
            return

        # Idempotency guard: if a JE already exists for this provider+batch,
        # skip. The event store also guarantees idempotency via
        # idempotency_key, but a projection rebuild would replay every
        # event — the source_document check ensures we don't duplicate.
        source_document = f"{provider_code}:{payout_batch_id}"
        if JournalEntry.objects.filter(
            company=company,
            source_module=PROJECTION_NAME,
            source_document=source_document,
            status=JournalEntry.Status.POSTED,
        ).exists():
            logger.info(
                "Settlement %s already posted for company %s — skipping",
                source_document,
                company.id,
            )
            return

        provider = (
            SettlementProvider.objects.filter(
                company=company,
                external_system=external_system,
                normalized_code=provider_code,
                is_active=True,
            )
            .select_related("posting_profile", "dimension_value", "dimension_value__dimension")
            .first()
        )
        if not provider:
            logger.warning(
                "PaymentSettlement: no SettlementProvider for company=%s external_system=%s code=%r — skipping",
                company.id,
                external_system,
                provider_code,
            )
            return

        gross = Decimal(str(data.get("gross_amount", "0")))
        fees = Decimal(str(data.get("fees", "0")))
        net = Decimal(str(data.get("net_amount", "0")))
        uncollected = Decimal(str(data.get("uncollected_amount", "0")))

        if gross <= 0:
            logger.warning("PaymentSettlement: zero gross — skipping batch %s", payout_batch_id)
            return

        # Sanity: net + fees + uncollected should equal gross. If they
        # don't, log the imbalance and refuse to post — better to surface
        # a parser bug than to silently miswrite the books.
        computed = (net + fees + uncollected).quantize(Decimal("0.01"))
        if computed != gross.quantize(Decimal("0.01")):
            logger.error(
                "PaymentSettlement %s imbalance: gross=%s, net+fees+uncollected=%s. "
                "Skipping JE — fix the CSV / parser first.",
                source_document,
                gross,
                computed,
            )
            return

        # Resolve accounts.
        module = _MODULE_BY_EXTERNAL_SYSTEM.get(external_system, f"{external_system}_connector")
        mapping = ModuleAccountMapping.get_mapping(company, module)
        expected_bank = mapping.get(ROLE_EXPECTED_BANK_DEPOSIT)
        fees_account = mapping.get(ROLE_FEES)
        returns_account = mapping.get(ROLE_SALES_RETURNS)

        if not expected_bank:
            logger.error(
                "PaymentSettlement: %s missing EXPECTED_BANK_DEPOSIT mapping — "
                "run backfill_settlement_providers / _setup_shopify_accounts. Batch %s skipped.",
                module,
                payout_batch_id,
            )
            return

        # A22: when a payout batch consolidates rows from multiple
        # gateways (e.g. 'Paymob' umbrella + 'Paymob Accept' sub-method),
        # we resolve a SettlementProvider per breakdown entry and post
        # one CR clearing line per provider. Otherwise the legacy single-
        # clearing path applies (back-compat for parsers that don't emit
        # a breakdown, and for batches that span only one gateway).
        provider_breakdown = data.get("provider_breakdown") or []
        clearing_lines: list[dict] = []

        memo = (
            f"Settlement: {provider.display_name} batch {payout_batch_id} ({data.get('source_filename') or 'manual'})"
        )

        if provider_breakdown:
            for sub in provider_breakdown:
                sub_code = (sub.get("gateway_normalized_code") or "").strip().lower()
                sub_gross = Decimal(str(sub.get("gross_amount", "0")))
                if sub_gross <= 0:
                    continue
                sub_provider = (
                    SettlementProvider.objects.filter(
                        company=company,
                        external_system=external_system,
                        normalized_code=sub_code,
                        is_active=True,
                    )
                    .select_related("posting_profile", "dimension_value", "dimension_value__dimension")
                    .first()
                )
                if not sub_provider or not sub_provider.posting_profile:
                    logger.error(
                        "PaymentSettlement: provider_breakdown references unknown gateway %r "
                        "for batch %s — skipping JE. Run backfill_settlement_providers or "
                        "let the lazy-create path resolve it on next order.",
                        sub_code,
                        payout_batch_id,
                    )
                    return
                sub_clearing = sub_provider.posting_profile.control_account
                line = {
                    "account_id": sub_clearing.id,
                    "description": f"{memo} — {sub_provider.display_name} clearing",
                    "debit": "0",
                    "credit": str(sub_gross),
                }
                if sub_provider.dimension_value_id:
                    line["analysis_tags"] = [
                        {
                            "dimension_public_id": str(sub_provider.dimension_value.dimension.public_id),
                            "value_public_id": str(sub_provider.dimension_value.public_id),
                        }
                    ]
                clearing_lines.append(line)
        else:
            clearing_account = provider.posting_profile.control_account if provider.posting_profile else None
            if not clearing_account:
                logger.error(
                    "PaymentSettlement: provider %s has no posting_profile/clearing account — batch %s skipped.",
                    provider.normalized_code,
                    payout_batch_id,
                )
                return
            line = {
                "account_id": clearing_account.id,
                "description": f"{memo} — clearing",
                "debit": "0",
                "credit": str(gross),
            }
            if provider.dimension_value_id:
                line["analysis_tags"] = [
                    {
                        "dimension_public_id": str(provider.dimension_value.dimension.public_id),
                        "value_public_id": str(provider.dimension_value.public_id),
                    }
                ]
            clearing_lines.append(line)

        # Build JE lines.
        je_lines: list[dict] = [
            {
                "account_id": expected_bank.id,
                "description": f"{memo} — net to bank",
                "debit": str(net),
                "credit": "0",
            }
        ]
        if fees > 0:
            if not fees_account:
                logger.error(
                    "PaymentSettlement: missing PAYMENT_PROCESSING_FEES mapping but fees=%s — batch %s skipped.",
                    fees,
                    payout_batch_id,
                )
                return
            je_lines.append(
                {
                    "account_id": fees_account.id,
                    "description": f"{memo} — fees",
                    "debit": str(fees),
                    "credit": "0",
                }
            )
        if uncollected > 0:
            if not returns_account:
                logger.error(
                    "PaymentSettlement: missing SALES_RETURNS mapping but uncollected=%s — batch %s skipped.",
                    uncollected,
                    payout_batch_id,
                )
                return
            je_lines.append(
                {
                    "account_id": returns_account.id,
                    "description": f"{memo} — uncollected / failed delivery",
                    "debit": str(uncollected),
                    "credit": "0",
                }
            )
        je_lines.extend(clearing_lines)

        actor = system_actor_for_company(company)
        currency = data.get("currency") or company.default_currency or "USD"
        entry_date = data.get("payout_date") or data.get("transaction_date")

        with transaction.atomic():
            create_result = create_journal_entry(
                actor=actor,
                date=entry_date,
                memo=memo,
                lines=je_lines,
                kind=JournalEntry.Kind.NORMAL,
                currency=currency,
            )
            if not create_result.success:
                logger.error(
                    "PaymentSettlement %s create_journal_entry failed: %s",
                    source_document,
                    create_result.error,
                )
                return
            entry = create_result.data

            save_result = save_journal_entry_complete(actor, entry.id)
            if not save_result.success:
                logger.error(
                    "PaymentSettlement %s save_journal_entry_complete failed: %s",
                    source_document,
                    save_result.error,
                )
                return
            entry = save_result.data

            post_result = post_journal_entry(actor, entry.id)
            if not post_result.success:
                logger.error(
                    "PaymentSettlement %s post_journal_entry failed: %s",
                    source_document,
                    post_result.error,
                )
                return
            entry = post_result.data

            # Stamp source_module/source_document for the idempotency check.
            with command_writes_allowed():
                JournalEntry.objects.filter(pk=entry.pk).update(
                    source_module=PROJECTION_NAME,
                    source_document=source_document,
                )

        logger.info(
            "PaymentSettlement: posted JE %s for %s batch %s (gross=%s net=%s fees=%s uncollected=%s)",
            entry.public_id,
            provider.display_name,
            payout_batch_id,
            gross,
            net,
            fees,
            uncollected,
        )
