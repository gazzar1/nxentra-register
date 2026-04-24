# properties/event_types.py
"""
Property module event data classes.

These define the canonical schema for property management events.
They follow the BaseEventData pattern from events/types.py.

Exposes REGISTERED_EVENTS dict consumed by ProjectionsConfig.ready()
for automatic event-type registration.
"""

from dataclasses import dataclass, field
from typing import Any

from events.types import BaseEventData, EventTypes

# =============================================================================
# Property Events
# =============================================================================


@dataclass
class PropertyCreatedData(BaseEventData):
    """Data for property.created event."""

    property_public_id: str
    company_public_id: str
    code: str
    name: str
    name_ar: str = ""
    property_type: str = ""
    status: str = "active"
    city: str = ""
    region: str = ""
    country: str = "SA"
    created_by_email: str = ""


@dataclass
class PropertyUpdatedData(BaseEventData):
    """Data for property.updated event."""

    property_public_id: str
    company_public_id: str
    changes: dict[str, dict[str, Any]] = field(default_factory=dict)
    updated_by_email: str = ""


# =============================================================================
# Unit Events
# =============================================================================


@dataclass
class UnitCreatedData(BaseEventData):
    """Data for unit.created event."""

    unit_public_id: str
    property_public_id: str
    company_public_id: str
    unit_code: str
    unit_type: str
    floor: str = ""
    status: str = "vacant"
    default_rent: str = ""
    created_by_email: str = ""


@dataclass
class UnitStatusChangedData(BaseEventData):
    """Data for unit.status_changed event."""

    unit_public_id: str
    property_public_id: str
    company_public_id: str
    old_status: str = ""
    new_status: str = ""
    reason: str = ""
    changed_by_email: str = ""


# =============================================================================
# Lessee Events
# =============================================================================


@dataclass
class LesseeCreatedData(BaseEventData):
    """Data for lessee.created event."""

    lessee_public_id: str
    company_public_id: str
    code: str
    display_name: str
    lessee_type: str
    status: str = "active"
    created_by_email: str = ""


@dataclass
class LesseeUpdatedData(BaseEventData):
    """Data for lessee.updated event."""

    lessee_public_id: str
    company_public_id: str
    changes: dict[str, dict[str, Any]] = field(default_factory=dict)
    updated_by_email: str = ""


# =============================================================================
# Lease Events
# =============================================================================


@dataclass
class LeaseCreatedData(BaseEventData):
    """Data for lease.created event."""

    lease_public_id: str
    company_public_id: str
    contract_no: str
    property_public_id: str
    unit_public_id: str = ""
    lessee_public_id: str = ""
    start_date: str = ""
    end_date: str = ""
    rent_amount: str = ""
    currency: str = ""
    payment_frequency: str = ""
    deposit_amount: str = "0"
    created_by_email: str = ""


@dataclass
class LeaseUpdatedData(BaseEventData):
    """Data for lease.updated event."""

    lease_public_id: str
    changes: dict[str, dict[str, Any]] = field(default_factory=dict)
    updated_by_email: str = ""


@dataclass
class LeaseActivatedData(BaseEventData):
    """Data for lease.activated event."""

    lease_public_id: str
    contract_no: str
    property_public_id: str
    unit_public_id: str = ""
    lessee_public_id: str = ""
    start_date: str = ""
    end_date: str = ""
    rent_amount: str = ""
    currency: str = ""
    deposit_amount: str = "0"
    payment_frequency: str = ""
    schedule_line_count: int = 0
    activated_by_email: str = ""
    activated_at: str = ""


@dataclass
class LeaseTerminatedData(BaseEventData):
    """Data for lease.terminated event."""

    lease_public_id: str
    contract_no: str
    property_public_id: str
    unit_public_id: str = ""
    lessee_public_id: str = ""
    termination_reason: str = ""
    terminated_by_email: str = ""
    terminated_at: str = ""


@dataclass
class LeaseRenewedData(BaseEventData):
    """Data for lease.renewed event (old lease)."""

    lease_public_id: str
    contract_no: str
    new_lease_public_id: str = ""
    new_contract_no: str = ""
    renewed_by_email: str = ""


# =============================================================================
# Rent Schedule Events
# =============================================================================


@dataclass
class RentScheduleGeneratedData(BaseEventData):
    """Data for rent.schedule_generated event."""

    lease_public_id: str
    contract_no: str
    schedule_line_count: int = 0
    total_rent: str = "0"
    currency: str = ""
    first_due_date: str = ""
    last_due_date: str = ""


@dataclass
class RentDuePostedData(BaseEventData):
    """Data for rent.due_posted event."""

    schedule_line_public_id: str
    lease_public_id: str
    contract_no: str
    installment_no: int = 0
    due_date: str = ""
    total_due: str = "0"
    currency: str = ""


@dataclass
class RentOverdueDetectedData(BaseEventData):
    """Data for rent.overdue_detected event."""

    schedule_line_public_id: str
    lease_public_id: str
    contract_no: str
    installment_no: int = 0
    due_date: str = ""
    outstanding: str = "0"
    currency: str = ""
    days_overdue: int = 0


@dataclass
class RentLineWaivedData(BaseEventData):
    """Data for rent.line_waived event."""

    schedule_line_public_id: str
    lease_public_id: str
    contract_no: str
    installment_no: int = 0
    waived_amount: str = "0"
    reason: str = ""
    waived_by_email: str = ""


# =============================================================================
# Payment Events
# =============================================================================


@dataclass
class RentPaymentReceivedData(BaseEventData):
    """Data for rent.payment_received event."""

    payment_public_id: str
    lease_public_id: str
    lessee_public_id: str
    receipt_no: str = ""
    amount: str = "0"
    currency: str = ""
    payment_method: str = ""
    payment_date: str = ""
    received_by_email: str = ""


@dataclass
class RentPaymentAllocatedData(BaseEventData):
    """Data for rent.payment_allocated event."""

    allocation_public_id: str
    payment_public_id: str
    schedule_line_public_id: str
    lease_public_id: str
    receipt_no: str = ""
    contract_no: str = ""
    allocated_amount: str = "0"
    currency: str = ""


@dataclass
class RentPaymentVoidedData(BaseEventData):
    """Data for rent.payment_voided event."""

    payment_public_id: str
    lease_public_id: str
    receipt_no: str = ""
    amount: str = "0"
    currency: str = ""
    reason: str = ""
    voided_by_email: str = ""
    allocation_count_reversed: int = 0


# =============================================================================
# Deposit Events
# =============================================================================


@dataclass
class DepositReceivedData(BaseEventData):
    """Data for deposit.received event."""

    transaction_public_id: str
    lease_public_id: str
    contract_no: str = ""
    amount: str = "0"
    currency: str = ""
    transaction_date: str = ""


@dataclass
class DepositAdjustedData(BaseEventData):
    """Data for deposit.adjusted event."""

    transaction_public_id: str
    lease_public_id: str
    contract_no: str = ""
    amount: str = "0"
    currency: str = ""
    reason: str = ""
    transaction_date: str = ""


@dataclass
class DepositRefundedData(BaseEventData):
    """Data for deposit.refunded event."""

    transaction_public_id: str
    lease_public_id: str
    contract_no: str = ""
    amount: str = "0"
    currency: str = ""
    transaction_date: str = ""


@dataclass
class DepositForfeitedData(BaseEventData):
    """Data for deposit.forfeited event."""

    transaction_public_id: str
    lease_public_id: str
    contract_no: str = ""
    amount: str = "0"
    currency: str = ""
    reason: str = ""
    transaction_date: str = ""


# =============================================================================
# Expense Events
# =============================================================================


@dataclass
class PropertyExpenseRecordedData(BaseEventData):
    """Data for property.expense_recorded event."""

    expense_public_id: str
    property_public_id: str
    unit_public_id: str = ""
    company_public_id: str = ""
    category: str = ""
    amount: str = "0"
    currency: str = ""
    payment_mode: str = ""
    expense_date: str = ""
    description: str = ""
    recorded_by_email: str = ""


# =============================================================================
# Account Mapping Events
# =============================================================================


@dataclass
class LeaseExpiryAlertData(BaseEventData):
    """Data for lease.expiry_alert event."""

    lease_public_id: str
    contract_no: str
    property_public_id: str
    unit_public_id: str = ""
    lessee_public_id: str = ""
    lessee_name: str = ""
    start_date: str = ""
    end_date: str = ""
    threshold_days: int = 0
    days_until_expiry: int = 0


@dataclass
class PropertyAccountMappingUpdatedData(BaseEventData):
    """Data for property.account_mapping_updated event."""

    company_public_id: str
    changes: dict[str, dict[str, Any]] = field(default_factory=dict)
    updated_by_email: str = ""


# =============================================================================
# Module event registry — consumed by ProjectionsConfig.ready()
# =============================================================================

REGISTERED_EVENTS: dict[str, type[BaseEventData]] = {
    EventTypes.PROPERTY_CREATED: PropertyCreatedData,
    EventTypes.PROPERTY_UPDATED: PropertyUpdatedData,
    EventTypes.UNIT_CREATED: UnitCreatedData,
    EventTypes.UNIT_STATUS_CHANGED: UnitStatusChangedData,
    EventTypes.LESSEE_CREATED: LesseeCreatedData,
    EventTypes.LESSEE_UPDATED: LesseeUpdatedData,
    EventTypes.LEASE_CREATED: LeaseCreatedData,
    EventTypes.LEASE_UPDATED: LeaseUpdatedData,
    EventTypes.LEASE_ACTIVATED: LeaseActivatedData,
    EventTypes.LEASE_TERMINATED: LeaseTerminatedData,
    EventTypes.LEASE_RENEWED: LeaseRenewedData,
    EventTypes.RENT_SCHEDULE_GENERATED: RentScheduleGeneratedData,
    EventTypes.RENT_DUE_POSTED: RentDuePostedData,
    EventTypes.RENT_OVERDUE_DETECTED: RentOverdueDetectedData,
    EventTypes.RENT_LINE_WAIVED: RentLineWaivedData,
    EventTypes.RENT_PAYMENT_RECEIVED: RentPaymentReceivedData,
    EventTypes.RENT_PAYMENT_ALLOCATED: RentPaymentAllocatedData,
    EventTypes.RENT_PAYMENT_VOIDED: RentPaymentVoidedData,
    EventTypes.DEPOSIT_RECEIVED: DepositReceivedData,
    EventTypes.DEPOSIT_ADJUSTED: DepositAdjustedData,
    EventTypes.DEPOSIT_REFUNDED: DepositRefundedData,
    EventTypes.DEPOSIT_FORFEITED: DepositForfeitedData,
    EventTypes.LEASE_EXPIRY_ALERT: LeaseExpiryAlertData,
    EventTypes.PROPERTY_EXPENSE_RECORDED: PropertyExpenseRecordedData,
    EventTypes.PROPERTY_ACCOUNT_MAPPING_UPDATED: PropertyAccountMappingUpdatedData,
}
