# events/locks.py
"""Canonical company-event serialization lock (A3-PR2b).

One helper owns the FOR UPDATE acquisition of the per-company
``CompanyEventCounter`` row — the linearization point of the company event
log. ``BusinessEvent.save`` allocates ``company_sequence`` under this same
row lock, so any transaction that acquires it BEFORE reading mutable
financial state (Account rows feeding ``AccountFacts``) is serialized
against every event emission for the company:

- if the transaction acquires the lock first, its writes/event are ordered
  before any concurrent emission and receive the lower ``company_sequence``;
- if a concurrent emission holds the lock, the transaction waits and then
  observes that emission's committed state.

Properties (empirically proven for PostgreSQL in the A3-PR2b design pass):

- the row lock is acquired inside whatever transaction/savepoint is open and
  is held until the OUTERMOST transaction commits (savepoint release does not
  release it; rollback to a savepoint does);
- re-acquiring the lock inside the same transaction is reentrant and free,
  so ``BusinessEvent.save`` re-locking the row after a caller already used
  this helper adds no ordering edge;
- acquisition does NOT increment ``last_sequence`` and consumes no event
  identity — a transaction that locks and then rolls back burns nothing.

This module must stay provider-neutral: no imports from connector or domain
apps.
"""

from django.db import IntegrityError

__all__ = ["lock_company_event_counter"]


def lock_company_event_counter(company):
    """Acquire (or create-and-acquire) the company's event-counter row lock.

    Returns the locked ``CompanyEventCounter`` instance. Safe against the
    first-ever-event creation race (mirrors the proven pattern previously
    inlined in ``BusinessEvent.save``): ``get_or_create`` savepoints its
    INSERT internally, and a loser of the creation race re-selects the
    winner's row FOR UPDATE.

    Must be called inside an open transaction; the lock lives until that
    transaction (not any inner savepoint) commits. Database routing follows
    the active tenant context, like every other read/write in the calling
    transaction.
    """
    from events.models import CompanyEventCounter

    try:
        counter, _ = CompanyEventCounter.objects.select_for_update().get_or_create(company=company)
    except IntegrityError:
        # Race: another transaction created the row between our failed get
        # and our create — take the lock on the existing row instead.
        counter = CompanyEventCounter.objects.select_for_update().get(company=company)
    return counter
