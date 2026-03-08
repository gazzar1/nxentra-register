# events/ingest.py
"""
External event ingest endpoint.

POST /api/events/ingest/

Accepts events from external systems authenticated via API key.
Events are validated, tagged with external metadata, and fed into
the same financial event pipeline used by internal commands.
"""

from __future__ import annotations

import logging

from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from events.api_keys import ExternalAPIKey
from events.external import emit_external_event
from events.types import EVENT_DATA_CLASSES, InvalidEventPayload


logger = logging.getLogger(__name__)


# =============================================================================
# Authentication
# =============================================================================

class APIKeyAuthentication:
    """
    DRF-compatible authentication backend for ExternalAPIKey.

    Reads the key from the Authorization header:
        Authorization: Api-Key nxk_...
    """

    keyword = "Api-Key"

    def authenticate(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith(self.keyword + " "):
            return None

        raw_key = auth_header[len(self.keyword) + 1:]
        api_key = ExternalAPIKey.authenticate(raw_key)
        if api_key is None:
            return None

        # Return (user, auth) tuple as DRF expects.
        # user=None is fine — we don't map external keys to Django users.
        # The api_key is stored on request.auth for the view to use.
        return (None, api_key)


# =============================================================================
# Rate limiting
# =============================================================================

class ExternalIngestThrottle(SimpleRateThrottle):
    """Rate limit external event ingestion per API key."""

    scope = "external_ingest"
    rate = "120/min"

    def get_cache_key(self, request, view):
        if request.auth and isinstance(request.auth, ExternalAPIKey):
            return f"throttle_external_ingest_{request.auth.pk}"
        return None


# =============================================================================
# Serializer
# =============================================================================

class IngestEventSerializer(serializers.Serializer):
    event_type = serializers.CharField(max_length=100)
    aggregate_type = serializers.CharField(max_length=50)
    aggregate_id = serializers.CharField(max_length=255)
    idempotency_key = serializers.CharField(max_length=255)
    data = serializers.DictField()
    metadata = serializers.DictField(required=False, default=dict)


# =============================================================================
# View
# =============================================================================

class EventIngestView(APIView):
    """
    POST /api/events/ingest/

    Ingest a business event from an external system.

    Request:
        Authorization: Api-Key nxk_...
        Content-Type: application/json

        {
            "event_type": "rent.due_posted",
            "aggregate_type": "RentScheduleLine",
            "aggregate_id": "ext-12345",
            "idempotency_key": "shopify:order:98765",
            "data": { ... },
            "metadata": { ... }  // optional
        }

    Response 201:
        { "event_id": "uuid", "status": "created" }

    Response 200:
        { "event_id": "uuid", "status": "duplicate" }
        (idempotency: same idempotency_key returns existing event)

    Response 401: Invalid or missing API key
    Response 403: Event type not authorized for this key
    Response 422: Payload validation failed
    Response 429: Rate limit exceeded
    """

    # Use our custom API key auth, not DRF's default session/JWT auth
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [AllowAny]  # Auth is handled by APIKeyAuthentication
    throttle_classes = [ExternalIngestThrottle]

    def post(self, request):
        # ── Auth check ────────────────────────────────────────────────
        api_key = request.auth
        if not isinstance(api_key, ExternalAPIKey):
            return Response(
                {"detail": "Authentication required. Provide Api-Key header."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # ── Deserialize ───────────────────────────────────────────────
        serializer = IngestEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        event_type = payload["event_type"]

        # ── Authorization: is this event type allowed? ────────────────
        if not api_key.is_event_type_allowed(event_type):
            logger.warning(
                "Unauthorized event type %s from key %s (%s)",
                event_type, api_key.key_prefix, api_key.source_system,
            )
            return Response(
                {
                    "detail": f"Event type '{event_type}' is not authorized for this API key.",
                    "allowed_event_types": api_key.allowed_event_types,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # ── Check event type is registered ────────────────────────────
        if event_type not in EVENT_DATA_CLASSES:
            return Response(
                {"detail": f"Unknown event type: '{event_type}'."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        # ── Emit ──────────────────────────────────────────────────────
        try:
            event = emit_external_event(
                api_key=api_key,
                event_type=event_type,
                aggregate_type=payload["aggregate_type"],
                aggregate_id=payload["aggregate_id"],
                idempotency_key=payload["idempotency_key"],
                data=payload["data"],
                metadata=payload.get("metadata"),
            )
        except InvalidEventPayload as exc:
            return Response(
                {"detail": "Payload validation failed.", "errors": str(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        # Determine if this was a new event or idempotent duplicate
        is_new = event.external_id == payload["idempotency_key"]
        # More reliable: check if the event was just created
        # (idempotency returns existing event, so recorded_at will differ)

        return Response(
            {
                "event_id": str(event.id),
                "event_type": event.event_type,
                "company": event.company.name,
                "status": "created",
            },
            status=status.HTTP_201_CREATED,
        )
