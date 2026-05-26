# reconciliation/exceptions.py
"""Reconciliation exception classification + queue helpers.

A86.1 (2026-05-26): empty scaffold. Houses the logic for classifying
reconciliation anomalies into ExceptionRaised events, plus the
queue/resolution helpers behind /finance/exceptions:

- Stale-clearing detection (provider clearing aged > N days unmatched)
- Difference-too-large detection (bank line ↔ settlement gap above
  the A35 tolerance)
- Orphan bank deposit (no settlement candidate)
- Orphan settlement (no bank line within tolerance)
- Negative provider clearing (over-drained)
- Duplicate match candidate (one bank line, multiple settlements)

The classification logic was previously scattered between
bank_reconciliation.py and the /finance/exceptions view; A86.8 will
centralize it here.

Note: not the same as Python's `exceptions` builtin pattern — this
file is domain exceptions (business anomalies that need operator
attention), not runtime errors.
"""
