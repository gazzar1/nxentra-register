# clinic/serializers.py
"""
Serializers for clinic API.

Input validation and output formatting only.
Business logic happens in commands.py.
"""

from rest_framework import serializers

from .models import Doctor, Invoice, Patient, PatientDocument, Payment, Visit

# =============================================================================
# Patient Serializers
# =============================================================================

class PatientSerializer(serializers.ModelSerializer):
    visit_count = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = [
            "id", "public_id", "code", "name", "name_ar",
            "date_of_birth", "gender", "phone", "email", "national_id",
            "blood_type", "allergies", "chronic_diseases", "current_medications",
            "emergency_contact_name", "emergency_contact_phone",
            "status", "notes", "visit_count", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "public_id", "created_at", "updated_at"]

    def get_visit_count(self, obj):
        return obj.visits.count()


class PatientCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=255)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    date_of_birth = serializers.DateField(required=False, allow_null=True, default=None)
    gender = serializers.ChoiceField(choices=Patient.Gender.choices, required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    national_id = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    blood_type = serializers.ChoiceField(choices=Patient.BloodType.choices, required=False, allow_blank=True, default="")
    allergies = serializers.ListField(child=serializers.CharField(), required=False, default=[])
    chronic_diseases = serializers.ListField(child=serializers.CharField(), required=False, default=[])
    current_medications = serializers.ListField(child=serializers.CharField(), required=False, default=[])
    emergency_contact_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    emergency_contact_phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class PatientUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    gender = serializers.ChoiceField(choices=Patient.Gender.choices, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    national_id = serializers.CharField(max_length=20, required=False, allow_blank=True)
    blood_type = serializers.ChoiceField(choices=Patient.BloodType.choices, required=False, allow_blank=True)
    allergies = serializers.ListField(child=serializers.CharField(), required=False)
    chronic_diseases = serializers.ListField(child=serializers.CharField(), required=False)
    current_medications = serializers.ListField(child=serializers.CharField(), required=False)
    emergency_contact_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    emergency_contact_phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=Patient.Status.choices, required=False)
    notes = serializers.CharField(required=False, allow_blank=True)


# =============================================================================
# Document Serializers
# =============================================================================

class PatientDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientDocument
        fields = [
            "id", "public_id", "patient_id", "visit_id",
            "document_type", "title", "file", "file_name", "file_size",
            "mime_type", "uploaded_by_id", "notes", "uploaded_at",
        ]
        read_only_fields = ["id", "public_id", "file_name", "file_size", "mime_type", "uploaded_at"]


class DocumentUploadSerializer(serializers.Serializer):
    document_type = serializers.ChoiceField(choices=PatientDocument.DocumentType.choices)
    title = serializers.CharField(max_length=255)
    file = serializers.FileField()
    visit_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


# =============================================================================
# Doctor Serializers
# =============================================================================

class DoctorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Doctor
        fields = [
            "id", "public_id", "code", "name", "name_ar",
            "specialization", "phone", "is_active",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "public_id", "created_at", "updated_at"]


class DoctorCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=255)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    specialization = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")


# =============================================================================
# Visit Serializers
# =============================================================================

class VisitSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.name", read_only=True)
    patient_code = serializers.CharField(source="patient.code", read_only=True)
    doctor_name = serializers.CharField(source="doctor.name", read_only=True)

    class Meta:
        model = Visit
        fields = [
            "id", "public_id", "patient_id", "patient_name", "patient_code",
            "doctor_id", "doctor_name",
            "visit_date", "visit_type", "chief_complaint", "diagnosis",
            "notes", "status", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "public_id", "created_at", "updated_at"]


class VisitCreateSerializer(serializers.Serializer):
    patient_id = serializers.IntegerField()
    doctor_id = serializers.IntegerField()
    visit_date = serializers.DateField()
    visit_type = serializers.ChoiceField(choices=Visit.VisitType.choices)
    chief_complaint = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class VisitCompleteSerializer(serializers.Serializer):
    diagnosis = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


# =============================================================================
# Invoice Serializers
# =============================================================================

class InvoiceLineItemSerializer(serializers.Serializer):
    description = serializers.CharField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)


class InvoiceSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.name", read_only=True)
    patient_code = serializers.CharField(source="patient.code", read_only=True)
    balance_due = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "public_id", "patient_id", "patient_name", "patient_code",
            "visit_id", "invoice_no", "date", "due_date",
            "line_items", "subtotal", "discount", "tax", "total",
            "amount_paid", "balance_due", "currency", "status", "notes",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "public_id", "invoice_no", "subtotal", "total",
            "amount_paid", "created_at", "updated_at",
        ]


class InvoiceCreateSerializer(serializers.Serializer):
    patient_id = serializers.IntegerField()
    date = serializers.DateField()
    line_items = InvoiceLineItemSerializer(many=True)
    visit_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    due_date = serializers.DateField(required=False, allow_null=True, default=None)
    discount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default="0")
    tax = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default="0")
    currency = serializers.CharField(max_length=3, required=False, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


# =============================================================================
# Payment Serializers
# =============================================================================

class PaymentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.name", read_only=True)
    invoice_no = serializers.CharField(source="invoice.invoice_no", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id", "public_id", "invoice_id", "invoice_no",
            "patient_id", "patient_name",
            "amount", "currency", "payment_method", "payment_date",
            "reference", "notes", "status", "created_at",
        ]
        read_only_fields = ["id", "public_id", "created_at"]


class PaymentCreateSerializer(serializers.Serializer):
    invoice_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    payment_method = serializers.ChoiceField(choices=Payment.PaymentMethod.choices)
    payment_date = serializers.DateField()
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class PaymentVoidSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


# =============================================================================
# Account Mapping Serializer
# =============================================================================

class ClinicAccountMappingSerializer(serializers.Serializer):
    """For reading/updating clinic account role mappings."""
    role = serializers.CharField(read_only=True)
    account_id = serializers.IntegerField(allow_null=True, required=False)
    account_code = serializers.CharField(read_only=True, required=False)
    account_name = serializers.CharField(read_only=True, required=False)
