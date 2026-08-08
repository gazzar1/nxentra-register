# accounting/posted_journal_boundary.py
"""A3-PR2b — the SERIALIZED runtime boundary for JOURNAL_ENTRY_POSTED.

Every production emission of a posted-journal event goes through
:func:`emit_posted_journal`. The boundary owns, in one transaction:

    same-key idempotency pre-lookup (no locks)
    → CompanyEventCounter lock (events.locks.lock_company_event_counter)
    → referenced Account rows, deduplicated, locked in primary-key order
    → AccountFacts loaded from the serialized state
    → exact payload preparation (prepare_posted_journal_for_emit)
    → post-validation-failure same-key recheck
    → race-safe event insertion (constraint-backed final idempotency)

Supported guarantee (the A3-PR2b contract, stated exactly):

    For a newly inserted JOURNAL_ENTRY_POSTED event, all account facts used
    by canonical validation come from Account rows serialized against the
    CompanyEventCounter linearization point.

Concretely: if a live account mutation (``update_account``/``delete_account``
— which acquire the same Counter lock FIRST, then the Account row) commits
before this boundary acquires the Counter, the boundary observes the
post-mutation account state; if the boundary acquires the Counter first, the
mutation waits and its account event receives a HIGHER company sequence than
the journal event. The forbidden history — an account mutation ordered first
in the company event log while a later journal is accepted using
pre-mutation facts — cannot occur.

Scope notes:

- Only the company's OWN accounts are locked. A payload referencing another
  company's account is rejected (``JE_ACCOUNT_CROSS_COMPANY``) from the
  unlocked global facts read — a rejected event needs no serialization, and
  locking foreign rows would let a malformed payload contend on another
  tenant's data.
- Accounts are locked one row at a time in ascending primary-key order —
  the deterministic total order that prevents lock-order deadlocks between
  two journals referencing overlapping account sets in different payload
  orders. Do not replace with a single ``pk__in`` query: PostgreSQL acquires
  row locks in plan order, not ORDER BY order.
- ``no_key=True`` (FOR NO KEY UPDATE) is used where the backend supports it
  so concurrent JournalLine inserts' foreign-key KEY SHARE checks against
  the locked accounts are not blocked; account UPDATEs still conflict.
- An idempotent same-key retry returns the stored event BEFORE any lock and
  is never revalidated against current account state — including when the
  retry payload differs (the recorded PR2 idempotency contract). If a
  concurrent same-key request commits while this one is validating, the
  post-failure recheck returns the stored event instead of a spurious
  rejection; with no stored event the original violations re-raise
  unchanged. No payload hashing or conflict detection is introduced.
- This PR does NOT claim global deadlock elimination — see the Lock-Order
  Normalization workstream in docs/status/constrained_pilot_status.md.

This module stays provider-neutral: it may import core apps (events,
accounting) only.
"""

from __future__ import annotations

from uuid import UUID

from django.db import connections, transaction

from accounting.journal_invariant import (
    PostedJournalInvalid,
    canonical_account_id,
    prepare_posted_journal_for_emit,
)
from events.locks import lock_company_event_counter
from events.models import BusinessEvent
from events.types import EventTypes

__all__ = ["emit_posted_journal"]


def _stored_event(company, idempotency_key: str) -> BusinessEvent | None:
    """The ONE company-scoped same-key semantics the emitter applies
    authoritatively at insert time (uniq_event_company_idempotency_key)."""
    return BusinessEvent.objects.filter(company=company, idempotency_key=idempotency_key).first()


def _referenced_account_pks(company, data: dict) -> list[int]:
    """Canonicalized, deduplicated, company-scoped account pks in ascending
    order — the exact lock order for every path."""
    canonical_ids: set[str] = set()
    for line in data.get("lines") or []:
        if not isinstance(line, dict):
            continue
        canonical = canonical_account_id(line.get("account_public_id"))
        if canonical:
            canonical_ids.add(canonical)
    if not canonical_ids:
        return []
    uuids = []
    for canonical in canonical_ids:
        try:
            uuids.append(UUID(canonical))
        except (ValueError, AttributeError, TypeError):
            continue  # unresolvable spelling → JE_ACCOUNT_UNKNOWN at prepare
    if not uuids:
        return []
    from accounting.models import Account

    return list(
        Account.objects.filter(company=company, public_id__in=uuids).order_by("pk").values_list("pk", flat=True)
    )


def _lock_accounts_in_pk_order(company, pks: list[int]) -> None:
    """Acquire the account row locks one by one in ascending pk order.

    FOR NO KEY UPDATE where the backend supports it (PostgreSQL); the plain
    FOR UPDATE fallback elsewhere. One query per row is deliberate — it is
    the only way to guarantee acquisition order (see module docstring)."""
    if not pks:
        return
    from accounting.models import Account

    alias = Account.objects.db
    no_key = connections[alias].features.has_select_for_no_key_update
    base = Account.objects.select_for_update(no_key=True) if no_key else Account.objects.select_for_update()
    for pk in pks:
        # list() forces the locking SELECT ... FOR [NO KEY] UPDATE; exists()
        # would rewrite the query and is not guaranteed to lock.
        list(base.filter(pk=pk, company=company).only("pk"))


def emit_posted_journal(
    *,
    company,
    data: dict,
    aggregate_type: str,
    aggregate_id,
    idempotency_key: str,
    actor=None,
    api_key=None,
    metadata: dict | None = None,
    caused_by_event=None,
) -> BusinessEvent:
    """Validate and emit ONE JOURNAL_ENTRY_POSTED event under account-state
    serialization. Returns the created (or stored idempotent) BusinessEvent;
    raises :class:`PostedJournalInvalid` (codes/violations intact) when the
    payload is genuinely invalid and no same-key event exists.

    Caller context selects the emitter:

    - ``actor`` (ActorContext) → ``emit_event`` (internal commands);
    - ``api_key`` (ExternalAPIKey) → ``emit_external_event`` (ingest);
    - neither → ``emit_event_no_actor`` (projection emitters; its internal
      tenant routing re-enters the ambient context reentrantly).

    Locks (Counter + Account rows) are acquired inside ``transaction.atomic``
    and therefore live until the OUTERMOST transaction commits — the caller's
    owning transaction for commands/projections, or the boundary's own
    transaction on the external-ingest path (which previously validated in
    autocommit). A rejected payload rolls the savepoint back: no event, no
    sequence, no lock residue.
    """
    # 1) Accepted retries return the stored event with NO locks and NO
    #    revalidation — same-key-different-payload included (PR2 contract).
    existing = _stored_event(company, idempotency_key)
    if existing is not None:
        return existing

    try:
        with transaction.atomic():
            # 2) The company financial-state linearization point.
            lock_company_event_counter(company)

            # 3) Referenced accounts, pk-sorted, locked before facts load.
            _lock_accounts_in_pk_order(company, _referenced_account_pks(company, data))

            # 4) Exact canonical preparation from the serialized state.
            prepared = prepare_posted_journal_for_emit(company, data)

            # 5) The prepared payload IS what gets emitted; the emitter's
            #    constraint-backed lookup stays the final idempotency
            #    authority. Imported lazily to keep provider-neutral import
            #    order stable.
            if actor is not None:
                from events.emitter import emit_event

                return emit_event(
                    actor=actor,
                    event_type=EventTypes.JOURNAL_ENTRY_POSTED,
                    aggregate_type=aggregate_type,
                    aggregate_id=str(aggregate_id),
                    idempotency_key=idempotency_key,
                    data=prepared,
                    **({"metadata": metadata} if metadata is not None else {}),
                    **({"caused_by_event": caused_by_event} if caused_by_event is not None else {}),
                )
            if api_key is not None:
                from events.external import emit_external_event

                return emit_external_event(
                    api_key=api_key,
                    event_type=EventTypes.JOURNAL_ENTRY_POSTED,
                    aggregate_type=aggregate_type,
                    aggregate_id=str(aggregate_id),
                    idempotency_key=idempotency_key,
                    data=prepared,
                    metadata=metadata,
                )
            from events.emitter import emit_event_no_actor

            return emit_event_no_actor(
                company=company,
                event_type=EventTypes.JOURNAL_ENTRY_POSTED,
                aggregate_type=aggregate_type,
                aggregate_id=str(aggregate_id),
                idempotency_key=idempotency_key,
                data=prepared,
                metadata=metadata or {},
                caused_by_event=caused_by_event,
            )
    except PostedJournalInvalid:
        # 6) Overlapping same-key requests: if a competitor committed while
        #    this request validated, the idempotency contract outranks this
        #    payload's invariant verdict (READ COMMITTED gives this fresh
        #    statement visibility of the concurrent commit). Otherwise the
        #    original violations stand, unchanged.
        existing = _stored_event(company, idempotency_key)
        if existing is not None:
            return existing
        raise
