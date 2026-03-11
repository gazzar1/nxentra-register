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

from django.db import transaction, IntegrityError
from django.conf import settings
from django.utils import timezone
import hashlib
import json
import logging
import uuid
from decimal import Decimal

logger = logging.getLogger("nxentra.accounting.commands")

from accounts.authz import ActorContext, require
from accounts.rls import rls_bypass
from accounting.models import (
    Account,
    JournalEntry,
    JournalLine,
    AnalysisDimension,
    AnalysisDimensionValue,
    AccountAnalysisDefault,
    CompanySequence,
    Customer,
    Vendor,
    StatisticalEntry,
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
    can_post_to_period,
    can_post_operational_document,
    validate_line_counterparty,
    validate_counterparty_exists,
    validate_subledger_tieout,
)
from accounting.dimension_validation import validate_line_dimensions
from events.emitter import emit_event
from projections.write_barrier import command_writes_allowed
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
    FiscalPeriodClosedData,
    FiscalPeriodOpenedData,
    FiscalPeriodsConfiguredData,
    FiscalPeriodRangeSetData,
    FiscalPeriodCurrentSetData,
    FiscalPeriodDatesUpdatedData,
    FiscalYearClosedData,
    FiscalYearReopenedData,
    FiscalYearCloseReadinessCheckedData,
    ClosingEntryGeneratedData,
    ClosingEntryReversedData,
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
    CustomerReceiptRecordedData,
    VendorPaymentRecordedData,
    StatisticalEntryCreatedData,
    StatisticalEntryUpdatedData,
    StatisticalEntryPostedData,
    StatisticalEntryReversedData,
    StatisticalEntryDeletedData,
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
    payload = json.dumps(changes, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def _idempotency_hash(prefix: str, payload: dict) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(normalized).hexdigest()[:16]
    return f"{prefix}:{digest}"

def _next_company_sequence(company, name: str) -> int:
    """
    Allocate the next sequence value for a company/name pair.
    Uses select_for_update to avoid concurrent duplicates.
    """
    with command_writes_allowed():
        try:
            seq = CompanySequence.objects.select_for_update().get(
                company=company,
                name=name,
            )
        except CompanySequence.DoesNotExist:
            try:
                seq = CompanySequence.objects.create(
                    company=company,
                    name=name,
                    next_value=1,
                )
            except IntegrityError:
                seq = CompanySequence.objects.select_for_update().get(
                    company=company,
                    name=name,
                )

        value = seq.next_value
        seq.next_value = value + 1
        seq.save(update_fields=["next_value"])
        return value


def _emit_automatic_reversal(actor, entry, posting_event, reason: str):
    """
    Emit a reversal event to undo a posting when tie-out validation fails.

    This is used when enforce_subledger_tieout is enabled and the posting
    would create a tie-out violation.
    """
    from django.utils import timezone as dj_timezone

    reversal_at = dj_timezone.now()

    emit_event(
        actor=actor,
        event_type=EventTypes.JOURNAL_ENTRY_REVERSED,
        aggregate_type="JournalEntry",
        aggregate_id=str(entry.public_id),
        idempotency_key=f"journal_entry.auto_reversed:{entry.public_id}:{reversal_at.isoformat()}",
        data={
            "entry_public_id": str(entry.public_id),
            "reversed_at": reversal_at.isoformat(),
            "reversed_by_id": actor.user.id,
            "reversed_by_email": actor.user.email,
            "reason": reason,
            "auto_reversal": True,
        },
    )

    _process_projections(actor.company)


def _resolve_analysis_tags_to_public_ids(company, analysis_tags: list) -> list:
    """
    Convert analysis_tags with integer IDs to public IDs, or pass through already-resolved tags.

    Input formats:
    - [{"dimension_id": 1, "value_id": 5}, ...] - needs resolution
    - [{"dimension_id": 1, "dimension_value_id": 5}, ...] - needs resolution
    - [{"dimension_public_id": "uuid", "value_public_id": "uuid"}, ...] - already resolved

    Output format: [{"dimension_public_id": "uuid", "value_public_id": "uuid"}, ...]
    """
    if not analysis_tags:
        return []

    result = []
    tags_to_resolve = []

    # First pass: separate already-resolved tags from those needing resolution
    for tag in analysis_tags:
        if tag.get("dimension_public_id") and tag.get("value_public_id"):
            # Already resolved - pass through
            result.append({
                "dimension_public_id": str(tag["dimension_public_id"]),
                "value_public_id": str(tag["value_public_id"]),
            })
        elif tag.get("dimension_id") and (tag.get("value_id") or tag.get("dimension_value_id")):
            # Needs resolution
            tags_to_resolve.append(tag)

    # If all tags were already resolved, return early
    if not tags_to_resolve:
        return result

    # Resolve integer IDs to public IDs
    dimension_ids = [t.get("dimension_id") for t in tags_to_resolve]
    value_ids = [
        t.get("value_id") or t.get("dimension_value_id")
        for t in tags_to_resolve
    ]

    dimensions = {
        dim.id: dim
        for dim in AnalysisDimension.objects.filter(company=company, id__in=dimension_ids)
    }
    values = {
        val.id: val
        for val in AnalysisDimensionValue.objects.filter(company=company, id__in=value_ids)
    }

    for tag in tags_to_resolve:
        dim_id = tag.get("dimension_id")
        val_id = tag.get("value_id") or tag.get("dimension_value_id")
        dim = dimensions.get(dim_id)
        val = values.get(val_id)
        if dim and val:
            result.append({
                "dimension_public_id": str(dim.public_id),
                "value_public_id": str(val.public_id),
            })

    return result


def _process_projections(company, exclude: set[str] | None = None) -> None:
    if not settings.PROJECTIONS_SYNC:
        return

    from projections.base import projection_registry

    excluded = exclude or set()
    for projection in projection_registry.all():
        if projection.name in excluded:
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
    role: str = "",
    ledger_domain: str = "FINANCIAL",
    allow_manual_posting: bool = True,
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
        role: Behavioral role (e.g., RECEIVABLE_CONTROL, PAYABLE_CONTROL)
        ledger_domain: FINANCIAL, STATISTICAL, or OFF_BALANCE
        allow_manual_posting: Admin override for control accounts

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
        "account_role": role,
        "ledger_domain": ledger_domain,
        "allow_manual_posting": allow_manual_posting,
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
            account_role=role,
            ledger_domain=ledger_domain,
            allow_manual_posting=allow_manual_posting,
        ).to_dict(),
    )

    _process_projections(actor.company)
    account = Account.objects.get(company=actor.company, public_id=account_public_id)
    return CommandResult.ok(account, event=event)


@transaction.atomic
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

    # Use rls_bypass for lookup since authorization is already done above
    # and the view has already validated company ownership
    with rls_bypass():
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
    if aggregate and aggregate.deleted:
        return CommandResult.fail("Account not found.")

    # Track changes for event
    # Use aggregate state if available, otherwise fall back to DB model (legacy accounts)
    changes = {}
    allowed_fields = {
        "name", "name_ar", "description", "description_ar",
        "status", "code", "account_type", "unit_of_measure"
    }

    for field, value in updates.items():
        if field in allowed_fields:
            old_value = getattr(aggregate, field) if aggregate else getattr(account, field)
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

    # Use rls_bypass for lookup since authorization is already done above
    with rls_bypass():
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
    currency: str = None,
    exchange_rate: str = None,
    period: int = None,
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
    entry_currency = currency or actor.company.default_currency
    entry_exchange_rate = exchange_rate or "1.0"

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
            line_currency = line.get("currency") or entry_currency
            line_exchange_rate = line.get("exchange_rate") or entry_exchange_rate
            amount_currency = line.get("amount_currency")
            line_data.append(JournalLineData(
                line_no=line_no,
                account_public_id=str(account.public_id),
                account_code=account.code,
                description=line.get("description", ""),
                description_ar=line.get("description_ar", ""),
                debit=str(debit),
                credit=str(credit),
                amount_currency=str(amount_currency) if amount_currency is not None else None,
                currency=line_currency,
                exchange_rate=str(line_exchange_rate) if line_exchange_rate is not None else None,
                is_memo_line=account.is_memo_account,
                analysis_tags=_resolve_analysis_tags_to_public_ids(
                    actor.company, line.get("analysis_tags", [])
                ),
                customer_public_id=line.get("customer_public_id"),
                vendor_public_id=line.get("vendor_public_id"),
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
                "amount_currency": str(line.get("amount_currency")) if line.get("amount_currency") is not None else None,
                "currency": line.get("currency"),
                "exchange_rate": str(line.get("exchange_rate")) if line.get("exchange_rate") is not None else None,
            })

    idempotency_key = _idempotency_hash("journal_entry.created", {
        "company_public_id": str(actor.company.public_id),
        "date": date.isoformat() if hasattr(date, "isoformat") else str(date),
        "memo": memo,
        "memo_ar": memo_ar,
        "kind": kind,
        "currency": entry_currency,
        "exchange_rate": str(entry_exchange_rate),
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
            period=period,
            currency=entry_currency,
            exchange_rate=str(entry_exchange_rate),
            created_by_id=actor.user.id,
            lines=line_data,
        ).to_dict(),
    )

    _process_projections(actor.company)
    try:
        entry = JournalEntry.objects.get(company=actor.company, public_id=entry_public_id)
    except JournalEntry.DoesNotExist:
        # Projection may have failed; check bookmark for errors
        from projections.base import projection_registry
        with rls_bypass():
            je_proj = projection_registry.get("journal_entry_read_model")
            if je_proj:
                bookmark = je_proj.get_bookmark(actor.company)
                if bookmark and bookmark.last_error:
                    return CommandResult.fail(f"Projection error: {bookmark.last_error}")
        return CommandResult.fail("Journal entry could not be created. Projection may have failed.")
    return CommandResult.ok(entry, event=event)


@transaction.atomic
def update_journal_entry(
    actor: ActorContext,
    entry_id: int,
    date=None,
    memo: str = None,
    memo_ar: str = None,
    currency: str = None,
    exchange_rate: str = None,
    lines: list = None,
    period: int = None,
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

    aggregate = load_journal_entry_aggregate(actor.company, str(entry.public_id))
    if not aggregate or aggregate.deleted:
        return CommandResult.fail("Journal entry not found.")

    allowed, reason = can_edit_entry(actor, aggregate)
    if not allowed:
        return CommandResult.fail(reason)

    if date is not None:
        allowed, reason = can_post_to_period(actor, date)
        if not allowed:
            return CommandResult.fail(reason)

    # Track changes
    changes = {}
    current_date = aggregate.date or (entry.date.isoformat() if entry.date else None)
    
    if date is not None:
        new_date = date.isoformat() if hasattr(date, "isoformat") else str(date)
        if current_date != new_date:
            changes["date"] = {"old": current_date, "new": new_date}
    
    if memo is not None and aggregate.memo != memo:
        changes["memo"] = {"old": aggregate.memo, "new": memo}
    
    if memo_ar is not None and aggregate.memo_ar != memo_ar:
        changes["memo_ar"] = {"old": aggregate.memo_ar, "new": memo_ar}

    if currency is not None and aggregate.currency != currency:
        changes["currency"] = {"old": aggregate.currency, "new": currency}

    if exchange_rate is not None and str(aggregate.exchange_rate) != str(exchange_rate):
        changes["exchange_rate"] = {"old": aggregate.exchange_rate, "new": exchange_rate}

    if period is not None and entry.period != period:
        changes["period"] = {"old": entry.period, "new": period}

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
            line_currency = line.get("currency") or entry.currency or actor.company.default_currency
            line_exchange_rate = line.get("exchange_rate") or entry.exchange_rate
            line_data.append(JournalLineData(
                line_no=line_no,
                account_public_id=str(account.public_id),
                account_code=account.code,
                description=line.get("description", ""),
                description_ar=line.get("description_ar", ""),
                debit=str(debit),
                credit=str(credit),
                amount_currency=str(line.get("amount_currency")) if line.get("amount_currency") is not None else None,
                currency=line_currency,
                exchange_rate=str(line_exchange_rate) if line_exchange_rate is not None else None,
                is_memo_line=account.is_memo_account,
                analysis_tags=_resolve_analysis_tags_to_public_ids(
                    actor.company, line.get("analysis_tags", [])
                ),
                customer_public_id=line.get("customer_public_id"),
                vendor_public_id=line.get("vendor_public_id"),
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
    currency: str = None,
    exchange_rate: str = None,
    lines: list = None,
    period: int = None,
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
            line_currency = line.get("currency") or entry.currency or actor.company.default_currency
            line_exchange_rate = line.get("exchange_rate") or entry.exchange_rate
            line_data.append(JournalLineData(
                line_no=line_no,
                account_public_id=str(account.public_id),
                account_code=account.code,
                description=line.get("description", ""),
                description_ar=line.get("description_ar", ""),
                debit=str(debit),
                credit=str(credit),
                amount_currency=str(line.get("amount_currency")) if line.get("amount_currency") is not None else None,
                currency=line_currency,
                exchange_rate=str(line_exchange_rate) if line_exchange_rate is not None else None,
                is_memo_line=account.is_memo_account,
                analysis_tags=_resolve_analysis_tags_to_public_ids(
                    actor.company, line.get("analysis_tags", [])
                ),
                customer_public_id=line.get("customer_public_id"),
                vendor_public_id=line.get("vendor_public_id"),
            ).to_dict())
            line_no += 1

    aggregate = load_journal_entry_aggregate(actor.company, str(entry.public_id))
    if not aggregate or aggregate.deleted:
        return CommandResult.fail("Journal entry not found.")

    allowed, reason = can_edit_entry(actor, aggregate)
    if not allowed:
        return CommandResult.fail(reason)

    if date is not None:
        allowed, reason = can_post_to_period(actor, date)
        if not allowed:
            return CommandResult.fail(reason)

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
    current_date = aggregate.date or (entry.date.isoformat() if entry.date else None)
    resolved_exchange_rate = exchange_rate if exchange_rate is not None else aggregate.exchange_rate
    payload = {
        "date": date.isoformat() if hasattr(date, "isoformat") else (str(date) if date is not None else current_date),
        "memo": memo if memo is not None else (aggregate.memo or ""),
        "memo_ar": memo_ar if memo_ar is not None else (aggregate.memo_ar or ""),
        "currency": currency if currency is not None else aggregate.currency,
        "exchange_rate": str(resolved_exchange_rate) if resolved_exchange_rate is not None else None,
        "lines": lines_payload,
    }
    if not payload["date"]:
        return CommandResult.fail("Entry date is required to save as complete.")
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]
    event = emit_event(
        actor=actor,
        event_type=EventTypes.JOURNAL_ENTRY_SAVED_COMPLETE,
        aggregate_type="JournalEntry",
        aggregate_id=str(entry.public_id),
        idempotency_key=f"journal_entry.saved_complete:{entry.public_id}:{digest}",
        data=JournalEntrySavedCompleteData(
            entry_public_id=str(entry.public_id),
            date=payload["date"],
            memo=payload["memo"],
            memo_ar=payload["memo_ar"],
            status=JournalEntry.Status.DRAFT,
            line_count=line_count,
            total_debit=str(total_debit),
            total_credit=str(total_credit),
            period=period if period is not None else entry.period,
            currency=payload.get("currency"),
            exchange_rate=str(payload.get("exchange_rate")) if payload.get("exchange_rate") is not None else None,
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

    postable_kinds = [JournalEntry.Kind.NORMAL, JournalEntry.Kind.OPENING, JournalEntry.Kind.ADJUSTMENT, JournalEntry.Kind.CLOSING]
    if aggregate.kind not in postable_kinds:
        return CommandResult.fail(f"Cannot post {aggregate.kind} entries.")

    if len(aggregate.lines) < 2:
        return CommandResult.fail("Entry must have at least 2 lines to be posted.")

    if aggregate.total_debit != aggregate.total_credit:
        return CommandResult.fail(
            f"Entry is not balanced. Debit={aggregate.total_debit} Credit={aggregate.total_credit}"
        )

    allowed, reason = can_post_to_period(actor, aggregate.date or entry.date, period=entry.period)
    if not allowed:
        return CommandResult.fail(reason)

    posted_at = timezone.now()

    sequence_value = _next_company_sequence(entry.company, "journal_entry_number")
    entry_number = f"JE-{entry.company_id}-{sequence_value:06d}"

    # Build line data for event (including analysis tags and counterparty)
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

        # Extract counterparty from line
        customer_public_id = line.get("customer_public_id")
        vendor_public_id = line.get("vendor_public_id")

        # Validate counterparty requirements for control accounts
        allowed, reason = validate_line_counterparty(
            account, customer_public_id, vendor_public_id
        )
        if not allowed:
            return CommandResult.fail(reason)

        # Validate counterparty exists if provided
        if customer_public_id or vendor_public_id:
            valid, reason, _ = validate_counterparty_exists(
                actor.company, customer_public_id, vendor_public_id
            )
            if not valid:
                return CommandResult.fail(reason)

        # Validate dimension rules (REQUIRED / FORBIDDEN per account)
        resolved_tags = _resolve_analysis_tags_to_public_ids(
            actor.company, line.get("analysis_tags", [])
        )
        dimension_errors = validate_line_dimensions(
            account=account,
            analysis_tags=resolved_tags,
            company=actor.company,
        )
        if dimension_errors:
            error_messages = "; ".join(e["message"] for e in dimension_errors)
            return CommandResult.fail(
                f"Line {line.get('line_no', '?')}: {error_messages}"
            )

        line_data.append(JournalLineData(
            line_no=line.get("line_no"),
            account_public_id=str(account.public_id),
            account_code=account.code,
            description=line.get("description", ""),
            description_ar=line.get("description_ar", ""),
            debit=str(line.get("debit", "0")),
            credit=str(line.get("credit", "0")),
            amount_currency=str(line.get("amount_currency")) if line.get("amount_currency") is not None else None,
            currency=line.get("currency") or aggregate.currency or entry.currency or actor.company.default_currency,
            exchange_rate=str(line.get("exchange_rate") or aggregate.exchange_rate or entry.exchange_rate or "1.0"),
            is_memo_line=account.is_memo_account,
            analysis_tags=resolved_tags,
            customer_public_id=customer_public_id,
            vendor_public_id=vendor_public_id,
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
            period=entry.period,
            currency=aggregate.currency or entry.currency or actor.company.default_currency,
            exchange_rate=str(aggregate.exchange_rate or entry.exchange_rate or "1.0"),
            posted_at=posted_at.isoformat(),
            posted_by_id=actor.user.id,
            posted_by_email=actor.user.email,
            total_debit=str(aggregate.total_debit),
            total_credit=str(aggregate.total_credit),
            lines=line_data,
        ).to_dict(),
    )

    _process_projections(actor.company)

    # Validate subledger tie-out after posting
    # Only check tie-out if the entry affects AR or AP control accounts
    has_subledger_impact = any(
        line.get("customer_public_id") or line.get("vendor_public_id")
        for line in line_data
    )
    if has_subledger_impact:
        import logging
        logger = logging.getLogger(__name__)
        is_valid, tieout_errors = validate_subledger_tieout(actor.company)
        if not is_valid:
            # Refresh company to get latest settings
            actor.company.refresh_from_db()
            if getattr(actor.company, 'enforce_subledger_tieout', False):
                # Strict enforcement enabled - fail the posting
                # Emit a reversal event to undo the posting
                _emit_automatic_reversal(actor, entry, event, reason="Subledger tie-out violation")
                return CommandResult.fail(
                    f"Posting failed: subledger tie-out violation. {'; '.join(tieout_errors)}. "
                    "The entry was automatically reversed."
                )
            else:
                # Log warning but don't fail - this catches projection lag
                for error in tieout_errors:
                    logger.warning(f"Subledger tie-out warning after posting {entry.public_id}: {error}")

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

    # Use the original entry's date for period resolution so the reversal
    # lands in the same fiscal period as the original entry.
    original_date = original.date
    reversal_period = None
    from projections.models import FiscalPeriod
    reversal_fp = FiscalPeriod.objects.filter(
        company=actor.company,
        start_date__lte=original_date,
        end_date__gte=original_date,
        period_type=FiscalPeriod.PeriodType.NORMAL,
    ).first()
    if reversal_fp:
        reversal_period = reversal_fp.period

    allowed, reason = can_post_to_period(actor, original_date, period=reversal_period)
    if not allowed:
        return CommandResult.fail(reason)

    sequence_value = _next_company_sequence(original.company, "journal_entry_number")
    reversal_entry_number = f"JE-{original.company_id}-{sequence_value:06d}"

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
            amount_currency=str(line.get("amount_currency")) if line.get("amount_currency") is not None else None,
            currency=line.get("currency") or aggregate.currency or original.currency or actor.company.default_currency,
            exchange_rate=str(line.get("exchange_rate") or aggregate.exchange_rate or original.exchange_rate or "1.0"),
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
            date=original_date.isoformat(),
            memo=f"Reversal of JE#{original.id}: {aggregate.memo}",
            memo_ar=f"عكس قيد #{original.id}: {aggregate.memo_ar}" if aggregate.memo_ar else "",
            kind=JournalEntry.Kind.REVERSAL,
            period=reversal_period,
            currency=aggregate.currency or original.currency or actor.company.default_currency,
            exchange_rate=str(aggregate.exchange_rate or original.exchange_rate or "1.0"),
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

    _process_projections(actor.company)
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

    aggregate = load_journal_entry_aggregate(actor.company, str(entry.public_id))
    if not aggregate or aggregate.deleted:
        return CommandResult.fail("Journal entry not found.")

    allowed, reason = can_delete_entry(actor, aggregate)
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
            date=aggregate.date or entry.date.isoformat(),
            memo=aggregate.memo,
            status=aggregate.status,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok({"deleted": True}, event=event)


# =============================================================================
# Fiscal Period Commands
# =============================================================================

@transaction.atomic
def close_period(
    actor: ActorContext,
    fiscal_year: int,
    period: int,
) -> CommandResult:
    """
    Close a fiscal period.

    Args:
        actor: The actor context
        fiscal_year: Fiscal year start (e.g., 2024)
        period: Period number (1-12)
    """
    require(actor, "periods.close")

    from projections.models import FiscalPeriod

    fiscal_period = FiscalPeriod.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
        period=period,
    ).first()
    if not fiscal_period:
        return CommandResult.fail("Fiscal period not found.")

    if fiscal_period.status == FiscalPeriod.Status.CLOSED:
        return CommandResult.fail("Fiscal period is already closed.")

    closed_at = timezone.now()
    event = emit_event(
        actor=actor,
        event_type=EventTypes.FISCAL_PERIOD_CLOSED,
        aggregate_type="FiscalPeriod",
        aggregate_id=f"{actor.company.public_id}:{fiscal_year}:{period}",
        idempotency_key=f"fiscal_period.closed:{actor.company.public_id}:{fiscal_year}:{period}:{closed_at.isoformat()}",
        data=FiscalPeriodClosedData(
            company_public_id=str(actor.company.public_id),
            fiscal_year=fiscal_year,
            period=period,
            closed_at=closed_at.isoformat(),
            closed_by_id=actor.user.id,
            closed_by_email=actor.user.email,
        ).to_dict(),
    )

    _process_projections(actor.company)
    fiscal_period = FiscalPeriod.objects.get(
        company=actor.company,
        fiscal_year=fiscal_year,
        period=period,
    )
    return CommandResult.ok(fiscal_period, event=event)


@transaction.atomic
def open_period(
    actor: ActorContext,
    fiscal_year: int,
    period: int,
) -> CommandResult:
    """
    Reopen a closed fiscal period.

    Args:
        actor: The actor context
        fiscal_year: Fiscal year
        period: Period number
    """
    require(actor, "periods.reopen")

    from projections.models import FiscalPeriod, FiscalYear as FiscalYearModel

    fiscal_period = FiscalPeriod.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
        period=period,
    ).first()
    if not fiscal_period:
        return CommandResult.fail("Fiscal period not found.")

    if fiscal_period.status == FiscalPeriod.Status.OPEN:
        return CommandResult.fail("Fiscal period is already open.")

    # Block opening periods in a closed fiscal year (must reopen year first)
    fy = FiscalYearModel.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
    ).first()
    if fy and fy.status == FiscalYearModel.Status.CLOSED:
        return CommandResult.fail(
            f"Cannot open period in closed fiscal year {fiscal_year}. "
            "Reopen the fiscal year first."
        )

    opened_at = timezone.now()
    event = emit_event(
        actor=actor,
        event_type=EventTypes.FISCAL_PERIOD_OPENED,
        aggregate_type="FiscalPeriod",
        aggregate_id=f"{actor.company.public_id}:{fiscal_year}:{period}",
        idempotency_key=f"fiscal_period.opened:{actor.company.public_id}:{fiscal_year}:{period}:{opened_at.isoformat()}",
        data=FiscalPeriodOpenedData(
            company_public_id=str(actor.company.public_id),
            fiscal_year=fiscal_year,
            period=period,
            opened_at=opened_at.isoformat(),
            opened_by_id=actor.user.id,
            opened_by_email=actor.user.email,
        ).to_dict(),
    )

    _process_projections(actor.company)
    fiscal_period = FiscalPeriod.objects.get(
        company=actor.company,
        fiscal_year=fiscal_year,
        period=period,
    )
    return CommandResult.ok(fiscal_period, event=event)


def _calculate_period_boundaries(fiscal_year: int, start_month: int, period_count: int = 13):
    """
    Calculate monthly-aligned period boundaries for a fiscal year.

    Standard ERP behavior: 12 monthly periods + 1 adjustment period (Period 13).
    Period 13 shares the same end date as Period 12 — it's a logical period
    for year-end adjustments and closing entries, not a calendar period.

    Args:
        fiscal_year: The fiscal year (calendar year the fiscal year starts)
        start_month: Month the fiscal year starts (1-12)
        period_count: Always 13 for standard ERP (12 normal + 1 adjustment)

    Returns:
        List of dicts with period, start_date, end_date, period_type
    """
    from datetime import date
    import calendar

    periods = []

    # Generate 12 monthly periods aligned to calendar months
    for i in range(12):
        month_index = (start_month - 1) + i
        year = fiscal_year + (month_index // 12)
        month = (month_index % 12) + 1
        last_day = calendar.monthrange(year, month)[1]

        periods.append({
            "period": i + 1,
            "start_date": date(year, month, 1).isoformat(),
            "end_date": date(year, month, last_day).isoformat(),
            "period_type": "NORMAL",
        })

    # Period 13: Adjustment period — same end date as Period 12
    # Start date = end date = last day of fiscal year
    fy_end_date = periods[11]["end_date"]
    periods.append({
        "period": 13,
        "start_date": fy_end_date,
        "end_date": fy_end_date,
        "period_type": "ADJUSTMENT",
    })

    return periods


@transaction.atomic
def configure_periods(
    actor: ActorContext,
    fiscal_year: int,
    period_count: int = 13,
) -> CommandResult:
    """
    Configure fiscal periods for a fiscal year.

    Standard ERP: always 12 monthly periods + 1 adjustment period (Period 13).
    The period_count parameter is accepted for backwards compatibility but
    is always normalized to 13.

    Args:
        actor: The actor context
        fiscal_year: Fiscal year
        period_count: Ignored — always 13 (12 normal + 1 adjustment)
    """
    require(actor, "periods.configure")

    # Standard ERP: always 13 periods (12 monthly + 1 adjustment)
    period_count = 13

    from projections.models import FiscalPeriod, FiscalPeriodConfig

    # Get existing config to record previous_period_count
    config = FiscalPeriodConfig.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
    ).first()
    previous_period_count = config.period_count if config else 12

    start_month = actor.company.fiscal_year_start_month or 1
    periods = _calculate_period_boundaries(fiscal_year, start_month, period_count)

    configured_at = timezone.now()
    event = emit_event(
        actor=actor,
        event_type=EventTypes.FISCAL_PERIODS_CONFIGURED,
        aggregate_type="FiscalPeriod",
        aggregate_id=f"{actor.company.public_id}:{fiscal_year}",
        idempotency_key=f"fiscal_periods.configured:{actor.company.public_id}:{fiscal_year}:{period_count}:{configured_at.isoformat()}",
        data=FiscalPeriodsConfiguredData(
            company_public_id=str(actor.company.public_id),
            fiscal_year=fiscal_year,
            period_count=period_count,
            periods=periods,
            configured_at=configured_at.isoformat(),
            configured_by_id=actor.user.id,
            configured_by_email=actor.user.email,
            previous_period_count=previous_period_count,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok({"period_count": period_count, "periods": periods}, event=event)


@transaction.atomic
def set_period_range(
    actor: ActorContext,
    fiscal_year: int,
    open_from_period: int,
    open_to_period: int,
) -> CommandResult:
    """
    Set which periods are open for posting via a from/to range.
    Periods within range become OPEN, all others become CLOSED.

    Args:
        actor: The actor context
        fiscal_year: Fiscal year
        open_from_period: First period to open
        open_to_period: Last period to open
    """
    require(actor, "periods.configure")

    from projections.models import FiscalPeriodConfig

    config = FiscalPeriodConfig.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
    ).first()
    max_period = config.period_count if config else 13

    if open_from_period < 1 or open_to_period > max_period:
        return CommandResult.fail(f"Period range must be between 1 and {max_period}.")
    if open_from_period > open_to_period:
        return CommandResult.fail("'Open From' must be less than or equal to 'Open To'.")

    set_at = timezone.now()
    event = emit_event(
        actor=actor,
        event_type=EventTypes.FISCAL_PERIOD_RANGE_SET,
        aggregate_type="FiscalPeriod",
        aggregate_id=f"{actor.company.public_id}:{fiscal_year}",
        idempotency_key=f"fiscal_period.range_set:{actor.company.public_id}:{fiscal_year}:{open_from_period}:{open_to_period}:{set_at.isoformat()}",
        data=FiscalPeriodRangeSetData(
            company_public_id=str(actor.company.public_id),
            fiscal_year=fiscal_year,
            open_from_period=open_from_period,
            open_to_period=open_to_period,
            set_at=set_at.isoformat(),
            set_by_id=actor.user.id,
            set_by_email=actor.user.email,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok({
        "open_from_period": open_from_period,
        "open_to_period": open_to_period,
    }, event=event)


@transaction.atomic
def set_current_period(
    actor: ActorContext,
    fiscal_year: int,
    period: int,
) -> CommandResult:
    """
    Set which period is the 'current' period.

    Args:
        actor: The actor context
        fiscal_year: Fiscal year
        period: Period number to mark as current
    """
    require(actor, "periods.configure")

    from projections.models import FiscalPeriod, FiscalPeriodConfig

    # Validate period exists
    fiscal_period = FiscalPeriod.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
        period=period,
    ).first()
    if not fiscal_period:
        return CommandResult.fail("Fiscal period not found.")

    # Get previous current period
    config = FiscalPeriodConfig.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
    ).first()
    previous_period = config.current_period if config else None

    set_at = timezone.now()
    event = emit_event(
        actor=actor,
        event_type=EventTypes.FISCAL_PERIOD_CURRENT_SET,
        aggregate_type="FiscalPeriod",
        aggregate_id=f"{actor.company.public_id}:{fiscal_year}",
        idempotency_key=f"fiscal_period.current_set:{actor.company.public_id}:{fiscal_year}:{period}:{set_at.isoformat()}",
        data=FiscalPeriodCurrentSetData(
            company_public_id=str(actor.company.public_id),
            fiscal_year=fiscal_year,
            period=period,
            set_at=set_at.isoformat(),
            set_by_id=actor.user.id,
            set_by_email=actor.user.email,
            previous_period=previous_period,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok({"current_period": period}, event=event)


@transaction.atomic
def update_period_dates(
    actor: ActorContext,
    fiscal_year: int,
    period: int,
    start_date: str,
    end_date: str,
) -> CommandResult:
    """
    Update the start and end dates of a fiscal period.

    Args:
        actor: The actor context
        fiscal_year: Fiscal year
        period: Period number
        start_date: New start date (ISO format)
        end_date: New end date (ISO format)
    """
    require(actor, "periods.configure")

    from datetime import date as date_cls
    from projections.models import FiscalPeriod

    # Parse dates
    try:
        new_start = date_cls.fromisoformat(start_date)
        new_end = date_cls.fromisoformat(end_date)
    except (ValueError, TypeError):
        return CommandResult.fail("Invalid date format. Use ISO format (YYYY-MM-DD).")

    if new_start > new_end:
        return CommandResult.fail("Start date must be on or before end date.")

    fiscal_period = FiscalPeriod.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
        period=period,
    ).first()
    if not fiscal_period:
        return CommandResult.fail("Fiscal period not found.")

    previous_start = fiscal_period.start_date.isoformat()
    previous_end = fiscal_period.end_date.isoformat()

    updated_at = timezone.now()
    event = emit_event(
        actor=actor,
        event_type=EventTypes.FISCAL_PERIOD_DATES_UPDATED,
        aggregate_type="FiscalPeriod",
        aggregate_id=f"{actor.company.public_id}:{fiscal_year}:{period}",
        idempotency_key=f"fiscal_period.dates_updated:{actor.company.public_id}:{fiscal_year}:{period}:{updated_at.isoformat()}",
        data=FiscalPeriodDatesUpdatedData(
            company_public_id=str(actor.company.public_id),
            fiscal_year=fiscal_year,
            period=period,
            start_date=start_date,
            end_date=end_date,
            previous_start_date=previous_start,
            previous_end_date=previous_end,
            updated_at=updated_at.isoformat(),
            updated_by_id=actor.user.id,
            updated_by_email=actor.user.email,
        ).to_dict(),
    )

    _process_projections(actor.company)
    fiscal_period = FiscalPeriod.objects.get(
        company=actor.company,
        fiscal_year=fiscal_year,
        period=period,
    )
    return CommandResult.ok(fiscal_period, event=event)


# =============================================================================
# Fiscal Year Close / Reopen Commands
# =============================================================================

def check_close_readiness(
    actor: ActorContext,
    fiscal_year: int,
) -> CommandResult:
    """
    Check if a fiscal year is ready to be closed.

    Preconditions checked:
    1. All periods 1-12 must be CLOSED
    2. Period 13 must exist and be OPEN (for closing entries)
    3. No DRAFT or INCOMPLETE journal entries in the fiscal year
    4. Subledger tie-out must pass
    5. No projection lag
    6. Fiscal year must not already be closed

    Returns:
        CommandResult with readiness data: {is_ready, issues}
    """
    require(actor, "periods.configure")

    from projections.models import FiscalPeriod, FiscalYear as FiscalYearModel

    checks = []

    # Check 1: Fiscal year not already closed
    fy = FiscalYearModel.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
    ).first()
    fy_not_closed = not (fy and fy.status == FiscalYearModel.Status.CLOSED)
    checks.append({
        "check": "Fiscal year not already closed",
        "passed": fy_not_closed,
        "detail": "" if fy_not_closed else f"Fiscal year {fiscal_year} is already closed.",
    })

    # Check 2: All normal periods (1-12) are closed
    normal_periods = FiscalPeriod.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
        period_type=FiscalPeriod.PeriodType.NORMAL,
    )
    open_normal = normal_periods.filter(status=FiscalPeriod.Status.OPEN)
    all_normal_closed = not open_normal.exists()
    open_nums = list(open_normal.values_list("period", flat=True)) if not all_normal_closed else []
    checks.append({
        "check": "All normal periods (1-12) closed",
        "passed": all_normal_closed,
        "detail": "" if all_normal_closed else f"Periods still open: {open_nums}",
    })

    # Check 3: Period 13 exists and is open
    p13 = FiscalPeriod.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
        period=13,
    ).first()
    p13_ready = p13 is not None and p13.status == FiscalPeriod.Status.OPEN
    if not p13:
        p13_detail = "Period 13 (adjustment) does not exist. Run configure_periods first."
    elif p13.status != FiscalPeriod.Status.OPEN:
        p13_detail = "Period 13 must be OPEN to post closing entries."
    else:
        p13_detail = ""
    checks.append({
        "check": "Period 13 (adjustment) exists and is open",
        "passed": p13_ready,
        "detail": p13_detail,
    })

    # Check 4: No draft/incomplete entries in this fiscal year
    fy_periods = FiscalPeriod.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
        period_type=FiscalPeriod.PeriodType.NORMAL,
    ).order_by("period")
    no_drafts = True
    draft_detail = ""
    if fy_periods.exists():
        fy_start = fy_periods.first().start_date
        fy_end = fy_periods.last().end_date
        draft_count = JournalEntry.objects.filter(
            company=actor.company,
            status__in=[JournalEntry.Status.INCOMPLETE, JournalEntry.Status.DRAFT],
            date__gte=fy_start, date__lte=fy_end,
        ).count()
        if draft_count > 0:
            no_drafts = False
            draft_detail = f"Found {draft_count} draft/incomplete journal entries."
    checks.append({
        "check": "No draft or incomplete journal entries",
        "passed": no_drafts,
        "detail": draft_detail,
    })

    # Check 5: Subledger tie-out
    tieout_valid, tieout_errors = validate_subledger_tieout(actor.company)
    checks.append({
        "check": "Subledger tie-out balanced",
        "passed": tieout_valid,
        "detail": "; ".join(tieout_errors) if not tieout_valid else "",
    })

    # Check 6: Projection lag (all projections must be up to date)
    from projections.base import projection_registry
    total_lag = 0
    for projection in projection_registry.all():
        total_lag += projection.get_lag(actor.company)
    projections_current = total_lag == 0
    checks.append({
        "check": "All projections up to date (no lag)",
        "passed": projections_current,
        "detail": "" if projections_current else f"{total_lag} events pending processing.",
    })

    is_ready = all(c["passed"] for c in checks)

    # Emit audit event (keep issues list for backward compat in event payload)
    issues = [c["detail"] for c in checks if not c["passed"]]
    checked_at = timezone.now()
    emit_event(
        actor=actor,
        event_type=EventTypes.FISCAL_YEAR_CLOSE_READINESS_CHECKED,
        aggregate_type="FiscalYear",
        aggregate_id=f"{actor.company.public_id}:{fiscal_year}",
        idempotency_key=f"fiscal_year.close_readiness:{actor.company.public_id}:{fiscal_year}:{checked_at.isoformat()}",
        data=FiscalYearCloseReadinessCheckedData(
            company_public_id=str(actor.company.public_id),
            fiscal_year=fiscal_year,
            is_ready=is_ready,
            issues=issues,
            checked_at=checked_at.isoformat(),
            checked_by_id=actor.user.id,
            checked_by_email=actor.user.email,
        ).to_dict(),
    )

    logger.info(
        "fiscal_year.close_readiness_checked",
        extra={
            "company_id": actor.company.id,
            "fiscal_year": fiscal_year,
            "is_ready": is_ready,
            "failed_checks": [c["check"] for c in checks if not c["passed"]],
            "user_id": actor.user.id,
        },
    )

    return CommandResult.ok({
        "fiscal_year": fiscal_year,
        "is_ready": is_ready,
        "checks": checks,
    })


@transaction.atomic
def close_fiscal_year(
    actor: ActorContext,
    fiscal_year: int,
    retained_earnings_account_code: str,
) -> CommandResult:
    """
    Close a fiscal year. This is the formal year-end close workflow.

    Steps:
    1. Verify close readiness (all preconditions)
    2. Generate closing entries in Period 13 (zero out Revenue & Expense to Retained Earnings)
    3. Lock all 13 periods (CLOSED)
    4. Mark fiscal year as CLOSED
    5. Create next fiscal year's 13 periods with Period 1 OPEN

    Idempotent: If already closed, returns success with no-op.

    Args:
        actor: The actor context
        fiscal_year: Fiscal year to close
        retained_earnings_account_code: Account code for retained earnings (must be EQUITY type)
    """
    require(actor, "fiscal_year.close")

    from projections.models import FiscalPeriod, FiscalPeriodConfig, FiscalYear as FiscalYearModel, AccountBalance

    # Idempotency: already closed?
    fy = FiscalYearModel.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
    ).first()
    if fy and fy.status == FiscalYearModel.Status.CLOSED:
        return CommandResult.ok({"already_closed": True, "fiscal_year": fiscal_year})

    # Validate retained earnings account
    re_account = Account.objects.filter(
        company=actor.company,
        code=retained_earnings_account_code,
    ).first()
    if not re_account:
        return CommandResult.fail(f"Retained earnings account '{retained_earnings_account_code}' not found.")
    if re_account.account_type != Account.AccountType.EQUITY:
        return CommandResult.fail(f"Account '{retained_earnings_account_code}' must be EQUITY type for retained earnings.")
    if re_account.is_header:
        return CommandResult.fail(f"Account '{retained_earnings_account_code}' is a header account. Use a postable account.")

    # Check readiness (reuse logic but inline to avoid double event)
    normal_periods = FiscalPeriod.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
        period_type=FiscalPeriod.PeriodType.NORMAL,
    )
    open_normal = normal_periods.filter(status=FiscalPeriod.Status.OPEN)
    if open_normal.exists():
        open_nums = list(open_normal.values_list("period", flat=True))
        return CommandResult.fail(f"Cannot close year: periods {open_nums} are still open. Close all normal periods first.")

    p13 = FiscalPeriod.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
        period=13,
    ).first()
    if not p13:
        return CommandResult.fail("Period 13 (adjustment period) does not exist. Run configure_periods first.")

    # Ensure P13 is open for closing entries
    if p13.status != FiscalPeriod.Status.OPEN:
        return CommandResult.fail("Period 13 must be OPEN to generate closing entries.")

    # Check no drafts in P13
    fy_periods = FiscalPeriod.objects.filter(
        company=actor.company, fiscal_year=fiscal_year,
        period_type=FiscalPeriod.PeriodType.NORMAL,
    ).order_by("period")
    if fy_periods.exists():
        fy_start = fy_periods.first().start_date
        fy_end = fy_periods.last().end_date
        drafts = JournalEntry.objects.filter(
            company=actor.company,
            status__in=[JournalEntry.Status.INCOMPLETE, JournalEntry.Status.DRAFT],
            date__gte=fy_start, date__lte=fy_end,
        )
        if drafts.exists():
            return CommandResult.fail(f"Cannot close year: {drafts.count()} draft/incomplete entries exist.")

    # Subledger tie-out
    tieout_valid, tieout_errors = validate_subledger_tieout(actor.company)
    if not tieout_valid:
        return CommandResult.fail(f"Subledger tie-out failed: {'; '.join(tieout_errors)}")

    # === STEP 1: Calculate net income from FISCAL-YEAR-SCOPED balances ===
    # Use PeriodAccountBalance to get FY-scoped balances, not global AccountBalance.
    # This ensures we only close THIS year's activity, not lifetime balances.
    from projections.models import PeriodAccountBalance

    revenue_accounts = Account.objects.filter(
        company=actor.company,
        account_type=Account.AccountType.REVENUE,
        is_header=False,
    )
    expense_accounts = Account.objects.filter(
        company=actor.company,
        account_type=Account.AccountType.EXPENSE,
        is_header=False,
    )

    total_revenue = Decimal("0.00")
    total_expenses = Decimal("0.00")
    closing_lines = []

    def _fy_account_balance(acct):
        """Sum period debit/credit for this account across all periods of the fiscal year."""
        pabs = PeriodAccountBalance.objects.filter(
            company=actor.company,
            account=acct,
            fiscal_year=fiscal_year,
        )
        total_debit = sum(p.period_debit for p in pabs)
        total_credit = sum(p.period_credit for p in pabs)
        return total_debit, total_credit

    for acct in revenue_accounts:
        fy_debit, fy_credit = _fy_account_balance(acct)
        # Revenue: CREDIT normal. Net balance = credit - debit
        net = fy_credit - fy_debit
        if net == Decimal("0.00"):
            continue
        # To zero this account: reverse whatever it holds.
        # If net > 0 (normal credit balance): debit revenue to zero it.
        # If net < 0 (abnormal debit balance): credit revenue to zero it.
        closing_lines.append({
            "account_public_id": str(acct.public_id),
            "account_code": acct.code,
            "debit": str(net) if net > 0 else "0",
            "credit": str(abs(net)) if net < 0 else "0",
        })
        total_revenue += net

    for acct in expense_accounts:
        fy_debit, fy_credit = _fy_account_balance(acct)
        # Expense: DEBIT normal. Net balance = debit - credit
        net = fy_debit - fy_credit
        if net == Decimal("0.00"):
            continue
        # To zero this account: reverse whatever it holds.
        # If net > 0 (normal debit balance): credit expense to zero it.
        # If net < 0 (abnormal credit balance): debit expense to zero it.
        closing_lines.append({
            "account_public_id": str(acct.public_id),
            "account_code": acct.code,
            "debit": str(abs(net)) if net < 0 else "0",
            "credit": str(net) if net > 0 else "0",
        })
        total_expenses += net

    # Net income = total revenue credits minus total expense debits
    net_income = total_revenue - total_expenses

    # Add the retained earnings line (balancing entry)
    # This line absorbs the net of all closing lines to keep the entry balanced.
    re_debit_total = sum(Decimal(l["debit"]) for l in closing_lines)
    re_credit_total = sum(Decimal(l["credit"]) for l in closing_lines)
    re_difference = re_debit_total - re_credit_total

    if re_difference > 0:
        # More debits than credits in temp accounts -> net profit -> credit RE
        closing_lines.append({
            "account_public_id": str(re_account.public_id),
            "account_code": re_account.code,
            "debit": "0",
            "credit": str(re_difference),
        })
    elif re_difference < 0:
        # More credits than debits in temp accounts -> net loss -> debit RE
        closing_lines.append({
            "account_public_id": str(re_account.public_id),
            "account_code": re_account.code,
            "debit": str(abs(re_difference)),
            "credit": "0",
        })
    # If re_difference == 0, no retained earnings line needed (but still close temp accounts)

    # === STEP 2: Generate closing journal entry in P13 ===
    closing_entry_public_id = str(uuid.uuid4())
    entry_number = _next_company_sequence(actor.company, "journal_entry")
    closed_at = timezone.now()

    # Build full journal lines with line numbers
    je_lines = []
    for i, line in enumerate(closing_lines, 1):
        je_lines.append({
            "line_no": i,
            "account_public_id": line["account_public_id"],
            "account_code": line["account_code"],
            "description": f"Year-end closing FY{fiscal_year}",
            "debit": line["debit"],
            "credit": line["credit"],
        })

    # Compute total debit/credit for the closing entry
    closing_total_debit = sum(Decimal(l["debit"]) for l in je_lines)
    closing_total_credit = sum(Decimal(l["credit"]) for l in je_lines)

    if je_lines:
        # Emit closing entry created + posted events
        emit_event(
            actor=actor,
            event_type=EventTypes.JOURNAL_ENTRY_CREATED,
            aggregate_type="JournalEntry",
            aggregate_id=closing_entry_public_id,
            idempotency_key=f"closing_entry.created:{actor.company.public_id}:{fiscal_year}:{closed_at.isoformat()}",
            data=JournalEntryCreatedData(
                entry_public_id=closing_entry_public_id,
                date=p13.end_date.isoformat(),
                memo=f"Year-end closing entries for FY{fiscal_year}",
                memo_ar=f"قيود إقفال السنة المالية {fiscal_year}",
                kind="CLOSING",
                status="DRAFT",
                period=13,
                currency=actor.company.default_currency,
                exchange_rate="1.0",
                lines=je_lines,
                created_by_id=actor.user.id,
            ).to_dict(),
        )

        # Post the closing entry
        emit_event(
            actor=actor,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=closing_entry_public_id,
            idempotency_key=f"closing_entry.posted:{actor.company.public_id}:{fiscal_year}:{closed_at.isoformat()}",
            data=JournalEntryPostedData(
                entry_public_id=closing_entry_public_id,
                entry_number=str(entry_number),
                date=p13.end_date.isoformat(),
                memo=f"Year-end closing entries for FY{fiscal_year}",
                memo_ar=f"قيود إقفال السنة المالية {fiscal_year}",
                kind="CLOSING",
                period=13,
                total_debit=str(closing_total_debit),
                total_credit=str(closing_total_credit),
                lines=je_lines,
                posted_at=closed_at.isoformat(),
                posted_by_id=actor.user.id,
                posted_by_email=actor.user.email,
                currency=actor.company.default_currency,
                exchange_rate="1.0",
            ).to_dict(),
        )

    # Emit closing entry generated audit event
    emit_event(
        actor=actor,
        event_type=EventTypes.CLOSING_ENTRY_GENERATED,
        aggregate_type="FiscalYear",
        aggregate_id=f"{actor.company.public_id}:{fiscal_year}",
        idempotency_key=f"closing_entry.generated:{actor.company.public_id}:{fiscal_year}:{closed_at.isoformat()}",
        data=ClosingEntryGeneratedData(
            company_public_id=str(actor.company.public_id),
            fiscal_year=fiscal_year,
            entry_public_id=closing_entry_public_id,
            entry_number=entry_number,
            retained_earnings_account_public_id=str(re_account.public_id),
            retained_earnings_account_code=re_account.code,
            net_income=str(net_income),
            total_revenue=str(total_revenue),
            total_expenses=str(total_expenses),
            accounts_closed=len(closing_lines) - (1 if net_income != 0 else 0),
            generated_at=closed_at.isoformat(),
            generated_by_id=actor.user.id,
            generated_by_email=actor.user.email,
        ).to_dict(),
    )

    # === STEP 3: Close all periods including P13 ===
    for period_num in range(1, 14):
        fp = FiscalPeriod.objects.filter(
            company=actor.company, fiscal_year=fiscal_year, period=period_num,
        ).first()
        if fp and fp.status == FiscalPeriod.Status.OPEN:
            emit_event(
                actor=actor,
                event_type=EventTypes.FISCAL_PERIOD_CLOSED,
                aggregate_type="FiscalPeriod",
                aggregate_id=f"{actor.company.public_id}:{fiscal_year}:{period_num}",
                idempotency_key=f"fiscal_period.closed.yearend:{actor.company.public_id}:{fiscal_year}:{period_num}:{closed_at.isoformat()}",
                data=FiscalPeriodClosedData(
                    company_public_id=str(actor.company.public_id),
                    fiscal_year=fiscal_year,
                    period=period_num,
                    closed_at=closed_at.isoformat(),
                    closed_by_id=actor.user.id,
                    closed_by_email=actor.user.email,
                ).to_dict(),
            )

    # === STEP 4: Mark fiscal year as CLOSED ===
    event = emit_event(
        actor=actor,
        event_type=EventTypes.FISCAL_YEAR_CLOSED,
        aggregate_type="FiscalYear",
        aggregate_id=f"{actor.company.public_id}:{fiscal_year}",
        idempotency_key=f"fiscal_year.closed:{actor.company.public_id}:{fiscal_year}:{closed_at.isoformat()}",
        data=FiscalYearClosedData(
            company_public_id=str(actor.company.public_id),
            fiscal_year=fiscal_year,
            retained_earnings_account_public_id=str(re_account.public_id),
            retained_earnings_account_code=re_account.code,
            closing_entry_public_id=closing_entry_public_id,
            net_income=str(net_income),
            total_revenue=str(total_revenue),
            total_expenses=str(total_expenses),
            closed_at=closed_at.isoformat(),
            closed_by_id=actor.user.id,
            closed_by_email=actor.user.email,
            next_year_created=True,
            next_year=fiscal_year + 1,
        ).to_dict(),
    )

    # === STEP 5: Create next fiscal year periods ===
    next_year = fiscal_year + 1
    start_month = actor.company.fiscal_year_start_month or 1
    next_periods = _calculate_period_boundaries(next_year, start_month)

    next_configured_at = timezone.now()
    emit_event(
        actor=actor,
        event_type=EventTypes.FISCAL_PERIODS_CONFIGURED,
        aggregate_type="FiscalPeriod",
        aggregate_id=f"{actor.company.public_id}:{next_year}",
        idempotency_key=f"fiscal_periods.configured.yearend:{actor.company.public_id}:{next_year}:{closed_at.isoformat()}",
        data=FiscalPeriodsConfiguredData(
            company_public_id=str(actor.company.public_id),
            fiscal_year=next_year,
            period_count=13,
            periods=next_periods,
            configured_at=next_configured_at.isoformat(),
            configured_by_id=actor.user.id,
            configured_by_email=actor.user.email,
            previous_period_count=0,
            is_yearend_creation=True,
        ).to_dict(),
    )

    _process_projections(actor.company)

    # Post-close reconciliation: AR/AP tie-out
    tieout_valid, tieout_errors = validate_subledger_tieout(actor.company)

    logger.info(
        "fiscal_year.closed",
        extra={
            "company_id": actor.company.id,
            "fiscal_year": fiscal_year,
            "net_income": str(net_income),
            "closing_entry_public_id": closing_entry_public_id,
            "next_year_created": next_year,
            "tieout_balanced": tieout_valid,
            "user_id": actor.user.id,
        },
    )

    return CommandResult.ok({
        "fiscal_year": fiscal_year,
        "net_income": str(net_income),
        "closing_entry_public_id": closing_entry_public_id,
        "next_year_created": next_year,
        "post_close_tieout": {
            "balanced": tieout_valid,
            "errors": tieout_errors,
        },
    }, event=event)


def run_reconciliation_check(actor: ActorContext) -> CommandResult:
    """
    Run AR/AP subledger tie-out reconciliation.

    Returns a structured report that can be stored in build artifacts
    or displayed in the UI.
    """
    require(actor, "reports.view")

    tieout_valid, tieout_errors = validate_subledger_tieout(actor.company)

    from decimal import Decimal
    from django.db.models import Sum
    from projections.models import AccountBalance, CustomerBalance, VendorBalance

    # Gather balances for the report
    ar_controls = Account.objects.filter(
        company=actor.company,
        role=Account.AccountRole.RECEIVABLE_CONTROL,
    )
    ap_controls = Account.objects.filter(
        company=actor.company,
        role=Account.AccountRole.PAYABLE_CONTROL,
    )

    ar_gl_total = AccountBalance.objects.filter(
        company=actor.company,
        account__in=ar_controls,
    ).aggregate(total=Sum("balance"))["total"] or Decimal("0")

    ar_sub_total = CustomerBalance.objects.filter(
        company=actor.company,
    ).aggregate(total=Sum("balance"))["total"] or Decimal("0")

    ap_gl_total = AccountBalance.objects.filter(
        company=actor.company,
        account__in=ap_controls,
    ).aggregate(total=Sum("balance"))["total"] or Decimal("0")

    ap_sub_total = VendorBalance.objects.filter(
        company=actor.company,
    ).aggregate(total=Sum("balance"))["total"] or Decimal("0")

    return CommandResult.ok({
        "balanced": tieout_valid,
        "errors": tieout_errors,
        "ar_reconciliation": {
            "gl_control_balance": str(ar_gl_total),
            "subledger_total": str(ar_sub_total),
            "difference": str(ar_gl_total - ar_sub_total),
            "balanced": ar_gl_total == ar_sub_total,
        },
        "ap_reconciliation": {
            "gl_control_balance": str(ap_gl_total),
            "subledger_total": str(ap_sub_total),
            "difference": str(ap_gl_total - ap_sub_total),
            "balanced": ap_gl_total == ap_sub_total,
        },
        "checked_at": timezone.now().isoformat(),
    })


@transaction.atomic
def reopen_fiscal_year(
    actor: ActorContext,
    fiscal_year: int,
    reason: str,
) -> CommandResult:
    """
    Reopen a closed fiscal year.

    This reverses the closing entries (creates compensating reversal entries)
    and reopens Period 13. The original closing entries are NEVER deleted.

    Requires:
    - fiscal_year.reopen permission
    - Reason is mandatory
    - Fiscal year must be CLOSED

    Args:
        actor: The actor context
        fiscal_year: Fiscal year to reopen
        reason: Mandatory reason for reopening
    """
    require(actor, "fiscal_year.reopen")

    if not reason or not reason.strip():
        return CommandResult.fail("Reason is required to reopen a fiscal year.")

    from projections.models import FiscalPeriod, FiscalYear as FiscalYearModel

    fy = FiscalYearModel.objects.filter(
        company=actor.company,
        fiscal_year=fiscal_year,
    ).first()
    if not fy:
        return CommandResult.fail(f"Fiscal year {fiscal_year} not found.")
    if fy.status != FiscalYearModel.Status.CLOSED:
        return CommandResult.fail(f"Fiscal year {fiscal_year} is not closed.")

    reopened_at = timezone.now()

    # Find the closing entry to reverse
    original_closing_public_id = fy.retained_earnings_entry_public_id
    reversal_entry_public_id = str(uuid.uuid4())

    if original_closing_public_id:
        # Reverse the closing entry (create a new reversal JE, don't delete)
        original_entry = JournalEntry.objects.filter(
            company=actor.company,
            public_id=original_closing_public_id,
        ).first()

        if original_entry and original_entry.status == JournalEntry.Status.POSTED:
            # Create reversal lines (swap debit/credit)
            original_lines = original_entry.lines.all()
            reversal_lines = []
            for i, line in enumerate(original_lines, 1):
                reversal_lines.append({
                    "line_no": i,
                    "account_public_id": str(line.account.public_id),
                    "account_code": line.account.code,
                    "description": f"Reversal of year-end closing FY{fiscal_year}: {reason}",
                    "debit": str(line.credit),
                    "credit": str(line.debit),
                })

            entry_number = _next_company_sequence(actor.company, "journal_entry")

            # Reopen P13 first so we can post the reversal there
            emit_event(
                actor=actor,
                event_type=EventTypes.FISCAL_PERIOD_OPENED,
                aggregate_type="FiscalPeriod",
                aggregate_id=f"{actor.company.public_id}:{fiscal_year}:13",
                idempotency_key=f"fiscal_period.opened.reopen:{actor.company.public_id}:{fiscal_year}:13:{reopened_at.isoformat()}",
                data=FiscalPeriodOpenedData(
                    company_public_id=str(actor.company.public_id),
                    fiscal_year=fiscal_year,
                    period=13,
                    opened_at=reopened_at.isoformat(),
                    opened_by_id=actor.user.id,
                    opened_by_email=actor.user.email,
                ).to_dict(),
            )

            # Create and post the reversal entry
            p13 = FiscalPeriod.objects.filter(
                company=actor.company, fiscal_year=fiscal_year, period=13,
            ).first()
            reversal_date = p13.end_date.isoformat() if p13 else original_entry.date.isoformat()

            # Compute reversal totals
            reversal_total_debit = sum(Decimal(l.get("debit", "0")) for l in reversal_lines)
            reversal_total_credit = sum(Decimal(l.get("credit", "0")) for l in reversal_lines)

            emit_event(
                actor=actor,
                event_type=EventTypes.JOURNAL_ENTRY_CREATED,
                aggregate_type="JournalEntry",
                aggregate_id=reversal_entry_public_id,
                idempotency_key=f"closing_reversal.created:{actor.company.public_id}:{fiscal_year}:{reopened_at.isoformat()}",
                data=JournalEntryCreatedData(
                    entry_public_id=reversal_entry_public_id,
                    date=reversal_date,
                    memo=f"Reversal of year-end closing FY{fiscal_year} - {reason}",
                    memo_ar=f"عكس قيود إقفال السنة المالية {fiscal_year}",
                    kind="CLOSING",
                    status="DRAFT",
                    period=13,
                    currency=actor.company.default_currency,
                    exchange_rate="1.0",
                    lines=reversal_lines,
                    created_by_id=actor.user.id,
                ).to_dict(),
            )

            emit_event(
                actor=actor,
                event_type=EventTypes.JOURNAL_ENTRY_POSTED,
                aggregate_type="JournalEntry",
                aggregate_id=reversal_entry_public_id,
                idempotency_key=f"closing_reversal.posted:{actor.company.public_id}:{fiscal_year}:{reopened_at.isoformat()}",
                data=JournalEntryPostedData(
                    entry_public_id=reversal_entry_public_id,
                    entry_number=str(entry_number),
                    date=reversal_date,
                    memo=f"Reversal of year-end closing FY{fiscal_year} - {reason}",
                    memo_ar=f"عكس قيود إقفال السنة المالية {fiscal_year}",
                    kind="CLOSING",
                    period=13,
                    total_debit=str(reversal_total_debit),
                    total_credit=str(reversal_total_credit),
                    lines=reversal_lines,
                    posted_at=reopened_at.isoformat(),
                    posted_by_id=actor.user.id,
                    posted_by_email=actor.user.email,
                    currency=actor.company.default_currency,
                    exchange_rate="1.0",
                ).to_dict(),
            )

            # Emit closing entry reversed audit event
            emit_event(
                actor=actor,
                event_type=EventTypes.CLOSING_ENTRY_REVERSED,
                aggregate_type="FiscalYear",
                aggregate_id=f"{actor.company.public_id}:{fiscal_year}",
                idempotency_key=f"closing_entry.reversed:{actor.company.public_id}:{fiscal_year}:{reopened_at.isoformat()}",
                data=ClosingEntryReversedData(
                    company_public_id=str(actor.company.public_id),
                    fiscal_year=fiscal_year,
                    original_entry_public_id=original_closing_public_id,
                    reversal_entry_public_id=reversal_entry_public_id,
                    reason=reason,
                    reversed_at=reopened_at.isoformat(),
                    reversed_by_id=actor.user.id,
                    reversed_by_email=actor.user.email,
                ).to_dict(),
            )

    # Emit fiscal year reopened event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.FISCAL_YEAR_REOPENED,
        aggregate_type="FiscalYear",
        aggregate_id=f"{actor.company.public_id}:{fiscal_year}",
        idempotency_key=f"fiscal_year.reopened:{actor.company.public_id}:{fiscal_year}:{reopened_at.isoformat()}",
        data=FiscalYearReopenedData(
            company_public_id=str(actor.company.public_id),
            fiscal_year=fiscal_year,
            reason=reason,
            reversal_entry_public_id=reversal_entry_public_id,
            reopened_at=reopened_at.isoformat(),
            reopened_by_id=actor.user.id,
            reopened_by_email=actor.user.email,
        ).to_dict(),
    )

    _process_projections(actor.company)

    logger.info(
        "fiscal_year.reopened",
        extra={
            "company_id": actor.company.id,
            "fiscal_year": fiscal_year,
            "reason": reason,
            "reversal_entry_public_id": reversal_entry_public_id,
            "user_id": actor.user.id,
        },
    )

    return CommandResult.ok({
        "fiscal_year": fiscal_year,
        "reversal_entry_public_id": reversal_entry_public_id,
    }, event=event)


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
    dimension_kind: str = "ANALYTIC",
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
        dimension_kind: CONTEXT (business meaning) or ANALYTIC (optional enrichment)
        is_required_on_posting: If True, must be filled when posting
        applies_to_account_types: List of account types, empty = all
        display_order: Order for UI display

    Returns:
        CommandResult with created AnalysisDimension or error
    """
    require(actor, "accounts.manage")

    # Validate dimension_kind
    valid_kinds = {k.value for k in AnalysisDimension.DimensionKind}
    if dimension_kind not in valid_kinds:
        return CommandResult.fail(f"Invalid dimension_kind '{dimension_kind}'. Must be one of: {', '.join(valid_kinds)}")

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
        "dimension_kind": dimension_kind,
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
            dimension_kind=dimension_kind,
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
        "dimension_kind", "is_required_on_posting", "applies_to_account_types",
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

    # Create a short aggregate_id that fits within 64 chars
    # Format: "aad:{hash}" where hash is derived from account+dimension
    aggregate_hash = hashlib.sha256(
        f"{account.public_id}:{dimension.public_id}".encode()
    ).hexdigest()[:32]
    aggregate_id = f"aad:{aggregate_hash}"

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
        aggregate_id=aggregate_id,
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

    # Create a short aggregate_id that fits within 64 chars
    aggregate_hash = hashlib.sha256(
        f"{account.public_id}:{dimension.public_id}".encode()
    ).hexdigest()[:32]
    aggregate_id = f"aad:{aggregate_hash}"

    idempotency_key = _idempotency_hash("account_analysis_default.removed", {
        "account_public_id": str(account.public_id),
        "dimension_public_id": str(dimension.public_id),
    })

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.ACCOUNT_ANALYSIS_DEFAULT_REMOVED,
        aggregate_type="AccountAnalysisDefault",
        aggregate_id=aggregate_id,
        idempotency_key=idempotency_key,
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
    aggregate = load_journal_entry_aggregate(actor.company, str(line.entry.public_id))
    if not aggregate or aggregate.deleted:
        return CommandResult.fail("Journal entry not found.")

    if aggregate.status not in [JournalEntry.Status.INCOMPLETE, JournalEntry.Status.DRAFT]:
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

    # IMPORTANT: Use JournalEntry as aggregate type so this event is included
    # in the journal entry's event stream. This allows load_journal_entry_aggregate()
    # to replay analysis events without a global scan.
    entry_public_id = str(line.entry.public_id)
    event = emit_event(
        actor=actor,
        event_type=EventTypes.JOURNAL_LINE_ANALYSIS_SET,
        aggregate_type="JournalEntry",
        aggregate_id=entry_public_id,
        idempotency_key=f"journal_line.analysis_set:{entry_public_id}:{line.line_no}:{_changes_hash({'tags': {'new': tag_data}})}",
        data=JournalLineAnalysisSetData(
            entry_public_id=entry_public_id,
            line_no=line.line_no,
            analysis_tags=tag_data,
        ).to_dict(),
    )

    _process_projections(actor.company)
    line = JournalLine.objects.get(entry=line.entry, line_no=line.line_no)
    return CommandResult.ok(line, event=event)


# =============================================================================
# Cash Application Commands
# =============================================================================

@transaction.atomic
def record_customer_receipt(
    actor: ActorContext,
    customer_id: int,
    receipt_date: str,
    amount: str,
    bank_account_id: int,
    ar_control_account_id: int,
    reference: str = "",
    memo: str = "",
    allocations: list = None,
) -> CommandResult:
    """
    Record a payment received from a customer.

    Creates a journal entry:
    - Dr Bank Account (amount)
    - Cr AR Control (amount) with customer counterparty

    This will:
    1. Create a posted journal entry
    2. Update account balances
    3. Update customer subledger balance
    4. If allocations provided, update invoice paid amounts

    Args:
        actor: The actor context
        customer_id: ID of the customer making the payment
        receipt_date: Date of receipt (ISO format)
        amount: Payment amount as string
        bank_account_id: ID of bank/cash account to debit
        ar_control_account_id: ID of AR control account to credit
        reference: External reference (check #, wire ref, etc.)
        memo: Optional memo
        allocations: Optional list of invoice allocations. Each allocation is:
            {
                "invoice_public_id": str (UUID),
                "amount": str (decimal amount to apply)
            }

    Returns:
        CommandResult with the journal entry or error
    """
    from events.types import CustomerReceiptRecordedData
    from sales.models import SalesInvoice, ReceiptAllocation
    from projections.write_barrier import command_writes_allowed

    require(actor, "journal.post")

    # Validate customer
    try:
        customer = Customer.objects.get(pk=customer_id, company=actor.company)
    except Customer.DoesNotExist:
        return CommandResult.fail("Customer not found.")

    # Validate bank account
    try:
        bank_account = Account.objects.get(pk=bank_account_id, company=actor.company)
    except Account.DoesNotExist:
        return CommandResult.fail("Bank account not found.")

    if bank_account.account_type != Account.AccountType.ASSET:
        return CommandResult.fail("Bank account must be an Asset account.")

    # Validate AR control account
    try:
        ar_control = Account.objects.get(pk=ar_control_account_id, company=actor.company)
    except Account.DoesNotExist:
        return CommandResult.fail("AR control account not found.")

    if ar_control.account_type != Account.AccountType.ASSET:
        return CommandResult.fail("AR control account must be an Asset account.")

    # Parse and validate amount
    try:
        receipt_amount = Decimal(amount)
    except (ValueError, TypeError):
        return CommandResult.fail("Invalid amount format.")

    if receipt_amount <= 0:
        return CommandResult.fail("Amount must be positive.")

    # Parse date
    from datetime import date as date_cls
    try:
        parsed_date = date_cls.fromisoformat(receipt_date)
    except (ValueError, TypeError):
        return CommandResult.fail("Invalid date format. Use ISO format (YYYY-MM-DD).")

    # Enforce period policy: receipts are operational documents
    allowed, reason = can_post_operational_document(actor, parsed_date)
    if not allowed:
        return CommandResult.fail(reason)

    # Validate allocations if provided
    validated_allocations = []
    total_allocated = Decimal("0")

    if allocations:
        for idx, alloc in enumerate(allocations):
            invoice_public_id = alloc.get("invoice_public_id")
            alloc_amount_str = alloc.get("amount")

            if not invoice_public_id:
                return CommandResult.fail(f"Allocation {idx + 1}: invoice_public_id is required.")
            if not alloc_amount_str:
                return CommandResult.fail(f"Allocation {idx + 1}: amount is required.")

            try:
                alloc_amount = Decimal(alloc_amount_str)
            except (ValueError, TypeError):
                return CommandResult.fail(f"Allocation {idx + 1}: invalid amount format.")

            if alloc_amount <= 0:
                return CommandResult.fail(f"Allocation {idx + 1}: amount must be positive.")

            # Find the invoice
            try:
                invoice = SalesInvoice.objects.get(
                    company=actor.company,
                    public_id=invoice_public_id,
                    customer=customer,
                )
            except SalesInvoice.DoesNotExist:
                return CommandResult.fail(
                    f"Allocation {idx + 1}: Invoice not found or doesn't belong to this customer."
                )

            if invoice.status != SalesInvoice.Status.POSTED:
                return CommandResult.fail(
                    f"Allocation {idx + 1}: Invoice {invoice.invoice_number} is not posted."
                )

            # Check if allocation exceeds amount due
            if alloc_amount > invoice.amount_due:
                return CommandResult.fail(
                    f"Allocation {idx + 1}: Amount {alloc_amount} exceeds invoice "
                    f"{invoice.invoice_number} amount due ({invoice.amount_due})."
                )

            total_allocated += alloc_amount
            validated_allocations.append({
                "invoice": invoice,
                "amount": alloc_amount,
            })

        # Total allocated cannot exceed receipt amount
        if total_allocated > receipt_amount:
            return CommandResult.fail(
                f"Total allocated ({total_allocated}) exceeds receipt amount ({receipt_amount})."
            )

    # Generate receipt public ID
    receipt_public_id = uuid.uuid4()

    # Create journal entry description
    description = f"Receipt from {customer.name}"
    if reference:
        description += f" - Ref: {reference}"

    # Build journal entry
    entry_sequence = _next_company_sequence(actor.company, "journal_entry")
    entry_public_id = uuid.uuid4()

    lines = [
        {
            "account_public_id": str(bank_account.public_id),
            "debit": str(receipt_amount),
            "credit": "0",
            "line_no": 1,
            "memo": memo or f"Customer receipt from {customer.code}",
        },
        {
            "account_public_id": str(ar_control.public_id),
            "debit": "0",
            "credit": str(receipt_amount),
            "line_no": 2,
            "memo": memo or f"Customer receipt from {customer.code}",
            "customer_public_id": str(customer.public_id),
        },
    ]

    # Create the journal entry directly (bypassing save_journal_entry for simplicity)
    # We'll use the existing post_journal_entry flow
    from events.types import JournalEntryPostedData, JournalLineData

    entry_number = f"JE-{actor.company.id}-{entry_sequence:06d}"

    line_data_list = []
    for line in lines:
        # Resolve account_code for the line
        if line["line_no"] == 1:
            line_account_code = bank_account.code
        else:
            line_account_code = ar_control.code
        line_data_list.append(JournalLineData(
            line_no=line["line_no"],
            account_public_id=line["account_public_id"],
            account_code=line_account_code,
            description=line.get("memo", ""),
            debit=line.get("debit", "0"),
            credit=line.get("credit", "0"),
            customer_public_id=line.get("customer_public_id"),
            vendor_public_id=None,
        ))

    posted_at = timezone.now()

    # Emit journal posted event
    journal_event = emit_event(
        actor=actor,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=str(entry_public_id),
        idempotency_key=f"customer_receipt:{receipt_public_id}",
        data=JournalEntryPostedData(
            entry_public_id=str(entry_public_id),
            entry_number=entry_number,
            date=receipt_date,
            memo=description,
            kind=JournalEntry.Kind.NORMAL,
            total_debit=str(receipt_amount),
            total_credit=str(receipt_amount),
            lines=[ld.to_dict() for ld in line_data_list],
            posted_at=posted_at.isoformat(),
            posted_by_id=actor.user.id,
            posted_by_email=actor.user.email,
            currency=actor.company.default_currency,
            exchange_rate="1.0",
        ).to_dict(),
    )

    # Build allocation data for event
    allocation_data = []

    # Create receipt allocations and update invoice paid amounts
    if validated_allocations:
        with command_writes_allowed():
            for alloc in validated_allocations:
                invoice = alloc["invoice"]
                alloc_amount = alloc["amount"]

                # Create allocation record
                ReceiptAllocation.objects.create(
                    company=actor.company,
                    receipt_public_id=receipt_public_id,
                    receipt_date=parsed_date,
                    invoice=invoice,
                    amount=alloc_amount,
                    created_by=actor.user,
                )

                # Update invoice amount_paid
                invoice.amount_paid += alloc_amount
                invoice.save(update_fields=["amount_paid"])

                allocation_data.append({
                    "invoice_public_id": str(invoice.public_id),
                    "invoice_number": invoice.invoice_number,
                    "amount": str(alloc_amount),
                })

    # Emit customer receipt event
    receipt_event = emit_event(
        actor=actor,
        event_type=EventTypes.CUSTOMER_RECEIPT_RECORDED,
        aggregate_type="CustomerReceipt",
        aggregate_id=str(receipt_public_id),
        idempotency_key=f"customer_receipt.recorded:{receipt_public_id}",
        data=CustomerReceiptRecordedData(
            receipt_public_id=str(receipt_public_id),
            company_public_id=str(actor.company.public_id),
            customer_public_id=str(customer.public_id),
            customer_code=customer.code,
            receipt_date=receipt_date,
            amount=str(receipt_amount),
            bank_account_public_id=str(bank_account.public_id),
            bank_account_code=bank_account.code,
            ar_control_account_public_id=str(ar_control.public_id),
            ar_control_account_code=ar_control.code,
            reference=reference,
            memo=memo,
            journal_entry_public_id=str(entry_public_id),
            recorded_at=posted_at.isoformat(),
            recorded_by_id=actor.user.id,
            recorded_by_email=actor.user.email,
            allocations=allocation_data,
        ).to_dict(),
    )

    _process_projections(actor.company)

    # Get the created journal entry
    entry = JournalEntry.objects.get(company=actor.company, public_id=entry_public_id)

    return CommandResult.ok({
        "receipt_public_id": str(receipt_public_id),
        "journal_entry": entry,
        "amount": str(receipt_amount),
        "customer_code": customer.code,
        "allocations": allocation_data,
    }, event=receipt_event)


@transaction.atomic
def record_vendor_payment(
    actor: ActorContext,
    vendor_id: int,
    payment_date: str,
    amount: str,
    bank_account_id: int,
    ap_control_account_id: int,
    reference: str = "",
    memo: str = "",
    allocations: list = None,
) -> CommandResult:
    """
    Record a payment made to a vendor.

    Creates a journal entry:
    - Dr AP Control (amount) with vendor counterparty
    - Cr Bank Account (amount)

    This will:
    1. Create a posted journal entry
    2. Update account balances
    3. Update vendor subledger balance
    4. If allocations provided, record bill payment allocations

    Args:
        actor: The actor context
        vendor_id: ID of the vendor receiving payment
        payment_date: Date of payment (ISO format)
        amount: Payment amount as string
        bank_account_id: ID of bank/cash account to credit
        ap_control_account_id: ID of AP control account to debit
        reference: External reference (check #, wire ref, etc.)
        memo: Optional memo
        allocations: Optional list of bill allocations. Each allocation is:
            {
                "bill_reference": str (vendor's bill/invoice number),
                "amount": str (decimal amount to apply),
                "bill_date": str (optional, ISO date of original bill),
                "bill_amount": str (optional, original bill total)
            }

    Returns:
        CommandResult with the journal entry or error
    """
    from events.types import VendorPaymentRecordedData
    from sales.models import PaymentAllocation
    from projections.write_barrier import command_writes_allowed

    require(actor, "journal.post")

    # Validate vendor
    try:
        vendor = Vendor.objects.get(pk=vendor_id, company=actor.company)
    except Vendor.DoesNotExist:
        return CommandResult.fail("Vendor not found.")

    # Validate bank account
    try:
        bank_account = Account.objects.get(pk=bank_account_id, company=actor.company)
    except Account.DoesNotExist:
        return CommandResult.fail("Bank account not found.")

    if bank_account.account_type != Account.AccountType.ASSET:
        return CommandResult.fail("Bank account must be an Asset account.")

    # Validate AP control account
    try:
        ap_control = Account.objects.get(pk=ap_control_account_id, company=actor.company)
    except Account.DoesNotExist:
        return CommandResult.fail("AP control account not found.")

    if ap_control.account_type != Account.AccountType.LIABILITY:
        return CommandResult.fail("AP control account must be a Liability account.")

    # Parse and validate amount
    try:
        payment_amount = Decimal(amount)
    except (ValueError, TypeError):
        return CommandResult.fail("Invalid amount format.")

    if payment_amount <= 0:
        return CommandResult.fail("Amount must be positive.")

    # Parse date
    from datetime import date as date_cls
    try:
        parsed_date = date_cls.fromisoformat(payment_date)
    except (ValueError, TypeError):
        return CommandResult.fail("Invalid date format. Use ISO format (YYYY-MM-DD).")

    # Enforce period policy: payments are operational documents
    allowed, reason = can_post_operational_document(actor, parsed_date)
    if not allowed:
        return CommandResult.fail(reason)

    # Validate allocations if provided
    validated_allocations = []
    total_allocated = Decimal("0")

    if allocations:
        for idx, alloc in enumerate(allocations):
            bill_reference = alloc.get("bill_reference")
            alloc_amount_str = alloc.get("amount")

            if not bill_reference:
                return CommandResult.fail(f"Allocation {idx + 1}: bill_reference is required.")
            if not alloc_amount_str:
                return CommandResult.fail(f"Allocation {idx + 1}: amount is required.")

            try:
                alloc_amount = Decimal(alloc_amount_str)
            except (ValueError, TypeError):
                return CommandResult.fail(f"Allocation {idx + 1}: invalid amount format.")

            if alloc_amount <= 0:
                return CommandResult.fail(f"Allocation {idx + 1}: amount must be positive.")

            # Parse optional bill_date
            bill_date = None
            if alloc.get("bill_date"):
                try:
                    bill_date = date_cls.fromisoformat(alloc["bill_date"])
                except (ValueError, TypeError):
                    return CommandResult.fail(f"Allocation {idx + 1}: invalid bill_date format.")

            # Parse optional bill_amount
            bill_amount = None
            if alloc.get("bill_amount"):
                try:
                    bill_amount = Decimal(alloc["bill_amount"])
                except (ValueError, TypeError):
                    return CommandResult.fail(f"Allocation {idx + 1}: invalid bill_amount format.")

            total_allocated += alloc_amount
            validated_allocations.append({
                "bill_reference": bill_reference,
                "amount": alloc_amount,
                "bill_date": bill_date,
                "bill_amount": bill_amount,
            })

        # Total allocated cannot exceed payment amount
        if total_allocated > payment_amount:
            return CommandResult.fail(
                f"Total allocated ({total_allocated}) exceeds payment amount ({payment_amount})."
            )

    # Generate payment public ID
    payment_public_id = uuid.uuid4()

    # Create journal entry description
    description = f"Payment to {vendor.name}"
    if reference:
        description += f" - Ref: {reference}"

    # Build journal entry
    entry_sequence = _next_company_sequence(actor.company, "journal_entry")
    entry_public_id = uuid.uuid4()

    lines = [
        {
            "account_public_id": str(ap_control.public_id),
            "debit": str(payment_amount),
            "credit": "0",
            "line_no": 1,
            "memo": memo or f"Vendor payment to {vendor.code}",
            "vendor_public_id": str(vendor.public_id),
        },
        {
            "account_public_id": str(bank_account.public_id),
            "debit": "0",
            "credit": str(payment_amount),
            "line_no": 2,
            "memo": memo or f"Vendor payment to {vendor.code}",
        },
    ]

    # Create the journal entry
    from events.types import JournalEntryPostedData, JournalLineData

    entry_number = f"JE-{actor.company.id}-{entry_sequence:06d}"

    line_data_list = []
    for line in lines:
        # Resolve account_code for the line
        if line["line_no"] == 1:
            line_account_code = ap_control.code
        else:
            line_account_code = bank_account.code
        line_data_list.append(JournalLineData(
            line_no=line["line_no"],
            account_public_id=line["account_public_id"],
            account_code=line_account_code,
            description=line.get("memo", ""),
            debit=line.get("debit", "0"),
            credit=line.get("credit", "0"),
            customer_public_id=None,
            vendor_public_id=line.get("vendor_public_id"),
        ))

    posted_at = timezone.now()

    # Emit journal posted event
    journal_event = emit_event(
        actor=actor,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=str(entry_public_id),
        idempotency_key=f"vendor_payment:{payment_public_id}",
        data=JournalEntryPostedData(
            entry_public_id=str(entry_public_id),
            entry_number=entry_number,
            date=payment_date,
            memo=description,
            kind=JournalEntry.Kind.NORMAL,
            total_debit=str(payment_amount),
            total_credit=str(payment_amount),
            lines=[ld.to_dict() for ld in line_data_list],
            posted_at=posted_at.isoformat(),
            posted_by_id=actor.user.id,
            posted_by_email=actor.user.email,
            currency=actor.company.default_currency,
            exchange_rate="1.0",
        ).to_dict(),
    )

    # Build allocation data for event
    allocation_data = []

    # Create payment allocations
    if validated_allocations:
        with command_writes_allowed():
            for alloc in validated_allocations:
                PaymentAllocation.objects.create(
                    company=actor.company,
                    payment_public_id=payment_public_id,
                    payment_date=parsed_date,
                    vendor=vendor,
                    bill_reference=alloc["bill_reference"],
                    bill_date=alloc["bill_date"],
                    bill_amount=alloc["bill_amount"],
                    amount=alloc["amount"],
                    created_by=actor.user,
                )

                allocation_data.append({
                    "bill_reference": alloc["bill_reference"],
                    "amount": str(alloc["amount"]),
                    "bill_date": alloc["bill_date"].isoformat() if alloc["bill_date"] else None,
                    "bill_amount": str(alloc["bill_amount"]) if alloc["bill_amount"] else None,
                })

    # Emit vendor payment event
    payment_event = emit_event(
        actor=actor,
        event_type=EventTypes.VENDOR_PAYMENT_RECORDED,
        aggregate_type="VendorPayment",
        aggregate_id=str(payment_public_id),
        idempotency_key=f"vendor_payment.recorded:{payment_public_id}",
        data=VendorPaymentRecordedData(
            payment_public_id=str(payment_public_id),
            company_public_id=str(actor.company.public_id),
            vendor_public_id=str(vendor.public_id),
            vendor_code=vendor.code,
            payment_date=payment_date,
            amount=str(payment_amount),
            bank_account_public_id=str(bank_account.public_id),
            bank_account_code=bank_account.code,
            ap_control_account_public_id=str(ap_control.public_id),
            ap_control_account_code=ap_control.code,
            reference=reference,
            memo=memo,
            journal_entry_public_id=str(entry_public_id),
            recorded_at=posted_at.isoformat(),
            recorded_by_id=actor.user.id,
            recorded_by_email=actor.user.email,
            allocations=allocation_data,
        ).to_dict(),
    )

    _process_projections(actor.company)

    # Get the created journal entry
    entry = JournalEntry.objects.get(company=actor.company, public_id=entry_public_id)

    return CommandResult.ok({
        "payment_public_id": str(payment_public_id),
        "journal_entry": entry,
        "amount": str(payment_amount),
        "vendor_code": vendor.code,
        "allocations": allocation_data,
    }, event=payment_event)


# =============================================================================
# Statistical Entry Commands
# =============================================================================


def create_statistical_entry(
    actor: ActorContext,
    account_id: int,
    entry_date: str,
    quantity: str,
    direction: str,
    unit: str,
    memo: str = "",
    memo_ar: str = "",
    source_module: str = "",
    source_document: str = "",
    related_journal_entry_id: int = None,
) -> CommandResult:
    """
    Create a draft statistical entry.

    Statistical entries track non-monetary quantities (headcount, inventory units,
    production hours, etc.) separately from financial accounting.

    Args:
        actor: The actor context
        account_id: ID of the statistical/off-balance account
        entry_date: Date of the entry (ISO format)
        quantity: Positive quantity value
        direction: INCREASE or DECREASE
        unit: Unit of measure (e.g., 'units', 'kg', 'hours')
        memo: Optional description
        memo_ar: Optional Arabic description
        source_module: Module that created this entry
        source_document: Reference to source document
        related_journal_entry_id: Optional related financial journal entry

    Returns:
        CommandResult with the entry public_id or error
    """
    require(actor, "journal.create")

    # Validate account
    try:
        account = Account.objects.get(pk=account_id, company=actor.company)
    except Account.DoesNotExist:
        return CommandResult.fail("Account not found.")

    # Verify it's a statistical or off-balance account
    if account.ledger_domain not in ("STATISTICAL", "OFF_BALANCE"):
        return CommandResult.fail(
            f"Account '{account.code}' is not a statistical or off-balance account. "
            f"Ledger domain is '{account.ledger_domain}'."
        )

    # Parse and validate quantity
    try:
        qty = Decimal(quantity)
    except (ValueError, TypeError):
        return CommandResult.fail("Invalid quantity format.")

    if qty <= 0:
        return CommandResult.fail("Quantity must be positive.")

    # Validate direction
    if direction not in (StatisticalEntry.Direction.INCREASE, StatisticalEntry.Direction.DECREASE):
        return CommandResult.fail(
            f"Direction must be '{StatisticalEntry.Direction.INCREASE}' or "
            f"'{StatisticalEntry.Direction.DECREASE}'."
        )

    # Parse date
    from datetime import date as date_cls
    try:
        parsed_date = date_cls.fromisoformat(entry_date)
    except (ValueError, TypeError):
        return CommandResult.fail("Invalid date format. Use ISO format (YYYY-MM-DD).")

    # Validate related journal entry if provided
    related_je = None
    if related_journal_entry_id:
        try:
            related_je = JournalEntry.objects.get(
                pk=related_journal_entry_id, company=actor.company
            )
        except JournalEntry.DoesNotExist:
            return CommandResult.fail("Related journal entry not found.")

    # Generate public ID
    entry_public_id = uuid.uuid4()

    # Emit the created event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.STATISTICAL_ENTRY_CREATED,
        aggregate_type="StatisticalEntry",
        aggregate_id=str(entry_public_id),
        idempotency_key=f"stat_entry_create:{entry_public_id}",
        data=StatisticalEntryCreatedData(
            entry_public_id=str(entry_public_id),
            company_public_id=str(actor.company.public_id),
            entry_date=entry_date,
            account_public_id=str(account.public_id),
            account_code=account.code,
            quantity=str(qty),
            direction=direction,
            unit=unit,
            memo=memo,
            memo_ar=memo_ar,
            source_module=source_module,
            source_document=source_document,
            related_journal_entry_public_id=str(related_je.public_id) if related_je else None,
            created_by_id=actor.user.id,
            created_by_email=actor.user.email,
        ).to_dict(),
    )

    _process_projections(actor.company)

    return CommandResult.ok({
        "entry_public_id": str(entry_public_id),
    }, event=event)


def update_statistical_entry(
    actor: ActorContext,
    entry_public_id: str,
    entry_date: str = None,
    quantity: str = None,
    direction: str = None,
    unit: str = None,
    memo: str = None,
    memo_ar: str = None,
    source_module: str = None,
    source_document: str = None,
) -> CommandResult:
    """
    Update a draft statistical entry.

    Only draft entries can be updated. Posted entries must be reversed.

    Args:
        actor: The actor context
        entry_public_id: The entry's public ID
        entry_date: New date (optional)
        quantity: New quantity (optional)
        direction: New direction (optional)
        unit: New unit (optional)
        memo: New memo (optional)
        memo_ar: New Arabic memo (optional)
        source_module: New source module (optional)
        source_document: New source document (optional)

    Returns:
        CommandResult with success or error
    """
    require(actor, "journal.edit")

    # Find the entry
    try:
        entry = StatisticalEntry.objects.get(
            public_id=entry_public_id, company=actor.company
        )
    except StatisticalEntry.DoesNotExist:
        return CommandResult.fail("Statistical entry not found.")

    # Check status
    if entry.status != StatisticalEntry.Status.DRAFT:
        return CommandResult.fail(
            f"Cannot update entry with status '{entry.status}'. Only DRAFT entries can be updated."
        )

    # Build changes dict
    changes = {}

    if entry_date is not None:
        from datetime import date as date_cls
        try:
            parsed_date = date_cls.fromisoformat(entry_date)
        except (ValueError, TypeError):
            return CommandResult.fail("Invalid date format. Use ISO format (YYYY-MM-DD).")
        if str(entry.date) != entry_date:
            changes["date"] = {"old": str(entry.date), "new": entry_date}

    if quantity is not None:
        try:
            qty = Decimal(quantity)
        except (ValueError, TypeError):
            return CommandResult.fail("Invalid quantity format.")
        if qty <= 0:
            return CommandResult.fail("Quantity must be positive.")
        if entry.quantity != qty:
            changes["quantity"] = {"old": str(entry.quantity), "new": str(qty)}

    if direction is not None:
        if direction not in (StatisticalEntry.Direction.INCREASE, StatisticalEntry.Direction.DECREASE):
            return CommandResult.fail(
                f"Direction must be '{StatisticalEntry.Direction.INCREASE}' or "
                f"'{StatisticalEntry.Direction.DECREASE}'."
            )
        if entry.direction != direction:
            changes["direction"] = {"old": entry.direction, "new": direction}

    if unit is not None and entry.unit != unit:
        changes["unit"] = {"old": entry.unit, "new": unit}

    if memo is not None and entry.memo != memo:
        changes["memo"] = {"old": entry.memo, "new": memo}

    if memo_ar is not None and entry.memo_ar != memo_ar:
        changes["memo_ar"] = {"old": entry.memo_ar, "new": memo_ar}

    if source_module is not None and entry.source_module != source_module:
        changes["source_module"] = {"old": entry.source_module, "new": source_module}

    if source_document is not None and entry.source_document != source_document:
        changes["source_document"] = {"old": entry.source_document, "new": source_document}

    if not changes:
        return CommandResult.ok({"message": "No changes to apply."})

    # Emit update event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.STATISTICAL_ENTRY_UPDATED,
        aggregate_type="StatisticalEntry",
        aggregate_id=str(entry.public_id),
        idempotency_key=f"stat_entry_update:{entry.public_id}:{_changes_hash(changes)}",
        data=StatisticalEntryUpdatedData(
            entry_public_id=str(entry.public_id),
            company_public_id=str(actor.company.public_id),
            changes=changes,
        ).to_dict(),
    )

    _process_projections(actor.company)

    return CommandResult.ok({
        "entry_public_id": str(entry.public_id),
        "changes": changes,
    }, event=event)


def post_statistical_entry(
    actor: ActorContext,
    entry_public_id: str,
) -> CommandResult:
    """
    Post a statistical entry to finalize it.

    Once posted, an entry cannot be modified, only reversed.

    Args:
        actor: The actor context
        entry_public_id: The entry's public ID

    Returns:
        CommandResult with success or error
    """
    require(actor, "journal.post")

    # Find the entry
    try:
        entry = StatisticalEntry.objects.get(
            public_id=entry_public_id, company=actor.company
        )
    except StatisticalEntry.DoesNotExist:
        return CommandResult.fail("Statistical entry not found.")

    # Check status
    if entry.status != StatisticalEntry.Status.DRAFT:
        return CommandResult.fail(
            f"Cannot post entry with status '{entry.status}'. Only DRAFT entries can be posted."
        )

    posted_at = timezone.now()

    # Emit posted event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.STATISTICAL_ENTRY_POSTED,
        aggregate_type="StatisticalEntry",
        aggregate_id=str(entry.public_id),
        idempotency_key=f"stat_entry_post:{entry.public_id}",
        data=StatisticalEntryPostedData(
            entry_public_id=str(entry.public_id),
            company_public_id=str(actor.company.public_id),
            entry_date=str(entry.date),
            account_public_id=str(entry.account.public_id),
            account_code=entry.account.code,
            quantity=str(entry.quantity),
            direction=entry.direction,
            unit=entry.unit,
            posted_at=posted_at.isoformat(),
            posted_by_id=actor.user.id,
            posted_by_email=actor.user.email,
            memo=entry.memo,
            memo_ar=entry.memo_ar,
            source_module=entry.source_module,
            source_document=entry.source_document,
            related_journal_entry_public_id=str(entry.related_journal_entry.public_id) if entry.related_journal_entry else None,
        ).to_dict(),
    )

    _process_projections(actor.company)

    return CommandResult.ok({
        "entry_public_id": str(entry.public_id),
    }, event=event)


def reverse_statistical_entry(
    actor: ActorContext,
    entry_public_id: str,
    reversal_date: str = None,
) -> CommandResult:
    """
    Reverse a posted statistical entry.

    Creates a new entry with opposite direction to negate the original.

    Args:
        actor: The actor context
        entry_public_id: The entry's public ID to reverse
        reversal_date: Date for the reversal (defaults to today)

    Returns:
        CommandResult with the reversal entry public_id or error
    """
    require(actor, "journal.post")

    # Find the entry
    try:
        entry = StatisticalEntry.objects.get(
            public_id=entry_public_id, company=actor.company
        )
    except StatisticalEntry.DoesNotExist:
        return CommandResult.fail("Statistical entry not found.")

    # Check status
    if entry.status != StatisticalEntry.Status.POSTED:
        return CommandResult.fail(
            f"Cannot reverse entry with status '{entry.status}'. Only POSTED entries can be reversed."
        )

    # Check if already reversed
    if hasattr(entry, 'reversal_entry') and entry.reversal_entry:
        return CommandResult.fail("This entry has already been reversed.")

    # Parse reversal date
    from datetime import date as date_cls
    if reversal_date:
        try:
            parsed_reversal_date = date_cls.fromisoformat(reversal_date)
        except (ValueError, TypeError):
            return CommandResult.fail("Invalid reversal date format. Use ISO format (YYYY-MM-DD).")
    else:
        parsed_reversal_date = date_cls.today()

    # Generate reversal public ID
    reversal_public_id = uuid.uuid4()
    reversed_at = timezone.now()

    # Emit reversed event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.STATISTICAL_ENTRY_REVERSED,
        aggregate_type="StatisticalEntry",
        aggregate_id=str(entry.public_id),
        idempotency_key=f"stat_entry_reverse:{entry.public_id}",
        data=StatisticalEntryReversedData(
            original_entry_public_id=str(entry.public_id),
            reversal_entry_public_id=str(reversal_public_id),
            company_public_id=str(actor.company.public_id),
            reversed_at=reversed_at.isoformat(),
            reversed_by_id=actor.user.id,
            reversed_by_email=actor.user.email,
            reversal_date=str(parsed_reversal_date),
        ).to_dict(),
    )

    _process_projections(actor.company)

    return CommandResult.ok({
        "original_entry_public_id": str(entry.public_id),
        "reversal_entry_public_id": str(reversal_public_id),
    }, event=event)


def delete_statistical_entry(
    actor: ActorContext,
    entry_public_id: str,
) -> CommandResult:
    """
    Delete a draft statistical entry.

    Only draft entries can be deleted. Posted entries must be reversed.

    Args:
        actor: The actor context
        entry_public_id: The entry's public ID

    Returns:
        CommandResult with success or error
    """
    require(actor, "journal.delete")

    # Find the entry
    try:
        entry = StatisticalEntry.objects.get(
            public_id=entry_public_id, company=actor.company
        )
    except StatisticalEntry.DoesNotExist:
        return CommandResult.fail("Statistical entry not found.")

    # Check status
    if entry.status != StatisticalEntry.Status.DRAFT:
        return CommandResult.fail(
            f"Cannot delete entry with status '{entry.status}'. Only DRAFT entries can be deleted. "
            f"Use reversal for posted entries."
        )

    # Emit deleted event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.STATISTICAL_ENTRY_DELETED,
        aggregate_type="StatisticalEntry",
        aggregate_id=str(entry.public_id),
        idempotency_key=f"stat_entry_delete:{entry.public_id}",
        data=StatisticalEntryDeletedData(
            entry_public_id=str(entry.public_id),
            company_public_id=str(actor.company.public_id),
            entry_date=str(entry.date),
            account_code=entry.account.code,
            quantity=str(entry.quantity),
            direction=entry.direction,
            deleted_by_id=actor.user.id,
            deleted_by_email=actor.user.email,
        ).to_dict(),
    )

    _process_projections(actor.company)

    return CommandResult.ok({
        "entry_public_id": str(entry.public_id),
        "deleted": True,
    }, event=event)
