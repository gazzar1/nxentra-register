# projections/write_barrier.py
#
# The write-barrier guards finance read-models against direct mutation outside
# the command/projection/bootstrap/migration paths. The "current context" is a
# scoped piece of state — push on enter, pop on exit — that ProjectionWriteManager
# inspects to decide whether a `.save()` is permitted.
#
# A95 (2026-05-26): swapped the backing store from threading.local to
# contextvars.ContextVar. The semantics are identical for plain sync Django
# (each thread still sees its own stack), but ContextVar additionally:
#
#   - Survives `asyncio.create_task` — a task started inside
#     `command_writes_allowed()` sees the parent's context. A
#     threading-local-based barrier would silently lose that context on
#     the first `await`, making finance writes in async views/workers
#     either spuriously blocked or — worse — sneak through one barrier
#     and trip another mid-transaction.
#   - Properly isolates a task's context changes from the parent
#     (Python copies the Context on task creation; mutations in the
#     task don't leak back).
#   - Is the recommended primitive for async-aware request-scoped state
#     since Python 3.7 (Django Channels, FastAPI, anyio all rely on it).
#
# No A86+ commands run under asyncio today, so the practical impact is zero —
# but every line in this file is something a future agent worker, Channels
# consumer, or async management command will lean on. Getting the primitive
# right BEFORE that work lands costs one focused commit; retrofitting it
# after means rewriting every test that asserts barrier behavior.

import contextvars
from contextlib import contextmanager

from django.conf import settings

# The stack is stored as an immutable tuple. Mutating a shared list would
# mutate it for every parent frame too — and across asyncio tasks, since
# ContextVar wraps the *value*, not a fresh copy. Tuples force the
# "rebind on every push" discipline that keeps the stack scoped correctly.
_write_context_stack: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "nxentra.projections.write_context_stack",
    default=(),
)


def current_write_context() -> str | None:
    stack = _write_context_stack.get()
    return stack[-1] if stack else None


def write_context_allowed(allowed_contexts: set[str]) -> bool:
    ctx = current_write_context()
    if ctx is None:
        return False
    if ctx == "admin_emergency":
        return ctx in allowed_contexts and getattr(settings, "ALLOW_ADMIN_EMERGENCY_WRITES", False)
    return ctx in allowed_contexts


@contextmanager
def _push_write_context(name: str):
    current = _write_context_stack.get()
    token = _write_context_stack.set(current + (name,))
    try:
        yield
    finally:
        _write_context_stack.reset(token)


@contextmanager
def command_writes_allowed():
    with _push_write_context("command"):
        yield


@contextmanager
def auth_writes_allowed():
    with _push_write_context("auth"):
        yield


@contextmanager
def projection_writes_allowed():
    with _push_write_context("projection"):
        yield


@contextmanager
def migration_writes_allowed():
    with _push_write_context("migration"):
        yield


@contextmanager
def bootstrap_writes_allowed():
    with _push_write_context("bootstrap"):
        yield


@contextmanager
def admin_emergency_writes_allowed():
    if not getattr(settings, "ALLOW_ADMIN_EMERGENCY_WRITES", False):
        raise RuntimeError("admin_emergency writes are disabled.")
    with _push_write_context("admin_emergency"):
        yield


# -----------------------------------------------------------------------------
# A129b / ADR-0001 P6 — bank-statement deletion guard
#
# A separate boolean flag (not the write-context stack) because statement
# deletion is orthogonal to read-model write permission: deleting a matched
# BankStatement orphans its posted clearance JE (the SET_NULL cascade leaves
# the JE without a reverser). A pre_delete signal blocks the delete unless this
# flag is set. Legitimate deleters (the unmatch_and_delete command after it
# reverses matches, the demo reseed, and offboarding) enter this context;
# restore-clear bypasses it by using raw SQL (signals don't fire). NOTE:
# offboarding's company.delete() is preempted by PROTECT FKs (BankStatement.
# account etc.) for a bootstrapped company, so that wrap only matters for an
# unverified company with no chart of accounts — see accounting/signals.py.
# -----------------------------------------------------------------------------
_statement_delete_allowed: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "nxentra.reconciliation.statement_delete_allowed",
    default=False,
)


def is_statement_delete_allowed() -> bool:
    return _statement_delete_allowed.get()


@contextmanager
def statement_delete_allowed():
    token = _statement_delete_allowed.set(True)
    try:
        yield
    finally:
        _statement_delete_allowed.reset(token)
