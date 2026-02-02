# events/models.py
"""
Event Store models for Nxentra.

The BusinessEvent table is the canonical source of truth for all
state changes in the system. Events are immutable once created.

EventBookmark tracks consumer progress for projection rebuilds.

LEPH (Large Event Payload Handling):
BusinessEvent supports three payload storage strategies:
- inline: Small payloads stored directly in the 'data' field
- external: Large payloads stored in EventPayload table
- chunked: Very large journal entries split across multiple events
"""

import uuid
from django.db import models, transaction, IntegrityError
from django.conf import settings
from django.db.models import F
from django.utils import timezone

from accounts.models import Company


# =============================================================================
# LEPH: EventPayload Model for External Storage
# =============================================================================

class EventPayload(models.Model):
    """
    External storage for large event payloads.

    This model stores large payloads separately from BusinessEvent.data,
    allowing the event stream to remain efficient while supporting
    arbitrarily large payloads.

    Key properties:
    - Content-addressed: content_hash = SHA-256(canonical_json(payload))
    - Immutable: Cannot be modified after creation
    - Deduplicated: Same payload content reuses existing record
    """

    id = models.BigAutoField(primary_key=True)

    content_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 hash of canonical JSON representation",
    )

    payload = models.JSONField(
        help_text="The actual payload data",
    )

    size_bytes = models.PositiveIntegerField(
        help_text="Size of the canonical JSON in bytes",
    )

    compression = models.CharField(
        max_length=20,
        default='none',
        choices=[
            ('none', 'No compression'),
            ('gzip', 'Gzip compression'),
            ('zstd', 'Zstandard compression'),
        ],
        help_text="Compression algorithm used (for future use)",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    class Meta:
        db_table = 'events_payload'
        verbose_name = 'Event Payload'
        verbose_name_plural = 'Event Payloads'

    def __str__(self):
        return f"EventPayload({self.content_hash[:12]}..., {self.size_bytes} bytes)"

    def save(self, *args, **kwargs):
        """
        Save the payload record.
        Only new records can be saved. Existing records are immutable.
        """
        if not self._state.adding:
            raise ValueError(
                "EventPayload records are immutable and cannot be modified. "
                "Content-addressed storage means changes would alter the hash."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        Prevent deletion of payload records.
        Payloads are referenced by events and must remain for replay integrity.
        """
        raise ValueError(
            "EventPayload records cannot be deleted. "
            "They are required for event replay and audit purposes."
        )

    def verify_integrity(self) -> bool:
        """
        Verify that the stored payload matches its hash.
        Returns True if the payload hash matches, False otherwise.
        """
        from events.serialization import compute_payload_hash

        try:
            computed_hash = compute_payload_hash(self.payload)
            return computed_hash == self.content_hash
        except Exception:
            return False

    @classmethod
    def store_payload(cls, payload: dict) -> 'EventPayload':
        """
        Store a payload, reusing existing record if content matches.
        """
        from events.serialization import compute_payload_hash, estimate_json_size

        content_hash = compute_payload_hash(payload)
        size_bytes = estimate_json_size(payload)

        record, _ = cls.objects.get_or_create(
            content_hash=content_hash,
            defaults={
                'payload': payload,
                'size_bytes': size_bytes,
            }
        )
        return record


class CompanyEventCounter(models.Model):
    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="event_counter",
    )
    last_sequence = models.BigIntegerField(default=0)

    class Meta:
        verbose_name = "Company Event Counter"

    def __str__(self):
        return f"{self.company_id}: {self.last_sequence}"


class BusinessEvent(models.Model):
    """
    Immutable event record.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="events",
    )

    event_type = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Event type name (e.g., 'account.created')",
    )

    aggregate_type = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Entity type (e.g., 'Account', 'JournalEntry')",
    )

    aggregate_id = models.CharField(
        max_length=64,
        db_index=True,
    )

    # Idempotency (deduplication across retries / integrations)
    idempotency_key = models.CharField(
        max_length=255,
        db_index=True,
        editable=False,
        help_text="Unique idempotency key per company",
    )

    # Sequence number for ordering events within an aggregate
    sequence = models.PositiveIntegerField(
        default=0,
        editable=False,
        help_text="Auto-incremented per aggregate",
    )

    # Global monotonic sequence per company (event stream cursor)
    company_sequence = models.BigIntegerField(
        db_index=True,
        editable=False,
        help_text="Monotonic event sequence per company",
    )

    data = models.JSONField(
        default=dict,
        help_text="Event data payload",
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional context (IP, user agent, etc.)",
    )

    schema_version = models.PositiveSmallIntegerField(
        default=1,
        help_text="Schema version for data migration",
    )

    caused_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="caused_events",
        help_text="User who triggered this event",
        db_constraint=False,  # Cross-database FK (User in system DB, Event in tenant DB)
    )

    caused_by_event = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="child_events",
        help_text="Parent event in causation chain",
    )

    external_source = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="External system identifier (e.g., 'stripe', 'shopify')",
    )

    external_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="ID in external system",
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # LEPH (Large Event Payload Handling) fields
    # ═══════════════════════════════════════════════════════════════════════════

    payload_storage = models.CharField(
        max_length=20,
        default='inline',
        choices=[
            ('inline', 'Inline'),
            ('external', 'External'),
            ('chunked', 'Chunked'),
        ],
        help_text="Storage strategy: inline (data field), external (EventPayload), or chunked (multi-event)",
    )

    payload_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="SHA-256 hash of canonical JSON payload for integrity verification",
    )

    payload_ref = models.ForeignKey(
        'EventPayload',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='events',
        help_text="Reference to external payload (when payload_storage='external')",
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # Ledger Survivability: Origin tracking
    # ═══════════════════════════════════════════════════════════════════════════

    class EventOrigin(models.TextChoices):
        """Origin of the event - who/what initiated it."""
        HUMAN = 'human', 'Human (Manual UI)'
        SYSTEM_BATCH = 'batch', 'System Batch Import'
        API = 'api', 'External API'
        SYSTEM = 'system', 'Internal System Process'

    origin = models.CharField(
        max_length=20,
        choices=EventOrigin.choices,
        default=EventOrigin.HUMAN,
        db_index=True,
        help_text="Origin of this event (human, batch import, API, or system)",
    )

    recorded_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    occurred_at = models.DateTimeField(
        db_index=True,
        default=timezone.now,
    )

    class Meta:
        ordering = ["company_id", "company_sequence"]
        indexes = [
            models.Index(fields=["company", "aggregate_type", "aggregate_id", "sequence"]),
            models.Index(fields=["event_type", "occurred_at"]),
            models.Index(fields=["company", "event_type", "occurred_at"]),
            models.Index(fields=["caused_by_event"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "aggregate_type", "aggregate_id", "sequence"],
                name="uniq_event_company_aggregate_sequence",
            ),
            models.UniqueConstraint(
                fields=["company", "idempotency_key"],
                name="uniq_event_company_idempotency_key",
            ),
            models.UniqueConstraint(
                fields=["company", "company_sequence"],
                name="uniq_event_company_sequence",
            ),
        ]

    def __str__(self):
        return f"{self.event_type} [{self.aggregate_type}#{self.aggregate_id}] @{self.occurred_at}"

    @property
    def aggregate_sequence(self) -> int:
        """Compatibility alias for per-aggregate sequence."""
        return self.sequence

    def save(self, *args, **kwargs):
        # Prevent updates (immutability)
        if not self._state.adding:
            raise ValueError("Events are immutable and cannot be modified.")

        if not self.idempotency_key or not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")

        with transaction.atomic():
            # Allocate per-company monotonic stream sequence
            try:
                counter, _ = CompanyEventCounter.objects.select_for_update().get_or_create(
                    company=self.company
                )
            except IntegrityError:
                # Race: someone created it between get_or_create attempts
                counter = CompanyEventCounter.objects.select_for_update().get(company=self.company)

            counter.last_sequence = F("last_sequence") + 1
            counter.save(update_fields=["last_sequence"])
            counter.refresh_from_db(fields=["last_sequence"])
            self.company_sequence = counter.last_sequence

            # Allocate per-aggregate sequence (scoped by company)
            if self.sequence == 0:
                last_event = BusinessEvent.objects.filter(
                    company=self.company,
                    aggregate_type=self.aggregate_type,
                    aggregate_id=self.aggregate_id,
                ).order_by("-sequence").first()
                self.sequence = (last_event.sequence + 1) if last_event else 1

            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Events are immutable and cannot be deleted.")

    # ═══════════════════════════════════════════════════════════════════════════
    # LEPH Payload Resolution
    # ═══════════════════════════════════════════════════════════════════════════

    def get_data(self) -> dict:
        """
        Resolve the event payload regardless of storage strategy.

        This method transparently handles all payload storage strategies,
        returning the full payload dict whether stored inline, externally,
        or across multiple chunked events.

        Returns:
            The complete event payload dict

        Raises:
            IntegrityError: If external payload is missing or hash verification fails
            ValueError: If chunk assembly is called on non-chunked events

        Usage:
            # Projections should use get_data() instead of accessing .data directly
            data = event.get_data()
            lines = data.get('lines', [])
        """
        if self.payload_storage == 'inline':
            return self.data

        elif self.payload_storage == 'external':
            if not self.payload_ref_id:
                raise IntegrityError(
                    f"Event {self.id} has external storage but no payload_ref"
                )

            payload = self.payload_ref.payload

            # Verify integrity if hash is set
            if self.payload_hash:
                from events.serialization import compute_payload_hash
                computed_hash = compute_payload_hash(payload)
                if computed_hash != self.payload_hash:
                    raise IntegrityError(
                        f"Payload hash mismatch for event {self.id}: "
                        f"expected {self.payload_hash[:16]}..., got {computed_hash[:16]}..."
                    )

            return payload

        elif self.payload_storage == 'chunked':
            return self._assemble_chunks()

        # Fallback for unknown strategy (shouldn't happen)
        return self.data

    def _assemble_chunks(self) -> dict:
        """
        Assemble full journal entry from JOURNAL_CREATED + chunk events.

        This method is called when payload_storage='chunked' to reconstruct
        the complete payload from the parent event and its child chunk events.

        Returns:
            Complete payload with all lines assembled

        Raises:
            ValueError: If called on non-JOURNAL_CREATED event type
        """
        from events.types import EventTypes

        # Chunked assembly only works for JOURNAL_CREATED events
        if self.event_type != EventTypes.JOURNAL_CREATED:
            raise ValueError(
                f"Can only assemble chunks from JOURNAL_CREATED event, "
                f"got {self.event_type}"
            )

        # Get all chunk events caused by this event, ordered by sequence
        chunk_events = BusinessEvent.objects.filter(
            caused_by_event=self,
            event_type=EventTypes.JOURNAL_LINES_CHUNK_ADDED,
        ).order_by('sequence')

        # Assemble lines from all chunks in order
        all_lines = []
        for chunk_event in chunk_events:
            chunk_data = chunk_event.get_data()
            chunk_lines = chunk_data.get('lines', [])
            all_lines.extend(chunk_lines)

        # Combine header data with assembled lines
        header = dict(self.data)  # Copy to avoid mutating
        header['lines'] = all_lines

        return header

    def has_external_payload(self) -> bool:
        """Check if this event uses external payload storage."""
        return self.payload_storage == 'external' and self.payload_ref_id is not None

    def has_chunked_payload(self) -> bool:
        """Check if this event uses chunked payload storage."""
        return self.payload_storage == 'chunked'

    def verify_payload_integrity(self) -> bool:
        """
        Verify the integrity of the payload hash.

        Returns:
            True if verification passes or no hash is set, False otherwise
        """
        if not self.payload_hash:
            return True  # No hash to verify

        from events.serialization import compute_payload_hash

        try:
            if self.payload_storage == 'inline':
                computed = compute_payload_hash(self.data)
            elif self.payload_storage == 'external' and self.payload_ref:
                computed = compute_payload_hash(self.payload_ref.payload)
            else:
                return True  # Chunked payloads verified differently

            return computed == self.payload_hash
        except Exception:
            return False


class EventBookmark(models.Model):
    consumer_name = models.CharField(
        max_length=100,
        help_text="Unique consumer identifier (e.g., 'account_balance_projection')",
    )

    company = models.ForeignKey(
        Company,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="event_bookmarks",
    )

    last_event = models.ForeignKey(
        BusinessEvent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Last successfully processed event",
    )

    last_processed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    is_paused = models.BooleanField(
        default=False,
        help_text="Pause event processing for this consumer",
    )

    error_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of consecutive errors",
    )

    last_error = models.TextField(
        blank=True,
        default="",
        help_text="Last error message",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["consumer_name", "company"],
                name="uniq_bookmark_consumer_company",
            ),
        ]

    def __str__(self):
        company_name = self.company.name if self.company else "GLOBAL"
        return f"{self.consumer_name} @ {company_name}"

    def mark_processed(self, event: BusinessEvent):
        self.last_event = event
        self.last_processed_at = timezone.now()
        self.error_count = 0
        self.last_error = ""
        self.save(update_fields=[
            "last_event", "last_processed_at", "error_count", "last_error", "updated_at"
        ])

    def mark_error(self, error_message: str):
        self.error_count += 1
        self.last_error = error_message[:1000]
        self.save(update_fields=["error_count", "last_error", "updated_at"])

    def get_unprocessed_events(self, event_types: list = None, limit: int = 100):
        """
        Company-wide stream ordering.

        Bookmarks advance on company_sequence; use aggregate ordering elsewhere.
        """
        qs = BusinessEvent.objects.all()

        if self.company:
            qs = qs.filter(company=self.company)

        if event_types:
            qs = qs.filter(event_type__in=event_types)

        if self.last_event:
            qs = qs.filter(company_sequence__gt=self.last_event.company_sequence)

        return qs.order_by("company_sequence")[:limit]
