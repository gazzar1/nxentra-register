# events/__init__.py
"""
Events app - Event sourcing infrastructure for Nxentra.

This app provides:
- BusinessEvent: Immutable event records
- EventBookmark: Consumer progress tracking
- Emitter functions: emit_event, emit_event_no_actor
- Event type definitions with CANONICAL SCHEMAS
- Payload validation at emission time

The event schemas in events/types.py are THE CONTRACT.
All events are validated against these schemas at emission time.

Usage:
    from events.emitter import emit_event
    from events.types import EventTypes, AccountCreatedData, InvalidEventPayload

    # Preferred: use dataclass instances
    emit_event(
        actor=actor,
        event_type=EventTypes.ACCOUNT_CREATED,
        aggregate_type="account",
        aggregate_id=public_id,
        data=AccountCreatedData(
            account_public_id=str(public_id),
            code="1000",
            name="Cash",
            account_type="ASSET",
            normal_balance="DEBIT",
            is_header=False,
        ),
        idempotency_key=f"account-{public_id}-created",
    )

    # Also valid: use dicts (will be validated against schema)
    emit_event(
        actor=actor,
        event_type=EventTypes.ACCOUNT_CREATED,
        aggregate_type="account",
        aggregate_id=public_id,
        data={"account_public_id": "...", ...},
        idempotency_key=f"account-{public_id}-created",
    )

Handling validation errors:
    try:
        emit_event(...)
    except InvalidEventPayload as e:
        # e.event_type - the event type that failed
        # e.errors - list of validation error messages
        logger.error(f"Invalid event payload: {e}")
"""

default_app_config = "events.apps.EventsConfig"