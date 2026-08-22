# projections/apply_validation.py
"""A3-PR3: the per-event APPLY validation choke point.

``BaseProjection.process_pending`` calls :func:`validate_event_for_apply` for
EVERY event immediately before dispatching to the projection handler — inside
the per-event transaction, after the idempotency stamp. That single call site
is the apply-side twin of the emit boundary (``emit_posted_journal``): every
trigger path that materializes events — the live sync drain, the on_commit
fallback, Celery drains, the CLI daemon, all rebuild entry points routed
through ``BaseProjection.rebuild`` (A154), and tenant replay — funnels through
``process_pending``, so a validator registered here covers all of them with
zero per-projection drift, and every projection consuming a validated event
type receives the SAME verdict (no read-model truth-splits).

This module is deliberately framework-neutral: it knows nothing about
journals or any other domain. Domain code (accounting) registers its
validators at app-ready — see ``accounting.posted_journal_apply`` — exactly
as projections register themselves. A validator signals a violation by
raising ``ProjectionTerminalSkip`` (or a subclass), which the framework
turns into the quarantine outcome: an operator-visible
``ProjectionFailureLog`` row, the event marked applied, the bookmark
advanced, the stream flowing (founder decisions D1/D2 — strict, quarantine,
no cutover state).

Consults NO settings flag — like the emit boundary, TESTING /
DISABLE_EVENT_VALIDATION do not exist here and never will.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from events.models import BusinessEvent

logger = logging.getLogger(__name__)

# event_type -> validator. A validator takes the BusinessEvent and returns
# None (clean) or raises ProjectionTerminalSkip (quarantine). It must be a
# pure decision over the event payload and durable referential state — no
# writes, no external calls.
_VALIDATORS: dict[str, Callable[[BusinessEvent], None]] = {}


def register_apply_validator(event_type: str, validator: Callable[[BusinessEvent], None]) -> None:
    """Register the apply-time validator for an event type.

    One validator per event type. Re-registering the SAME callable is an
    idempotent no-op (app-ready may run more than once in test harnesses);
    registering a DIFFERENT callable for an already-claimed type raises —
    silently replacing a financial validator would be a bypass door.
    """
    existing = _VALIDATORS.get(event_type)
    if existing is not None and existing is not validator:
        raise RuntimeError(
            f"Apply validator for event type '{event_type}' is already registered "
            f"({existing.__module__}.{existing.__qualname__}); refusing to replace it with "
            f"{validator.__module__}.{validator.__qualname__}."
        )
    _VALIDATORS[event_type] = validator


def get_apply_validator(event_type: str) -> Callable[[BusinessEvent], None] | None:
    """Return the registered validator for an event type (None if unvalidated)."""
    return _VALIDATORS.get(event_type)


def registered_apply_event_types() -> frozenset[str]:
    """The event types currently under apply-time validation (for the
    architecture ratchet — the journal family must always be present)."""
    return frozenset(_VALIDATORS)


def validate_event_for_apply(event: BusinessEvent) -> None:
    """THE choke-point call. Dispatches to the registered validator for the
    event's type; event types without a validator pass through untouched
    (most read-model events carry no cross-projection financial invariant).

    Raises whatever the validator raises — ``ProjectionTerminalSkip`` for the
    quarantine outcome; anything else propagates as an ordinary handler error
    (halt) exactly as if the projection handler had raised it.
    """
    validator = _VALIDATORS.get(event.event_type)
    if validator is not None:
        validator(event)
