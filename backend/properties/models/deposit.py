# properties/models/deposit.py
"""
Security deposit transaction model.
"""

import uuid

from django.db import models

from accounts.models import Company, ProjectionWriteGuard
from .lease import Lease


class SecurityDepositTransaction(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    class DepositTransactionType(models.TextChoices):
        RECEIVED = "received", "Received"
        ADJUSTED = "adjusted", "Adjusted"
        REFUNDED = "refunded", "Refunded"
        FORFEITED = "forfeited", "Forfeited"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="property_deposit_transactions",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    lease = models.ForeignKey(
        Lease,
        on_delete=models.CASCADE,
        related_name="deposit_transactions",
    )
    transaction_type = models.CharField(
        max_length=20, choices=DepositTransactionType.choices
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, default="SAR")
    transaction_date = models.DateField()
    reason = models.TextField(blank=True, null=True)
    reference = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-transaction_date"]

    def __str__(self):
        return f"Deposit {self.transaction_type} {self.amount} on Lease {self.lease.contract_no}"
