# clinic/event_types.py
"""
Clinic module event data classes.

Each event type has a corresponding dataclass that defines its payload schema.
REGISTERED_EVENTS at the bottom is discovered by ProjectionsConfig.ready().
"""

from dataclasses import dataclass, field
from typing import List

from events.types import BaseEventData, FinancialEventData, EventTypes


# =============================================================================
# Non-financial events
# =============================================================================

@dataclass
class PatientCreatedData(BaseEventData):
    patient_public_id: str = ""
    company_public_id: str = ""
    code: str = ""
    name: str = ""
    date_of_birth: str = ""
    gender: str = ""
    phone: str = ""
    created_by_email: str = ""


@dataclass
class PatientUpdatedData(BaseEventData):
    patient_public_id: str = ""
    changes: dict = field(default_factory=dict)
    updated_by_email: str = ""


@dataclass
class VisitCreatedData(BaseEventData):
    visit_public_id: str = ""
    patient_public_id: str = ""
    doctor_public_id: str = ""
    visit_date: str = ""
    visit_type: str = ""
    chief_complaint: str = ""
    created_by_email: str = ""


@dataclass
class VisitCompletedData(BaseEventData):
    visit_public_id: str = ""
    patient_public_id: str = ""
    doctor_public_id: str = ""
    diagnosis: str = ""
    completed_by_email: str = ""


# =============================================================================
# Financial events (extend FinancialEventData)
# =============================================================================

@dataclass
class InvoiceIssuedData(FinancialEventData):
    """Triggers DR Accounts Receivable / CR Consultation Revenue."""
    invoice_public_id: str = ""
    patient_public_id: str = ""
    visit_public_id: str = ""
    invoice_no: str = ""
    line_items: list = field(default_factory=list)
    discount: str = "0"
    tax: str = "0"


@dataclass
class PaymentReceivedData(FinancialEventData):
    """Triggers DR Cash-Bank / CR Accounts Receivable."""
    payment_public_id: str = ""
    invoice_public_id: str = ""
    patient_public_id: str = ""
    payment_method: str = ""
    reference: str = ""


@dataclass
class PaymentVoidedData(FinancialEventData):
    """Triggers reversal of the payment journal entry."""
    payment_public_id: str = ""
    invoice_public_id: str = ""
    patient_public_id: str = ""
    void_reason: str = ""


# =============================================================================
# REGISTERED_EVENTS — discovered by ProjectionsConfig.ready()
# =============================================================================

REGISTERED_EVENTS: dict[str, type[BaseEventData]] = {
    EventTypes.CLINIC_PATIENT_CREATED: PatientCreatedData,
    EventTypes.CLINIC_PATIENT_UPDATED: PatientUpdatedData,
    EventTypes.CLINIC_VISIT_CREATED: VisitCreatedData,
    EventTypes.CLINIC_VISIT_COMPLETED: VisitCompletedData,
    EventTypes.CLINIC_INVOICE_ISSUED: InvoiceIssuedData,
    EventTypes.CLINIC_PAYMENT_RECEIVED: PaymentReceivedData,
    EventTypes.CLINIC_PAYMENT_VOIDED: PaymentVoidedData,
}
