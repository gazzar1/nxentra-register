# accounting/commands.py
"""
Command layer for accounting operations.

Commands are the single point where business operations happen.
Views call commands; commands enforce rules and emit events.

Pattern:
1. Validate permissions (require)
2. Apply business policies (can_*)
3. Perform the operation (model changes)
4. Emit event (emit_event)
5. Return CommandResult

ALL state changes MUST go through commands to ensure events are emitted.
"""

from django.db import transaction
from django.utils import timezone
import hashlib
import json
import uuid
from decimal import Decimal

from accounts.authz import ActorContext, require
from accounting.models import (
    Account,
    JournalEntry,
    JournalLine,
    AnalysisDimension,
    AnalysisDimensionValue,
    AccountAnalysisDefault,
)
from accounting.aggregates import load_journal_entry_aggregate, load_account_aggregate
from accounting.policies import (
    can_edit_entry,
    can_delete_entry,
    can_delete_account,
    can_change_account_code,
    can_change_account_type,
    can_delete_dimension,
    can_delete_dimension_value,
    can_post_to_account,
)
from events.emitter import emit_event
from events.types import (
    EventTypes,
    AccountCreatedData,
    AccountUpdatedData,
    AccountDeletedData,
    JournalEntryCreatedData,
    JournalEntryUpdatedData,
    JournalEntryPostedData,
    JournalEntryReversedData,
    JournalEntrySavedCompleteData,
    JournalEntryDeletedData,
    JournalLineData,
    AnalysisDimensionCreatedData,
    AnalysisDimensionUpdatedData,
    AnalysisDimensionDeletedData,
    AnalysisDimensionValueCreatedData,
    AnalysisDimensionValueUpdatedData,
    AnalysisDimensionValueDeletedData,
    AccountAnalysisDefaultSetData,
    AccountAnalysisDefaultRemovedData,
    JournalLineAnalysisSetData,
)


class CommandResult:
    """
    Wrapper for command results with success/failure info.
    
    Usage:
        result = create_account(actor, code="1000", ...)
        if result.success:
            account = result.data
            event = result.event
        else:
            error_message = result.error
    """
    
    def __init__(self, success: bool, data=None, error: str = None, event=None):
        self.success = success
        self.data = data
        self.error = error
        self.event = event  # The emitted event, if any

    @classmethod
    def ok(cls, data=None, event=None):
        return cls(success=True, data=data, event=event)

    @classmethod
    def fail(cls, error: str):
        return cls(success=False, error=error)


def _changes_hash(changes: dict) -> str:
    payload = str(sorted((k, v.get("new")) for k, v in changes.items())).encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def _idempotency_hash(prefix: str, payload: dict) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(normalized).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _process_projections(company, exclude: set[str] | None = None) -> None:
    from projections.base import projection_registry
    exclude = exclude or set()
    for projection in projection_registry.all():
        if projection.name in exclude:
            continue
        projection.process_pending(company, limit=1000)


# =============================================================================
# Account Commands
# =============================================================================

@transaction.atomic
def create_account(
    actor: ActorContext,
    code: str,
    name: str,
    account_type: str,
    parent_id: int = None,
    is_header: bool = False,
    name_ar: str = "",
    description: str = "",
    description_ar: str = "",
    unit_of_measure: str = "",
) -> CommandResult:
    """
    Create a new account in the chart of accounts.
    
    Args:
        actor: The actor context (user + company)
        code: Account code (unique per company)
        name: Account name (English)
        account_type: One of Account.AccountType choices
        parent_id: Optional parent account ID (must be a header)
        is_header: True if this is a grouping account
        name_ar: Arabic name (optional)
        description: Description (English)
        description_ar: Arabic description
        unit_of_measure: Unit for MEMO accounts only
    
    Returns:
        CommandResult with the created Account or error
    """
    require(actor, "accounts.manage")

    # Check for duplicate code
    if Account.objects.filter(company=actor.company, code=code).exists():
        return CommandResult.fail(f"Account code '{code}' already exists.")

    # Validate parent if provided
    parent = None
    if parent_id:
        try:
            parent = Account.objects.get(pk=parent_id, company=actor.company)
        except Account.DoesNotExist:
            return CommandResult.fail("Parent account not found.")
        if not parent.is_header:
            return CommandResult.fail("Parent account must be a header account.")

    # Validate unit_of_measure only for MEMO accounts
    if unit_of_measure and account_type != Account.AccountType.MEMO:
        return CommandResult.fail("Unit of measure can only be set for MEMO accounts.")

    account_public_id = uuid.uuid4()
    normal_balance = Account.NORMAL_BALANCE_MAP.get(
        account_type,
        Account.NormalBalance.DEBIT,
    )

    idempotency_key = _idempotency_hash("account.created", {
        "company_public_id": str(actor.company.public_id),
        "code": code,
        "name": name,
        "name_ar": name_ar,
        "account_type": account_type,
        "is_header": is_header,
        "parent_public_id": str(parent.public_id) if parent else None,
        "description": description,
        "description_ar": description_ar,
        "unit_of_measure": unit_of_measure,
    })

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.ACCOUNT_CREATED,
        aggregate_type="Account",
        aggregate_id=str(account_public_id),
        idempotency_key=idempotency_key,
        data=AccountCreatedData(
            account_public_id=str(account_public_id),
            code=code,
            name=name,
            account_type=account_type,
            normal_balance=normal_balance,
            is_header=is_header,
            parent_public_id=str(parent.public_id) if parent else None,
            name_ar=name_ar,
            description=description,
            description_ar=description_ar,
            unit_of_measure=unit_of_measure,
        ).to_dict(),
    )

    _process_projections(actor.company)
    account = Account.objects.get(company=actor.company, public_id=account_public_id)
    return CommandResult.ok(account, event=event)


@transaction.atomic
def update_account(
    actor: ActorContext,
    account_id: int,
    **updates,
) -> CommandResult:
    """
    Update an existing account.
    
    Args:
        actor: The actor context
        account_id: ID of account to update
        **updates: Field updates (name, name_ar, description, etc.)
    
    Returns:
        CommandResult with updated Account or error
    """
    require(actor, "accounts.manage")

    try:
        account = Account.objects.select_for_update().get(
            pk=account_id, company=actor.company
        )
    except Account.DoesNotExist:
        return CommandResult.fail("Account not found.")

    # Policy checks for specific field changes
    if "code" in updates and updates["code"] != account.code:
        allowed, reason = can_change_account_code(actor, account)
        if not allowed:
            return CommandResult.fail(reason)
        if Account.objects.filter(
            company=actor.company,
            code=updates["code"],
        ).exclude(pk=account.id).exists():
            return CommandResult.fail(f"Account code '{updates['code']}' already exists.")

    if "account_type" in updates and updates["account_type"] != account.account_type:
        allowed, reason = can_change_account_type(actor, account)
        if not allowed:
            return CommandResult.fail(reason)

    # Validate unit_of_measure changes
    new_type = updates.get("account_type", account.account_type)
    new_uom = updates.get("unit_of_measure", account.unit_of_measure)
    if new_uom and new_type != Account.AccountType.MEMO:
        return CommandResult.fail("Unit of measure can only be set for MEMO accounts.")

    aggregate = load_account_aggregate(actor.company, str(account.public_id))
    if not aggregate or aggregate.deleted:
        return CommandResult.fail("Account not found.")

    # Track changes for event
    changes = {}
    allowed_fields = {
        "name", "name_ar", "description", "description_ar",
        "status", "code", "account_type", "unit_of_measure"
    }
    
    for field, value in updates.items():
        if field in allowed_fields:
            old_value = getattr(aggregate, field)
            if old_value != value:
                changes[field] = {"old": old_value, "new": value}

    if not changes:
        return CommandResult.ok(account)  # No changes, no event

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.ACCOUNT_UPDATED,
        aggregate_type="Account",
        aggregate_id=str(account.public_id),
        idempotency_key=f"account.updated:{account.public_id}:{_changes_hash(changes)}",
        data=AccountUpdatedData(
            account_public_id=str(account.public_id),
            changes=changes,
        ).to_dict(),
    )

    _process_projections(actor.company)
    account = Account.objects.get(company=actor.company, public_id=account.public_id)
    return CommandResult.ok(account, event=event)


@transaction.atomic
def delete_account(actor: ActorContext, account_id: int) -> CommandResult:
    """
    Delete an account.
    
    Args:
        actor: The actor context
        account_id: ID of account to delete
    
    Returns:
        CommandResult with deletion confirmation or error
    """
    require(actor, "accounts.manage")

    try:
        account = Account.objects.select_for_update().get(
            pk=account_id, company=actor.company
        )
    except Account.DoesNotExist:
        return CommandResult.fail("Account not found.")

    allowed, reason = can_delete_account(actor, account)
    if not allowed:
        return CommandResult.fail(reason)

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.ACCOUNT_DELETED,
        aggregate_type="Account",
        aggregate_id=str(account.public_id),
        idempotency_key=f"account.deleted:{account.public_id}",
        data=AccountDeletedData(
            account_public_id=str(account.public_id),
            code=account.code,
            name=account.name,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok({"deleted": True}, event=event)


# =============================================================================
# Journal Entry Commands
# =============================================================================

@transaction.atomic
def create_journal_entry(
    actor: ActorContext,
    date,
    memo: str = "",
    memo_ar: str = "",
    lines: list = None,
    kind: str = JournalEntry.Kind.NORMAL,
) -> CommandResult:
    """
    Create a new journal entry.
    
    Args:
        actor: The actor context
        date: Entry date
        memo: Memo/description (English)
        memo_ar: Arabic memo
        lines: List of line dicts with account_id, description, debit, credit
        kind: Entry kind (NORMAL, OPENING, ADJUSTMENT, etc.)
    
    Returns:
        CommandResult with created JournalEntry or error
    """
    require(actor, "journal.create")

    entry_public_id = uuid.uuid4()
    line_data = []
    if lines:
        account_ids = [line.get("account_id") for line in lines if line.get("account_id")]
        accounts = {
            acc.id: acc
            for acc in Account.objects.filter(company=actor.company, id__in=account_ids)
        }

        line_no = 1
        for line in lines:
            account_id = line.get("account_id")
            if account_id not in accounts:
                return CommandResult.fail(f"Account {account_id} not found.")

            account = accounts[account_id]
            debit = line.get("debit", 0)
            credit = line.get("credit", 0)
            if debit == 0 and credit == 0:
                continue
            line_data.append(JournalLineData(
                line_no=line_no,
                account_public_id=str(account.public_id),
                account_code=account.code,
                description=line.get("description", ""),
                description_ar=line.get("description_ar", ""),
                debit=str(debit),
                credit=str(credit),
                is_memo_line=account.is_memo_account,
                analysis_tags=[],
            ).to_dict())
            line_no += 1

    normalized_lines = []
    if lines:
        for idx, line in enumerate(lines, start=1):
            debit = line.get("debit", 0)
            credit = line.get("credit", 0)
            if debit == 0 and credit == 0:
                continue
            normalized_lines.append({
                "line_no": idx,
                "account_id": line.get("account_id"),
                "description": line.get("description", ""),
                "description_ar": line.get("description_ar", ""),
                "debit": str(debit),
                "credit": str(credit),
            })

    idempotency_key = _idempotency_hash("journal_entry.created", {
        "company_public_id": str(actor.company.public_id),
        "date": date.isoformat() if hasattr(date, "isoformat") else str(date),
        "memo": memo,
        "memo_ar": memo_ar,
        "kind": kind,
        "lines": normalized_lines,
    })

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.JOURNAL_ENTRY_CREATED,
        aggregate_type="JournalEntry",
        aggregate_id=str(entry_public_id),
        idempotency_key=idempotency_key,
        data=JournalEntryCreatedData(
            entry_public_id=str(entry_public_id),
            date=date.isoformat() if hasattr(date, "isoformat") else str(date),
            memo=memo,
            memo_ar=memo_ar,
            kind=kind,
            status=JournalEntry.Status.INCOMPLETE,
            created_by_id=actor.user.id,
            lines=line_data,
        ).to_dict(),
    )

    _process_projections(actor.company)
    entry = JournalEntry.objects.get(company=actor.company, public_id=entry_public_id)
    return CommandResult.ok(entry, event=event)


@transaction.atomic
def update_journal_entry(
    actor: ActorContext,
    entry_id: int,
    date=None,
    memo: str = None,
    memo_ar: str = None,
    lines: list = None,
) -> CommandResult:
    """
    Update a journal entry (autosave mode - status becomes INCOMPLETE).
    
    Args:
        actor: The actor context
        entry_id: ID of entry to update
        date: New date (optional)
        memo: New memo (optional)
        memo_ar: New Arabic memo (optional)
        lines: New lines (optional, replaces all existing lines)
    
    Returns:
        CommandResult with updated JournalEntry or error
    """
    require(actor, "journal.edit_draft")

    try:
        entry = JournalEntry.objects.select_for_update().get(
            pk=entry_id, company=actor.company
        )
    except JournalEntry.DoesNotExist:
        return CommandResult.fail("Journal entry not found.")

    allowed, reason = can_edit_entry(actor, entry)
    if not allowed:
        return CommandResult.fail(reason)

    aggregate = load_journal_entry_aggregate(actor.company, str(entry.public_id))
    if not aggregate or aggregate.deleted:
        return CommandResult.fail("Journal entry not found.")

    # Track changes
    changes = {}
    
    if date is not None and entry.date != date:
        changes["date"] = {"old": entry.date.isoformat(), "new": date.isoformat() if hasattr(date, 'isoformat') else str(date)}
    
    if memo is not None and entry.memo != memo:
        changes["memo"] = {"old": entry.memo, "new": memo}
    
    if memo_ar is not None and entry.memo_ar != memo_ar:
        changes["memo_ar"] = {"old": entry.memo_ar, "new": memo_ar}

    # Update lines if provided
    line_data = None
    if lines is not None:
        changes["lines"] = {"old": "replaced", "new": f"{len(lines)} lines"}
        line_data = []

        account_ids = [
            line.get("account_id") or line.get("account")
            for line in lines
            if line.get("account_id") or line.get("account")
        ]
        accounts = {
            acc.id: acc
            for acc in Account.objects.filter(company=actor.company, id__in=account_ids)
        }

        line_no = 1
        for line in lines:
            debit = line.get("debit", 0)
            credit = line.get("credit", 0)

            # Skip placeholder lines (0/0)
            if debit == 0 and credit == 0:
                continue

            account_id = line.get("account_id") or line.get("account")
            if account_id not in accounts:
                return CommandResult.fail(f"Account {account_id} not found.")

            account = accounts[account_id]
            line_data.append(JournalLineData(
                line_no=line_no,
                account_public_id=str(account.public_id),
                account_code=account.code,
                description=line.get("description", ""),
                description_ar=line.get("description_ar", ""),
                debit=str(debit),
                credit=str(credit),
                is_memo_line=account.is_memo_account,
                analysis_tags=[],
            ).to_dict())
            line_no += 1

    # Any edit sets status back to INCOMPLETE
    # Only emit event if there were changes
    event = None
    if changes:
        event = emit_event(
            actor=actor,
            event_type=EventTypes.JOURNAL_ENTRY_UPDATED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry.public_id),
            idempotency_key=f"journal_entry.updated:{entry.public_id}:{_changes_hash(changes)}",
            data=JournalEntryUpdatedData(
                entry_public_id=str(entry.public_id),
                changes=changes,
                lines=line_data,
            ).to_dict(),
        )

    if event:
        _process_projections(actor.company)
        entry = JournalEntry.objects.get(company=actor.company, public_id=entry.public_id)
    return CommandResult.ok(entry, event=event)


@transaction.atomic
def save_journal_entry_complete(
    actor: ActorContext,
    entry_id: int,
    date=None,
    memo: str = None,
    memo_ar: str = None,
    lines: list = None,
) -> CommandResult:
    """
    Save a journal entry as complete (DRAFT status).
    Validates that entry is balanced and has at least 2 lines.
    
    Args:
        actor: The actor context
        entry_id: ID of entry to save as complete
        date: New date (optional)
        memo: New memo (optional)
        memo_ar: New Arabic memo (optional)
        lines: New lines (optional)
    
    Returns:
        CommandResult with saved JournalEntry or error
    """
    require(actor, "journal.edit_draft")

    try:
        entry = JournalEntry.objects.select_for_update().get(
            pk=entry_id, company=actor.company
        )
    except JournalEntry.DoesNotExist:
        return CommandResult.fail("Journal entry not found.")

    allowed, reason = can_edit_entry(actor, entry)
    if not allowed:
        return CommandResult.fail(reason)

    line_data = None
    if lines is not None:
        line_data = []
        account_ids = [
            line.get("account_id") or line.get("account")
            for line in lines
            if line.get("account_id") or line.get("account")
        ]
        accounts = {
            acc.id: acc
            for acc in Account.objects.filter(company=actor.company, id__in=account_ids)
        }

        line_no = 1
        for line in lines:
            debit = line.get("debit", 0)
            credit = line.get("credit", 0)

            if debit == 0 and credit == 0:
                continue

            account_id = line.get("account_id") or line.get("account")
            if account_id not in accounts:
                return CommandResult.fail(f"Account {account_id} not found.")

            account = accounts[account_id]
            line_data.append(JournalLineData(
                line_no=line_no,
                account_public_id=str(account.public_id),
                account_code=account.code,
                description=line.get("description", ""),
                description_ar=line.get("description_ar", ""),
                debit=str(debit),
                credit=str(credit),
                is_memo_line=account.is_memo_account,
                analysis_tags=[],
            ).to_dict())
            line_no += 1
            
    aggregate = load_journal_entry_aggregate(actor.company, str(entry.public_id))
    if not aggregate or aggregate.deleted:
        return CommandResult.fail("Journal entry not found.")

    # Validate for DRAFT status (use provided lines or aggregate)
    if line_data is not None:
        line_count = len(line_data)
        total_debit = sum(Decimal(l["debit"]) for l in line_data)
        total_credit = sum(Decimal(l["credit"]) for l in line_data)
    else:
        line_count = len(aggregate.lines)
        total_debit = aggregate.total_debit
        total_credit = aggregate.total_credit

    if line_count < 2:
        return CommandResult.fail("Entry must have at least 2 lines to be complete.")

    if total_debit != total_credit:
        return CommandResult.fail(
            f"Entry is not balanced. Debit={total_debit} Credit={total_credit}"
        )

    # Emit event
    lines_payload = line_data if line_data is not None else aggregate.lines
    payload = {
        "date": (date or entry.date).isoformat() if hasattr((date or entry.date), "isoformat") else str(date or entry.date),
        "memo": memo if memo is not None else entry.memo,
        "memo_ar": memo_ar if memo_ar is not None else entry.memo_ar,
        "lines": lines_payload,
    }
    digest = hashlib.sha256(str(payload).encode()).hexdigest()[:12]
    event = emit_event(
        actor=actor,
        event_type=EventTypes.JOURNAL_ENTRY_SAVED_COMPLETE,
        aggregate_type="JournalEntry",
        aggregate_id=str(entry.public_id),
        idempotency_key=f"journal_entry.saved_complete:{entry.public_id}:{digest}",
        data=JournalEntrySavedCompleteData(
            entry_public_id=str(entry.public_id),
            date=(date or entry.date).isoformat() if hasattr((date or entry.date), "isoformat") else str(date or entry.date),
            memo=memo if memo is not None else entry.memo,
            memo_ar=memo_ar if memo_ar is not None else entry.memo_ar,
            status=JournalEntry.Status.DRAFT,
            line_count=line_count,
            total_debit=str(total_debit),
            total_credit=str(total_credit),
            lines=lines_payload,
        ).to_dict(),
    )

    _process_projections(actor.company)
    entry = JournalEntry.objects.get(company=actor.company, public_id=entry.public_id)
    return CommandResult.ok(entry, event=event)


@transaction.atomic
def post_journal_entry(actor: ActorContext, entry_id: int) -> CommandResult:
    """
    Post a journal entry, making it affect account balances.
    
    Args:
        actor: The actor context
        entry_id: ID of entry to post
    
    Returns:
        CommandResult with posted JournalEntry or error
    """
    require(actor, "journal.post")

    try:
        entry = JournalEntry.objects.select_for_update().get(
            pk=entry_id, company=actor.company
        )
    except JournalEntry.DoesNotExist:
        return CommandResult.fail("Journal entry not found.")

    aggregate = load_journal_entry_aggregate(actor.company, str(entry.public_id))
    if not aggregate or aggregate.deleted:
        return CommandResult.fail("Journal entry not found.")

    if aggregate.status != JournalEntry.Status.DRAFT:
        return CommandResult.fail("Only DRAFT entries can be posted.")

    postable_kinds = [JournalEntry.Kind.NORMAL, JournalEntry.Kind.OPENING, JournalEntry.Kind.ADJUSTMENT]
    if aggregate.kind not in postable_kinds:
        return CommandResult.fail(f"Cannot post {aggregate.kind} entries.")

    if len(aggregate.lines) < 2:
        return CommandResult.fail("Entry must have at least 2 lines to be posted.")

    if aggregate.total_debit != aggregate.total_credit:
        return CommandResult.fail(
            f"Entry is not balanced. Debit={aggregate.total_debit} Credit={aggregate.total_credit}"
        )

    posted_at = timezone.now()

    last_num = JournalEntry.objects.filter(
        company=entry.company,
        status=JournalEntry.Status.POSTED,
    ).count()
    entry_number = f"JE-{entry.company_id}-{last_num + 1:06d}"

    # Build line data for event (including analysis tags)
    line_data = []
    for line in aggregate.lines:
        account_public_id = line.get("account_public_id")
        account = Account.objects.filter(
            company=actor.company,
            public_id=account_public_id,
        ).first()
        if not account:
            return CommandResult.fail(f"Account {account_public_id} not found.")

        allowed, reason = can_post_to_account(account)
        if not allowed:
            return CommandResult.fail(reason)

        line_data.append(JournalLineData(
            line_no=line.get("line_no"),
            account_public_id=str(account.public_id),
            account_code=account.code,
            description=line.get("description", ""),
            description_ar=line.get("description_ar", ""),
            debit=str(line.get("debit", "0")),
            credit=str(line.get("credit", "0")),
            is_memo_line=account.is_memo_account,
            analysis_tags=line.get("analysis_tags", []),
        ).to_dict())

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=str(entry.public_id),
        idempotency_key=f"journal_entry.posted:{entry.public_id}",
        data=JournalEntryPostedData(
            entry_public_id=str(entry.public_id),
            entry_number=entry_number,
            date=aggregate.date or entry.date.isoformat(),
            memo=aggregate.memo,
            memo_ar=aggregate.memo_ar,
            kind=aggregate.kind,
            posted_at=posted_at.isoformat(),
            posted_by_id=actor.user.id,
            posted_by_email=actor.user.email,
            total_debit=str(aggregate.total_debit),
            total_credit=str(aggregate.total_credit),
            lines=line_data,
        ).to_dict(),
    )

    _process_projections(actor.company, exclude={"account_balance"})
    posted_entry = JournalEntry.objects.get(company=actor.company, public_id=entry.public_id)
    return CommandResult.ok(posted_entry, event=event)


@transaction.atomic
def reverse_journal_entry(actor: ActorContext, entry_id: int) -> CommandResult:
    """
    Reverse a posted journal entry.
    
    Creates a new reversal entry with swapped debit/credit amounts
    and marks the original entry as REVERSED.
    
    IMPORTANT: Emits TWO events:
    1. JOURNAL_ENTRY_POSTED for the reversal entry (so projections update balances)
    2. JOURNAL_ENTRY_REVERSED for audit trail
    
    Args:
        actor: The actor context
        entry_id: ID of entry to reverse
    
    Returns:
        CommandResult with {"original": entry, "reversal": reversal_entry} or error
    """
    require(actor, "journal.reverse")

    try:
        original = JournalEntry.objects.select_for_update().get(
            pk=entry_id, company=actor.company
        )
    except JournalEntry.DoesNotExist:
        return CommandResult.fail("Journal entry not found.")

    aggregate = load_journal_entry_aggregate(actor.company, str(original.public_id))
    if not aggregate or aggregate.deleted:
        return CommandResult.fail("Journal entry not found.")

    if aggregate.status != JournalEntry.Status.POSTED:
        return CommandResult.fail("Only POSTED entries can be reversed.")

    if aggregate.kind != JournalEntry.Kind.NORMAL:
        return CommandResult.fail("Only NORMAL entries can be reversed.")

    if aggregate.reversed:
        return CommandResult.fail("This entry was already reversed.")

    reversal_public_id = uuid.uuid4()
    posted_at = timezone.now()

    last_num = JournalEntry.objects.filter(
        company=original.company,
        status=JournalEntry.Status.POSTED,
    ).count()
    reversal_entry_number = f"JE-{original.company_id}-{last_num + 1:06d}"

    reversal_line_data = []
    for line in aggregate.lines:
        account_public_id = line.get("account_public_id")
        account = Account.objects.filter(
            company=actor.company,
            public_id=account_public_id,
        ).first()
        if not account:
            return CommandResult.fail(f"Account {account_public_id} not found.")

        analysis_tags = line.get("analysis_tags", [])
        reversal_line_data.append(JournalLineData(
            line_no=line.get("line_no"),
            account_public_id=str(account.public_id),
            account_code=account.code,
            description=f"Reversal: {line.get('description', '')}".strip(),
            description_ar=f"عكس: {line.get('description_ar', '')}".strip() if line.get("description_ar") else "",
            debit=str(line.get("credit", "0")),
            credit=str(line.get("debit", "0")),
            is_memo_line=account.is_memo_account,
            analysis_tags=analysis_tags,
        ).to_dict())

    event_posted = emit_event(
        actor=actor,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=str(reversal_public_id),
        idempotency_key=f"journal_entry.reversal.posted:{original.public_id}",
        data=JournalEntryPostedData(
            entry_public_id=str(reversal_public_id),
            entry_number=reversal_entry_number,
            date=aggregate.date or original.date.isoformat(),
            memo=f"Reversal of JE#{original.id}: {aggregate.memo}",
            memo_ar=f"عكس قيد #{original.id}: {aggregate.memo_ar}" if aggregate.memo_ar else "",
            kind=JournalEntry.Kind.REVERSAL,
            posted_at=posted_at.isoformat(),
            posted_by_id=actor.user.id,
            posted_by_email=actor.user.email,
            total_debit=str(aggregate.total_credit),
            total_credit=str(aggregate.total_debit),
            lines=reversal_line_data,
        ).to_dict(),
    )

    # Emit REVERSED event for audit trail (links original to reversal)
    event_reversed = emit_event(
        actor=actor,
        event_type=EventTypes.JOURNAL_ENTRY_REVERSED,
        aggregate_type="JournalEntry",
        aggregate_id=str(original.public_id),
        idempotency_key=f"journal_entry.reversed:{original.public_id}",
        data=JournalEntryReversedData(
            original_entry_public_id=str(original.public_id),
            reversal_entry_public_id=str(event_posted.data.get("entry_public_id", reversal_public_id)),
            reversed_at=posted_at.isoformat(),
            reversed_by_id=actor.user.id,
            reversed_by_email=actor.user.email,
        ).to_dict(),
    )

    _process_projections(actor.company, exclude={"account_balance"})
    original = JournalEntry.objects.get(company=actor.company, public_id=original.public_id)
    reversal_public_id = event_posted.data.get("entry_public_id", reversal_public_id)
    reversal = JournalEntry.objects.get(company=actor.company, public_id=reversal_public_id)
    return CommandResult.ok({
        "original": original,
        "reversal": reversal,
    }, event=event_reversed)

@transaction.atomic
def delete_journal_entry(actor: ActorContext, entry_id: int) -> CommandResult:
    """
    Delete a journal entry (only INCOMPLETE or DRAFT entries can be deleted).
    
    Args:
        actor: The actor context
        entry_id: ID of entry to delete
    
    Returns:
        CommandResult with deletion confirmation or error
    """
    require(actor, "journal.edit_draft")

    try:
        entry = JournalEntry.objects.select_for_update().get(
            pk=entry_id, company=actor.company
        )
    except JournalEntry.DoesNotExist:
        return CommandResult.fail("Journal entry not found.")

    allowed, reason = can_delete_entry(actor, entry)
    if not allowed:
        return CommandResult.fail(reason)

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.JOURNAL_ENTRY_DELETED,
        aggregate_type="JournalEntry",
        aggregate_id=str(entry.public_id),
        idempotency_key=f"journal_entry.deleted:{entry.public_id}",
        data=JournalEntryDeletedData(
            entry_public_id=str(entry.public_id),
            date=entry.date.isoformat(),
            memo=entry.memo,
            status=entry.status,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok({"deleted": True}, event=event)


# =============================================================================
# Analysis Dimension Commands
# =============================================================================

@transaction.atomic
def create_analysis_dimension(
    actor: ActorContext,
    code: str,
    name: str,
    name_ar: str = "",
    description: str = "",
    description_ar: str = "",
    is_required_on_posting: bool = False,
    applies_to_account_types: list = None,
    display_order: int = 0,
) -> CommandResult:
    """
    Create a new analysis dimension.
    
    Args:
        actor: The actor context
        code: Dimension code (unique per company)
        name: Dimension name (English)
        name_ar: Arabic name
        description: Description
        description_ar: Arabic description
        is_required_on_posting: If True, must be filled when posting
        applies_to_account_types: List of account types, empty = all
        display_order: Order for UI display
    
    Returns:
        CommandResult with created AnalysisDimension or error
    """
    require(actor, "accounts.manage")

    # Check for duplicate code
    if AnalysisDimension.objects.filter(company=actor.company, code=code).exists():
        return CommandResult.fail(f"Dimension code '{code}' already exists.")

    dimension_public_id = uuid.uuid4()

    idempotency_key = _idempotency_hash("analysis_dimension.created", {
        "company_public_id": str(actor.company.public_id),
        "code": code,
        "name": name,
        "name_ar": name_ar,
        "description": description,
        "description_ar": description_ar,
        "is_required_on_posting": is_required_on_posting,
        "applies_to_account_types": applies_to_account_types or [],
        "display_order": display_order,
    })

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.ANALYSIS_DIMENSION_CREATED,
        aggregate_type="AnalysisDimension",
        aggregate_id=str(dimension_public_id),
        idempotency_key=idempotency_key,
        data=AnalysisDimensionCreatedData(
            dimension_public_id=str(dimension_public_id),
            code=code,
            name=name,
            name_ar=name_ar,
            description=description,
            description_ar=description_ar,
            is_required_on_posting=is_required_on_posting,
            applies_to_account_types=applies_to_account_types or [],
            display_order=display_order,
        ).to_dict(),
    )

    _process_projections(actor.company)
    dimension = AnalysisDimension.objects.get(company=actor.company, public_id=dimension_public_id)
    return CommandResult.ok(dimension, event=event)


@transaction.atomic
def update_analysis_dimension(
    actor: ActorContext,
    dimension_id: int,
    **updates,
) -> CommandResult:
    """
    Update an analysis dimension.
    
    Args:
        actor: The actor context
        dimension_id: ID of dimension to update
        **updates: Field updates
    
    Returns:
        CommandResult with updated AnalysisDimension or error
    """
    require(actor, "accounts.manage")

    try:
        dimension = AnalysisDimension.objects.select_for_update().get(
            pk=dimension_id, company=actor.company
        )
    except AnalysisDimension.DoesNotExist:
        return CommandResult.fail("Dimension not found.")

    # Track changes
    changes = {}
    allowed_fields = {
        "name", "name_ar", "description", "description_ar",
        "is_required_on_posting", "applies_to_account_types",
        "display_order", "is_active"
    }
    
    for field, value in updates.items():
        if field in allowed_fields:
            old_value = getattr(dimension, field)
            if old_value != value:
                changes[field] = {"old": old_value, "new": value}
                setattr(dimension, field, value)

    if not changes:
        return CommandResult.ok(dimension)

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.ANALYSIS_DIMENSION_UPDATED,
        aggregate_type="AnalysisDimension",
        aggregate_id=str(dimension.public_id),
        idempotency_key=f"analysis_dimension.updated:{dimension.public_id}:{_changes_hash(changes)}",
        data=AnalysisDimensionUpdatedData(
            dimension_public_id=str(dimension.public_id),
            changes=changes,
        ).to_dict(),
    )

    _process_projections(actor.company)
    dimension = AnalysisDimension.objects.get(company=actor.company, public_id=dimension.public_id)
    return CommandResult.ok(dimension, event=event)


@transaction.atomic
def delete_analysis_dimension(actor: ActorContext, dimension_id: int) -> CommandResult:
    """
    Delete an analysis dimension.
    
    Args:
        actor: The actor context
        dimension_id: ID of dimension to delete
    
    Returns:
        CommandResult with deletion confirmation or error
    """
    require(actor, "accounts.manage")

    try:
        dimension = AnalysisDimension.objects.select_for_update().get(
            pk=dimension_id, company=actor.company
        )
    except AnalysisDimension.DoesNotExist:
        return CommandResult.fail("Dimension not found.")

    allowed, reason = can_delete_dimension(actor, dimension)
    if not allowed:
        return CommandResult.fail(reason)

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.ANALYSIS_DIMENSION_DELETED,
        aggregate_type="AnalysisDimension",
        aggregate_id=str(dimension.public_id),
        idempotency_key=f"analysis_dimension.deleted:{dimension.public_id}",
        data=AnalysisDimensionDeletedData(
            dimension_public_id=str(dimension.public_id),
            code=dimension.code,
            name=dimension.name,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok({"deleted": True}, event=event)


# =============================================================================
# Analysis Dimension Value Commands
# =============================================================================

@transaction.atomic
def create_dimension_value(
    actor: ActorContext,
    dimension_id: int,
    code: str,
    name: str,
    name_ar: str = "",
    description: str = "",
    description_ar: str = "",
    parent_id: int = None,
) -> CommandResult:
    """
    Create a new value within an analysis dimension.
    
    Args:
        actor: The actor context
        dimension_id: Parent dimension ID
        code: Value code (unique within dimension)
        name: Value name (English)
        name_ar: Arabic name
        description: Description
        description_ar: Arabic description
        parent_id: Parent value ID for hierarchical dimensions
    
    Returns:
        CommandResult with created AnalysisDimensionValue or error
    """
    require(actor, "accounts.manage")

    try:
        dimension = AnalysisDimension.objects.get(
            pk=dimension_id, company=actor.company
        )
    except AnalysisDimension.DoesNotExist:
        return CommandResult.fail("Dimension not found.")

    # Check for duplicate code within dimension
    if AnalysisDimensionValue.objects.filter(dimension=dimension, code=code).exists():
        return CommandResult.fail(f"Value code '{code}' already exists in this dimension.")

    # Validate parent if provided
    parent = None
    if parent_id:
        try:
            parent = AnalysisDimensionValue.objects.get(
                pk=parent_id, dimension=dimension
            )
        except AnalysisDimensionValue.DoesNotExist:
            return CommandResult.fail("Parent value not found in this dimension.")

    value_public_id = uuid.uuid4()

    idempotency_key = _idempotency_hash("analysis_dimension_value.created", {
        "dimension_public_id": str(dimension.public_id),
        "code": code,
        "name": name,
        "name_ar": name_ar,
        "description": description,
        "description_ar": description_ar,
        "parent_public_id": str(parent.public_id) if parent else None,
    })

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.ANALYSIS_DIMENSION_VALUE_CREATED,
        aggregate_type="AnalysisDimensionValue",
        aggregate_id=str(value_public_id),
        idempotency_key=idempotency_key,
        data=AnalysisDimensionValueCreatedData(
            value_public_id=str(value_public_id),
            dimension_public_id=str(dimension.public_id),
            dimension_code=dimension.code,
            code=code,
            name=name,
            name_ar=name_ar,
            description=description,
            description_ar=description_ar,
            parent_public_id=str(parent.public_id) if parent else None,
        ).to_dict(),
    )

    _process_projections(actor.company)
    value = AnalysisDimensionValue.objects.get(dimension=dimension, public_id=value_public_id)
    return CommandResult.ok(value, event=event)


@transaction.atomic
def update_dimension_value(
    actor: ActorContext,
    value_id: int,
    **updates,
) -> CommandResult:
    """
    Update an analysis dimension value.
    
    Args:
        actor: The actor context
        value_id: ID of value to update
        **updates: Field updates
    
    Returns:
        CommandResult with updated AnalysisDimensionValue or error
    """
    require(actor, "accounts.manage")

    try:
        value = AnalysisDimensionValue.objects.select_for_update().select_related(
            "dimension"
        ).get(pk=value_id, dimension__company=actor.company)
    except AnalysisDimensionValue.DoesNotExist:
        return CommandResult.fail("Dimension value not found.")

    # Track changes
    changes = {}
    allowed_fields = {"name", "name_ar", "description", "description_ar", "is_active"}
    
    for field, value_new in updates.items():
        if field in allowed_fields:
            old_value = getattr(value, field)
            if old_value != value_new:
                changes[field] = {"old": old_value, "new": value_new}
                setattr(value, field, value_new)

    if not changes:
        return CommandResult.ok(value)

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.ANALYSIS_DIMENSION_VALUE_UPDATED,
        aggregate_type="AnalysisDimensionValue",
        aggregate_id=str(value.public_id),
        idempotency_key=f"analysis_dimension_value.updated:{value.public_id}:{_changes_hash(changes)}",
        data=AnalysisDimensionValueUpdatedData(
            value_public_id=str(value.public_id),
            dimension_public_id=str(value.dimension.public_id),
            changes=changes,
        ).to_dict(),
    )

    _process_projections(actor.company)
    value = AnalysisDimensionValue.objects.get(dimension=value.dimension, public_id=value.public_id)
    return CommandResult.ok(value, event=event)


@transaction.atomic
def delete_dimension_value(actor: ActorContext, value_id: int) -> CommandResult:
    """
    Delete an analysis dimension value.
    
    Args:
        actor: The actor context
        value_id: ID of value to delete
    
    Returns:
        CommandResult with deletion confirmation or error
    """
    require(actor, "accounts.manage")

    try:
        value = AnalysisDimensionValue.objects.select_for_update().select_related(
            "dimension"
        ).get(pk=value_id, dimension__company=actor.company)
    except AnalysisDimensionValue.DoesNotExist:
        return CommandResult.fail("Dimension value not found.")

    allowed, reason = can_delete_dimension_value(actor, value)
    if not allowed:
        return CommandResult.fail(reason)

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.ANALYSIS_DIMENSION_VALUE_DELETED,
        aggregate_type="AnalysisDimensionValue",
        aggregate_id=str(value.public_id),
        idempotency_key=f"analysis_dimension_value.deleted:{value.public_id}",
        data=AnalysisDimensionValueDeletedData(
            value_public_id=str(value.public_id),
            dimension_public_id=str(value.dimension.public_id),
            code=value.code,
            name=value.name,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok({"deleted": True}, event=event)


# =============================================================================
# Account Analysis Default Commands
# =============================================================================

@transaction.atomic
def set_account_analysis_default(
    actor: ActorContext,
    account_id: int,
    dimension_id: int,
    value_id: int,
) -> CommandResult:
    """
    Set or update a default analysis value for an account.
    
    Args:
        actor: The actor context
        account_id: Account ID
        dimension_id: Dimension ID
        value_id: Default value ID
    
    Returns:
        CommandResult with created/updated AccountAnalysisDefault or error
    """
    require(actor, "accounts.manage")

    try:
        account = Account.objects.get(pk=account_id, company=actor.company)
    except Account.DoesNotExist:
        return CommandResult.fail("Account not found.")

    try:
        dimension = AnalysisDimension.objects.get(pk=dimension_id, company=actor.company)
    except AnalysisDimension.DoesNotExist:
        return CommandResult.fail("Dimension not found.")

    try:
        value = AnalysisDimensionValue.objects.get(pk=value_id, dimension=dimension)
    except AnalysisDimensionValue.DoesNotExist:
        return CommandResult.fail("Dimension value not found.")

    # Check if dimension applies to this account type
    if not dimension.applies_to_account(account):
        return CommandResult.fail(
            f"Dimension '{dimension.code}' does not apply to account type '{account.account_type}'."
        )

    idempotency_key = _idempotency_hash("account_analysis_default.set", {
        "account_public_id": str(account.public_id),
        "dimension_public_id": str(dimension.public_id),
        "value_public_id": str(value.public_id),
    })

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.ACCOUNT_ANALYSIS_DEFAULT_SET,
        aggregate_type="AccountAnalysisDefault",
        aggregate_id=f"{account.public_id}:{dimension.public_id}",
        idempotency_key=idempotency_key,
        data=AccountAnalysisDefaultSetData(
            account_public_id=str(account.public_id),
            account_code=account.code,
            dimension_public_id=str(dimension.public_id),
            dimension_code=dimension.code,
            value_public_id=str(value.public_id),
            value_code=value.code,
        ).to_dict(),
    )

    _process_projections(actor.company)
    default = AccountAnalysisDefault.objects.get(account=account, dimension=dimension)
    return CommandResult.ok(default, event=event)


@transaction.atomic
def remove_account_analysis_default(
    actor: ActorContext,
    account_id: int,
    dimension_id: int,
) -> CommandResult:
    """
    Remove a default analysis value from an account.
    
    Args:
        actor: The actor context
        account_id: Account ID
        dimension_id: Dimension ID
    
    Returns:
        CommandResult with deletion confirmation or error
    """
    require(actor, "accounts.manage")

    try:
        account = Account.objects.get(pk=account_id, company=actor.company)
    except Account.DoesNotExist:
        return CommandResult.fail("Account not found.")

    try:
        dimension = AnalysisDimension.objects.get(pk=dimension_id, company=actor.company)
    except AnalysisDimension.DoesNotExist:
        return CommandResult.fail("Dimension not found.")

    try:
        default = AccountAnalysisDefault.objects.get(account=account, dimension=dimension)
    except AccountAnalysisDefault.DoesNotExist:
        return CommandResult.fail("No default set for this account and dimension.")

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.ACCOUNT_ANALYSIS_DEFAULT_REMOVED,
        aggregate_type="AccountAnalysisDefault",
        aggregate_id=f"{account.public_id}:{dimension.public_id}",
        idempotency_key=f"account_analysis_default.removed:{account.public_id}:{dimension.public_id}",
        data=AccountAnalysisDefaultRemovedData(
            account_public_id=str(account.public_id),
            account_code=account.code,
            dimension_public_id=str(dimension.public_id),
            dimension_code=dimension.code,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok({"deleted": True}, event=event)


# =============================================================================
# Journal Line Analysis Commands
# =============================================================================

@transaction.atomic
def set_journal_line_analysis(
    actor: ActorContext,
    line_id: int,
    analysis_tags: list,
) -> CommandResult:
    """
    Set analysis tags for a journal line.
    
    Args:
        actor: The actor context
        line_id: Journal line ID
        analysis_tags: List of {"dimension_id": int, "value_id": int}
    
    Returns:
        CommandResult with the line or error
    """
    require(actor, "journal.edit_draft")

    try:
        line = JournalLine.objects.select_for_update().select_related(
            "entry"
        ).get(pk=line_id, entry__company=actor.company)
    except JournalLine.DoesNotExist:
        return CommandResult.fail("Journal line not found.")

    # Check entry is editable
    if line.entry.status not in [JournalEntry.Status.INCOMPLETE, JournalEntry.Status.DRAFT]:
        return CommandResult.fail("Cannot modify analysis on posted/reversed entries.")

    tag_data = []
    for tag in analysis_tags:
        dimension_id = tag.get("dimension_id")
        value_id = tag.get("value_id")
        
        try:
            dimension = AnalysisDimension.objects.get(
                pk=dimension_id, company=actor.company
            )
        except AnalysisDimension.DoesNotExist:
            return CommandResult.fail(f"Dimension {dimension_id} not found.")

        try:
            value = AnalysisDimensionValue.objects.get(
                pk=value_id, dimension=dimension
            )
        except AnalysisDimensionValue.DoesNotExist:
            return CommandResult.fail(
                f"Value {value_id} not found in dimension {dimension.code}."
            )

        tag_data.append({
            "dimension_public_id": str(dimension.public_id),
            "dimension_code": dimension.code,
            "value_public_id": str(value.public_id),
            "value_code": value.code,
        })

    event = emit_event(
        actor=actor,
        event_type=EventTypes.JOURNAL_LINE_ANALYSIS_SET,
        aggregate_type="JournalLine",
        aggregate_id=str(line.public_id),
        idempotency_key=f"journal_line.analysis_set:{line.public_id}:{_changes_hash({'tags': {'new': tag_data}})}",
        data=JournalLineAnalysisSetData(
            entry_public_id=str(line.entry.public_id),
            line_no=line.line_no,
            analysis_tags=tag_data,
        ).to_dict(),
    )

    _process_projections(actor.company)
    line = JournalLine.objects.get(entry=line.entry, line_no=line.line_no)
    return CommandResult.ok(line, event=event)
