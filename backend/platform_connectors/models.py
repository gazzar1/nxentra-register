# platform_connectors/models.py
"""
Abstract base models for platform connectors.

Each concrete platform (Shopify, Stripe, etc.) inherits from these abstract
models to get consistent fields while keeping platform-specific tables
(avoids wide sparse tables with a platform_slug column).

These models are abstract (Meta.abstract = True) — they create no database
tables. Concrete models live in each platform's own app.
"""

import uuid

from django.db import models

from accounts.models import Company


class AbstractPlatformConnection(models.Model):
    """
    Base model for a connected platform store/account.

    Concrete examples:
    - ShopifyStore (shopify_connector.models)
    - StripeAccount (future stripe_connector.models)
    """

    class ConnectionStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACTIVE = "ACTIVE", "Active"
        DISCONNECTED = "DISCONNECTED", "Disconnected"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_connections",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    connection_status = models.CharField(
        max_length=20,
        choices=ConnectionStatus.choices,
        default=ConnectionStatus.PENDING,
    )
    error_message = models.TextField(blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.__class__.__name__} ({self.connection_status})"


class AbstractPlatformOrder(models.Model):
    """
    Base model for a commerce order from any platform.

    Stores the financial data needed for reconciliation.
    Platform-specific fields (e.g. Shopify's order_name) go on the
    concrete subclass.
    """

    class ProcessingStatus(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_orders",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    platform_order_id = models.CharField(max_length=100, db_index=True)

    # Financial data
    total_price = models.DecimalField(max_digits=18, decimal_places=2)
    subtotal_price = models.DecimalField(max_digits=18, decimal_places=2)
    total_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_discounts = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    currency = models.CharField(max_length=3)

    # Processing state
    status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.RECEIVED,
    )
    order_date = models.DateField()
    journal_entry_id = models.UUIDField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"Order {self.platform_order_id}"


class AbstractPlatformRefund(models.Model):
    """Base model for a refund from any platform."""

    class ProcessingStatus(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_refunds",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    platform_refund_id = models.CharField(max_length=100, db_index=True)
    platform_order_id = models.CharField(max_length=100, blank=True)

    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    reason = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.RECEIVED,
    )
    refund_date = models.DateField()
    journal_entry_id = models.UUIDField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"Refund {self.platform_refund_id}"


class AbstractPlatformPayout(models.Model):
    """Base model for a payout/settlement from any platform."""

    class ProcessingStatus(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_payouts",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    platform_payout_id = models.CharField(max_length=100, db_index=True)

    gross_amount = models.DecimalField(max_digits=18, decimal_places=2)
    fees = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    platform_status = models.CharField(max_length=50, blank=True)

    status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.RECEIVED,
    )
    payout_date = models.DateField()
    journal_entry_id = models.UUIDField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"Payout {self.platform_payout_id}"


class AbstractPlatformDispute(models.Model):
    """Base model for a dispute/chargeback from any platform."""

    class ProcessingStatus(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        WON = "WON", "Won"
        LOST = "LOST", "Lost"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_disputes",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    platform_dispute_id = models.CharField(max_length=100, db_index=True)
    platform_order_id = models.CharField(max_length=100, blank=True)

    amount = models.DecimalField(max_digits=18, decimal_places=2)
    fee = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    currency = models.CharField(max_length=3)
    reason = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.RECEIVED,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"Dispute {self.platform_dispute_id}"


# =============================================================================
# Concrete Model: PlatformSettlement
# =============================================================================

from decimal import Decimal


class PlatformSettlement(models.Model):
    """
    Financial transaction from a platform that isn't a sale or purchase.

    Covers payouts, disputes, fees, adjustments, and chargebacks from any
    connected platform (Shopify, Stripe, Amazon, etc.).

    Each settlement creates a journal entry through the command layer.
    This is the financial document equivalent of SalesInvoice for sales —
    it gives every platform money movement a proper record with FK links
    to the journal entry and optional bank statement line match.

    Examples:
    - PAYOUT: Shopify deposits $847.50 to bank (gross $900 - $52.50 fees)
    - DISPUTE: Customer disputes $150 order + $15 chargeback fee
    - DISPUTE_WON: Dispute reversed, $165 returned
    - FEE: Monthly platform subscription fee
    - ADJUSTMENT: Platform correction or reserve hold/release
    """

    class SettlementType(models.TextChoices):
        PAYOUT = "PAYOUT", "Payout"
        FEE = "FEE", "Fee"
        DISPUTE = "DISPUTE", "Dispute / Chargeback"
        DISPUTE_WON = "DISPUTE_WON", "Dispute Won / Reversed"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        POSTED = "POSTED", "Posted"
        VOIDED = "VOIDED", "Voided"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="platform_settlements",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Platform identification
    platform = models.CharField(
        max_length=50,
        help_text="Platform connector (shopify, stripe, amazon, etc.)",
    )
    platform_document_id = models.CharField(
        max_length=100,
        help_text="External ID (payout ID, dispute ID, etc.)",
    )
    settlement_type = models.CharField(
        max_length=20,
        choices=SettlementType.choices,
    )

    # Financial amounts
    gross_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Gross amount before fees",
    )
    fees = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Platform fees deducted",
    )
    net_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="Net amount (gross - fees). For payouts: amount deposited to bank.",
    )
    currency = models.CharField(max_length=3)

    # Settlement date
    settlement_date = models.DateField()

    # Status and posting
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_journal_entry = models.ForeignKey(
        "accounting.JournalEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_settlements",
        help_text="Journal entry created when posted",
    )

    # Bank reconciliation link
    matched_bank_line = models.ForeignKey(
        "accounting.BankStatementLine",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_settlements",
        help_text="Matched bank statement line (for reconciliation)",
    )

    # Metadata
    reference = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    auto_created = models.BooleanField(default=False)

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "platform_settlement"
        ordering = ["-settlement_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "platform", "platform_document_id", "settlement_type"],
                name="uniq_platform_settlement",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "platform", "settlement_type"]),
            models.Index(fields=["company", "settlement_date"]),
            models.Index(fields=["company", "status"]),
        ]
        verbose_name = "Platform Settlement"
        verbose_name_plural = "Platform Settlements"

    def __str__(self):
        return f"{self.platform} {self.settlement_type} {self.platform_document_id} ({self.net_amount} {self.currency})"
