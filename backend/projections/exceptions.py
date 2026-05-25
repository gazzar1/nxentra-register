# projections/exceptions.py
"""
Exception types raised by projection handlers to signal failure to the
framework. A80 (2026-05-25): replaces the silent `logger.warning + return`
anti-pattern in handlers with explicit failure modes that surface to
operators via `ProjectionFailureLog`.

See:
- docs/finance_event_first_policy.md §8 — Failure modes: loud, not silent
- projections/base.py BaseProjection.on_error — writes log entries
- projections/models.py ProjectionFailureLog — operator-visible failures
"""


class ProjectionStateError(Exception):
    """Raised when a projection handler cannot process an event because the
    company's accounting state is incomplete or inconsistent — e.g.:

    - Missing ModuleAccountMapping (no chart-of-accounts wiring for the module)
    - Missing role inside the mapping (e.g. SALES_REVENUE not configured)
    - Store missing default_customer or default_posting_profile
    - Required dimension or settlement provider not lazy-creatable

    These are FIXABLE by operator action (complete the wizard, run a backfill
    command, etc.). The event remains unprocessed (transaction rolled back),
    so once the operator fixes the underlying state, the next
    `process_pending` pass self-heals.

    The framework's `on_error` writes a `ProjectionFailureLog` row so the
    operator can see what's broken in `/finance/exceptions` without having
    to grep Django logs.
    """

    def __init__(self, message: str, *, fix_hint: str = ""):
        super().__init__(message)
        self.fix_hint = fix_hint


class ProjectionInvalidDataError(Exception):
    """Raised when an event's payload is structurally valid but the values
    don't permit producing a meaningful read-model record — e.g.:

    - Order with all amounts (subtotal, tax, shipping) zero so no lines exist
    - Refund whose amount exceeds the original invoice total
    - Line with quantity but no price (would create a zero-value line)

    Distinct from ProjectionStateError because the company's CONFIG is fine;
    it's the SOURCE DATA that's problematic. Operator may need to edit the
    source system (Shopify, Stripe, etc.) or manually mark the failure
    resolved if the data should be intentionally skipped.
    """

    def __init__(self, message: str):
        super().__init__(message)


class ProjectionCommandFailedError(Exception):
    """Raised when a downstream command call inside a projection handler
    returned ``CommandResult.fail(...)``. The handler can't proceed but
    this isn't an exception in the command itself — it's an expected
    failure result that the projection layer must surface upward instead
    of silently swallowing.

    Pre-A80, projections matched the bad pattern:

        if not result.success:
            logger.error("Failed to create X: %s", result.error)
            return  # ← silent — event marked consumed, no record created

    Post-A80, the projection raises this exception so:
    - The transaction rolls back (event remains unprocessed)
    - `on_error` writes a ProjectionFailureLog entry
    - Operator can see WHY the command refused in `/finance/exceptions`
    - After the underlying issue is fixed, next pass auto-recovers
    """

    def __init__(self, message: str, *, command_name: str = "", original_error: str = ""):
        super().__init__(message)
        self.command_name = command_name
        self.original_error = original_error
