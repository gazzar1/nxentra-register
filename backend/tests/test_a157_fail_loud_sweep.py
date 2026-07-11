# tests/test_a157_fail_loud_sweep.py
"""
A157 — fail-loud sweep of the remaining silent-loss projection branches
(same class as F27; 2026-07-11 dual audit).

Before this fix, these branches did `logger.* + return` (or a bare return),
so the framework committed ProjectionAppliedEvent and advanced the bookmark:
event consumed, money silently lost, unrecoverable even by re-import
(idempotency freezes the bad event):

- accounting/payment_settlement_projection.py — zero-gross and
  gross != net+fees+uncollected (the exact branch behind the real
  A20/MAY01-A loss), and missing provider/batch fields.
- platform_connectors/projections.py — missing mapping/account-role on
  every platform JE path (order, refund, payout, dispute). The refund
  branch was a BARE return — not even a log line.

Now:
- Immutable-payload defects (imbalance, zero-gross-with-money, missing
  batch fields) raise ProjectionTerminalSkip: ProjectionFailureLog row +
  event visibly quarantined + stream advances (retry can never help —
  re-import reuses the same idempotency key).
- Operator-fixable config gaps (missing mapping/roles) raise
  ProjectionStateError: ProjectionFailureLog row + event NOT applied —
  self-heals on the next pass once the mapping is wired.
"""

import calendar
from datetime import date
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from accounting.models import JournalEntry
from accounting.payment_settlement_projection import PaymentSettlementProjection
from accounts.models import Company, CompanyMembership
from events.emitter import emit_event_no_actor
from events.types import (
    EventTypes,
    PaymentSettlementReceivedData,
    PlatformOrderPaidData,
    PlatformPayoutSettledData,
    PlatformRefundCreatedData,
)
from platform_connectors.projections import PlatformAccountingProjection
from projections.models import (
    FiscalPeriod,
    ProjectionAppliedEvent,
    ProjectionFailureLog,
)
from projections.write_barrier import projection_writes_allowed

pytestmark = pytest.mark.django_db

SETTLEMENT_PROJECTION = "payment_settlement"
PLATFORM_PROJECTION = "platform_accounting"


def _make_company():
    User = get_user_model()
    uid = uuid4().hex[:8]
    company = Company.objects.create(
        public_id=uuid4(),
        name=f"A157 Co {uid}",
        slug=f"a157-{uid}",
        default_currency="USD",
        functional_currency="EGP",
        is_active=True,
    )
    user = User.objects.create_user(
        public_id=uuid4(),
        email=f"owner-a157-{uid}@test.com",
        password="testpass123",
        name="A157 Owner",
    )
    user.active_company = company
    user.save()
    CompanyMembership.objects.create(
        public_id=uuid4(),
        company=company,
        user=user,
        role=CompanyMembership.Role.OWNER,
        is_active=True,
    )
    return company


def _open_period(company, when):
    last_day = calendar.monthrange(when.year, when.month)[1]
    with projection_writes_allowed():
        FiscalPeriod.objects.update_or_create(
            company=company,
            fiscal_year=when.year,
            period=when.month,
            defaults=dict(
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=when.replace(day=1),
                end_date=when.replace(day=last_day),
                status=FiscalPeriod.Status.OPEN,
            ),
        )


def _emit_settlement(company, batch_id, *, gross, net, fees, uncollected="0"):
    """Books-currency settlement (currency='') so no FX rate is needed —
    isolates the amount-sanity branches from the F27 missing-rate branch."""
    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
        aggregate_type="PaymentSettlement",
        aggregate_id=f"stripe:{batch_id}",
        idempotency_key=f"payment.settlement.received:stripe:{batch_id}",
        data=PaymentSettlementReceivedData(
            amount=gross,
            currency="",
            transaction_date=date.today().isoformat(),
            document_ref=batch_id,
            provider_normalized_code="stripe",
            external_system="stripe",
            payout_batch_id=batch_id,
            gross_amount=gross,
            fees=fees,
            net_amount=net,
            uncollected_amount=uncollected,
            payment_method="card",
            payout_date=date.today().isoformat(),
            line_items=[],
            provider_status="paid",
        ),
    )


def _failure_log(company, projection_name):
    return ProjectionFailureLog.objects.filter(company=company, projection_name=projection_name).first()


def _is_applied(company, projection_name, event):
    return ProjectionAppliedEvent.objects.filter(company=company, projection_name=projection_name, event=event).exists()


def _settlement_je_exists(company, batch_id):
    return JournalEntry.objects.filter(
        company=company,
        source_module=SETTLEMENT_PROJECTION,
        source_document=f"stripe:{batch_id}",
    ).exists()


# ─────────────────────────────────────────────────────────────────────────────
# Settlement projection — immutable-payload defects → TerminalSkip
# ─────────────────────────────────────────────────────────────────────────────


class TestSettlementImbalance:
    def test_imbalanced_settlement_quarantines_visibly(self):
        """gross != net+fees+uncollected: the exact silent branch behind the
        A20/MAY01-A real-world loss. Must produce a ProjectionFailureLog and
        advance (TerminalSkip), never a silent consume."""
        from stripe_connector.seed import setup_stripe_platform

        company = _make_company()
        _open_period(company, date.today())
        setup_stripe_platform(company)

        event = _emit_settlement(company, "po_imbalanced", gross="100.00", net="90.00", fees="5.00")
        projection = PaymentSettlementProjection()
        projection.process_pending(company)

        assert not _settlement_je_exists(company, "po_imbalanced")
        log = _failure_log(company, SETTLEMENT_PROJECTION)
        assert log is not None, "imbalance must write an operator-visible failure log"
        assert "imbalance" in log.message
        assert log.category == ProjectionFailureLog.Category.MISSING_CONFIG  # TerminalSkip mapping
        assert _is_applied(company, SETTLEMENT_PROJECTION, event), (
            "TerminalSkip must advance past the poisoned event (no head-of-line stall)"
        )

        # The stream is NOT stalled: a later well-formed settlement still posts.
        _emit_settlement(company, "po_good", gross="103.20", net="96.80", fees="6.40")
        projection.process_pending(company)
        assert _settlement_je_exists(company, "po_good")

    def test_zero_gross_with_money_quarantines_visibly(self):
        from stripe_connector.seed import setup_stripe_platform

        company = _make_company()
        _open_period(company, date.today())
        setup_stripe_platform(company)

        event = _emit_settlement(company, "po_zero_gross", gross="0", net="96.80", fees="6.40")
        PaymentSettlementProjection().process_pending(company)

        assert not _settlement_je_exists(company, "po_zero_gross")
        log = _failure_log(company, SETTLEMENT_PROJECTION)
        assert log is not None, "zero-gross with nonzero net/fees must be operator-visible"
        assert _is_applied(company, SETTLEMENT_PROJECTION, event)

    def test_all_zero_batch_stays_benign(self):
        """Guard against over-raising: a genuinely empty batch is a no-op —
        no JE, no failure log, event consumed."""
        from stripe_connector.seed import setup_stripe_platform

        company = _make_company()
        _open_period(company, date.today())
        setup_stripe_platform(company)

        event = _emit_settlement(company, "po_empty", gross="0", net="0", fees="0")
        PaymentSettlementProjection().process_pending(company)

        assert not _settlement_je_exists(company, "po_empty")
        assert _failure_log(company, SETTLEMENT_PROJECTION) is None
        assert _is_applied(company, SETTLEMENT_PROJECTION, event)

    def test_missing_batch_fields_quarantine_visibly(self):
        company = _make_company()
        _open_period(company, date.today())

        event = emit_event_no_actor(
            company=company,
            event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
            aggregate_type="PaymentSettlement",
            aggregate_id="broken:missing-fields",
            idempotency_key=f"payment.settlement.received:broken:{uuid4().hex[:8]}",
            data=PaymentSettlementReceivedData(
                amount="50.00",
                currency="",
                transaction_date=date.today().isoformat(),
                document_ref="",
                provider_normalized_code="",  # missing
                external_system="stripe",
                payout_batch_id="",  # missing
                gross_amount="50.00",
                fees="0",
                net_amount="50.00",
                uncollected_amount="0",
                payment_method="card",
                payout_date=date.today().isoformat(),
                line_items=[],
                provider_status="paid",
            ),
        )
        PaymentSettlementProjection().process_pending(company)

        log = _failure_log(company, SETTLEMENT_PROJECTION)
        assert log is not None, "structurally-invalid settlement must be operator-visible"
        assert "missing required fields" in log.message
        assert _is_applied(company, SETTLEMENT_PROJECTION, event)


# ─────────────────────────────────────────────────────────────────────────────
# Platform projection — operator-fixable config gaps → StateError (self-heal)
# ─────────────────────────────────────────────────────────────────────────────


def _emit_order_paid(company, order_id, amount="20.00"):
    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.PLATFORM_ORDER_PAID,
        aggregate_type="PlatformOrder",
        aggregate_id=order_id,
        idempotency_key=f"test.a157.order_paid:{order_id}",
        data=PlatformOrderPaidData(
            platform_slug="stripe",
            platform_order_id=order_id,
            order_name=order_id,
            amount=amount,
            subtotal=amount,
            total_tax="0",
            total_shipping="0",
            currency="USD",
            transaction_date="2026-06-20",
        ),
    )


class TestPlatformMissingMapping:
    def test_missing_mapping_is_visible_and_self_heals(self, company, owner_membership):
        """No Stripe mapping wired: the order event must surface in the
        failure log and stay UNPROCESSED, then post automatically once
        setup_stripe_platform wires the mapping. Before the fix the event
        was consumed on pass 1 — the JE never appeared even after wiring."""
        from stripe_connector.seed import setup_stripe_platform

        projection = PlatformAccountingProjection()
        event = _emit_order_paid(company, "ch_a157_selfheal")
        projection.process_pending(company)

        assert not JournalEntry.objects.filter(company=company, source_module="platform_stripe").exists()
        log = _failure_log(company, PLATFORM_PROJECTION)
        assert log is not None, "missing platform mapping must be operator-visible"
        assert log.category == ProjectionFailureLog.Category.MISSING_CONFIG
        assert not _is_applied(company, PLATFORM_PROJECTION, event), (
            "StateError must leave the event unprocessed so it can self-heal"
        )

        # Wire the mapping — the same event now posts without re-import.
        setup_stripe_platform(company)
        projection.process_pending(company)

        je = JournalEntry.objects.filter(company=company, source_module="platform_stripe").first()
        assert je is not None, "event must self-heal once the mapping is wired"

    def test_missing_revenue_role_names_the_role(self, company, owner_membership):
        from accounting.mappings import ModuleAccountMapping
        from stripe_connector.seed import setup_stripe_platform

        setup_stripe_platform(company)
        ModuleAccountMapping.objects.filter(company=company, role="SALES_REVENUE").delete()

        projection = PlatformAccountingProjection()
        event = emit_event_no_actor(
            company=company,
            event_type=EventTypes.PLATFORM_REFUND_CREATED,
            aggregate_type="PlatformRefund",
            aggregate_id="re_a157",
            idempotency_key="test.a157.refund:re_a157",
            data=PlatformRefundCreatedData(
                platform_slug="stripe",
                platform_refund_id="re_a157",
                order_number="ch_a157",
                amount="10.00",
                currency="USD",
                transaction_date="2026-06-21",
            ),
        )
        projection.process_pending(company)

        log = _failure_log(company, PLATFORM_PROJECTION)
        assert log is not None, "the refund branch was a BARE return — must now be visible"
        assert "SALES_REVENUE" in log.message
        assert not _is_applied(company, PLATFORM_PROJECTION, event)

    def test_missing_bank_role_on_payout_is_visible(self, company, owner_membership):
        from accounting.mappings import ModuleAccountMapping
        from stripe_connector.seed import setup_stripe_platform

        setup_stripe_platform(company)
        ModuleAccountMapping.objects.filter(company=company, role="CASH_BANK").delete()

        projection = PlatformAccountingProjection()
        event = emit_event_no_actor(
            company=company,
            event_type=EventTypes.PLATFORM_PAYOUT_SETTLED,
            aggregate_type="PlatformPayout",
            aggregate_id="po_a157",
            idempotency_key="test.a157.payout:po_a157",
            data=PlatformPayoutSettledData(
                platform_slug="stripe",
                platform_payout_id="po_a157",
                gross_amount="100.00",
                fees="3.00",
                net_amount="97.00",
                currency="USD",
                payout_date="2026-06-22",
            ),
        )
        projection.process_pending(company)

        log = _failure_log(company, PLATFORM_PROJECTION)
        assert log is not None
        assert "CASH_BANK" in log.message
        assert not _is_applied(company, PLATFORM_PROJECTION, event)
