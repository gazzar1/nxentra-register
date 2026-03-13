# clinic/commands.py
"""
Command layer for clinic operations.

Commands enforce business rules and emit events.
Views call commands; projections consume the resulting events.
"""

from decimal import Decimal

from django.db import transaction

from accounts.authz import ActorContext, require
from accounting.commands import CommandResult
from events.emitter import emit_event
from events.types import EventTypes
from projections.write_barrier import command_writes_allowed

from .models import Patient, PatientDocument, Doctor, Visit, Invoice, Payment
from .event_types import (
    DoctorCreatedData,
    PatientCreatedData,
    PatientUpdatedData,
    VisitCreatedData,
    VisitCompletedData,
    InvoiceIssuedData,
    PaymentReceivedData,
    PaymentVoidedData,
)


# =============================================================================
# Patient Commands
# =============================================================================

@transaction.atomic
def create_patient(
    actor: ActorContext,
    code: str,
    name: str,
    name_ar: str = "",
    date_of_birth=None,
    gender: str = "",
    phone: str = "",
    email: str = "",
    national_id: str = "",
    blood_type: str = "",
    allergies: list = None,
    chronic_diseases: list = None,
    current_medications: list = None,
    emergency_contact_name: str = "",
    emergency_contact_phone: str = "",
    notes: str = "",
) -> CommandResult:
    require(actor, "clinic.manage")

    if Patient.objects.filter(company=actor.company, code=code).exists():
        return CommandResult.fail(f"Patient with code '{code}' already exists.")

    with command_writes_allowed():
        patient = Patient.objects.create(
            company=actor.company,
            code=code,
            name=name,
            name_ar=name_ar,
            date_of_birth=date_of_birth,
            gender=gender,
            phone=phone,
            email=email,
            national_id=national_id,
            blood_type=blood_type,
            allergies=allergies or [],
            chronic_diseases=chronic_diseases or [],
            current_medications=current_medications or [],
            emergency_contact_name=emergency_contact_name,
            emergency_contact_phone=emergency_contact_phone,
            notes=notes,
        )

    event = emit_event(
        actor=actor,
        event_type=EventTypes.CLINIC_PATIENT_CREATED,
        aggregate_type="Patient",
        aggregate_id=str(patient.public_id),
        idempotency_key=f"clinic.patient.created:{patient.public_id}",
        data=PatientCreatedData(
            patient_public_id=str(patient.public_id),
            company_public_id=str(actor.company.public_id),
            code=patient.code,
            name=patient.name,
            date_of_birth=str(patient.date_of_birth or ""),
            gender=patient.gender,
            phone=patient.phone,
            created_by_email=actor.user.email,
        ),
    )

    return CommandResult.ok(data={"patient": patient}, event=event)


@transaction.atomic
def update_patient(actor: ActorContext, patient_id: int, **kwargs) -> CommandResult:
    require(actor, "clinic.manage")

    try:
        patient = Patient.objects.get(company=actor.company, pk=patient_id)
    except Patient.DoesNotExist:
        return CommandResult.fail("Patient not found.")

    changes = {}
    allowed_fields = {
        "name", "name_ar", "date_of_birth", "gender", "phone", "email",
        "national_id", "blood_type", "allergies", "chronic_diseases",
        "current_medications", "emergency_contact_name", "emergency_contact_phone",
        "status", "notes",
    }

    with command_writes_allowed():
        for field_name, value in kwargs.items():
            if field_name not in allowed_fields:
                continue
            old_value = getattr(patient, field_name)
            if old_value != value:
                changes[field_name] = {"old": str(old_value), "new": str(value)}
                setattr(patient, field_name, value)
        if changes:
            patient.save()

    if changes:
        emit_event(
            actor=actor,
            event_type=EventTypes.CLINIC_PATIENT_UPDATED,
            aggregate_type="Patient",
            aggregate_id=str(patient.public_id),
            idempotency_key=f"clinic.patient.updated:{patient.public_id}:{patient.updated_at.isoformat()}",
            data=PatientUpdatedData(
                patient_public_id=str(patient.public_id),
                changes=changes,
                updated_by_email=actor.user.email,
            ),
        )

    return CommandResult.ok(data={"patient": patient})


# =============================================================================
# Document Commands
# =============================================================================

@transaction.atomic
def upload_document(
    actor: ActorContext,
    patient_id: int,
    document_type: str,
    title: str,
    file,
    visit_id: int = None,
    notes: str = "",
) -> CommandResult:
    require(actor, "clinic.manage")

    try:
        patient = Patient.objects.get(company=actor.company, pk=patient_id)
    except Patient.DoesNotExist:
        return CommandResult.fail("Patient not found.")

    visit = None
    if visit_id:
        try:
            visit = Visit.objects.get(company=actor.company, pk=visit_id, patient=patient)
        except Visit.DoesNotExist:
            return CommandResult.fail("Visit not found for this patient.")

    with command_writes_allowed():
        doc = PatientDocument.objects.create(
            patient=patient,
            visit=visit,
            document_type=document_type,
            title=title,
            file=file,
            file_name=file.name,
            file_size=file.size,
            mime_type=getattr(file, "content_type", ""),
            uploaded_by=actor.user,
            notes=notes,
        )

    return CommandResult.ok(data={"document": doc})


# =============================================================================
# Doctor Commands
# =============================================================================

@transaction.atomic
def create_doctor(
    actor: ActorContext,
    code: str,
    name: str,
    name_ar: str = "",
    specialization: str = "",
    phone: str = "",
) -> CommandResult:
    require(actor, "clinic.manage")

    if Doctor.objects.filter(company=actor.company, code=code).exists():
        return CommandResult.fail(f"Doctor with code '{code}' already exists.")

    with command_writes_allowed():
        doctor = Doctor.objects.create(
            company=actor.company,
            code=code,
            name=name,
            name_ar=name_ar,
            specialization=specialization,
            phone=phone,
        )

    event = emit_event(
        actor=actor,
        event_type=EventTypes.CLINIC_DOCTOR_CREATED,
        aggregate_type="Doctor",
        aggregate_id=str(doctor.public_id),
        idempotency_key=f"clinic.doctor.created:{doctor.public_id}",
        data=DoctorCreatedData(
            doctor_public_id=str(doctor.public_id),
            company_public_id=str(actor.company.public_id),
            code=doctor.code,
            name=doctor.name,
            name_ar=doctor.name_ar,
            specialization=doctor.specialization,
            created_by_email=actor.user.email,
        ),
    )

    return CommandResult.ok(data={"doctor": doctor}, event=event)


# =============================================================================
# Visit Commands
# =============================================================================

@transaction.atomic
def create_visit(
    actor: ActorContext,
    patient_id: int,
    doctor_id: int,
    visit_date: str,
    visit_type: str,
    chief_complaint: str = "",
    notes: str = "",
) -> CommandResult:
    require(actor, "clinic.manage")

    try:
        patient = Patient.objects.get(company=actor.company, pk=patient_id)
    except Patient.DoesNotExist:
        return CommandResult.fail("Patient not found.")

    try:
        doctor = Doctor.objects.get(company=actor.company, pk=doctor_id)
    except Doctor.DoesNotExist:
        return CommandResult.fail("Doctor not found.")

    with command_writes_allowed():
        visit = Visit.objects.create(
            company=actor.company,
            patient=patient,
            doctor=doctor,
            visit_date=visit_date,
            visit_type=visit_type,
            chief_complaint=chief_complaint,
            notes=notes,
        )

    event = emit_event(
        actor=actor,
        event_type=EventTypes.CLINIC_VISIT_CREATED,
        aggregate_type="Visit",
        aggregate_id=str(visit.public_id),
        idempotency_key=f"clinic.visit.created:{visit.public_id}",
        data=VisitCreatedData(
            visit_public_id=str(visit.public_id),
            patient_public_id=str(patient.public_id),
            doctor_public_id=str(doctor.public_id),
            visit_date=str(visit.visit_date),
            visit_type=visit.visit_type,
            chief_complaint=visit.chief_complaint,
            created_by_email=actor.user.email,
        ),
    )

    return CommandResult.ok(data={"visit": visit}, event=event)


@transaction.atomic
def complete_visit(
    actor: ActorContext,
    visit_id: int,
    diagnosis: str = "",
    notes: str = "",
) -> CommandResult:
    require(actor, "clinic.manage")

    try:
        visit = Visit.objects.get(company=actor.company, pk=visit_id)
    except Visit.DoesNotExist:
        return CommandResult.fail("Visit not found.")

    if visit.status == Visit.Status.COMPLETED:
        return CommandResult.fail("Visit is already completed.")
    if visit.status == Visit.Status.CANCELLED:
        return CommandResult.fail("Cannot complete a cancelled visit.")

    with command_writes_allowed():
        visit.status = Visit.Status.COMPLETED
        visit.diagnosis = diagnosis
        if notes:
            visit.notes = notes
        visit.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.CLINIC_VISIT_COMPLETED,
        aggregate_type="Visit",
        aggregate_id=str(visit.public_id),
        idempotency_key=f"clinic.visit.completed:{visit.public_id}",
        data=VisitCompletedData(
            visit_public_id=str(visit.public_id),
            patient_public_id=str(visit.patient.public_id),
            doctor_public_id=str(visit.doctor.public_id),
            diagnosis=diagnosis,
            completed_by_email=actor.user.email,
        ),
    )

    return CommandResult.ok(data={"visit": visit}, event=event)


# =============================================================================
# Invoice Commands
# =============================================================================

def _next_invoice_no(company) -> str:
    last = Invoice.objects.filter(company=company).order_by("-id").first()
    if not last:
        return "CINV-0001"
    try:
        num = int(last.invoice_no.split("-")[1]) + 1
    except (IndexError, ValueError):
        num = Invoice.objects.filter(company=company).count() + 1
    return f"CINV-{num:04d}"


@transaction.atomic
def create_invoice(
    actor: ActorContext,
    patient_id: int,
    date: str,
    line_items: list,
    visit_id: int = None,
    due_date: str = None,
    discount: str = "0",
    tax: str = "0",
    currency: str = None,
    notes: str = "",
) -> CommandResult:
    """Create a draft invoice."""
    require(actor, "clinic.manage")

    try:
        patient = Patient.objects.get(company=actor.company, pk=patient_id)
    except Patient.DoesNotExist:
        return CommandResult.fail("Patient not found.")

    visit = None
    if visit_id:
        try:
            visit = Visit.objects.get(company=actor.company, pk=visit_id)
        except Visit.DoesNotExist:
            return CommandResult.fail("Visit not found.")

    subtotal = sum(Decimal(item.get("amount", "0")) for item in line_items)
    total = subtotal - Decimal(discount) + Decimal(tax)
    invoice_no = _next_invoice_no(actor.company)
    resolved_currency = currency or actor.company.default_currency

    with command_writes_allowed():
        invoice = Invoice.objects.create(
            company=actor.company,
            patient=patient,
            visit=visit,
            invoice_no=invoice_no,
            date=date,
            due_date=due_date,
            line_items=line_items,
            subtotal=subtotal,
            discount=Decimal(discount),
            tax=Decimal(tax),
            total=total,
            currency=resolved_currency,
            notes=notes,
        )

    return CommandResult.ok(data={"invoice": invoice})


@transaction.atomic
def issue_invoice(actor: ActorContext, invoice_id: int) -> CommandResult:
    """Issue a draft invoice. Emits the financial event."""
    require(actor, "clinic.manage")

    try:
        invoice = Invoice.objects.select_related("patient", "visit").get(
            company=actor.company, pk=invoice_id,
        )
    except Invoice.DoesNotExist:
        return CommandResult.fail("Invoice not found.")

    if invoice.status != Invoice.Status.DRAFT:
        return CommandResult.fail(f"Cannot issue invoice in status '{invoice.status}'.")

    with command_writes_allowed():
        invoice.status = Invoice.Status.ISSUED
        invoice.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.CLINIC_INVOICE_ISSUED,
        aggregate_type="ClinicInvoice",
        aggregate_id=str(invoice.public_id),
        idempotency_key=f"clinic.invoice.issued:{invoice.public_id}",
        data=InvoiceIssuedData(
            amount=str(invoice.total),
            currency=invoice.currency,
            transaction_date=str(invoice.date),
            document_ref=invoice.invoice_no,
            invoice_public_id=str(invoice.public_id),
            patient_public_id=str(invoice.patient.public_id),
            visit_public_id=str(invoice.visit.public_id) if invoice.visit else "",
            invoice_no=invoice.invoice_no,
            line_items=invoice.line_items,
            discount=str(invoice.discount),
            tax=str(invoice.tax),
        ),
    )

    return CommandResult.ok(data={"invoice": invoice}, event=event)


# =============================================================================
# Payment Commands
# =============================================================================

@transaction.atomic
def receive_payment(
    actor: ActorContext,
    invoice_id: int,
    amount: str,
    payment_method: str,
    payment_date: str,
    reference: str = "",
    notes: str = "",
) -> CommandResult:
    require(actor, "clinic.manage")

    try:
        invoice = Invoice.objects.select_related("patient").get(
            company=actor.company, pk=invoice_id,
        )
    except Invoice.DoesNotExist:
        return CommandResult.fail("Invoice not found.")

    if invoice.status not in (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID):
        return CommandResult.fail(f"Cannot pay invoice in status '{invoice.status}'.")

    payment_amount = Decimal(amount)
    if payment_amount <= 0:
        return CommandResult.fail("Payment amount must be positive.")

    with command_writes_allowed():
        payment = Payment.objects.create(
            company=actor.company,
            invoice=invoice,
            patient=invoice.patient,
            amount=payment_amount,
            currency=invoice.currency,
            payment_method=payment_method,
            payment_date=payment_date,
            reference=reference,
            notes=notes,
        )

        invoice.amount_paid += payment_amount
        if invoice.amount_paid >= invoice.total:
            invoice.status = Invoice.Status.PAID
        else:
            invoice.status = Invoice.Status.PARTIALLY_PAID
        invoice.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.CLINIC_PAYMENT_RECEIVED,
        aggregate_type="ClinicPayment",
        aggregate_id=str(payment.public_id),
        idempotency_key=f"clinic.payment.received:{payment.public_id}",
        data=PaymentReceivedData(
            amount=str(payment.amount),
            currency=payment.currency,
            transaction_date=str(payment.payment_date),
            document_ref=invoice.invoice_no,
            payment_public_id=str(payment.public_id),
            invoice_public_id=str(invoice.public_id),
            patient_public_id=str(invoice.patient.public_id),
            payment_method=payment.payment_method,
            reference=payment.reference,
        ),
    )

    return CommandResult.ok(data={"payment": payment, "invoice": invoice}, event=event)


@transaction.atomic
def void_payment(actor: ActorContext, payment_id: int, reason: str = "") -> CommandResult:
    require(actor, "clinic.manage")

    try:
        payment = Payment.objects.select_related("invoice", "patient").get(
            company=actor.company, pk=payment_id,
        )
    except Payment.DoesNotExist:
        return CommandResult.fail("Payment not found.")

    if payment.status == Payment.Status.VOIDED:
        return CommandResult.fail("Payment is already voided.")

    with command_writes_allowed():
        payment.status = Payment.Status.VOIDED
        payment.save()

        invoice = payment.invoice
        invoice.amount_paid -= payment.amount
        if invoice.amount_paid <= 0:
            invoice.status = Invoice.Status.ISSUED
            invoice.amount_paid = Decimal("0")
        else:
            invoice.status = Invoice.Status.PARTIALLY_PAID
        invoice.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.CLINIC_PAYMENT_VOIDED,
        aggregate_type="ClinicPayment",
        aggregate_id=str(payment.public_id),
        idempotency_key=f"clinic.payment.voided:{payment.public_id}",
        data=PaymentVoidedData(
            amount=str(payment.amount),
            currency=payment.currency,
            transaction_date=str(payment.payment_date),
            document_ref=invoice.invoice_no,
            payment_public_id=str(payment.public_id),
            invoice_public_id=str(invoice.public_id),
            patient_public_id=str(payment.patient.public_id),
            void_reason=reason,
        ),
    )

    return CommandResult.ok(data={"payment": payment, "invoice": invoice}, event=event)
