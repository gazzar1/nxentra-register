# events/models.py
"""
Event Store models for Nxentra.

The BusinessEvent table is the canonical source of truth for all
state changes in the system. Events are immutable once created.

EventBookmark tracks consumer progress for projection rebuilds.
"""

import uuid
from django.db import models, transaction, IntegrityError
from django.conf import settings
from django.db.models import F
from django.utils import timezone

from accounts.models import Company


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
        qs = BusinessEvent.objects.all()

        if self.company:
            qs = qs.filter(company=self.company)

        if event_types:
            qs = qs.filter(event_type__in=event_types)

        if self.last_event:
            qs = qs.filter(company_sequence__gt=self.last_event.company_sequence)

        return qs.order_by("company_sequence")[:limit]
