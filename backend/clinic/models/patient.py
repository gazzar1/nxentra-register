# clinic/models/patient.py
import uuid

from django.conf import settings
from django.db import models

from accounts.models import Company, ProjectionWriteGuard


def clinic_document_path(instance, filename):
    ext = filename.split(".")[-1]
    return f"clinic/documents/{instance.patient.company_id}/{instance.patient.public_id}/{uuid.uuid4().hex[:12]}.{ext}"


class Patient(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"

    class BloodType(models.TextChoices):
        A_POS = "A+", "A+"
        A_NEG = "A-", "A-"
        B_POS = "B+", "B+"
        B_NEG = "B-", "B-"
        AB_POS = "AB+", "AB+"
        AB_NEG = "AB-", "AB-"
        O_POS = "O+", "O+"
        O_NEG = "O-", "O-"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="clinic_patients",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255, blank=True, default="")
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    national_id = models.CharField(max_length=20, blank=True, default="")
    blood_type = models.CharField(max_length=5, choices=BloodType.choices, blank=True, default="")

    # Light patient file fields
    allergies = models.JSONField(default=list, blank=True, help_text="List of allergy strings.")
    chronic_diseases = models.JSONField(default=list, blank=True, help_text="List of chronic disease strings.")
    current_medications = models.JSONField(default=list, blank=True, help_text="List of medication strings.")

    emergency_contact_name = models.CharField(max_length=255, blank=True, default="")
    emergency_contact_phone = models.CharField(max_length=20, blank=True, default="")

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="uniq_clinic_patient_code_per_company",
            )
        ]
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class PatientDocument(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    class DocumentType(models.TextChoices):
        PRESCRIPTION = "prescription", "Prescription"
        LAB_RESULT = "lab_result", "Lab/Test Result"
        RADIOLOGY = "radiology", "Scan/Radiology"
        SURGERY_REPORT = "surgery_report", "Surgery Report"
        REFERRAL = "referral", "Referral Letter"
        OTHER = "other", "Other"

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="documents")
    visit = models.ForeignKey(
        "clinic.Visit", on_delete=models.SET_NULL, null=True, blank=True, related_name="documents",
    )
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to=clinic_document_path)
    file_name = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(help_text="File size in bytes.")
    mime_type = models.CharField(max_length=100, blank=True, default="")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+",
    )
    notes = models.TextField(blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.document_type}: {self.title}"
