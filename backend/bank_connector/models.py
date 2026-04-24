# bank_connector/models.py
"""
Bank connector models.

Stores bank accounts, imported statements, and individual transactions
for reconciliation against platform payouts.
"""

import uuid

from django.conf import settings
from django.db import models

from accounts.models import Company


class BankAccount(models.Model):
    """A bank account belonging to a company."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="bank_accounts")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    bank_name = models.CharField(max_length=255)
    account_name = models.CharField(max_length=255, help_text="Display name, e.g. 'CIB Main Account'")
    account_number_last4 = models.CharField(max_length=4, default="", blank=True, help_text="Last 4 digits for display")
    currency = models.CharField(max_length=3, default="USD")
    gl_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Linked GL account from chart of accounts",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.account_name} ({self.bank_name})"


class BankStatement(models.Model):
    """An imported bank statement (one CSV upload = one statement)."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSED = "PROCESSED", "Processed"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="bank_connector_statements")
    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name="statements")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    filename = models.CharField(max_length=255)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    transaction_count = models.IntegerField(default=0)
    total_debits = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_credits = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    column_mapping = models.JSONField(
        default=dict,
        help_text="Mapping of CSV columns to our fields: {date: col_name, description: col_name, ...}",
    )
    error_message = models.TextField(default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.filename} ({self.bank_account.account_name})"


class BankTransaction(models.Model):
    """An individual bank transaction line from an imported statement."""

    class Status(models.TextChoices):
        UNMATCHED = "UNMATCHED", "Unmatched"
        MATCHED = "MATCHED", "Matched"
        EXCLUDED = "EXCLUDED", "Excluded"

    class TransactionType(models.TextChoices):
        CREDIT = "CREDIT", "Credit"
        DEBIT = "DEBIT", "Debit"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="bank_transactions")
    statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="transactions")
    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name="transactions")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    transaction_date = models.DateField()
    value_date = models.DateField(null=True, blank=True)
    description = models.CharField(max_length=500)
    reference = models.CharField(max_length=255, default="", blank=True, db_index=True)
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="Positive = credit (money in), Negative = debit (money out)",
    )
    transaction_type = models.CharField(
        max_length=10,
        choices=TransactionType.choices,
    )
    running_balance = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNMATCHED)
    # Reconciliation link — generic so it can match to any payout type
    matched_content_type = models.CharField(
        max_length=100,
        default="",
        blank=True,
        help_text="e.g. 'stripe_payout', 'shopify_payout'",
    )
    matched_object_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="ID of the matched payout record",
    )
    matched_at = models.DateTimeField(null=True, blank=True)
    matched_by = models.CharField(
        max_length=20,
        default="",
        blank=True,
        help_text="'auto' or 'manual'",
    )
    raw_data = models.JSONField(default=dict, help_text="Original CSV row data")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-transaction_date", "-id"]
        indexes = [
            models.Index(fields=["company", "transaction_date"]),
            models.Index(fields=["company", "status"]),
        ]

    def __str__(self):
        return f"{self.transaction_date} {self.description} {self.amount}"


class ReconciliationException(models.Model):
    """
    A reconciliation exception requiring operator attention.

    Auto-detected by the matching engine or Shopify reconciliation layer
    when discrepancies, unmatched items, or clearing balance anomalies are found.
    """

    class ExceptionType(models.TextChoices):
        UNMATCHED_BANK_TX = "UNMATCHED_BANK_TX", "Unmatched Bank Transaction"
        UNMATCHED_PAYOUT = "UNMATCHED_PAYOUT", "Unmatched Payout"
        PAYOUT_DISCREPANCY = "PAYOUT_DISCREPANCY", "Payout Amount Discrepancy"
        CLEARING_BALANCE = "CLEARING_BALANCE", "Clearing Balance Anomaly"
        MISSING_JE = "MISSING_JE", "Missing Journal Entry"
        FEE_VARIANCE = "FEE_VARIANCE", "Fee Variance"
        DUPLICATE_MATCH = "DUPLICATE_MATCH", "Duplicate Match Detected"

    class Severity(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        RESOLVED = "RESOLVED", "Resolved"
        ESCALATED = "ESCALATED", "Escalated"
        DISMISSED = "DISMISSED", "Dismissed"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="reconciliation_exceptions")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Classification
    exception_type = models.CharField(max_length=30, choices=ExceptionType.choices)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)

    # Context
    platform = models.CharField(
        max_length=20,
        default="",
        blank=True,
        help_text="Platform involved: shopify, stripe, or empty for bank-only",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(default="")
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Amount involved in the exception",
    )
    currency = models.CharField(max_length=3, default="USD")
    exception_date = models.DateField(
        help_text="Date the exception relates to (e.g., payout date, transaction date)",
    )

    # References (generic so it can point to any related object)
    reference_type = models.CharField(
        max_length=50,
        default="",
        blank=True,
        help_text="e.g. 'bank_transaction', 'shopify_payout', 'stripe_payout'",
    )
    reference_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="ID of the related record",
    )
    reference_label = models.CharField(
        max_length=255,
        default="",
        blank=True,
        help_text="Human-readable reference, e.g. 'Payout po_abc123'",
    )

    # Assignment
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_recon_exceptions",
        db_constraint=False,
    )

    # Resolution
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_recon_exceptions",
        db_constraint=False,
    )
    resolution_note = models.TextField(default="", blank=True)

    # Metadata
    details = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured data about the exception (variance amounts, IDs, etc.)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "exception_type"]),
            models.Index(fields=["company", "severity", "status"]),
            models.Index(fields=["company", "exception_date"]),
        ]

    def __str__(self):
        return f"[{self.severity}] {self.title} ({self.status})"
