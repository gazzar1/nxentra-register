# events/types.py
"""
Event type definitions for Nxentra.

This module defines THE CANONICAL SCHEMA for all event payloads.
These dataclasses are the CONTRACT, not a "helper". All event emission
MUST use these types, and validation is enforced at emission time.

Each event type defines:
- The event name (used in event_type field)
- The expected data schema (enforced at runtime)
- Documentation of what the event means

Naming Convention: {aggregate}.{action}
Examples:
- account.created
- journal_entry.posted
- analysis_dimension.created

IMPORTANT: Events are a STABLE API
============================================
- Adding optional fields with defaults is safe
- Removing fields breaks projections (requires migration)
- Changing field types breaks projections (requires migration)
- Renaming fields breaks projections (requires migration)

When modifying event schemas:
1. Consider backward compatibility
2. Update all projections that consume the event
3. Consider event versioning for breaking changes
"""

from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union, get_args, get_origin, get_type_hints

# =============================================================================
# Event Validation
# =============================================================================


class InvalidEventPayload(Exception):
    """
    Raised when an event payload fails validation.

    This exception is raised at event emission time when the provided
    data does not match the expected schema for the event type.
    """

    def __init__(self, event_type: str, errors: List[str]):
        self.event_type = event_type
        self.errors = errors
        error_list = "\n  - ".join(errors)
        super().__init__(f"Invalid payload for event '{event_type}':\n  - {error_list}")


def _is_optional_type(type_hint) -> bool:
    """Check if a type hint is Optional[X] (i.e., Union[X, None])."""
    origin = get_origin(type_hint)
    if origin is Union:
        args = get_args(type_hint)
        return type(None) in args
    return False


def _get_inner_type(type_hint):
    """Get the inner type from Optional[X]."""
    origin = get_origin(type_hint)
    if origin is Union:
        args = get_args(type_hint)
        non_none_args = [a for a in args if a is not type(None)]
        if len(non_none_args) == 1:
            return non_none_args[0]
    return type_hint


def validate_event_payload(event_type: str, data: Dict[str, Any]) -> None:
    """
    Validate that a data dict matches the expected schema for an event type.

    This function is called at event emission time to ensure all events
    conform to their defined schemas. It validates:

    1. Required fields are present (fields without defaults)
    2. No unexpected fields are provided (strict schema)
    3. Field types are correct (basic type checking)

    Args:
        event_type: The event type string (e.g., "account.created")
        data: The data dict to validate

    Raises:
        InvalidEventPayload: If validation fails
        ValueError: If event_type has no registered schema
    """
    # Import here to avoid circular import
    from events.types import EVENT_DATA_CLASSES

    data_class = EVENT_DATA_CLASSES.get(event_type)
    if data_class is None:
        raise ValueError(f"No schema registered for event type '{event_type}'. Add a dataclass to EVENT_DATA_CLASSES.")

    errors = []

    # Get field information from the dataclass
    dc_fields = {f.name: f for f in dataclass_fields(data_class)}

    # Get type hints for the dataclass
    try:
        type_hints = get_type_hints(data_class)
    except Exception:
        # Fallback if type hints fail (shouldn't happen normally)
        type_hints = {}

    # Check for required fields (fields without defaults)
    from dataclasses import MISSING

    for field_name, field_info in dc_fields.items():
        required = field_info.default is MISSING and field_info.default_factory is MISSING
        if required and field_name not in data:
            errors.append(f"Missing required field: '{field_name}'")

    # Check for unexpected fields (strict mode)
    expected_fields = set(dc_fields.keys())
    provided_fields = set(data.keys())
    unexpected = provided_fields - expected_fields
    if unexpected:
        errors.append(f"Unexpected fields: {sorted(unexpected)}. Expected: {sorted(expected_fields)}")

    # Basic type validation for provided fields
    for field_name, value in data.items():
        if field_name not in dc_fields:
            continue  # Already reported as unexpected

        type_hint = type_hints.get(field_name)
        if type_hint is None:
            continue

        # Handle Optional types
        if value is None:
            if not _is_optional_type(type_hint):
                errors.append(f"Field '{field_name}' cannot be None (type: {type_hint})")
            continue

        # Get the actual type to check against
        check_type = _get_inner_type(type_hint) if _is_optional_type(type_hint) else type_hint
        origin = get_origin(check_type)

        # Basic type checks (not exhaustive, but catches common errors)
        if origin is list or check_type is list or check_type is List:
            if not isinstance(value, list):
                errors.append(f"Field '{field_name}' must be a list, got {type(value).__name__}")
            else:
                inner = get_args(check_type)
                if inner:
                    inner_type = inner[0]
                    for idx, item in enumerate(value):
                        if inner_type in (dict, Dict) and not isinstance(item, dict):
                            errors.append(f"Field '{field_name}[{idx}]' must be a dict, got {type(item).__name__}")
                        elif inner_type in (str,) and not isinstance(item, str):
                            errors.append(f"Field '{field_name}[{idx}]' must be a string, got {type(item).__name__}")
        elif origin is dict or check_type is dict or check_type is Dict:
            if not isinstance(value, dict):
                errors.append(f"Field '{field_name}' must be a dict, got {type(value).__name__}")
            else:
                key_type, value_type = (get_args(check_type) + (None, None))[:2]
                if key_type is str:
                    for key in value.keys():
                        if not isinstance(key, str):
                            errors.append(f"Field '{field_name}' has non-string key: {key!r}")
                if value_type is not None and value_type not in (Any,):
                    for key, item in value.items():
                        if value_type is dict and not isinstance(item, dict):
                            errors.append(f"Field '{field_name}[{key}]' must be a dict, got {type(item).__name__}")
                        elif value_type is str and not isinstance(item, str):
                            errors.append(f"Field '{field_name}[{key}]' must be a string, got {type(item).__name__}")
        elif check_type is str:
            if not isinstance(value, str):
                errors.append(f"Field '{field_name}' must be a string, got {type(value).__name__}")
        elif check_type is int:
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"Field '{field_name}' must be an int, got {type(value).__name__}")
        elif check_type is bool:
            if not isinstance(value, bool):
                errors.append(f"Field '{field_name}' must be a bool, got {type(value).__name__}")

    # Domain-specific validation for common semantics
    from datetime import date as _date
    from datetime import datetime as _datetime
    from decimal import Decimal, InvalidOperation

    from accounting.models import Account, JournalEntry
    from accounts.models import CompanyMembership

    enum_fields = {
        "account_type": set(Account.AccountType.values),
        "normal_balance": set(Account.NormalBalance.values),
        "kind": set(JournalEntry.Kind.values),
        "role": set(CompanyMembership.Role.values),
    }

    decimal_fields = {
        "debit",
        "credit",
        "amount_currency",
        "exchange_rate",
        "total_debit",
        "total_credit",
        "balance",
        "opening_balance",
        "closing_balance",
        "period_debit",
        "period_credit",
    }

    currency_fields = {
        "currency",
        "base_currency",
        "default_currency",
    }

    date_fields = {"date", "start_date", "end_date", "previous_start_date", "previous_end_date"}
    datetime_fields = {"posted_at", "recorded_at", "occurred_at", "closed_at", "reversed_at", "updated_at"}

    def _validate_scalar(name: str, value: Any) -> None:
        if isinstance(value, dict | list):
            return  # Only validate scalar values
        if name in enum_fields and value is not None:
            if value not in enum_fields[name]:
                errors.append(f"Field '{name}' must be one of {sorted(enum_fields[name])}, got {value!r}")
        if name in decimal_fields and value is not None:
            if isinstance(value, bool):
                errors.append(f"Field '{name}' must be a decimal string, got bool")
            elif isinstance(value, int | Decimal | str):
                try:
                    parsed = Decimal(str(value))
                    if name == "exchange_rate" and parsed <= 0:
                        errors.append(f"Field '{name}' must be > 0, got {value!r}")
                except (InvalidOperation, ValueError):
                    errors.append(f"Field '{name}' must be a decimal string, got {value!r}")
            else:
                errors.append(f"Field '{name}' must be a decimal string, got {type(value).__name__}")
        if name in currency_fields and value is not None:
            if not isinstance(value, str) or len(value) != 3 or not value.isalpha() or value != value.upper():
                errors.append(f"Field '{name}' must be a 3-letter uppercase currency code, got {value!r}")
        if name in date_fields and value is not None:
            if not isinstance(value, str):
                errors.append(f"Field '{name}' must be an ISO date string, got {type(value).__name__}")
            else:
                try:
                    _date.fromisoformat(value)
                except ValueError:
                    errors.append(f"Field '{name}' must be an ISO date string, got {value!r}")
        if name in datetime_fields and value is not None:
            if not isinstance(value, str):
                errors.append(f"Field '{name}' must be an ISO datetime string, got {type(value).__name__}")
            else:
                try:
                    _datetime.fromisoformat(value)
                except ValueError:
                    errors.append(f"Field '{name}' must be an ISO datetime string, got {value!r}")

    def _walk(name: str, value: Any) -> None:
        _validate_scalar(name, value)
        if isinstance(value, dict):
            if {"account_public_id", "account_code", "line_no"} & set(value.keys()):
                if value.get("amount_currency") is not None and not value.get("currency"):
                    errors.append("Line field 'amount_currency' requires a currency code.")
                if value.get("exchange_rate") is not None and not value.get("currency"):
                    errors.append("Line field 'exchange_rate' requires a currency code.")
            for k, v in value.items():
                _walk(k, v)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict | list):
                    _walk(name, item)

    if data.get("exchange_rate") is not None and not data.get("currency"):
        errors.append("Field 'exchange_rate' requires a currency code.")

    for field_name, value in data.items():
        _walk(field_name, value)

    if errors:
        raise InvalidEventPayload(event_type, errors)


# =============================================================================
# Base Event Classes
# =============================================================================


@dataclass
class BaseEventData:
    """Base class for all event data."""

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON storage."""
        result = {}
        for key, value in asdict(self).items():
            if isinstance(value, Decimal):
                result[key] = str(value)
            elif isinstance(value, date | datetime):
                result[key] = value.isoformat()
            elif isinstance(value, list):
                result[key] = [
                    item.to_dict() if hasattr(item, "to_dict") else (dict(item) if isinstance(item, dict) else item)
                    for item in value
                ]
            elif isinstance(value, dict):
                result[key] = value
            else:
                result[key] = value
        return result


@dataclass
class FinancialEventData(BaseEventData):
    """
    Base class for events that will be converted to journal entries
    by a projection (Pattern B: projection creates JE from event).

    New vertical modules should subclass this for any event that
    carries a financial amount and should result in a journal entry.
    Projections can rely on these fields being present.

    Existing events (e.g. RentDuePostedData) are not required to adopt
    this immediately — it is the canonical contract for new modules.
    """

    amount: str = "0"
    currency: str = ""
    transaction_date: str = ""
    document_ref: str = ""


# =============================================================================
# Account Events
# =============================================================================


@dataclass
class AccountCreatedData(BaseEventData):
    """Data for account.created event."""

    account_public_id: str
    code: str
    name: str
    account_type: str
    normal_balance: str
    is_header: bool
    parent_public_id: Optional[str] = None
    name_ar: str = ""
    description: str = ""
    description_ar: str = ""
    unit_of_measure: str = ""  # For MEMO accounts
    account_role: str = ""  # Behavioral role (e.g., RECEIVABLE_CONTROL, PAYABLE_CONTROL)
    ledger_domain: str = "FINANCIAL"  # FINANCIAL, STATISTICAL, or OFF_BALANCE
    allow_manual_posting: bool = True  # False for control accounts by default


@dataclass
class AccountUpdatedData(BaseEventData):
    """Data for account.updated event."""

    account_public_id: str
    changes: Dict[str, Dict[str, Any]]  # {"field": {"old": x, "new": y}}


@dataclass
class AccountDeletedData(BaseEventData):
    """Data for account.deleted event."""

    account_public_id: str
    code: str
    name: str


# =============================================================================
# Journal Entry Events
# =============================================================================


@dataclass
class JournalLineData:
    """Journal line data for embedding in events."""

    line_no: int
    account_public_id: str
    account_code: str
    description: str
    debit: str  # String for JSON safety
    credit: str
    amount_currency: Optional[str] = None
    currency: Optional[str] = None
    exchange_rate: Optional[str] = None
    description_ar: str = ""
    is_memo_line: bool = False
    analysis_tags: List[Dict[str, Any]] = field(default_factory=list)
    # Counterparty for AR/AP subledger
    customer_public_id: Optional[str] = None
    vendor_public_id: Optional[str] = None

    def to_dict(self) -> dict:
        result = {
            "line_no": self.line_no,
            "account_public_id": self.account_public_id,
            "account_code": self.account_code,
            "description": self.description,
            "description_ar": self.description_ar,
            "debit": self.debit,
            "credit": self.credit,
            "amount_currency": self.amount_currency,
            "currency": self.currency,
            "exchange_rate": self.exchange_rate,
            "is_memo_line": self.is_memo_line,
            "analysis_tags": self.analysis_tags,
        }
        # Only include counterparty if set
        if self.customer_public_id:
            result["customer_public_id"] = self.customer_public_id
        if self.vendor_public_id:
            result["vendor_public_id"] = self.vendor_public_id
        return result


@dataclass
class JournalEntryCreatedData(BaseEventData):
    """Data for journal_entry.created event."""

    entry_public_id: str
    date: str  # ISO format
    memo: str
    memo_ar: str = ""
    kind: str = "NORMAL"
    status: str = "INCOMPLETE"
    period: Optional[int] = None
    currency: Optional[str] = None
    exchange_rate: Optional[str] = None
    created_by_id: Optional[int] = None
    lines: List[dict] = field(default_factory=list)


@dataclass
class JournalEntryUpdatedData(BaseEventData):
    """Data for journal_entry.updated event."""

    entry_public_id: str
    changes: Dict[str, Dict[str, Any]]
    lines: Optional[List[dict]] = None


@dataclass
class JournalEntryPostedData(BaseEventData):
    """Data for journal_entry.posted event."""

    entry_public_id: str
    entry_number: str
    date: str
    memo: str
    kind: str
    posted_at: str
    posted_by_id: int
    posted_by_email: str
    total_debit: str
    total_credit: str
    lines: List[dict]  # List of JournalLineData dicts
    period: Optional[int] = None
    currency: Optional[str] = None
    exchange_rate: Optional[str] = None
    memo_ar: str = ""


@dataclass
class JournalEntryReversedData(BaseEventData):
    """Data for journal_entry.reversed event."""

    original_entry_public_id: str
    reversal_entry_public_id: str
    reversed_at: str
    reversed_by_id: int
    reversed_by_email: str


@dataclass
class JournalEntrySavedCompleteData(BaseEventData):
    """Data for journal_entry.saved_complete event."""

    entry_public_id: str
    date: str
    memo: str
    status: str
    line_count: int
    total_debit: str
    total_credit: str
    period: Optional[int] = None
    currency: Optional[str] = None
    exchange_rate: Optional[str] = None
    memo_ar: str = ""
    lines: List[dict] = field(default_factory=list)


@dataclass
class JournalEntryDeletedData(BaseEventData):
    """Data for journal_entry.deleted event."""

    entry_public_id: str
    date: str
    memo: str
    status: str


# =============================================================================
# LEPH Chunked Journal Events
# =============================================================================


@dataclass
class JournalCreatedData(BaseEventData):
    """
    Data for journal.created event (LEPH chunked journals).

    This event represents the creation of a journal entry header without lines.
    Lines are added via JOURNAL_LINES_CHUNK_ADDED events.

    Used by EDIM batch imports for large journal entries.
    """

    journal_entry_id: str  # public_id of the journal entry
    company_public_id: str
    date: str  # ISO format
    memo: str
    currency: str
    origin: str  # 'human' or 'batch'
    batch_id: Optional[str] = None  # EDIM batch public_id if from import
    memo_ar: str = ""
    kind: str = "NORMAL"


@dataclass
class JournalLinesChunkData(BaseEventData):
    """
    Data for journal.lines_chunk_added event (LEPH chunked journals).

    This event represents a chunk of journal lines added to a journal entry.
    Multiple chunks are used for large journal entries (500+ lines).

    The parent JOURNAL_CREATED event is linked via caused_by_event.
    """

    journal_entry_id: str  # public_id of the journal entry
    company_public_id: str
    chunk_index: int  # 0-based chunk index
    total_chunks: int  # Expected total number of chunks
    lines: List[dict]  # Subset of journal lines (JournalLineData format)


@dataclass
class JournalFinalizedData(BaseEventData):
    """
    Data for journal.finalized event (LEPH chunked journals).

    This event marks the completion of a chunked journal entry.
    It contains totals for verification but not the lines themselves
    (lines are in the chunk events).

    After this event, the journal can be posted.
    """

    journal_entry_id: str  # public_id of the journal entry
    company_public_id: str
    total_debit: str
    total_credit: str
    line_count: int
    chunk_count: int
    status: str = "DRAFT"


# =============================================================================
# Period Events
# =============================================================================


@dataclass
class FiscalPeriodClosedData(BaseEventData):
    """Data for fiscal_period.closed event."""

    company_public_id: str
    fiscal_year: int
    period: int
    closed_at: str
    closed_by_id: int
    closed_by_email: str


@dataclass
class FiscalPeriodOpenedData(BaseEventData):
    """Data for fiscal_period.opened event."""

    company_public_id: str
    fiscal_year: int
    period: int
    opened_at: str
    opened_by_id: int
    opened_by_email: str


@dataclass
class FiscalPeriodsConfiguredData(BaseEventData):
    """Data for fiscal_period.configured event."""

    company_public_id: str
    fiscal_year: int
    period_count: int
    periods: List[Dict[str, Any]]
    configured_at: str
    configured_by_id: int
    configured_by_email: str
    previous_period_count: int = 12
    is_yearend_creation: bool = False


@dataclass
class FiscalPeriodRangeSetData(BaseEventData):
    """Data for fiscal_period.range_set event."""

    company_public_id: str
    fiscal_year: int
    open_from_period: int
    open_to_period: int
    set_at: str
    set_by_id: int
    set_by_email: str


@dataclass
class FiscalPeriodCurrentSetData(BaseEventData):
    """Data for fiscal_period.current_set event."""

    company_public_id: str
    fiscal_year: int
    period: int
    set_at: str
    set_by_id: int
    set_by_email: str
    previous_period: Optional[int] = None


@dataclass
class FiscalPeriodDatesUpdatedData(BaseEventData):
    """Data for fiscal_period.dates_updated event."""

    company_public_id: str
    fiscal_year: int
    period: int
    start_date: str
    end_date: str
    previous_start_date: str
    previous_end_date: str
    updated_at: str
    updated_by_id: int
    updated_by_email: str


# =============================================================================
# Fiscal Year Events
# =============================================================================


@dataclass
class ReceiptAllocationData(BaseEventData):
    """Typed allocation for customer receipts."""

    invoice_public_id: str
    invoice_number: str = ""
    amount: str = "0"


@dataclass
class PaymentAllocationData(BaseEventData):
    """Typed allocation for vendor payments."""

    bill_reference: str = ""
    amount: str = "0"
    bill_date: Optional[str] = None
    bill_amount: Optional[str] = None


@dataclass
class FiscalYearCloseReadinessCheckedData(BaseEventData):
    """Data for fiscal_year.close_readiness_checked event (audit trail)."""

    company_public_id: str
    fiscal_year: int
    is_ready: bool
    issues: List[str] = field(default_factory=list)
    checked_at: str = ""
    checked_by_id: Optional[int] = None
    checked_by_email: str = ""


@dataclass
class FiscalYearClosedData(BaseEventData):
    """
    Data for fiscal_year.closed event.

    Emitted when a fiscal year is formally closed. This is the completion
    event after closing entries have been generated and all periods locked.
    """

    company_public_id: str
    fiscal_year: int
    retained_earnings_account_public_id: str
    retained_earnings_account_code: str
    closing_entry_public_id: str
    net_income: str  # Decimal as string
    total_revenue: str
    total_expenses: str
    closed_at: str
    closed_by_id: int
    closed_by_email: str
    next_year_created: bool = True
    next_year: Optional[int] = None


@dataclass
class FiscalYearReopenedData(BaseEventData):
    """
    Data for fiscal_year.reopened event.

    Emitted when a closed fiscal year is reopened. Closing entries are
    reversed (not deleted) to maintain audit trail.
    """

    company_public_id: str
    fiscal_year: int
    reason: str
    reversal_entry_public_id: str
    reopened_at: str
    reopened_by_id: int
    reopened_by_email: str


@dataclass
class ClosingEntryGeneratedData(BaseEventData):
    """
    Data for closing_entry.generated event.

    Emitted when the year-end closing journal entry is created in Period 13.
    This entry zeros out all temporary accounts (Revenue, Expense) to
    retained earnings.
    """

    company_public_id: str
    fiscal_year: int
    entry_public_id: str
    entry_number: int
    retained_earnings_account_public_id: str
    retained_earnings_account_code: str
    net_income: str
    total_revenue: str
    total_expenses: str
    accounts_closed: int  # Number of temporary accounts zeroed
    generated_at: str
    generated_by_id: int
    generated_by_email: str


@dataclass
class ClosingEntryReversedData(BaseEventData):
    """
    Data for closing_entry.reversed event.

    Emitted when closing entries are reversed due to fiscal year reopen.
    The original closing entry is NOT deleted — a compensating reversal is created.
    """

    company_public_id: str
    fiscal_year: int
    original_entry_public_id: str
    reversal_entry_public_id: str
    reason: str
    reversed_at: str
    reversed_by_id: int
    reversed_by_email: str


# =============================================================================
# Analysis Dimension Events
# =============================================================================


@dataclass
class AnalysisDimensionCreatedData(BaseEventData):
    """Data for analysis_dimension.created event."""

    dimension_public_id: str
    code: str
    name: str
    name_ar: str = ""
    description: str = ""
    description_ar: str = ""
    dimension_kind: str = "ANALYTIC"
    is_required_on_posting: bool = False
    applies_to_account_types: List[str] = field(default_factory=list)
    display_order: int = 0


@dataclass
class AnalysisDimensionUpdatedData(BaseEventData):
    """Data for analysis_dimension.updated event."""

    dimension_public_id: str
    changes: Dict[str, Dict[str, Any]]


@dataclass
class AnalysisDimensionDeletedData(BaseEventData):
    """Data for analysis_dimension.deleted event."""

    dimension_public_id: str
    code: str
    name: str


@dataclass
class AnalysisDimensionValueCreatedData(BaseEventData):
    """Data for analysis_dimension_value.created event."""

    value_public_id: str
    dimension_public_id: str
    dimension_code: str
    code: str
    name: str
    name_ar: str = ""
    description: str = ""
    description_ar: str = ""
    parent_public_id: Optional[str] = None


@dataclass
class AnalysisDimensionValueUpdatedData(BaseEventData):
    """Data for analysis_dimension_value.updated event."""

    value_public_id: str
    dimension_public_id: str
    changes: Dict[str, Dict[str, Any]]


@dataclass
class AnalysisDimensionValueDeletedData(BaseEventData):
    """Data for analysis_dimension_value.deleted event."""

    value_public_id: str
    dimension_public_id: str
    code: str
    name: str


# =============================================================================
# Account Analysis Default Events
# =============================================================================


@dataclass
class AccountAnalysisDefaultSetData(BaseEventData):
    """Data for account_analysis_default.set event."""

    account_public_id: str
    account_code: str
    dimension_public_id: str
    dimension_code: str
    value_public_id: str
    value_code: str


@dataclass
class AccountAnalysisDefaultRemovedData(BaseEventData):
    """Data for account_analysis_default.removed event."""

    account_public_id: str
    account_code: str
    dimension_public_id: str
    dimension_code: str


# =============================================================================
# Journal Line Analysis Events
# =============================================================================


@dataclass
class JournalLineAnalysisSetData(BaseEventData):
    """Data for journal_line.analysis_set event."""

    entry_public_id: str
    line_no: int
    analysis_tags: List[Dict[str, Any]]


# =============================================================================
# User/Auth Events
# =============================================================================


@dataclass
class UserRegisteredData(BaseEventData):
    """Data for user.registered event."""

    user_public_id: str
    email: str
    name: str
    company_public_id: str
    company_name: str
    membership_public_id: str


@dataclass
class CompanyCreatedData(BaseEventData):
    """Data for company.created event."""

    company_public_id: str
    name: str
    name_ar: str = ""
    slug: str = ""
    default_currency: str = "USD"
    functional_currency: str = "USD"
    fiscal_year_start_month: int = 1
    is_active: bool = True


@dataclass
class UserLoggedInData(BaseEventData):
    """Data for user.logged_in event."""

    user_public_id: str
    email: str
    ip_address: str = ""
    user_agent: str = ""


@dataclass
class UserLoggedOutData(BaseEventData):
    """Data for user.logged_out event."""

    user_public_id: str
    email: str


@dataclass
class UserCompanySwitchedData(BaseEventData):
    """Data for user.company_switched event."""

    user_public_id: str
    email: str
    from_company_public_id: Optional[str]
    to_company_public_id: str
    to_company_name: str


@dataclass
class UserCreatedData(BaseEventData):
    user_public_id: str
    email: str
    name: str
    phone: str = ""
    created_by_user_public_id: Optional[str] = None


@dataclass
class MembershipCreatedData(BaseEventData):
    membership_public_id: str
    company_public_id: str
    user_public_id: str
    role: str
    is_active: bool = True


@dataclass
class MembershipReactivatedData(BaseEventData):
    membership_public_id: str
    company_public_id: str
    user_public_id: str
    role: str
    reactivated_by_user_public_id: Optional[str] = None


@dataclass
class UserUpdatedData(BaseEventData):
    user_public_id: str
    email: str
    changes: Dict[str, Dict[str, Any]]


@dataclass
class UserPasswordChangedData(BaseEventData):
    user_public_id: str
    email: str
    changed_by_self: bool


@dataclass
class MembershipRoleChangedData(BaseEventData):
    membership_public_id: str
    user_public_id: str
    old_role: str
    new_role: str
    permissions_before: List[str] = field(default_factory=list)
    permissions_after: List[str] = field(default_factory=list)
    permissions_granted: List[str] = field(default_factory=list)
    permissions_revoked: List[str] = field(default_factory=list)
    policy: str = ""


@dataclass
class MembershipDeactivatedData(BaseEventData):
    membership_public_id: str
    user_public_id: str
    company_public_id: str
    user_email: str = ""


@dataclass
class MembershipPermissionsUpdatedData(BaseEventData):
    membership_public_id: str
    user_public_id: str
    old_permissions: List[str] = field(default_factory=list)
    new_permissions: List[str] = field(default_factory=list)
    granted: List[str] = field(default_factory=list)
    revoked: List[str] = field(default_factory=list)
    user_email: str = ""


# =============================================================================
# Email Verification Events
# =============================================================================


@dataclass
class UserEmailVerificationSentData(BaseEventData):
    """Data for user.email_verification_sent event."""

    user_public_id: str
    email: str
    expires_at: str  # ISO datetime
    ip_address: str = ""


@dataclass
class UserEmailVerifiedData(BaseEventData):
    """Data for user.email_verified event."""

    user_public_id: str
    email: str
    verified_at: str  # ISO datetime
    ip_address: str = ""


# =============================================================================
# Admin Approval Events (Beta Gate)
# =============================================================================


@dataclass
class UserApprovalRequestedData(BaseEventData):
    """Data for user.approval_requested event."""

    user_public_id: str
    email: str
    company_public_id: str
    company_name: str


@dataclass
class UserApprovedData(BaseEventData):
    """Data for user.approved event."""

    user_public_id: str
    email: str
    approved_by_public_id: str
    approved_by_email: str
    approved_at: str  # ISO datetime


@dataclass
class UserRejectedData(BaseEventData):
    """Data for user.rejected event."""

    user_public_id: str
    email: str
    rejected_by_public_id: str
    rejected_by_email: str
    reason: str = ""


# =============================================================================
# Permission Events
# =============================================================================


@dataclass
class PermissionGrantedData(BaseEventData):
    """Data for permission.granted event."""

    membership_public_id: str
    user_public_id: str
    user_email: str
    permission_codes: List[str] = field(default_factory=list)
    granted_by_public_id: Optional[str] = None
    granted_by_email: str = ""


@dataclass
class PermissionRevokedData(BaseEventData):
    """Data for permission.revoked event."""

    membership_public_id: str
    user_public_id: str
    user_email: str
    permission_codes: List[str] = field(default_factory=list)
    revoked_by_public_id: Optional[str] = None
    revoked_by_email: str = ""


# =============================================================================
# Company Events
# =============================================================================


@dataclass
class CompanyUpdatedData(BaseEventData):
    """
    Data for company.updated event.

    Emitted when basic company information changes:
    - name, name_ar, slug, is_active

    Example:
        CompanyUpdatedData(
            company_public_id="abc-123",
            changes={
                "name": {"old": "Acme Inc", "new": "Acme Corporation"},
                "slug": {"old": "acme-inc", "new": "acme-corp"},
            }
        )
    """

    company_public_id: str
    changes: Dict[str, Dict[str, Any]]  # {"field": {"old": value, "new": value}}


@dataclass
class CompanySettingsChangedData(BaseEventData):
    """
    Data for company.settings_changed event.

    Emitted when company configuration/settings change:
    - default_currency
    - fiscal_year_start_month
    - timezone, locale, etc. (future)

    Example:
        CompanySettingsChangedData(
            company_public_id="abc-123",
            setting="default_currency",
            old_value="USD",
            new_value="EUR",
        )

    Or for multiple settings at once:
        CompanySettingsChangedData(
            company_public_id="abc-123",
            changes={
                "default_currency": {"old": "USD", "new": "EUR"},
                "fiscal_year_start_month": {"old": 1, "new": 4},
            }
        )
    """

    company_public_id: str
    changes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Alternative: single setting change
    setting: str = ""
    old_value: Any = None
    new_value: Any = None


@dataclass
class CompanyLogoUploadedData(BaseEventData):
    """
    Data for company.logo_uploaded event.

    Emitted when a company logo is uploaded or changed.
    """

    company_public_id: str
    logo_path: str  # Relative path to media root
    old_logo_path: Optional[str] = None  # Previous logo if any


@dataclass
class CompanyLogoDeletedData(BaseEventData):
    """
    Data for company.logo_deleted event.

    Emitted when a company logo is removed.
    """

    company_public_id: str
    deleted_logo_path: str  # Path of the deleted logo


# =============================================================================
# Sales Module Events
# =============================================================================


@dataclass
class ItemCreatedData(BaseEventData):
    """Data for sales.item_created event."""

    item_public_id: str
    company_public_id: str
    code: str
    name: str
    item_type: str
    name_ar: str = ""
    description: str = ""
    sales_account_public_id: Optional[str] = None
    purchase_account_public_id: Optional[str] = None
    default_unit_price: str = "0"
    default_cost: str = "0"
    default_tax_code_public_id: Optional[str] = None


@dataclass
class ItemUpdatedData(BaseEventData):
    """Data for sales.item_updated event."""

    item_public_id: str
    company_public_id: str
    changes: Dict[str, Dict[str, Any]]


@dataclass
class TaxCodeCreatedData(BaseEventData):
    """Data for sales.taxcode_created event."""

    taxcode_public_id: str
    company_public_id: str
    code: str
    name: str
    rate: str
    direction: str
    tax_account_public_id: str
    tax_account_code: str
    name_ar: str = ""
    description: str = ""


@dataclass
class TaxCodeUpdatedData(BaseEventData):
    """Data for sales.taxcode_updated event."""

    taxcode_public_id: str
    company_public_id: str
    changes: Dict[str, Dict[str, Any]]


@dataclass
class PostingProfileCreatedData(BaseEventData):
    """Data for sales.postingprofile_created event."""

    profile_public_id: str
    company_public_id: str
    code: str
    name: str
    profile_type: str
    control_account_public_id: str
    control_account_code: str
    is_default: bool = False
    name_ar: str = ""
    description: str = ""


@dataclass
class PostingProfileUpdatedData(BaseEventData):
    """Data for sales.postingprofile_updated event."""

    profile_public_id: str
    company_public_id: str
    changes: Dict[str, Dict[str, Any]]


@dataclass
class SalesInvoiceLineData:
    """Sales invoice line data for embedding in events."""

    line_no: int
    item_public_id: Optional[str]
    description: str
    quantity: str
    unit_price: str
    discount_amount: str
    tax_code_public_id: Optional[str]
    tax_rate: str
    gross_amount: str
    net_amount: str
    tax_amount: str
    line_total: str
    account_public_id: str
    account_code: str
    dimension_value_public_ids: List[str] = field(default_factory=list)
    description_ar: str = ""

    def to_dict(self) -> dict:
        return {
            "line_no": self.line_no,
            "item_public_id": self.item_public_id,
            "description": self.description,
            "description_ar": self.description_ar,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "discount_amount": self.discount_amount,
            "tax_code_public_id": self.tax_code_public_id,
            "tax_rate": self.tax_rate,
            "gross_amount": self.gross_amount,
            "net_amount": self.net_amount,
            "tax_amount": self.tax_amount,
            "line_total": self.line_total,
            "account_public_id": self.account_public_id,
            "account_code": self.account_code,
            "dimension_value_public_ids": self.dimension_value_public_ids,
        }


@dataclass
class SalesInvoiceCreatedData(BaseEventData):
    """Data for sales.invoice_created event."""

    invoice_public_id: str
    company_public_id: str
    invoice_number: str
    invoice_date: str
    customer_public_id: str
    customer_code: str
    posting_profile_public_id: str
    status: str
    due_date: Optional[str] = None
    reference: str = ""
    notes: str = ""
    subtotal: str = "0"
    total_discount: str = "0"
    total_tax: str = "0"
    total_amount: str = "0"
    lines: List[dict] = field(default_factory=list)
    created_by_id: Optional[int] = None


@dataclass
class SalesInvoiceUpdatedData(BaseEventData):
    """Data for sales.invoice_updated event."""

    invoice_public_id: str
    company_public_id: str
    changes: Dict[str, Dict[str, Any]]
    lines: Optional[List[dict]] = None


@dataclass
class SalesInvoicePostedData(BaseEventData):
    """Data for sales.invoice_posted event."""

    invoice_public_id: str
    company_public_id: str
    invoice_number: str
    invoice_date: str
    customer_public_id: str
    customer_code: str
    posting_profile_public_id: str
    journal_entry_public_id: str
    posted_at: str
    posted_by_id: int
    posted_by_email: str
    subtotal: str
    total_discount: str
    total_tax: str
    total_amount: str
    lines: List[dict]


@dataclass
class SalesInvoiceVoidedData(BaseEventData):
    """Data for sales.invoice_voided event."""

    invoice_public_id: str
    company_public_id: str
    invoice_number: str
    reversing_journal_entry_public_id: str
    voided_at: str
    voided_by_id: int
    voided_by_email: str
    reason: str = ""


# =============================================================================
# Sales Credit Note Events
# =============================================================================


@dataclass
class SalesCreditNoteCreatedData(BaseEventData):
    """Data for sales.credit_note_created event."""

    credit_note_public_id: str
    company_public_id: str
    credit_note_number: str
    credit_note_date: str
    invoice_public_id: str
    invoice_number: str
    customer_public_id: str
    customer_code: str
    reason: str = ""
    total_amount: str = "0"


@dataclass
class SalesCreditNotePostedData(BaseEventData):
    """Data for sales.credit_note_posted event."""

    credit_note_public_id: str
    company_public_id: str
    credit_note_number: str
    credit_note_date: str
    invoice_public_id: str
    invoice_number: str
    customer_public_id: str
    customer_code: str
    journal_entry_public_id: str
    posted_at: str
    posted_by_id: int
    posted_by_email: str
    subtotal: str = "0"
    total_discount: str = "0"
    total_tax: str = "0"
    total_amount: str = "0"
    reason: str = ""


@dataclass
class SalesCreditNoteVoidedData(BaseEventData):
    """Data for sales.credit_note_voided event."""

    credit_note_public_id: str
    company_public_id: str
    credit_note_number: str
    reversing_journal_entry_public_id: str
    voided_at: str
    voided_by_id: int
    voided_by_email: str
    reason: str = ""


# =============================================================================
# Purchases Module Events
# =============================================================================


@dataclass
class PurchaseBillLineData:
    """Purchase bill line data for embedding in events."""

    line_no: int
    item_public_id: Optional[str]
    description: str
    quantity: str
    unit_price: str
    discount_amount: str
    tax_code_public_id: Optional[str]
    tax_rate: str
    gross_amount: str
    net_amount: str
    tax_amount: str
    line_total: str
    account_public_id: str
    account_code: str
    dimension_value_public_ids: List[str] = field(default_factory=list)
    description_ar: str = ""

    def to_dict(self) -> dict:
        return {
            "line_no": self.line_no,
            "item_public_id": self.item_public_id,
            "description": self.description,
            "description_ar": self.description_ar,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "discount_amount": self.discount_amount,
            "tax_code_public_id": self.tax_code_public_id,
            "tax_rate": self.tax_rate,
            "gross_amount": self.gross_amount,
            "net_amount": self.net_amount,
            "tax_amount": self.tax_amount,
            "line_total": self.line_total,
            "account_public_id": self.account_public_id,
            "account_code": self.account_code,
            "dimension_value_public_ids": self.dimension_value_public_ids,
        }


@dataclass
class PurchaseBillCreatedData(BaseEventData):
    """Data for purchases.bill_created event."""

    bill_public_id: str
    company_public_id: str
    bill_number: str
    bill_date: str
    vendor_public_id: str
    vendor_code: str
    posting_profile_public_id: str
    status: str
    due_date: Optional[str] = None
    reference: str = ""
    notes: str = ""
    subtotal: str = "0"
    total_discount: str = "0"
    total_tax: str = "0"
    total_amount: str = "0"
    lines: List[dict] = field(default_factory=list)
    created_by_id: Optional[int] = None


@dataclass
class PurchaseBillUpdatedData(BaseEventData):
    """Data for purchases.bill_updated event."""

    bill_public_id: str
    company_public_id: str
    changes: Dict[str, Dict[str, Any]]
    lines: Optional[List[dict]] = None


@dataclass
class PurchaseBillPostedData(BaseEventData):
    """Data for purchases.bill_posted event."""

    bill_public_id: str
    company_public_id: str
    bill_number: str
    bill_date: str
    vendor_public_id: str
    vendor_code: str
    posting_profile_public_id: str
    journal_entry_public_id: str
    posted_at: str
    posted_by_id: int
    posted_by_email: str
    subtotal: str
    total_discount: str
    total_tax: str
    total_amount: str
    lines: List[dict]


@dataclass
class PurchaseBillVoidedData(BaseEventData):
    """Data for purchases.bill_voided event."""

    bill_public_id: str
    company_public_id: str
    bill_number: str
    reversing_journal_entry_public_id: str
    voided_at: str
    voided_by_id: int
    voided_by_email: str
    reason: str = ""


# =============================================================================
# Purchase Order Events
# =============================================================================


@dataclass
class PurchaseOrderCreatedData(BaseEventData):
    order_public_id: str
    company_public_id: str
    order_number: str
    order_date: str
    vendor_public_id: str
    vendor_code: str
    status: str = "DRAFT"
    total_amount: str = "0"
    expected_delivery_date: Optional[str] = None
    reference: str = ""


@dataclass
class PurchaseOrderUpdatedData(BaseEventData):
    order_public_id: str
    company_public_id: str
    order_number: str


@dataclass
class PurchaseOrderApprovedData(BaseEventData):
    order_public_id: str
    company_public_id: str
    order_number: str
    approved_at: str
    approved_by_id: int
    approved_by_email: str


@dataclass
class PurchaseOrderCancelledData(BaseEventData):
    order_public_id: str
    company_public_id: str
    order_number: str
    reason: str = ""


@dataclass
class PurchaseOrderClosedData(BaseEventData):
    order_public_id: str
    company_public_id: str
    order_number: str


@dataclass
class GoodsReceiptCreatedData(BaseEventData):
    receipt_public_id: str
    company_public_id: str
    receipt_number: str
    receipt_date: str
    order_public_id: str
    order_number: str
    vendor_public_id: str
    warehouse_public_id: str


@dataclass
class GoodsReceiptPostedData(BaseEventData):
    receipt_public_id: str
    company_public_id: str
    receipt_number: str
    receipt_date: str
    order_public_id: str
    order_number: str
    vendor_public_id: str
    warehouse_public_id: str
    posted_at: str
    posted_by_id: int
    posted_by_email: str
    lines: List[dict] = field(default_factory=list)


@dataclass
class GoodsReceiptVoidedData(BaseEventData):
    receipt_public_id: str
    company_public_id: str
    receipt_number: str
    voided_at: str
    voided_by_id: int
    voided_by_email: str
    reason: str = ""


# =============================================================================
# Purchase Credit Note Events
# =============================================================================


@dataclass
class PurchaseCreditNoteCreatedData(BaseEventData):
    """Data for purchases.credit_note_created event."""

    credit_note_public_id: str
    company_public_id: str
    credit_note_number: str
    credit_note_date: str
    bill_public_id: str
    bill_number: str
    vendor_public_id: str
    vendor_code: str
    posting_profile_public_id: str
    reason: str
    total_amount: str = "0"
    subtotal: str = "0"
    total_discount: str = "0"
    total_tax: str = "0"
    reason_notes: str = ""
    lines: List[dict] = field(default_factory=list)
    created_by_id: Optional[int] = None


@dataclass
class PurchaseCreditNotePostedData(BaseEventData):
    """Data for purchases.credit_note_posted event."""

    credit_note_public_id: str
    company_public_id: str
    credit_note_number: str
    credit_note_date: str
    bill_public_id: str
    bill_number: str
    vendor_public_id: str
    vendor_code: str
    posting_profile_public_id: str
    journal_entry_public_id: str
    posted_at: str
    posted_by_id: int
    posted_by_email: str
    subtotal: str = "0"
    total_discount: str = "0"
    total_tax: str = "0"
    total_amount: str = "0"
    lines: List[dict] = field(default_factory=list)


@dataclass
class PurchaseCreditNoteVoidedData(BaseEventData):
    """Data for purchases.credit_note_voided event."""

    credit_note_public_id: str
    company_public_id: str
    credit_note_number: str
    reversing_journal_entry_public_id: str
    voided_at: str
    voided_by_id: int
    voided_by_email: str
    reason: str = ""


# =============================================================================
# Inventory Module Events
# =============================================================================


@dataclass
class StockLedgerEntryData:
    """Stock ledger entry data for embedding in events."""

    item_public_id: str
    warehouse_public_id: str
    qty_delta: str  # String for JSON safety, +IN or -OUT
    unit_cost: str
    value_delta: str
    costing_method_snapshot: str
    source_line_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "item_public_id": self.item_public_id,
            "warehouse_public_id": self.warehouse_public_id,
            "qty_delta": self.qty_delta,
            "unit_cost": self.unit_cost,
            "value_delta": self.value_delta,
            "costing_method_snapshot": self.costing_method_snapshot,
            "source_line_id": self.source_line_id,
        }


@dataclass
class WarehouseCreatedData(BaseEventData):
    """Data for inventory.warehouse_created event."""

    warehouse_public_id: str
    company_public_id: str
    code: str
    name: str
    name_ar: str = ""
    is_default: bool = False
    is_active: bool = True


@dataclass
class WarehouseUpdatedData(BaseEventData):
    """Data for inventory.warehouse_updated event."""

    warehouse_public_id: str
    company_public_id: str
    changes: Dict[str, Dict[str, Any]]


@dataclass
class StockReceivedData(BaseEventData):
    """
    Data for inventory.stock_received event.

    Emitted when stock is received from purchase bill or opening balance.
    Each entry increases inventory qty and value.
    """

    source_type: str  # PURCHASE_BILL, OPENING_BALANCE, ADJUSTMENT
    source_id: str  # Document public_id
    company_public_id: str
    entries: List[dict]  # List of StockLedgerEntryData dicts
    journal_entry_public_id: Optional[str] = None
    posted_at: str = ""
    posted_by_id: Optional[int] = None
    posted_by_email: str = ""


@dataclass
class StockIssuedData(BaseEventData):
    """
    Data for inventory.stock_issued event.

    Emitted when stock is issued for sales invoice.
    Each entry decreases inventory qty and calculates COGS.
    """

    source_type: str  # SALES_INVOICE, ADJUSTMENT
    source_id: str  # Document public_id
    company_public_id: str
    entries: List[dict]  # List of StockLedgerEntryData dicts
    total_cogs: str  # Total cost of goods sold
    journal_entry_public_id: Optional[str] = None
    posted_at: str = ""
    posted_by_id: Optional[int] = None
    posted_by_email: str = ""


@dataclass
class InventoryAdjustedData(BaseEventData):
    """
    Data for inventory.adjusted event.

    Emitted when inventory is manually adjusted (count, write-off, etc).
    Can include both positive and negative adjustments.
    """

    adjustment_public_id: str
    company_public_id: str
    adjustment_date: str
    reason: str
    entries: List[dict]  # List of StockLedgerEntryData dicts
    journal_entry_public_id: str
    adjusted_at: str
    adjusted_by_id: int
    adjusted_by_email: str


@dataclass
class InventoryOpeningBalanceData(BaseEventData):
    """
    Data for inventory.opening_balance event.

    Emitted when opening balances are set for inventory items.
    Dr Inventory, Cr Opening Balance Equity.
    """

    company_public_id: str
    as_of_date: str
    entries: List[dict]  # List of StockLedgerEntryData dicts
    journal_entry_public_id: str
    recorded_at: str
    recorded_by_id: int
    recorded_by_email: str


# =============================================================================
# Invitation Events
# =============================================================================


@dataclass
class InvitationCreatedData(BaseEventData):
    """Data for invitation.created event."""

    invitation_public_id: str
    email: str
    name: str
    primary_company_public_id: str
    role: str
    company_ids: List[int]  # List of company IDs the invitee will have access to
    permission_codes: List[str]  # List of permission codes to grant
    invited_by_public_id: str
    invited_by_email: str
    expires_at: str  # ISO datetime


@dataclass
class InvitationAcceptedData(BaseEventData):
    """Data for invitation.accepted event."""

    invitation_public_id: str
    email: str
    user_public_id: str
    accepted_at: str  # ISO datetime
    membership_public_ids: List[str]  # List of created membership public_ids


@dataclass
class InvitationCancelledData(BaseEventData):
    """Data for invitation.cancelled event."""

    invitation_public_id: str
    email: str
    cancelled_by_public_id: str
    cancelled_by_email: str
    reason: str = ""


# =============================================================================
# Cash Application Events
# =============================================================================


@dataclass
class CustomerReceiptRecordedData(BaseEventData):
    """
    Data for cash.customer_receipt_recorded event.

    Emitted when a payment is received from a customer.
    Reduces the customer's AR balance.
    """

    receipt_public_id: str
    company_public_id: str
    customer_public_id: str
    customer_code: str
    receipt_date: str
    amount: str
    bank_account_public_id: str
    bank_account_code: str
    ar_control_account_public_id: str
    ar_control_account_code: str
    reference: str = ""
    memo: str = ""
    currency: str = ""
    exchange_rate: str = ""
    journal_entry_public_id: str = ""
    recorded_at: str = ""
    recorded_by_id: Optional[int] = None
    recorded_by_email: str = ""
    allocations: Optional[List[Dict[str, Any]]] = None  # Invoice allocations


@dataclass
class VendorPaymentRecordedData(BaseEventData):
    """
    Data for cash.vendor_payment_recorded event.

    Emitted when a payment is made to a vendor.
    Reduces the vendor's AP balance.
    """

    payment_public_id: str
    company_public_id: str
    vendor_public_id: str
    vendor_code: str
    payment_date: str
    amount: str
    bank_account_public_id: str
    bank_account_code: str
    ap_control_account_public_id: str
    ap_control_account_code: str
    reference: str = ""
    memo: str = ""
    currency: str = ""
    exchange_rate: str = ""
    journal_entry_public_id: str = ""
    recorded_at: str = ""
    recorded_by_id: Optional[int] = None
    recorded_by_email: str = ""
    allocations: Optional[List[Dict[str, Any]]] = None  # Bill allocations


# =============================================================================
# Scratchpad Events
# =============================================================================


@dataclass
class ScratchpadBatchCommittedData(BaseEventData):
    """
    Data for scratchpad.batch_committed event.

    Emitted when scratchpad rows are committed to journal entries.
    This is an audit event that links scratchpad rows to the created
    journal entries.
    """

    batch_id: str  # UUID of the commit batch
    group_ids: List[str]  # UUIDs of committed groups
    row_count: int
    journal_entry_public_ids: List[str]  # Resulting JournalEntry public_ids
    committed_at: str
    committed_by_id: int
    committed_by_email: str


# =============================================================================
# Statistical Entry Events
# =============================================================================


@dataclass
class StatisticalEntryCreatedData(BaseEventData):
    """
    Data for statistical.entry_created event.

    Emitted when a draft statistical entry is created.
    Statistical entries track non-monetary quantities (headcount, units, etc).
    """

    entry_public_id: str
    company_public_id: str
    entry_date: str  # ISO date
    account_public_id: str
    account_code: str
    quantity: str  # Decimal as string
    direction: str  # INCREASE or DECREASE
    unit: str  # Unit of measure
    memo: str = ""
    memo_ar: str = ""
    source_module: str = ""
    source_document: str = ""
    related_journal_entry_public_id: Optional[str] = None
    created_by_id: Optional[int] = None
    created_by_email: str = ""


@dataclass
class StatisticalEntryUpdatedData(BaseEventData):
    """
    Data for statistical.entry_updated event.

    Emitted when a draft statistical entry is modified.
    """

    entry_public_id: str
    company_public_id: str
    changes: Dict[str, Dict[str, Any]]  # {"field": {"old": x, "new": y}}


@dataclass
class StatisticalEntryPostedData(BaseEventData):
    """
    Data for statistical.entry_posted event.

    Emitted when a statistical entry is finalized (posted).
    Once posted, it cannot be modified, only reversed.
    """

    entry_public_id: str
    company_public_id: str
    entry_date: str
    account_public_id: str
    account_code: str
    quantity: str
    direction: str
    unit: str
    posted_at: str
    posted_by_id: int
    posted_by_email: str
    memo: str = ""
    memo_ar: str = ""
    source_module: str = ""
    source_document: str = ""
    related_journal_entry_public_id: Optional[str] = None


@dataclass
class StatisticalEntryReversedData(BaseEventData):
    """
    Data for statistical.entry_reversed event.

    Emitted when a posted statistical entry is reversed.
    Creates a new entry with opposite direction.
    """

    original_entry_public_id: str
    reversal_entry_public_id: str
    company_public_id: str
    reversed_at: str
    reversed_by_id: int
    reversed_by_email: str
    reversal_date: str  # Date of the reversal entry


@dataclass
class StatisticalEntryDeletedData(BaseEventData):
    """
    Data for statistical.entry_deleted event.

    Emitted when a draft statistical entry is deleted.
    Posted entries cannot be deleted, only reversed.
    """

    entry_public_id: str
    company_public_id: str
    entry_date: str
    account_code: str
    quantity: str
    direction: str
    deleted_by_id: int
    deleted_by_email: str


# =============================================================================
# Event Type Registry
# =============================================================================


class EventTypes:
    """
    Registry of all event types.

    Naming convention: {aggregate}.{past_tense_verb}
    - account.created (not account.create)
    - journal_entry.posted (not journal_entry.post)
    """

    # Account events
    ACCOUNT_CREATED = "account.created"
    ACCOUNT_UPDATED = "account.updated"
    ACCOUNT_DELETED = "account.deleted"

    # Journal entry events
    JOURNAL_ENTRY_CREATED = "journal_entry.created"
    JOURNAL_ENTRY_UPDATED = "journal_entry.updated"
    JOURNAL_ENTRY_POSTED = "journal_entry.posted"
    JOURNAL_ENTRY_REVERSED = "journal_entry.reversed"
    JOURNAL_ENTRY_SAVED_COMPLETE = "journal_entry.saved_complete"
    JOURNAL_ENTRY_DELETED = "journal_entry.deleted"
    JOURNAL_LINE_ANALYSIS_SET = "journal_line.analysis_set"

    # LEPH chunked journal events (for large batch imports)
    JOURNAL_CREATED = "journal.created"  # Header only
    JOURNAL_LINES_CHUNK_ADDED = "journal.lines_chunk_added"  # Batch of lines
    JOURNAL_FINALIZED = "journal.finalized"  # Completion marker

    # Fiscal period events
    FISCAL_PERIOD_CLOSED = "fiscal_period.closed"
    FISCAL_PERIOD_OPENED = "fiscal_period.opened"
    FISCAL_PERIODS_CONFIGURED = "fiscal_period.configured"
    FISCAL_PERIOD_RANGE_SET = "fiscal_period.range_set"
    FISCAL_PERIOD_CURRENT_SET = "fiscal_period.current_set"
    FISCAL_PERIOD_DATES_UPDATED = "fiscal_period.dates_updated"

    # Analysis dimension events
    ANALYSIS_DIMENSION_CREATED = "analysis_dimension.created"
    ANALYSIS_DIMENSION_UPDATED = "analysis_dimension.updated"
    ANALYSIS_DIMENSION_DELETED = "analysis_dimension.deleted"

    # Analysis dimension value events
    ANALYSIS_DIMENSION_VALUE_CREATED = "analysis_dimension_value.created"
    ANALYSIS_DIMENSION_VALUE_UPDATED = "analysis_dimension_value.updated"
    ANALYSIS_DIMENSION_VALUE_DELETED = "analysis_dimension_value.deleted"

    # Account analysis default events
    ACCOUNT_ANALYSIS_DEFAULT_SET = "account_analysis_default.set"
    ACCOUNT_ANALYSIS_DEFAULT_REMOVED = "account_analysis_default.removed"

    # User/Auth events
    USER_REGISTERED = "user.registered"
    USER_CREATED = "user.created"
    USER_LOGGED_IN = "user.logged_in"
    USER_LOGGED_OUT = "user.logged_out"
    USER_COMPANY_SWITCHED = "user.company_switched"
    USER_UPDATED = "user.updated"
    USER_PASSWORD_CHANGED = "user.password_changed"

    # Email verification events
    USER_EMAIL_VERIFICATION_SENT = "user.email_verification_sent"
    USER_EMAIL_VERIFIED = "user.email_verified"

    # Admin approval events (Beta Gate)
    USER_APPROVAL_REQUESTED = "user.approval_requested"
    USER_APPROVED = "user.approved"
    USER_REJECTED = "user.rejected"

    # Permission events
    PERMISSION_GRANTED = "permission.granted"
    PERMISSION_REVOKED = "permission.revoked"

    # Company events
    COMPANY_CREATED = "company.created"
    COMPANY_UPDATED = "company.updated"
    COMPANY_SETTINGS_CHANGED = "company.settings_changed"
    COMPANY_LOGO_UPLOADED = "company.logo_uploaded"
    COMPANY_LOGO_DELETED = "company.logo_deleted"

    # Membership events
    MEMBERSHIP_CREATED = "membership.created"  # <-- add
    MEMBERSHIP_ROLE_CHANGED = "membership.role_changed"  # optional but recommended
    MEMBERSHIP_DEACTIVATED = "membership.deactivated"  # optional but recommended
    MEMBERSHIP_PERMISSIONS_UPDATED = "membership.permissions_updated"  # optional
    MEMBERSHIP_REACTIVATED = "membership.reactivated"

    # EDIM: Source System events
    EDIM_SOURCE_SYSTEM_CREATED = "edim_source_system.created"
    EDIM_SOURCE_SYSTEM_UPDATED = "edim_source_system.updated"
    EDIM_SOURCE_SYSTEM_DEACTIVATED = "edim_source_system.deactivated"

    # EDIM: Ingestion Batch events
    EDIM_BATCH_STAGED = "edim_batch.staged"
    EDIM_BATCH_MAPPED = "edim_batch.mapped"
    EDIM_BATCH_VALIDATED = "edim_batch.validated"
    EDIM_BATCH_PREVIEWED = "edim_batch.previewed"
    EDIM_BATCH_COMMITTED = "edim_batch.committed"
    EDIM_BATCH_REJECTED = "edim_batch.rejected"

    # EDIM: Mapping Profile events
    EDIM_MAPPING_PROFILE_CREATED = "edim_mapping_profile.created"
    EDIM_MAPPING_PROFILE_UPDATED = "edim_mapping_profile.updated"
    EDIM_MAPPING_PROFILE_ACTIVATED = "edim_mapping_profile.activated"
    EDIM_MAPPING_PROFILE_DEPRECATED = "edim_mapping_profile.deprecated"

    # EDIM: Identity Crosswalk events
    EDIM_CROSSWALK_CREATED = "edim_crosswalk.created"
    EDIM_CROSSWALK_VERIFIED = "edim_crosswalk.verified"
    EDIM_CROSSWALK_REJECTED = "edim_crosswalk.rejected"
    EDIM_CROSSWALK_UPDATED = "edim_crosswalk.updated"

    # Sales module events
    SALES_ITEM_CREATED = "sales.item_created"
    SALES_ITEM_UPDATED = "sales.item_updated"
    SALES_TAXCODE_CREATED = "sales.taxcode_created"
    SALES_TAXCODE_UPDATED = "sales.taxcode_updated"
    SALES_POSTINGPROFILE_CREATED = "sales.postingprofile_created"
    SALES_POSTINGPROFILE_UPDATED = "sales.postingprofile_updated"
    SALES_INVOICE_CREATED = "sales.invoice_created"
    SALES_INVOICE_UPDATED = "sales.invoice_updated"
    SALES_INVOICE_POSTED = "sales.invoice_posted"
    SALES_INVOICE_VOIDED = "sales.invoice_voided"
    SALES_CREDIT_NOTE_CREATED = "sales.credit_note_created"
    SALES_CREDIT_NOTE_POSTED = "sales.credit_note_posted"
    SALES_CREDIT_NOTE_VOIDED = "sales.credit_note_voided"

    # Purchases module events
    PURCHASES_BILL_CREATED = "purchases.bill_created"
    PURCHASES_BILL_UPDATED = "purchases.bill_updated"
    PURCHASES_BILL_POSTED = "purchases.bill_posted"
    PURCHASES_BILL_VOIDED = "purchases.bill_voided"

    # Purchase Order events
    PURCHASES_ORDER_CREATED = "purchases.order_created"
    PURCHASES_ORDER_UPDATED = "purchases.order_updated"
    PURCHASES_ORDER_APPROVED = "purchases.order_approved"
    PURCHASES_ORDER_CANCELLED = "purchases.order_cancelled"
    PURCHASES_ORDER_CLOSED = "purchases.order_closed"

    # Goods Receipt events
    PURCHASES_GOODS_RECEIPT_CREATED = "purchases.goods_receipt_created"
    PURCHASES_GOODS_RECEIPT_POSTED = "purchases.goods_receipt_posted"
    PURCHASES_GOODS_RECEIPT_VOIDED = "purchases.goods_receipt_voided"

    # Purchase Credit Note events
    PURCHASES_CREDIT_NOTE_CREATED = "purchases.credit_note_created"
    PURCHASES_CREDIT_NOTE_POSTED = "purchases.credit_note_posted"
    PURCHASES_CREDIT_NOTE_VOIDED = "purchases.credit_note_voided"

    # Inventory module events
    INVENTORY_WAREHOUSE_CREATED = "inventory.warehouse_created"
    INVENTORY_WAREHOUSE_UPDATED = "inventory.warehouse_updated"
    INVENTORY_STOCK_RECEIVED = "inventory.stock_received"
    INVENTORY_STOCK_ISSUED = "inventory.stock_issued"
    INVENTORY_ADJUSTED = "inventory.adjusted"
    INVENTORY_OPENING_BALANCE = "inventory.opening_balance"

    # Invitation events
    INVITATION_CREATED = "invitation.created"
    INVITATION_ACCEPTED = "invitation.accepted"
    INVITATION_CANCELLED = "invitation.cancelled"

    # Fiscal year events
    FISCAL_YEAR_CLOSE_READINESS_CHECKED = "fiscal_year.close_readiness_checked"
    FISCAL_YEAR_CLOSED = "fiscal_year.closed"
    FISCAL_YEAR_REOPENED = "fiscal_year.reopened"
    CLOSING_ENTRY_GENERATED = "closing_entry.generated"
    CLOSING_ENTRY_REVERSED = "closing_entry.reversed"

    # Cash application events
    CUSTOMER_RECEIPT_RECORDED = "cash.customer_receipt_recorded"
    VENDOR_PAYMENT_RECORDED = "cash.vendor_payment_recorded"

    # Scratchpad events
    SCRATCHPAD_BATCH_COMMITTED = "scratchpad.batch_committed"

    # Statistical entry events
    STATISTICAL_ENTRY_CREATED = "statistical.entry_created"
    STATISTICAL_ENTRY_UPDATED = "statistical.entry_updated"
    STATISTICAL_ENTRY_POSTED = "statistical.entry_posted"
    STATISTICAL_ENTRY_REVERSED = "statistical.entry_reversed"
    STATISTICAL_ENTRY_DELETED = "statistical.entry_deleted"

    # Property management events
    PROPERTY_CREATED = "property.created"
    PROPERTY_UPDATED = "property.updated"
    UNIT_CREATED = "unit.created"
    UNIT_STATUS_CHANGED = "unit.status_changed"
    LESSEE_CREATED = "lessee.created"
    LESSEE_UPDATED = "lessee.updated"
    LEASE_CREATED = "lease.created"
    LEASE_UPDATED = "lease.updated"
    LEASE_ACTIVATED = "lease.activated"
    LEASE_TERMINATED = "lease.terminated"
    LEASE_RENEWED = "lease.renewed"
    RENT_SCHEDULE_GENERATED = "rent.schedule_generated"
    RENT_DUE_POSTED = "rent.due_posted"
    RENT_OVERDUE_DETECTED = "rent.overdue_detected"
    RENT_LINE_WAIVED = "rent.line_waived"
    RENT_PAYMENT_RECEIVED = "rent.payment_received"
    RENT_PAYMENT_ALLOCATED = "rent.payment_allocated"
    RENT_PAYMENT_VOIDED = "rent.payment_voided"
    DEPOSIT_RECEIVED = "deposit.received"
    DEPOSIT_ADJUSTED = "deposit.adjusted"
    DEPOSIT_REFUNDED = "deposit.refunded"
    DEPOSIT_FORFEITED = "deposit.forfeited"
    LEASE_EXPIRY_ALERT = "lease.expiry_alert"
    PROPERTY_EXPENSE_RECORDED = "property.expense_recorded"
    PROPERTY_ACCOUNT_MAPPING_UPDATED = "property.account_mapping_updated"

    # Clinic events
    CLINIC_DOCTOR_CREATED = "clinic.doctor_created"
    CLINIC_PATIENT_CREATED = "clinic.patient_created"
    CLINIC_PATIENT_UPDATED = "clinic.patient_updated"
    CLINIC_VISIT_CREATED = "clinic.visit_created"
    CLINIC_VISIT_COMPLETED = "clinic.visit_completed"
    CLINIC_INVOICE_ISSUED = "clinic.invoice_issued"
    CLINIC_PAYMENT_RECEIVED = "clinic.payment_received"
    CLINIC_PAYMENT_VOIDED = "clinic.payment_voided"

    # Shopify Connector
    SHOPIFY_STORE_CONNECTED = "shopify.store_connected"
    SHOPIFY_STORE_DISCONNECTED = "shopify.store_disconnected"
    SHOPIFY_ORDER_PAID = "shopify.order_paid"
    SHOPIFY_REFUND_CREATED = "shopify.refund_created"
    SHOPIFY_PAYOUT_SETTLED = "shopify.payout_settled"
    SHOPIFY_ORDER_FULFILLED = "shopify.order_fulfilled"
    SHOPIFY_DISPUTE_CREATED = "shopify.dispute_created"
    SHOPIFY_DISPUTE_WON = "shopify.dispute_won"

    # Platform-agnostic commerce events (used by new platform connectors)
    PLATFORM_ORDER_PAID = "platform.order_paid"
    PLATFORM_REFUND_CREATED = "platform.refund_created"
    PLATFORM_PAYOUT_SETTLED = "platform.payout_settled"
    PLATFORM_DISPUTE_CREATED = "platform.dispute_created"
    PLATFORM_FULFILLMENT_CREATED = "platform.fulfillment_created"


# =============================================================================
# Platform-agnostic Event Data Classes
# =============================================================================


@dataclass
class PlatformOrderPaidData(FinancialEventData):
    """
    Generic order-paid event emitted by any platform connector.

    The platform_slug field identifies the source platform (e.g. 'stripe').
    Shopify keeps its own events for backward compatibility; new platforms
    use these generic events.
    """

    platform_slug: str = ""
    platform_order_id: str = ""
    order_number: str = ""
    order_name: str = ""
    subtotal: str = "0"
    total_tax: str = "0"
    total_shipping: str = "0"
    total_discounts: str = "0"
    financial_status: str = ""
    gateway: str = ""
    customer_email: str = ""
    customer_name: str = ""
    line_items: list = field(default_factory=list)


@dataclass
class PlatformRefundCreatedData(FinancialEventData):
    """Generic refund event from any platform connector."""

    platform_slug: str = ""
    platform_refund_id: str = ""
    platform_order_id: str = ""
    order_number: str = ""
    reason: str = ""


@dataclass
class PlatformPayoutSettledData(FinancialEventData):
    """Generic payout/settlement event from any platform connector."""

    platform_slug: str = ""
    platform_payout_id: str = ""
    gross_amount: str = "0"
    fees: str = "0"
    net_amount: str = "0"
    payout_date: str = ""
    platform_status: str = ""


@dataclass
class PlatformDisputeCreatedData(FinancialEventData):
    """Generic chargeback/dispute event from any platform connector."""

    platform_slug: str = ""
    platform_dispute_id: str = ""
    platform_order_id: str = ""
    order_name: str = ""
    dispute_amount: str = "0"
    chargeback_fee: str = "0"
    reason: str = ""
    dispute_status: str = ""


@dataclass
class PlatformFulfillmentCreatedData(FinancialEventData):
    """Generic fulfillment event for COGS recognition."""

    platform_slug: str = ""
    platform_fulfillment_id: str = ""
    platform_order_id: str = ""
    order_name: str = ""
    fulfillment_date: str = ""
    total_cogs: str = "0"
    cogs_lines: list = field(default_factory=list)
    unmatched_skus: list = field(default_factory=list)


# =============================================================================
# Event Type to Data Class Mapping (for validation/documentation)
# =============================================================================

EVENT_DATA_CLASSES = {
    EventTypes.ACCOUNT_CREATED: AccountCreatedData,
    EventTypes.ACCOUNT_UPDATED: AccountUpdatedData,
    EventTypes.ACCOUNT_DELETED: AccountDeletedData,
    EventTypes.JOURNAL_ENTRY_CREATED: JournalEntryCreatedData,
    EventTypes.JOURNAL_ENTRY_UPDATED: JournalEntryUpdatedData,
    EventTypes.JOURNAL_ENTRY_POSTED: JournalEntryPostedData,
    EventTypes.JOURNAL_ENTRY_REVERSED: JournalEntryReversedData,
    EventTypes.JOURNAL_ENTRY_SAVED_COMPLETE: JournalEntrySavedCompleteData,
    EventTypes.JOURNAL_ENTRY_DELETED: JournalEntryDeletedData,
    EventTypes.JOURNAL_LINE_ANALYSIS_SET: JournalLineAnalysisSetData,
    # LEPH chunked journal events
    EventTypes.JOURNAL_CREATED: JournalCreatedData,
    EventTypes.JOURNAL_LINES_CHUNK_ADDED: JournalLinesChunkData,
    EventTypes.JOURNAL_FINALIZED: JournalFinalizedData,
    EventTypes.FISCAL_PERIOD_CLOSED: FiscalPeriodClosedData,
    EventTypes.FISCAL_PERIOD_OPENED: FiscalPeriodOpenedData,
    EventTypes.FISCAL_PERIODS_CONFIGURED: FiscalPeriodsConfiguredData,
    EventTypes.FISCAL_PERIOD_RANGE_SET: FiscalPeriodRangeSetData,
    EventTypes.FISCAL_PERIOD_CURRENT_SET: FiscalPeriodCurrentSetData,
    EventTypes.FISCAL_PERIOD_DATES_UPDATED: FiscalPeriodDatesUpdatedData,
    EventTypes.FISCAL_YEAR_CLOSE_READINESS_CHECKED: FiscalYearCloseReadinessCheckedData,
    EventTypes.FISCAL_YEAR_CLOSED: FiscalYearClosedData,
    EventTypes.FISCAL_YEAR_REOPENED: FiscalYearReopenedData,
    EventTypes.CLOSING_ENTRY_GENERATED: ClosingEntryGeneratedData,
    EventTypes.CLOSING_ENTRY_REVERSED: ClosingEntryReversedData,
    EventTypes.ANALYSIS_DIMENSION_CREATED: AnalysisDimensionCreatedData,
    EventTypes.ANALYSIS_DIMENSION_UPDATED: AnalysisDimensionUpdatedData,
    EventTypes.ANALYSIS_DIMENSION_DELETED: AnalysisDimensionDeletedData,
    EventTypes.ANALYSIS_DIMENSION_VALUE_CREATED: AnalysisDimensionValueCreatedData,
    EventTypes.ANALYSIS_DIMENSION_VALUE_UPDATED: AnalysisDimensionValueUpdatedData,
    EventTypes.ANALYSIS_DIMENSION_VALUE_DELETED: AnalysisDimensionValueDeletedData,
    EventTypes.ACCOUNT_ANALYSIS_DEFAULT_SET: AccountAnalysisDefaultSetData,
    EventTypes.ACCOUNT_ANALYSIS_DEFAULT_REMOVED: AccountAnalysisDefaultRemovedData,
    EventTypes.USER_REGISTERED: UserRegisteredData,
    EventTypes.USER_LOGGED_IN: UserLoggedInData,
    EventTypes.USER_LOGGED_OUT: UserLoggedOutData,
    EventTypes.USER_COMPANY_SWITCHED: UserCompanySwitchedData,
    EventTypes.COMPANY_CREATED: CompanyCreatedData,
    EventTypes.COMPANY_UPDATED: CompanyUpdatedData,
    EventTypes.COMPANY_SETTINGS_CHANGED: CompanySettingsChangedData,
    EventTypes.COMPANY_LOGO_UPLOADED: CompanyLogoUploadedData,
    EventTypes.COMPANY_LOGO_DELETED: CompanyLogoDeletedData,
    EventTypes.PERMISSION_GRANTED: PermissionGrantedData,
    EventTypes.PERMISSION_REVOKED: PermissionRevokedData,
    EventTypes.USER_CREATED: UserCreatedData,
    EventTypes.USER_UPDATED: UserUpdatedData,
    EventTypes.USER_PASSWORD_CHANGED: UserPasswordChangedData,
    EventTypes.MEMBERSHIP_CREATED: MembershipCreatedData,
    EventTypes.MEMBERSHIP_REACTIVATED: MembershipReactivatedData,
    EventTypes.MEMBERSHIP_ROLE_CHANGED: MembershipRoleChangedData,
    EventTypes.MEMBERSHIP_DEACTIVATED: MembershipDeactivatedData,
    EventTypes.MEMBERSHIP_PERMISSIONS_UPDATED: MembershipPermissionsUpdatedData,
    # Email verification events
    EventTypes.USER_EMAIL_VERIFICATION_SENT: UserEmailVerificationSentData,
    EventTypes.USER_EMAIL_VERIFIED: UserEmailVerifiedData,
    # Admin approval events
    EventTypes.USER_APPROVAL_REQUESTED: UserApprovalRequestedData,
    EventTypes.USER_APPROVED: UserApprovedData,
    EventTypes.USER_REJECTED: UserRejectedData,
    # Invitation events
    EventTypes.INVITATION_CREATED: InvitationCreatedData,
    EventTypes.INVITATION_ACCEPTED: InvitationAcceptedData,
    EventTypes.INVITATION_CANCELLED: InvitationCancelledData,
    # Cash application events
    EventTypes.CUSTOMER_RECEIPT_RECORDED: CustomerReceiptRecordedData,
    EventTypes.VENDOR_PAYMENT_RECORDED: VendorPaymentRecordedData,
    # Scratchpad events
    EventTypes.SCRATCHPAD_BATCH_COMMITTED: ScratchpadBatchCommittedData,
    # Statistical entry events
    EventTypes.STATISTICAL_ENTRY_CREATED: StatisticalEntryCreatedData,
    EventTypes.STATISTICAL_ENTRY_UPDATED: StatisticalEntryUpdatedData,
    EventTypes.STATISTICAL_ENTRY_POSTED: StatisticalEntryPostedData,
    EventTypes.STATISTICAL_ENTRY_REVERSED: StatisticalEntryReversedData,
    EventTypes.STATISTICAL_ENTRY_DELETED: StatisticalEntryDeletedData,
    # Sales module events
    EventTypes.SALES_ITEM_CREATED: ItemCreatedData,
    EventTypes.SALES_ITEM_UPDATED: ItemUpdatedData,
    EventTypes.SALES_TAXCODE_CREATED: TaxCodeCreatedData,
    EventTypes.SALES_TAXCODE_UPDATED: TaxCodeUpdatedData,
    EventTypes.SALES_POSTINGPROFILE_CREATED: PostingProfileCreatedData,
    EventTypes.SALES_POSTINGPROFILE_UPDATED: PostingProfileUpdatedData,
    EventTypes.SALES_INVOICE_CREATED: SalesInvoiceCreatedData,
    EventTypes.SALES_INVOICE_UPDATED: SalesInvoiceUpdatedData,
    EventTypes.SALES_INVOICE_POSTED: SalesInvoicePostedData,
    EventTypes.SALES_INVOICE_VOIDED: SalesInvoiceVoidedData,
    EventTypes.SALES_CREDIT_NOTE_CREATED: SalesCreditNoteCreatedData,
    EventTypes.SALES_CREDIT_NOTE_POSTED: SalesCreditNotePostedData,
    EventTypes.SALES_CREDIT_NOTE_VOIDED: SalesCreditNoteVoidedData,
    # Purchases module events
    EventTypes.PURCHASES_BILL_CREATED: PurchaseBillCreatedData,
    EventTypes.PURCHASES_BILL_UPDATED: PurchaseBillUpdatedData,
    EventTypes.PURCHASES_BILL_POSTED: PurchaseBillPostedData,
    EventTypes.PURCHASES_BILL_VOIDED: PurchaseBillVoidedData,
    EventTypes.PURCHASES_ORDER_CREATED: PurchaseOrderCreatedData,
    EventTypes.PURCHASES_ORDER_UPDATED: PurchaseOrderUpdatedData,
    EventTypes.PURCHASES_ORDER_APPROVED: PurchaseOrderApprovedData,
    EventTypes.PURCHASES_ORDER_CANCELLED: PurchaseOrderCancelledData,
    EventTypes.PURCHASES_ORDER_CLOSED: PurchaseOrderClosedData,
    EventTypes.PURCHASES_GOODS_RECEIPT_CREATED: GoodsReceiptCreatedData,
    EventTypes.PURCHASES_GOODS_RECEIPT_POSTED: GoodsReceiptPostedData,
    EventTypes.PURCHASES_GOODS_RECEIPT_VOIDED: GoodsReceiptVoidedData,
    EventTypes.PURCHASES_CREDIT_NOTE_CREATED: PurchaseCreditNoteCreatedData,
    EventTypes.PURCHASES_CREDIT_NOTE_POSTED: PurchaseCreditNotePostedData,
    EventTypes.PURCHASES_CREDIT_NOTE_VOIDED: PurchaseCreditNoteVoidedData,
    # Inventory module events
    EventTypes.INVENTORY_WAREHOUSE_CREATED: WarehouseCreatedData,
    EventTypes.INVENTORY_WAREHOUSE_UPDATED: WarehouseUpdatedData,
    EventTypes.INVENTORY_STOCK_RECEIVED: StockReceivedData,
    EventTypes.INVENTORY_STOCK_ISSUED: StockIssuedData,
    EventTypes.INVENTORY_ADJUSTED: InventoryAdjustedData,
    EventTypes.INVENTORY_OPENING_BALANCE: InventoryOpeningBalanceData,
    # Platform-agnostic commerce events
    EventTypes.PLATFORM_ORDER_PAID: PlatformOrderPaidData,
    EventTypes.PLATFORM_REFUND_CREATED: PlatformRefundCreatedData,
    EventTypes.PLATFORM_PAYOUT_SETTLED: PlatformPayoutSettledData,
    EventTypes.PLATFORM_DISPUTE_CREATED: PlatformDisputeCreatedData,
    EventTypes.PLATFORM_FULFILLMENT_CREATED: PlatformFulfillmentCreatedData,
}

# =============================================================================
# Vertical module event registration
# =============================================================================
# EDIM and Property event types are now registered declaratively via
# their AppConfig.event_types_module + REGISTERED_EVENTS convention.
# See ProjectionsConfig.ready() in projections/apps.py for the discovery
# mechanism. The legacy _register_*_events() functions have been removed.
