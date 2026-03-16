# stripe_connector/models.py
"""
Stripe connector models.

Stores Stripe account connection info, charges, refunds, payouts,
and payout transactions for reconciliation.
"""

import uuid

from django.db import models

from accounts.models import Company


class StripeAccount(models.Model):
    """A connected Stripe account for a company."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACTIVE = "ACTIVE", "Active"
        DISCONNECTED = "DISCONNECTED", "Disconnected"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="stripe_accounts"
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    stripe_account_id = models.CharField(
        max_length=255, help_text="Stripe account ID (acct_...)"
    )
    display_name = models.CharField(max_length=255, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    livemode = models.BooleanField(default=False)
    webhook_secret = models.CharField(max_length=255, default="")
    last_sync_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("company", "stripe_account_id")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.stripe_account_id} ({self.company.name})"


class StripeCharge(models.Model):
    """A Stripe charge (payment) record."""

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="stripe_charges"
    )
    account = models.ForeignKey(
        StripeAccount, on_delete=models.CASCADE, related_name="charges"
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    stripe_charge_id = models.CharField(max_length=255, db_index=True)
    stripe_payment_intent_id = models.CharField(max_length=255, default="")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    fee = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    net = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    currency = models.CharField(max_length=3)
    description = models.CharField(max_length=500, default="")
    customer_email = models.CharField(max_length=255, default="")
    customer_name = models.CharField(max_length=255, default="")
    charge_date = models.DateField()
    stripe_created_at = models.DateTimeField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RECEIVED
    )
    event_id = models.UUIDField(null=True, blank=True)
    journal_entry_id = models.UUIDField(null=True, blank=True)
    error_message = models.TextField(default="")
    raw_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("company", "stripe_charge_id")
        ordering = ["-stripe_created_at"]

    def __str__(self):
        return f"Charge {self.stripe_charge_id} ({self.amount} {self.currency})"


class StripeRefund(models.Model):
    """A Stripe refund record."""

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="stripe_refunds"
    )
    charge = models.ForeignKey(
        StripeCharge, on_delete=models.CASCADE, related_name="refunds"
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    stripe_refund_id = models.CharField(max_length=255, db_index=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    reason = models.CharField(max_length=255, default="")
    stripe_created_at = models.DateTimeField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RECEIVED
    )
    event_id = models.UUIDField(null=True, blank=True)
    journal_entry_id = models.UUIDField(null=True, blank=True)
    error_message = models.TextField(default="")
    raw_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("company", "stripe_refund_id")

    def __str__(self):
        return f"Refund {self.stripe_refund_id} ({self.amount} {self.currency})"


class StripePayout(models.Model):
    """A Stripe payout (transfer to bank) record."""

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="stripe_payouts"
    )
    account = models.ForeignKey(
        StripeAccount, on_delete=models.CASCADE, related_name="payouts"
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    stripe_payout_id = models.CharField(max_length=255, db_index=True)
    gross_amount = models.DecimalField(max_digits=18, decimal_places=2)
    fees = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    stripe_status = models.CharField(max_length=30, default="")
    payout_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RECEIVED
    )
    event_id = models.UUIDField(null=True, blank=True)
    journal_entry_id = models.UUIDField(null=True, blank=True)
    error_message = models.TextField(default="")
    raw_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("company", "stripe_payout_id")
        ordering = ["-payout_date"]

    def __str__(self):
        return f"Payout {self.stripe_payout_id} ({self.net_amount} {self.currency})"


class StripePayoutTransaction(models.Model):
    """Individual transaction within a Stripe payout (balance transaction)."""

    class TransactionType(models.TextChoices):
        CHARGE = "charge", "Charge"
        REFUND = "refund", "Refund"
        ADJUSTMENT = "adjustment", "Adjustment"
        PAYOUT = "payout", "Payout"
        OTHER = "other", "Other"

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="stripe_payout_transactions"
    )
    payout = models.ForeignKey(
        StripePayout, on_delete=models.CASCADE, related_name="transactions"
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    stripe_balance_txn_id = models.CharField(max_length=255, db_index=True)
    transaction_type = models.CharField(
        max_length=20, choices=TransactionType.choices, default=TransactionType.OTHER
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    fee = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    net = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    source_id = models.CharField(max_length=255, default="", help_text="Source charge/refund ID")
    verified = models.BooleanField(default=False)
    local_charge = models.ForeignKey(
        StripeCharge, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payout_transactions",
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    raw_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("company", "stripe_balance_txn_id")
        ordering = ["-processed_at"]

    def __str__(self):
        return f"Txn {self.stripe_balance_txn_id} ({self.transaction_type}: {self.amount})"
