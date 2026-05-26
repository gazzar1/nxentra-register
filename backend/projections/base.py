# projections/base.py
"""
Base classes for projections.

A projection is an event consumer that builds materialized views.
Projections:
- Declare which event types they consume
- Process events idempotently (same event twice = same result)
- Track their progress via EventBookmark
- Can be rebuilt from scratch by replaying all events
"""

import logging
from abc import ABC, abstractmethod
from typing import cast

from django.db import transaction
from django.utils import timezone

from accounts.models import Company
from events.models import BusinessEvent, EventBookmark
from projections.models import ProjectionAppliedEvent
from projections.write_barrier import projection_writes_allowed

logger = logging.getLogger(__name__)


class DeferEvent(Exception):
    """A41: raised by a projection handler when the event can't be processed
    yet but isn't a permanent failure — typically a precondition that's
    expected to become true shortly (e.g. the refund handler waiting for
    the order_paid handler to commit a SalesInvoice POSTED).

    `process_pending` catches this specifically:
    - Logs at INFO (not ERROR — defer isn't a failure)
    - Rolls back the per-event transaction (so ProjectionAppliedEvent
      is not created and the event remains "unprocessed")
    - Continues to the next event in the same pass — does NOT halt
      processing the way generic exceptions do under stop_on_error=True
    - At end of the pass, rewinds the bookmark to one BEFORE the earliest
      deferred event so the next call reprocesses it. Any events that
      successfully processed in the same pass are protected by the
      ProjectionAppliedEvent idempotency check on the second pass.

    Handlers should attach a short reason via the exception message for
    log readability and a `defer_until` attribute (datetime or None) to
    support future deadline enforcement (e.g. Sentry alert if a deferred
    event ages past 24h).
    """

    def __init__(self, message: str = "", defer_until=None):
        super().__init__(message)
        self.defer_until = defer_until


class BaseProjection(ABC):
    """
    Base class for all projections.

    Subclasses must implement:
    - name: Unique identifier for this projection
    - consumes: List of event types this projection handles
    - handle(event): Process a single event

    Optional overrides:
    - rebuild(): Custom rebuild logic
    - on_error(event, error): Custom error handling
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this projection (used in bookmarks)."""
        pass

    @property
    @abstractmethod
    def consumes(self) -> list[str]:
        """List of event types this projection consumes."""
        pass

    @abstractmethod
    def handle(self, event: BusinessEvent) -> None:
        """
        Process a single event.

        MUST be idempotent: processing the same event twice
        should produce the same result.

        Args:
            event: The event to process

        Raises:
            Exception: Any error will be recorded and processing will stop
        """
        pass

    def rebuild(self, company: Company) -> int:
        """
        Rebuild this projection from scratch for a company.

        Default implementation:
        1. Reset bookmark to beginning
        2. Clear existing projected data
        3. Process all relevant events

        Returns:
            Number of events processed
        """
        # Reset bookmark
        bookmark, _ = EventBookmark.objects.get_or_create(
            consumer_name=self.name,
            company=company,
        )
        bookmark.last_event = None
        bookmark.last_processed_at = None
        bookmark.error_count = 0
        bookmark.last_error = ""
        bookmark.save()

        # Clear projected data (subclasses should override if needed)
        with projection_writes_allowed():
            self._clear_projected_data(company)

        # Clear applied-event markers for rebuild
        ProjectionAppliedEvent.objects.filter(
            company=company,
            projection_name=self.name,
        ).delete()

        # Process all events
        return self.process_pending(company)

    def _clear_projected_data(self, company: Company) -> None:
        """
        Clear all projected data for rebuild.
        Subclasses should override this.
        """
        pass

    def process_pending(
        self,
        company: Company,
        limit: int = 1000,
        stop_on_error: bool = True,
    ) -> int:
        """
        Process all pending events for this projection.

        Args:
            company: The company to process events for
            limit: Maximum events to process in one call
            stop_on_error: If True, stop on first error

        Returns:
            Number of events successfully processed
        """
        from accounts.rls import rls_bypass as _rls_bypass
        from accounts.rls import set_current_company_id

        # Projections are system-level operations that build read models.
        # They bypass RLS because they explicitly receive the company parameter
        # and should not be blocked by tenant isolation policies.
        # We also ensure current_company_id is set so that WITH CHECK clauses
        # on INSERT/UPDATE pass correctly.
        with _rls_bypass():
            set_current_company_id(company.id)

            bookmark, _ = EventBookmark.objects.get_or_create(
                consumer_name=self.name,
                company=company,
            )

            if bookmark.is_paused:
                logger.info(f"Projection {self.name} is paused for {company.name}")
                return 0

            # Get unprocessed events
            events = list(
                bookmark.get_unprocessed_events(
                    event_types=self.consumes,
                    limit=limit,
                )
            )

            processed = 0
            # A41: events that the handler raised DeferEvent on. We track
            # the lowest-sequence one so we can rewind the bookmark below
            # — guaranteeing the next pass re-attempts it. Already-
            # processed events in the same pass remain idempotent via the
            # ProjectionAppliedEvent unique constraint.
            earliest_deferred: BusinessEvent | None = None

            for event in events:
                try:
                    with transaction.atomic(), projection_writes_allowed():
                        _applied, created = ProjectionAppliedEvent.objects.get_or_create(
                            company=company,
                            projection_name=self.name,
                            event=event,
                        )

                        if not created:
                            bookmark.mark_processed(event)
                            processed += 1
                            continue

                        self.handle(event)
                        bookmark.mark_processed(event)
                        processed += 1

                except DeferEvent as defer:
                    # A41: precondition not met yet (e.g. refund handler
                    # waiting on order_paid). Transaction rolled back, so
                    # ProjectionAppliedEvent for THIS event was not
                    # created — the event remains unprocessed. We keep
                    # going through the rest of the batch; subsequent
                    # successful events will also advance the bookmark,
                    # so we'll rewind below to ensure the deferred event
                    # is revisited.
                    logger.info(
                        "Projection %s deferred event %s: %s",
                        self.name,
                        event.id,
                        defer or "no reason given",
                    )
                    if earliest_deferred is None or event.company_sequence < earliest_deferred.company_sequence:
                        earliest_deferred = event
                    continue

                except Exception as e:
                    logger.exception(f"Error processing event {event.id} in {self.name}: {e}")
                    bookmark.mark_error(str(e))
                    self.on_error(event, e)

                    if stop_on_error:
                        break

            # A41: if any event was deferred, rewind the bookmark to the
            # event immediately preceding the earliest deferred one. This
            # guarantees the next process_pending pass reprocesses it.
            # Successfully-processed events in this pass stay idempotent
            # via the ProjectionAppliedEvent unique constraint — they'll
            # short-circuit on the get_or_create check on the second pass.
            if earliest_deferred is not None:
                predecessor = (
                    BusinessEvent.objects.filter(
                        company=company,
                        company_sequence__lt=earliest_deferred.company_sequence,
                    )
                    .order_by("-company_sequence")
                    .first()
                )
                # Refresh bookmark from DB — recursive _process_projections
                # calls may have advanced it. We need the current row to
                # update_fields against, otherwise our save() races against
                # the inner advancements.
                bookmark.refresh_from_db()
                bookmark.last_event = predecessor
                bookmark.last_processed_at = timezone.now()
                bookmark.save(update_fields=["last_event", "last_processed_at", "updated_at"])

            if processed > 0:
                logger.info(f"Projection {self.name} processed {processed} events for {company.name}")

            return processed

    def on_error(self, event: BusinessEvent, error: Exception) -> None:
        """A80 (2026-05-25): write an operator-visible failure log entry.

        Replaces the previous `pass` no-op. Whenever a handler raises (instead
        of silently `return`ing — see docs/finance_event_first_policy.md §8),
        the framework records what failed where, so the merchant or operator
        can see it in /finance/exceptions instead of having to grep Django
        logs.

        Dedup contract: (company, projection_name, event) is unique. Repeated
        failures of the same event before resolution bump occurrence_count
        instead of duplicating rows.

        Subclasses MAY override to add custom handling (alerts, dead-letter
        queue, etc.), but MUST call super().on_error(event, error) to preserve
        operator visibility — silent overrides would re-introduce the A80
        anti-pattern.
        """
        from django.db import transaction
        from django.db.models import F
        from django.utils import timezone

        from projections.exceptions import (
            ProjectionCommandFailedError,
            ProjectionInvalidDataError,
            ProjectionStateError,
        )
        from projections.models import ProjectionFailureLog
        from projections.write_barrier import projection_writes_allowed

        # Categorize the error so the operator UI can group / filter sensibly.
        if isinstance(error, ProjectionStateError):
            category = ProjectionFailureLog.Category.MISSING_CONFIG
            fix_hint = getattr(error, "fix_hint", "") or ""
        elif isinstance(error, ProjectionInvalidDataError):
            category = ProjectionFailureLog.Category.INVALID_DATA
            fix_hint = ""
        elif isinstance(error, ProjectionCommandFailedError):
            category = ProjectionFailureLog.Category.DOWNSTREAM_FAILED
            fix_hint = ""
        else:
            category = ProjectionFailureLog.Category.UNEXPECTED
            fix_hint = ""

        # A80: use a separate atomic block so writing the failure log is not
        # rolled back when the outer per-event transaction rolls back (the
        # handler raised, so the outer block is already poisoned).
        try:
            with transaction.atomic(), projection_writes_allowed():
                obj, created = ProjectionFailureLog.objects.get_or_create(
                    company=event.company,
                    projection_name=self.name,
                    event=event,
                    defaults={
                        "event_type": event.event_type,
                        "category": category,
                        "message": str(error)[:5000],
                        "fix_hint": fix_hint,
                    },
                )
                if not created:
                    # Same event failed again before being resolved — bump the
                    # counter, refresh the message (it may have changed since
                    # the prior occurrence), and clear `resolved` in the rare
                    # case the operator marked it resolved prematurely.
                    ProjectionFailureLog.objects.filter(pk=obj.pk).update(
                        occurrence_count=F("occurrence_count") + 1,
                        message=str(error)[:5000],
                        category=category,
                        fix_hint=fix_hint,
                        last_seen_at=timezone.now(),
                        resolved=False,
                        resolved_at=None,
                        resolved_by=None,
                    )
        except Exception as logging_error:
            # Never let on_error itself crash the projection loop. Worst case
            # we lose visibility on this one failure but the framework keeps
            # processing other events.
            logger.error(
                "Failed to write ProjectionFailureLog for %s/%s on event %s: %s",
                self.name,
                event.event_type,
                event.id,
                logging_error,
            )

    def get_bookmark(self, company: Company) -> EventBookmark | None:
        """Get the bookmark for this projection and company."""
        try:
            return EventBookmark.objects.get(
                consumer_name=self.name,
                company=company,
            )
        except EventBookmark.DoesNotExist:
            return None

    def get_lag(self, company: Company) -> int:
        """
        Get number of unprocessed events for this projection.

        Useful for monitoring projection health.
        """
        bookmark = self.get_bookmark(company)
        if not bookmark:
            # Never run - count all relevant events
            return BusinessEvent.objects.filter(
                company=company,
                event_type__in=self.consumes,
            ).count()

        # Cast: get_unprocessed_events returns an untyped QuerySet (events/models.py
        # has no return annotation to avoid a TYPE_CHECKING cycle), so .count()
        # types as Any without help.
        return cast(
            int,
            bookmark.get_unprocessed_events(
                event_types=self.consumes,
                limit=10000,
            ).count(),
        )


class ProjectionRegistry:
    """
    Registry of all projections.

    Usage:
        registry = ProjectionRegistry()
        registry.register(AccountBalanceProjection())

        # Process all projections
        for projection in registry.all():
            projection.process_pending(company)
    """

    _instance: "ProjectionRegistry | None" = None
    # Declared at class level so mypy knows the attribute exists; populated in
    # __new__ to keep the singleton's dict identity stable across imports.
    _projections: dict[str, "BaseProjection"]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._projections = {}
        return cls._instance

    def register(self, projection: BaseProjection, *, allow_override: bool = False) -> None:
        """
        Register a projection.

        Raises RuntimeError if a projection with the same name is already
        registered, unless allow_override is True.
        """
        if not allow_override and projection.name in self._projections:
            existing = type(self._projections[projection.name])
            raise RuntimeError(
                f"Duplicate projection name '{projection.name}': "
                f"{type(projection).__qualname__} conflicts with "
                f"already-registered {existing.__qualname__}"
            )
        self._projections[projection.name] = projection

    def get(self, name: str) -> BaseProjection | None:
        """Get a projection by name."""
        return self._projections.get(name)

    def all(self) -> list[BaseProjection]:
        """Get all registered projections."""
        return list(self._projections.values())

    def names(self) -> list[str]:
        """Get all projection names."""
        return list(self._projections.keys())


# Global registry instance
projection_registry = ProjectionRegistry()
