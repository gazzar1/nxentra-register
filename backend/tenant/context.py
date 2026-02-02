"""
Tenant context using contextvars for async-safety.

This module provides thread-safe and async-safe storage of the
current tenant context, including the database alias to use.

Usage:
    # In middleware
    set_tenant_context(company_id=123, db_alias="tenant_acme", is_shared=False)

    # In application code
    db_alias = get_current_db_alias()  # Returns "tenant_acme"

    # Context manager for explicit scoping
    with tenant_context(company_id=123, db_alias="tenant_acme"):
        # All DB operations use tenant_acme database
        Account.objects.all()

Why contextvars instead of threading.local()?
- Async-safe: Works correctly with async views and ASGI
- Automatic cleanup: Token-based reset prevents context leakage
- No explicit thread management needed
"""
from contextvars import ContextVar
from contextlib import contextmanager
from typing import Optional, NamedTuple


class TenantContext(NamedTuple):
    """Immutable tenant context for a request."""

    company_id: int
    db_alias: str
    is_shared: bool  # True if mode=SHARED (RLS applies)


# ContextVar for current tenant - None means no tenant context (system operations)
_current_tenant: ContextVar[Optional[TenantContext]] = ContextVar(
    "current_tenant",
    default=None,
)


def get_current_tenant() -> Optional[TenantContext]:
    """
    Get the current tenant context.

    Returns None if no tenant context is set (e.g., during system operations
    or before middleware has processed the request).
    """
    return _current_tenant.get()


def get_current_db_alias() -> str:
    """
    Get the current database alias.

    Returns 'default' if no tenant context is set.
    This ensures backward compatibility - operations without
    explicit tenant context go to the shared database.
    """
    ctx = _current_tenant.get()
    return ctx.db_alias if ctx else "default"


def get_current_company_id() -> Optional[int]:
    """
    Get the current company ID.

    Returns None if no tenant context is set.
    """
    ctx = _current_tenant.get()
    return ctx.company_id if ctx else None


def is_shared_tenant() -> bool:
    """
    Check if current tenant is in shared mode (RLS applies).

    Returns True if:
    - No tenant context is set (assume shared/system operation)
    - Tenant context has is_shared=True
    """
    ctx = _current_tenant.get()
    return ctx.is_shared if ctx else True


def is_dedicated_tenant() -> bool:
    """
    Check if current tenant has a dedicated database.

    Returns True only if tenant context is set AND is_shared=False.
    """
    ctx = _current_tenant.get()
    return ctx is not None and not ctx.is_shared


def set_tenant_context(
    company_id: int,
    db_alias: str,
    is_shared: bool = True,
) -> None:
    """
    Set the current tenant context.

    Called by middleware after JWT authentication and TenantDirectory lookup.

    Args:
        company_id: The company ID from JWT token
        db_alias: Database alias from TenantDirectory (or "default")
        is_shared: True if tenant uses shared DB with RLS
    """
    ctx = TenantContext(
        company_id=company_id,
        db_alias=db_alias,
        is_shared=is_shared,
    )
    _current_tenant.set(ctx)


def clear_tenant_context() -> None:
    """
    Clear the current tenant context.

    Called by middleware in finally block to ensure cleanup.
    """
    _current_tenant.set(None)


@contextmanager
def tenant_context(company_id: int, db_alias: str, is_shared: bool = True):
    """
    Context manager for setting tenant context.

    Automatically restores previous context on exit (even on exception).

    Usage:
        with tenant_context(company_id=123, db_alias="tenant_acme", is_shared=False):
            # All DB operations use tenant_acme database
            events = BusinessEvent.objects.all()

    Args:
        company_id: The company ID
        db_alias: Database alias to use
        is_shared: True if using RLS (shared mode)
    """
    token = _current_tenant.set(
        TenantContext(
            company_id=company_id,
            db_alias=db_alias,
            is_shared=is_shared,
        )
    )
    try:
        yield
    finally:
        _current_tenant.reset(token)


@contextmanager
def system_db_context():
    """
    Context manager for system database operations.

    Temporarily clears tenant context so all operations go to 'default'.
    Useful for cross-tenant operations like TenantDirectory lookups.

    Usage:
        with system_db_context():
            # Queries go to default database regardless of current tenant
            tenant = TenantDirectory.objects.get(company_id=company_id)
    """
    token = _current_tenant.set(None)
    try:
        yield
    finally:
        _current_tenant.reset(token)


@contextmanager
def override_tenant_context(company_id: int, db_alias: str, is_shared: bool = True):
    """
    Context manager that overrides current tenant context.

    Same as tenant_context() but with a clearer name indicating override behavior.
    Useful in management commands or background tasks.

    Usage:
        # Export events for a specific tenant
        with override_tenant_context(company_id=tenant_id, db_alias=db_alias):
            events = BusinessEvent.objects.filter(company_id=tenant_id)
    """
    token = _current_tenant.set(
        TenantContext(
            company_id=company_id,
            db_alias=db_alias,
            is_shared=is_shared,
        )
    )
    try:
        yield
    finally:
        _current_tenant.reset(token)
