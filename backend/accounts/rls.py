"""
PostgreSQL Row-Level Security (RLS) context management.

This module manages PostgreSQL session configuration parameters that
control RLS policies. The parameters are set per-connection and used
by database-level policies to filter data.

Multi-Database Support:
- Functions accept an optional `conn` parameter for explicit connection
- If not specified, uses the current tenant's database connection
- Falls back to default connection if no tenant context is set

Parameters set:
- app.current_company_id: The current tenant's company ID
- app.rls_bypass: "on" or "off" to bypass RLS policies

Usage:
    # In middleware
    set_current_company_id(company_id)
    set_rls_bypass(settings.RLS_BYPASS)

    # In views that need cross-tenant access
    with rls_bypass():
        users = User.objects.all()

    # Cleanup
    clear_rls_context()
"""
from contextlib import contextmanager
from typing import Optional

from django.db import connection as default_connection, connections


def _get_connection(conn=None):
    """
    Get the appropriate database connection.

    Priority:
    1. Explicitly passed connection
    2. Current tenant's database connection (from tenant context)
    3. Default connection (fallback)
    """
    if conn is not None:
        return conn

    # Try to get tenant context database
    try:
        from tenant.context import get_current_db_alias

        db_alias = get_current_db_alias()
        return connections[db_alias]
    except (ImportError, LookupError):
        # tenant module not loaded yet or invalid alias
        return default_connection


def _set_config(name: str, value: Optional[str], *, conn=None) -> None:
    """
    Set a PostgreSQL session configuration parameter.

    Args:
        name: Parameter name (e.g., "app.current_company_id")
        value: Parameter value, or None to reset
        conn: Database connection to use
    """
    conn = _get_connection(conn)

    # Skip for non-PostgreSQL databases (SQLite in tests)
    if conn.vendor != "postgresql":
        return

    with conn.cursor() as cursor:
        if value is None:
            cursor.execute(f"RESET {name}")
        else:
            cursor.execute(
                "SELECT set_config(%s, %s, false)",
                [name, value],
            )


def _get_config(name: str, *, conn=None) -> Optional[str]:
    """
    Get a PostgreSQL session configuration parameter.

    Args:
        name: Parameter name
        conn: Database connection to use

    Returns:
        Parameter value or None if not set
    """
    conn = _get_connection(conn)

    # Return None for non-PostgreSQL databases
    if conn.vendor != "postgresql":
        return None

    with conn.cursor() as cursor:
        cursor.execute("SELECT current_setting(%s, true)", [name])
        row = cursor.fetchone()
    return row[0] if row else None


def set_current_company_id(company_id: Optional[int], *, conn=None) -> None:
    """
    Set the current company ID for RLS filtering.

    Args:
        company_id: Company ID to set, or None to clear
        conn: Database connection to use
    """
    if company_id is None:
        _set_config("app.current_company_id", None, conn=conn)
        return
    _set_config("app.current_company_id", str(company_id), conn=conn)


def get_current_company_id(*, conn=None) -> Optional[int]:
    """
    Get the current company ID from the database session.

    Returns:
        Company ID or None if not set
    """
    value = _get_config("app.current_company_id", conn=conn)
    if value:
        try:
            return int(value)
        except ValueError:
            return None
    return None


def set_rls_bypass(enabled: bool, *, conn=None) -> None:
    """
    Enable or disable RLS bypass.

    When bypass is enabled, RLS policies allow all operations.
    This is used for:
    - Testing environments
    - Cross-tenant administrative operations
    - Dedicated tenant databases (single tenant, no need for RLS)

    Args:
        enabled: True to bypass RLS, False to enforce
        conn: Database connection to use
    """
    _set_config("app.rls_bypass", "on" if enabled else "off", conn=conn)


def is_rls_bypassed(*, conn=None) -> bool:
    """
    Check if RLS is currently bypassed.

    Returns:
        True if bypass is enabled
    """
    value = _get_config("app.rls_bypass", conn=conn)
    return value == "on"


@contextmanager
def rls_bypass(*, conn=None):
    """
    Context manager to temporarily bypass RLS.

    Saves the previous bypass state and restores it on exit.

    Usage:
        with rls_bypass():
            # RLS is bypassed here
            all_users = User.objects.all()
        # RLS is restored to previous state

    Args:
        conn: Database connection to use
    """
    previous = _get_config("app.rls_bypass", conn=conn)
    set_rls_bypass(True, conn=conn)
    try:
        yield
    finally:
        if previous is None:
            _set_config("app.rls_bypass", None, conn=conn)
        else:
            _set_config("app.rls_bypass", previous, conn=conn)


def clear_rls_context(*, conn=None) -> None:
    """
    Clear all RLS-related session parameters.

    Should be called in middleware finally blocks to ensure
    connection state is clean for the next request.

    Args:
        conn: Database connection to use
    """
    _set_config("app.current_company_id", None, conn=conn)
    _set_config("app.rls_bypass", None, conn=conn)


def set_rls_context(company_id: int, bypass: bool = False, *, conn=None) -> None:
    """
    Convenience function to set both RLS parameters at once.

    Args:
        company_id: Company ID for filtering
        bypass: Whether to bypass RLS
        conn: Database connection to use
    """
    set_current_company_id(company_id, conn=conn)
    set_rls_bypass(bypass, conn=conn)
