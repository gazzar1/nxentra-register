# reconciliation/events.py
"""Reconciliation event payload classes.

A86.1 (2026-05-26): empty scaffold. The actual payload classes
(ReconciliationMatchProposed / MatchConfirmed / MatchRejected /
MatchUnmatched / ExceptionRaised / ExceptionResolved) land in A86.2
using the existing dataclass-based BaseEventData pattern from
events/types.py (not Pydantic — that's a documentation drift in
finance_event_first_policy.md §2.3 to be corrected in A86.2's commit).

See:
- backend/events/types.py — BaseEventData + EventTypes constants
- docs/finance_event_first_policy.md §2 (event emission rules)
"""
