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

from dataclasses import dataclass, asdict, field, fields as dataclass_fields
from typing import Optional, List, Dict, Any, Type, get_type_hints, get_origin, get_args, Union
from decimal import Decimal
from datetime import date, datetime
import inspect


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
        super().__init__(
            f"Invalid payload for event '{event_type}':\n  - {error_list}"
        )


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
        raise ValueError(
            f"No schema registered for event type '{event_type}'. "
            f"Add a dataclass to EVENT_DATA_CLASSES."
        )

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
        required = (
            field_info.default is MISSING and
            field_info.default_factory is MISSING
        )
        if required and field_name not in data:
            errors.append(f"Missing required field: '{field_name}'")

    # Check for unexpected fields (strict mode)
    expected_fields = set(dc_fields.keys())
    provided_fields = set(data.keys())
    unexpected = provided_fields - expected_fields
    if unexpected:
        errors.append(
            f"Unexpected fields: {sorted(unexpected)}. "
            f"Expected: {sorted(expected_fields)}"
        )

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
                errors.append(
                    f"Field '{field_name}' cannot be None (type: {type_hint})"
                )
            continue

        # Get the actual type to check against
        check_type = _get_inner_type(type_hint) if _is_optional_type(type_hint) else type_hint
        origin = get_origin(check_type)

        # Basic type checks (not exhaustive, but catches common errors)
        if origin is list or check_type is list or check_type is List:
            if not isinstance(value, list):
                errors.append(
                    f"Field '{field_name}' must be a list, got {type(value).__name__}"
                )
            else:
                inner = get_args(check_type)
                if inner:
                    inner_type = inner[0]
                    for idx, item in enumerate(value):
                        if inner_type in (dict, Dict) and not isinstance(item, dict):
                            errors.append(
                                f"Field '{field_name}[{idx}]' must be a dict, got {type(item).__name__}"
                            )
                        elif inner_type in (str,) and not isinstance(item, str):
                            errors.append(
                                f"Field '{field_name}[{idx}]' must be a string, got {type(item).__name__}"
                            )
        elif origin is dict or check_type is dict or check_type is Dict:
            if not isinstance(value, dict):
                errors.append(
                    f"Field '{field_name}' must be a dict, got {type(value).__name__}"
                )
            else:
                key_type, value_type = (get_args(check_type) + (None, None))[:2]
                if key_type is str:
                    for key in value.keys():
                        if not isinstance(key, str):
                            errors.append(
                                f"Field '{field_name}' has non-string key: {key!r}"
                            )
                if value_type is not None and value_type not in (Any,):
                    for key, item in value.items():
                        if value_type is dict and not isinstance(item, dict):
                            errors.append(
                                f"Field '{field_name}[{key}]' must be a dict, got {type(item).__name__}"
                            )
                        elif value_type is str and not isinstance(item, str):
                            errors.append(
                                f"Field '{field_name}[{key}]' must be a string, got {type(item).__name__}"
                            )
        elif check_type is str:
            if not isinstance(value, str):
                errors.append(
                    f"Field '{field_name}' must be a string, got {type(value).__name__}"
                )
        elif check_type is int:
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(
                    f"Field '{field_name}' must be an int, got {type(value).__name__}"
                )
        elif check_type is bool:
            if not isinstance(value, bool):
                errors.append(
                    f"Field '{field_name}' must be a bool, got {type(value).__name__}"
                )

    # Domain-specific validation for common semantics
    from decimal import Decimal, InvalidOperation
    from datetime import date as _date, datetime as _datetime
    from accounts.models import CompanyMembership
    from accounting.models import Account, JournalEntry

    enum_fields = {
        "account_type": set(Account.AccountType.values),
        "normal_balance": set(Account.NormalBalance.values),
        "status": set(JournalEntry.Status.values),
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
        if isinstance(value, (dict, list)):
            return  # Only validate scalar values
        if name in enum_fields and value is not None:
            if value not in enum_fields[name]:
                errors.append(
                    f"Field '{name}' must be one of {sorted(enum_fields[name])}, got {value!r}"
                )
        if name in decimal_fields and value is not None:
            if isinstance(value, bool):
                errors.append(f"Field '{name}' must be a decimal string, got bool")
            elif isinstance(value, (int, Decimal, str)):
                try:
                    parsed = Decimal(str(value))
                    if name == "exchange_rate" and parsed <= 0:
                        errors.append(f"Field '{name}' must be > 0, got {value!r}")
                except (InvalidOperation, ValueError):
                    errors.append(f"Field '{name}' must be a decimal string, got {value!r}")
            else:
                errors.append(f"Field '{name}' must be a decimal string, got {type(value).__name__}")
        if name in currency_fields and value is not None:
            if (
                not isinstance(value, str)
                or len(value) != 3
                or not value.isalpha()
                or value != value.upper()
            ):
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
                if isinstance(item, (dict, list)):
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
            elif isinstance(value, (date, datetime)):
                result[key] = value.isoformat()
            elif isinstance(value, list):
                result[key] = [
                    item.to_dict() if hasattr(item, 'to_dict') else 
                    (dict(item) if isinstance(item, dict) else item)
                    for item in value
                ]
            elif isinstance(value, dict):
                result[key] = value
            else:
                result[key] = value
        return result


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
    
    def to_dict(self) -> dict:
        return {
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
    JOURNAL_CREATED = "journal.created"                    # Header only
    JOURNAL_LINES_CHUNK_ADDED = "journal.lines_chunk_added"  # Batch of lines
    JOURNAL_FINALIZED = "journal.finalized"                # Completion marker

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
    MEMBERSHIP_DEACTIVATED = "membership.deactivated"    # optional but recommended
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
}

# =============================================================================
# EDIM Event Data Classes (imported separately to avoid circular deps)
# =============================================================================

def _register_edim_events():
    """Register EDIM event data classes. Called at module load."""
    from edim.event_types import (
        EdimSourceSystemCreatedData,
        EdimSourceSystemUpdatedData,
        EdimSourceSystemDeactivatedData,
        EdimBatchStagedData,
        EdimBatchMappedData,
        EdimBatchValidatedData,
        EdimBatchPreviewedData,
        EdimBatchCommittedData,
        EdimBatchRejectedData,
        EdimMappingProfileCreatedData,
        EdimMappingProfileUpdatedData,
        EdimMappingProfileActivatedData,
        EdimMappingProfileDeprecatedData,
        EdimCrosswalkCreatedData,
        EdimCrosswalkVerifiedData,
        EdimCrosswalkRejectedData,
        EdimCrosswalkUpdatedData,
    )

    EVENT_DATA_CLASSES.update({
        EventTypes.EDIM_SOURCE_SYSTEM_CREATED: EdimSourceSystemCreatedData,
        EventTypes.EDIM_SOURCE_SYSTEM_UPDATED: EdimSourceSystemUpdatedData,
        EventTypes.EDIM_SOURCE_SYSTEM_DEACTIVATED: EdimSourceSystemDeactivatedData,
        EventTypes.EDIM_BATCH_STAGED: EdimBatchStagedData,
        EventTypes.EDIM_BATCH_MAPPED: EdimBatchMappedData,
        EventTypes.EDIM_BATCH_VALIDATED: EdimBatchValidatedData,
        EventTypes.EDIM_BATCH_PREVIEWED: EdimBatchPreviewedData,
        EventTypes.EDIM_BATCH_COMMITTED: EdimBatchCommittedData,
        EventTypes.EDIM_BATCH_REJECTED: EdimBatchRejectedData,
        EventTypes.EDIM_MAPPING_PROFILE_CREATED: EdimMappingProfileCreatedData,
        EventTypes.EDIM_MAPPING_PROFILE_UPDATED: EdimMappingProfileUpdatedData,
        EventTypes.EDIM_MAPPING_PROFILE_ACTIVATED: EdimMappingProfileActivatedData,
        EventTypes.EDIM_MAPPING_PROFILE_DEPRECATED: EdimMappingProfileDeprecatedData,
        EventTypes.EDIM_CROSSWALK_CREATED: EdimCrosswalkCreatedData,
        EventTypes.EDIM_CROSSWALK_VERIFIED: EdimCrosswalkVerifiedData,
        EventTypes.EDIM_CROSSWALK_REJECTED: EdimCrosswalkRejectedData,
        EventTypes.EDIM_CROSSWALK_UPDATED: EdimCrosswalkUpdatedData,
    })

# Try to register EDIM events (may fail during initial migration)
try:
    _register_edim_events()
except ImportError:
    pass  # EDIM app not yet installed
