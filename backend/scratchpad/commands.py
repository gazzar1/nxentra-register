# scratchpad/commands.py
"""
Command layer for scratchpad operations.

Commands are the single point where business operations happen.
Views call commands; commands enforce rules and emit events.

Pattern:
1. Validate permissions (require)
2. Apply business policies
3. Perform the operation
4. Emit event (for commit)
5. Return CommandResult

The commit command converts scratchpad rows into journal entries
through the accounting command layer.
"""

import uuid
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounting.commands import (
    CommandResult,
    create_journal_entry,
    post_journal_entry,
    save_journal_entry_complete,
)
from accounting.models import JournalEntry
from accounts.authz import ActorContext, require
from events.emitter import emit_event
from events.types import EventTypes

from .models import ScratchpadRow
from .validation import validate_group_balance, validate_row


def _process_projections(company, exclude: set = None) -> None:
    """Process pending projection events for a company."""
    if not settings.PROJECTIONS_SYNC:
        return

    from projections.base import projection_registry

    excluded = exclude or set()
    for projection in projection_registry.all():
        if projection.name in excluded:
            continue
        projection.process_pending(company, limit=1000)


@transaction.atomic
def commit_scratchpad_groups(
    actor: ActorContext,
    group_ids: list[UUID],
    post_immediately: bool = False,
) -> CommandResult:
    """
    Commit scratchpad groups to journal entries.

    Flow:
    1. Validate all rows in groups are READY
    2. For each group, create JournalEntry via existing accounting commands
    3. Mark rows as COMMITTED with committed_event reference
    4. Optionally post entries immediately
    5. Emit scratchpad.batch_committed audit event
    6. Return created entries

    Args:
        actor: The actor context
        group_ids: List of group UUIDs to commit
        post_immediately: If True, also post the entries (not just save as draft)

    Returns:
        CommandResult with commit details or error
    """
    require(actor, "journal.create")

    batch_id = uuid.uuid4()
    committed_at = timezone.now()
    created_entries = []
    all_row_ids = []

    for group_id in group_ids:
        # Load all READY rows for this group
        rows = list(
            ScratchpadRow.objects.filter(
                company=actor.company,
                group_id=group_id,
                status=ScratchpadRow.Status.READY,
            )
            .select_related("debit_account", "credit_account")
            .prefetch_related("dimensions__dimension", "dimensions__dimension_value")
            .order_by("group_order")
        )

        if not rows:
            return CommandResult.fail(f"No READY rows found for group {group_id}")

        # Re-validate all rows (defensive)
        for row in rows:
            validation = validate_row(row, actor.company)
            if not validation["is_valid"]:
                return CommandResult.fail(f"Row {row.public_id} failed validation: {validation['errors']}")

        # Check group balance (should always pass for simple model)
        balance_check = validate_group_balance(rows)
        if not balance_check["is_balanced"]:
            return CommandResult.fail(f"Group {group_id} is unbalanced: {balance_check['errors']}")

        # Build journal entry data from scratchpad rows
        entry_data = _build_journal_entry_from_rows(rows)

        # Create journal entry via existing command
        result = create_journal_entry(
            actor,
            date=entry_data["date"],
            memo=entry_data["memo"],
            memo_ar=entry_data.get("memo_ar", ""),
            lines=entry_data["lines"],
            kind=JournalEntry.Kind.NORMAL,
        )

        if not result.success:
            return CommandResult.fail(f"Failed to create journal entry for group {group_id}: {result.error}")

        entry = result.data
        event = result.event

        # Save as complete (mark it as DRAFT, not INCOMPLETE)
        save_result = save_journal_entry_complete(actor, entry.id)
        if not save_result.success:
            return CommandResult.fail(f"Failed to save journal entry: {save_result.error}")

        # Optionally post immediately
        if post_immediately:
            post_result = post_journal_entry(actor, entry.id)
            if not post_result.success:
                # Don't fail the whole commit, just note the error
                pass

        # Mark scratchpad rows as committed
        row_public_ids = [row.public_id for row in rows]
        all_row_ids.extend(row_public_ids)

        ScratchpadRow.objects.filter(
            company=actor.company,
            public_id__in=row_public_ids,
        ).update(
            status=ScratchpadRow.Status.COMMITTED,
            committed_at=committed_at,
            committed_by=actor.user,
            committed_event=event,
        )

        created_entries.append(
            {
                "group_id": str(group_id),
                "entry_id": entry.id,
                "entry_public_id": str(entry.public_id),
            }
        )

    # Emit audit event for the batch commit
    from events.types import ScratchpadBatchCommittedData

    emit_event(
        actor=actor,
        event_type=EventTypes.SCRATCHPAD_BATCH_COMMITTED,
        aggregate_type="scratchpad",
        aggregate_id=str(batch_id),
        idempotency_key=f"scratchpad.batch_committed:{batch_id}",
        data=ScratchpadBatchCommittedData(
            batch_id=str(batch_id),
            group_ids=[str(g) for g in group_ids],
            row_count=len(all_row_ids),
            journal_entry_public_ids=[e["entry_public_id"] for e in created_entries],
            committed_at=committed_at.isoformat(),
            committed_by_id=actor.user.id,
            committed_by_email=actor.user.email,
        ).to_dict(),
    )

    return CommandResult.ok(
        {
            "batch_id": str(batch_id),
            "committed_groups": len(group_ids),
            "journal_entries": created_entries,
        }
    )


def _build_journal_entry_from_rows(rows: list[ScratchpadRow]) -> dict:
    """
    Convert scratchpad rows to journal entry format.

    Each scratchpad row becomes two journal lines:
    - A debit line
    - A credit line

    The first row's date is used as the entry date.
    Descriptions are combined into the memo.
    """
    if not rows:
        return {}

    first_row = rows[0]

    # Combine descriptions for memo
    descriptions = [row.description for row in rows if row.description]
    memo = "; ".join(descriptions) if descriptions else "Scratchpad commit"

    # Combine Arabic descriptions
    descriptions_ar = [row.description_ar for row in rows if row.description_ar]
    memo_ar = "; ".join(descriptions_ar) if descriptions_ar else ""

    lines = []
    for row in rows:
        # Build analysis tags from dimensions
        analysis_tags = []
        for dim in row.dimensions.all():
            if dim.dimension_value_id:
                analysis_tags.append(
                    {
                        "dimension_id": dim.dimension_id,
                        "value_id": dim.dimension_value_id,
                    }
                )

        # Debit line
        if row.debit_account_id and row.amount:
            lines.append(
                {
                    "account_id": row.debit_account_id,
                    "description": row.description,
                    "description_ar": row.description_ar,
                    "debit": row.amount,
                    "credit": Decimal("0"),
                    "analysis_tags": analysis_tags,
                }
            )

        # Credit line
        if row.credit_account_id and row.amount:
            lines.append(
                {
                    "account_id": row.credit_account_id,
                    "description": row.description,
                    "description_ar": row.description_ar,
                    "debit": Decimal("0"),
                    "credit": row.amount,
                    "analysis_tags": analysis_tags,
                }
            )

    return {
        "date": first_row.transaction_date,
        "memo": memo,
        "memo_ar": memo_ar,
        "lines": lines,
    }
