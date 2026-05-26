# reconciliation/policies.py
"""Reconciliation domain policies (pure validators).

A86.1 (2026-05-26): empty scaffold. Will house the rules for:
- Who can confirm/reject a match (extends accounting.je.override_period
  to a reconciliation.match.confirm gate if needed)
- Closed-period rules for unmatch (reversing a confirmed match dated
  in a closed period requires override)
- Tenant-boundary checks on match operations
- High-confidence-auto-confirm threshold tuning

Policies are pure functions of (input, current state) -> (allow | deny
+ reason). No side effects, no event emission, no projection writes.

See: accounting/policies.py for the pattern.
"""
