# events/serializers.py
"""
Serializers for event audit API.

Implements PRD Section 10: Audit Chain Views
- Journal -> Event IDs
- Event -> Command
- Event -> Payload hash / ref
- Event -> Origin
"""

from rest_framework import serializers

from events.models import BusinessEvent, EventPayload, EventBookmark


class EventPayloadSerializer(serializers.ModelSerializer):
    """Serializer for external payload records."""

    class Meta:
        model = EventPayload
        fields = [
            'id',
            'content_hash',
            'size_bytes',
            'compression',
            'created_at',
        ]


class BusinessEventListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for event listing."""

    caused_by_user_email = serializers.CharField(
        source='caused_by_user.email',
        read_only=True,
        default=None,
    )

    class Meta:
        model = BusinessEvent
        fields = [
            'id',
            'event_type',
            'aggregate_type',
            'aggregate_id',
            'sequence',
            'company_sequence',
            'occurred_at',
            'recorded_at',
            'caused_by_user_email',
            'origin',
            'payload_storage',
            'payload_hash',
        ]


class BusinessEventDetailSerializer(serializers.ModelSerializer):
    """Full serializer for single event detail."""

    caused_by_user_email = serializers.CharField(
        source='caused_by_user.email',
        read_only=True,
        default=None,
    )
    caused_by_event_id = serializers.UUIDField(
        source='caused_by_event.id',
        read_only=True,
        default=None,
    )
    child_event_ids = serializers.SerializerMethodField()
    payload_ref_info = serializers.SerializerMethodField()
    resolved_data = serializers.SerializerMethodField()

    class Meta:
        model = BusinessEvent
        fields = [
            'id',
            'event_type',
            'aggregate_type',
            'aggregate_id',
            'sequence',
            'company_sequence',
            'idempotency_key',
            'data',
            'resolved_data',
            'metadata',
            'schema_version',
            'caused_by_user',
            'caused_by_user_email',
            'caused_by_event',
            'caused_by_event_id',
            'child_event_ids',
            'external_source',
            'external_id',
            'origin',
            'payload_storage',
            'payload_hash',
            'payload_ref',
            'payload_ref_info',
            'occurred_at',
            'recorded_at',
        ]

    def get_child_event_ids(self, obj):
        """Get IDs of events caused by this event."""
        return list(
            obj.child_events.values_list('id', flat=True)[:100]
        )

    def get_payload_ref_info(self, obj):
        """Get external payload info if applicable."""
        if obj.payload_ref_id:
            return EventPayloadSerializer(obj.payload_ref).data
        return None

    def get_resolved_data(self, obj):
        """Get the fully resolved payload (handles LEPH)."""
        try:
            return obj.get_data()
        except Exception as e:
            return {'_error': str(e), '_error_type': type(e).__name__}


class EventCausationChainSerializer(serializers.Serializer):
    """Serializer for causation chain view."""

    event = BusinessEventListSerializer()
    parent = BusinessEventListSerializer(allow_null=True)
    children = BusinessEventListSerializer(many=True)
    chain_depth = serializers.IntegerField()


class AggregateEventHistorySerializer(serializers.Serializer):
    """Serializer for aggregate event history."""

    aggregate_type = serializers.CharField()
    aggregate_id = serializers.CharField()
    event_count = serializers.IntegerField()
    first_event_at = serializers.DateTimeField(allow_null=True)
    last_event_at = serializers.DateTimeField(allow_null=True)
    events = BusinessEventListSerializer(many=True)


class IntegrityCheckResultSerializer(serializers.Serializer):
    """Serializer for integrity check results."""

    total_events = serializers.IntegerField()
    verified_events = serializers.IntegerField()
    external_payload_count = serializers.IntegerField()
    chunked_event_count = serializers.IntegerField()
    inline_event_count = serializers.IntegerField()
    total_payload_bytes = serializers.IntegerField()
    payload_errors = serializers.ListField()
    sequence_gaps = serializers.ListField()
    is_valid = serializers.BooleanField()


class IntegritySummarySerializer(serializers.Serializer):
    """Serializer for quick integrity summary."""

    total_events = serializers.IntegerField()
    max_sequence = serializers.IntegerField()
    has_potential_gaps = serializers.BooleanField()
    storage_breakdown = serializers.DictField()
    origin_breakdown = serializers.DictField()
    external_payload_count = serializers.IntegerField()
    chunked_event_count = serializers.IntegerField()


class EventBookmarkSerializer(serializers.ModelSerializer):
    """Serializer for projection bookmarks."""

    last_event_id = serializers.UUIDField(
        source='last_event.id',
        read_only=True,
        default=None,
    )
    company_name = serializers.CharField(
        source='company.name',
        read_only=True,
        default=None,
    )

    class Meta:
        model = EventBookmark
        fields = [
            'id',
            'consumer_name',
            'company',
            'company_name',
            'last_event',
            'last_event_id',
            'last_processed_at',
            'is_paused',
            'error_count',
            'last_error',
            'created_at',
            'updated_at',
        ]
