# clinic/views.py
"""
API views for clinic module.

Views handle HTTP requests and delegate business logic to commands.
"""

import mimetypes

from django.db import models as db_models
from django.http import FileResponse, Http404

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser

from accounts.authz import resolve_actor
from accounting.mappings import ModuleAccountMapping
from accounting.models import Account
from projections.write_barrier import command_writes_allowed

from .models import Patient, PatientDocument, Doctor, Visit, Invoice, Payment
from .serializers import (
    PatientSerializer, PatientCreateSerializer, PatientUpdateSerializer,
    PatientDocumentSerializer, DocumentUploadSerializer,
    DoctorSerializer, DoctorCreateSerializer,
    VisitSerializer, VisitCreateSerializer, VisitCompleteSerializer,
    InvoiceSerializer, InvoiceCreateSerializer,
    PaymentSerializer, PaymentCreateSerializer, PaymentVoidSerializer,
)
from .commands import (
    create_patient, update_patient, upload_document,
    create_doctor,
    create_visit, complete_visit,
    create_invoice, issue_invoice,
    receive_payment, void_payment,
)
from .projections import REQUIRED_ROLES, MODULE_NAME


# =============================================================================
# Patient Views
# =============================================================================

class PatientListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = Patient.objects.filter(company=actor.company).order_by("code")

        if "status" in request.query_params:
            qs = qs.filter(status=request.query_params["status"])
        if "search" in request.query_params:
            q = request.query_params["search"]
            qs = qs.filter(
                db_models.Q(name__icontains=q) |
                db_models.Q(code__icontains=q) |
                db_models.Q(phone__icontains=q) |
                db_models.Q(national_id__icontains=q)
            )

        serializer = PatientSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = PatientCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_patient(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            PatientSerializer(result.data["patient"]).data,
            status=status.HTTP_201_CREATED,
        )


class PatientDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, actor, pk):
        try:
            return Patient.objects.get(company=actor.company, pk=pk)
        except Patient.DoesNotExist:
            return None

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        patient = self.get_object(actor, pk)
        if not patient:
            return Response({"detail": "Patient not found."}, status=404)

        return Response(PatientSerializer(patient).data)

    def put(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = PatientUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = update_patient(actor, patient_id=pk, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(PatientSerializer(result.data["patient"]).data)

    patch = put


# =============================================================================
# Document Views
# =============================================================================

class PatientDocumentListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request, patient_id):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            patient = Patient.objects.get(company=actor.company, pk=patient_id)
        except Patient.DoesNotExist:
            return Response({"detail": "Patient not found."}, status=404)

        qs = PatientDocument.objects.filter(patient=patient)
        if "document_type" in request.query_params:
            qs = qs.filter(document_type=request.query_params["document_type"])

        serializer = PatientDocumentSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request, patient_id):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = upload_document(
            actor,
            patient_id=patient_id,
            **serializer.validated_data,
        )
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            PatientDocumentSerializer(result.data["document"]).data,
            status=status.HTTP_201_CREATED,
        )


class PatientDocumentDetailView(APIView):
    """Download or delete a patient document."""
    permission_classes = [IsAuthenticated]

    def _get_document(self, request, patient_id, doc_id):
        actor = resolve_actor(request)
        if not actor.company:
            return None, Response({"detail": "No active company."}, status=400)
        try:
            doc = PatientDocument.objects.select_related("patient").get(
                pk=doc_id,
                patient_id=patient_id,
                patient__company=actor.company,
            )
        except PatientDocument.DoesNotExist:
            return None, None
        return doc, None

    def get(self, request, patient_id, doc_id):
        doc, err = self._get_document(request, patient_id, doc_id)
        if err:
            return err
        if not doc:
            raise Http404("Document not found.")
        if not doc.file:
            raise Http404("File not found.")

        content_type = doc.mime_type or mimetypes.guess_type(doc.file.name)[0] or "application/octet-stream"
        response = FileResponse(doc.file.open("rb"), content_type=content_type)
        response["Content-Disposition"] = f'inline; filename="{doc.file_name}"'
        return response

    def delete(self, request, patient_id, doc_id):
        doc, err = self._get_document(request, patient_id, doc_id)
        if err:
            return err
        if not doc:
            return Response({"detail": "Document not found."}, status=404)

        # Delete the file from storage
        if doc.file:
            doc.file.delete(save=False)
        doc.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Doctor Views
# =============================================================================

class DoctorListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = Doctor.objects.filter(company=actor.company).order_by("code")
        if "is_active" in request.query_params:
            qs = qs.filter(is_active=request.query_params["is_active"].lower() == "true")

        serializer = DoctorSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = DoctorCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_doctor(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            DoctorSerializer(result.data["doctor"]).data,
            status=status.HTTP_201_CREATED,
        )


class DoctorDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            doctor = Doctor.objects.get(company=actor.company, pk=pk)
        except Doctor.DoesNotExist:
            return Response({"detail": "Doctor not found."}, status=404)

        return Response(DoctorSerializer(doctor).data)


# =============================================================================
# Visit Views
# =============================================================================

class VisitListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = Visit.objects.filter(
            company=actor.company,
        ).select_related("patient", "doctor").order_by("-visit_date", "-created_at")

        if "patient_id" in request.query_params:
            qs = qs.filter(patient_id=request.query_params["patient_id"])
        if "doctor_id" in request.query_params:
            qs = qs.filter(doctor_id=request.query_params["doctor_id"])
        if "status" in request.query_params:
            qs = qs.filter(status=request.query_params["status"])

        serializer = VisitSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = VisitCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_visit(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            VisitSerializer(result.data["visit"]).data,
            status=status.HTTP_201_CREATED,
        )


class VisitDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            visit = Visit.objects.select_related("patient", "doctor").get(
                company=actor.company, pk=pk,
            )
        except Visit.DoesNotExist:
            return Response({"detail": "Visit not found."}, status=404)

        return Response(VisitSerializer(visit).data)


class VisitCompleteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = VisitCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = complete_visit(actor, visit_id=pk, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        visit = Visit.objects.select_related("patient", "doctor").get(pk=pk)
        return Response(VisitSerializer(visit).data)


# =============================================================================
# Invoice Views
# =============================================================================

class InvoiceListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = Invoice.objects.filter(
            company=actor.company,
        ).select_related("patient").order_by("-date", "-created_at")

        if "patient_id" in request.query_params:
            qs = qs.filter(patient_id=request.query_params["patient_id"])
        if "status" in request.query_params:
            qs = qs.filter(status=request.query_params["status"])

        serializer = InvoiceSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = InvoiceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        line_items = [
            {"description": li["description"], "amount": str(li["amount"])}
            for li in data.pop("line_items")
        ]

        result = create_invoice(actor, line_items=line_items, **data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            InvoiceSerializer(result.data["invoice"]).data,
            status=status.HTTP_201_CREATED,
        )


class InvoiceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            invoice = Invoice.objects.select_related("patient").get(
                company=actor.company, pk=pk,
            )
        except Invoice.DoesNotExist:
            return Response({"detail": "Invoice not found."}, status=404)

        return Response(InvoiceSerializer(invoice).data)


class InvoiceIssueView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        result = issue_invoice(actor, invoice_id=pk)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(InvoiceSerializer(result.data["invoice"]).data)


# =============================================================================
# Payment Views
# =============================================================================

class PaymentListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = Payment.objects.filter(
            company=actor.company,
        ).select_related("invoice", "patient").order_by("-payment_date", "-created_at")

        if "invoice_id" in request.query_params:
            qs = qs.filter(invoice_id=request.query_params["invoice_id"])
        if "patient_id" in request.query_params:
            qs = qs.filter(patient_id=request.query_params["patient_id"])

        serializer = PaymentSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        data["amount"] = str(data["amount"])

        result = receive_payment(actor, **data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            PaymentSerializer(result.data["payment"]).data,
            status=status.HTTP_201_CREATED,
        )


class PaymentVoidView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = PaymentVoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = void_payment(actor, payment_id=pk, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(PaymentSerializer(result.data["payment"]).data)


# =============================================================================
# Account Mapping View
# =============================================================================

class ClinicAccountMappingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        mapping = ModuleAccountMapping.get_mapping(actor.company, MODULE_NAME)
        result = []
        for role in REQUIRED_ROLES:
            account = mapping.get(role)
            result.append({
                "role": role,
                "account_id": account.id if account else None,
                "account_code": account.code if account else "",
                "account_name": account.name if account else "",
            })
        return Response(result)

    def put(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        mappings = request.data
        if not isinstance(mappings, list):
            return Response({"detail": "Expected a list of role mappings."}, status=400)

        with command_writes_allowed():
            for item in mappings:
                role = item.get("role")
                account_id = item.get("account_id")

                if role not in REQUIRED_ROLES:
                    continue

                account = None
                if account_id:
                    try:
                        account = Account.objects.get(
                            company=actor.company, pk=account_id,
                        )
                    except Account.DoesNotExist:
                        return Response(
                            {"detail": f"Account {account_id} not found."},
                            status=400,
                        )

                ModuleAccountMapping.objects.update_or_create(
                    company=actor.company,
                    module=MODULE_NAME,
                    role=role,
                    defaults={"account": account},
                )

        return Response({"detail": "Account mappings updated."})
