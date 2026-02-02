# events/urls.py
"""
URL configuration for event audit API.

Implements PRD Section 10: Audit Chain Views
- Journal -> Event IDs
- Event -> Command
- Event -> Payload hash / ref
- Event -> Origin
"""

from django.urls import path

from events.views import (
    EventListView,
    EventDetailView,
    EventCausationChainView,
    AggregateEventHistoryView,
    JournalEventMappingView,
    IntegrityCheckView,
    IntegritySummaryView,
    EventBookmarkListView,
)


app_name = "events"

urlpatterns = [
    # Event listing and detail
    path("", EventListView.as_view(), name="event-list"),
    path("<uuid:id>/", EventDetailView.as_view(), name="event-detail"),

    # Causation chain
    path("<uuid:event_id>/chain/", EventCausationChainView.as_view(), name="event-chain"),

    # Aggregate history
    path(
        "aggregate/<str:aggregate_type>/<str:aggregate_id>/",
        AggregateEventHistoryView.as_view(),
        name="aggregate-history",
    ),

    # Journal -> Events mapping
    path(
        "journal/<uuid:journal_public_id>/",
        JournalEventMappingView.as_view(),
        name="journal-events",
    ),

    # Integrity verification
    path("integrity-check/", IntegrityCheckView.as_view(), name="integrity-check"),
    path("integrity-summary/", IntegritySummaryView.as_view(), name="integrity-summary"),

    # Projection bookmarks
    path("bookmarks/", EventBookmarkListView.as_view(), name="bookmark-list"),
]
