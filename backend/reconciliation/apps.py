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
