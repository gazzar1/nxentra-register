# tests/e2e/test_statistical_entry.py
"""
End-to-end tests for statistical entry event sourcing.

These tests verify the full event-sourced lifecycle:
1. Create statistical entry -> STATISTICAL_ENTRY_CREATED event
2. Update statistical entry -> STATISTICAL_ENTRY_UPDATED event
3. Post statistical entry -> STATISTICAL_ENTRY_POSTED event
4. Reverse statistical entry -> STATISTICAL_ENTRY_REVERSED event
5. Delete statistical entry -> STATISTICAL_ENTRY_DELETED event
"""

import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from accounts.models import Company
from accounting.models import Account, StatisticalEntry
from accounting.commands import (
    create_statistical_entry,
    update_statistical_entry,
    post_statistical_entry,
    reverse_statistical_entry,
    delete_statistical_entry,
)
from events.models import BusinessEvent
from events.types import EventTypes


@pytest.mark.django_db(transaction=True)
class TestStatisticalEntryEventSourcing:
    """End-to-end tests for statistical entry event sourcing."""

    @pytest.fixture
    def statistical_account(self, db, company):
        """Create a statistical account."""
        return Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="9100",
            name="Employee Headcount",
            account_type=Account.AccountType.MEMO,
            normal_balance=Account.NormalBalance.DEBIT,
            ledger_domain=Account.LedgerDomain.STATISTICAL,
            unit_of_measure="employees",
            status=Account.Status.ACTIVE,
        )

    def test_create_statistical_entry_emits_event(
        self, actor_context, company, statistical_account
    ):
        """Creating a statistical entry should emit STATISTICAL_ENTRY_CREATED event."""
        result = create_statistical_entry(
            actor_context,
            account_id=statistical_account.id,
            entry_date=date.today().isoformat(),
            quantity="10",
            direction=StatisticalEntry.Direction.INCREASE,
            unit="employees",
            memo="Monthly headcount",
        )

        assert result.success, f"Failed: {result.error}"
        entry_public_id = result.data["entry_public_id"]

        # Verify event was emitted
        event = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.STATISTICAL_ENTRY_CREATED,
            aggregate_id=entry_public_id,
        ).first()
        assert event is not None, "STATISTICAL_ENTRY_CREATED event not found"
        assert event.data["quantity"] == "10"
        assert event.data["direction"] == "INCREASE"

        # Verify projection created the entry
        entry = StatisticalEntry.objects.get(public_id=entry_public_id)
        assert entry.quantity == Decimal("10")
        assert entry.status == StatisticalEntry.Status.DRAFT

    def test_update_statistical_entry_emits_event(
        self, actor_context, company, statistical_account
    ):
        """Updating a statistical entry should emit STATISTICAL_ENTRY_UPDATED event."""
        # Create entry
        create_result = create_statistical_entry(
            actor_context,
            account_id=statistical_account.id,
            entry_date=date.today().isoformat(),
            quantity="10",
            direction=StatisticalEntry.Direction.INCREASE,
            unit="employees",
        )
        entry_public_id = create_result.data["entry_public_id"]

        # Update entry
        update_result = update_statistical_entry(
            actor_context,
            entry_public_id=entry_public_id,
            quantity="15",
            memo="Updated headcount",
        )
        assert update_result.success, f"Failed: {update_result.error}"

        # Verify event was emitted
        event = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.STATISTICAL_ENTRY_UPDATED,
            aggregate_id=entry_public_id,
        ).first()
        assert event is not None, "STATISTICAL_ENTRY_UPDATED event not found"
        assert "quantity" in event.data["changes"]
        # Compare as Decimals to handle formatting differences
        assert Decimal(event.data["changes"]["quantity"]["old"]) == Decimal("10")
        assert Decimal(event.data["changes"]["quantity"]["new"]) == Decimal("15")

        # Verify projection updated the entry
        entry = StatisticalEntry.objects.get(public_id=entry_public_id)
        assert entry.quantity == Decimal("15")
        assert entry.memo == "Updated headcount"

    def test_post_statistical_entry_emits_event(
        self, actor_context, company, statistical_account
    ):
        """Posting a statistical entry should emit STATISTICAL_ENTRY_POSTED event."""
        # Create entry
        create_result = create_statistical_entry(
            actor_context,
            account_id=statistical_account.id,
            entry_date=date.today().isoformat(),
            quantity="20",
            direction=StatisticalEntry.Direction.INCREASE,
            unit="employees",
        )
        entry_public_id = create_result.data["entry_public_id"]

        # Post entry
        post_result = post_statistical_entry(actor_context, entry_public_id)
        assert post_result.success, f"Failed: {post_result.error}"

        # Verify event was emitted
        event = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.STATISTICAL_ENTRY_POSTED,
            aggregate_id=entry_public_id,
        ).first()
        assert event is not None, "STATISTICAL_ENTRY_POSTED event not found"
        assert event.data["posted_by_id"] == actor_context.user.id

        # Verify projection updated status
        entry = StatisticalEntry.objects.get(public_id=entry_public_id)
        assert entry.status == StatisticalEntry.Status.POSTED
        assert entry.posted_at is not None

    def test_reverse_statistical_entry_emits_event(
        self, actor_context, company, statistical_account
    ):
        """Reversing a statistical entry should emit STATISTICAL_ENTRY_REVERSED event."""
        # Create and post entry
        create_result = create_statistical_entry(
            actor_context,
            account_id=statistical_account.id,
            entry_date=date.today().isoformat(),
            quantity="5",
            direction=StatisticalEntry.Direction.INCREASE,
            unit="employees",
        )
        entry_public_id = create_result.data["entry_public_id"]
        post_statistical_entry(actor_context, entry_public_id)

        # Reverse entry
        reverse_result = reverse_statistical_entry(actor_context, entry_public_id)
        assert reverse_result.success, f"Failed: {reverse_result.error}"

        reversal_public_id = reverse_result.data["reversal_entry_public_id"]

        # Verify event was emitted
        event = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.STATISTICAL_ENTRY_REVERSED,
            aggregate_id=entry_public_id,
        ).first()
        assert event is not None, "STATISTICAL_ENTRY_REVERSED event not found"
        assert event.data["reversal_entry_public_id"] == reversal_public_id

        # Verify original is marked reversed
        original = StatisticalEntry.objects.get(public_id=entry_public_id)
        assert original.status == StatisticalEntry.Status.REVERSED

        # Verify reversal entry exists with opposite direction
        reversal = StatisticalEntry.objects.get(public_id=reversal_public_id)
        assert reversal.direction == StatisticalEntry.Direction.DECREASE
        assert reversal.quantity == Decimal("5")
        assert reversal.status == StatisticalEntry.Status.POSTED

    def test_delete_statistical_entry_emits_event(
        self, actor_context, company, statistical_account
    ):
        """Deleting a draft statistical entry should emit STATISTICAL_ENTRY_DELETED event."""
        # Create entry
        create_result = create_statistical_entry(
            actor_context,
            account_id=statistical_account.id,
            entry_date=date.today().isoformat(),
            quantity="3",
            direction=StatisticalEntry.Direction.DECREASE,
            unit="employees",
        )
        entry_public_id = create_result.data["entry_public_id"]

        # Delete entry
        delete_result = delete_statistical_entry(actor_context, entry_public_id)
        assert delete_result.success, f"Failed: {delete_result.error}"

        # Verify event was emitted
        event = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.STATISTICAL_ENTRY_DELETED,
            aggregate_id=entry_public_id,
        ).first()
        assert event is not None, "STATISTICAL_ENTRY_DELETED event not found"

        # Verify entry was deleted
        assert not StatisticalEntry.objects.filter(public_id=entry_public_id).exists()

    def test_cannot_update_posted_entry(
        self, actor_context, company, statistical_account
    ):
        """Cannot update a posted statistical entry."""
        # Create and post
        create_result = create_statistical_entry(
            actor_context,
            account_id=statistical_account.id,
            entry_date=date.today().isoformat(),
            quantity="10",
            direction=StatisticalEntry.Direction.INCREASE,
            unit="employees",
        )
        entry_public_id = create_result.data["entry_public_id"]
        post_statistical_entry(actor_context, entry_public_id)

        # Try to update
        update_result = update_statistical_entry(
            actor_context,
            entry_public_id=entry_public_id,
            quantity="20",
        )
        assert not update_result.success
        assert "POSTED" in update_result.error

    def test_cannot_delete_posted_entry(
        self, actor_context, company, statistical_account
    ):
        """Cannot delete a posted statistical entry."""
        # Create and post
        create_result = create_statistical_entry(
            actor_context,
            account_id=statistical_account.id,
            entry_date=date.today().isoformat(),
            quantity="10",
            direction=StatisticalEntry.Direction.INCREASE,
            unit="employees",
        )
        entry_public_id = create_result.data["entry_public_id"]
        post_statistical_entry(actor_context, entry_public_id)

        # Try to delete
        delete_result = delete_statistical_entry(actor_context, entry_public_id)
        assert not delete_result.success
        assert "POSTED" in delete_result.error or "reversal" in delete_result.error.lower()

    def test_cannot_reverse_draft_entry(
        self, actor_context, company, statistical_account
    ):
        """Cannot reverse a draft statistical entry."""
        # Create entry (don't post)
        create_result = create_statistical_entry(
            actor_context,
            account_id=statistical_account.id,
            entry_date=date.today().isoformat(),
            quantity="10",
            direction=StatisticalEntry.Direction.INCREASE,
            unit="employees",
        )
        entry_public_id = create_result.data["entry_public_id"]

        # Try to reverse
        reverse_result = reverse_statistical_entry(actor_context, entry_public_id)
        assert not reverse_result.success
        assert "POSTED" in reverse_result.error

    def test_validates_statistical_account(self, actor_context, cash_account):
        """Cannot create statistical entry for non-statistical account."""
        result = create_statistical_entry(
            actor_context,
            account_id=cash_account.id,
            entry_date=date.today().isoformat(),
            quantity="10",
            direction=StatisticalEntry.Direction.INCREASE,
            unit="units",
        )
        assert not result.success
        assert "statistical" in result.error.lower() or "off-balance" in result.error.lower()
