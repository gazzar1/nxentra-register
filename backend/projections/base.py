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

from abc import ABC, abstractmethod
from typing import List, Optional
import logging

from django.db import transaction
from django.utils import timezone

from accounts.models import Company
from events.models import BusinessEvent, EventBookmark
from projections.models import ProjectionAppliedEvent
from projections.write_barrier import projection_writes_allowed


logger = logging.getLogger(__name__)


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
    def consumes(self) -> List[str]:
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
        from accounts.rls import rls_bypass as _rls_bypass, set_current_company_id

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
            events = list(bookmark.get_unprocessed_events(
                event_types=self.consumes,
                limit=limit,
            ))

            processed = 0

            for event in events:
                try:
                    with transaction.atomic():
                        with projection_writes_allowed():
                            applied, created = ProjectionAppliedEvent.objects.get_or_create(
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

                except Exception as e:
                    logger.exception(
                        f"Error processing event {event.id} in {self.name}: {e}"
                    )
                    bookmark.mark_error(str(e))
                    self.on_error(event, e)

                    if stop_on_error:
                        break

            if processed > 0:
                logger.info(
                    f"Projection {self.name} processed {processed} events for {company.name}"
                )

            return processed
    
    def on_error(self, event: BusinessEvent, error: Exception) -> None:
        """
        Called when an error occurs during event processing.
        
        Override this to implement custom error handling
        (e.g., dead letter queue, alerting).
        """
        pass
    
    def get_bookmark(self, company: Company) -> Optional[EventBookmark]:
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
        
        return bookmark.get_unprocessed_events(
            event_types=self.consumes,
            limit=10000,
        ).count()


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
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._projections = {}
        return cls._instance
    
    def register(self, projection: BaseProjection) -> None:
        """Register a projection."""
        self._projections[projection.name] = projection
    
    def get(self, name: str) -> Optional[BaseProjection]:
        """Get a projection by name."""
        return self._projections.get(name)
    
    def all(self) -> List[BaseProjection]:
        """Get all registered projections."""
        return list(self._projections.values())
    
    def names(self) -> List[str]:
        """Get all projection names."""
        return list(self._projections.keys())


# Global registry instance
projection_registry = ProjectionRegistry()
