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
from accounting.mappings import ModuleAccountMapping, module_key_for_provider
from accounting.models import JournalEntry
from accounting.settlement_provider import SettlementProvider
from accounts.authz import system_actor_for_company
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection

logger = logging.getLogger(__name__)


PROJECTION_NAME = "payment_settlement"

# Account-role lookups for the JE shape. EXPECTED_BANK_DEPOSIT and
# SALES_RETURNS are added to the chart in A14 (see _setup_shopify_accounts).
ROLE_EXPECTED_BANK_DEPOSIT = "EXPECTED_BANK_DEPOSIT"
ROLE_FEES = "PAYMENT_PROCESSING_FEES"
ROLE_SALES_RETURNS = "SALES_RETURNS"


def _raise_settlement_command_failure(company, entry_date, source_document, command_name, error):
    """A80: surface a settlement-JE command failure LOUDLY instead of the
    pre-fix silent ``return``.

    F27 (2026-07-10): the silent ``return`` on a ``create``/``save``/``post``
    failure committed an orphan DRAFT entry AND burned a
    ``journal_entry_number`` (a gap in the GL sequence) while the framework
    marked the event applied — so a settlement whose foreign line had no FX
    rate for its payout date (or landed in a closed period) silently vanished
    from the posted ledger and NEVER self-healed, even after the operator
    added the rate. Raising instead rolls back ``handle()``'s atomic block,
    undoing both the orphan DRAFT and the burned sequence number.

    Mirrors the Shopify order path (``shopify_connector/projections.py``):
    - a CLOSED fiscal period is terminal (won't self-heal until an operator
      reopens it) -> ``ProjectionTerminalSkip`` so a historical closed-period
      payout can't head-of-line-stall the whole settlement stream (the
      framework records the failure AND advances past it).
    - any other refusal -- notably a MISSING FX rate for the payout date -- is
      transient -> ``ProjectionCommandFailedError`` (DOWNSTREAM_FAILED), which
      surfaces in /finance/exceptions AND retries so it self-heals once the
      operator adds the rate.
    """
    from datetime import date as _date

    from accounting.validation import _check_period
    from projections.exceptions import (
        ProjectionCommandFailedError,
        ProjectionTerminalSkip,
    )

    period_date = entry_date
    if isinstance(entry_date, str):
        try:
            period_date = _date.fromisoformat(entry_date[:10])
        except (ValueError, TypeError):
            period_date = None

    if period_date and _check_period(company, period_date):
        raise ProjectionTerminalSkip(
            f"Settlement {source_document} dated {entry_date} cannot post: {error}",
            fix_hint=(
                "Reopen the fiscal period to post this settlement's journal "
                "entry, or exclude pre-close history from the settlement import."
            ),
        )

    raise ProjectionCommandFailedError(
        f"PaymentSettlement {source_document} {command_name} failed: {error}",
        command_name=command_name,
        original_error=error or "",
    )


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

        # A39: detect lines whose order already has a posted credit note from
        # the platform's own refund flow. Canonical case: BST-701 / order
        # 1007 — Shopify fires refund_created on COD failed delivery
        # (CN-000002 credits Bosta clearing 1,200), then Bosta's settlement
        # statement later reports the same order as `returned_uncollected`
        # (the importer would credit clearing another 1,200 via the Sales
        # Returns line). Same economic event, counted twice — drove
        # Aljazeera8's Bosta clearing to -2,200 EGP. We subtract the
        # already-credited rows from `gross` + `uncollected` (and from any
        # per-gateway breakdown) so the JE lands a balanced reduction:
        # DR Sales Returns drops, CR Clearing drops, net + fees unchanged.
        line_items = data.get("line_items") or []
        provider_breakdown = data.get("provider_breakdown") or []
        skipped_total, skipped_count, skipped_per_gateway = _detect_already_credited_lines(company, line_items)
        if skipped_total > 0:
            logger.info(
                "PaymentSettlement %s: A39 dropping %d already-credited line(s) "
                "totaling %s — credit notes posted via platform refund flow.",
                source_document,
                skipped_count,
                skipped_total,
            )
            uncollected -= skipped_total
            gross -= skipped_total
            for sub in provider_breakdown:
                gw = (sub.get("gateway_normalized_code") or "").strip().lower()
                sub_skip = skipped_per_gateway.get(gw, Decimal("0"))
                if sub_skip > 0:
                    sub["gross_amount"] = str(
                        (Decimal(str(sub.get("gross_amount", "0"))) - sub_skip).quantize(Decimal("0.01"))
                    )
                    sub["uncollected_amount"] = str(
                        (Decimal(str(sub.get("uncollected_amount", "0"))) - sub_skip).quantize(Decimal("0.01"))
                    )

            # If the entire batch was already credited via CNs, every JE line
            # would be zero — there's nothing to post. Stamp the source
            # document anyway so the idempotency guard recognizes the batch
            # as "handled" on replay.
            if gross <= 0:
                logger.info(
                    "PaymentSettlement %s: every line already credited via CN — no JE needed.",
                    source_document,
                )
                return

        # Resolve accounts.
        module = module_key_for_provider(external_system)
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
        # provider_breakdown is read above (A39 pre-pass mutates it).
        clearing_lines: list[dict] = []

        # A142: CSV imports carry their filename as provenance; API-pulled
        # settlements (Stripe sync) carry none — don't label those "(manual)".
        source_filename = data.get("source_filename")
        memo = f"Settlement: {provider.display_name} batch {payout_batch_id}"
        if source_filename:
            memo += f" ({source_filename})"

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
        # A146: both production emitters always set currency, so this fallback
        # is hardening for future emitters — and it must agree with the books
        # currency (functional-first, like create_journal_entry/je_builder).
        # default-first would stamp USD on an EGP-books company and convert
        # functional-magnitude amounts at the USD rate (or quarantine).
        currency = data.get("currency") or company.functional_currency or company.default_currency or "USD"
        entry_date = data.get("payout_date") or data.get("transaction_date")

        # A85 chunk 3b (2026-05-26): honor optional period override carried
        # in the event payload. When `period_override` is non-zero, the
        # JE posts to that (period, fiscal_year) instead of auto-resolving
        # from entry_date. The matching PeriodOverrideAudit row was already
        # written at import time by import_settlement_csv(); replay reads
        # the override from the immutable event payload and produces the
        # same JE.
        period_override = int(data.get("period_override") or 0) or None

        with transaction.atomic():
            create_result = create_journal_entry(
                actor=actor,
                date=entry_date,
                memo=memo,
                lines=je_lines,
                kind=JournalEntry.Kind.NORMAL,
                currency=currency,
                period=period_override,
                # A116: source provenance travels in the event payload so the
                # idempotency check (above) and Banked join survive rebuild.
                source_module=PROJECTION_NAME,
                source_document=source_document,
            )
            if not create_result.success:
                _raise_settlement_command_failure(
                    company,
                    entry_date,
                    source_document,
                    "create_journal_entry",
                    create_result.error,
                )
            entry = create_result.data

            save_result = save_journal_entry_complete(actor, entry.id)
            if not save_result.success:
                _raise_settlement_command_failure(
                    company,
                    entry_date,
                    source_document,
                    "save_journal_entry_complete",
                    save_result.error,
                )
            entry = save_result.data

            post_result = post_journal_entry(actor, entry.id)
            if not post_result.success:
                _raise_settlement_command_failure(
                    company,
                    entry_date,
                    source_document,
                    "post_journal_entry",
                    post_result.error,
                )
            entry = post_result.data

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


def _detect_already_credited_lines(
    company,
    line_items: list,
) -> tuple[Decimal, int, dict[str, Decimal]]:
    """A39: which settlement-statement lines have already been credited?

    A failed-delivery COD order can land in the books twice — once as a
    Shopify-fired ``refund_created`` event (CreditNote credits clearing)
    and again as the courier's settlement statement reporting the same
    order as ``returned_uncollected`` (settlement projection would credit
    clearing a second time). This helper returns which lines should be
    dropped from the settlement JE.

    Returns ``(total_skipped, count, per_gateway)`` so the caller can
    subtract from gross/uncollected/breakdown before posting.

    Detection key: ``SalesCreditNote.invoice.source_document_id`` matches
    the settlement line's ``order_id``. The CN must be POSTED (DRAFT
    credit notes haven't hit the GL yet, so they don't double-count).
    """
    from sales.models import SalesCreditNote

    skipped_total = Decimal("0")
    skipped_count = 0
    skipped_per_gateway: dict[str, Decimal] = {}

    for line in line_items or []:
        status = (line.get("status") or "").lower()
        if status not in ("returned", "refunded", "uncollected"):
            continue
        order_id = str(line.get("order_id") or "").strip()
        if not order_id:
            continue
        # Bosta line_items use "uncollected"; Paymob line_items use "refund".
        row_amount = Decimal(str(line.get("uncollected") or line.get("refund") or "0"))
        if row_amount <= 0:
            continue
        cn_exists = SalesCreditNote.objects.filter(
            company=company,
            invoice__company=company,
            invoice__source="shopify",
            invoice__source_document_id=order_id,
            status=SalesCreditNote.Status.POSTED,
        ).exists()
        if not cn_exists:
            continue
        skipped_total += row_amount
        skipped_count += 1
        gw = (line.get("gateway") or "").strip().lower()
        if gw:
            skipped_per_gateway[gw] = skipped_per_gateway.get(gw, Decimal("0")) + row_amount

    return skipped_total, skipped_count, skipped_per_gateway
