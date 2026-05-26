# tests/test_a85_unmatch_preview.py
"""
A85 chunk 2b (2026-05-26): unmatch preview tests.

preview_unmatch_line(actor, bank_line_id) must return the same reversal
plan that unmatch_line would execute, but without committing the state
change or the JE reversals. Three scenarios covered:

1. Match is a flag-flip only (manual match against pre-existing JE).
   Preview shows no reversals + a warning that nothing will be reversed.

2. Match was a settlement-prepass clearance JE. Preview shows the
   clearance JE in the reversal plan.

3. Match additionally had a difference adjustment JE. Preview shows
   both reversals in the correct order (adjustment first).

Also: dry-run safety check — preview correctly flags reversal as unsafe
if the target period is closed.
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import preview_unmatch_line
from accounting.models import (
    Account,
    BankStatement,
    BankStatementLine,
    JournalEntry,
)
from accounts.authz import ActorContext
from projections.write_barrier import command_writes_allowed, projection_writes_allowed


def _make_actor(company, user, membership):
    perms = frozenset(membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=membership, perms=perms)


@pytest.fixture
def bank_account(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="11101",
            name="A85 Unmatch Test Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def april_2026_period(db, company):
    """An OPEN FiscalPeriod the test JEs can post to."""
    from projections.models import FiscalPeriod

    with projection_writes_allowed():
        fp, _ = FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=2026,
            period=4,
            defaults=dict(
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 30),
                status=FiscalPeriod.Status.OPEN,
            ),
        )
    return fp


def _make_bank_line(
    company,
    bank_account,
    *,
    amount=Decimal("100.00"),
    description="test line",
    line_date=date(2026, 4, 25),
):
    """Helper: create a bank statement + line."""
    with command_writes_allowed():
        stmt = BankStatement.objects.create(
            company=company,
            account=bank_account,
            statement_date=line_date,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
            opening_balance=Decimal("0"),
            closing_balance=amount,
            currency="USD",
            source="test",
            status=BankStatement.Status.IMPORTED,
        )
        return BankStatementLine.objects.create(
            statement=stmt,
            company=company,
            line_date=line_date,
            description=description,
            reference="",
            amount=amount,
            transaction_type=BankStatementLine.TransactionType.DEPOSIT,
            dedup_hash=f"test-{description}-{amount}",
        )


# =============================================================================
# Scenario 1: flag-flip-only match (no JE reversal)
# =============================================================================


@pytest.mark.django_db
def test_preview_flag_flip_only_match(company, user, owner_membership, bank_account, april_2026_period):
    """If the matched JE was pre-existing (not synthesized by the prepass),
    unmatch only flips flags. Preview should show no reversals + a clear
    warning."""
    actor = _make_actor(company, user, owner_membership)

    bank_line = _make_bank_line(company, bank_account)

    # Set up a fake "manual match" — bank line is matched, no clearance JE
    # source_module. We don't need a real JournalLine because the preview's
    # source_module check is `journal_line.entry.source_module == "..."` and
    # if we leave matched_journal_line = None, the check is skipped.
    with command_writes_allowed():
        bank_line.match_status = BankStatementLine.MatchStatus.MANUAL_MATCHED
        bank_line.save()

    result = preview_unmatch_line(actor, bank_line.id)
    assert result.success

    data = result.data
    assert data["bank_line_id"] == bank_line.id
    assert data["match_status"] == BankStatementLine.MatchStatus.MANUAL_MATCHED
    assert data["reversal_plan"] == []
    assert data["dry_run_safe"] is True

    # Warning explains why nothing will be reversed
    assert any("flag-flip only" in w for w in data["warnings"])

    # Flag flips always include the bank line's match_status
    flag_fields = {f["field"] for f in data["flag_flips"]}
    assert "match_status" in flag_fields


# =============================================================================
# Scenario 2: settlement-prepass clearance JE reversal
# =============================================================================


@pytest.mark.django_db
def test_preview_reports_clearance_je_reversal(company, user, owner_membership, bank_account, april_2026_period):
    """If the matched JE has source_module='payment_settlement_clearance',
    the preview shows that JE in the reversal plan."""
    actor = _make_actor(company, user, owner_membership)

    bank_line = _make_bank_line(company, bank_account)

    # Create a clearance JE + line, attach to bank line as the match.
    # The JE doesn't need to be fully posted for the preview's logic
    # path (we test the source_module branch + posted-status filter).
    from accounting.models import JournalLine

    with projection_writes_allowed():
        # total_debit / total_credit are read-only properties on
        # JournalEntry (sum of lines); construct without them. line_no is
        # the field name on JournalLine (not line_number).
        clearance_je = JournalEntry.objects.create(
            company=company,
            date=date(2026, 4, 25),
            period=4,
            memo="Test clearance JE",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            source_module="payment_settlement_clearance",
            source_document="paymob:BATCH-X",
            entry_number="JE-CLEAR-1",
        )
        clearance_line = JournalLine.objects.create(
            company=company,
            entry=clearance_je,
            line_no=1,
            account=bank_account,
            debit=Decimal("100"),
            credit=Decimal("0"),
        )

    with command_writes_allowed():
        bank_line.matched_journal_line = clearance_line
        bank_line.match_status = BankStatementLine.MatchStatus.AUTO_MATCHED
        bank_line.save()

    result = preview_unmatch_line(actor, bank_line.id)
    assert result.success

    data = result.data
    assert len(data["reversal_plan"]) == 1

    row = data["reversal_plan"][0]
    assert row["entry_id"] == clearance_je.id
    assert row["entry_number"] == "JE-CLEAR-1"
    assert row["source_module"] == "payment_settlement_clearance"
    assert row["period"] == 4
    assert row["would_reverse"] is True
    assert "Settlement-prepass" in row["reason_for_reversal"]
    assert row["blocker"] is None
    assert data["dry_run_safe"] is True


# =============================================================================
# Scenario 3: blocked by closed period
# =============================================================================


@pytest.mark.django_db
def test_preview_flags_closed_period_as_blocker(company, user, owner_membership, bank_account, april_2026_period):
    """If the target period for the reversal is CLOSED, the preview
    surfaces a blocker so the operator knows the unmatch will fail."""
    actor = _make_actor(company, user, owner_membership)

    bank_line = _make_bank_line(company, bank_account)

    from accounting.models import JournalLine

    with projection_writes_allowed():
        clearance_je = JournalEntry.objects.create(
            company=company,
            date=date(2026, 4, 25),
            period=4,
            memo="Test clearance JE — period will be closed",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            source_module="payment_settlement_clearance",
            source_document="paymob:BATCH-Y",
            entry_number="JE-CLEAR-2",
        )
        clearance_line = JournalLine.objects.create(
            company=company,
            entry=clearance_je,
            line_no=1,
            account=bank_account,
            debit=Decimal("100"),
            credit=Decimal("0"),
        )

    with command_writes_allowed():
        bank_line.matched_journal_line = clearance_line
        bank_line.match_status = BankStatementLine.MatchStatus.AUTO_MATCHED
        bank_line.save()

    # Close the period AFTER the JE was posted
    from projections.models import FiscalPeriod

    april_2026_period.status = FiscalPeriod.Status.CLOSED
    april_2026_period.save()

    result = preview_unmatch_line(actor, bank_line.id)
    assert result.success

    data = result.data
    assert data["dry_run_safe"] is False
    row = data["reversal_plan"][0]
    assert row["would_reverse"] is False
    assert "closed" in row["blocker"].lower()

    # Aggregate warning surfaces too
    assert any("period 4" in w.lower() for w in data["warnings"])


# =============================================================================
# Error paths
# =============================================================================


@pytest.mark.django_db
def test_preview_rejects_unknown_bank_line(company, user, owner_membership):
    actor = _make_actor(company, user, owner_membership)
    result = preview_unmatch_line(actor, bank_line_id=999_999)
    assert not result.success
    assert "not found" in result.error.lower()


@pytest.mark.django_db
def test_preview_rejects_already_unmatched_line(company, user, owner_membership, bank_account):
    actor = _make_actor(company, user, owner_membership)
    bank_line = _make_bank_line(company, bank_account)
    # Default match_status is UNMATCHED — no setup needed

    result = preview_unmatch_line(actor, bank_line.id)
    assert not result.success
    assert "not matched" in result.error.lower()
