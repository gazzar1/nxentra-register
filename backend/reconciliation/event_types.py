# reconciliation/event_types.py
"""Reconciliation event payload classes.

A86.2 (2026-05-26): six dataclasses defining the canonical truth for
reconciliation match + exception state.

Per docs/finance_event_first_policy.md §1 ("the BusinessEvent log is
the source of truth, everything else is a derived view"), these
events ARE the canonical reconciliation state. BankStatementLine's
match_status / matched_journal_line_id / match_confidence fields are
derived from the projection in A86.3+.

Event shape design choices:

- MatchProposed is ADVISORY — a suggestion from a heuristic or AI
  agent. The projection records it for the operator's UI queue but
  does NOT mutate canonical match state. This is the line between
  "AI suggestion" and "operator/rule decision" — Codex/GPT both flag
  this distinction as load-bearing, see ENGINEERING_PROTOCOL.md §1.5.

- MatchConfirmed is the canonical decision. confirmation_kind discriminates
  who/what confirmed: "auto" (rule-based heuristic with confidence above
  threshold), "manual" (operator), "rule" (future policy/agent rule),
  "platform_payout_reconcile" (the bank_connector platform-payout path).

- MatchRejected, MatchUnmatched, ExceptionRaised, ExceptionResolved
  are operator/system decisions and ARE canonical.

- Reason fields are required at the API layer (>=10 chars), enforced
  by the command surface in A86.4-A86.6 — not by the dataclass itself
  (the dataclass would accept an empty string; the command rejects it).
  Keeping validation at the command layer mirrors A85 chunk 3b's
  override_reason handling.

Naming convention (matches EventTypes registry in events/types.py):
- reconciliation.match_proposed
- reconciliation.match_confirmed
- reconciliation.match_rejected
- reconciliation.match_unmatched
- reconciliation.exception_raised
- reconciliation.exception_resolved

REGISTERED_EVENTS at the bottom of this file is discovered by
ProjectionsConfig.ready() and merged into events.types.EVENT_DATA_CLASSES
so emit_event() validation works without an explicit import.
"""

from dataclasses import dataclass, field
from typing import Optional

from events.types import BaseEventData, EventTypes

# =============================================================================
# Match lifecycle
# =============================================================================


@dataclass
class ReconciliationMatchProposedData(BaseEventData):
    """Advisory: a suggested pairing between a bank line and a journal
    line. Emitted by the auto-match heuristic, manual-match suggestion
    surface, or a future AI agent.

    The projection records this in a suggestion queue for the operator
    UI but does NOT mutate canonical match state. Only MatchConfirmed
    changes the bank_line's match_status.

    Triggers no balance impact, no JE side effect. Pure signal.
    """

    bank_line_public_id: str = ""
    journal_line_public_id: str = ""
    # match_kind discriminates the source of the suggestion:
    # - "settlement_clearance"     — paired against a payment_settlement EBD line
    # - "platform_payout"          — paired against a Shopify/Stripe payout JE
    # - "generic_gl"               — paired against any unreconciled GL line
    # - "ai_agent"                 — paired by an AI agent (future)
    match_kind: str = ""
    # Decimal string 0-100. >= AUTO_MATCH_THRESHOLD (80) means a rule-based
    # confirmer would auto-confirm; lower values surface as Needs Review.
    confidence: str = "0"
    # proposer identifies the source: "auto_match_settlement_prepass_v1",
    # "platform_payout_prepass_v1", "ai_agent:claude-recon-v1", "operator_picker".
    proposer: str = ""
    proposed_at: str = ""
    # Free-form structured evidence backing the suggestion — e.g.,
    # {"matched_on": "amount+batch_id_in_description", "batch_id": "PMB-555"}.
    # The projection passes this through to the UI suggestion row.
    proposer_metadata: dict = field(default_factory=dict)


@dataclass
class ReconciliationMatchConfirmedData(BaseEventData):
    """Canonical: a confirmed match between a bank line and a journal
    line. The projection writes match_status / matched_journal_line_id
    / match_confidence onto the bank_line.

    confirmation_kind discriminates who/what confirmed:
    - "auto"                       — heuristic above AUTO_MATCH_THRESHOLD
    - "manual"                     — operator clicked confirm
    - "rule"                       — future automated policy rule
    - "platform_payout_reconcile"  — bank_connector matching.py path
    """

    bank_line_public_id: str = ""
    journal_line_public_id: str = ""
    match_kind: str = ""
    confidence: str = "100"
    confirmation_kind: str = ""
    # confirmed_by_user_id is None when a system/rule/agent confirms.
    confirmed_by_user_id: Optional[int] = None
    confirmed_by_email: str = ""
    confirmed_at: str = ""
    # If this confirmation followed a MatchProposed event, carry that
    # event's id forward for the audit chain. None for direct confirms
    # (e.g., auto-match flows that skip the propose step today).
    proposed_by_event_id: Optional[str] = None
    # MATCHED_WITH_DIFFERENCE support — A16 semantics carried through.
    # difference_amount == "0" for exact matches.
    difference_amount: str = "0"
    # UNRESOLVED initially when match has a difference; transitions when the
    # A16 resolve_difference flow emits ReconciliationDifferenceResolved
    # (A180 — before that event existed, resolution was a direct write that
    # rebuilds silently reverted).
    difference_reason: str = "UNRESOLVED"
    # statement_date captured for the read-model's reconciled_date column.
    statement_date: str = ""
    # A99 (2026-05-26): additional JournalLines that this match also
    # reconciles, beyond the primary `journal_line_public_id`. Used by the
    # settlement-prepass path in auto_match_statement, which creates a
    # clearance JE AND drains an EBD line in the same logical match — the
    # projection needs to flip JL.reconciled=True on both. Empty list for
    # the common bank-line ↔ single-JL case (manual_match, generic-GL
    # match, platform_payout_reconcile).
    additional_journal_lines_to_reconcile: list = field(default_factory=list)


@dataclass
class ReconciliationMatchRejectedData(BaseEventData):
    """Canonical: operator (or rule) rejected a previously-Proposed match.
    The projection records the rejection so the UI can hide that
    suggestion and (future) feed it back to the agent's learning loop.

    Rejection does NOT modify the bank_line — it stays UNMATCHED.
    """

    bank_line_public_id: str = ""
    journal_line_public_id: str = ""
    rejected_by_user_id: Optional[int] = None
    rejected_by_email: str = ""
    rejected_at: str = ""
    # Required (>=10 chars enforced by command); audit trail explains why.
    rejection_reason: str = ""
    # Link back to the MatchProposed event being rejected.
    proposed_by_event_id: Optional[str] = None


@dataclass
class ReconciliationMatchUnmatchedData(BaseEventData):
    """Canonical: a previously-Confirmed match is being reversed.

    The projection clears the bank_line's match fields (status →
    UNMATCHED or EXCLUDED per final_status) and the journal_line's
    reconciled flag.

    A19 reversal context: when a clearance JE was synthesized by the
    Confirmed event (settlement-prepass path), Unmatched also reverses
    those JEs. The reversed JE public_ids are captured here for audit.
    """

    bank_line_public_id: str = ""
    previously_matched_journal_line_public_id: str = ""
    # Carried forward from the MatchConfirmed event being reversed.
    match_kind: str = ""
    unmatched_by_user_id: Optional[int] = None
    unmatched_by_email: str = ""
    unmatched_at: str = ""
    # Required (>=10 chars enforced by command).
    unmatch_reason: str = ""
    # "UNMATCHED" (returns to needs-review queue) or "EXCLUDED" (operator
    # marks the bank line as out-of-scope for reconciliation).
    final_status: str = "UNMATCHED"
    # A19: clearance JEs reversed as part of this unmatch (empty list for
    # flag-flip-only unmatches against pre-existing JEs).
    reversed_clearance_je_public_ids: list = field(default_factory=list)
    # Link back to the MatchConfirmed event being reversed.
    confirmed_by_event_id: Optional[str] = None
    # A99 (2026-05-26): additional JournalLines to flip reconciled=False
    # beyond previously_matched_journal_line. Settlement-prepass path
    # carries the EBD line here so its reconciled flag is reset when the
    # original match is reversed. Empty list for the common case.
    additional_journal_lines_to_unreconcile: list = field(default_factory=list)


@dataclass
class ReconciliationDifferenceResolvedData(BaseEventData):
    """A180: operator resolved a MATCHED_WITH_DIFFERENCE bank line (the A16
    reason-picker flow). Carries the full resolution state so the
    ReconciliationProjection is the writer — before this event existed,
    resolve_difference direct-wrote the fields and a projection rebuild
    actively reverted difference_reason to UNRESOLVED (re-arming the
    double-submit guard → duplicate adjustment JEs)."""

    bank_line_public_id: str = ""
    # The matched clearance journal line (ReconciliationLink key derivation).
    journal_line_public_id: str = ""
    difference_reason: str = ""
    difference_notes: str = ""
    # Audit echo of the resolved residual (Decimal-as-string).
    difference_amount: str = "0"
    resolved_by_user_id: Optional[int] = None
    resolved_by_email: str = ""
    resolved_at: str = ""
    # The adjustment JE posted by resolve_difference.
    adjustment_entry_public_id: str = ""
    # The original settlement JE's EBD line to flip reconciled (fully
    # drained once clearance + adjustment are both posted). Empty when the
    # settlement JE has no un-reconciled EBD line.
    settlement_ebd_journal_line_public_id: str = ""
    # For the EBD line's reconciled_date.
    statement_date: str = ""


# =============================================================================
# Exception lifecycle (surfaces in /finance/exceptions)
# =============================================================================


@dataclass
class ReconciliationExceptionRaisedData(BaseEventData):
    """A reconciliation anomaly detected by the system. Surfaces in
    /finance/exceptions for operator triage.

    Exception kinds (extensible):
    - "stale_clearing"                   — provider clearing aged > N days unmatched
    - "orphan_bank_deposit"              — bank line with no settlement candidate
    - "orphan_settlement"                — settlement with no bank line within tolerance
    - "negative_clearing"                — provider clearing went negative (over-drained)
    - "duplicate_match_candidate"        — one bank line, multiple equally-good candidates
    - "match_with_difference_unresolved" — A16 MATCHED_WITH_DIFFERENCE without resolution
    """

    # Stable UUID for this exception — links Raised to Resolved.
    exception_public_id: str = ""
    bank_line_public_id: Optional[str] = None
    journal_entry_public_id: Optional[str] = None
    exception_kind: str = ""
    # "info" | "warning" | "blocker"
    severity: str = "warning"
    title: str = ""
    detail: str = ""
    detected_at: str = ""
    # Free-form structured evidence — e.g., {"days_aged": 21, "expected_amount": "1455.00"}.
    evidence: dict = field(default_factory=dict)


@dataclass
class ReconciliationExceptionResolvedData(BaseEventData):
    """Operator (or system action) resolved a previously-raised
    reconciliation exception. The projection marks the exception as
    resolved and links any related events (e.g., the MatchConfirmed
    that fixed an orphan deposit)."""

    # Links back to the Raised event by stable UUID.
    exception_public_id: str = ""
    resolved_by_user_id: Optional[int] = None
    resolved_by_email: str = ""
    resolved_at: str = ""
    # "matched" | "manually_adjusted" | "written_off" | "ignored"
    resolution_kind: str = ""
    # Required (>=10 chars enforced by command). Audit narrative.
    resolution_note: str = ""
    # E.g., the MatchConfirmed event_id that fixed an orphan-deposit
    # exception; empty for "ignored" resolutions.
    related_event_ids: list = field(default_factory=list)


# =============================================================================
# REGISTERED_EVENTS — auto-discovered by ProjectionsConfig.ready()
# =============================================================================


REGISTERED_EVENTS: dict[str, type[BaseEventData]] = {
    EventTypes.RECONCILIATION_MATCH_PROPOSED: ReconciliationMatchProposedData,
    EventTypes.RECONCILIATION_MATCH_CONFIRMED: ReconciliationMatchConfirmedData,
    EventTypes.RECONCILIATION_MATCH_REJECTED: ReconciliationMatchRejectedData,
    EventTypes.RECONCILIATION_MATCH_UNMATCHED: ReconciliationMatchUnmatchedData,
    EventTypes.RECONCILIATION_DIFFERENCE_RESOLVED: ReconciliationDifferenceResolvedData,
    EventTypes.RECONCILIATION_EXCEPTION_RAISED: ReconciliationExceptionRaisedData,
    EventTypes.RECONCILIATION_EXCEPTION_RESOLVED: ReconciliationExceptionResolvedData,
}
