# purchases/models.py
"""
Purchases Module Models for Nxentra ERP.

This module contains models for:
- PurchaseBill: Purchase bill/invoice headers
- PurchaseBillLine: Purchase bill line items

These are WRITE MODELS that are mutable until posted.
Posting creates immutable events that project to JournalEntries.

Note: Item, TaxCode, and PostingProfile are defined in sales.models
and are shared between sales and purchases.
"""

import uuid
from decimal import Decimal

from django.db import models

from accounting.models import Account, AnalysisDimensionValue, Vendor
from accounts.models import Company, ProjectionWriteGuard, User
from sales.models import Item, PostingProfile, TaxCode

# =============================================================================
# Purchase Order
# =============================================================================

class PurchaseOrder(ProjectionWriteGuard):
    """
    Purchase Order header.

    Workflow: DRAFT -> APPROVED -> PARTIALLY_RECEIVED / FULLY_RECEIVED -> CLOSED
    - DRAFT: PO being edited, can be modified
    - APPROVED: PO confirmed, ready for goods receipt
    - PARTIALLY_RECEIVED: Some goods received via GR
    - FULLY_RECEIVED: All goods received
    - CLOSED: Manually closed (all billing done or remaining qty abandoned)
    - CANCELLED: PO cancelled before any receipt

    POs are commitment/planning documents — they create NO journal entries.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        APPROVED = "APPROVED", "Approved"
        PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED", "Partially Received"
        FULLY_RECEIVED = "FULLY_RECEIVED", "Fully Received"
        CLOSED = "CLOSED", "Closed"
        CANCELLED = "CANCELLED", "Cancelled"

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="purchase_orders")
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    order_number = models.CharField(max_length=50, help_text="Auto-generated as PO-XXXXXX")
    order_date = models.DateField()
    expected_delivery_date = models.DateField(null=True, blank=True)

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="purchase_orders")
    posting_profile = models.ForeignKey(PostingProfile, on_delete=models.PROTECT, related_name="+")

    currency = models.CharField(max_length=3, blank=True, default="")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))

    subtotal = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    total_discount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    total_tax = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    notes = models.TextField(blank=True, default="")
    reference = models.CharField(max_length=100, blank=True, default="")
    shipping_address = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-order_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "order_number"],
                name="uniq_po_number_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "vendor"]),
            models.Index(fields=["company", "order_date"]),
        ]

    def __str__(self):
        return f"PO-{self.order_number}"

    def recalculate_totals(self):
        from django.db.models import Sum
        totals = self.lines.aggregate(
            subtotal=Sum("gross_amount"),
            total_discount=Sum("discount_amount"),
            total_tax=Sum("tax_amount"),
            total_amount=Sum("line_total"),
        )
        self.subtotal = totals["subtotal"] or Decimal("0")
        self.total_discount = totals["total_discount"] or Decimal("0")
        self.total_tax = totals["total_tax"] or Decimal("0")
        self.total_amount = totals["total_amount"] or Decimal("0")

    def update_receipt_status(self):
        """Update status based on received quantities across all lines."""
        lines = self.lines.all()
        if not lines.exists():
            return
        all_received = all(line.qty_received >= line.quantity for line in lines)
        any_received = any(line.qty_received > 0 for line in lines)
        if all_received:
            self.status = self.Status.FULLY_RECEIVED
        elif any_received:
            self.status = self.Status.PARTIALLY_RECEIVED


class PurchaseOrderLine(ProjectionWriteGuard):
    """Purchase order line item with receipt tracking."""

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="+")
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    line_number = models.PositiveIntegerField()

    item = models.ForeignKey(Item, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    description = models.CharField(max_length=500)
    description_ar = models.CharField(max_length=500, blank=True, default="")

    quantity = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    tax_code = models.ForeignKey(TaxCode, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0"))

    gross_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    net_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    line_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    dimension_values = models.ManyToManyField(AnalysisDimensionValue, blank=True, related_name="+")

    # 3-way matching counters
    qty_received = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0"))
    qty_billed = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0"))

    class Meta:
        ordering = ["line_number"]
        constraints = [
            models.UniqueConstraint(fields=["order", "line_number"], name="uniq_po_line_number"),
        ]

    def __str__(self):
        return f"{self.order.order_number}:{self.line_number}"

    def calculate(self):
        self.gross_amount = self.quantity * self.unit_price
        self.net_amount = self.gross_amount - self.discount_amount
        self.tax_amount = self.net_amount * self.tax_rate
        self.line_total = self.net_amount + self.tax_amount

    @property
    def qty_outstanding(self):
        return self.quantity - self.qty_received

    @property
    def qty_unbilled(self):
        return self.quantity - self.qty_billed


# =============================================================================
# Goods Receipt
# =============================================================================

class GoodsReceipt(ProjectionWriteGuard):
    """
    Goods Receipt Note (GRN) — records physical receipt of goods against a PO.

    Workflow: DRAFT -> POSTED -> VOIDED
    - DRAFT: Receipt being prepared
    - POSTED: Goods received, stock updated, PO qty_received incremented
    - VOIDED: Receipt reversed, stock reversed, PO qty_received decremented

    GRs create NO journal entries — accounting happens at bill posting.
    GRs DO create stock receipts for inventory items.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        POSTED = "POSTED", "Posted"
        VOIDED = "VOIDED", "Voided"

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="goods_receipts")
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    receipt_number = models.CharField(max_length=50, help_text="Auto-generated as GRN-XXXXXX")
    receipt_date = models.DateField()

    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="goods_receipts")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="goods_receipts")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-receipt_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "receipt_number"],
                name="uniq_grn_number_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "purchase_order"]),
        ]

    def __str__(self):
        return f"GRN-{self.receipt_number}"


class GoodsReceiptLine(ProjectionWriteGuard):
    """Goods receipt line — qty received against a specific PO line."""

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="+")
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    line_number = models.PositiveIntegerField()

    po_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipt_lines")
    item = models.ForeignKey(Item, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    description = models.CharField(max_length=500)

    qty_received = models.DecimalField(max_digits=18, decimal_places=4)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=2, help_text="Copied from PO line unit_price")

    class Meta:
        ordering = ["line_number"]
        constraints = [
            models.UniqueConstraint(fields=["receipt", "line_number"], name="uniq_gr_line_number"),
        ]

    def __str__(self):
        return f"{self.receipt.receipt_number}:{self.line_number}"


# =============================================================================
# Purchase Bill (existing)
# =============================================================================

class PurchaseBill(ProjectionWriteGuard):
    """
    Purchase Bill header.

    Workflow: DRAFT -> POSTED -> VOIDED
    - DRAFT: Bill being edited, can be modified
    - POSTED: Bill is finalized, journal entry created
    - VOIDED: Bill has been cancelled (reversing entry created)

    When posted, creates a journal entry:
    - Credit: AP Control (from posting_profile.control_account)
    - Debit: Expense accounts (from lines)
    - Debit: Input VAT (aggregated tax)
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        POSTED = "POSTED", "Posted"
        VOIDED = "VOIDED", "Voided"

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="purchase_bills",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    # Document info
    bill_number = models.CharField(
        max_length=50,
        help_text="Bill/Invoice number from vendor",
    )

    bill_date = models.DateField(
        help_text="Date on the vendor's bill",
    )

    due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Payment due date",
    )

    # Counterparty
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.PROTECT,
        related_name="bills",
    )

    posting_profile = models.ForeignKey(
        PostingProfile,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Posting profile (determines AP control account)",
    )

    # Multi-currency support
    currency = models.CharField(
        max_length=3,
        blank=True,
        default="",
        help_text="Bill currency (ISO 4217). Empty = company default currency.",
    )
    exchange_rate = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("1"),
        help_text="Exchange rate to functional currency at bill date",
    )

    # Calculated totals (updated when lines change)
    subtotal = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Sum of line gross amounts",
    )

    total_discount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Sum of line discounts",
    )

    total_tax = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Sum of line tax amounts",
    )

    total_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Total bill amount (subtotal - discount + tax)",
    )

    # Status and posting
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    posted_at = models.DateTimeField(null=True, blank=True)

    posted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    posted_journal_entry = models.ForeignKey(
        "accounting.JournalEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_bills",
        help_text="Journal entry created when posted",
    )

    # Optional link to Purchase Order (for 3-way matching)
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bills",
        help_text="Purchase order this bill is matched against",
    )

    # Metadata
    notes = models.TextField(blank=True, default="")
    reference = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="External reference (PO number, etc.)",
    )

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-bill_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "vendor", "bill_number"],
                name="uniq_bill_number_per_vendor",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "vendor"]),
            models.Index(fields=["company", "bill_date"]),
        ]
        verbose_name = "Purchase Bill"
        verbose_name_plural = "Purchase Bills"

    def __str__(self):
        return f"BILL-{self.bill_number}"

    def recalculate_totals(self):
        """
        Recalculate totals from lines.

        Call this after modifying lines.
        """
        from django.db.models import Sum

        totals = self.lines.aggregate(
            subtotal=Sum("gross_amount"),
            total_discount=Sum("discount_amount"),
            total_tax=Sum("tax_amount"),
            total_amount=Sum("line_total"),
        )

        self.subtotal = totals["subtotal"] or Decimal("0")
        self.total_discount = totals["total_discount"] or Decimal("0")
        self.total_tax = totals["total_tax"] or Decimal("0")
        self.total_amount = totals["total_amount"] or Decimal("0")


# =============================================================================
# Purchase Credit Note (Vendor Return / Debit Note)
# =============================================================================

class PurchaseCreditNote(ProjectionWriteGuard):
    """
    Purchase Credit Note (Debit Note / Vendor Return).

    Reverses or adjusts a posted purchase bill.
    When posted, creates a journal entry that reduces AP liability.

    Workflow: DRAFT -> POSTED -> VOIDED
    - DRAFT: Credit note being prepared
    - POSTED: Finalized, journal entry created (reduces AP)
    - VOIDED: Reversed via reversing journal entry

    Journal Entry on post:
    - Debit: AP Control (reduces payable to vendor)
    - Credit: Expense/Inventory accounts (reverses original cost)
    - Credit: Input VAT (reverses recoverable tax)
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        POSTED = "POSTED", "Posted"
        VOIDED = "VOIDED", "Voided"

    class Reason(models.TextChoices):
        RETURN = "RETURN", "Goods Return"
        PRICE_ADJUSTMENT = "PRICE_ADJUSTMENT", "Price Adjustment"
        TAX_CORRECTION = "TAX_CORRECTION", "Tax Correction"
        DAMAGED = "DAMAGED", "Damaged Goods"
        OTHER = "OTHER", "Other"

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="purchase_credit_notes")
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    credit_note_number = models.CharField(max_length=50, help_text="Auto-generated as PCN-XXXXXX")
    credit_note_date = models.DateField()

    # Link to original bill
    bill = models.ForeignKey(
        PurchaseBill,
        on_delete=models.PROTECT,
        related_name="credit_notes",
        help_text="Original purchase bill being credited",
    )

    # Counterparty (denormalized from bill for query convenience)
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="purchase_credit_notes")
    posting_profile = models.ForeignKey(PostingProfile, on_delete=models.PROTECT, related_name="+")

    # Reason
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.RETURN)
    reason_notes = models.TextField(blank=True, default="")

    # Multi-currency
    currency = models.CharField(max_length=3, blank=True, default="")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))

    # Totals
    subtotal = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    total_discount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    total_tax = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    # Status and posting
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    posted_journal_entry = models.ForeignKey(
        "accounting.JournalEntry",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="purchase_credit_notes",
    )

    # Metadata
    notes = models.TextField(blank=True, default="")

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-credit_note_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "credit_note_number"],
                name="uniq_pcn_number_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "vendor"]),
            models.Index(fields=["company", "bill"]),
        ]
        verbose_name = "Purchase Credit Note"
        verbose_name_plural = "Purchase Credit Notes"

    def __str__(self):
        return f"PCN-{self.credit_note_number}"

    def recalculate_totals(self):
        from django.db.models import Sum
        totals = self.lines.aggregate(
            subtotal=Sum("gross_amount"),
            total_discount=Sum("discount_amount"),
            total_tax=Sum("tax_amount"),
            total_amount=Sum("line_total"),
        )
        self.subtotal = totals["subtotal"] or Decimal("0")
        self.total_discount = totals["total_discount"] or Decimal("0")
        self.total_tax = totals["total_tax"] or Decimal("0")
        self.total_amount = totals["total_amount"] or Decimal("0")


class PurchaseCreditNoteLine(ProjectionWriteGuard):
    """
    Line item within a purchase credit note.

    Calculation pipeline (same as bill lines):
    - gross_amount = quantity * unit_price
    - net_amount = gross_amount - discount_amount
    - tax_amount = net_amount * tax_rate
    - line_total = net_amount + tax_amount
    """

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    credit_note = models.ForeignKey(PurchaseCreditNote, on_delete=models.CASCADE, related_name="lines")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="+")
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    line_number = models.PositiveIntegerField()

    # Optional link to original bill line
    bill_line = models.ForeignKey(
        "purchases.PurchaseBillLine",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="credit_note_lines",
    )

    item = models.ForeignKey(Item, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    description = models.CharField(max_length=500)
    description_ar = models.CharField(max_length=500, blank=True, default="")

    quantity = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    tax_code = models.ForeignKey(TaxCode, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0"))

    gross_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    net_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    line_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    dimension_values = models.ManyToManyField(AnalysisDimensionValue, blank=True, related_name="+")

    class Meta:
        ordering = ["line_number"]
        constraints = [
            models.UniqueConstraint(fields=["credit_note", "line_number"], name="uniq_pcn_line_number"),
        ]
        verbose_name = "Purchase Credit Note Line"
        verbose_name_plural = "Purchase Credit Note Lines"

    def __str__(self):
        return f"{self.credit_note.credit_note_number}:{self.line_number}"

    def calculate(self):
        self.gross_amount = self.quantity * self.unit_price
        self.net_amount = self.gross_amount - self.discount_amount
        self.tax_amount = self.net_amount * self.tax_rate
        self.line_total = self.net_amount + self.tax_amount


class PurchaseBillLine(ProjectionWriteGuard):
    """
    Purchase Bill line item.

    Calculation pipeline:
    - gross_amount = quantity * unit_price
    - net_amount = gross_amount - discount_amount
    - tax_amount = net_amount * tax_rate
    - line_total = net_amount + tax_amount

    When the bill is posted:
    - Debit: account (expense) for net_amount
    - Debit: tax_code.tax_account (Input VAT) for tax_amount
    """

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    bill = models.ForeignKey(
        PurchaseBill,
        on_delete=models.CASCADE,
        related_name="lines",
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="+",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    line_number = models.PositiveIntegerField(
        help_text="Line ordering within the bill",
    )

    # Optional link to PO line (for 3-way matching)
    po_line = models.ForeignKey(
        PurchaseOrderLine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bill_lines",
        help_text="PO line this bill line is matched against",
    )

    # Item (optional - can be ad-hoc line)
    item = models.ForeignKey(
        Item,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Optional item reference (auto-fills defaults)",
    )

    description = models.CharField(max_length=500)
    description_ar = models.CharField(max_length=500, blank=True, default="")

    # Pricing inputs
    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("1"),
    )

    unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=2,
    )

    discount_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Line discount amount",
    )

    # Tax
    tax_code = models.ForeignKey(
        TaxCode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=Decimal("0"),
        help_text="Tax rate at time of bill (copied from tax_code)",
    )

    # Calculated amounts
    gross_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="quantity * unit_price",
    )

    net_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="gross_amount - discount_amount",
    )

    tax_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="net_amount * tax_rate",
    )

    line_total = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="net_amount + tax_amount",
    )

    # GL Account for expense
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Expense account for this line",
    )

    # Dimensions (optional analysis tags)
    dimension_values = models.ManyToManyField(
        AnalysisDimensionValue,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["line_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["bill", "line_number"],
                name="uniq_bill_line_number",
            ),
        ]
        indexes = [
            models.Index(fields=["bill", "line_number"]),
        ]
        verbose_name = "Purchase Bill Line"
        verbose_name_plural = "Purchase Bill Lines"

    def __str__(self):
        return f"{self.bill.bill_number}:{self.line_number}"

    def calculate(self):
        """
        Calculate line amounts from inputs.

        Call this before saving to update calculated fields.
        """
        self.gross_amount = self.quantity * self.unit_price
        self.net_amount = self.gross_amount - self.discount_amount
        self.tax_amount = self.net_amount * self.tax_rate
        self.line_total = self.net_amount + self.tax_amount
