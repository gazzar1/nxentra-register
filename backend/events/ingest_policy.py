# events/ingest_policy.py
"""
External-ingest event-type policy.

Canonical Account lifecycle events are command-owned internal aggregates.
Their projection (AccountProjection) mutates the Account rows that feed
posted-journal validation (AccountFacts), and live commands mutate those
rows only under the A3-PR2b serialization protocol (CompanyEventCounter
lock -> Account row lock). An externally ingested ``account.*`` event
would commit at sequence N while its row-apply lags in the post-commit
projection pass, so a journal at N+1 could lock the still-unchanged row
and validate against pre-update facts — the exact forbidden history the
serialized boundary exists to prevent. External systems therefore may not
emit these types at all.

This module is the SINGLE definition of the reserved set. The runtime
guard in events/ingest.py, key-creation validation in events/api_keys.py,
and the architecture meta-tests all import it from here — never restate
the set elsewhere.
"""

from __future__ import annotations

from events.types import EventTypes

# Command-owned internal event types no external system may emit.
# Growing this set is a deliberate act: an architecture meta-test pins the
# registered account.* namespace (and every type AccountProjection
# consumes) to this set, so a future account.renamed cannot silently
# become externally ingestible merely by registration in
# EVENT_DATA_CLASSES.
RESERVED_EXTERNAL_INGEST_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EventTypes.ACCOUNT_CREATED,
        EventTypes.ACCOUNT_UPDATED,
        EventTypes.ACCOUNT_DELETED,
    }
)


def is_reserved_external_event_type(event_type: str) -> bool:
    """True if external ingest must reject this event type outright."""
    return event_type in RESERVED_EXTERNAL_INGEST_EVENT_TYPES
