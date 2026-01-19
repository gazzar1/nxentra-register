# events/emitter.py

from __future__ import annotations

from typing import Optional, Any, Dict
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from events.models import BusinessEvent


def _emit_event_core(
    *,
    company,
    user,
    event_type: str,
    aggregate_type: str,
    aggregate_id: Any,
    data: Dict[str, Any],
    occurred_at: Optional[datetime],
    idempotency_key: str,
    metadata: Optional[Dict[str, Any]],
    caused_by_event: Optional[BusinessEvent],
    external_source: str,
    external_id: str,
) -> BusinessEvent:
    if not idempotency_key or not str(idempotency_key).strip():
        raise ValueError("idempotency_key is required")

    if occurred_at is None:
        occurred_at = timezone.now()

    # Quick idempotency check (common case)
    existing = BusinessEvent.objects.filter(company=company, idempotency_key=idempotency_key).first()
    if existing:
        return existing

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
                    data=data,
                    metadata=metadata or {},
                    caused_by_user=user,
                    caused_by_event=caused_by_event,
                    occurred_at=occurred_at,
                    idempotency_key=idempotency_key,
                    external_source=external_source,
                    external_id=external_id,
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
    Emit an event with either an actor or explicit company/user.

    Supported calls:
    - emit_event(actor, event_type, aggregate_type, aggregate_id, data, ...)
    - emit_event(actor=..., event_type=..., aggregate_type=..., aggregate_id=..., data=..., ...)
    - emit_event(company=..., caused_by_user=..., event_type=..., aggregate_type=..., aggregate_id=..., data=..., ...)
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
    )


def emit_event_no_actor(
    company,
    event_type: str,
    aggregate_type: str,
    aggregate_id: Any,
    data: Dict[str, Any],
    *,
    user=None,
    occurred_at: Optional[datetime] = None,
    idempotency_key: str,
    metadata: Optional[Dict[str, Any]] = None,
    caused_by_event: Optional[BusinessEvent] = None,
    external_source: str = "",
    external_id: str = "",
) -> BusinessEvent:
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
    )


def get_aggregate_events(company, aggregate_type: str, aggregate_id: Any) -> list[BusinessEvent]:
    return list(
        BusinessEvent.objects.filter(
            company=company,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
        ).order_by("sequence")
    )


def get_events_by_type(
    company,
    event_types: list[str],
    since_event: Optional[BusinessEvent] = None,
    limit: int = 1000,
) -> list[BusinessEvent]:
    qs = BusinessEvent.objects.filter(company=company, event_type__in=event_types)
    if since_event:
        qs = qs.filter(company_sequence__gt=since_event.company_sequence)
    return list(qs.order_by("company_sequence")[:limit])
