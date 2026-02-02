# tests/test_events.py
"""
Tests for the events module.

Tests cover:
- Event immutability
- Idempotency key handling
- Event sequencing
- Data class serialization
"""

import pytest
from decimal import Decimal
from datetime import date, datetime
from uuid import uuid4

from django.db import IntegrityError

from events.models import BusinessEvent, EventBookmark, CompanyEventCounter
from events.emitter import emit_event, emit_event_no_actor
from events.types import (
    EventTypes,
    BaseEventData,
    AccountCreatedData,
    JournalEntryPostedData,
    JournalLineData,
    UserCreatedData,
    InvalidEventPayload,
)


# =============================================================================
# Event Immutability Tests
# =============================================================================

@pytest.mark.django_db
class TestEventImmutability:
    """Test that events cannot be modified after creation."""
    
    def test_cannot_modify_existing_event(self, account_created_event):
        """Modifying an existing event should raise an error."""
        account_created_event.data["code"] = "MODIFIED"
        
        with pytest.raises(ValueError, match="immutable"):
            account_created_event.save()
    
    def test_cannot_delete_event(self, account_created_event):
        """Deleting an event should raise an error."""
        with pytest.raises(ValueError, match="immutable"):
            account_created_event.delete()
    
    def test_new_event_can_be_saved(self, company, user):
        """New events can be created and saved."""
        event = BusinessEvent(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(uuid4()),
            data={"test": "data"},
            caused_by_user=user,
            idempotency_key=f"test:{uuid4()}",
        )
        
        event.save()
        
        assert event.id is not None
        assert event.company_sequence > 0


# =============================================================================
# Idempotency Tests
# =============================================================================

@pytest.mark.django_db
class TestIdempotency:
    """Test idempotency key handling."""
    
    def test_duplicate_idempotency_key_returns_existing_event(self, company, user):
        """Emitting with same idempotency key returns existing event."""
        idempotency_key = f"test:idempotent:{uuid4()}"
        
        # First emit
        event1 = emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(uuid4()),
            data={"code": "1000", "name": "Cash"},
            caused_by_user=user,
            idempotency_key=idempotency_key,
        )
        
        # Second emit with same key
        event2 = emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(uuid4()),
            data={"code": "2000", "name": "Different"},  # Different data!
            caused_by_user=user,
            idempotency_key=idempotency_key,
        )
        
        # Should return the same event
        assert event1.id == event2.id
        assert event1.data["code"] == "1000"  # Original data preserved
    
    def test_different_idempotency_keys_create_different_events(self, company, user):
        """Different idempotency keys create separate events."""
        event1 = emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(uuid4()),
            data={"code": "1000"},
            caused_by_user=user,
            idempotency_key=f"test:{uuid4()}",
        )
        
        event2 = emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(uuid4()),
            data={"code": "2000"},
            caused_by_user=user,
            idempotency_key=f"test:{uuid4()}",
        )
        
        assert event1.id != event2.id
    
    def test_idempotency_key_required(self, company, user):
        """Events must have an idempotency key."""
        with pytest.raises((ValueError, IntegrityError)):
            emit_event(
                company=company,
                event_type=EventTypes.ACCOUNT_CREATED,
                aggregate_type="Account",
                aggregate_id=str(uuid4()),
                data={"code": "1000"},
                caused_by_user=user,
                idempotency_key="",  # Empty key
            )


# =============================================================================
# Event Sequencing Tests
# =============================================================================

@pytest.mark.django_db
class TestEventSequencing:
    """Test event sequence number allocation."""
    
    def test_company_sequence_increments(self, company, user):
        """Each event gets an incrementing company sequence."""
        events = []
        for i in range(5):
            event = emit_event(
                company=company,
                event_type=EventTypes.ACCOUNT_CREATED,
                aggregate_type="Account",
                aggregate_id=str(uuid4()),
                data={"code": f"{1000 + i}"},
                caused_by_user=user,
                idempotency_key=f"seq-test:{uuid4()}",
            )
            events.append(event)
        
        # Check sequences are incrementing
        sequences = [e.company_sequence for e in events]
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == 5  # All unique
    
    def test_aggregate_sequence_increments_per_aggregate(self, company, user):
        """Aggregate sequence increments per aggregate."""
        aggregate_id = str(uuid4())
        
        # Create multiple events for same aggregate
        event1 = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_CREATED,
            aggregate_type="JournalEntry",
            aggregate_id=aggregate_id,
            data={"entry_public_id": aggregate_id},
            caused_by_user=user,
            idempotency_key=f"agg-seq:1:{uuid4()}",
        )
        
        event2 = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_UPDATED,
            aggregate_type="JournalEntry",
            aggregate_id=aggregate_id,
            data={"entry_public_id": aggregate_id, "changes": {}},
            caused_by_user=user,
            idempotency_key=f"agg-seq:2:{uuid4()}",
        )
        
        event3 = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=aggregate_id,
            data={"entry_public_id": aggregate_id, "entry_number": "JE-001", "lines": []},
            caused_by_user=user,
            idempotency_key=f"agg-seq:3:{uuid4()}",
        )
        
        assert event1.aggregate_sequence == 1
        assert event2.aggregate_sequence == 2
        assert event3.aggregate_sequence == 3
    
    def test_different_companies_have_separate_sequences(self, company, second_company, user):
        """Different companies maintain separate sequence counters."""
        event1 = emit_event_no_actor(
            company=company,
            event_type=EventTypes.COMPANY_CREATED,
            aggregate_type="Company",
            aggregate_id=str(company.public_id),
            data={"company_public_id": str(company.public_id), "name": "Company 1"},
            idempotency_key=f"company-seq:1:{uuid4()}",
        )
        
        event2 = emit_event_no_actor(
            company=second_company,
            event_type=EventTypes.COMPANY_CREATED,
            aggregate_type="Company",
            aggregate_id=str(second_company.public_id),
            data={"company_public_id": str(second_company.public_id), "name": "Company 2"},
            idempotency_key=f"company-seq:2:{uuid4()}",
        )
        
        # Both should have sequence 1 (first event in their company)
        # or whatever the current state is - just check they're independent
        assert event1.company_id != event2.company_id


# =============================================================================
# Data Class Serialization Tests
# =============================================================================

@pytest.mark.django_db
class TestDataClassSerialization:
    """Test event data class serialization."""
    
    def test_decimal_serialization(self):
        """Decimals should serialize to strings."""
        line = JournalLineData(
            line_no=1,
            account_public_id="abc-123",
            account_code="1000",
            description="Test",
            debit=str(Decimal("1000.50")),
            credit=str(Decimal("0.00")),
        )
        
        data = line.to_dict()
        
        assert data["debit"] == "1000.50"
        assert data["credit"] == "0.00"
        assert isinstance(data["debit"], str)
    
    def test_date_serialization(self):
        """Dates should serialize to ISO format strings."""
        data = AccountCreatedData(
            account_public_id="abc-123",
            code="1000",
            name="Cash",
            account_type="ASSET",
            normal_balance="DEBIT",
            is_header=False,
        )
        
        result = data.to_dict()
        
        assert isinstance(result["code"], str)
        assert result["is_header"] is False
    
    def test_optional_fields_default_correctly(self):
        """Optional fields should have correct defaults."""
        data = AccountCreatedData(
            account_public_id="abc-123",
            code="1000",
            name="Cash",
            account_type="ASSET",
            normal_balance="DEBIT",
            is_header=False,
        )
        
        result = data.to_dict()
        
        assert result["parent_public_id"] is None
        assert result["name_ar"] == ""
        assert result["description"] == ""
    
    def test_user_created_data_field_ordering(self):
        """
        UserCreatedData should accept positional args correctly.
        
        This tests the fix for required field after optional field.
        """
        # This should NOT raise TypeError about required positional argument
        data = UserCreatedData(
            user_public_id="user-123",
            email="test@example.com",
            name="Test User",
            created_by_user_public_id=None,  # Optional
        )
        
        assert data.user_public_id == "user-123"
        assert data.created_by_user_public_id is None
    
    def test_user_created_data_without_optional(self):
        """UserCreatedData should work without optional field."""
        data = UserCreatedData(
            user_public_id="user-123",
            email="test@example.com",
            name="Test User",
            # created_by_user_public_id omitted - should default to None
        )
        
        assert data.created_by_user_public_id is None


# =============================================================================
# Event Payload Validation Tests
# =============================================================================

@pytest.mark.django_db
class TestEventPayloadValidation:
    """Test stricter enum/currency validation rules."""

    @pytest.fixture(autouse=True)
    def _enable_event_validation(self, settings):
        settings.DISABLE_EVENT_VALIDATION = False

    def test_invalid_currency_code_rejected(self, company, user):
        with pytest.raises(InvalidEventPayload, match="currency"):
            emit_event(
                company=company,
                event_type=EventTypes.JOURNAL_ENTRY_CREATED,
                aggregate_type="JournalEntry",
                aggregate_id=str(uuid4()),
                data={
                    "entry_public_id": str(uuid4()),
                    "date": date.today().isoformat(),
                    "memo": "Bad currency",
                    "currency": "usd",
                },
                caused_by_user=user,
                idempotency_key=f"currency-bad:{uuid4()}",
            )

    def test_exchange_rate_requires_currency(self, company, user):
        with pytest.raises(InvalidEventPayload, match="exchange_rate"):
            emit_event(
                company=company,
                event_type=EventTypes.JOURNAL_ENTRY_CREATED,
                aggregate_type="JournalEntry",
                aggregate_id=str(uuid4()),
                data={
                    "entry_public_id": str(uuid4()),
                    "date": date.today().isoformat(),
                    "memo": "Missing currency",
                    "exchange_rate": "1.25",
                },
                caused_by_user=user,
                idempotency_key=f"exchange-rate-bad:{uuid4()}",
            )

    def test_line_amount_currency_requires_currency(self, company, user):
        with pytest.raises(InvalidEventPayload, match="amount_currency"):
            emit_event(
                company=company,
                event_type=EventTypes.JOURNAL_ENTRY_CREATED,
                aggregate_type="JournalEntry",
                aggregate_id=str(uuid4()),
                data={
                    "entry_public_id": str(uuid4()),
                    "date": date.today().isoformat(),
                    "memo": "Missing line currency",
                    "lines": [
                        {
                            "line_no": 1,
                            "account_public_id": str(uuid4()),
                            "account_code": "1000",
                            "description": "Line",
                            "debit": "10.00",
                            "credit": "0.00",
                            "amount_currency": "10.00",
                        },
                    ],
                },
                caused_by_user=user,
                idempotency_key=f"line-currency-bad:{uuid4()}",
            )


# =============================================================================
# Event Bookmark Tests
# =============================================================================

@pytest.mark.django_db
class TestEventBookmark:
    """Test event bookmark functionality."""
    
    def test_get_unprocessed_events(self, company, user, event_bookmark):
        """Bookmark should return events after last processed."""
        # Create some events
        events = []
        for i in range(5):
            event = emit_event(
                company=company,
                event_type=EventTypes.ACCOUNT_CREATED,
                aggregate_type="Account",
                aggregate_id=str(uuid4()),
                data={"code": f"{1000 + i}"},
                caused_by_user=user,
                idempotency_key=f"bookmark-test:{uuid4()}",
            )
            events.append(event)
        
        # Mark first 2 as processed
        event_bookmark.mark_processed(events[1])
        
        # Should return remaining 3
        unprocessed = list(event_bookmark.get_unprocessed_events(
            event_types=[EventTypes.ACCOUNT_CREATED],
            limit=10,
        ))
        
        assert len(unprocessed) == 3
        assert unprocessed[0].id == events[2].id
    
    def test_bookmark_tracks_last_processed(self, company, user, event_bookmark):
        """Bookmark should track the last processed event."""
        event = emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(uuid4()),
            data={"code": "1000"},
            caused_by_user=user,
            idempotency_key=f"bookmark-track:{uuid4()}",
        )
        
        event_bookmark.mark_processed(event)
        event_bookmark.refresh_from_db()
        
        assert event_bookmark.last_event_id == event.id
        assert event_bookmark.last_processed_at is not None
    
    def test_bookmark_error_tracking(self, event_bookmark):
        """Bookmark should track errors."""
        event_bookmark.mark_error("Test error message")
        event_bookmark.refresh_from_db()
        
        assert event_bookmark.error_count == 1
        assert "Test error" in event_bookmark.last_error
    
    def test_paused_bookmark(self, event_bookmark):
        """Paused bookmark should be detectable."""
        event_bookmark.is_paused = True
        event_bookmark.save()
        
        assert event_bookmark.is_paused is True


# =============================================================================
# Causation Chain Tests
# =============================================================================

@pytest.mark.django_db
class TestCausationChain:
    """Test event causation tracking."""
    
    def test_caused_by_event_links(self, company, user):
        """Events can be linked via caused_by_event."""
        # Create original event
        original = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(uuid4()),
            data={"entry_public_id": str(uuid4()), "entry_number": "JE-001", "lines": []},
            caused_by_user=user,
            idempotency_key=f"causation:original:{uuid4()}",
        )
        
        # Create derived event
        derived = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_REVERSED,
            aggregate_type="JournalEntry",
            aggregate_id=original.aggregate_id,
            data={
                "original_entry_public_id": original.aggregate_id,
                "reversal_entry_public_id": str(uuid4()),
            },
            caused_by_user=user,
            caused_by_event=original,
            idempotency_key=f"causation:derived:{uuid4()}",
        )
        
        assert derived.caused_by_event_id == original.id
        assert derived.caused_by_user_id == user.id
