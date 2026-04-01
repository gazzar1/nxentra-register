# projections/statistical_entry.py
"""
Statistical entry projection (read model).

This projection materializes statistical entries from events.
It is the ONLY code allowed to write to the StatisticalEntry model.

Events consumed:
- STATISTICAL_ENTRY_CREATED
- STATISTICAL_ENTRY_UPDATED
- STATISTICAL_ENTRY_POSTED
- STATISTICAL_ENTRY_REVERSED
- STATISTICAL_ENTRY_DELETED
"""

import logging
from datetime import date, datetime
from decimal import Decimal

from accounting.models import Account, JournalEntry, StatisticalEntry
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection, projection_registry

logger = logging.getLogger(__name__)


def _parse_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    return value


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


class StatisticalEntryProjection(BaseProjection):
    """
    Projection that maintains the StatisticalEntry read model.

    Handles creation, update, posting, reversal, and deletion of
    statistical entries based on events emitted by commands.
    """

    @property
    def name(self) -> str:
        return "statistical_entry_read_model"

    @property
    def consumes(self):
        return [
            EventTypes.STATISTICAL_ENTRY_CREATED,
            EventTypes.STATISTICAL_ENTRY_UPDATED,
            EventTypes.STATISTICAL_ENTRY_POSTED,
            EventTypes.STATISTICAL_ENTRY_REVERSED,
            EventTypes.STATISTICAL_ENTRY_DELETED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        data = event.get_data()

        if event.event_type == EventTypes.STATISTICAL_ENTRY_CREATED:
            self._handle_created(event, data)
        elif event.event_type == EventTypes.STATISTICAL_ENTRY_UPDATED:
            self._handle_updated(event, data)
        elif event.event_type == EventTypes.STATISTICAL_ENTRY_POSTED:
            self._handle_posted(event, data)
        elif event.event_type == EventTypes.STATISTICAL_ENTRY_REVERSED:
            self._handle_reversed(event, data)
        elif event.event_type == EventTypes.STATISTICAL_ENTRY_DELETED:
            self._handle_deleted(event, data)
        else:
            logger.warning(
                "Unhandled event type for StatisticalEntryProjection: %s",
                event.event_type
            )

    def _handle_created(self, event: BusinessEvent, data: dict) -> None:
        """Handle STATISTICAL_ENTRY_CREATED event."""
        # Look up the account
        account = Account.objects.filter(
            company=event.company,
            public_id=data["account_public_id"],
        ).first()

        if not account:
            logger.error(
                "Account not found for statistical entry: %s",
                data["account_public_id"]
            )
            return

        # Look up related journal entry if provided
        related_je = None
        if data.get("related_journal_entry_public_id"):
            related_je = JournalEntry.objects.filter(
                company=event.company,
                public_id=data["related_journal_entry_public_id"],
            ).first()

        # Look up created_by user
        created_by = None
        if data.get("created_by_id"):
            from django.contrib.auth import get_user_model
            User = get_user_model()
            created_by = User.objects.filter(pk=data["created_by_id"]).first()

        # Create the statistical entry
        StatisticalEntry.objects.projection().update_or_create(
            company=event.company,
            public_id=data["entry_public_id"],
            defaults={
                "account": account,
                "date": _parse_date(data["entry_date"]),
                "quantity": Decimal(data["quantity"]),
                "direction": data["direction"],
                "unit": data["unit"],
                "memo": data.get("memo", ""),
                "memo_ar": data.get("memo_ar", ""),
                "source_module": data.get("source_module", ""),
                "source_document": data.get("source_document", ""),
                "related_journal_entry": related_je,
                "created_by": created_by,
                "status": StatisticalEntry.Status.DRAFT,
            },
        )

    def _handle_updated(self, event: BusinessEvent, data: dict) -> None:
        """Handle STATISTICAL_ENTRY_UPDATED event."""
        entry = StatisticalEntry.objects.filter(
            company=event.company,
            public_id=data["entry_public_id"],
        ).first()

        if not entry:
            logger.warning(
                "Statistical entry not found for update: %s",
                data["entry_public_id"]
            )
            return

        changes = data.get("changes", {})
        for field, change in changes.items():
            new_value = change.get("new")

            # Handle type conversions
            if field == "date":
                new_value = _parse_date(new_value)
            elif field == "quantity":
                new_value = Decimal(new_value)

            setattr(entry, field, new_value)

        entry.save(_projection_write=True)

    def _handle_posted(self, event: BusinessEvent, data: dict) -> None:
        """Handle STATISTICAL_ENTRY_POSTED event."""
        entry = StatisticalEntry.objects.filter(
            company=event.company,
            public_id=data["entry_public_id"],
        ).first()

        if not entry:
            logger.warning(
                "Statistical entry not found for posting: %s",
                data["entry_public_id"]
            )
            return

        # Look up posted_by user
        posted_by = None
        if data.get("posted_by_id"):
            from django.contrib.auth import get_user_model
            User = get_user_model()
            posted_by = User.objects.filter(pk=data["posted_by_id"]).first()

        entry.status = StatisticalEntry.Status.POSTED
        entry.posted_at = _parse_datetime(data["posted_at"])
        entry.posted_by = posted_by
        entry.save(_projection_write=True)

    def _handle_reversed(self, event: BusinessEvent, data: dict) -> None:
        """Handle STATISTICAL_ENTRY_REVERSED event."""
        # Find the original entry
        original = StatisticalEntry.objects.filter(
            company=event.company,
            public_id=data["original_entry_public_id"],
        ).first()

        if not original:
            logger.warning(
                "Original statistical entry not found for reversal: %s",
                data["original_entry_public_id"]
            )
            return

        # Look up reversed_by user
        reversed_by = None
        if data.get("reversed_by_id"):
            from django.contrib.auth import get_user_model
            User = get_user_model()
            reversed_by = User.objects.filter(pk=data["reversed_by_id"]).first()

        # Determine opposite direction
        opposite_direction = (
            StatisticalEntry.Direction.DECREASE
            if original.direction == StatisticalEntry.Direction.INCREASE
            else StatisticalEntry.Direction.INCREASE
        )

        # Create the reversal entry
        reversal, _ = StatisticalEntry.objects.projection().update_or_create(
            company=event.company,
            public_id=data["reversal_entry_public_id"],
            defaults={
                "account": original.account,
                "date": _parse_date(data["reversal_date"]),
                "quantity": original.quantity,
                "direction": opposite_direction,
                "unit": original.unit,
                "memo": f"Reversal of entry {original.public_id}",
                "memo_ar": original.memo_ar,
                "source_module": original.source_module,
                "source_document": original.source_document,
                "related_journal_entry": original.related_journal_entry,
                "created_by": reversed_by,
                "status": StatisticalEntry.Status.POSTED,
                "posted_at": _parse_datetime(data["reversed_at"]),
                "posted_by": reversed_by,
                "reverses_entry": original,
            },
        )

        # Mark the original as reversed
        original.status = StatisticalEntry.Status.REVERSED
        original.save(_projection_write=True)

    def _handle_deleted(self, event: BusinessEvent, data: dict) -> None:
        """Handle STATISTICAL_ENTRY_DELETED event."""
        # Actually delete the draft entry
        deleted_count, _ = StatisticalEntry.objects.filter(
            company=event.company,
            public_id=data["entry_public_id"],
            status=StatisticalEntry.Status.DRAFT,
        ).delete()

        if deleted_count == 0:
            logger.warning(
                "Statistical entry not found for deletion (or not in DRAFT status): %s",
                data["entry_public_id"]
            )

    def _clear_projected_data(self, company) -> None:
        """Clear all statistical entries for rebuild."""
        StatisticalEntry.objects.filter(company=company).delete()


# Register the projection
projection_registry.register(StatisticalEntryProjection())
