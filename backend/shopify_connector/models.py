# shopify_connector/models.py
"""
Shopify connector models.

ShopifyStore: connection state for a merchant's Shopify store.
ShopifyOrder/ShopifyRefund: local copies of Shopify data for reconciliation.
"""

import uuid

from django.db import models
from accounts.models import Company


class ShopifyStore(models.Model):
    """
    Represents a connected Shopify store for a company.

    Stores OAuth credentials and webhook state.
    A company can connect multiple Shopify stores (e.g. regional stores).
    Each store is identified by its unique shop_domain.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending OAuth"
        ACTIVE = "ACTIVE", "Active"
        DISCONNECTED = "DISCONNECTED", "Disconnected"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="shopify_stores",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    shop_domain = models.CharField(
        max_length=255,
        help_text="e.g. my-store.myshopify.com",
    )
    access_token = models.CharField(
        max_length=255,
        blank=True,
        help_text="Shopify Admin API access token (encrypted at rest in production).",
    )
    scopes = models.CharField(
        max_length=500,
        blank=True,
        help_text="Granted OAuth scopes, comma-separated.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    webhooks_registered = models.BooleanField(default=False)

    # EDIM source system link (created during connection)
    source_system_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="FK to edim.SourceSystem for this store.",
    )

    # OAuth state parameter for CSRF protection
    oauth_nonce = models.CharField(max_length=64, blank=True)

    # Default accounts for auto-creating Items from Shopify products
    default_inventory_account = models.ForeignKey(
        "accounting.Account", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
        help_text="Default inventory asset account for auto-created Items",
    )
    default_cogs_account = models.ForeignKey(
        "accounting.Account", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
        help_text="Default COGS expense account for auto-created Items",
    )
    product_sync_enabled = models.BooleanField(
        default=False,
        help_text="Auto-create Items from Shopify product webhooks",
    )

    last_sync_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "shopify_store"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "shop_domain"],
                name="uniq_company_shop_domain",
            ),
        ]

    def __str__(self):
        return f"{self.shop_domain} ({self.status})"


class ShopifyOrder(models.Model):
    """
    Local record of a Shopify order for reconciliation and audit.

    Created when we receive an orders/paid webhook.
    """

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="shopify_orders",
    )
    store = models.ForeignKey(
        ShopifyStore,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Shopify identifiers
    shopify_order_id = models.BigIntegerField(db_index=True)
    shopify_order_number = models.CharField(max_length=50)
    shopify_order_name = models.CharField(
        max_length=50, blank=True,
        help_text="Display name like #1001",
    )

    # Financial data
    total_price = models.DecimalField(max_digits=18, decimal_places=2)
    subtotal_price = models.DecimalField(max_digits=18, decimal_places=2)
    total_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_discounts = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    currency = models.CharField(max_length=3)

    # Payment info
    financial_status = models.CharField(max_length=30, blank=True)
    gateway = models.CharField(max_length=100, blank=True)

    # Timestamps
    shopify_created_at = models.DateTimeField()
    order_date = models.DateField(help_text="Date used for journal entry")

    # Processing state
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    event_id = models.UUIDField(
        null=True, blank=True,
        help_text="BusinessEvent ID created for this order.",
    )
    journal_entry_id = models.UUIDField(
        null=True, blank=True,
        help_text="JournalEntry public_id created by projection.",
    )
    error_message = models.TextField(blank=True)

    # Raw webhook payload for debugging
    raw_payload = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "shopify_order"
        unique_together = [("company", "shopify_order_id")]
        ordering = ["-shopify_created_at"]

    def __str__(self):
        return f"Order {self.shopify_order_name} ({self.currency} {self.total_price})"


class ShopifyRefund(models.Model):
    """Local record of a Shopify refund."""

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="shopify_refunds",
    )
    order = models.ForeignKey(
        ShopifyOrder,
        on_delete=models.CASCADE,
        related_name="refunds",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    shopify_refund_id = models.BigIntegerField(db_index=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    reason = models.CharField(max_length=255, blank=True)
    shopify_created_at = models.DateTimeField()

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    event_id = models.UUIDField(null=True, blank=True)
    journal_entry_id = models.UUIDField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    raw_payload = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "shopify_refund"
        unique_together = [("company", "shopify_refund_id")]

    def __str__(self):
        return f"Refund {self.shopify_refund_id} ({self.currency} {self.amount})"


class ShopifyPayout(models.Model):
    """
    Local record of a Shopify Payments payout.

    Tracks when Shopify transfers funds to the merchant's bank account,
    including gross amount, fees deducted, and net deposited.
    """

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="shopify_payouts",
    )
    store = models.ForeignKey(
        ShopifyStore,
        on_delete=models.CASCADE,
        related_name="payouts",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Shopify payout identifiers
    shopify_payout_id = models.BigIntegerField(db_index=True)

    # Financial data
    gross_amount = models.DecimalField(
        max_digits=18, decimal_places=2,
        help_text="Total amount before fees",
    )
    fees = models.DecimalField(
        max_digits=18, decimal_places=2, default=0,
        help_text="Processing fees deducted by Shopify",
    )
    net_amount = models.DecimalField(
        max_digits=18, decimal_places=2,
        help_text="Amount deposited to bank (gross - fees)",
    )
    currency = models.CharField(max_length=3)

    # Fee breakdown (from Shopify summary — actual, not computed)
    charges_fee = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    refunds_fee = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    adjustments_fee = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    charges_gross = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    refunds_gross = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    adjustments_gross = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # Payout status from Shopify (paid, in_transit, scheduled, etc.)
    shopify_status = models.CharField(max_length=30, blank=True)

    # Date the payout was initiated
    payout_date = models.DateField(help_text="Date used for journal entry")

    # Processing state
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    event_id = models.UUIDField(
        null=True, blank=True,
        help_text="BusinessEvent ID created for this payout.",
    )
    journal_entry_id = models.UUIDField(
        null=True, blank=True,
        help_text="JournalEntry public_id created by projection.",
    )
    error_message = models.TextField(blank=True)
    raw_payload = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "shopify_payout"
        unique_together = [("company", "shopify_payout_id")]
        ordering = ["-payout_date"]

    def __str__(self):
        return f"Payout {self.shopify_payout_id} ({self.currency} {self.net_amount})"


class ShopifyFulfillment(models.Model):
    """
    Local record of a Shopify fulfillment for COGS tracking.

    Created when we receive a fulfillments/create webhook.
    Triggers inventory deduction and COGS journal entry.
    """

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        PARTIAL = "PARTIAL", "Partially Matched"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="shopify_fulfillments",
    )
    order = models.ForeignKey(
        ShopifyOrder,
        on_delete=models.CASCADE,
        related_name="fulfillments",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Shopify identifiers
    shopify_fulfillment_id = models.BigIntegerField(db_index=True)
    shopify_order_id = models.BigIntegerField()

    # Fulfillment details
    tracking_number = models.CharField(max_length=255, blank=True)
    tracking_company = models.CharField(max_length=255, blank=True)
    shopify_status = models.CharField(
        max_length=30, blank=True,
        help_text="Shopify fulfillment status (success, cancelled, error)",
    )
    shopify_created_at = models.DateTimeField()

    # COGS data (computed during processing)
    total_cogs = models.DecimalField(
        max_digits=18, decimal_places=2, default=0,
        help_text="Total cost of goods for this fulfillment",
    )
    currency = models.CharField(max_length=3)
    matched_items = models.IntegerField(
        default=0,
        help_text="Number of line items matched to inventory Items",
    )
    total_items = models.IntegerField(
        default=0,
        help_text="Total number of line items in the fulfillment",
    )

    # Processing state
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    event_id = models.UUIDField(
        null=True, blank=True,
        help_text="BusinessEvent ID created for this fulfillment.",
    )
    journal_entry_id = models.UUIDField(
        null=True, blank=True,
        help_text="COGS JournalEntry public_id created by projection.",
    )
    error_message = models.TextField(blank=True)
    raw_payload = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "shopify_fulfillment"
        unique_together = [("company", "shopify_fulfillment_id")]
        ordering = ["-shopify_created_at"]

    def __str__(self):
        return f"Fulfillment {self.shopify_fulfillment_id} (COGS {self.currency} {self.total_cogs})"


class ShopifyPayoutTransaction(models.Model):
    """
    Individual transaction within a Shopify payout.

    Each payout consists of multiple transactions (charges, refunds,
    adjustments, fees). Fetched from Shopify's Payout Transactions API
    for Layer 2 (platform settlement) reconciliation.
    """

    class TransactionType(models.TextChoices):
        CHARGE = "charge", "Charge"
        REFUND = "refund", "Refund"
        ADJUSTMENT = "adjustment", "Adjustment"
        PAYOUT = "payout", "Payout"
        OTHER = "other", "Other"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="shopify_payout_transactions",
    )
    payout = models.ForeignKey(
        ShopifyPayout,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Shopify identifiers
    shopify_transaction_id = models.BigIntegerField(db_index=True)
    transaction_type = models.CharField(
        max_length=20,
        choices=TransactionType.choices,
        default=TransactionType.OTHER,
    )

    # Financial data
    amount = models.DecimalField(
        max_digits=18, decimal_places=2,
        help_text="Gross amount of the transaction",
    )
    fee = models.DecimalField(
        max_digits=18, decimal_places=2, default=0,
        help_text="Fee charged on this transaction",
    )
    net = models.DecimalField(
        max_digits=18, decimal_places=2,
        help_text="Net amount (amount - fee)",
    )
    currency = models.CharField(max_length=3)

    # Link to local order/refund if applicable
    source_order_id = models.BigIntegerField(
        null=True, blank=True,
        help_text="Shopify order ID this transaction relates to",
    )
    source_type = models.CharField(
        max_length=30, blank=True,
        help_text="Source type from Shopify (e.g. 'order', 'refund', 'adjustment')",
    )

    # Verification state
    verified = models.BooleanField(
        default=False,
        help_text="Whether this transaction has been matched to a local order/refund",
    )
    local_order = models.ForeignKey(
        ShopifyOrder,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="payout_transactions",
    )

    processed_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When this transaction was processed by Shopify",
    )
    raw_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "shopify_payout_transaction"
        unique_together = [("company", "shopify_transaction_id")]
        ordering = ["-processed_at"]

    def __str__(self):
        return f"PayoutTxn {self.shopify_transaction_id} ({self.transaction_type}: {self.currency} {self.amount})"


class ShopifyDispute(models.Model):
    """
    Local record of a Shopify payment dispute (chargeback).

    Created when we receive a disputes/create or disputes/update webhook.
    Triggers a reversal journal entry to move funds from Clearing back
    and record the chargeback loss or receivable.
    """

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        WON = "WON", "Won"
        LOST = "LOST", "Lost"
        ERROR = "ERROR", "Error"

    class DisputeStatus(models.TextChoices):
        """Shopify dispute statuses."""
        NEEDS_RESPONSE = "needs_response", "Needs Response"
        UNDER_REVIEW = "under_review", "Under Review"
        CHARGE_REFUNDED = "charge_refunded", "Charge Refunded"
        ACCEPTED = "accepted", "Accepted"
        WON = "won", "Won"
        LOST = "lost", "Lost"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="shopify_disputes",
    )
    store = models.ForeignKey(
        ShopifyStore,
        on_delete=models.CASCADE,
        related_name="disputes",
    )
    order = models.ForeignKey(
        ShopifyOrder,
        on_delete=models.CASCADE,
        related_name="disputes",
        null=True, blank=True,
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Shopify identifiers
    shopify_dispute_id = models.BigIntegerField(db_index=True)
    shopify_order_id = models.BigIntegerField(null=True, blank=True)

    # Financial data
    amount = models.DecimalField(
        max_digits=18, decimal_places=2,
        help_text="Disputed amount",
    )
    currency = models.CharField(max_length=3)
    fee = models.DecimalField(
        max_digits=18, decimal_places=2, default=0,
        help_text="Chargeback fee charged by payment processor",
    )

    # Dispute details
    reason = models.CharField(max_length=100, blank=True)
    shopify_dispute_status = models.CharField(
        max_length=30, choices=DisputeStatus.choices,
        blank=True,
    )
    evidence_due_by = models.DateTimeField(null=True, blank=True)
    finalized_on = models.DateField(null=True, blank=True)

    # Processing state
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    event_id = models.UUIDField(null=True, blank=True)
    journal_entry_id = models.UUIDField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    raw_payload = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "shopify_dispute"
        unique_together = [("company", "shopify_dispute_id")]
        ordering = ["-created_at"]


class ShopifyProduct(models.Model):
    """
    Maps a Shopify product variant to a Nxentra Item.

    Each Shopify variant (which has its own SKU) maps to one Item.
    The parent Shopify product ID is stored for grouping/display.
    """

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="shopify_products",
    )
    store = models.ForeignKey(
        ShopifyStore, on_delete=models.CASCADE, related_name="products",
    )

    # Shopify identifiers
    shopify_product_id = models.BigIntegerField(db_index=True)
    shopify_variant_id = models.BigIntegerField(db_index=True)

    # Shopify data snapshot
    title = models.CharField(max_length=500)
    variant_title = models.CharField(max_length=500, blank=True, default="")
    sku = models.CharField(max_length=255, blank=True, default="", db_index=True)
    barcode = models.CharField(max_length=255, blank=True, default="")
    shopify_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    shopify_inventory_item_id = models.BigIntegerField(
        null=True, blank=True,
        help_text="Shopify inventory_item_id for future inventory level sync",
    )

    # Link to Nxentra Item
    item = models.ForeignKey(
        "sales.Item", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="shopify_variants",
    )

    # Sync state
    auto_created = models.BooleanField(
        default=False,
        help_text="Whether the linked Item was auto-created by sync",
    )
    last_synced_at = models.DateTimeField(auto_now=True)
    raw_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "shopify_product"
        unique_together = [("company", "shopify_variant_id")]
        ordering = ["-created_at"]

    def __str__(self):
        label = f"{self.title}"
        if self.variant_title:
            label += f" - {self.variant_title}"
        if self.sku:
            label += f" ({self.sku})"
        return label

    def __str__(self):
        return f"Dispute {self.shopify_dispute_id} ({self.currency} {self.amount})"
