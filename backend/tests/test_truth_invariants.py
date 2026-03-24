# tests/test_truth_invariants.py
"""
Nxentra Truth Invariants.

These tests are the accounting system's spine of trust.
They verify structural properties that must ALWAYS hold,
regardless of how events are emitted or projections are run.

Each test is an invariant, not a feature test.
If any of these fail, the system's truth is broken.

Invariants:
1. Replay-from-zero equals incremental projection state
2. Reprocessing does not change balances (double-apply stability)
3. Reversal fully offsets original entry
4. Multiple same-account lines in one event sum correctly
5. External payload and inline payload produce identical projection output
6. verify_all_balances agrees with projection state after mixed operations
"""

import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from django.conf import settings
from django.utils import timezone

from accounting.models import Account, JournalEntry, JournalLine
from events.emitter import emit_event
from events.models import BusinessEvent
from events.types import EventTypes
from projections.account_balance import AccountBalanceProjection
from projections.models import AccountBalance
from projections.write_barrier import (
    projection_writes_allowed,
    command_writes_allowed,
    write_context_allowed,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _emit_posted_event(company, user, lines, memo="Truth test", entry_id=None):
    """Emit a JOURNAL_ENTRY_POSTED event with the given lines."""
    entry_id = entry_id or uuid4()
    total_debit = sum(Decimal(l.get("debit", "0")) for l in lines)
    total_credit = sum(Decimal(l.get("credit", "0")) for l in lines)

    return emit_event(
        company=company,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=str(entry_id),
        data={
            "entry_public_id": str(entry_id),
            "entry_number": f"JE-TRUTH-{uuid4().hex[:6]}",
            "date": date.today().isoformat(),
            "memo": memo,
            "kind": "NORMAL",
            "posted_at": timezone.now().isoformat(),
            "posted_by_id": user.id,
            "posted_by_email": user.email,
            "total_debit": str(total_debit),
            "total_credit": str(total_credit),
            "lines": lines,
        },
        caused_by_user=user,
        idempotency_key=f"truth:{entry_id}",
    )


def _make_line(account, debit="0.00", credit="0.00", line_no=1):
    """Build a journal line dict."""
    return {
        "line_no": line_no,
        "account_public_id": str(account.public_id),
        "account_code": account.code,
        "description": f"Truth line {line_no}",
        "debit": str(debit),
        "credit": str(credit),
    }


def _snapshot_balances(company):
    """Capture all AccountBalance records as a dict keyed by account code."""
    balances = AccountBalance.objects.filter(company=company).select_related("account")
    return {
        b.account.code: {
            "balance": b.balance,
            "debit_total": b.debit_total,
            "credit_total": b.credit_total,
            "entry_count": b.entry_count,
        }
        for b in balances
    }


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 1: Replay-from-zero equals incremental projection state
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestReplayEqualsIncremental:
    """
    After processing events incrementally, rebuilding from scratch must
    produce the exact same balances.

    If this fails, the projection has order-dependent or state-dependent bugs.
    """

    def test_rebuild_matches_incremental(
        self, company, user, cash_account, revenue_account, expense_account
    ):
        # Emit several events with varied patterns
        _emit_posted_event(company, user, [
            _make_line(cash_account, debit="1000.00", line_no=1),
            _make_line(revenue_account, credit="1000.00", line_no=2),
        ], memo="Sale 1")

        _emit_posted_event(company, user, [
            _make_line(expense_account, debit="300.00", line_no=1),
            _make_line(cash_account, credit="300.00", line_no=2),
        ], memo="Expense payment")

        # Multi-line same account (the bug we just fixed)
        _emit_posted_event(company, user, [
            _make_line(cash_account, debit="200.00", line_no=1),
            _make_line(cash_account, debit="150.00", line_no=2),
            _make_line(revenue_account, credit="350.00", line_no=3),
        ], memo="Consolidated receipt")

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        # Snapshot incremental state
        incremental = _snapshot_balances(company)

        # Rebuild from scratch
        projection.rebuild(company)

        # Snapshot rebuilt state
        rebuilt = _snapshot_balances(company)

        # They must be identical
        assert incremental == rebuilt, (
            f"Rebuild diverged from incremental.\n"
            f"Incremental: {incremental}\n"
            f"Rebuilt:      {rebuilt}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 2: Double-apply stability (reprocessing = no-op)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestDoubleApplyStability:
    """
    Running process_pending() N times must produce the same result as
    running it once. ProjectionAppliedEvent guarantees this.

    If this fails, the idempotency mechanism is broken.
    """

    def test_process_pending_is_idempotent(
        self, company, user, cash_account, revenue_account
    ):
        _emit_posted_event(company, user, [
            _make_line(cash_account, debit="500.00", line_no=1),
            _make_line(revenue_account, credit="500.00", line_no=2),
        ])

        projection = AccountBalanceProjection()

        projection.process_pending(company)
        after_first = _snapshot_balances(company)

        projection.process_pending(company)
        after_second = _snapshot_balances(company)

        projection.process_pending(company)
        after_third = _snapshot_balances(company)

        assert after_first == after_second == after_third


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 3: Reversal fully offsets original
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestReversalFullyOffsets:
    """
    Posting an entry then posting its reversal (swapped debits/credits)
    must result in zero net balance for every account touched.

    This simulates what reverse_journal_entry() does: it emits a second
    JOURNAL_ENTRY_POSTED event with debits and credits swapped.
    """

    def test_reversal_nets_to_zero(
        self, company, user, cash_account, revenue_account, expense_account
    ):
        # Original entry
        _emit_posted_event(company, user, [
            _make_line(cash_account, debit="750.00", line_no=1),
            _make_line(expense_account, debit="250.00", line_no=2),
            _make_line(revenue_account, credit="1000.00", line_no=3),
        ], memo="Original entry")

        # Reversal: swap debit/credit on every line
        _emit_posted_event(company, user, [
            _make_line(cash_account, credit="750.00", line_no=1),
            _make_line(expense_account, credit="250.00", line_no=2),
            _make_line(revenue_account, debit="1000.00", line_no=3),
        ], memo="Reversal entry")

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        # Every account must net to zero
        for account in [cash_account, revenue_account, expense_account]:
            bal = AccountBalance.objects.get(company=company, account=account)
            assert bal.balance == Decimal("0.00"), (
                f"Account {account.code} balance is {bal.balance}, expected 0.00 after reversal"
            )
            assert bal.debit_total == bal.credit_total, (
                f"Account {account.code}: debit_total={bal.debit_total} != "
                f"credit_total={bal.credit_total}"
            )

    def test_partial_reversal_leaves_correct_remainder(
        self, company, user, cash_account, revenue_account
    ):
        """A partial reversal (smaller amount) leaves the correct remainder."""
        _emit_posted_event(company, user, [
            _make_line(cash_account, debit="1000.00", line_no=1),
            _make_line(revenue_account, credit="1000.00", line_no=2),
        ], memo="Original")

        _emit_posted_event(company, user, [
            _make_line(cash_account, credit="400.00", line_no=1),
            _make_line(revenue_account, debit="400.00", line_no=2),
        ], memo="Partial reversal")

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        cash_bal = AccountBalance.objects.get(company=company, account=cash_account)
        assert cash_bal.balance == Decimal("600.00")
        assert cash_bal.debit_total == Decimal("1000.00")
        assert cash_bal.credit_total == Decimal("400.00")

        rev_bal = AccountBalance.objects.get(company=company, account=revenue_account)
        assert rev_bal.balance == Decimal("600.00")  # Credit-normal: credits - debits


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 4: Multiple same-account lines sum correctly
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestMultiLineSameAccount:
    """
    A single event with N lines to the same account must apply all N lines.

    This was the bug fixed in the last_event_id guard removal.
    These tests ensure it stays fixed under varied conditions.
    """

    def test_three_debits_to_same_account(
        self, company, user, cash_account, revenue_account
    ):
        _emit_posted_event(company, user, [
            _make_line(cash_account, debit="100.00", line_no=1),
            _make_line(cash_account, debit="200.00", line_no=2),
            _make_line(cash_account, debit="300.00", line_no=3),
            _make_line(revenue_account, credit="600.00", line_no=4),
        ])

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        bal = AccountBalance.objects.get(company=company, account=cash_account)
        assert bal.debit_total == Decimal("600.00")
        assert bal.entry_count == 3

    def test_mixed_debit_credit_to_same_account(
        self, company, user, cash_account, revenue_account
    ):
        """Debit + credit to the same account in one event (allocation pattern)."""
        _emit_posted_event(company, user, [
            _make_line(cash_account, debit="500.00", line_no=1),
            _make_line(cash_account, credit="200.00", line_no=2),
            _make_line(revenue_account, credit="300.00", line_no=3),
        ])

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        bal = AccountBalance.objects.get(company=company, account=cash_account)
        assert bal.debit_total == Decimal("500.00")
        assert bal.credit_total == Decimal("200.00")
        assert bal.balance == Decimal("300.00")  # Debit-normal: debits - credits

    def test_same_account_across_multiple_events(
        self, company, user, cash_account, revenue_account
    ):
        """Multiple events each with multi-line same-account must all accumulate."""
        for i in range(3):
            _emit_posted_event(company, user, [
                _make_line(cash_account, debit="100.00", line_no=1),
                _make_line(cash_account, debit="50.00", line_no=2),
                _make_line(revenue_account, credit="150.00", line_no=3),
            ], memo=f"Batch {i}")

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        bal = AccountBalance.objects.get(company=company, account=cash_account)
        assert bal.debit_total == Decimal("450.00")  # 3 * (100 + 50)
        assert bal.entry_count == 6  # 3 events * 2 lines each


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 5: External payload produces identical output to inline
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestExternalPayloadEquivalence:
    """
    An event stored externally (LEPH) must produce the exact same projection
    result as an identical event stored inline.

    If this fails, get_data() has a bug or the projection treats storage
    strategies differently.
    """

    def test_external_and_inline_produce_same_balance(
        self, company, user
    ):
        # Create two sets of accounts
        inline_debit = Account.objects.create(
            public_id=uuid4(), company=company, code="7001",
            name="Inline Debit", account_type=Account.AccountType.EXPENSE,
            normal_balance=Account.NormalBalance.DEBIT, status=Account.Status.ACTIVE,
        )
        inline_credit = Account.objects.create(
            public_id=uuid4(), company=company, code="7002",
            name="Inline Credit", account_type=Account.AccountType.PAYABLE,
            normal_balance=Account.NormalBalance.CREDIT, status=Account.Status.ACTIVE,
        )
        external_debit = Account.objects.create(
            public_id=uuid4(), company=company, code="7003",
            name="External Debit", account_type=Account.AccountType.EXPENSE,
            normal_balance=Account.NormalBalance.DEBIT, status=Account.Status.ACTIVE,
        )
        external_credit = Account.objects.create(
            public_id=uuid4(), company=company, code="7004",
            name="External Credit", account_type=Account.AccountType.PAYABLE,
            normal_balance=Account.NormalBalance.CREDIT, status=Account.Status.ACTIVE,
        )

        amount = Decimal("42.50")

        # Inline event (small payload, stays inline)
        _emit_posted_event(company, user, [
            _make_line(inline_debit, debit=str(amount), line_no=1),
            _make_line(inline_credit, credit=str(amount), line_no=2),
        ], memo="Inline event")

        # External event (large payload, forced to external storage)
        # Build 300+ lines to exceed 64KB inline threshold
        external_lines = []
        external_accounts = []
        total = Decimal("0.00")
        for i in range(300):
            acct = Account.objects.create(
                public_id=uuid4(), company=company, code=f"8{i:03d}",
                name=f"Ext Account {i}",
                account_type=Account.AccountType.EXPENSE,
                normal_balance=Account.NormalBalance.DEBIT,
                status=Account.Status.ACTIVE,
            )
            external_accounts.append(acct)
            line = _make_line(acct, debit=str(amount), line_no=i + 1)
            line["description"] = f"External expense line {i + 1} " + ("x" * 100)
            external_lines.append(line)
            total += amount

        # Also add our tracked external_debit account
        external_lines.append(
            _make_line(external_debit, debit=str(amount), line_no=301)
        )
        total += amount
        # Balancing credit
        external_lines.append(
            _make_line(external_credit, credit=str(total), line_no=302)
        )

        ext_entry_id = uuid4()
        ext_event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(ext_entry_id),
            data={
                "entry_public_id": str(ext_entry_id),
                "entry_number": "JE-EXT-EQUIV",
                "date": date.today().isoformat(),
                "memo": "External event" + " padding" * 500,
                "kind": "NORMAL",
                "posted_at": timezone.now().isoformat(),
                "posted_by_id": user.id,
                "posted_by_email": user.email,
                "total_debit": str(total),
                "total_credit": str(total),
                "lines": external_lines,
            },
            caused_by_user=user,
            idempotency_key=f"truth-ext:{ext_entry_id}",
        )

        # Verify storage strategy differs
        inline_event = BusinessEvent.objects.filter(
            company=company,
            data__memo="Inline event",
        ).first()
        assert inline_event is not None
        # External event should be external
        assert ext_event.payload_storage == "external", (
            f"Expected external storage, got {ext_event.payload_storage}"
        )

        # Process projection
        projection = AccountBalanceProjection()
        projection.process_pending(company)

        # Both tracked accounts should have the exact same amount
        inline_bal = AccountBalance.objects.get(company=company, account=inline_debit)
        external_bal = AccountBalance.objects.get(company=company, account=external_debit)

        assert inline_bal.debit_total == external_bal.debit_total == amount
        assert inline_bal.credit_total == external_bal.credit_total == Decimal("0.00")
        assert inline_bal.balance == external_bal.balance == amount


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 6: verify_all_balances agrees with projection state
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestVerifyAllBalancesConsistency:
    """
    After a mix of normal entries, multi-line entries, and reversals,
    verify_all_balances (event replay) must agree with the projection state.

    This is the ultimate consistency check: events are truth, projections
    must match.
    """

    def test_verify_passes_after_mixed_operations(
        self, company, user, cash_account, revenue_account, expense_account
    ):
        # Normal entry
        _emit_posted_event(company, user, [
            _make_line(cash_account, debit="2000.00", line_no=1),
            _make_line(revenue_account, credit="2000.00", line_no=2),
        ], memo="Big sale")

        # Multi-line same account
        _emit_posted_event(company, user, [
            _make_line(expense_account, debit="300.00", line_no=1),
            _make_line(expense_account, debit="200.00", line_no=2),
            _make_line(cash_account, credit="500.00", line_no=3),
        ], memo="Split expense")

        # Reversal-style entry
        _emit_posted_event(company, user, [
            _make_line(cash_account, credit="100.00", line_no=1),
            _make_line(revenue_account, debit="100.00", line_no=2),
        ], memo="Partial refund")

        # Another normal entry
        _emit_posted_event(company, user, [
            _make_line(cash_account, debit="800.00", line_no=1),
            _make_line(revenue_account, credit="800.00", line_no=2),
        ], memo="Another sale")

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        # verify_all_balances replays events independently and compares
        result = projection.verify_all_balances(company)

        assert result["mismatches"] == [], (
            f"Projection state diverges from event replay:\n{result['mismatches']}"
        )
        assert result["verified"] == result["total_accounts"]

    def test_verify_passes_after_rebuild(
        self, company, user, cash_account, revenue_account
    ):
        """verify_all_balances must also pass after a full rebuild."""
        for i in range(5):
            _emit_posted_event(company, user, [
                _make_line(cash_account, debit="100.00", line_no=1),
                _make_line(cash_account, debit="50.00", line_no=2),
                _make_line(revenue_account, credit="150.00", line_no=3),
            ], memo=f"Entry {i}")

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        # Corrupt one balance
        bal = AccountBalance.objects.get(company=company, account=cash_account)
        bal.debit_total = Decimal("9999.99")
        bal.save()

        # Rebuild
        projection.rebuild(company)

        # Verify after rebuild
        result = projection.verify_all_balances(company)
        assert result["mismatches"] == [], (
            f"Post-rebuild verification failed:\n{result['mismatches']}"
        )

        # Check the actual values are correct
        bal.refresh_from_db()
        assert bal.debit_total == Decimal("750.00")  # 5 * (100 + 50)
        assert bal.credit_total == Decimal("0.00")


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 7: Trial balance is always balanced
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestTrialBalanceAlwaysBalanced:
    """
    No matter what mix of entries we process, the trial balance must
    have total_debit == total_credit.

    This is the fundamental accounting equation. If it fails,
    either the projection or the event emission is corrupted.
    """

    def test_balanced_after_complex_sequence(
        self, company, user, cash_account, revenue_account, expense_account
    ):
        entries = [
            # Normal sale
            ([_make_line(cash_account, debit="1000.00", line_no=1),
              _make_line(revenue_account, credit="1000.00", line_no=2)], "Sale"),
            # Multi-line expense
            ([_make_line(expense_account, debit="200.00", line_no=1),
              _make_line(expense_account, debit="300.00", line_no=2),
              _make_line(cash_account, credit="500.00", line_no=3)], "Expenses"),
            # Reversal
            ([_make_line(cash_account, credit="100.00", line_no=1),
              _make_line(revenue_account, debit="100.00", line_no=2)], "Refund"),
            # Another sale
            ([_make_line(cash_account, debit="750.00", line_no=1),
              _make_line(revenue_account, credit="750.00", line_no=2)], "Sale 2"),
        ]

        for lines, memo in entries:
            _emit_posted_event(company, user, lines, memo=memo)

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        tb = projection.get_trial_balance(company)
        assert tb["is_balanced"], (
            f"Trial balance not balanced: "
            f"debit={tb['total_debit']}, credit={tb['total_credit']}"
        )
        assert Decimal(tb["total_debit"]) == Decimal(tb["total_credit"])


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 8: Every posted JE traces to a business event
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestPostedJETracesToEvent:
    """
    Every POSTED JournalEntry should have a corresponding
    JOURNAL_ENTRY_POSTED event in the event store.

    If this fails, a JE was created outside the event-first path.
    """

    def test_event_emitted_je_has_matching_event(
        self, company, user, cash_account, revenue_account
    ):
        """JE created via event emission must have a matching event."""
        entry_id = uuid4()
        _emit_posted_event(company, user, [
            _make_line(cash_account, debit="500.00", line_no=1),
            _make_line(revenue_account, credit="500.00", line_no=2),
        ], entry_id=entry_id)

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        # The event must exist
        events = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_id=str(entry_id),
        )
        assert events.count() == 1, (
            f"Expected 1 JOURNAL_ENTRY_POSTED event for entry {entry_id}, "
            f"found {events.count()}"
        )

    def test_no_orphan_posted_events(
        self, company, user, cash_account, revenue_account
    ):
        """Every JOURNAL_ENTRY_POSTED event should reference a valid entry_public_id."""
        _emit_posted_event(company, user, [
            _make_line(cash_account, debit="100.00", line_no=1),
            _make_line(revenue_account, credit="100.00", line_no=2),
        ])
        _emit_posted_event(company, user, [
            _make_line(cash_account, debit="200.00", line_no=1),
            _make_line(revenue_account, credit="200.00", line_no=2),
        ])

        # All JOURNAL_ENTRY_POSTED events must have valid aggregate_ids
        posted_events = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        )
        for event in posted_events:
            data = event.get_data()
            assert "entry_public_id" in data, (
                f"Event {event.id} missing entry_public_id in payload"
            )
            assert data["entry_public_id"] == str(event.aggregate_id), (
                f"Event {event.id}: entry_public_id={data['entry_public_id']} "
                f"doesn't match aggregate_id={event.aggregate_id}"
            )


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 9: No finance writes outside write barriers
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestWriteBarrierEnforcement:
    """
    Direct writes to JournalEntry/JournalLine outside of
    projection_writes_allowed() or command_writes_allowed()
    must be blocked (unless TESTING=True allows it).

    These tests temporarily disable TESTING to verify real enforcement.
    """

    def test_journal_entry_blocked_without_write_context(self, company, user):
        """Creating a JournalEntry without write context must raise RuntimeError."""
        original_testing = getattr(settings, "TESTING", False)
        settings.TESTING = False
        try:
            with pytest.raises(RuntimeError, match="write"):
                JournalEntry.objects.create(
                    company=company,
                    public_id=uuid4(),
                    date=date.today(),
                    memo="Should be blocked",
                    status=JournalEntry.Status.DRAFT,
                    created_by=user,
                )
        finally:
            settings.TESTING = original_testing

    def test_journal_entry_allowed_with_projection_context(self, company, user):
        """Creating a JournalEntry within projection_writes_allowed() must succeed."""
        original_testing = getattr(settings, "TESTING", False)
        settings.TESTING = False
        try:
            with projection_writes_allowed():
                entry = JournalEntry.objects.create(
                    company=company,
                    public_id=uuid4(),
                    date=date.today(),
                    memo="Projection-created",
                    status=JournalEntry.Status.DRAFT,
                    created_by=user,
                )
            assert entry.pk is not None
        finally:
            settings.TESTING = original_testing

    def test_journal_entry_blocked_with_command_context(self, company, user):
        """JournalEntry is a projection-owned read model — command context alone cannot write it.

        In Nxentra, the command layer creates JEs via emit_event() → projection,
        NOT via direct JournalEntry.objects.create(). This test confirms that
        even command_writes_allowed() cannot bypass the projection-only guard.
        """
        original_testing = getattr(settings, "TESTING", False)
        settings.TESTING = False
        try:
            with pytest.raises(RuntimeError, match="read model"):
                with command_writes_allowed():
                    JournalEntry.objects.create(
                        company=company,
                        public_id=uuid4(),
                        date=date.today(),
                        memo="Should be blocked",
                        status=JournalEntry.Status.DRAFT,
                        created_by=user,
                    )
        finally:
            settings.TESTING = original_testing

    def test_account_balance_blocked_without_projection_context(self, company, cash_account):
        """AccountBalance writes require projection context specifically."""
        original_testing = getattr(settings, "TESTING", False)
        settings.TESTING = False
        try:
            with pytest.raises(RuntimeError):
                AccountBalance.objects.create(
                    company=company,
                    account=cash_account,
                    balance=Decimal("0"),
                    debit_total=Decimal("0"),
                    credit_total=Decimal("0"),
                    entry_count=0,
                )
        finally:
            settings.TESTING = original_testing


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 10: Event causation chain integrity
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestCausationChainIntegrity:
    """
    When events declare a caused_by_event, that parent event must exist
    and belong to the same company.

    If this fails, the causation chain has dangling references.
    """

    def test_caused_by_event_exists(self, company, user, cash_account, revenue_account):
        """Events with caused_by_event must reference valid parent events."""
        # Emit parent event
        parent_event = _emit_posted_event(company, user, [
            _make_line(cash_account, debit="500.00", line_no=1),
            _make_line(revenue_account, credit="500.00", line_no=2),
        ], memo="Parent entry")

        # Emit child event with causation link
        child_entry_id = uuid4()
        child_event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(child_entry_id),
            data={
                "entry_public_id": str(child_entry_id),
                "entry_number": "JE-CHILD",
                "date": date.today().isoformat(),
                "memo": "Child entry",
                "kind": "NORMAL",
                "posted_at": timezone.now().isoformat(),
                "posted_by_id": user.id,
                "posted_by_email": user.email,
                "total_debit": "500.00",
                "total_credit": "500.00",
                "lines": [
                    _make_line(cash_account, credit="500.00", line_no=1),
                    _make_line(revenue_account, debit="500.00", line_no=2),
                ],
            },
            caused_by_user=user,
            caused_by_event=parent_event,
            idempotency_key=f"truth:child:{child_entry_id}",
        )

        # Verify chain
        assert child_event.caused_by_event_id == parent_event.id
        assert child_event.caused_by_event.company_id == company.id

    def test_all_causation_links_valid(self, company, user, cash_account, revenue_account):
        """No event in the system should have a dangling caused_by_event."""
        # Emit a few linked events
        e1 = _emit_posted_event(company, user, [
            _make_line(cash_account, debit="100.00", line_no=1),
            _make_line(revenue_account, credit="100.00", line_no=2),
        ])
        e2 = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(uuid4()),
            data={
                "entry_public_id": str(uuid4()),
                "entry_number": "JE-LINKED",
                "date": date.today().isoformat(),
                "memo": "Linked",
                "kind": "REVERSAL",
                "posted_at": timezone.now().isoformat(),
                "posted_by_id": user.id,
                "posted_by_email": user.email,
                "total_debit": "100.00",
                "total_credit": "100.00",
                "lines": [
                    _make_line(cash_account, credit="100.00", line_no=1),
                    _make_line(revenue_account, debit="100.00", line_no=2),
                ],
            },
            caused_by_user=user,
            caused_by_event=e1,
            idempotency_key=f"truth:linked:{uuid4()}",
        )

        # Check ALL events with caused_by_event are valid
        linked_events = BusinessEvent.objects.filter(
            company=company,
            caused_by_event__isnull=False,
        ).select_related("caused_by_event")

        for event in linked_events:
            parent = event.caused_by_event
            assert parent is not None, (
                f"Event {event.id} has caused_by_event_id={event.caused_by_event_id} "
                f"but parent is None (dangling reference)"
            )
            assert parent.company_id == event.company_id, (
                f"Event {event.id} (company={event.company_id}) links to parent "
                f"event {parent.id} (company={parent.company_id}) — cross-company link"
            )
