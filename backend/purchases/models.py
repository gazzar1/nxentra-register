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

from django.db import models
from django.conf import settings
from decimal import Decimal
import uuid

from accounts.models import Company, User, ProjectionWriteGuard
from accounting.models import Account, Vendor, AnalysisDimensionValue
from sales.models import Item, TaxCode, PostingProfile


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
