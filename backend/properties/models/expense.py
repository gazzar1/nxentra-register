# properties/models/expense.py
"""
Property expense model.
"""

import uuid

from django.db import models

from accounts.models import Company, ProjectionWriteGuard

from .property import Property, Unit


class PropertyExpense(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    class ExpenseCategory(models.TextChoices):
        MAINTENANCE = "maintenance", "Maintenance"
        UTILITIES = "utilities", "Utilities"
        CLEANING = "cleaning", "Cleaning"
        SECURITY = "security", "Security"
        SALARY = "salary", "Salary"
        TAX = "tax", "Tax"
        INSURANCE = "insurance", "Insurance"
        LEGAL = "legal", "Legal"
        MARKETING = "marketing", "Marketing"
        OTHER = "other", "Other"

    class PaymentMode(models.TextChoices):
        CASH_PAID = "cash_paid", "Cash Paid"
        CREDIT = "credit", "Credit"

    class PaidStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"
        PARTIALLY_PAID = "partially_paid", "Partially Paid"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="property_expenses",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="expenses",
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
    )
    category = models.CharField(max_length=20, choices=ExpenseCategory.choices)
    vendor_ref = models.CharField(max_length=255, blank=True, null=True)
    expense_date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, default="SAR")
    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices)
    paid_status = models.CharField(
        max_length=20, choices=PaidStatus.choices, default=PaidStatus.UNPAID
    )
    description = models.TextField(blank=True, null=True)
    document_ref = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-expense_date"]

    def __str__(self):
        return f"{self.category} expense {self.amount} on {self.property.code}"
