# platform_connectors/projections.py
"""
Platform-agnostic accounting projection.

Consumes PLATFORM_* events and creates journal entries using the shared
JE builder. This single projection handles all platform connectors that
emit generic platform.* events.

Shopify keeps its own ShopifyAccountingHandler for backward compatibility.
New platforms (Stripe, WooCommerce, etc.) emit PLATFORM_* events and are
handled here.
"""

import logging
from datetime import date, datetime
from decimal import Decimal

from accounting.mappings import ModuleAccountMapping, module_key_for_provider
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection
from projections.exceptions import ProjectionStateError

from .je_builder import JELine, JERequest, build_journal_entry

logger = logging.getLogger(__name__)

PROJECTION_NAME = "platform_accounting"

# Standard account roles expected across all platforms
ROLE_CLEARING = "PLATFORM_CLEARING"
ROLE_SALES_REVENUE = "SALES_REVENUE"
ROLE_SALES_TAX = "SALES_TAX_PAYABLE"
ROLE_SHIPPING_REVENUE = "SHIPPING_REVENUE"
ROLE_CASH_BANK = "CASH_BANK"
ROLE_PROCESSING_FEES = "PAYMENT_PROCESSING_FEES"
ROLE_CHARGEBACK_EXPENSE = "CHARGEBACK_EXPENSE"


def _parse_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value).date()
    return None


def _role_fix_hint(missing_role: str, platform_slug: str) -> str:
    """A157: fix_hint for a missing account-mapping role — tells the operator
    exactly which role to wire; the event self-heals on the next pass."""
    role_name = f"PLATFORM_CLEARING or {platform_slug.upper()}_CLEARING" if missing_role == "CLEARING" else missing_role
    return (
        f"Map an account to the {role_name} role for this platform's module "
        f"(re-run the platform seed or fix it in account mappings); the event then self-heals."
    )


class PlatformAccountingProjection(BaseProjection):
    """
    Creates journal entries from generic platform commerce events.

    Order paid:
        DR Platform Clearing    (total_price)
        CR Sales Revenue        (subtotal)
        CR Sales Tax            (total_tax)      — if > 0
        CR Shipping Revenue     (total_shipping)  — if > 0

    Refund created:
        DR Sales Revenue        (amount)
        CR Platform Clearing    (amount)

    Payout settled:
        DR Cash/Bank            (net_amount)
        DR Processing Fees      (fees)
        CR Platform Clearing    (gross_amount)

    Dispute created:
        DR Chargeback Expense   (dispute_amount)
        DR Processing Fees      (chargeback_fee)
        CR Platform Clearing    (total)
    """

    @property
    def name(self) -> str:
        return PROJECTION_NAME

    @property
    def consumes(self):
        return [
            EventTypes.PLATFORM_ORDER_PAID,
            EventTypes.PLATFORM_REFUND_CREATED,
            EventTypes.PLATFORM_PAYOUT_SETTLED,
            EventTypes.PLATFORM_DISPUTE_CREATED,
            EventTypes.PLATFORM_FULFILLMENT_CREATED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        metadata = event.metadata or {}
        if metadata.get("source_projection") == PROJECTION_NAME:
            return

        data = event.get_data()
        company = event.company
        platform_slug = data.get("platform_slug", "unknown")
        module_key = module_key_for_provider(platform_slug)

        handler = {
            EventTypes.PLATFORM_ORDER_PAID: self._handle_order_paid,
            EventTypes.PLATFORM_REFUND_CREATED: self._handle_refund_created,
            EventTypes.PLATFORM_PAYOUT_SETTLED: self._handle_payout_settled,
            EventTypes.PLATFORM_DISPUTE_CREATED: self._handle_dispute_created,
        }.get(event.event_type)
        if not handler:
            # e.g. PLATFORM_FULFILLMENT_CREATED — consumed but posts no JE.
            return

        mapping = ModuleAccountMapping.get_mapping(company, module_key)
        if not mapping:
            # A157: raise (not silent skip) so a missing platform mapping is
            # operator-visible in /finance/exceptions and the event self-heals
            # on the next process_pending pass once the mapping is wired.
            # This exact class bit production once (STRIPE_CLEARING role
            # mismatch silently dropped every charge JE).
            raise ProjectionStateError(
                f"No ModuleAccountMapping for module {module_key!r} "
                f"(platform {platform_slug!r}) — cannot post {event.event_type}.",
                fix_hint=(
                    f"Connect the platform / re-run its seed (e.g. setup_stripe_platform) to "
                    f"create the {module_key!r} account mapping; the event then self-heals."
                ),
            )

        # Resolve dimension context for JE line tagging
        from platform_connectors.dimensions import (
            resolve_platform_dimensions,
            resolve_settlement_provider_value,
        )

        dimension_context = resolve_platform_dimensions(company, platform_slug)
        # A139: the SETTLEMENT_PROVIDER value tagged on clearing lines only —
        # /finance/reconciliation Stage 1 pivots on it.
        provider_value = resolve_settlement_provider_value(company, platform_slug)

        handler(event, data, mapping, platform_slug, dimension_context, provider_value)

    @staticmethod
    def _clearing_account(mapping, platform_slug):
        """Resolve the platform-clearing account from the module mapping.

        Connectors may register their clearing account under the canonical
        ``PLATFORM_CLEARING`` role OR a provider-specific ``{PROVIDER}_CLEARING``
        role — Stripe seeds ``STRIPE_CLEARING`` for its dedicated per-provider
        clearing account (code 11510, ADR-0002), so accept either. Without this,
        the projection couldn't find the clearing account and skipped every JE
        (the charge→JE path silently posted nothing once charges first flowed).
        """
        return mapping.get(ROLE_CLEARING) or mapping.get(f"{platform_slug.upper()}_CLEARING")

    def _handle_order_paid(self, event, data, mapping, platform_slug, dimension_context=None, provider_value=None):
        clearing = self._clearing_account(mapping, platform_slug)
        revenue = mapping.get(ROLE_SALES_REVENUE)
        if not clearing or not revenue:
            # A157: raise so the missing role is operator-visible and the
            # order self-heals once the mapping is wired.
            missing = "CLEARING" if not clearing else "SALES_REVENUE"
            raise ProjectionStateError(
                f"Account mapping missing {missing} for platform {platform_slug!r} — "
                f"cannot post order {data.get('order_name', data.get('order_number', ''))!r}.",
                fix_hint=_role_fix_hint(missing, platform_slug),
            )

        total_price = Decimal(str(data.get("amount", "0")))
        subtotal = Decimal(str(data.get("subtotal", "0")))
        total_tax = Decimal(str(data.get("total_tax", "0")))
        total_shipping = Decimal(str(data.get("total_shipping", "0")))
        order_name = data.get("order_name", data.get("order_number", ""))
        entry_date = _parse_date(data.get("transaction_date")) or event.occurred_at.date()
        currency = data.get("currency") or "USD"
        memo = f"{platform_slug.title()} order: {order_name}"

        if total_price <= 0:
            return

        lines = [
            # A139: SETTLEMENT_PROVIDER on the clearing line ONLY — Stage 1 of
            # /finance/reconciliation pivots on it (Expected = tagged debits).
            JELine(
                account=clearing,
                description=memo,
                debit=total_price,
                analysis_values=[provider_value] if provider_value else [],
            ),
        ]

        revenue_amount = subtotal if subtotal > 0 else total_price - total_tax
        lines.append(JELine(account=revenue, description=memo, credit=revenue_amount))

        tax_account = mapping.get(ROLE_SALES_TAX)
        if total_tax > 0 and tax_account:
            lines.append(
                JELine(
                    account=tax_account,
                    description=f"Sales tax: {order_name}",
                    credit=total_tax,
                )
            )

        shipping_account = mapping.get(ROLE_SHIPPING_REVENUE)
        if total_shipping > 0:
            ship_acct = shipping_account or revenue
            lines.append(
                JELine(
                    account=ship_acct,
                    description=f"Shipping: {order_name}",
                    credit=total_shipping,
                )
            )

        entry = build_journal_entry(
            JERequest(
                company=event.company,
                entry_date=entry_date,
                memo=memo,
                source_module=f"platform_{platform_slug}",
                source_document=data.get("platform_order_id", ""),
                currency=currency,
                lines=lines,
                caused_by_event=event,
                projection_name=PROJECTION_NAME,
                posted_by_email=f"system@{platform_slug}",
                dimension_context=dimension_context or {},
            )
        )

        if entry:
            logger.info(
                "Created JE %s for %s order %s",
                entry.public_id,
                platform_slug,
                order_name,
            )

    def _handle_refund_created(self, event, data, mapping, platform_slug, dimension_context=None, provider_value=None):
        clearing = self._clearing_account(mapping, platform_slug)
        revenue = mapping.get(ROLE_SALES_REVENUE)
        if not clearing or not revenue:
            # A157: this was a bare `return` — a refund event vanished with
            # zero trace, not even a log line. Raise so it's operator-visible
            # and self-heals once the mapping is wired.
            missing = "CLEARING" if not clearing else "SALES_REVENUE"
            raise ProjectionStateError(
                f"Account mapping missing {missing} for platform {platform_slug!r} — "
                f"cannot post refund {data.get('platform_refund_id', '')!r} "
                f"(order {data.get('order_number', '')!r}).",
                fix_hint=_role_fix_hint(missing, platform_slug),
            )

        amount = Decimal(str(data.get("amount", "0")))
        order_number = data.get("order_number", "")
        refund_id = data.get("platform_refund_id", "")
        entry_date = _parse_date(data.get("transaction_date")) or event.occurred_at.date()
        currency = data.get("currency") or "USD"
        memo = f"{platform_slug.title()} refund: Order {order_number} (Ref {refund_id})"

        if amount <= 0:
            return

        entry = build_journal_entry(
            JERequest(
                company=event.company,
                entry_date=entry_date,
                memo=memo,
                source_module=f"platform_{platform_slug}",
                source_document=str(refund_id),
                currency=currency,
                lines=[
                    JELine(account=revenue, description=memo, debit=amount),
                    # A139: tagged so the refund lands in Stage 1 "Refunded"
                    # (classified by source_module platform_* + credit>0).
                    JELine(
                        account=clearing,
                        description=memo,
                        credit=amount,
                        analysis_values=[provider_value] if provider_value else [],
                    ),
                ],
                caused_by_event=event,
                projection_name=PROJECTION_NAME,
                posted_by_email=f"system@{platform_slug}",
                dimension_context=dimension_context or {},
            )
        )

        if entry:
            logger.info(
                "Created refund JE %s for %s order %s",
                entry.public_id,
                platform_slug,
                order_number,
            )

    def _handle_payout_settled(self, event, data, mapping, platform_slug, dimension_context=None, provider_value=None):
        clearing = self._clearing_account(mapping, platform_slug)
        bank = mapping.get(ROLE_CASH_BANK)
        if not clearing or not bank:
            # A157: raise so the missing role is operator-visible and the
            # payout self-heals once the mapping is wired.
            missing = "CLEARING" if not clearing else "CASH_BANK"
            raise ProjectionStateError(
                f"Account mapping missing {missing} for platform {platform_slug!r} — "
                f"cannot post payout {data.get('platform_payout_id', '')!r}.",
                fix_hint=_role_fix_hint(missing, platform_slug),
            )

        fees_account = mapping.get(ROLE_PROCESSING_FEES)

        gross_amount = Decimal(str(data.get("gross_amount", "0")))
        fees = Decimal(str(data.get("fees", "0")))
        net_amount = Decimal(str(data.get("net_amount", "0")))
        payout_id = data.get("platform_payout_id", "")
        entry_date = _parse_date(data.get("payout_date") or data.get("transaction_date")) or event.occurred_at.date()
        currency = data.get("currency") or "USD"
        memo = f"{platform_slug.title()} payout: {payout_id}"

        if gross_amount == 0:
            return

        is_negative = gross_amount < 0
        abs_gross = abs(gross_amount)
        abs_net = abs(net_amount)

        lines = []
        if is_negative:
            # Negative payout: refunds > charges in this period
            lines.append(JELine(account=clearing, description=memo, debit=abs_gross))
            if fees > 0 and fees_account:
                lines.append(
                    JELine(
                        account=fees_account,
                        description=f"Processing fees: {payout_id}",
                        debit=fees,
                    )
                )
            lines.append(JELine(account=bank, description=memo, credit=abs_net))
        else:
            # Normal positive payout
            lines.append(JELine(account=bank, description=memo, debit=abs_net))
            if fees > 0 and fees_account:
                lines.append(
                    JELine(
                        account=fees_account,
                        description=f"Processing fees: {payout_id}",
                        debit=fees,
                    )
                )
            lines.append(JELine(account=clearing, description=memo, credit=abs_gross))

        entry = build_journal_entry(
            JERequest(
                company=event.company,
                entry_date=entry_date,
                memo=memo,
                source_module=f"platform_{platform_slug}",
                source_document=str(payout_id),
                currency=currency,
                lines=lines,
                caused_by_event=event,
                projection_name=PROJECTION_NAME,
                posted_by_email=f"system@{platform_slug}",
                dimension_context=dimension_context or {},
            )
        )

        if entry:
            logger.info(
                "Created payout JE %s for %s payout %s",
                entry.public_id,
                platform_slug,
                payout_id,
            )

    def _handle_dispute_created(self, event, data, mapping, platform_slug, dimension_context=None, provider_value=None):
        # A139 note: dispute JEs (CR clearing) are deliberately NOT tagged yet —
        # Stage 1 has no "clawed back" classification; revisit with Phase 3
        # dispute-resolution events.
        clearing = self._clearing_account(mapping, platform_slug)
        chargeback = mapping.get(ROLE_CHARGEBACK_EXPENSE)
        if not clearing or not chargeback:
            # A157: raise so the missing role is operator-visible and the
            # dispute self-heals once the mapping is wired.
            missing = "CLEARING" if not clearing else "CHARGEBACK_EXPENSE"
            raise ProjectionStateError(
                f"Account mapping missing {missing} for platform {platform_slug!r} — "
                f"cannot post dispute {data.get('platform_dispute_id', '')!r}.",
                fix_hint=_role_fix_hint(missing, platform_slug),
            )

        fees_account = mapping.get(ROLE_PROCESSING_FEES)

        dispute_amount = Decimal(str(data.get("dispute_amount", "0")))
        chargeback_fee = Decimal(str(data.get("chargeback_fee", "0")))
        dispute_id = data.get("platform_dispute_id", "")
        order_name = data.get("order_name", "")
        entry_date = _parse_date(data.get("transaction_date")) or event.occurred_at.date()
        currency = data.get("currency") or "USD"
        memo = f"{platform_slug.title()} chargeback: {order_name} (Dispute {dispute_id})"
        total = dispute_amount + chargeback_fee

        if total <= 0:
            return

        lines = [
            JELine(account=chargeback, description=memo, debit=dispute_amount),
        ]
        if chargeback_fee > 0 and fees_account:
            lines.append(
                JELine(
                    account=fees_account,
                    description=f"Chargeback fee: {dispute_id}",
                    debit=chargeback_fee,
                )
            )
        lines.append(JELine(account=clearing, description=memo, credit=total))

        entry = build_journal_entry(
            JERequest(
                company=event.company,
                entry_date=entry_date,
                memo=memo,
                source_module=f"platform_{platform_slug}",
                source_document=str(dispute_id),
                currency=currency,
                lines=lines,
                caused_by_event=event,
                projection_name=PROJECTION_NAME,
                posted_by_email=f"system@{platform_slug}",
                dimension_context=dimension_context or {},
            )
        )

        if entry:
            logger.info(
                "Created chargeback JE %s for %s dispute %s",
                entry.public_id,
                platform_slug,
                dispute_id,
            )


# =============================================================================
# PaymentsProjection — ADR-0002 Phase 2 PR-A
# =============================================================================

PAYMENTS_PROJECTION_NAME = "payments"


class PaymentsProjection(BaseProjection):
    """Materialize the per-payout line breakdown (ProviderPayoutLine) from
    ``PAYMENT_SETTLEMENT_RECEIVED.line_items[]`` (ADR-0002 Phase 2).

    A SECOND, independent consumer of the settlement event: PaymentSettlementProjection
    keeps posting the drain JE; this projection only builds the read-model. Sole
    writer of ProviderPayoutLine, with deterministic ids so replay/rebuild upserts
    the same rows.

    Dual-write phase (PR-A): the legacy StripePayout/StripePayoutTransaction caches
    are still direct-written by stripe_connector.sync._upsert_read_models. PR-C flips
    this projection to the source of truth and removes those direct writes.
    """

    @property
    def name(self) -> str:
        return PAYMENTS_PROJECTION_NAME

    @property
    def consumes(self) -> list[str]:
        return [
            EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
            # PR-D2: match-state snapshots stamped onto the rows the settlement
            # event materialized. NOTE deploy ordering: reconciled events emitted
            # BEFORE this type entered `consumes` sit behind the bookmark — the
            # D2 runbook must run `payments_canonical_backfill --apply`
            # (rebuild + drain) to replay them.
            EventTypes.PROVIDER_PAYOUT_RECONCILED,
        ]

    def _clear_projected_data(self, company) -> None:
        # provider_payout / provider_payout_line have FORCE RLS (migrations 0005/0008).
        # rebuild() calls this BEFORE process_pending() establishes the tenant session
        # context, so via a direct rebuild path the DELETE would be RLS-filtered to
        # zero rows and stale rows would survive a replay (Codex P2). Bypass RLS for
        # the clear — the explicit company filter keeps it tenant-scoped.
        from accounts.rls import rls_bypass

        from .models import ProviderPayout, ProviderPayoutLine

        with rls_bypass():
            ProviderPayoutLine.objects.filter(company=company).delete()
            ProviderPayout.objects.filter(company=company).delete()

    def handle(self, event: BusinessEvent) -> None:
        if event.event_type == EventTypes.PROVIDER_PAYOUT_RECONCILED:
            self._handle_payout_reconciled(event)
            return
        self._handle_settlement(event)

    def _handle_settlement(self, event: BusinessEvent) -> None:
        from .models import (
            ProviderPayout,
            ProviderPayoutLine,
            derive_provider_payout_id,
            derive_provider_payout_line_id,
        )

        data = event.get_data()
        company = event.company

        # The settling provider for the lines. provider_normalized_code is the
        # canonical code ("stripe"); fall back to external_system for older payloads.
        provider = (data.get("provider_normalized_code") or data.get("external_system") or "").strip().lower()
        payout_batch_id = data.get("payout_batch_id") or ""
        if not provider or not payout_batch_id:
            return

        # A146: fallback for currency-less events prefers the FUNCTIONAL
        # (books) currency, in lockstep with the JE consumer — default-first
        # displayed "USD" payout headers on EGP books (Paymob demo artifact).
        currency = (data.get("currency") or company.functional_currency or company.default_currency or "").upper()

        # Header (one per event). Totals come from the event's TOP-LEVEL fields
        # (not the line aggregate) so they match the legacy StripePayout header.
        # PR-D2 LOAD-BEARING: the defaults dicts here and on the lines below must
        # NEVER grow the reconciliation/match-state fields — update_or_create only
        # writes the keys listed, which is what keeps a settlement re-apply from
        # zeroing the verdicts _handle_payout_reconciled stamped.
        ProviderPayout.objects.update_or_create(
            id=derive_provider_payout_id(company.id, provider, payout_batch_id),
            defaults={
                "company": company,
                "provider": provider,
                "payout_batch_id": payout_batch_id,
                "provider_status": str(data.get("provider_status") or ""),
                "provider_account_reference": str(data.get("provider_account_reference") or ""),
                "provider_account_name": str(data.get("provider_account_name") or ""),
                "gross_amount": Decimal(str(data.get("gross_amount") or "0")),
                "fees": Decimal(str(data.get("fees") or "0")),
                "net_amount": Decimal(str(data.get("net_amount") or "0")),
                "uncollected_amount": Decimal(str(data.get("uncollected_amount") or "0")),
                "currency": currency,
                "payout_date": _parse_date(data.get("payout_date")),
                "provider_metadata": data.get("provider_metadata") or {},
            },
        )

        line_items = data.get("line_items") or []
        for index, line in enumerate(line_items):
            ProviderPayoutLine.objects.update_or_create(
                id=derive_provider_payout_line_id(company.id, provider, payout_batch_id, index),
                defaults={
                    "company": company,
                    "provider": provider,
                    "payout_batch_id": payout_batch_id,
                    "line_index": index,
                    "source_id": str(line.get("order_id") or ""),
                    "kind": str(line.get("status") or ""),
                    "gross_amount": Decimal(str(line.get("gross") or "0")),
                    "fee": Decimal(str(line.get("fee") or "0")),
                    "net_amount": Decimal(str(line.get("net") or "0")),
                    # Provider-agnostic: Bosta carries the failed-delivery value in
                    # "uncollected", Paymob the refund/chargeback in "refund". Reading
                    # only gross/fee/net would zero those lines (the Stripe-shaped leak).
                    "uncollected_amount": Decimal(str(line.get("uncollected") or line.get("refund") or "0")),
                    "currency": currency,
                    # PR-D2: match-state fields deliberately absent — see the
                    # header comment above.
                },
            )

    def _handle_payout_reconciled(self, event: BusinessEvent) -> None:
        """Stamp a PROVIDER_PAYOUT_RECONCILED snapshot onto the canonical rows.

        A dumb last-write-wins stamp (A139): the snapshot is self-sufficient, so
        replay in company_sequence order reconstructs match state exactly. Rows
        are addressed by their deterministic ids recomputed from the event's
        correlation keys; a verdict for a missing line is a warn + no-op (NOT a
        defer — the emitter refuses to emit without a settlement event, so a
        missing row means the settlement handler skipped, and deferring would
        head-of-line-block the company's stream forever).

        Uses queryset .update() (never creates stubs); updated_at is stamped
        explicitly because .update() bypasses auto_now.
        """
        from django.utils import timezone as dj_timezone

        from .models import (
            ProviderPayout,
            ProviderPayoutLine,
            derive_provider_payout_id,
            derive_provider_payout_line_id,
        )

        data = event.get_data()
        company = event.company
        provider = (data.get("provider") or "").strip().lower()
        payout_batch_id = str(data.get("payout_batch_id") or "")
        if not provider or not payout_batch_id:
            return

        # Defensive parse: reconciled_at is convention-only (not validator-
        # enforced); a malformed value must not stop the whole payments stream.
        reconciled_at = None
        raw_reconciled_at = data.get("reconciled_at") or ""
        if raw_reconciled_at:
            try:
                reconciled_at = datetime.fromisoformat(raw_reconciled_at)
            except (TypeError, ValueError):
                logger.warning(
                    "PROVIDER_PAYOUT_RECONCILED %s: unparseable reconciled_at %r — stamping without it",
                    event.id,
                    raw_reconciled_at,
                )

        now = dj_timezone.now()
        for verdict in data.get("line_verdicts") or []:
            try:
                line_index = int(verdict.get("line_index"))
            except (TypeError, ValueError):
                logger.warning(
                    "PROVIDER_PAYOUT_RECONCILED %s: verdict without a usable line_index (%r) — skipped",
                    event.id,
                    verdict.get("line_index"),
                )
                continue
            line_pk = derive_provider_payout_line_id(company.id, provider, payout_batch_id, line_index)
            is_verified = bool(verdict.get("verified"))
            updated = ProviderPayoutLine.objects.filter(id=line_pk).update(
                verified=is_verified,
                match_kind=str(verdict.get("match_kind") or ""),
                matched_ref=str(verdict.get("matched_ref") or ""),
                matched_ref_type=str(verdict.get("matched_ref_type") or ""),
                provider_line_ref=str(verdict.get("provider_line_ref") or ""),
                # "When was this line verified" — None for unverified lines
                # (snapshot time lives on header.last_reconciled_at).
                verified_at=reconciled_at if is_verified else None,
                updated_at=now,
            )
            if not updated:
                logger.warning(
                    "PROVIDER_PAYOUT_RECONCILED %s: no canonical line %s:%s#%s to stamp "
                    "(settlement handler skipped this payout?)",
                    event.id,
                    provider,
                    payout_batch_id,
                    line_index,
                )

        ProviderPayout.objects.filter(id=derive_provider_payout_id(company.id, provider, payout_batch_id)).update(
            reconciliation_outcome=str(data.get("outcome") or ""),
            matched_line_count=int(data.get("matched_count") or 0),
            unmatched_line_count=int(data.get("unmatched_count") or 0),
            verified_line_count=int(data.get("verified_count") or 0),
            gross_variance=Decimal(str(data.get("gross_variance") or "0")),
            fee_variance=Decimal(str(data.get("fee_variance") or "0")),
            net_variance=Decimal(str(data.get("net_variance") or "0")),
            last_reconciled_at=reconciled_at,
            reconciliation_source=str(data.get("source") or ""),
            updated_at=now,
        )
