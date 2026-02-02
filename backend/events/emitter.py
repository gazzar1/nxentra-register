# events/emitter.py
"""
Event emission functions.

This module provides the primary interface for emitting business events.
All events MUST be emitted through these functions to ensure:
1. Payload validation against canonical schemas (events/types.py)
2. Idempotency handling
3. Proper sequencing
4. Audit trail (caused_by_user, metadata)
5. LEPH (Large Event Payload Handling) - automatic external storage for large payloads

IMPORTANT: Events are validated at emission time.
==============================================
If you get an InvalidEventPayload error, it means the data dict
does not match the schema defined in events/types.py. Fix the
data being passed, don't disable validation.
"""

from __future__ import annotations

from typing import Optional, Any, Dict, Union
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.conf import settings

from events.models import BusinessEvent
from events.types import validate_event_payload, InvalidEventPayload, BaseEventData
from events.payload_policy import (
    PayloadOrigin,
    PayloadStrategy,
    determine_storage_strategy,
)
from events.serialization import compute_payload_hash, estimate_json_size


def _emit_event_core(
    *,
    company,
    user,
    event_type: str,
    aggregate_type: str,
    aggregate_id: Any,
    data: Union[Dict[str, Any], BaseEventData],
    occurred_at: Optional[datetime],
    idempotency_key: str,
    metadata: Optional[Dict[str, Any]],
    caused_by_event: Optional[BusinessEvent],
    external_source: str,
    external_id: str,
    payload_origin: PayloadOrigin = PayloadOrigin.HUMAN,
) -> BusinessEvent:
    """
    Core event emission logic.

    This function validates the event payload, handles idempotency,
    and persists the event to the database. For large payloads, it
    automatically uses external storage (LEPH).

    Args:
        company: The company this event belongs to
        user: The user who caused the event (can be None for system events)
        event_type: The event type (must be registered in EVENT_DATA_CLASSES)
        aggregate_type: The type of aggregate (e.g., "account", "journal_entry")
        aggregate_id: The ID of the aggregate instance
        data: Event payload (dict or BaseEventData instance)
        occurred_at: When the event occurred (defaults to now)
        idempotency_key: Unique key for idempotent event emission
        metadata: Optional metadata (request info, etc.)
        caused_by_event: Optional parent event that caused this one
        external_source: Source system for external events
        external_id: External system's ID for this event
        payload_origin: Origin of the payload (affects LEPH storage decisions)

    Returns:
        The created (or existing, if idempotent) BusinessEvent

    Raises:
        InvalidEventPayload: If data doesn't match the schema
        ValueError: If idempotency_key is missing
    """
    if not idempotency_key or not str(idempotency_key).strip():
        raise ValueError("idempotency_key is required")

    # ═══════════════════════════════════════════════════════════════════════════
    # PAYLOAD VALIDATION: Enforce canonical schema from events/types.py
    # ═══════════════════════════════════════════════════════════════════════════
    # Convert BaseEventData to dict if needed
    if isinstance(data, BaseEventData):
        data = data.to_dict()

    # Validate payload against the schema (unless explicitly disabled for testing)
    if not getattr(settings, "DISABLE_EVENT_VALIDATION", False):
        validate_event_payload(event_type, data)

    if occurred_at is None:
        occurred_at = timezone.now()

    # Quick idempotency check (common case)
    existing = BusinessEvent.objects.filter(company=company, idempotency_key=idempotency_key).first()
    if existing:
        return existing

    # ═══════════════════════════════════════════════════════════════════════════
    # LEPH: Determine storage strategy based on payload size and origin
    # ═══════════════════════════════════════════════════════════════════════════
    strategy, strategy_meta = determine_storage_strategy(data, payload_origin)

    # Prepare LEPH fields based on strategy
    payload_storage = 'inline'
    payload_hash = ''
    payload_ref = None
    event_data = data

    if strategy == PayloadStrategy.EXTERNAL:
        # Large payload: store externally
        from events.payload_store import EventPayload

        payload_hash = compute_payload_hash(data)
        payload_ref = EventPayload.store_payload(data)
        payload_storage = 'external'
        event_data = {}  # Don't store payload inline

    elif strategy == PayloadStrategy.INLINE:
        # Small payload: compute hash for integrity, store inline
        payload_hash = compute_payload_hash(data)
        payload_storage = 'inline'
        event_data = data

    elif strategy == PayloadStrategy.CHUNKED:
        # Chunked payloads are handled separately by emit_chunked_journal()
        # If we reach here, treat as external (caller should use chunked emission)
        from events.payload_store import EventPayload

        payload_hash = compute_payload_hash(data)
        payload_ref = EventPayload.store_payload(data)
        payload_storage = 'external'
        event_data = {}

    # Minimal v0 retry:
    # - If sequence collides for same aggregate (uniq_event_company_aggregate_sequence)
    # - If idempotency collides (uniq_event_company_idempotency_key), we return the existing row
    for attempt in range(3):
        try:
            with transaction.atomic():
                return BusinessEvent.objects.create(
                    company=company,
                    event_type=event_type,
                    aggregate_type=aggregate_type,
                    aggregate_id=str(aggregate_id),
                    data=event_data,
                    metadata=metadata or {},
                    caused_by_user=user,
                    caused_by_event=caused_by_event,
                    occurred_at=occurred_at,
                    idempotency_key=idempotency_key,
                    external_source=external_source,
                    external_id=external_id,
                    # LEPH fields
                    payload_storage=payload_storage,
                    payload_hash=payload_hash,
                    payload_ref=payload_ref,
                    # Ledger Survivability: Origin tracking
                    origin=payload_origin.value,
                )
        except IntegrityError:
            # Most likely:
            # 1) idempotency key collision (another worker inserted same key)
            # 2) aggregate sequence collision (two inserts same aggregate concurrently)
            existing = BusinessEvent.objects.filter(company=company, idempotency_key=idempotency_key).first()
            if existing:
                return existing

            if attempt == 2:
                raise

    # Unreachable, but keeps type-checkers happy
    raise RuntimeError("Failed to emit event after retries")


def emit_event(*args, **kwargs) -> BusinessEvent:
    """
    Emit a business event with payload validation and automatic LEPH handling.

    The data parameter MUST conform to the schema defined in events/types.py.
    Validation is performed automatically - invalid payloads raise InvalidEventPayload.

    For large payloads, LEPH (Large Event Payload Handling) automatically stores
    the payload externally. Use payload_origin to hint the storage strategy:
    - PayloadOrigin.HUMAN: Manual UI entry (default)
    - PayloadOrigin.SYSTEM_BATCH: EDIM imports (may use chunking)
    - PayloadOrigin.API: External API calls

    Supported calls:
    - emit_event(actor, event_type, aggregate_type, aggregate_id, data, ...)
    - emit_event(actor=..., event_type=..., aggregate_type=..., aggregate_id=..., data=..., ...)
    - emit_event(company=..., caused_by_user=..., event_type=..., aggregate_type=..., aggregate_id=..., data=..., ...)

    The data parameter can be:
    - A dict matching the schema for the event type
    - A BaseEventData subclass instance (will be converted via .to_dict())

    Example using dataclass (preferred):
        from events.types import AccountCreatedData, EventTypes

        emit_event(
            actor=actor,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="account",
            aggregate_id=account_public_id,
            data=AccountCreatedData(
                account_public_id=str(account_public_id),
                code="1000",
                name="Cash",
                account_type="ASSET",
                normal_balance="DEBIT",
                is_header=False,
            ),
            idempotency_key=f"account-{account_public_id}-created",
        )

    Example using dict:
        emit_event(
            actor=actor,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="account",
            aggregate_id=account_public_id,
            data={
                "account_public_id": str(account_public_id),
                "code": "1000",
                "name": "Cash",
                "account_type": "ASSET",
                "normal_balance": "DEBIT",
                "is_header": False,
            },
            idempotency_key=f"account-{account_public_id}-created",
        )

    Raises:
        InvalidEventPayload: If data doesn't match the event type schema
    """
    if args:
        if len(args) < 5:
            raise TypeError("emit_event() requires actor, event_type, aggregate_type, aggregate_id, data")
        actor = args[0]
        event_type, aggregate_type, aggregate_id, data = args[1:5]
        occurred_at = kwargs.pop("occurred_at", None)
        idempotency_key = kwargs.pop("idempotency_key")
        metadata = kwargs.pop("metadata", None)
        caused_by_event = kwargs.pop("caused_by_event", None)
        external_source = kwargs.pop("external_source", "")
        external_id = kwargs.pop("external_id", "")
        payload_origin = kwargs.pop("payload_origin", PayloadOrigin.HUMAN)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs.keys()))
            raise TypeError(f"emit_event() got unexpected keyword arguments: {unexpected}")
        return _emit_event_core(
            company=actor.company,
            user=actor.user,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            data=data,
            occurred_at=occurred_at,
            idempotency_key=idempotency_key,
            metadata=metadata,
            caused_by_event=caused_by_event,
            external_source=external_source,
            external_id=external_id,
            payload_origin=payload_origin,
        )

    actor = kwargs.pop("actor", None)
    company = kwargs.pop("company", None)
    user = kwargs.pop("user", None)
    caused_by_user = kwargs.pop("caused_by_user", None)

    if actor is not None:
        company = actor.company
        user = actor.user
    else:
        if user is None:
            user = caused_by_user
        if company is None:
            raise TypeError("emit_event() requires company when actor is not provided")

    event_type = kwargs.pop("event_type")
    aggregate_type = kwargs.pop("aggregate_type")
    aggregate_id = kwargs.pop("aggregate_id")
    data = kwargs.pop("data")
    occurred_at = kwargs.pop("occurred_at", None)
    idempotency_key = kwargs.pop("idempotency_key")
    metadata = kwargs.pop("metadata", None)
    caused_by_event = kwargs.pop("caused_by_event", None)
    external_source = kwargs.pop("external_source", "")
    external_id = kwargs.pop("external_id", "")
    payload_origin = kwargs.pop("payload_origin", PayloadOrigin.HUMAN)

    if kwargs:
        unexpected = ", ".join(sorted(kwargs.keys()))
        raise TypeError(f"emit_event() got unexpected keyword arguments: {unexpected}")

    return _emit_event_core(
        company=company,
        user=user,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        data=data,
        occurred_at=occurred_at,
        idempotency_key=idempotency_key,
        metadata=metadata,
        caused_by_event=caused_by_event,
        external_source=external_source,
        external_id=external_id,
        payload_origin=payload_origin,
    )


def emit_event_no_actor(
    company,
    event_type: str,
    aggregate_type: str,
    aggregate_id: Any,
    data: Union[Dict[str, Any], BaseEventData],
    *,
    user=None,
    occurred_at: Optional[datetime] = None,
    idempotency_key: str,
    metadata: Optional[Dict[str, Any]] = None,
    caused_by_event: Optional[BusinessEvent] = None,
    external_source: str = "",
    external_id: str = "",
    payload_origin: PayloadOrigin = PayloadOrigin.HUMAN,
) -> BusinessEvent:
    """
    Emit an event without an actor context.

    Use this for system-initiated events or events from external sources.
    Payload validation is still enforced. LEPH (Large Event Payload Handling)
    is applied automatically for large payloads.

    IMPORTANT: This function sets tenant context for proper database routing.
    For dedicated tenants, events are written to their dedicated database.
    For shared tenants, RLS context is set to ensure proper isolation.

    Args:
        company: The company this event belongs to
        event_type: Event type (must be registered in EVENT_DATA_CLASSES)
        aggregate_type: Type of aggregate (e.g., "account")
        aggregate_id: ID of the aggregate instance
        data: Event payload (dict or BaseEventData instance)
        user: Optional user who caused the event
        occurred_at: When the event occurred (defaults to now)
        idempotency_key: Unique key for idempotent emission
        metadata: Optional metadata
        caused_by_event: Optional parent event
        external_source: Source system for external events
        external_id: External system's ID
        payload_origin: Origin hint for LEPH storage strategy

    Raises:
        InvalidEventPayload: If data doesn't match the event type schema
    """
    # Import tenant modules here to avoid circular imports
    from tenant.context import tenant_context, system_db_context
    from tenant.models import TenantDirectory
    from accounts import rls

    # Look up tenant configuration to determine database routing
    # TenantDirectory is in system DB, so use system_db_context for lookup
    with system_db_context():
        tenant_info = TenantDirectory.get_tenant_info(company.id)

    db_alias = tenant_info["db_alias"]
    is_shared = tenant_info["is_shared"]

    # Set tenant context for proper database routing
    with tenant_context(company_id=company.id, db_alias=db_alias, is_shared=is_shared):
        # For shared tenants, set RLS context; for dedicated, bypass RLS
        if is_shared:
            rls.set_rls_context(company.id, bypass=getattr(settings, "RLS_BYPASS", False))
        else:
            # Dedicated DB: no RLS needed, bypass for single-tenant database
            rls.set_rls_bypass(True)

        try:
            return _emit_event_core(
                company=company,
                user=user,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                data=data,
                occurred_at=occurred_at,
                idempotency_key=idempotency_key,
                metadata=metadata,
                caused_by_event=caused_by_event,
                external_source=external_source,
                external_id=external_id,
                payload_origin=payload_origin,
            )
        finally:
            # Clean up RLS context
            rls.clear_rls_context()


def get_aggregate_events(company, aggregate_type: str, aggregate_id: Any) -> list[BusinessEvent]:
    """
    Aggregate stream ordering.

    Use per-aggregate sequence so rebuilds are deterministic within the
    aggregate, independent of other aggregates' interleaving.
    """
    return list(
        BusinessEvent.objects.filter(
            company=company,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
        ).order_by("sequence")
    )


def get_company_events_by_type(
    company,
    event_types: list[str],
    since_event: Optional[BusinessEvent] = None,
    limit: int = 1000,
) -> list[BusinessEvent]:
    """
    Company-wide stream ordering.

    Use company_sequence so projections and bookmarks advance on the same clock.
    """
    qs = BusinessEvent.objects.filter(company=company, event_type__in=event_types)
    if since_event:
        qs = qs.filter(company_sequence__gt=since_event.company_sequence)
    return list(qs.order_by("company_sequence")[:limit])


def get_events_by_type(
    company,
    event_types: list[str],
    since_event: Optional[BusinessEvent] = None,
    limit: int = 1000,
) -> list[BusinessEvent]:
    """Backward-compatible alias for company-wide stream ordering."""
    return get_company_events_by_type(
        company=company,
        event_types=event_types,
        since_event=since_event,
        limit=limit,
    )
