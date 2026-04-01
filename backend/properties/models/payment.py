# properties/models/payment.py
"""
Payment receipt and allocation models.
"""

import uuid

from django.conf import settings
from django.db import models

from accounts.models import Company, ProjectionWriteGuard

from .lease import Lease, RentScheduleLine
from .lessee import Lessee


class PaymentReceipt(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        CHEQUE = "cheque", "Cheque"
        WALLET = "wallet", "Wallet"

    class AllocationStatus(models.TextChoices):
        UNALLOCATED = "unallocated", "Unallocated"
        PARTIALLY_ALLOCATED = "partially_allocated", "Partially Allocated"
        FULLY_ALLOCATED = "fully_allocated", "Fully Allocated"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="property_payments",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    receipt_no = models.CharField(max_length=50)
    lessee = models.ForeignKey(
        Lessee,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    lease = models.ForeignKey(
        Lease,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    payment_date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, default="SAR")
    method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    reference_no = models.CharField(max_length=100, blank=True, null=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="property_payments_received",
    )
    notes = models.TextField(blank=True, null=True)
    allocation_status = models.CharField(
        max_length=25,
        choices=AllocationStatus.choices,
        default=AllocationStatus.UNALLOCATED,
    )
    voided = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_reason = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "receipt_no"],
                name="uniq_receipt_no_per_company",
            )
        ]
        ordering = ["-payment_date"]

    def __str__(self):
        return f"Receipt {self.receipt_no} ({self.allocation_status})"


class PaymentAllocation(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="property_allocations",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    payment = models.ForeignKey(
        PaymentReceipt,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    schedule_line = models.ForeignKey(
        RentScheduleLine,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    allocated_amount = models.DecimalField(max_digits=18, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment", "schedule_line"],
                name="uniq_allocation_per_payment_line",
            )
        ]
        ordering = ["created_at"]

    def __str__(self):
        return f"Allocation {self.allocated_amount} from {self.payment.receipt_no}"
