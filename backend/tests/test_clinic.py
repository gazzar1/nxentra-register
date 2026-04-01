# tests/test_clinic.py
"""
Tests for the Clinic Lite module.

Covers:
- Patient CRUD
- Doctor creation
- Visit lifecycle (create → complete)
- Document upload
- Invoice lifecycle (create → issue → JE)
- Payment lifecycle (receive → JE, void → reversal JE)
- Account mapping via ModuleAccountMapping
- API endpoints
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounting.mappings import ModuleAccountMapping
from accounting.models import Account, JournalEntry
from clinic.commands import (
    complete_visit,
    create_doctor,
    create_invoice,
    create_patient,
    create_visit,
    issue_invoice,
    receive_payment,
    update_patient,
    upload_document,
    void_payment,
)
from clinic.models import Invoice, Visit
from clinic.projections import ClinicAccountingProjection
from events.types import EventTypes

# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def clinic_module_enabled(db, company):
    """Enable the clinic module for the test company."""
    from accounts.models import CompanyModule
    return CompanyModule.objects.get_or_create(
        company=company,
        module_key="clinic",
        defaults={"is_enabled": True},
    )[0]


@pytest.fixture
def ar_account(db, company):
    return Account.objects.create(
        public_id=uuid4(), company=company,
        code="1200", name="Accounts Receivable - Clinic",
        account_type=Account.AccountType.RECEIVABLE,
        normal_balance=Account.NormalBalance.DEBIT,
        status=Account.Status.ACTIVE,
    )


@pytest.fixture
def clinic_revenue_account(db, company):
    return Account.objects.create(
        public_id=uuid4(), company=company,
        code="4100", name="Consultation Revenue",
        account_type=Account.AccountType.REVENUE,
        normal_balance=Account.NormalBalance.CREDIT,
        status=Account.Status.ACTIVE,
    )


@pytest.fixture
def clinic_cash_account(db, company):
    return Account.objects.create(
        public_id=uuid4(), company=company,
        code="1010", name="Clinic Cash",
        account_type=Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        status=Account.Status.ACTIVE,
    )


@pytest.fixture
def clinic_mapping(db, company, ar_account, clinic_revenue_account, clinic_cash_account):
    """Set up clinic account mappings."""
    ModuleAccountMapping.objects.create(
        company=company, module="clinic",
        role="ACCOUNTS_RECEIVABLE", account=ar_account,
    )
    ModuleAccountMapping.objects.create(
        company=company, module="clinic",
        role="CONSULTATION_REVENUE", account=clinic_revenue_account,
    )
    ModuleAccountMapping.objects.create(
        company=company, module="clinic",
        role="CASH_BANK", account=clinic_cash_account,
    )


@pytest.fixture
def patient(actor_context):
    result = create_patient(
        actor_context, code="P001", name="Ahmad Ali",
        phone="0501234567", gender="male",
    )
    return result.data["patient"]


@pytest.fixture
def doctor(actor_context):
    result = create_doctor(
        actor_context, code="D001", name="Dr. Sarah",
        specialization="General",
    )
    return result.data["doctor"]


@pytest.fixture
def visit(actor_context, patient, doctor):
    result = create_visit(
        actor_context,
        patient_id=patient.id,
        doctor_id=doctor.id,
        visit_date=str(date.today()),
        visit_type="consultation",
    )
    return result.data["visit"]


@pytest.fixture
def invoice(actor_context, patient, visit):
    result = create_invoice(
        actor_context,
        patient_id=patient.id,
        date=str(date.today()),
        line_items=[
            {"description": "Consultation", "amount": "200"},
            {"description": "Lab test", "amount": "50"},
        ],
        visit_id=visit.id,
    )
    return result.data["invoice"]


# =============================================================================
# Patient Command Tests
# =============================================================================

@pytest.mark.django_db
class TestPatientCommands:

    def test_create_patient(self, actor_context):
        result = create_patient(
            actor_context, code="P001", name="Ahmad Ali",
            phone="0501234567", gender="male",
            allergies=["Penicillin"], chronic_diseases=["Diabetes"],
        )
        assert result.success
        p = result.data["patient"]
        assert p.code == "P001"
        assert p.name == "Ahmad Ali"
        assert p.allergies == ["Penicillin"]
        assert p.chronic_diseases == ["Diabetes"]

    def test_create_patient_emits_event(self, actor_context):
        result = create_patient(actor_context, code="P002", name="Test")
        assert result.success
        assert result.event is not None
        assert result.event.event_type == EventTypes.CLINIC_PATIENT_CREATED

    def test_duplicate_code_fails(self, actor_context):
        create_patient(actor_context, code="P001", name="First")
        result = create_patient(actor_context, code="P001", name="Second")
        assert not result.success
        assert "already exists" in result.error

    def test_update_patient(self, actor_context, patient):
        result = update_patient(
            actor_context, patient_id=patient.id,
            phone="0559999999", allergies=["Aspirin"],
        )
        assert result.success
        patient.refresh_from_db()
        assert patient.phone == "0559999999"
        assert patient.allergies == ["Aspirin"]

    def test_update_nonexistent_patient(self, actor_context):
        result = update_patient(actor_context, patient_id=99999, name="Ghost")
        assert not result.success
        assert "not found" in result.error


# =============================================================================
# Doctor Command Tests
# =============================================================================

@pytest.mark.django_db
class TestDoctorCommands:

    def test_create_doctor(self, actor_context):
        result = create_doctor(
            actor_context, code="D001", name="Dr. Sarah",
            specialization="General",
        )
        assert result.success
        assert result.data["doctor"].name == "Dr. Sarah"

    def test_duplicate_doctor_code(self, actor_context):
        create_doctor(actor_context, code="D001", name="First")
        result = create_doctor(actor_context, code="D001", name="Second")
        assert not result.success


# =============================================================================
# Visit Command Tests
# =============================================================================

@pytest.mark.django_db
class TestVisitCommands:

    def test_create_visit(self, actor_context, patient, doctor):
        result = create_visit(
            actor_context,
            patient_id=patient.id,
            doctor_id=doctor.id,
            visit_date=str(date.today()),
            visit_type="consultation",
            chief_complaint="Headache",
        )
        assert result.success
        v = result.data["visit"]
        assert v.status == Visit.Status.SCHEDULED
        assert v.chief_complaint == "Headache"
        assert result.event.event_type == EventTypes.CLINIC_VISIT_CREATED

    def test_complete_visit(self, actor_context, visit):
        result = complete_visit(
            actor_context, visit_id=visit.id,
            diagnosis="Migraine", notes="Prescribed rest",
        )
        assert result.success
        visit.refresh_from_db()
        assert visit.status == Visit.Status.COMPLETED
        assert visit.diagnosis == "Migraine"
        assert result.event.event_type == EventTypes.CLINIC_VISIT_COMPLETED

    def test_complete_already_completed(self, actor_context, visit):
        complete_visit(actor_context, visit_id=visit.id, diagnosis="Test")
        result = complete_visit(actor_context, visit_id=visit.id, diagnosis="Again")
        assert not result.success
        assert "already completed" in result.error

    def test_visit_invalid_patient(self, actor_context, doctor):
        result = create_visit(
            actor_context, patient_id=99999, doctor_id=doctor.id,
            visit_date=str(date.today()), visit_type="consultation",
        )
        assert not result.success
        assert "Patient not found" in result.error


# =============================================================================
# Document Upload Tests
# =============================================================================

@pytest.mark.django_db
class TestDocumentUpload:

    def test_upload_document(self, actor_context, patient):
        file = SimpleUploadedFile(
            "test_report.pdf", b"fake pdf content",
            content_type="application/pdf",
        )
        result = upload_document(
            actor_context, patient_id=patient.id,
            document_type="lab_result", title="Blood Test",
            file=file,
        )
        assert result.success
        doc = result.data["document"]
        assert doc.title == "Blood Test"
        assert doc.file_name == "test_report.pdf"
        assert doc.file_size == len(b"fake pdf content")

    def test_upload_with_visit(self, actor_context, patient, visit):
        file = SimpleUploadedFile("scan.jpg", b"image", content_type="image/jpeg")
        result = upload_document(
            actor_context, patient_id=patient.id,
            document_type="radiology", title="X-Ray",
            file=file, visit_id=visit.id,
        )
        assert result.success
        assert result.data["document"].visit_id == visit.id


# =============================================================================
# Invoice Command Tests
# =============================================================================

@pytest.mark.django_db
class TestInvoiceCommands:

    def test_create_invoice(self, actor_context, patient):
        result = create_invoice(
            actor_context,
            patient_id=patient.id,
            date=str(date.today()),
            line_items=[
                {"description": "Consultation", "amount": "200"},
                {"description": "Lab test", "amount": "50"},
            ],
        )
        assert result.success
        inv = result.data["invoice"]
        assert inv.status == Invoice.Status.DRAFT
        assert inv.subtotal == Decimal("250")
        assert inv.total == Decimal("250")
        assert inv.invoice_no.startswith("CINV-")

    def test_create_invoice_with_discount_tax(self, actor_context, patient):
        result = create_invoice(
            actor_context,
            patient_id=patient.id,
            date=str(date.today()),
            line_items=[{"description": "Surgery", "amount": "1000"}],
            discount="100",
            tax="50",
        )
        assert result.success
        inv = result.data["invoice"]
        assert inv.subtotal == Decimal("1000")
        assert inv.discount == Decimal("100")
        assert inv.tax == Decimal("50")
        assert inv.total == Decimal("950")  # 1000 - 100 + 50

    def test_issue_invoice(self, actor_context, invoice):
        result = issue_invoice(actor_context, invoice_id=invoice.id)
        assert result.success
        invoice.refresh_from_db()
        assert invoice.status == Invoice.Status.ISSUED
        assert result.event.event_type == EventTypes.CLINIC_INVOICE_ISSUED

    def test_issue_non_draft_fails(self, actor_context, invoice):
        issue_invoice(actor_context, invoice_id=invoice.id)
        result = issue_invoice(actor_context, invoice_id=invoice.id)
        assert not result.success
        assert "Cannot issue" in result.error

    def test_sequential_invoice_numbers(self, actor_context, patient):
        r1 = create_invoice(
            actor_context, patient_id=patient.id,
            date=str(date.today()),
            line_items=[{"description": "A", "amount": "100"}],
        )
        r2 = create_invoice(
            actor_context, patient_id=patient.id,
            date=str(date.today()),
            line_items=[{"description": "B", "amount": "100"}],
        )
        assert r1.data["invoice"].invoice_no == "CINV-0001"
        assert r2.data["invoice"].invoice_no == "CINV-0002"


# =============================================================================
# Payment Command Tests
# =============================================================================

@pytest.mark.django_db
class TestPaymentCommands:

    def test_receive_payment(self, actor_context, invoice):
        issue_invoice(actor_context, invoice_id=invoice.id)

        result = receive_payment(
            actor_context,
            invoice_id=invoice.id,
            amount="250",
            payment_method="cash",
            payment_date=str(date.today()),
        )
        assert result.success
        payment = result.data["payment"]
        assert payment.amount == Decimal("250")
        assert result.event.event_type == EventTypes.CLINIC_PAYMENT_RECEIVED

        invoice.refresh_from_db()
        assert invoice.status == Invoice.Status.PAID
        assert invoice.amount_paid == Decimal("250")

    def test_partial_payment(self, actor_context, invoice):
        issue_invoice(actor_context, invoice_id=invoice.id)

        receive_payment(
            actor_context, invoice_id=invoice.id,
            amount="100", payment_method="cash",
            payment_date=str(date.today()),
        )
        invoice.refresh_from_db()
        assert invoice.status == Invoice.Status.PARTIALLY_PAID
        assert invoice.amount_paid == Decimal("100")
        assert invoice.balance_due == Decimal("150")

    def test_payment_on_draft_invoice_fails(self, actor_context, invoice):
        result = receive_payment(
            actor_context, invoice_id=invoice.id,
            amount="100", payment_method="cash",
            payment_date=str(date.today()),
        )
        assert not result.success
        assert "Cannot pay" in result.error

    def test_void_payment(self, actor_context, invoice):
        issue_invoice(actor_context, invoice_id=invoice.id)
        pay_result = receive_payment(
            actor_context, invoice_id=invoice.id,
            amount="250", payment_method="card",
            payment_date=str(date.today()),
        )

        result = void_payment(
            actor_context, payment_id=pay_result.data["payment"].id,
            reason="Duplicate",
        )
        assert result.success
        assert result.event.event_type == EventTypes.CLINIC_PAYMENT_VOIDED

        invoice.refresh_from_db()
        assert invoice.status == Invoice.Status.ISSUED
        assert invoice.amount_paid == Decimal("0")

    def test_void_already_voided(self, actor_context, invoice):
        issue_invoice(actor_context, invoice_id=invoice.id)
        pay_result = receive_payment(
            actor_context, invoice_id=invoice.id,
            amount="250", payment_method="cash",
            payment_date=str(date.today()),
        )
        void_payment(actor_context, payment_id=pay_result.data["payment"].id)
        result = void_payment(actor_context, payment_id=pay_result.data["payment"].id)
        assert not result.success
        assert "already voided" in result.error


# =============================================================================
# Accounting Projection Tests
# =============================================================================

@pytest.mark.django_db
class TestClinicAccountingProjection:

    def test_invoice_issued_creates_je(self, actor_context, invoice, clinic_mapping):
        result = issue_invoice(actor_context, invoice_id=invoice.id)
        assert result.success

        # Run projection manually
        projection = ClinicAccountingProjection()
        projection.handle(result.event)

        je = JournalEntry.objects.filter(
            company=actor_context.company,
            memo__startswith="Clinic invoice:",
        ).first()
        assert je is not None
        assert je.status == JournalEntry.Status.POSTED

        lines = je.lines.all().order_by("line_no")
        assert lines.count() == 2
        assert lines[0].debit == Decimal("250")  # AR
        assert lines[1].credit == Decimal("250")  # Revenue

    def test_payment_received_creates_je(self, actor_context, invoice, clinic_mapping):
        issue_invoice(actor_context, invoice_id=invoice.id)
        pay_result = receive_payment(
            actor_context, invoice_id=invoice.id,
            amount="250", payment_method="cash",
            payment_date=str(date.today()),
        )

        projection = ClinicAccountingProjection()
        projection.handle(pay_result.event)

        je = JournalEntry.objects.filter(
            company=actor_context.company,
            memo__startswith="Clinic payment:",
        ).first()
        assert je is not None

        lines = je.lines.all().order_by("line_no")
        assert lines[0].debit == Decimal("250")  # Cash
        assert lines[1].credit == Decimal("250")  # AR

    def test_payment_voided_creates_reversal_je(self, actor_context, invoice, clinic_mapping):
        issue_invoice(actor_context, invoice_id=invoice.id)
        pay_result = receive_payment(
            actor_context, invoice_id=invoice.id,
            amount="250", payment_method="cash",
            payment_date=str(date.today()),
        )
        void_result = void_payment(
            actor_context, payment_id=pay_result.data["payment"].id,
            reason="Error",
        )

        projection = ClinicAccountingProjection()
        projection.handle(void_result.event)

        je = JournalEntry.objects.filter(
            company=actor_context.company,
            memo__startswith="VOID clinic payment:",
        ).first()
        assert je is not None

        lines = je.lines.all().order_by("line_no")
        assert lines[0].debit == Decimal("250")  # AR (reversal)
        assert lines[1].credit == Decimal("250")  # Cash (reversal)

    def test_no_mapping_skips_silently(self, actor_context, invoice):
        """Without account mapping, projection logs warning and skips."""
        result = issue_invoice(actor_context, invoice_id=invoice.id)
        projection = ClinicAccountingProjection()
        projection.handle(result.event)

        je_count = JournalEntry.objects.filter(
            company=actor_context.company,
            memo__startswith="Clinic invoice:",
        ).count()
        assert je_count == 0

    def test_idempotency(self, actor_context, invoice, clinic_mapping):
        """Processing the same event twice should not create duplicate JEs."""
        result = issue_invoice(actor_context, invoice_id=invoice.id)
        projection = ClinicAccountingProjection()
        projection.handle(result.event)
        projection.handle(result.event)

        je_count = JournalEntry.objects.filter(
            company=actor_context.company,
            memo__startswith="Clinic invoice:",
        ).count()
        assert je_count == 1


# =============================================================================
# API Endpoint Tests
# =============================================================================

@pytest.mark.django_db
class TestClinicAPI:

    def test_list_patients(self, authenticated_client, patient):
        resp = authenticated_client.get("/api/clinic/patients/")
        assert resp.status_code == 200
        assert len(resp.data) >= 1
        assert resp.data[0]["code"] == "P001"

    def test_create_patient_api(self, authenticated_client, owner_membership):
        resp = authenticated_client.post("/api/clinic/patients/", {
            "code": "P100",
            "name": "API Patient",
        }, format="json")
        assert resp.status_code == 201
        assert resp.data["code"] == "P100"

    def test_get_patient(self, authenticated_client, patient):
        resp = authenticated_client.get(f"/api/clinic/patients/{patient.id}/")
        assert resp.status_code == 200
        assert resp.data["name"] == "Ahmad Ali"

    def test_update_patient_api(self, authenticated_client, patient):
        resp = authenticated_client.put(
            f"/api/clinic/patients/{patient.id}/",
            {"phone": "0551111111"}, format="json",
        )
        assert resp.status_code == 200
        assert resp.data["phone"] == "0551111111"

    def test_list_doctors(self, authenticated_client, doctor):
        resp = authenticated_client.get("/api/clinic/doctors/")
        assert resp.status_code == 200
        assert len(resp.data) >= 1

    def test_create_doctor_api(self, authenticated_client, owner_membership):
        resp = authenticated_client.post("/api/clinic/doctors/", {
            "code": "D100", "name": "Dr. API",
        }, format="json")
        assert resp.status_code == 201

    def test_create_visit_api(self, authenticated_client, patient, doctor):
        resp = authenticated_client.post("/api/clinic/visits/", {
            "patient_id": patient.id,
            "doctor_id": doctor.id,
            "visit_date": str(date.today()),
            "visit_type": "consultation",
        }, format="json")
        assert resp.status_code == 201
        assert resp.data["status"] == "scheduled"

    def test_complete_visit_api(self, authenticated_client, visit):
        resp = authenticated_client.post(
            f"/api/clinic/visits/{visit.id}/complete/",
            {"diagnosis": "Flu"}, format="json",
        )
        assert resp.status_code == 200
        assert resp.data["status"] == "completed"

    def test_create_invoice_api(self, authenticated_client, patient):
        resp = authenticated_client.post("/api/clinic/invoices/", {
            "patient_id": patient.id,
            "date": str(date.today()),
            "line_items": [
                {"description": "Consultation", "amount": "200"},
            ],
        }, format="json")
        assert resp.status_code == 201
        assert resp.data["status"] == "draft"
        assert resp.data["total"] == "200.00"

    def test_issue_invoice_api(self, authenticated_client, invoice):
        resp = authenticated_client.post(f"/api/clinic/invoices/{invoice.id}/issue/")
        assert resp.status_code == 200
        assert resp.data["status"] == "issued"

    def test_payment_flow_api(self, authenticated_client, invoice):
        # Issue the invoice first
        authenticated_client.post(f"/api/clinic/invoices/{invoice.id}/issue/")

        # Create payment
        resp = authenticated_client.post("/api/clinic/payments/", {
            "invoice_id": invoice.id,
            "amount": "250.00",
            "payment_method": "cash",
            "payment_date": str(date.today()),
        }, format="json")
        assert resp.status_code == 201
        payment_id = resp.data["id"]

        # Void payment
        resp = authenticated_client.post(
            f"/api/clinic/payments/{payment_id}/void/",
            {"reason": "Mistake"}, format="json",
        )
        assert resp.status_code == 200
        assert resp.data["status"] == "voided"

    def test_account_mapping_api(self, authenticated_client, owner_membership,
                                  ar_account, clinic_revenue_account, clinic_cash_account):
        # GET current mapping (empty)
        resp = authenticated_client.get("/api/clinic/account-mapping/")
        assert resp.status_code == 200
        assert len(resp.data) == 3

        # PUT new mapping
        resp = authenticated_client.put("/api/clinic/account-mapping/", [
            {"role": "ACCOUNTS_RECEIVABLE", "account_id": ar_account.id},
            {"role": "CONSULTATION_REVENUE", "account_id": clinic_revenue_account.id},
            {"role": "CASH_BANK", "account_id": clinic_cash_account.id},
        ], format="json")
        assert resp.status_code == 200

        # Verify
        mapping = ModuleAccountMapping.get_mapping(
            authenticated_client.handler._force_user.active_company, "clinic",
        )
        assert mapping["ACCOUNTS_RECEIVABLE"].id == ar_account.id

    def test_unauthenticated_access(self, api_client):
        resp = api_client.get("/api/clinic/patients/")
        assert resp.status_code in (401, 403)
