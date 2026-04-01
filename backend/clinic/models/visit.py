# clinic/models/visit.py
import uuid

from django.db import models

from accounts.models import Company, ProjectionWriteGuard


class Visit(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    class VisitType(models.TextChoices):
        CONSULTATION = "consultation", "Consultation"
        FOLLOW_UP = "follow_up", "Follow-up"
        PROCEDURE = "procedure", "Procedure"
        EMERGENCY = "emergency", "Emergency"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="clinic_visits",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    patient = models.ForeignKey(
        "clinic.Patient", on_delete=models.CASCADE, related_name="visits",
    )
    doctor = models.ForeignKey(
        "clinic.Doctor", on_delete=models.CASCADE, related_name="visits",
    )
    visit_date = models.DateField()
    visit_type = models.CharField(max_length=20, choices=VisitType.choices)
    chief_complaint = models.TextField(blank=True, default="")
    diagnosis = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-visit_date", "-created_at"]

    def __str__(self):
        return f"Visit {self.public_id} — {self.patient.name} with Dr. {self.doctor.name}"
