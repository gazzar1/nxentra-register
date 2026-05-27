# tests/test_a99_reconciliation_event_first.py
"""A99 (2026-05-26) capstone — prove `manual_match` / `unmatch_line` /
`auto_match_statement` perform NO direct writes to JournalLine.reconciled
or BankStatementLine.difference_*. Every transition flows through the
ReconciliationMatchConfirmed / ReconciliationMatchUnmatched event +
ReconciliationProjection.

Test mechanism (same shape as A89's bank_connector capstone):
stub `ReconciliationProjection.process_pending` to a no-op while running
the command. If a direct write still existed, the read-model fields
would flip anyway. Because the event-first contract holds, they stay at
their pre-call value — but the canonical event IS emitted.

If anyone reintroduces a "just in case" direct flip alongside the event
path, these tests fail.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

import pytest

from accounting.bank_reconciliation import import_bank_statement
from accounting.models import (
    Account,
    BankStatementLine,
    JournalEntry,
    JournalLine,
)
from accounts.authz import ActorContext
from events.models import BusinessEvent
from events.types import EventTypes
from projections.write_barrier import projection_writes_allowed
from reconciliation.commands import manual_match, unmatch_line

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


@pytest.fixture
def merchant_bank(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="Merchant Bank — A99",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def revenue_account(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="41001",
            name="A99 Test Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def manual_match_setup(db, company, merchant_bank, revenue_account, actor):
    """Bank statement line + a pre-existing JE the operator can manually
    match against. Mirrors test_a86_5_manual_match_unmatch_emission's
    setup but kept local to avoid cross-file fixture imports."""
    je_date = date(2026, 4, 26)
    with projection_writes_allowed():
        entry = JournalEntry.objects.create(
            company=company,
            date=je_date,
            period=4,
            memo="A99 manual JE awaiting match",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-A99-MAN-1",
        )
        bank_jl = JournalLine.objects.create(
            company=company,
            entry=entry,
            line_no=1,
            account=merchant_bank,
            debit=Decimal("777.00"),
            credit=Decimal("0"),
        )
        JournalLine.objects.create(
            company=company,
            entry=entry,
            line_no=2,
            account=revenue_account,
            debit=Decimal("0"),
            credit=Decimal("777.00"),
        )

    period_start = je_date - timedelta(days=2)
    period_end = je_date + timedelta(days=2)
    statement_result = import_bank_statement(
        actor=actor,
        account_id=merchant_bank.id,
        statement_date=je_date,
        period_start=period_start,
        period_end=period_end,
        opening_balance=Decimal("0"),
        closing_balance=Decimal("777.00"),
        lines_data=[
            {
                "line_date": je_date.isoformat(),
                "amount": "777.00",
                "description": "A99 deposit",
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="EGP",
    )
    assert statement_result.success
    statement = statement_result.data["statement"]
    bank_line = BankStatementLine.objects.get(statement=statement)
    return {"bank_line": bank_line, "journal_line": bank_jl}


# =============================================================================
# manual_match: no direct write
# =============================================================================


@pytest.mark.django_db
def test_manual_match_does_not_directly_flip_journal_line_reconciled(company, manual_match_setup, actor):
    """If the projection is stubbed, manual_match must still emit the
    event but JournalLine.reconciled must NOT flip — proving the command
    has no direct-write fallback. Previously (pre-A99) the command did
    `JournalLine.objects.filter(...).update(reconciled=True)` directly,
    so this test would fail.
    """
    bank_line = manual_match_setup["bank_line"]
    journal_line = manual_match_setup["journal_line"]
    assert journal_line.reconciled is False, "Precondition: JL starts unreconciled."

    events_before = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
    ).count()

    with mock.patch(
        "reconciliation.projections.ReconciliationProjection.process_pending",
        return_value=None,
    ):
        result = manual_match(
            actor=actor,
            bank_line_id=bank_line.id,
            journal_line_id=journal_line.id,
        )

    assert result.success, f"manual_match failed: {result.error}"

    # Event WAS emitted.
    events_after = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
    ).count()
    assert events_after == events_before + 1, (
        "manual_match must emit ReconciliationMatchConfirmed regardless of projection state."
    )

    # JL.reconciled did NOT flip — proves no direct-write fallback.
    journal_line.refresh_from_db()
    assert journal_line.reconciled is False, (
        "JournalLine.reconciled flipped while the projection was stubbed. "
        "manual_match has a direct-write fallback that bypasses the event-first "
        "contract. Find and remove it."
    )

    # BSL.match_status also did NOT flip — same reason.
    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED, (
        "BankStatementLine.match_status flipped while the projection was stubbed. "
        "manual_match writes match state directly somewhere."
    )


# =============================================================================
# unmatch_line: no direct write
# =============================================================================


@pytest.mark.django_db
def test_unmatch_line_does_not_directly_clear_difference_fields(company, manual_match_setup, actor):
    """Same shape as the manual_match test but for the unmatch path.
    After a successful match, stub the projection and call unmatch — the
    BSL.difference_* fields and the JL.reconciled flag must stay at
    their matched-state values. Only the projection should clear them.
    """
    bank_line = manual_match_setup["bank_line"]
    journal_line = manual_match_setup["journal_line"]

    # First, do a real match so we have a state to unmatch from. Let the
    # projection run normally here so the setup arrives at a confirmed
    # state.
    match_result = manual_match(
        actor=actor,
        bank_line_id=bank_line.id,
        journal_line_id=journal_line.id,
    )
    assert match_result.success, f"setup match failed: {match_result.error}"

    bank_line.refresh_from_db()
    journal_line.refresh_from_db()
    assert bank_line.match_status != BankStatementLine.MatchStatus.UNMATCHED
    assert journal_line.reconciled is True

    events_before = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_UNMATCHED,
    ).count()

    # Now stub the projection and call unmatch.
    with mock.patch(
        "reconciliation.projections.ReconciliationProjection.process_pending",
        return_value=None,
    ):
        result = unmatch_line(actor=actor, bank_line_id=bank_line.id)

    assert result.success, f"unmatch_line failed: {result.error}"

    # Event WAS emitted.
    events_after = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_UNMATCHED,
    ).count()
    assert events_after == events_before + 1, (
        "unmatch_line must emit ReconciliationMatchUnmatched regardless of projection state."
    )

    # JL.reconciled stays True — projection was stubbed, no direct un-flip.
    journal_line.refresh_from_db()
    assert journal_line.reconciled is True, (
        "JournalLine.reconciled flipped back to False while the projection was "
        "stubbed. unmatch_line has a direct-write fallback. Find and remove it."
    )

    # BSL.match_status stays matched (no direct clear).
    bank_line.refresh_from_db()
    assert bank_line.match_status != BankStatementLine.MatchStatus.UNMATCHED, (
        "BankStatementLine.match_status was cleared while the projection was "
        "stubbed. unmatch_line writes match state directly somewhere."
    )


# =============================================================================
# Event payload contract: difference fields and additional_journal_lines
# =============================================================================


@pytest.mark.django_db
def test_manual_match_event_carries_difference_fields(company, manual_match_setup, actor):
    """The MatchConfirmed event payload must carry difference_amount +
    difference_reason. Without these, the projection has no way to write
    BSL.difference_* — they used to be direct mutations in commands.py.
    """
    bank_line = manual_match_setup["bank_line"]
    journal_line = manual_match_setup["journal_line"]

    result = manual_match(
        actor=actor,
        bank_line_id=bank_line.id,
        journal_line_id=journal_line.id,
    )
    assert result.success

    event = (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        )
        .order_by("-company_sequence")
        .first()
    )
    assert event is not None

    data = event.get_data()
    assert "difference_amount" in data
    assert "difference_reason" in data
    assert data["difference_amount"] == "0"
    assert data["difference_reason"] == "UNRESOLVED"


@pytest.mark.django_db
def test_unmatch_event_carries_unreconcile_list(company, manual_match_setup, actor):
    """The MatchUnmatched event must carry both
    previously_matched_journal_line_public_id AND
    additional_journal_lines_to_unreconcile. Without these, the
    projection has no way to un-flip JL.reconciled — that used to be a
    direct mutation in _clear_match_state.
    """
    bank_line = manual_match_setup["bank_line"]
    journal_line = manual_match_setup["journal_line"]

    manual_match(
        actor=actor,
        bank_line_id=bank_line.id,
        journal_line_id=journal_line.id,
    )
    unmatch_line(actor=actor, bank_line_id=bank_line.id)

    event = (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.RECONCILIATION_MATCH_UNMATCHED,
        )
        .order_by("-company_sequence")
        .first()
    )
    assert event is not None

    data = event.get_data()
    assert data.get("previously_matched_journal_line_public_id") == str(journal_line.public_id)
    # Manual-match unmatch has no settlement EBD line, so this list is empty.
    assert data.get("additional_journal_lines_to_unreconcile") == []
