# inventory/models.py
"""
Inventory Models for Nxentra ERP.

This module contains:
- Warehouse: Storage locations for inventory
- StockLedgerEntry: Immutable record of stock movements (SOURCE OF TRUTH)

The stock ledger is append-only. Corrections are made via reversing entries,
not by modifying existing records. This ensures full audit trail.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

from accounts.models import Company, ProjectionWriteGuard


class Warehouse(ProjectionWriteGuard):
    """
    Warehouse/Location for inventory storage.

    Each company has at least one default "MAIN" warehouse.
    Multi-location inventory is supported via multiple warehouses.

    Example warehouses:
    - MAIN: Primary warehouse
    - WH-01, WH-02: Additional storage locations
    - TRANSIT: Goods in transit
    """

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="warehouses",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    code = models.CharField(
        max_length=20,
        help_text="Warehouse code (e.g., MAIN, WH-01)",
    )

    name = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255, blank=True, default="")

    address = models.TextField(
        blank=True,
        default="",
        help_text="Physical address of the warehouse",
    )

    is_active = models.BooleanField(default=True)

    is_default = models.BooleanField(
        default=False,
        help_text="Default warehouse for new transactions",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Warehouse"
        verbose_name_plural = "Warehouses"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="uniq_warehouse_code_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "is_active"]),
            models.Index(fields=["company", "is_default"]),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_localized_name(self, language: str = "en") -> str:
        """Return localized name based on language preference."""
        if language == "ar" and self.name_ar:
            return self.name_ar
        return self.name


class StockLedgerEntry(ProjectionWriteGuard):
    """
    Immutable append-only stock ledger.

    This is the SOURCE OF TRUTH for inventory quantities and movements.
    Stock ledger entries drive:
    1. InventoryBalance projections (qty_on_hand, avg_cost, stock_value)
    2. Accounting entries (Inventory Dr/Cr, COGS Dr)

    Each entry records a stock movement (in or out) with its costing.

    RULES:
    - Never update/delete ledger lines after creation
    - Corrections happen via reversal documents
    - Each entry has a monotonic sequence number for ordering

    Movement direction:
    - qty_delta > 0: Stock IN (receipt from purchase, return from customer, adjustment up)
    - qty_delta < 0: Stock OUT (issue to sale, return to vendor, adjustment down)
    """

    class SourceType(models.TextChoices):
        PURCHASE_BILL = "PURCHASE_BILL", "Purchase Bill"
        SALES_INVOICE = "SALES_INVOICE", "Sales Invoice"
        ADJUSTMENT = "ADJUSTMENT", "Inventory Adjustment"
        OPENING_BALANCE = "OPENING_BALANCE", "Opening Balance"
        TRANSFER_IN = "TRANSFER_IN", "Warehouse Transfer In"
        TRANSFER_OUT = "TRANSFER_OUT", "Warehouse Transfer Out"
        SALES_RETURN = "SALES_RETURN", "Sales Return"
        PURCHASE_RETURN = "PURCHASE_RETURN", "Purchase Return"
        GOODS_RECEIPT = "GOODS_RECEIPT", "Goods Receipt"

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="stock_ledger_entries",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    # Monotonic sequence for ordering
    sequence = models.PositiveIntegerField(
        editable=False,
        help_text="Sequential order within company (monotonic)",
    )

    # Source document linkage
    source_type = models.CharField(
        max_length=20,
        choices=SourceType.choices,
        help_text="Type of document that created this entry",
    )

    source_id = models.UUIDField(
        help_text="Public ID of source document (bill, invoice, adjustment)",
    )

    source_line_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="Public ID of source document line (for line-level tracking)",
    )

    # What moved
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="stock_entries",
    )

    item = models.ForeignKey(
        "sales.Item",
        on_delete=models.PROTECT,
        related_name="stock_entries",
    )

    # Movement amounts
    qty_delta = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Quantity change: positive = IN, negative = OUT",
    )

    unit_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        help_text="Unit cost in base currency. For IN: purchase cost. For OUT: issue cost (avg_cost at time of issue).",
    )

    value_delta = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="Value change = qty_delta * unit_cost",
    )

    # Snapshot of costing method at time of entry
    costing_method_snapshot = models.CharField(
        max_length=20,
        help_text="Costing method used at time of entry (WEIGHTED_AVERAGE, FIFO, LIFO)",
    )

    # Running balances (denormalized for query efficiency)
    qty_balance_after = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Quantity on hand after this entry",
    )

    value_balance_after = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="Total stock value after this entry",
    )

    avg_cost_after = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        help_text="Weighted average cost after this entry",
    )

    # Audit fields
    posted_at = models.DateTimeField(
        help_text="Timestamp when this entry was posted",
    )

    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="User who posted this entry",
    )

    # Link to accounting entry
    journal_entry = models.ForeignKey(
        "accounting.JournalEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_ledger_entries",
        help_text="Corresponding accounting journal entry",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        verbose_name = "Stock Ledger Entry"
        verbose_name_plural = "Stock Ledger Entries"
        indexes = [
            models.Index(fields=["company", "item", "warehouse"]),
            models.Index(fields=["company", "sequence"]),
            models.Index(fields=["company", "source_type", "source_id"]),
            models.Index(fields=["company", "posted_at"]),
            models.Index(fields=["company", "item", "posted_at"]),
        ]

    def __str__(self):
        direction = "IN" if self.qty_delta > 0 else "OUT"
        return f"SLE-{self.sequence}: {self.item.code} {direction} {abs(self.qty_delta)}"

    def save(self, *args, **kwargs):
        # Calculate value_delta if not set
        if self.value_delta is None or self.value_delta == Decimal("0"):
            self.value_delta = self.qty_delta * self.unit_cost
        super().save(*args, **kwargs)


class FifoLayer(ProjectionWriteGuard):
    """
    FIFO cost layer — tracks remaining quantity from each receipt batch.

    When goods are received, a new layer is created with the receipt qty and cost.
    When goods are issued with FIFO costing, layers are consumed oldest-first
    (by sequence number). Partially consumed layers reduce qty_remaining.

    This model is only used when item.costing_method == 'FIFO'.
    """

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="fifo_layers",
    )

    item = models.ForeignKey(
        "sales.Item",
        on_delete=models.PROTECT,
        related_name="fifo_layers",
    )

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="fifo_layers",
    )

    # Link to the stock ledger entry that created this layer
    receipt_entry = models.ForeignKey(
        StockLedgerEntry,
        on_delete=models.PROTECT,
        related_name="fifo_layer",
        help_text="The stock ledger entry that created this FIFO layer",
    )

    # Original receipt quantity (immutable after creation)
    qty_original = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Original quantity received in this layer",
    )

    # Remaining quantity (decremented on each issue)
    qty_remaining = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Quantity remaining in this layer (decremented by FIFO issues)",
    )

    # Cost per unit for this layer (immutable — the purchase cost)
    unit_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        help_text="Unit cost at time of receipt (immutable)",
    )

    # Ordering — monotonic sequence to determine FIFO order
    sequence = models.PositiveIntegerField(
        help_text="Receipt sequence for FIFO ordering (lower = older = consumed first)",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        indexes = [
            models.Index(fields=["company", "item", "warehouse", "sequence"]),
            models.Index(fields=["company", "item", "warehouse", "qty_remaining"]),
        ]
        verbose_name = "FIFO Layer"
        verbose_name_plural = "FIFO Layers"

    def __str__(self):
        return f"FIFO Layer #{self.sequence}: {self.item.code} {self.qty_remaining}/{self.qty_original} @ {self.unit_cost}"

    @property
    def is_exhausted(self):
        return self.qty_remaining <= 0


class StockLedgerSequenceCounter(models.Model):
    """
    Counter for generating monotonic stock ledger sequence numbers.

    Similar to EventSequenceCounter in events app.
    Each company has its own counter.
    """

    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="stock_ledger_counter",
        primary_key=True,
    )

    last_sequence = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Stock Ledger Sequence Counter"
        verbose_name_plural = "Stock Ledger Sequence Counters"

    def __str__(self):
        return f"StockLedgerCounter({self.company_id}): {self.last_sequence}"
