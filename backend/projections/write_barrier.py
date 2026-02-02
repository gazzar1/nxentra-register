# projections/write_barrier.py

from contextlib import contextmanager
import threading

from django.conf import settings


_state = threading.local()


def _context_stack() -> list[str]:
    stack = getattr(_state, "write_context_stack", None)
    if stack is None:
        stack = []
        _state.write_context_stack = stack
    return stack


def current_write_context() -> str | None:
    stack = _context_stack()
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
    stack = _context_stack()
    stack.append(name)
    try:
        yield
    finally:
        stack.pop()


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
