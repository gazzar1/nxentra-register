# clinic/models/invoice.py
import uuid
from django.db import models
from accounts.models import Company, ProjectionWriteGuard


class Invoice(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PAID = "paid", "Paid"
        PARTIALLY_PAID = "partially_paid", "Partially Paid"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="clinic_invoices",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    patient = models.ForeignKey(
        "clinic.Patient", on_delete=models.CASCADE, related_name="invoices",
    )
    visit = models.ForeignKey(
        "clinic.Visit", on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices",
    )
    invoice_no = models.CharField(max_length=30)
    date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    line_items = models.JSONField(
        default=list,
        help_text='List of {"description": str, "amount": str} dicts.',
    )
    subtotal = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="SAR")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "invoice_no"],
                name="uniq_clinic_invoice_no_per_company",
            )
        ]
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"Invoice {self.invoice_no} — {self.patient.name}"

    @property
    def balance_due(self):
        return self.total - self.amount_paid
