# reconciliation/matching.py
"""Reconciliation matching helpers — heuristic + future agent suggestions.

A86.1 (2026-05-26): empty scaffold. Houses the pure-read planners
once they're moved out of accounting/bank_reconciliation.py in A86.8:
- _plan_settlement_prepass_matches (auto-match planning)
- _platform_prepass_match planning
- generic GL-level match planning
- candidate generation for the operator UI's "needs review" queue

This module is ADVISORY ONLY. Nothing in here is allowed to mutate
canonical match state. The output is a plan (suggestion) that a
command turns into a ReconciliationMatchProposed or
ReconciliationMatchConfirmed event.

Future AI-agent suggestions live here too — they emit MatchProposed,
they never confirm.

See: accounting/bank_reconciliation.py (current home of the planners)
"""
