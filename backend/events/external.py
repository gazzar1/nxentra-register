# events/external.py
"""
External event ingestion.

Provides emit_external_event() — the dedicated path for events emitted
by external systems via the ingest API. This is intentionally separate
from the internal emit_event() to:

1. Tag events with external origin metadata (source_system, api_key_prefix)
2. Use PayloadOrigin.API for LEPH storage decisions
3. Provide a clear audit trail distinguishing external vs. internal events
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from events.api_keys import ExternalAPIKey
from events.emitter import _emit_event_core
from events.models import BusinessEvent
from events.payload_policy import PayloadOrigin

logger = logging.getLogger(__name__)


def emit_external_event(
    *,
    api_key: ExternalAPIKey,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    idempotency_key: str,
    data: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> BusinessEvent:
    """
    Emit a business event from an external system.

    This wraps _emit_event_core() with external-specific defaults:
    - payload_origin = PayloadOrigin.API
    - external_source = api_key.source_system
    - external_id = idempotency_key (external systems own the key)
    - metadata includes source_system and api_key_prefix

    Args:
        api_key: The authenticated ExternalAPIKey instance
        event_type: Registered event type string
        aggregate_type: Aggregate type (e.g., "Consultation", "Order")
        aggregate_id: Unique ID of the aggregate in the external system
        idempotency_key: Caller-provided key for deduplication
        data: Event payload dict (validated against registered schema)
        metadata: Optional additional metadata from the caller

    Returns:
        The created (or existing, if idempotent) BusinessEvent

    Raises:
        InvalidEventPayload: If data doesn't match the event type schema
        ValueError: If idempotency_key is missing
    """
    enriched_metadata = {
        "source_system": api_key.source_system,
        "api_key_prefix": api_key.key_prefix,
        "api_key_name": api_key.name,
        "ingestion_path": "external_api",
    }
    if metadata:
        enriched_metadata.update(metadata)

    event = _emit_event_core(
        company=api_key.company,
        user=None,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        data=data,
        occurred_at=None,
        idempotency_key=idempotency_key,
        metadata=enriched_metadata,
        caused_by_event=None,
        external_source=api_key.source_system,
        external_id=idempotency_key,
        payload_origin=PayloadOrigin.API,
    )

    logger.info(
        "External event ingested: type=%s source=%s company=%s key=%s",
        event_type,
        api_key.source_system,
        api_key.company.name,
        api_key.key_prefix,
    )

    return event
