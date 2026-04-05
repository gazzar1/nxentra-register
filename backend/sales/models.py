# sales/models.py
"""
Sales Module Models for Nxentra ERP.

This module contains models for:
- Item: Product/Service catalog with default accounts
- TaxCode: Tax rate configuration (INPUT/OUTPUT)
- PostingProfile: Control account configuration (AR/AP)
- SalesInvoice: Sales invoice headers
- SalesInvoiceLine: Sales invoice line items

These are WRITE MODELS that are mutable until posted.
Posting creates immutable events that project to JournalEntries.
"""

import uuid
from decimal import Decimal

from django.db import models

from accounting.models import Account, AnalysisDimensionValue, Customer
from accounts.models import Company, ProjectionWriteGuard, User


class Item(ProjectionWriteGuard):
    """
    Product/Service catalog item.

    Items provide default accounts and pricing for invoice lines.
    When selecting an item on an invoice line, it auto-fills:
    - description
    - unit_price
    - account (sales_account or purchase_account)
    - tax_code

    Item Types:
    - INVENTORY: Tracked in stock ledger with perpetual inventory
    - SERVICE: No stock tracking, posted directly to expense/revenue
    - NON_STOCK: Purchased but not tracked in inventory (e.g., consumables)

    For INVENTORY items:
    - inventory_account is required (asset account for stock value)
    - cogs_account is required (expense account for cost of goods sold)
    - Posting sales creates COGS entries automatically
    """

    class ItemType(models.TextChoices):
        INVENTORY = "INVENTORY", "Inventory Item"
        SERVICE = "SERVICE", "Service"
        NON_STOCK = "NON_STOCK", "Non-Stock Item"

    class CostingMethod(models.TextChoices):
        WEIGHTED_AVERAGE = "WEIGHTED_AVERAGE", "Weighted Average"
        FIFO = "FIFO", "First In, First Out"
        LIFO = "LIFO", "Last In, First Out"

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="items",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    code = models.CharField(
        max_length=50,
        help_text="Item code (e.g., ITEM001)",
    )

    name = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255, blank=True, default="")

    description = models.TextField(blank=True, default="")
    description_ar = models.TextField(blank=True, default="")

    item_type = models.CharField(
        max_length=20,
        choices=ItemType.choices,
        default=ItemType.INVENTORY,
    )

    # Default accounts for sales and purchases
    sales_account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Default revenue account for sales",
    )

    purchase_account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Default expense account for purchases",
    )

    # Default pricing
    default_unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Default selling price",
    )

    default_cost = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Default purchase cost",
    )

    # Default tax code
    default_tax_code = models.ForeignKey(
        "TaxCode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Default tax code for this item",
    )

    # =========================================================================
    # Inventory-specific fields (required for INVENTORY item_type)
    # =========================================================================

    inventory_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Inventory asset account (required for INVENTORY items)",
    )

    cogs_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Cost of Goods Sold account (required for INVENTORY items)",
    )

    costing_method = models.CharField(
        max_length=20,
        choices=CostingMethod.choices,
        default=CostingMethod.WEIGHTED_AVERAGE,
        help_text="Costing method for inventory valuation",
    )

    # Calculated cost fields (non-editable, updated by inventory projections)
    average_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("0"),
        editable=False,
        help_text="Current weighted average cost (auto-calculated)",
    )

    last_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("0"),
        editable=False,
        help_text="Last purchase cost (auto-calculated)",
    )

    # Unit of measure for inventory
    uom = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Unit of measure (e.g., EA, KG, BOX)",
    )

    # Item photo
    image = models.ImageField(
        upload_to="items/",
        null=True,
        blank=True,
        help_text="Product photo (max 10MB, PNG/JPG/WEBP)",
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="uniq_item_code_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "is_active"]),
            models.Index(fields=["company", "item_type", "is_active"]),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_localized_name(self, language: str = "en") -> str:
        if language == "ar" and self.name_ar:
            return self.name_ar
        return self.name

    @property
    def is_inventory_item(self) -> bool:
        """Check if this item is tracked in inventory."""
        return self.item_type == self.ItemType.INVENTORY

    def clean(self):
        """Validate inventory items have required accounts."""
        from django.core.exceptions import ValidationError

        if self.item_type == self.ItemType.INVENTORY:
            errors = {}
            if not self.inventory_account_id:
                errors["inventory_account"] = "Inventory account is required for inventory items."
            if not self.cogs_account_id:
                errors["cogs_account"] = "COGS account is required for inventory items."
            if errors:
                raise ValidationError(errors)


class TaxCode(ProjectionWriteGuard):
    """
    Tax rate configuration.

    Tax codes are classified by direction:
    - OUTPUT: Collected VAT on sales (credit to VAT Payable)
    - INPUT: Deductible VAT on purchases (debit to Input VAT)

    The tax_account must be a liability account (typically VAT Payable or Input VAT).
    """

    class TaxDirection(models.TextChoices):
        INPUT = "INPUT", "Input (Purchases)"
        OUTPUT = "OUTPUT", "Output (Sales)"

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="tax_codes",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    code = models.CharField(
        max_length=20,
        help_text="Tax code (e.g., VAT15, ZERO)",
    )

    name = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100, blank=True, default="")

    description = models.TextField(blank=True, default="")

    rate = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        help_text="Tax rate as decimal (e.g., 0.15 = 15%)",
    )

    direction = models.CharField(
        max_length=10,
        choices=TaxDirection.choices,
        help_text="INPUT for purchases (deductible), OUTPUT for sales (collected)",
    )

    tax_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="GL account for tax (VAT Payable for OUTPUT, Input VAT for INPUT)",
    )

    # Recoverability flag for INPUT taxes
    recoverable = models.BooleanField(
        default=True,
        help_text=(
            "For INPUT (purchase) taxes: If True, tax is recoverable and goes to Input VAT account. "
            "If False, tax is non-recoverable and capitalizes into the inventory/expense cost. "
            "Has no effect on OUTPUT taxes."
        ),
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="uniq_taxcode_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "direction", "is_active"]),
        ]
        verbose_name = "Tax Code"
        verbose_name_plural = "Tax Codes"

    def __str__(self):
        return f"{self.code} ({self.rate * 100:.0f}%)"

    @property
    def rate_percentage(self) -> Decimal:
        """Return rate as percentage (e.g., 15 instead of 0.15)."""
        return self.rate * 100

    @property
    def is_recoverable(self) -> bool:
        """Check if this tax is recoverable (for INPUT taxes)."""
        # OUTPUT taxes are always "recoverable" in the sense that they're passed through
        # This property is primarily for INPUT taxes
        return self.recoverable if self.direction == self.TaxDirection.INPUT else True


class PostingProfile(ProjectionWriteGuard):
    """
    Posting profile for control account configuration.

    Posting profiles define which control account to use when posting
    sales invoices (AR) or purchase bills (AP).

    - CUSTOMER profiles use AR control accounts
    - VENDOR profiles use AP control accounts

    One profile per type can be marked as default.
    """

    class ProfileType(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Customer (AR)"
        VENDOR = "VENDOR", "Vendor (AP)"

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="posting_profiles",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    code = models.CharField(
        max_length=20,
        help_text="Profile code (e.g., AR-DEFAULT, AP-FOREIGN)",
    )

    name = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100, blank=True, default="")

    description = models.TextField(blank=True, default="")

    profile_type = models.CharField(
        max_length=10,
        choices=ProfileType.choices,
        help_text="CUSTOMER for AR, VENDOR for AP",
    )

    control_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Control account (AR or AP)",
    )

    is_default = models.BooleanField(
        default=False,
        help_text="Default profile for this type",
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["profile_type", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="uniq_postingprofile_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "profile_type", "is_active"]),
        ]
        verbose_name = "Posting Profile"
        verbose_name_plural = "Posting Profiles"

    def __str__(self):
        return f"{self.code} - {self.name}"


class SalesInvoice(ProjectionWriteGuard):
    """
    Sales Invoice header.

    Workflow: DRAFT -> POSTED -> VOIDED
    - DRAFT: Invoice being edited, can be modified
    - POSTED: Invoice is finalized, journal entry created
    - VOIDED: Invoice has been cancelled (reversing entry created)

    When posted, creates a journal entry:
    - Debit: AR Control (from posting_profile.control_account)
    - Credit: Revenue accounts (from lines)
    - Credit: VAT Payable (aggregated tax)
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        POSTED = "POSTED", "Posted"
        VOIDED = "VOIDED", "Voided"

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="sales_invoices",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    # Document info
    invoice_number = models.CharField(
        max_length=50,
        help_text="Invoice number (can be auto-generated)",
    )

    invoice_date = models.DateField()

    due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Payment due date",
    )

    # Counterparty
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="invoices",
    )

    posting_profile = models.ForeignKey(
        PostingProfile,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Posting profile (determines AR control account)",
    )

    # Multi-currency support
    currency = models.CharField(
        max_length=3,
        blank=True,
        default="",
        help_text="Invoice currency (ISO 4217). Empty = company default currency.",
    )
    exchange_rate = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("1"),
        help_text="Exchange rate to functional currency at invoice date",
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
        help_text="Total invoice amount (subtotal - discount + tax)",
    )

    # Payment tracking
    amount_paid = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Total amount paid against this invoice",
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
        related_name="sales_invoices",
        help_text="Journal entry created when posted",
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
        ordering = ["-invoice_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "invoice_number"],
                name="uniq_invoice_number_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "customer"]),
            models.Index(fields=["company", "invoice_date"]),
        ]
        verbose_name = "Sales Invoice"
        verbose_name_plural = "Sales Invoices"

    def __str__(self):
        return f"INV-{self.invoice_number}"

    @property
    def amount_due(self) -> Decimal:
        """Calculate remaining amount due on invoice."""
        return self.total_amount - self.amount_paid

    @property
    def is_fully_paid(self) -> bool:
        """Check if invoice is fully paid."""
        return self.amount_paid >= self.total_amount

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


class SalesInvoiceLine(ProjectionWriteGuard):
    """
    Sales Invoice line item.

    Calculation pipeline:
    - gross_amount = quantity * unit_price
    - net_amount = gross_amount - discount_amount
    - tax_amount = net_amount * tax_rate
    - line_total = net_amount + tax_amount

    When the invoice is posted:
    - Credit: account (revenue) for net_amount
    - Credit: tax_code.tax_account for tax_amount
    """

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    invoice = models.ForeignKey(
        SalesInvoice,
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
        help_text="Line ordering within the invoice",
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
        help_text="Tax rate at time of invoice (copied from tax_code)",
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

    # GL Account for revenue
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Revenue account for this line",
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
                fields=["invoice", "line_number"],
                name="uniq_invoice_line_number",
            ),
        ]
        indexes = [
            models.Index(fields=["invoice", "line_number"]),
        ]
        verbose_name = "Sales Invoice Line"
        verbose_name_plural = "Sales Invoice Lines"

    def __str__(self):
        return f"{self.invoice.invoice_number}:{self.line_number}"

    def calculate(self):
        """
        Calculate line amounts from inputs.

        Call this before saving to update calculated fields.
        """
        self.gross_amount = self.quantity * self.unit_price
        self.net_amount = self.gross_amount - self.discount_amount
        self.tax_amount = self.net_amount * self.tax_rate
        self.line_total = self.net_amount + self.tax_amount


class ReceiptAllocation(ProjectionWriteGuard):
    """
    Tracks allocation of customer receipts to sales invoices.

    When a customer pays, the receipt amount can be allocated to one or more
    open invoices. This model tracks those allocations.
    """

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="receipt_allocations",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    # The receipt that was allocated
    receipt_public_id = models.UUIDField(
        help_text="Public ID of the customer receipt",
    )

    receipt_date = models.DateField(
        help_text="Date of the receipt",
    )

    # The invoice being paid
    invoice = models.ForeignKey(
        SalesInvoice,
        on_delete=models.PROTECT,
        related_name="receipt_allocations",
    )

    # Amount allocated from this receipt to this invoice
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="Amount allocated to this invoice",
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

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "receipt_public_id"]),
            models.Index(fields=["company", "invoice"]),
        ]
        verbose_name = "Receipt Allocation"
        verbose_name_plural = "Receipt Allocations"

    def __str__(self):
        return f"Receipt {self.receipt_public_id} -> {self.invoice.invoice_number}: {self.amount}"


# =============================================================================
# Sales Credit Note
# =============================================================================

class SalesCreditNote(ProjectionWriteGuard):
    """
    Sales Credit Note — partial or full reversal of a posted invoice.

    Workflow: DRAFT -> POSTED -> VOIDED
    - DRAFT: Credit note being edited
    - POSTED: Finalized, reversing journal entry created, invoice.amount_paid adjusted
    - VOIDED: Credit note cancelled (re-reversing entry created)

    When posted, creates a journal entry that reverses the original invoice posting:
    - Credit: AR Control (reduces customer balance)
    - Debit: Revenue accounts (reverses original revenue)
    - Debit: VAT Payable (reverses original tax)
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        POSTED = "POSTED", "Posted"
        VOIDED = "VOIDED", "Voided"

    class Reason(models.TextChoices):
        RETURN = "RETURN", "Goods returned"
        PRICE_ADJUSTMENT = "PRICE_ADJUSTMENT", "Price adjustment"
        TAX_CORRECTION = "TAX_CORRECTION", "Tax correction"
        DAMAGED = "DAMAGED", "Damaged goods"
        OTHER = "OTHER", "Other"

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="sales_credit_notes",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    # Document info
    credit_note_number = models.CharField(
        max_length=50,
        help_text="Credit note number (auto-generated as CN-XXXXXX)",
    )

    credit_note_date = models.DateField()

    # Link to original invoice (required)
    invoice = models.ForeignKey(
        SalesInvoice,
        on_delete=models.PROTECT,
        related_name="credit_notes",
        help_text="The original invoice being credited",
    )

    # Counterparty (denormalized from invoice for querying)
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="credit_notes",
    )

    posting_profile = models.ForeignKey(
        PostingProfile,
        on_delete=models.PROTECT,
        related_name="+",
    )

    # Reason
    reason = models.CharField(
        max_length=20,
        choices=Reason.choices,
        default=Reason.OTHER,
    )
    reason_notes = models.TextField(blank=True, default="")

    # Multi-currency (inherited from invoice)
    currency = models.CharField(max_length=3, blank=True, default="")
    exchange_rate = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("1"),
    )

    # Calculated totals
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
        null=True,
        blank=True,
        related_name="sales_credit_notes",
    )

    # Metadata
    notes = models.TextField(blank=True, default="")
    reference = models.CharField(max_length=100, blank=True, default="")

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-credit_note_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "credit_note_number"],
                name="uniq_credit_note_number_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "customer"]),
            models.Index(fields=["company", "invoice"]),
        ]

    def __str__(self):
        return f"CN-{self.credit_note_number}"

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


class SalesCreditNoteLine(ProjectionWriteGuard):
    """
    Credit note line item.

    Same calculation pipeline as SalesInvoiceLine but amounts represent
    the credited (reversed) values.
    """

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    credit_note = models.ForeignKey(
        SalesCreditNote,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="+")
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    line_number = models.PositiveIntegerField()

    # Optional reference to original invoice line
    invoice_line = models.ForeignKey(
        SalesInvoiceLine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="credit_note_lines",
        help_text="Original invoice line being credited (optional)",
    )

    item = models.ForeignKey(Item, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    description = models.CharField(max_length=500)
    description_ar = models.CharField(max_length=500, blank=True, default="")

    # Pricing
    quantity = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    # Tax
    tax_code = models.ForeignKey(TaxCode, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0"))

    # Calculated amounts
    gross_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    net_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    line_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    # GL Account
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")

    # Dimensions
    dimension_values = models.ManyToManyField(AnalysisDimensionValue, blank=True, related_name="+")

    class Meta:
        ordering = ["line_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["credit_note", "line_number"],
                name="uniq_credit_note_line_number",
            ),
        ]

    def __str__(self):
        return f"{self.credit_note.credit_note_number}:{self.line_number}"

    def calculate(self):
        self.gross_amount = self.quantity * self.unit_price
        self.net_amount = self.gross_amount - self.discount_amount
        self.tax_amount = self.net_amount * self.tax_rate
        self.line_total = self.net_amount + self.tax_amount


class PaymentAllocation(ProjectionWriteGuard):
    """
    Tracks allocation of vendor payments to vendor bills.

    Since we don't have a full PurchaseInvoice model yet, this uses
    a bill_reference string to identify bills.
    """

    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="payment_allocations",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    # The payment that was allocated
    payment_public_id = models.UUIDField(
        help_text="Public ID of the vendor payment",
    )

    payment_date = models.DateField(
        help_text="Date of the payment",
    )

    # The vendor bill being paid (reference-based until we have full model)
    vendor = models.ForeignKey(
        "accounting.Vendor",
        on_delete=models.PROTECT,
        related_name="payment_allocations",
    )

    bill_reference = models.CharField(
        max_length=100,
        help_text="Vendor bill/invoice reference number",
    )

    bill_date = models.DateField(
        null=True,
        blank=True,
        help_text="Original bill date",
    )

    bill_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Original bill total amount",
    )

    # Amount allocated from this payment to this bill
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="Amount allocated to this bill",
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

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "payment_public_id"]),
            models.Index(fields=["company", "vendor"]),
            models.Index(fields=["company", "bill_reference"]),
        ]
        verbose_name = "Payment Allocation"
        verbose_name_plural = "Payment Allocations"

    def __str__(self):
        return f"Payment {self.payment_public_id} -> {self.bill_reference}: {self.amount}"
