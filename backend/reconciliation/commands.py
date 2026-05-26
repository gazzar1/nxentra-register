# reconciliation/commands.py
"""Reconciliation command surface.

A86.1 (2026-05-26): empty scaffold. The propose_match, confirm_match,
reject_match, unmatch_match, raise_exception, resolve_exception
commands land in A86.4 (auto-match emits events), A86.5 (manual ops
emit events), and A86.6 (bank_connector payout reconciliation emits
events).

Until A86.8, the actual command implementations remain in
accounting/bank_reconciliation.py; this module receives them as a
move (with backward-compat shims) once the event-emission contract
is locked.

See:
- accounting/bank_reconciliation.py (current home)
- docs/finance_event_first_policy.md §2 (commands emit events)
"""
