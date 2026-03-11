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
- AccountDimensionRule: Moved to accounting.models (re-exported here for backward compat)
"""

from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
import uuid

from accounts.models import Company
from accounting.models import Account, AccountDimensionRule, AnalysisDimension, AnalysisDimensionValue  # noqa: F401


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

    # Voice parsing output (stores structured LLM response)
    parser_output_json = models.JSONField(
        null=True,
        blank=True,
        help_text="Structured output from LLM parsing of voice/text input",
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


class VoiceUsageEvent(models.Model):
    """
    Append-only log for voice feature usage tracking.

    This model captures every voice parsing request for:
    - Per-user usage tracking
    - Cost estimation and billing
    - Abuse detection
    - Usage analytics

    IMPORTANT: This is an APPEND-ONLY table. Rows should never be updated
    or deleted (except for GDPR compliance via separate process).

    Cost Calculation (as of 2025):
    - ASR (gpt-4o-transcribe): $0.006 per minute of audio
    - Parsing (gpt-4o): varies by token usage
    """

    # Identity
    id = models.BigAutoField(primary_key=True)
    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
    )

    # Who
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="voice_usage_events",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="voice_usage_events",
    )

    # What was created (optional - may be null if parsing failed)
    scratchpad_row = models.ForeignKey(
        ScratchpadRow,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="voice_usage_events",
        help_text="The scratchpad row created from this voice input (if any)",
    )

    # Input metrics
    audio_seconds = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Duration of audio recording in seconds",
    )
    transcript_chars = models.PositiveIntegerField(
        default=0,
        help_text="Length of the transcript in characters",
    )

    # Model info
    asr_model = models.CharField(
        max_length=50,
        default="gpt-4o-audio-preview",
        help_text="Model used for speech-to-text",
    )
    parse_model = models.CharField(
        max_length=50,
        default="gpt-4o",
        help_text="Model used for transaction parsing",
    )

    # Token usage (from OpenAI response)
    asr_input_tokens = models.PositiveIntegerField(
        default=0,
        help_text="Input tokens used by ASR (if available)",
    )
    parse_input_tokens = models.PositiveIntegerField(
        default=0,
        help_text="Input tokens used by parser",
    )
    parse_output_tokens = models.PositiveIntegerField(
        default=0,
        help_text="Output tokens generated by parser",
    )

    # Cost tracking (in USD)
    # ASR: $0.006/min for gpt-4o-transcribe
    # GPT-4o: $2.50/1M input, $10.00/1M output (as of late 2024)
    asr_cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal("0"),
        help_text="Estimated ASR cost in USD",
    )
    parse_cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal("0"),
        help_text="Estimated parsing cost in USD",
    )

    @property
    def total_cost_usd(self) -> Decimal:
        """Total cost for this voice usage event."""
        return self.asr_cost_usd + self.parse_cost_usd

    # Outcome
    success = models.BooleanField(
        default=True,
        help_text="Whether the voice parsing succeeded",
    )
    error_message = models.TextField(
        blank=True,
        default="",
        help_text="Error message if parsing failed",
    )
    transactions_parsed = models.PositiveIntegerField(
        default=0,
        help_text="Number of transactions parsed from the input",
    )

    # When
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Voice Usage Event"
        verbose_name_plural = "Voice Usage Events"
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["company", "user", "created_at"]),
        ]

    def __str__(self):
        return f"Voice #{self.id}: {self.user.email} @ {self.created_at}"

    @classmethod
    def calculate_asr_cost(cls, audio_seconds: Decimal) -> Decimal:
        """
        Calculate ASR cost based on audio duration.

        gpt-4o-audio-preview pricing: ~$0.06 per minute (audio input tokens)
        This is significantly higher than whisper-1 ($0.006/min) but provides
        better accuracy, especially for Arabic.
        """
        if not audio_seconds:
            return Decimal("0")
        minutes = audio_seconds / Decimal("60")
        return (minutes * Decimal("0.06")).quantize(Decimal("0.000001"))

    @classmethod
    def calculate_parse_cost(cls, input_tokens: int, output_tokens: int) -> Decimal:
        """
        Calculate parsing cost based on token usage.

        gpt-4o pricing (as of late 2024):
        - Input: $2.50 per 1M tokens
        - Output: $10.00 per 1M tokens
        """
        input_cost = Decimal(input_tokens) / Decimal("1000000") * Decimal("2.50")
        output_cost = Decimal(output_tokens) / Decimal("1000000") * Decimal("10.00")
        return (input_cost + output_cost).quantize(Decimal("0.000001"))
