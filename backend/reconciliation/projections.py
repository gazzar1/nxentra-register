# reconciliation/projections.py
"""ReconciliationProjection — derives bank-line match state from events.

A86.1 (2026-05-26): empty scaffold. The projection class lands in
A86.3 in shadow mode (writes to new event_* fields alongside the
existing direct-mutation pattern). A86.7's cutover flips the canonical
read off the shadow fields onto these projection writes.

What this projection will own:
- bank_statement_line.match_status (derived from MatchConfirmed/
  MatchRejected/MatchUnmatched events)
- bank_statement_line.matched_journal_line_id
- bank_statement_line.match_confidence
- A pending-suggestions queue derived from MatchProposed events
  (advisory only; never affects canonical match state — per
  ENGINEERING_PROTOCOL.md §1.4 "read models are derived")

See:
- projections/base.py (BaseProjection)
- docs/projection-idempotency.md
"""
