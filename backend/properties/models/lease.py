# properties/models/lease.py
"""
Lease and RentScheduleLine models.
"""

import uuid

from django.db import models

from accounts.models import Company, ProjectionWriteGuard
from .property import Property, Unit
from .lessee import Lessee


class Lease(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    class PaymentFrequency(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        SEMIANNUAL = "semiannual", "Semi-Annual"
        ANNUAL = "annual", "Annual"

    class DueDayRule(models.TextChoices):
        FIRST_DAY = "first_day", "First Day of Period"
        SPECIFIC_DAY = "specific_day", "Specific Day"

    class LeaseStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        TERMINATED = "terminated", "Terminated"
        RENEWED = "renewed", "Renewed"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="leases",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    contract_no = models.CharField(max_length=50)
    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name="leases",
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        related_name="leases",
        null=True,
        blank=True,
    )
    lessee = models.ForeignKey(
        Lessee,
        on_delete=models.PROTECT,
        related_name="leases",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    handover_date = models.DateField(null=True, blank=True)
    payment_frequency = models.CharField(
        max_length=20, choices=PaymentFrequency.choices
    )
    rent_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, default="SAR")
    grace_days = models.IntegerField(default=0)
    due_day_rule = models.CharField(max_length=20, choices=DueDayRule.choices)
    specific_due_day = models.SmallIntegerField(null=True, blank=True)
    deposit_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=0
    )
    status = models.CharField(
        max_length=20, choices=LeaseStatus.choices, default=LeaseStatus.DRAFT
    )
    renewed_from_lease = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="renewal_leases",
    )
    renewal_option = models.BooleanField(default=False)
    notice_period_days = models.IntegerField(null=True, blank=True)
    terms_summary = models.TextField(blank=True, null=True)
    document_ref = models.CharField(max_length=255, blank=True, null=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    terminated_at = models.DateTimeField(null=True, blank=True)
    termination_reason = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "contract_no"],
                name="uniq_lease_contract_per_company",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Lease {self.contract_no} ({self.status})"


class RentScheduleLine(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    class ScheduleStatus(models.TextChoices):
        UPCOMING = "upcoming", "Upcoming"
        DUE = "due", "Due"
        OVERDUE = "overdue", "Overdue"
        PARTIALLY_PAID = "partially_paid", "Partially Paid"
        PAID = "paid", "Paid"
        WAIVED = "waived", "Waived"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="rent_schedule_lines",
    )
    lease = models.ForeignKey(
        Lease,
        on_delete=models.CASCADE,
        related_name="schedule_lines",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    installment_no = models.PositiveIntegerField()
    period_start = models.DateField()
    period_end = models.DateField()
    due_date = models.DateField()
    base_rent = models.DecimalField(max_digits=18, decimal_places=2)
    adjustments = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    penalties = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_due = models.DecimalField(max_digits=18, decimal_places=2)
    total_allocated = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    outstanding = models.DecimalField(max_digits=18, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=ScheduleStatus.choices,
        default=ScheduleStatus.UPCOMING,
    )
    posted_event_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["lease", "installment_no"],
                name="uniq_schedule_installment",
            )
        ]
        ordering = ["lease", "installment_no"]

    def __str__(self):
        return f"Lease {self.lease.contract_no} #{self.installment_no} ({self.status})"
