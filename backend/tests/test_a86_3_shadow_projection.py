# tests/test_a86_3_shadow_projection.py
"""A86.3 (2026-05-26): ReconciliationProjection shadow-mode tests.

Lives in backend/tests/ (rather than reconciliation/tests/) because
it needs the central `company` / DB fixtures from backend/tests/conftest.py.
The framework-scaffolding tests that don't touch the DB stay under
reconciliation/tests/ (test_a86_1_scaffold.py, test_a86_2_event_payloads.py).

The projection consumes ReconciliationMatch*/Exception* events and
writes shadow fields (event_match_status, event_matched_journal_line,
event_match_confidence, event_last_match_event_id, event_confirmed_at)
on BankStatementLine. The existing match_status / matched_journal_line
/ match_confidence fields (the direct-mutation legacy path) MUST NOT
be touched here — A86.7 cutover swaps the operator UI read off the
legacy fields onto the shadow fields; until then they're independent.

Test scenarios:

- MatchConfirmed (auto / manual / rule / platform_payout_reconcile) →
  shadow fields populated; legacy fields untouched
- MatchConfirmed with difference_amount > 0 →
  status = MATCHED_WITH_DIFFERENCE (per A16)
- MatchUnmatched (final_status="UNMATCHED") → shadow fields cleared
- MatchUnmatched (final_status="EXCLUDED") → shadow status = EXCLUDED
- MatchProposed → projection records BUT NO shadow state change
  (the load-bearing advisory-vs-canonical contract)
- MatchRejected → projection records BUT NO shadow state change
- ExceptionRaised/Resolved → events consumed cleanly (no-op for A86.3)
- Re-processing same event is idempotent (framework guard)
- Missing bank_line raises ProjectionInvalidDataError (loud failure
  per finance_event_first_policy.md §8)
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.models import (
    Account,
    BankStatement,
    BankStatementLine,
    JournalEntry,
    JournalLine,
)
from events.emitter import emit_event_no_actor
from events.types import EventTypes
from projections.write_barrier import projection_writes_allowed
from reconciliation.event_types import (
    ReconciliationExceptionRaisedData,
    ReconciliationExceptionResolvedData,
    ReconciliationMatchConfirmedData,
    ReconciliationMatchProposedData,
    ReconciliationMatchRejectedData,
    ReconciliationMatchUnmatchedData,
)
from reconciliation.projections import ReconciliationProjection

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def bank_account(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="A86.3 Test Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def revenue_account(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="41001",
            name="A86.3 Test Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def bank_statement(db, company, bank_account):
    with projection_writes_allowed():
        return BankStatement.objects.create(
            company=company,
            account=bank_account,
            statement_date=date(2026, 4, 30),
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
            opening_balance=Decimal("0"),
            closing_balance=Decimal("1455.00"),
            currency="EGP",
            status=BankStatement.Status.IMPORTED,
            source="MANUAL",
        )


@pytest.fixture
def bank_line(db, company, bank_statement):
    with projection_writes_allowed():
        return BankStatementLine.objects.create(
            company=company,
            statement=bank_statement,
            line_date=date(2026, 4, 26),
            description="Test bank deposit for A86.3",
            amount=Decimal("1455.00"),
            transaction_type=BankStatementLine.TransactionType.DEPOSIT,
        )


@pytest.fixture
def journal_entry(db, company, bank_account, revenue_account):
    """A minimal JE the bank line can match against. Created in POSTED
    status with a single line on bank_account — enough for the
    projection's FK lookup."""
    with projection_writes_allowed():
        entry = JournalEntry.objects.create(
            company=company,
            date=date(2026, 4, 26),
            period=4,
            memo="A86.3 test JE",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-A86-3-1",
        )
        JournalLine.objects.create(
            company=company,
            entry=entry,
            line_no=1,
            account=bank_account,
            debit=Decimal("1455.00"),
            credit=Decimal("0"),
        )
        JournalLine.objects.create(
            company=company,
            entry=entry,
            line_no=2,
            account=revenue_account,
            debit=Decimal("0"),
            credit=Decimal("1455.00"),
        )
    return entry


@pytest.fixture
def cash_line(db, journal_entry, bank_account):
    """The bank-side journal line — what the bank_line will match against."""
    return JournalLine.objects.get(entry=journal_entry, account=bank_account)


# =============================================================================
# Event-emission helpers
# =============================================================================


def _emit_match_confirmed(
    company,
    bank_line,
    journal_line,
    *,
    confirmation_kind: str = "auto",
    confidence: str = "92.5",
    difference_amount: str = "0",
    suffix: str = "",
):
    """Idempotency key includes a suffix so tests can emit multiple
    Confirmed events for the same pair (e.g., to test idempotency on
    re-process)."""
    data = ReconciliationMatchConfirmedData(
        bank_line_public_id=str(bank_line.public_id),
        journal_line_public_id=str(journal_line.public_id),
        match_kind="settlement_clearance",
        confidence=confidence,
        confirmation_kind=confirmation_kind,
        confirmed_at="2026-04-26T10:30:00+00:00",
        difference_amount=difference_amount,
        difference_reason="UNRESOLVED",
        statement_date="2026-04-26",
    )
    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        aggregate_type="ReconciliationMatch",
        aggregate_id=f"{bank_line.public_id}:{journal_line.public_id}",
        idempotency_key=f"reconciliation.match_confirmed:{bank_line.public_id}:{journal_line.public_id}:{suffix}",
        data=data,
    )


def _emit_match_unmatched(company, bank_line, journal_line, *, final_status="UNMATCHED", suffix=""):
    data = ReconciliationMatchUnmatchedData(
        bank_line_public_id=str(bank_line.public_id),
        previously_matched_journal_line_public_id=str(journal_line.public_id),
        match_kind="settlement_clearance",
        unmatched_at="2026-04-27T09:00:00+00:00",
        unmatch_reason="Test unmatch for A86.3",
        final_status=final_status,
    )
    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_UNMATCHED,
        aggregate_type="ReconciliationMatch",
        aggregate_id=f"{bank_line.public_id}:{journal_line.public_id}",
        idempotency_key=f"reconciliation.match_unmatched:{bank_line.public_id}:{journal_line.public_id}:{suffix}",
        data=data,
    )


def _emit_match_proposed(company, bank_line, journal_line, *, suffix=""):
    data = ReconciliationMatchProposedData(
        bank_line_public_id=str(bank_line.public_id),
        journal_line_public_id=str(journal_line.public_id),
        match_kind="settlement_clearance",
        confidence="65",
        proposer="auto_match_settlement_prepass_v1",
        proposed_at="2026-04-26T10:30:00+00:00",
        proposer_metadata={"matched_on": "amount+date"},
    )
    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_PROPOSED,
        aggregate_type="ReconciliationMatch",
        aggregate_id=f"{bank_line.public_id}:{journal_line.public_id}",
        idempotency_key=f"reconciliation.match_proposed:{bank_line.public_id}:{journal_line.public_id}:{suffix}",
        data=data,
    )


def _emit_match_rejected(company, bank_line, journal_line, *, suffix=""):
    data = ReconciliationMatchRejectedData(
        bank_line_public_id=str(bank_line.public_id),
        journal_line_public_id=str(journal_line.public_id),
        rejected_at="2026-04-26T11:00:00+00:00",
        rejection_reason="Not the right batch; testing rejection.",
    )
    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_REJECTED,
        aggregate_type="ReconciliationMatch",
        aggregate_id=f"{bank_line.public_id}:{journal_line.public_id}",
        idempotency_key=f"reconciliation.match_rejected:{bank_line.public_id}:{journal_line.public_id}:{suffix}",
        data=data,
    )


def _emit_exception_raised(company, bank_line, *, suffix=""):
    exception_id = f"exc-{suffix or 'x1'}"
    data = ReconciliationExceptionRaisedData(
        exception_public_id=exception_id,
        bank_line_public_id=str(bank_line.public_id),
        exception_kind="orphan_bank_deposit",
        severity="warning",
        title="Test exception",
        detail="A86.3 no-op smoke test",
        detected_at="2026-04-27T08:00:00+00:00",
    )
    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.RECONCILIATION_EXCEPTION_RAISED,
        aggregate_type="ReconciliationException",
        aggregate_id=exception_id,
        idempotency_key=f"reconciliation.exception_raised:{exception_id}",
        data=data,
    )


def _emit_exception_resolved(company, *, exception_id="exc-x1", suffix=""):
    data = ReconciliationExceptionResolvedData(
        exception_public_id=exception_id,
        resolved_at="2026-04-27T10:00:00+00:00",
        resolution_kind="ignored",
        resolution_note="A86.3 no-op smoke test resolution.",
    )
    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.RECONCILIATION_EXCEPTION_RESOLVED,
        aggregate_type="ReconciliationException",
        aggregate_id=exception_id,
        idempotency_key=f"reconciliation.exception_resolved:{exception_id}:{suffix}",
        data=data,
    )


# =============================================================================
# MatchConfirmed: shadow writes happy path
# =============================================================================


@pytest.mark.django_db
def test_match_confirmed_writes_shadow_fields(company, bank_line, cash_line):
    """Happy path: a Confirmed event populates the shadow fields with
    the values from the event payload."""
    _emit_match_confirmed(company, bank_line, cash_line)

    ReconciliationProjection().process_pending(company)

    bank_line.refresh_from_db()
    assert bank_line.event_match_status == BankStatementLine.MatchStatus.AUTO_MATCHED
    assert bank_line.event_matched_journal_line_id == cash_line.id
    assert bank_line.event_match_confidence == Decimal("92.50")
    assert bank_line.event_last_match_event_id is not None
    assert bank_line.event_confirmed_at is not None


@pytest.mark.django_db
def test_match_confirmed_with_manual_kind_maps_to_MANUAL_MATCHED(company, bank_line, cash_line):
    """confirmation_kind='manual' maps to MANUAL_MATCHED status."""
    _emit_match_confirmed(company, bank_line, cash_line, confirmation_kind="manual")

    ReconciliationProjection().process_pending(company)

    bank_line.refresh_from_db()
    assert bank_line.event_match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED


@pytest.mark.django_db
def test_match_confirmed_with_difference_maps_to_MATCHED_WITH_DIFFERENCE(company, bank_line, cash_line):
    """difference_amount > 0 forces MATCHED_WITH_DIFFERENCE regardless
    of confirmation_kind — A16 semantics."""
    _emit_match_confirmed(company, bank_line, cash_line, confirmation_kind="auto", difference_amount="200.00")

    ReconciliationProjection().process_pending(company)

    bank_line.refresh_from_db()
    assert bank_line.event_match_status == BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE


@pytest.mark.django_db
def test_match_confirmed_DOES_NOT_touch_legacy_match_status(company, bank_line, cash_line):
    """SHADOW MODE INVARIANT: the projection writes only event_* fields.
    The legacy match_status / matched_journal_line / match_confidence
    fields are owned by the direct-mutation path until A86.7 cutover."""
    # Verify baseline: legacy fields untouched at fixture-creation time.
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED
    assert bank_line.matched_journal_line is None
    assert bank_line.match_confidence is None

    _emit_match_confirmed(company, bank_line, cash_line)
    ReconciliationProjection().process_pending(company)

    bank_line.refresh_from_db()
    # Legacy fields STILL untouched — only shadow fields moved.
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED, (
        "A86.3 shadow projection must NOT write the legacy match_status field. "
        "That's the direct-mutation path's job until A86.7 cutover."
    )
    assert bank_line.matched_journal_line is None
    assert bank_line.match_confidence is None
    # And shadow fields ARE populated.
    assert bank_line.event_match_status == BankStatementLine.MatchStatus.AUTO_MATCHED


# =============================================================================
# MatchUnmatched: shadow clears
# =============================================================================


@pytest.mark.django_db
def test_match_unmatched_clears_shadow_fields(company, bank_line, cash_line):
    """Unmatched after a Confirmed: shadow status → UNMATCHED, FK + confidence cleared."""
    _emit_match_confirmed(company, bank_line, cash_line)
    ReconciliationProjection().process_pending(company)
    bank_line.refresh_from_db()
    assert bank_line.event_match_status == BankStatementLine.MatchStatus.AUTO_MATCHED

    _emit_match_unmatched(company, bank_line, cash_line)
    ReconciliationProjection().process_pending(company)

    bank_line.refresh_from_db()
    assert bank_line.event_match_status == BankStatementLine.MatchStatus.UNMATCHED
    assert bank_line.event_matched_journal_line_id is None
    assert bank_line.event_match_confidence is None
    assert bank_line.event_confirmed_at is None


@pytest.mark.django_db
def test_match_unmatched_with_excluded_sets_EXCLUDED_status(company, bank_line, cash_line):
    """exclude_line path: final_status='EXCLUDED' marks the bank line
    as out-of-scope (shadow → EXCLUDED, FK still cleared)."""
    _emit_match_confirmed(company, bank_line, cash_line)
    ReconciliationProjection().process_pending(company)

    _emit_match_unmatched(company, bank_line, cash_line, final_status="EXCLUDED")
    ReconciliationProjection().process_pending(company)

    bank_line.refresh_from_db()
    assert bank_line.event_match_status == BankStatementLine.MatchStatus.EXCLUDED
    assert bank_line.event_matched_journal_line_id is None


# =============================================================================
# Advisory contract: Proposed / Rejected don't mutate shadow state
# =============================================================================


@pytest.mark.django_db
def test_match_proposed_does_NOT_mutate_shadow_state(company, bank_line, cash_line):
    """LOAD-BEARING: a Proposed event is advisory. The projection
    records it (logs, future suggestion queue) but the bank_line's
    shadow match state is unchanged. This is the line between AI
    suggestion and operator/rule decision."""
    _emit_match_proposed(company, bank_line, cash_line)

    ReconciliationProjection().process_pending(company)

    bank_line.refresh_from_db()
    # Shadow status NEVER moves on Proposed.
    assert bank_line.event_match_status == ""
    assert bank_line.event_matched_journal_line_id is None
    assert bank_line.event_match_confidence is None
    assert bank_line.event_last_match_event_id is None
    assert bank_line.event_confirmed_at is None


@pytest.mark.django_db
def test_match_rejected_does_NOT_mutate_shadow_state(company, bank_line, cash_line):
    """Rejection of a Proposed match doesn't touch shadow state either —
    the line was never Confirmed, so there's nothing to clear."""
    _emit_match_proposed(company, bank_line, cash_line)
    _emit_match_rejected(company, bank_line, cash_line)

    ReconciliationProjection().process_pending(company)

    bank_line.refresh_from_db()
    assert bank_line.event_match_status == ""
    assert bank_line.event_matched_journal_line_id is None


@pytest.mark.django_db
def test_proposed_then_confirmed_only_confirms_writes_shadow(company, bank_line, cash_line):
    """Common flow: agent/heuristic Proposes → operator Confirms. Only
    the Confirmed event causes a shadow write."""
    _emit_match_proposed(company, bank_line, cash_line)
    ReconciliationProjection().process_pending(company)
    bank_line.refresh_from_db()
    assert bank_line.event_match_status == ""

    _emit_match_confirmed(company, bank_line, cash_line, confirmation_kind="manual")
    ReconciliationProjection().process_pending(company)

    bank_line.refresh_from_db()
    assert bank_line.event_match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED


# =============================================================================
# Exception events: A86.3 no-op (consumed without crash)
# =============================================================================


@pytest.mark.django_db
def test_exception_raised_and_resolved_consume_without_crash(company, bank_line):
    """A86.3 doesn't have an exception read model yet, but the
    projection consumes the events (bookmark advances) without
    crashing. A later chunk adds the exception read model."""
    _emit_exception_raised(company, bank_line)
    _emit_exception_resolved(company)

    proj = ReconciliationProjection()
    processed = proj.process_pending(company)
    assert processed == 2

    # Re-process: idempotent, no further events.
    processed = proj.process_pending(company)
    assert processed == 0


# =============================================================================
# Idempotency
# =============================================================================


@pytest.mark.django_db
def test_reprocessing_same_event_is_idempotent(company, bank_line, cash_line):
    """Framework's ProjectionAppliedEvent guard — re-running
    process_pending after the same events does nothing (no extra
    writes, no double-application of the shadow state)."""
    _emit_match_confirmed(company, bank_line, cash_line)
    proj = ReconciliationProjection()

    first = proj.process_pending(company)
    assert first == 1
    bank_line.refresh_from_db()
    initial_event_id = bank_line.event_last_match_event_id

    second = proj.process_pending(company)
    assert second == 0, "Re-running should mark 0 new events processed"

    bank_line.refresh_from_db()
    assert bank_line.event_last_match_event_id == initial_event_id


# =============================================================================
# Loud failures (per finance_event_first_policy.md §8)
# =============================================================================


@pytest.mark.django_db
def test_unknown_bank_line_raises_ProjectionInvalidDataError(company, cash_line):
    """A MatchConfirmed referencing a bank_line that doesn't exist
    raises ProjectionInvalidDataError so /finance/exceptions surfaces
    the failure. Silent-pass would let bad data accumulate."""
    import uuid

    data = ReconciliationMatchConfirmedData(
        bank_line_public_id=str(uuid.uuid4()),  # never created
        journal_line_public_id=str(cash_line.public_id),
        match_kind="settlement_clearance",
        confirmation_kind="auto",
    )
    emit_event_no_actor(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        aggregate_type="ReconciliationMatch",
        aggregate_id="bogus",
        idempotency_key="reconciliation.match_confirmed:test_unknown_bank_line",
        data=data,
    )

    proj = ReconciliationProjection()
    # process_pending catches the exception via on_error (writes to
    # ProjectionFailureLog per A80) and returns; no exception bubbles up.
    # The failure log row is what proves loud-failure.
    proj.process_pending(company)

    from projections.models import ProjectionFailureLog

    failures = ProjectionFailureLog.objects.filter(
        company=company,
        projection_name="reconciliation",
    )
    assert failures.count() == 1
    assert "unknown" in failures.first().message.lower()


@pytest.mark.django_db
def test_unknown_journal_line_raises_ProjectionInvalidDataError(company, bank_line):
    """Same loud-failure contract for a missing journal_line FK target."""
    import uuid

    data = ReconciliationMatchConfirmedData(
        bank_line_public_id=str(bank_line.public_id),
        journal_line_public_id=str(uuid.uuid4()),  # never created
        match_kind="settlement_clearance",
        confirmation_kind="auto",
    )
    emit_event_no_actor(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        aggregate_type="ReconciliationMatch",
        aggregate_id="bogus2",
        idempotency_key="reconciliation.match_confirmed:test_unknown_journal_line",
        data=data,
    )

    proj = ReconciliationProjection()
    proj.process_pending(company)

    from projections.models import ProjectionFailureLog

    failures = ProjectionFailureLog.objects.filter(
        company=company,
        projection_name="reconciliation",
    )
    assert failures.count() == 1


# =============================================================================
# Sanity: projection is registered
# =============================================================================


def test_reconciliation_projection_is_registered():
    """The framework discovered the projection via
    AppConfig.projections at startup."""
    from projections.base import projection_registry

    proj = projection_registry.get("reconciliation")
    assert proj is not None
    assert isinstance(proj, ReconciliationProjection)


def test_reconciliation_projection_consumes_all_six_event_types():
    """The consumes list covers every event type defined in A86.2 —
    if A86 ever adds another event type, this test fails until the
    projection is updated."""
    proj = ReconciliationProjection()
    consumes = set(proj.consumes)
    expected = {
        EventTypes.RECONCILIATION_MATCH_PROPOSED,
        EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        EventTypes.RECONCILIATION_MATCH_REJECTED,
        EventTypes.RECONCILIATION_MATCH_UNMATCHED,
        EventTypes.RECONCILIATION_EXCEPTION_RAISED,
        EventTypes.RECONCILIATION_EXCEPTION_RESOLVED,
    }
    assert consumes == expected
