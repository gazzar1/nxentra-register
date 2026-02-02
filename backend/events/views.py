# events/views.py
"""
Event audit API views.

Implements PRD Section 10: Audit Chain Views
- Journal -> Event IDs
- Event -> Command
- Event -> Payload hash / ref
- Event -> Origin

All endpoints require authentication and are scoped to the user's company.
"""

from rest_framework import generics, views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from accounts.authz import resolve_actor
from events.models import BusinessEvent, EventBookmark
from events.serializers import (
    BusinessEventListSerializer,
    BusinessEventDetailSerializer,
    AggregateEventHistorySerializer,
    IntegrityCheckResultSerializer,
    IntegritySummarySerializer,
    EventBookmarkSerializer,
)
from events.verification import full_integrity_check, get_integrity_summary


class EventListView(generics.ListAPIView):
    """
    List events for the current company.

    GET /api/events/

    Supports filtering by:
    - event_type: Filter by event type (exact match)
    - aggregate_type: Filter by aggregate type
    - aggregate_id: Filter by aggregate ID
    - origin: Filter by origin (human, batch, api, system)
    - occurred_at__gte: Events after this timestamp
    - occurred_at__lte: Events before this timestamp
    """

    serializer_class = BusinessEventListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        actor = resolve_actor(self.request)
        qs = BusinessEvent.objects.filter(
            company=actor.company
        ).select_related('caused_by_user').order_by('-company_sequence')

        # Apply filters
        event_type = self.request.query_params.get('event_type')
        if event_type:
            qs = qs.filter(event_type=event_type)

        aggregate_type = self.request.query_params.get('aggregate_type')
        if aggregate_type:
            qs = qs.filter(aggregate_type=aggregate_type)

        aggregate_id = self.request.query_params.get('aggregate_id')
        if aggregate_id:
            qs = qs.filter(aggregate_id=aggregate_id)

        origin = self.request.query_params.get('origin')
        if origin:
            qs = qs.filter(origin=origin)

        occurred_after = self.request.query_params.get('occurred_at__gte')
        if occurred_after:
            qs = qs.filter(occurred_at__gte=occurred_after)

        occurred_before = self.request.query_params.get('occurred_at__lte')
        if occurred_before:
            qs = qs.filter(occurred_at__lte=occurred_before)

        return qs[:1000]  # Limit for safety


class EventDetailView(generics.RetrieveAPIView):
    """
    Get full details of a single event.

    GET /api/events/<uuid:id>/

    Includes:
    - Full resolved payload (LEPH transparent)
    - Causation chain (parent + children)
    - Payload hash and reference info
    """

    serializer_class = BusinessEventDetailSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        actor = resolve_actor(self.request)
        return BusinessEvent.objects.filter(
            company=actor.company
        ).select_related('caused_by_user', 'caused_by_event', 'payload_ref')


class EventCausationChainView(views.APIView):
    """
    Get the full causation chain for an event.

    GET /api/events/<uuid:event_id>/chain/

    Returns:
    - The event itself
    - Parent event (caused_by_event)
    - All child events (caused by this event)
    - Chain depth from root
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, event_id):
        actor = resolve_actor(request)

        try:
            event = BusinessEvent.objects.select_related(
                'caused_by_user', 'caused_by_event'
            ).get(id=event_id, company=actor.company)
        except BusinessEvent.DoesNotExist:
            return Response(
                {'error': 'Event not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Calculate chain depth (walk up to root)
        depth = 0
        parent = event.caused_by_event
        while parent:
            depth += 1
            parent = parent.caused_by_event
            if depth > 100:  # Safety limit
                break

        # Get children
        children = list(event.child_events.all()[:100])

        data = {
            'event': BusinessEventListSerializer(event).data,
            'parent': BusinessEventListSerializer(event.caused_by_event).data if event.caused_by_event else None,
            'children': BusinessEventListSerializer(children, many=True).data,
            'chain_depth': depth,
        }

        return Response(data)


class AggregateEventHistoryView(views.APIView):
    """
    Get event history for a specific aggregate.

    GET /api/events/aggregate/<str:aggregate_type>/<str:aggregate_id>/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, aggregate_type, aggregate_id):
        actor = resolve_actor(request)

        events = BusinessEvent.objects.filter(
            company=actor.company,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
        ).order_by('sequence')

        event_list = list(events[:500])

        if not event_list:
            return Response(
                {'error': 'No events found for this aggregate'},
                status=status.HTTP_404_NOT_FOUND
            )

        data = {
            'aggregate_type': aggregate_type,
            'aggregate_id': aggregate_id,
            'event_count': events.count(),
            'first_event_at': event_list[0].occurred_at if event_list else None,
            'last_event_at': event_list[-1].occurred_at if event_list else None,
            'events': BusinessEventListSerializer(event_list, many=True).data,
        }

        return Response(data)


class JournalEventMappingView(views.APIView):
    """
    Get events associated with a journal entry.

    GET /api/events/journal/<uuid:journal_public_id>/

    PRD Section 10: Journal -> Event IDs mapping
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, journal_public_id):
        actor = resolve_actor(request)

        # Get all events for this journal entry
        events = BusinessEvent.objects.filter(
            company=actor.company,
            aggregate_type='journal_entry',
            aggregate_id=str(journal_public_id),
        ).order_by('sequence')

        if not events.exists():
            return Response(
                {'error': 'No events found for this journal entry'},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response({
            'journal_public_id': str(journal_public_id),
            'event_count': events.count(),
            'events': BusinessEventListSerializer(events, many=True).data,
        })


class IntegrityCheckView(views.APIView):
    """
    Run integrity check for the company's event stream.

    GET /api/events/integrity-check/

    Admin-only endpoint for diagnostics.
    Returns full integrity verification results.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)

        # Only allow admins
        if actor.role not in ['OWNER', 'ADMIN']:
            return Response(
                {'error': 'Admin access required'},
                status=status.HTTP_403_FORBIDDEN
            )

        result = full_integrity_check(actor.company, verbose=False)

        return Response(IntegrityCheckResultSerializer(result).data)


class IntegritySummaryView(views.APIView):
    """
    Get a quick summary of event integrity status.

    GET /api/events/integrity-summary/

    Lightweight check for dashboards and monitoring.
    Does not perform full verification.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)

        summary = get_integrity_summary(actor.company)

        return Response(IntegritySummarySerializer(summary).data)


class EventBookmarkListView(generics.ListAPIView):
    """
    List event bookmarks for the company.

    GET /api/events/bookmarks/

    Shows projection consumer progress.
    """

    serializer_class = EventBookmarkSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        actor = resolve_actor(request=self.request)
        return EventBookmark.objects.filter(
            company=actor.company
        ).select_related('company', 'last_event').order_by('consumer_name')
