# reconciliation/apps.py
"""Reconciliation bounded context app configuration.

A86.1 (2026-05-26): scaffold only. No models, no event registration,
no projection registration. Those land in A86.2 (events.py payload
types) and A86.3 (ReconciliationProjection).

The reconciliation domain owns:
- Match lifecycle events (proposed, confirmed, rejected, unmatched)
- Exception lifecycle events (raised, resolved)
- The ReconciliationProjection that derives bank-line match state from
  the above events (currently a side effect of the bank-rec commands;
  to be cut over in A86.7)
- Heuristic matching helpers + future agent suggestion surface
  (advisory only, never canonical)

See:
- docs/finance_event_first_policy.md §1 (read models are derived)
- ENGINEERING_PROTOCOL.md §1.4 (read models are derived, not canonical)
"""

from django.apps import AppConfig


class ReconciliationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "reconciliation"
    verbose_name = "Reconciliation (bounded context)"
    # A86.2 (2026-05-26): point ProjectionsConfig.ready() at our event
    # payload registry. REGISTERED_EVENTS in this module is auto-merged
    # into events.types.EVENT_DATA_CLASSES so emit_event() validation
    # works without an explicit import from caller code.
    event_types_module = "reconciliation.event_types"
    # A86.3 (2026-05-26): ReconciliationProjection in shadow mode.
    # Consumes the 6 RECONCILIATION_* event types; writes shadow fields
    # on BankStatementLine alongside the existing direct-mutation path.
    # A86.7 cutover swaps these in as canonical.
    projections = [
        "reconciliation.projections.ReconciliationProjection",
    ]
