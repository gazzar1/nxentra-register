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
    One company can connect one Shopify store (enforced by unique constraint).
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending OAuth"
        ACTIVE = "ACTIVE", "Active"
        DISCONNECTED = "DISCONNECTED", "Disconnected"
        ERROR = "ERROR", "Error"

    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="shopify_store",
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

    last_sync_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "shopify_store"

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
