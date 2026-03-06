# properties/models/config.py
"""
Property account mapping configuration.

Each tenant configures which GL accounts to use for property accounting entries.
Without this, projections cannot create journal entries.
"""

import uuid

from django.db import models

from accounts.models import Company, ProjectionWriteGuard
from accounting.models import Account


class PropertyAccountMapping(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="property_account_mapping",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    # Revenue
    rental_income_account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    other_income_account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    # Assets
    accounts_receivable_account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    cash_bank_account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    unapplied_cash_account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    # Liabilities
    security_deposit_account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    accounts_payable_account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    # Expenses
    property_expense_account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Property Account Mapping"
        verbose_name_plural = "Property Account Mappings"

    def __str__(self):
        return f"Property Account Mapping for {self.company}"
