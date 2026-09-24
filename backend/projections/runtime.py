# projections/runtime.py
"""
The registry-walk projection drain for everything outside ``projections/``.

Every registry-walk drain that lived outside this package — the command
layer's synchronous drain, the emitter's post-commit fallback and the Shopify
seed commands — now calls this module; ``projections.tasks``, the
``run_projections`` operator command and the tenant replay command keep their
own walks under Rule 20's per-file pins (tests/test_architecture_rules.py).
The runtime is a WALKER: it dispatches only through
``BaseProjection.process_pending`` — the A3 apply choke point and the in-flight
guard (a projection never observes its own pass) sit there, never here — and it
owns no transaction, savepoint, on_commit hook, RLS state or logging of its own.

Two entry points, proven by tests/test_projection_drain_characterisation.py
through their callers — ``command_drain`` (the command layer), the emitter
fallback and the seed commands:

``drain_company``  the ungated primitive: one ``process_pending`` pass per
                   registered projection, in live registration order, with the
                   caller's ``limit``; the first escaping exception propagates.
``command_drain``  the synchronous drain a domain command runs inside its own
                   open transaction after emitting: gated on
                   ``settings.PROJECTIONS_SYNC`` read at call time, whole
                   registry, ``limit=1000``, propagate, returns None. This is
                   the body the six former private ``_process_projections``
                   copies shared — the same gate → walk →
                   ``process_pending(company, limit=1000)`` in each, four of
                   them with an ``exclude`` filter no caller passes.

Deliberately absent (constitution rule 7: refactor around proven workflows):
``stop_on_error``, an include list, a per-projection error hook, a result dict
— no caller needs them today; the Celery task and the operator tools keep
their own selection, error and logging contracts until an ADR under A175
decides otherwise. Also absent by design: any dedup or in-flight logic (PR
#152 keeps that in ``process_pending``) and any transaction or RLS handling
(the caller's context is the contract — see the characterisation suite).

The registry import is lazy inside each function, exactly as the former
copies had it: ``projections.base`` imports the account, event and projection
models at module import, and an eager import here would give every command
module that edge at boot.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from accounts.models import Company


def drain_company(company: Company, *, limit: int = 1000, exclude: frozenset[str] = frozenset()) -> None:
    """Run one ``process_pending`` pass per registered projection for ``company``.

    Walks ``projection_registry.all()`` live, in registration (dict-insertion)
    order, never cached or sorted; skips projections whose exact name is in
    ``exclude``; dispatches ``projection.process_pending(company, limit=limit)``
    with ``company`` positional and ``stop_on_error`` at its default. The first
    exception escaping ``process_pending`` propagates at once and later
    projections are not visited. Opens no transaction, savepoint or on_commit
    hook and touches no RLS state.
    """
    from projections.base import projection_registry  # lazy: see module docstring

    for projection in projection_registry.all():
        if projection.name in exclude:
            continue
        projection.process_pending(company, limit=limit)


def command_drain(company: Company, exclude: Iterable[str] | None = None) -> None:
    """The synchronous command-layer drain (the former ``_process_projections``).

    Gated on ``settings.PROJECTIONS_SYNC`` read at CALL time (the production
    posture asserts it True at boot; tests flip it per test); whole registry in
    registration order; ``limit=1000``; the first escaping exception propagates
    into the caller's open transaction; the return value is discarded.
    ``exclude`` keeps the shape of the former copies (``None`` == nothing
    excluded); no production caller passes it.
    """
    if not settings.PROJECTIONS_SYNC:
        return
    drain_company(company, limit=1000, exclude=frozenset(exclude or ()))
