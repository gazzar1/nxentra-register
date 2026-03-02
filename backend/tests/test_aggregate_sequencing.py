# tests/test_aggregate_sequencing.py
"""
Aggregate sequence allocation tests.

These tests verify that per-aggregate event sequences are:
- Gap-free: no missing sequence numbers
- Duplicate-free: no repeated sequence numbers
- Monotonically increasing: each new event gets next integer

Two categories:
1. Sequential test (all DBs): 20 sequential emits, assert sequences 1..20
2. Concurrent test (PostgreSQL only): 20 parallel emits via threads,
   assert sequences 1..20 with no gaps, no duplicates, no failures

The concurrent test is the real proof. Without it, we're trusting our
mental model that select_for_update() on CompanyEventCounter serializes
per-aggregate sequence allocation.
"""

import pytest
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.db import connections, connection
from django.utils import timezone

from events.emitter import emit_event_no_actor
from events.models import BusinessEvent
from events.types import EventTypes


def _is_postgresql():
    """Check if the default database is PostgreSQL."""
    return connection.vendor == "postgresql"


# ═══════════════════════════════════════════════════════════════════════════
# Sequential test (works on all DBs including SQLite)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestSequentialAggregateSequencing:
    """Prove sequence allocation is correct under sequential emission."""

    def test_20_sequential_emits_produce_gapless_sequences(
        self, company, user, owner_membership
    ):
        """
        Emit 20 events to the same aggregate sequentially.
        Assert sequences are exactly [1, 2, 3, ..., 20].
        """
        aggregate_id = str(uuid4())
        events = []

        for i in range(20):
            event = emit_event_no_actor(
                company=company,
                user=user,
                event_type=EventTypes.ACCOUNT_UPDATED,
                aggregate_type="Account",
                aggregate_id=aggregate_id,
                data={
                    "account_public_id": aggregate_id,
                    "changes": {"name": {"old": f"Name {i}", "new": f"Name {i+1}"}},
                },
                idempotency_key=f"seq-test-{aggregate_id}-{i}",
            )
            events.append(event)

        sequences = [e.sequence for e in events]
        assert sequences == list(range(1, 21)), (
            f"Expected [1..20], got {sequences}"
        )

        # Verify no duplicates
        assert len(set(sequences)) == 20

        # Verify company_sequences are also unique and monotonic
        company_seqs = [e.company_sequence for e in events]
        assert company_seqs == sorted(company_seqs)
        assert len(set(company_seqs)) == 20

    def test_different_aggregates_have_independent_sequences(
        self, company, user, owner_membership
    ):
        """
        Emit events to two different aggregates interleaved.
        Each aggregate should have its own independent sequence.
        """
        agg_a = str(uuid4())
        agg_b = str(uuid4())

        # Interleave: A, B, A, B, A, B
        for i in range(3):
            emit_event_no_actor(
                company=company,
                user=user,
                event_type=EventTypes.ACCOUNT_UPDATED,
                aggregate_type="Account",
                aggregate_id=agg_a,
                data={
                    "account_public_id": agg_a,
                    "changes": {"name": {"old": f"A{i}", "new": f"A{i+1}"}},
                },
                idempotency_key=f"interleave-a-{agg_a}-{i}",
            )
            emit_event_no_actor(
                company=company,
                user=user,
                event_type=EventTypes.ACCOUNT_UPDATED,
                aggregate_type="Account",
                aggregate_id=agg_b,
                data={
                    "account_public_id": agg_b,
                    "changes": {"name": {"old": f"B{i}", "new": f"B{i+1}"}},
                },
                idempotency_key=f"interleave-b-{agg_b}-{i}",
            )

        # Each aggregate should have sequences [1, 2, 3]
        a_seqs = list(
            BusinessEvent.objects.filter(
                company=company, aggregate_id=agg_a
            ).order_by("sequence").values_list("sequence", flat=True)
        )
        b_seqs = list(
            BusinessEvent.objects.filter(
                company=company, aggregate_id=agg_b
            ).order_by("sequence").values_list("sequence", flat=True)
        )

        assert a_seqs == [1, 2, 3], f"Aggregate A sequences: {a_seqs}"
        assert b_seqs == [1, 2, 3], f"Aggregate B sequences: {b_seqs}"


# ═══════════════════════════════════════════════════════════════════════════
# Concurrent test (PostgreSQL only — the real proof)
# ═══════════════════════════════════════════════════════════════════════════

def _emit_worker(company_id, user_id, aggregate_id, worker_index):
    """
    Thread worker: emit one event to the given aggregate.

    Each thread gets its own DB connection (Django allocates per-thread).
    We close connections at the end to avoid leaks.
    """
    try:
        # Each thread needs fresh imports after Django is set up
        from accounts.models import Company
        from django.contrib.auth import get_user_model
        from events.emitter import emit_event_no_actor
        from events.types import EventTypes
        from accounts import rls
        from django.conf import settings

        User = get_user_model()

        company = Company.objects.using("default").get(id=company_id)
        user_obj = User.objects.using("default").get(id=user_id)

        # Set RLS bypass for this thread's connection
        if getattr(settings, "RLS_BYPASS", False):
            rls.set_rls_bypass(True)

        event = emit_event_no_actor(
            company=company,
            user=user_obj,
            event_type=EventTypes.ACCOUNT_UPDATED,
            aggregate_type="Account",
            aggregate_id=aggregate_id,
            data={
                "account_public_id": aggregate_id,
                "changes": {
                    "name": {
                        "old": f"Name-{worker_index}",
                        "new": f"Name-{worker_index + 1}",
                    }
                },
            },
            idempotency_key=f"concurrent-{aggregate_id}-{worker_index}",
        )
        return {"sequence": event.sequence, "company_sequence": event.company_sequence}
    except Exception as e:
        return {"error": str(e), "type": type(e).__name__}
    finally:
        connections.close_all()


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    not _is_postgresql(),
    reason="Concurrency test requires PostgreSQL (SQLite has no row-level locking)",
)
class TestConcurrentAggregateSequencing:
    """
    Prove that concurrent event emissions to the same aggregate produce
    gap-free, duplicate-free sequences under PostgreSQL row-locking.

    This test uses real threads with real committed transactions.
    It is the definitive proof that CompanyEventCounter.select_for_update()
    correctly serializes per-aggregate sequence allocation.
    """

    def test_20_concurrent_emits_produce_gapless_sequences(self, company, user, owner_membership):
        """
        Fire 20 concurrent emits to the same aggregate.
        Assert sequences are exactly {1..20} with no gaps or duplicates.
        """
        aggregate_id = str(uuid4())
        num_workers = 20

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(
                    _emit_worker,
                    company.id,
                    user.id,
                    aggregate_id,
                    i,
                ): i
                for i in range(num_workers)
            }

            results = []
            for future in as_completed(futures):
                result = future.result()
                results.append(result)

        # Check for errors
        errors = [r for r in results if "error" in r]
        assert not errors, f"Worker errors: {errors}"

        # Verify sequences
        sequences = sorted(r["sequence"] for r in results)
        assert sequences == list(range(1, num_workers + 1)), (
            f"Expected [1..{num_workers}], got {sequences}"
        )

        # Verify no duplicate sequences
        assert len(set(sequences)) == num_workers, (
            f"Duplicate sequences detected: {sequences}"
        )

        # Verify company_sequences are unique
        company_seqs = sorted(r["company_sequence"] for r in results)
        assert len(set(company_seqs)) == num_workers, (
            f"Duplicate company sequences: {company_seqs}"
        )

        # Double-check from the database
        db_events = list(
            BusinessEvent.objects.filter(
                company=company,
                aggregate_type="Account",
                aggregate_id=aggregate_id,
            ).order_by("sequence")
        )
        assert len(db_events) == num_workers
        db_seqs = [e.sequence for e in db_events]
        assert db_seqs == list(range(1, num_workers + 1))
