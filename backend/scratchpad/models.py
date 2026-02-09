# scratchpad/models.py
"""
Scratchpad models for Nxentra ERP.

IMPORTANT: Unlike accounting models, ScratchpadRow is a WRITE MODEL.
=================================================================
Users directly create, edit, and delete scratchpad rows. Only when
committed do they become immutable BusinessEvents and JournalEntries.

Models:
- ScratchpadRow: Staging area for journal entry preparation (mutable)
- ScratchpadRowDimension: Dynamic dimension values per row
- AccountDimensionRule: Required/forbidden dimensions per account
"""

from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
import uuid

from accounts.models import Company
from accounting.models import Account, AnalysisDimension, AnalysisDimensionValue


class ScratchpadRow(models.Model):
    """
    Staging area for journal entry preparation.

    Unlike JournalEntry (a read model), ScratchpadRow is a WRITE MODEL
    that users directly manipulate. It becomes a BusinessEvent only
    when committed.

    Lifecycle: RAW -> PARSED -> (INVALID|READY) -> COMMITTED

    Each row represents one debit-credit pair. Rows with the same
    group_id will be committed together as a single JournalEntry.
    """

    class Status(models.TextChoices):
        RAW = "RAW", "Raw input"              # Just created, not yet validated
        PARSED = "PARSED", "Parsed"           # Voice/import parsed, pending validation
        INVALID = "INVALID", "Invalid"        # Validation failed
        READY = "READY", "Ready"              # Valid, ready to commit
        COMMITTED = "COMMITTED", "Committed"  # Converted to JournalEntry

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual Entry"
        PASTE = "paste", "Clipboard Paste"
        IMPORT = "import", "File Import"
        VOICE = "voice", "Voice Input"

    # Identity
    id = models.BigAutoField(primary_key=True)
    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="scratchpad_rows",
    )

    # Grouping - rows with same group_id commit together as one JournalEntry
    group_id = models.UUIDField(
        default=uuid.uuid4,
        db_index=True,
        help_text="Rows with same group_id are committed together as one JournalEntry",
    )
    group_order = models.PositiveIntegerField(
        default=0,
        help_text="Order within the group",
    )

    # Status tracking
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.RAW,
    )
    source = models.CharField(
        max_length=12,
        choices=Source.choices,
        default=Source.MANUAL,
    )

    # Core transaction data
    transaction_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of the transaction (per-row, not per-entry)",
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    description_ar = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Arabic description",
    )
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
    )
    debit_account = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Account to debit",
    )
    credit_account = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Account to credit",
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Additional notes for this row",
    )

    # Raw input preservation (for voice/paste/import)
    raw_input = models.TextField(
        blank=True,
        default="",
        help_text="Original voice transcript, pasted text, or import row data",
    )

    # Validation state
    validation_errors = models.JSONField(
        default=list,
        blank=True,
        help_text="List of validation error objects: [{field, code, message}]",
    )

    # Import tracking
    import_batch_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Links rows from the same import operation",
    )
    import_row_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Original row number from import file",
    )

    # Commit tracking
    committed_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    committed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="committed_scratchpad_rows",
    )
    committed_event = models.ForeignKey(
        "events.BusinessEvent",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="scratchpad_rows",
        help_text="The event created when this row was committed",
    )

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_scratchpad_rows",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["group_id", "group_order", "created_at"]
        verbose_name = "Scratchpad Row"
        verbose_name_plural = "Scratchpad Rows"
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "group_id"]),
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["company", "source"]),
        ]

    def __str__(self):
        return f"Scratchpad #{self.id}: {self.description[:30]}... ({self.status})"

    @property
    def is_committed(self) -> bool:
        return self.status == self.Status.COMMITTED

    @property
    def is_ready(self) -> bool:
        return self.status == self.Status.READY

    @property
    def can_edit(self) -> bool:
        """Returns True if this row can still be edited."""
        return self.status != self.Status.COMMITTED

    @property
    def has_errors(self) -> bool:
        return bool(self.validation_errors)


class ScratchpadRowDimension(models.Model):
    """
    Dynamic dimension values for scratchpad rows.

    Links ScratchpadRow to existing AnalysisDimension/AnalysisDimensionValue
    infrastructure. One value per dimension per row.
    """

    scratchpad_row = models.ForeignKey(
        ScratchpadRow,
        on_delete=models.CASCADE,
        related_name="dimensions",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="+",
    )
    dimension = models.ForeignKey(
        AnalysisDimension,
        on_delete=models.CASCADE,
        related_name="+",
    )
    dimension_value = models.ForeignKey(
        AnalysisDimensionValue,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # For import matching - stores raw value before matching to dimension_value
    raw_value = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Raw imported value before matching to a dimension value",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scratchpad_row", "dimension"],
                name="uniq_scratchpad_row_dimension",
            ),
        ]
        verbose_name = "Scratchpad Row Dimension"
        verbose_name_plural = "Scratchpad Row Dimensions"

    def __str__(self):
        value = self.dimension_value.code if self.dimension_value else self.raw_value
        return f"{self.dimension.code}={value}"


class AccountDimensionRule(models.Model):
    """
    Rules for which dimensions are required/forbidden per account.

    Extends the global AnalysisDimension.is_required_on_posting with
    fine-grained per-account control. For example:
    - "Cost Center" required for Expense accounts
    - "Project" required for specific project expense accounts
    - "Department" forbidden for certain inter-company accounts
    """

    class RuleType(models.TextChoices):
        REQUIRED = "REQUIRED", "Required"
        FORBIDDEN = "FORBIDDEN", "Forbidden"
        OPTIONAL = "OPTIONAL", "Optional"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="dimension_rules",
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="dimension_rules",
    )
    dimension = models.ForeignKey(
        AnalysisDimension,
        on_delete=models.CASCADE,
        related_name="account_rules",
    )
    rule_type = models.CharField(
        max_length=12,
        choices=RuleType.choices,
        default=RuleType.OPTIONAL,
    )
    # Optional: default value when required (for auto-fill suggestions)
    default_value = models.ForeignKey(
        AnalysisDimensionValue,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Default value to suggest when this dimension is required",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["account", "dimension"],
                name="uniq_account_dimension_rule",
            ),
        ]
        verbose_name = "Account Dimension Rule"
        verbose_name_plural = "Account Dimension Rules"

    def __str__(self):
        return f"{self.account.code} - {self.dimension.code}: {self.rule_type}"
