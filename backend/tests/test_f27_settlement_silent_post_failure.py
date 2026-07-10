# tests/test_f27_settlement_silent_post_failure.py
"""F27 (2026-07-10) — settlement projection must FAIL LOUDLY, not silently draft.

Found live in the Run-2 FX E2E: a Stripe payout settlement whose USD line had
no USD->EGP rate for its payout date left a committed DRAFT journal entry,
burned a journal_entry_number (a gap in the GL sequence), wrote NO
ProjectionFailureLog, and marked the event applied — so the settlement silently
vanished from the posted ledger / trial balance and never self-healed even
after the operator added the rate.

Root cause: `PaymentSettlementProjection.handle()` did
`logger.error(...); return` on a create/save/post `CommandResult.fail` instead
of raising. A plain `return` inside `with transaction.atomic()` COMMITS — so the
orphan DRAFT + the burned sequence number persisted and the bookmark advanced.

The fix mirrors the Shopify order path: raise `ProjectionCommandFailedError` for
a transient refusal (missing FX rate) so it surfaces in /finance/exceptions AND
self-heals once the rate exists; raise `ProjectionTerminalSkip` for a closed
period so it quarantines without head-of-line-stalling the settlement stream.
Raising rolls back both the orphan DRAFT and the burned sequence number.
"""

import calendar
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from accounting.models import CompanySequence, ExchangeRate, JournalEntry
from accounting.payment_settlement_projection import (
    PaymentSettlementProjection,
    _raise_settlement_command_failure,
)
from accounts.models import Company, CompanyMembership
from events.emitter import emit_event_no_actor
from events.models import BusinessEvent
from events.types import EventTypes, PaymentSettlementReceivedData
from projections.exceptions import ProjectionCommandFailedError, ProjectionTerminalSkip
from projections.models import (
    FiscalPeriod,
    ProjectionAppliedEvent,
    ProjectionFailureLog,
)
from projections.write_barrier import projection_writes_allowed

USD_EGP_RATE = Decimal("48")
SEQ_NAME = "journal_entry_number"


def _make_company():
    User = get_user_model()
    uid = uuid4().hex[:8]
    company = Company.objects.create(
        public_id=uuid4(),
        name=f"F27 Co {uid}",
        slug=f"f27-{uid}",
        default_currency="USD",
        functional_currency="EGP",
        is_active=True,
    )
    user = User.objects.create_user(
        public_id=uuid4(),
        email=f"owner-f27-{uid}@test.com",
        password="testpass123",
        name="F27 Owner",
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


def _open_period(company, when, status=FiscalPeriod.Status.OPEN):
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
                status=status,
            ),
        )


def _emit_usd_settlement(company, batch_id, payout_date):
    """A USD Stripe payout on EGP books — post_journal_entry must find a
    USD->EGP rate for `payout_date` or refuse."""
    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
        aggregate_type="PaymentSettlement",
        aggregate_id=f"stripe:{batch_id}",
        idempotency_key=f"payment.settlement.received:stripe:{batch_id}",
        data=PaymentSettlementReceivedData(
            amount="103.20",
            currency="USD",
            transaction_date=payout_date.isoformat(),
            document_ref=batch_id,
            provider_normalized_code="stripe",
            external_system="stripe",
            payout_batch_id=batch_id,
            gross_amount="103.20",
            fees="6.40",
            net_amount="96.80",
            uncollected_amount="0",
            payment_method="card",
            payout_date=payout_date.isoformat(),
            line_items=[
                {"order_id": "ch_a", "gross": "51.60", "fee": "3.20", "net": "48.40", "status": "charge"},
                {"order_id": "ch_b", "gross": "51.60", "fee": "3.20", "net": "48.40", "status": "charge"},
            ],
            provider_status="paid",
        ),
    )


def _seq_next_value(company):
    seq = CompanySequence.objects.filter(company=company, name=SEQ_NAME).first()
    return None if seq is None else seq.next_value


@pytest.mark.django_db
def test_missing_rate_settlement_does_not_silently_draft_and_self_heals(db):
    """The P1: a USD settlement with no rate for its date must NOT leave a
    committed DRAFT + burned sequence number; it must surface in
    /finance/exceptions and self-heal once the rate is added."""
    from stripe_connector.seed import setup_stripe_platform

    company = _make_company()
    _open_period(company, date.today())  # open period → isolates the missing-rate cause
    setup_stripe_platform(company)

    batch = "po_f27_missing_rate"
    source_document = f"stripe:{batch}"
    _emit_usd_settlement(company, batch, date.today())

    # --- First pass: post fails (USD line, no USD->EGP rate on file) ---
    PaymentSettlementProjection().process_pending(company)

    # 1) No orphan DRAFT committed (pre-fix left a Status.DRAFT row here).
    assert not JournalEntry.objects.filter(
        company=company, source_module="payment_settlement", source_document=source_document
    ).exists()

    # 2) No burned sequence number (pre-fix advanced next_value to 2).
    assert _seq_next_value(company) in (None, 1)

    # 3) The failure is operator-visible (pre-fix wrote nothing).
    event = BusinessEvent.objects.get(company=company, event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED)
    failure = ProjectionFailureLog.objects.get(company=company, projection_name="payment_settlement", event=event)
    assert failure.category == ProjectionFailureLog.Category.DOWNSTREAM_FAILED
    assert "exchange rate" in failure.message.lower()

    # 4) The event is NOT marked applied — it stays available to self-heal.
    assert not ProjectionAppliedEvent.objects.filter(
        company=company, projection_name="payment_settlement", event=event
    ).exists()

    # --- Operator adds the missing rate; the next sweep self-heals ---
    ExchangeRate.objects.create(
        company=company,
        from_currency="USD",
        to_currency="EGP",
        rate=USD_EGP_RATE,
        effective_date=date.today(),
        rate_type="SPOT",
    )
    PaymentSettlementProjection().process_pending(company)

    je = JournalEntry.objects.get(company=company, source_module="payment_settlement", source_document=source_document)
    assert je.status == JournalEntry.Status.POSTED
    assert je.currency == "USD"
    assert Decimal(str(je.exchange_rate)) == USD_EGP_RATE
    # First (and only) posted entry is 000001 — no number was burned before it.
    assert je.entry_number == "JE-000001"


@pytest.mark.django_db
def test_failure_helper_classifies_by_override_period_not_just_date(db):
    """The two-arm decision uses the SAME gate post applies —
    can_post_to_period(actor, date, period=entry.period): OPEN → transient
    ProjectionCommandFailedError (visible + self-heals), CLOSED/undefined →
    terminal ProjectionTerminalSkip (quarantine, no head-of-line stall).

    Adversarial-review P2: an A85 period_override whose period differs in
    OPEN/CLOSED status from the date's calendar-month period must be classified
    by the OVERRIDE period (entry.period), not a date-only lookup — else it
    misroutes into a permanent head-of-line stall or a wrong quarantine."""
    from accounts.authz import system_actor_for_company

    company = _make_company()
    actor = system_actor_for_company(company)

    # July 2026 (period 7) OPEN; June 2026 (period 6) CLOSED — same fiscal year.
    open_date = date(2026, 7, 15)
    closed_date = date(2026, 6, 15)
    _open_period(company, open_date)  # period 7 OPEN
    _open_period(company, closed_date, status=FiscalPeriod.Status.CLOSED)  # period 6 CLOSED

    # (a) No override, OPEN date → transient (retriable) arm.
    with projection_writes_allowed(), pytest.raises(ProjectionCommandFailedError):
        _raise_settlement_command_failure(
            actor,
            open_date.isoformat(),
            None,
            "stripe:open",
            "post_journal_entry",
            "Missing USD->EGP rate for the date.",
        )

    # (b) No override, CLOSED date → terminal arm.
    with projection_writes_allowed(), pytest.raises(ProjectionTerminalSkip):
        _raise_settlement_command_failure(
            actor,
            closed_date.isoformat(),
            None,
            "stripe:date_closed",
            "post_journal_entry",
            "Fiscal period is closed.",
        )

    # (c) THE P2 regression: the date's own period (July) is OPEN, but the
    # override period (June, entry.period=6) is CLOSED → terminal. A date-only
    # classifier would have wrongly said "open" → a permanent head-of-line stall.
    class _EntryWithOverridePeriod:
        period = 6  # only .period is read by the helper

    with projection_writes_allowed(), pytest.raises(ProjectionTerminalSkip):
        _raise_settlement_command_failure(
            actor,
            open_date.isoformat(),
            _EntryWithOverridePeriod(),
            "stripe:override_closed",
            "post_journal_entry",
            "period closed",
        )
