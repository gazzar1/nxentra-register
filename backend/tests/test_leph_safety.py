# tests/test_leph_safety.py
"""
LEPH (Large Event Payload Handling) safety tests.

These tests verify that large event payloads stored externally
are fully recoverable through event.get_data() and through
aggregate replay. Without these tests, LEPH correctness is
operating on trust alone.

Tests:
1. External storage roundtrip: payload > 64KB stored externally,
   recovered via get_data() with all lines intact.
2. Aggregate replay with external payload: load_journal_entry_aggregate()
   produces correct totals from externally-stored events.
3. Payload hash integrity: get_data() verifies SHA-256 hash on read.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from django.utils import timezone

from accounting.aggregates import load_journal_entry_aggregate
from events.emitter import emit_event_no_actor
from events.models import BusinessEvent
from events.payload_policy import INLINE_MAX_SIZE
from events.serialization import estimate_json_size
from events.types import EventTypes


@pytest.mark.django_db
class TestLEPHExternalStorageRoundtrip:
    """Verify external storage payloads are fully recoverable."""

    def _make_large_je_posted_data(self, entry_public_id, user, num_lines=300):
        """
        Build a JOURNAL_ENTRY_POSTED payload with enough lines to exceed
        the 64KB inline threshold, forcing external storage.
        """
        lines = []
        for i in range(1, num_lines + 1):
            lines.append({
                "line_no": i,
                "account_public_id": str(uuid4()),
                "account_code": f"{1000 + i}",
                "description": f"Test line {i} with padding " + ("x" * 150),
                "debit": f"{Decimal('100.00')}",
                "credit": "0.00",
            })
        # Add one balancing credit line
        total_debit = Decimal("100.00") * num_lines
        lines.append({
            "line_no": num_lines + 1,
            "account_public_id": str(uuid4()),
            "account_code": "2000",
            "description": "Balancing credit line",
            "debit": "0.00",
            "credit": str(total_debit),
        })

        return {
            "entry_public_id": str(entry_public_id),
            "entry_number": "JE-LEPH-0001",
            "date": "2026-01-15",
            "memo": "LEPH safety test entry",
            "kind": "NORMAL",
            "posted_at": timezone.now().isoformat(),
            "posted_by_id": user.id,
            "posted_by_email": user.email,
            "total_debit": str(total_debit),
            "total_credit": str(total_debit),
            "lines": lines,
        }

    def test_external_storage_roundtrip(self, company, user, owner_membership):
        """
        Emit a JE event with payload > 64KB.
        Verify:
        - Event uses external storage
        - get_data() returns the complete payload
        - All lines are present (no truncation)
        - Totals match
        """
        entry_id = uuid4()
        data = self._make_large_je_posted_data(entry_id, user)

        # Sanity: confirm payload exceeds inline threshold
        payload_size = estimate_json_size(data)
        assert payload_size > INLINE_MAX_SIZE, (
            f"Test payload {payload_size} bytes should exceed "
            f"INLINE_MAX_SIZE {INLINE_MAX_SIZE} bytes"
        )

        event = emit_event_no_actor(
            company=company,
            user=user,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data=data,
            idempotency_key=f"leph-test:{entry_id}",
        )

        # Verify external storage was used
        assert event.payload_storage == "external", (
            f"Expected external storage, got {event.payload_storage}"
        )
        assert event.payload_ref is not None, "External event must have payload_ref"
        assert event.payload_hash, "External event must have payload_hash"
        assert event.data == {}, "Inline data should be empty for external events"

        # Verify get_data() returns full payload
        recovered = event.get_data()
        assert recovered["entry_public_id"] == str(entry_id)
        assert recovered["entry_number"] == "JE-LEPH-0001"

        # Verify ALL lines are present (no truncation)
        expected_line_count = 301  # 300 debit + 1 credit
        assert len(recovered["lines"]) == expected_line_count, (
            f"Expected {expected_line_count} lines, got {len(recovered['lines'])}"
        )

        # Verify line content integrity (spot-check first, last, middle)
        assert recovered["lines"][0]["line_no"] == 1
        assert recovered["lines"][0]["debit"] == "100.00"
        assert recovered["lines"][-1]["line_no"] == expected_line_count
        assert recovered["lines"][-1]["credit"] == recovered["total_credit"]
        assert recovered["lines"][150]["line_no"] == 151

        # Verify totals match
        assert recovered["total_debit"] == recovered["total_credit"]

    def test_aggregate_replay_with_external_payload(self, company, user, owner_membership):
        """
        Emit a JOURNAL_ENTRY_POSTED event with external storage,
        then replay the aggregate and verify totals match.
        """
        entry_id = uuid4()
        data = self._make_large_je_posted_data(entry_id, user)

        emit_event_no_actor(
            company=company,
            user=user,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data=data,
            idempotency_key=f"leph-agg-test:{entry_id}",
        )

        # Replay the aggregate
        aggregate = load_journal_entry_aggregate(company, str(entry_id))
        assert aggregate is not None, "Aggregate should exist"

        # Verify aggregate has all lines
        assert len(aggregate.lines) == 301

        # Verify totals via aggregate properties
        expected_total = Decimal("100.00") * 300
        assert aggregate.total_debit == expected_total
        assert aggregate.total_credit == expected_total
        assert aggregate.status == "POSTED"

    def test_payload_hash_integrity(self, company, user, owner_membership):
        """
        Verify that get_data() checks the SHA-256 hash on external payloads.
        If the hash doesn't match, it should raise IntegrityError.
        """
        entry_id = uuid4()
        data = self._make_large_je_posted_data(entry_id, user)

        event = emit_event_no_actor(
            company=company,
            user=user,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data=data,
            idempotency_key=f"leph-hash-test:{entry_id}",
        )

        assert event.payload_storage == "external"

        # Tamper with the hash (simulate corruption)
        original_hash = event.payload_hash
        BusinessEvent.objects.filter(pk=event.pk).update(
            payload_hash="0000000000000000000000000000000000000000000000000000000000000000"
        )

        # Refresh from DB
        event.refresh_from_db()
        assert event.payload_hash != original_hash

        # get_data() should raise IntegrityError due to hash mismatch
        from django.db import IntegrityError
        with pytest.raises(IntegrityError, match="hash mismatch"):
            event.get_data()

    def test_inline_small_payload_still_works(self, company, user, owner_membership):
        """
        Verify that small payloads remain inline and get_data() works.
        This is the baseline: LEPH should not break normal events.
        """
        entry_id = uuid4()
        data = {
            "entry_public_id": str(entry_id),
            "entry_number": "JE-SMALL-0001",
            "date": "2026-01-15",
            "memo": "Small entry",
            "kind": "NORMAL",
            "posted_at": timezone.now().isoformat(),
            "posted_by_id": user.id,
            "posted_by_email": user.email,
            "total_debit": "500.00",
            "total_credit": "500.00",
            "lines": [
                {
                    "line_no": 1,
                    "account_public_id": str(uuid4()),
                    "account_code": "1000",
                    "description": "Debit",
                    "debit": "500.00",
                    "credit": "0.00",
                },
                {
                    "line_no": 2,
                    "account_public_id": str(uuid4()),
                    "account_code": "2000",
                    "description": "Credit",
                    "debit": "0.00",
                    "credit": "500.00",
                },
            ],
        }

        # Should be well under inline threshold
        assert estimate_json_size(data) < INLINE_MAX_SIZE

        event = emit_event_no_actor(
            company=company,
            user=user,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data=data,
            idempotency_key=f"leph-inline-test:{entry_id}",
        )

        assert event.payload_storage == "inline"
        assert event.payload_ref is None

        # get_data() should return the same data
        recovered = event.get_data()
        assert recovered["entry_public_id"] == str(entry_id)
        assert len(recovered["lines"]) == 2
        assert recovered["total_debit"] == "500.00"

    def test_content_addressed_deduplication(self, company, user, owner_membership):
        """
        Verify that identical external payloads share the same EventPayload record.
        """
        entry_id_1 = uuid4()
        entry_id_2 = uuid4()

        # Build two events with IDENTICAL payload content
        # (same lines, same everything except the idempotency key)
        data = self._make_large_je_posted_data(entry_id_1, user)

        event1 = emit_event_no_actor(
            company=company,
            user=user,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id_1),
            data=data,
            idempotency_key=f"leph-dedup-1:{entry_id_1}",
        )

        # Emit again with same data but different idempotency key
        event2 = emit_event_no_actor(
            company=company,
            user=user,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id_2),
            data=data,
            idempotency_key=f"leph-dedup-2:{entry_id_2}",
        )

        # Both should use external storage
        assert event1.payload_storage == "external"
        assert event2.payload_storage == "external"

        # Both should reference the SAME EventPayload record (deduplication)
        assert event1.payload_ref_id == event2.payload_ref_id, (
            "Identical payloads should be deduplicated to the same EventPayload record"
        )

        # Both should still return correct data
        assert event1.get_data() == event2.get_data()
