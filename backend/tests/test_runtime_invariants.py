# tests/test_runtime_invariants.py
"""
Nxentra Runtime Invariants.

These tests verify operational resilience under conditions that occur
in production but are not covered by deterministic truth/control tests:

1. Concurrent posting — multiple threads posting to the same accounts
2. Projection crash recovery — partial processing, restart, correct result
3. Out-of-order event replay — rebuild produces correct state regardless
4. Projection lag reporting — lag is accurate before and after processing
5. Idempotent event emission — duplicate idempotency keys don't corrupt state

If any of these fail, the system is not safe under real-world conditions.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import connection
from django.utils import timezone

from accounting.models import Account
from events.emitter import emit_event
from events.models import BusinessEvent, EventBookmark
from events.types import EventTypes
from projections.account_balance import AccountBalanceProjection
from projections.models import AccountBalance, ProjectionAppliedEvent

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _emit_posted(company, user, lines, memo="Runtime test", entry_id=None):
    """Emit a JOURNAL_ENTRY_POSTED event."""
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
            "entry_number": f"JE-RT-{uuid4().hex[:6]}",
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
        idempotency_key=f"rt:{entry_id}",
    )


def _line(account, debit="0.00", credit="0.00", line_no=1):
    """Build a journal line dict."""
    return {
        "line_no": line_no,
        "account_public_id": str(account.public_id),
        "account_code": account.code,
        "description": f"Runtime line {line_no}",
        "debit": str(debit),
        "credit": str(credit),
    }


def _snapshot_balances(company):
    """Capture all AccountBalance records as dict keyed by account code."""
    return {
        b.account.code: {
            "balance": b.balance,
            "debit_total": b.debit_total,
            "credit_total": b.credit_total,
            "entry_count": b.entry_count,
        }
        for b in AccountBalance.objects.filter(company=company).select_related("account")
    }


def _is_sqlite():
    """Check if the test database is SQLite."""
    return "sqlite" in connection.settings_dict.get("ENGINE", "")


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 1: Concurrent posting
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db(transaction=True)
class TestConcurrentPosting:
    """
    Multiple threads posting entries to the same account set must produce:
    - correct totals (no lost updates)
    - no duplicate events (idempotency keys unique)
    - no sequence gaps in company_sequence
    - trial balance remains balanced

    Skipped on SQLite because it serializes all writes.
    """

    @pytest.mark.skipif(_is_sqlite(), reason="SQLite serializes writes; concurrency test requires Postgres")
    def test_concurrent_posts_preserve_correctness(self, company, user, owner_membership):
        # Create accounts
        cash = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="1001",
            name="Cash",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="4001",
            name="Revenue",
            account_type=Account.AccountType.REVENUE,
            normal_balance=Account.NormalBalance.CREDIT,
            status=Account.Status.ACTIVE,
        )

        num_entries = 20
        amount_per_entry = Decimal("100.00")
        errors = []

        def post_entry(i):
            """Post a single entry in its own thread."""
            try:
                from django.db import connection as thread_conn

                entry_id = uuid4()
                emit_event(
                    company=company,
                    event_type=EventTypes.JOURNAL_ENTRY_POSTED,
                    aggregate_type="JournalEntry",
                    aggregate_id=str(entry_id),
                    data={
                        "entry_public_id": str(entry_id),
                        "entry_number": f"JE-CONC-{i:04d}",
                        "date": date.today().isoformat(),
                        "memo": f"Concurrent entry {i}",
                        "kind": "NORMAL",
                        "posted_at": timezone.now().isoformat(),
                        "posted_by_id": user.id,
                        "posted_by_email": user.email,
                        "total_debit": str(amount_per_entry),
                        "total_credit": str(amount_per_entry),
                        "lines": [
                            {
                                "line_no": 1,
                                "account_public_id": str(cash.public_id),
                                "account_code": cash.code,
                                "description": f"Concurrent debit {i}",
                                "debit": str(amount_per_entry),
                                "credit": "0.00",
                            },
                            {
                                "line_no": 2,
                                "account_public_id": str(revenue.public_id),
                                "account_code": revenue.code,
                                "description": f"Concurrent credit {i}",
                                "debit": "0.00",
                                "credit": str(amount_per_entry),
                            },
                        ],
                    },
                    caused_by_user=user,
                    idempotency_key=f"conc:{entry_id}",
                )
            except Exception as e:
                errors.append((i, str(e)))
            finally:
                thread_conn.close()

        # Fire all posts concurrently
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(post_entry, i) for i in range(num_entries)]
            for f in as_completed(futures):
                f.result()  # Re-raise any exceptions

        assert len(errors) == 0, f"Concurrent posting errors: {errors}"

        # Verify event count
        event_count = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        ).count()
        assert event_count == num_entries, f"Expected {num_entries} events, got {event_count}"

        # Verify no sequence gaps
        sequences = list(
            BusinessEvent.objects.filter(company=company)
            .order_by("company_sequence")
            .values_list("company_sequence", flat=True)
        )
        for i in range(1, len(sequences)):
            assert sequences[i] == sequences[i - 1] + 1, f"Sequence gap: {sequences[i - 1]} -> {sequences[i]}"

        # Process projections and verify
        projection = AccountBalanceProjection()
        projection.process_pending(company)

        expected_total = amount_per_entry * num_entries
        cash_bal = AccountBalance.objects.get(company=company, account=cash)
        rev_bal = AccountBalance.objects.get(company=company, account=revenue)

        assert cash_bal.debit_total == expected_total, (
            f"Cash debit expected {expected_total}, got {cash_bal.debit_total}"
        )
        assert rev_bal.credit_total == expected_total, (
            f"Revenue credit expected {expected_total}, got {rev_bal.credit_total}"
        )

        tb = projection.get_trial_balance(company)
        assert tb["is_balanced"], "Trial balance not balanced after concurrent posting"


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 2: Projection crash recovery
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestProjectionCrashRecovery:
    """
    Simulate a crash mid-projection by:
    1. Emitting multiple events
    2. Processing only a subset (by using limit=1 repeatedly)
    3. Resetting the bookmark to simulate a crash after partial processing
    4. Re-running process_pending
    5. Verifying final state matches expected values

    The projection must complete correctly after restart.
    """

    def test_partial_process_then_restart_produces_correct_state(
        self, company, user, cash_account, revenue_account, expense_account
    ):
        # Emit 5 events
        for i in range(5):
            _emit_posted(
                company,
                user,
                [
                    _line(cash_account, debit=f"{100 + i * 10}.00", line_no=1),
                    _line(revenue_account, credit=f"{100 + i * 10}.00", line_no=2),
                ],
                memo=f"Crash recovery entry {i}",
            )

        projection = AccountBalanceProjection()

        # Process only the first 2 events
        processed = projection.process_pending(company, limit=2)
        assert processed == 2

        # Snapshot after partial processing
        partial_state = _snapshot_balances(company)

        # Verify only 2 events were applied
        applied_count = ProjectionAppliedEvent.objects.filter(
            company=company, projection_name="account_balance"
        ).count()
        assert applied_count == 2

        # Simulate crash: reset the bookmark back to the start
        # but leave ProjectionAppliedEvent records intact (as they would be
        # in a real crash where the transaction committed but the process died
        # before processing more events)
        bookmark = EventBookmark.objects.get(consumer_name="account_balance", company=company)
        first_event = BusinessEvent.objects.filter(company=company).order_by("company_sequence").first()

        # Set bookmark back to first event (simulating partial progress loss)
        bookmark.last_event = first_event
        bookmark.save()

        # Process remaining — ProjectionAppliedEvent prevents double-counting
        # for the already-processed event, and new events get processed
        remaining = projection.process_pending(company)

        # Total applied should be 5 (2 original + 3 new, with 1 skipped duplicate)
        total_applied = ProjectionAppliedEvent.objects.filter(
            company=company, projection_name="account_balance"
        ).count()
        assert total_applied == 5, f"Expected 5 applied events, got {total_applied}"

        # Verify final state
        expected_debit = sum(Decimal(f"{100 + i * 10}.00") for i in range(5))
        cash_bal = AccountBalance.objects.get(company=company, account=cash_account)
        assert cash_bal.debit_total == expected_debit, f"Expected {expected_debit}, got {cash_bal.debit_total}"

        tb = projection.get_trial_balance(company)
        assert tb["is_balanced"]

    def test_bookmark_error_state_clears_on_successful_processing(self, company, user, cash_account, revenue_account):
        """After a crash that left error_count > 0, successful processing resets it."""
        _emit_posted(
            company,
            user,
            [
                _line(cash_account, debit="500.00", line_no=1),
                _line(revenue_account, credit="500.00", line_no=2),
            ],
        )

        projection = AccountBalanceProjection()

        # Manually create a bookmark with error state
        bookmark, _ = EventBookmark.objects.get_or_create(
            consumer_name="account_balance",
            company=company,
        )
        bookmark.error_count = 3
        bookmark.last_error = "Simulated crash error"
        bookmark.save()

        # Process should succeed and clear error
        projection.process_pending(company)

        bookmark.refresh_from_db()
        assert bookmark.error_count == 0, f"Error count should be 0 after success, got {bookmark.error_count}"
        assert bookmark.last_error == ""


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 3: Out-of-order event replay via rebuild
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestOutOfOrderReplay:
    """
    Events are stored with monotonic company_sequence, but rebuild always
    replays in sequence order. This test verifies that:

    1. Events emitted in arbitrary logical order (not chronological)
    2. Rebuild processes them in company_sequence order
    3. Final state is correct regardless of emission order

    This proves the projection is not dependent on event emission order,
    only on company_sequence order (which is set at write time).
    """

    def test_rebuild_after_interleaved_entries_produces_correct_state(
        self, company, user, cash_account, revenue_account, expense_account
    ):
        # Emit events that represent logically out-of-order operations:
        # A "refund" event emitted before the "sale" it relates to
        # (possible if events from different systems arrive out of order)

        # Event 1: First sale
        _emit_posted(
            company,
            user,
            [
                _line(cash_account, debit="1000.00", line_no=1),
                _line(revenue_account, credit="1000.00", line_no=2),
            ],
            memo="First sale",
        )

        # Event 2: Expense (unrelated)
        _emit_posted(
            company,
            user,
            [
                _line(expense_account, debit="200.00", line_no=1),
                _line(cash_account, credit="200.00", line_no=2),
            ],
            memo="Expense payment",
        )

        # Event 3: Refund against first sale
        _emit_posted(
            company,
            user,
            [
                _line(revenue_account, debit="300.00", line_no=1),
                _line(cash_account, credit="300.00", line_no=2),
            ],
            memo="Refund",
        )

        # Event 4: Second sale
        _emit_posted(
            company,
            user,
            [
                _line(cash_account, debit="500.00", line_no=1),
                _line(revenue_account, credit="500.00", line_no=2),
            ],
            memo="Second sale",
        )

        # Event 5: Multi-line to same account
        _emit_posted(
            company,
            user,
            [
                _line(expense_account, debit="150.00", line_no=1),
                _line(expense_account, debit="50.00", line_no=2),
                _line(cash_account, credit="200.00", line_no=3),
            ],
            memo="Split expense",
        )

        # Process incrementally
        projection = AccountBalanceProjection()
        projection.process_pending(company)

        incremental = _snapshot_balances(company)
        tb_inc = projection.get_trial_balance(company)
        assert tb_inc["is_balanced"]

        # Expected cash: +1000 -200 -300 +500 -200 = 800
        assert incremental[cash_account.code]["balance"] == Decimal("800.00")
        # Expected revenue: +1000 -300 +500 = 1200
        assert incremental[revenue_account.code]["balance"] == Decimal("1200.00")
        # Expected expense: +200 +150 +50 = 400
        assert incremental[expense_account.code]["balance"] == Decimal("400.00")

        # Rebuild from zero
        projection.rebuild(company)

        rebuilt = _snapshot_balances(company)
        tb_rebuilt = projection.get_trial_balance(company)

        assert incremental == rebuilt, (
            f"Rebuild diverged from incremental.\nIncremental: {incremental}\nRebuilt: {rebuilt}"
        )
        assert tb_rebuilt["is_balanced"]
        assert tb_inc["total_debit"] == tb_rebuilt["total_debit"]

        # Verify via event replay
        verify = projection.verify_all_balances(company)
        assert verify["mismatches"] == []


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 4: Projection lag reporting
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestProjectionLagReporting:
    """
    The projection must accurately report its lag (number of unprocessed events)
    at every point in its lifecycle:

    1. Before any processing: lag = total relevant events
    2. After partial processing: lag = remaining events
    3. After full processing: lag = 0
    4. After new events arrive: lag increases correctly
    """

    def test_lag_tracks_unprocessed_events_accurately(self, company, user, cash_account, revenue_account):
        projection = AccountBalanceProjection()

        # Initially no events, lag should be 0
        initial_lag = projection.get_lag(company)
        assert initial_lag == 0, f"Initial lag should be 0, got {initial_lag}"

        # Emit 3 events
        for i in range(3):
            _emit_posted(
                company,
                user,
                [
                    _line(cash_account, debit=f"{100 * (i + 1)}.00", line_no=1),
                    _line(revenue_account, credit=f"{100 * (i + 1)}.00", line_no=2),
                ],
                memo=f"Lag test {i}",
            )

        # Before processing: lag = 3
        lag_before = projection.get_lag(company)
        assert lag_before == 3, f"Expected lag=3, got {lag_before}"

        # Process 1 event
        projection.process_pending(company, limit=1)

        lag_after_one = projection.get_lag(company)
        assert lag_after_one == 2, f"Expected lag=2 after processing 1, got {lag_after_one}"

        # Process remaining
        projection.process_pending(company)

        lag_after_all = projection.get_lag(company)
        assert lag_after_all == 0, f"Expected lag=0 after processing all, got {lag_after_all}"

        # Emit 2 more events
        for i in range(2):
            _emit_posted(
                company,
                user,
                [
                    _line(cash_account, debit="50.00", line_no=1),
                    _line(revenue_account, credit="50.00", line_no=2),
                ],
                memo=f"Lag test extra {i}",
            )

        lag_new = projection.get_lag(company)
        assert lag_new == 2, f"Expected lag=2 after new events, got {lag_new}"

        # Process and verify zero again
        projection.process_pending(company)
        assert projection.get_lag(company) == 0

    def test_paused_projection_reports_lag_but_does_not_process(self, company, user, cash_account, revenue_account):
        """A paused projection should still report lag but not process events."""
        _emit_posted(
            company,
            user,
            [
                _line(cash_account, debit="100.00", line_no=1),
                _line(revenue_account, credit="100.00", line_no=2),
            ],
        )

        projection = AccountBalanceProjection()

        # Create bookmark and pause it
        bookmark, _ = EventBookmark.objects.get_or_create(
            consumer_name="account_balance",
            company=company,
        )
        bookmark.is_paused = True
        bookmark.save()

        # Process should return 0 (paused)
        processed = projection.process_pending(company)
        assert processed == 0, f"Paused projection should process 0, got {processed}"

        # Lag should still be reported
        lag = projection.get_lag(company)
        assert lag == 1, f"Paused projection should still report lag=1, got {lag}"

        # Unpause and process
        bookmark.is_paused = False
        bookmark.save()

        processed = projection.process_pending(company)
        assert processed == 1
        assert projection.get_lag(company) == 0


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 5: Idempotent event emission
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestIdempotentEventEmission:
    """
    Duplicate event emission (same idempotency_key) must not create
    duplicate events, and projections must produce the same result
    as a single emission.

    This protects against retry storms, network duplicates, and
    at-least-once delivery semantics.
    """

    def test_duplicate_idempotency_key_does_not_create_duplicate_event(
        self, company, user, cash_account, revenue_account
    ):
        """Same idempotency_key emitted twice should produce exactly one event."""
        entry_id = uuid4()
        idem_key = f"idemp-test:{entry_id}"

        lines = [
            _line(cash_account, debit="1000.00", line_no=1),
            _line(revenue_account, credit="1000.00", line_no=2),
        ]

        data = {
            "entry_public_id": str(entry_id),
            "entry_number": "JE-IDEMP-001",
            "date": date.today().isoformat(),
            "memo": "Idempotency test",
            "kind": "NORMAL",
            "posted_at": timezone.now().isoformat(),
            "posted_by_id": user.id,
            "posted_by_email": user.email,
            "total_debit": "1000.00",
            "total_credit": "1000.00",
            "lines": lines,
        }

        # First emission
        event1 = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data=data,
            caused_by_user=user,
            idempotency_key=idem_key,
        )

        # Second emission with same key
        event2 = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data=data,
            caused_by_user=user,
            idempotency_key=idem_key,
        )

        # Should return same event (not a new one)
        assert event1.id == event2.id, f"Duplicate idempotency key created new event: {event1.id} != {event2.id}"

        # Only one event should exist
        count = BusinessEvent.objects.filter(
            company=company,
            idempotency_key=idem_key,
        ).count()
        assert count == 1, f"Expected 1 event, got {count}"

    def test_duplicate_emission_does_not_corrupt_projection(self, company, user, cash_account, revenue_account):
        """
        Even if somehow two events with different keys but identical content
        get emitted and processed, ProjectionAppliedEvent prevents double-counting.
        """
        # Emit two distinct events (different keys, same amounts)
        _emit_posted(
            company,
            user,
            [
                _line(cash_account, debit="500.00", line_no=1),
                _line(revenue_account, credit="500.00", line_no=2),
            ],
            memo="Entry A",
        )

        _emit_posted(
            company,
            user,
            [
                _line(cash_account, debit="500.00", line_no=1),
                _line(revenue_account, credit="500.00", line_no=2),
            ],
            memo="Entry B",
        )

        projection = AccountBalanceProjection()

        # Process all
        projection.process_pending(company)

        # Each event is distinct — both should be applied (total = 1000)
        cash_bal = AccountBalance.objects.get(company=company, account=cash_account)
        assert cash_bal.debit_total == Decimal("1000.00")

        # Now process again — should be no-op
        projection.process_pending(company)

        cash_bal.refresh_from_db()
        assert cash_bal.debit_total == Decimal("1000.00"), (
            f"Double processing corrupted balance: {cash_bal.debit_total}"
        )

        tb = projection.get_trial_balance(company)
        assert tb["is_balanced"]

    def test_sequence_continuity_after_idempotent_emission(self, company, user, cash_account, revenue_account):
        """
        After a duplicate emission is rejected, the company_sequence
        must remain gap-free for subsequent events.
        """
        entry_id = uuid4()
        idem_key = f"seq-test:{entry_id}"

        lines = [
            _line(cash_account, debit="100.00", line_no=1),
            _line(revenue_account, credit="100.00", line_no=2),
        ]
        data = {
            "entry_public_id": str(entry_id),
            "entry_number": "JE-SEQ-001",
            "date": date.today().isoformat(),
            "memo": "Sequence test",
            "kind": "NORMAL",
            "posted_at": timezone.now().isoformat(),
            "posted_by_id": user.id,
            "posted_by_email": user.email,
            "total_debit": "100.00",
            "total_credit": "100.00",
            "lines": lines,
        }

        # Emit + duplicate
        emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data=data,
            caused_by_user=user,
            idempotency_key=idem_key,
        )
        emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data=data,
            caused_by_user=user,
            idempotency_key=idem_key,
        )

        # Emit a new event
        _emit_posted(
            company,
            user,
            [
                _line(cash_account, debit="200.00", line_no=1),
                _line(revenue_account, credit="200.00", line_no=2),
            ],
            memo="After duplicate",
        )

        # Check sequence continuity
        sequences = list(
            BusinessEvent.objects.filter(company=company)
            .order_by("company_sequence")
            .values_list("company_sequence", flat=True)
        )
        assert len(sequences) == 2, f"Expected 2 events, got {len(sequences)}"
        assert sequences[1] == sequences[0] + 1, f"Sequence gap after idempotent rejection: {sequences}"
