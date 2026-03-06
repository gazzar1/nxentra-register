# properties/models/lessee.py
"""
Lessee model — the property renter.

Named "Lessee" to avoid collision with Nxentra's system-level "Tenant".
"""

import uuid

from django.db import models

from accounts.models import Company, ProjectionWriteGuard


class Lessee(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    class LesseeType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        COMPANY = "company", "Company"

    class LesseeStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        BLACKLISTED = "blacklisted", "Blacklisted"

    class RiskRating(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="lessees",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    code = models.CharField(max_length=20)
    lessee_type = models.CharField(max_length=20, choices=LesseeType.choices)
    display_name = models.CharField(max_length=255)
    display_name_ar = models.CharField(max_length=255, blank=True, default="")
    national_id = models.CharField(max_length=50, blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    whatsapp = models.CharField(max_length=30, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    emergency_contact = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(
        max_length=20, choices=LesseeStatus.choices, default=LesseeStatus.ACTIVE
    )
    risk_rating = models.CharField(
        max_length=10, choices=RiskRating.choices, blank=True, null=True
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="uniq_lessee_code_per_company",
            )
        ]
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.display_name}"
