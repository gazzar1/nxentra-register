# reconciliation/projections.py
"""ReconciliationProjection — derives bank-line match state from events.

A86.3 (2026-05-26): SHADOW mode. The projection writes new event_*
fields on BankStatementLine alongside the existing match_status /
matched_journal_line / match_confidence direct-mutation path. Nothing
in production behavior changes — the operator UI continues to read
the legacy fields. Convergence between the two paths is exercised
by tests; the cutover (event_* becomes canonical) happens in A86.7.

What this projection handles
============================

- ReconciliationMatchConfirmed   → write shadow fields on bank_line
- ReconciliationMatchUnmatched   → clear shadow fields on bank_line
- ReconciliationMatchProposed    → recorded, NO shadow state change
                                   (advisory-vs-canonical contract)
- ReconciliationMatchRejected    → recorded, NO shadow state change
- ReconciliationExceptionRaised  → A86.3 no-op (exception read model
                                   lands in a later chunk)
- ReconciliationExceptionResolved → A86.3 no-op

Idempotency
===========

Provided by BaseProjection.process_pending() via ProjectionAppliedEvent
unique constraint on (company, projection_name, event). The handler
itself does not need to guard against re-application; framework
guarantees handle() is called at most once per (company, event) pair
under normal operation.

For defense-in-depth during a rebuild, event_last_match_event_id on
BankStatementLine lets the projection assert "this event already
applied" without consulting ProjectionAppliedEvent — useful for the
A86.7 replay-convergence test.

See:
- docs/finance_event_first_policy.md §3 (read model + projection rules)
- docs/projection-idempotency.md (idempotency contract)
- reconciliation/event_types.py (event payload classes)
"""

from __future__ import annotations

import logging
from decimal import Decimal

from accounting.models import BankStatementLine, JournalLine
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection
from projections.exceptions import ProjectionInvalidDataError

logger = logging.getLogger(__name__)


PROJECTION_NAME = "reconciliation"


# Event types this projection consumes — declared up front so subclasses /
# tests / framework registration don't need to walk handle() to discover them.
_CONSUMES = [
    EventTypes.RECONCILIATION_MATCH_PROPOSED,
    EventTypes.RECONCILIATION_MATCH_CONFIRMED,
    EventTypes.RECONCILIATION_MATCH_REJECTED,
    EventTypes.RECONCILIATION_MATCH_UNMATCHED,
    EventTypes.RECONCILIATION_EXCEPTION_RAISED,
    EventTypes.RECONCILIATION_EXCEPTION_RESOLVED,
]


class ReconciliationProjection(BaseProjection):
    """Shadow projection that derives bank-line match state from
    ReconciliationMatch* events. A86.7 cutover swaps the operator UI's
    canonical read onto these shadow fields; until then the legacy
    direct-mutation path is the canonical source and these fields are
    used only for convergence proofs."""

    @property
    def name(self) -> str:
        return PROJECTION_NAME

    @property
    def consumes(self) -> list[str]:
        return list(_CONSUMES)

    def handle(self, event: BusinessEvent) -> None:
        et = event.event_type

        if et == EventTypes.RECONCILIATION_MATCH_CONFIRMED:
            self._handle_match_confirmed(event)
        elif et == EventTypes.RECONCILIATION_MATCH_UNMATCHED:
            self._handle_match_unmatched(event)
        elif et == EventTypes.RECONCILIATION_MATCH_PROPOSED:
            # ADVISORY: a suggestion is not a state change. The projection
            # records it (future chunks may build a suggestion-queue read
            # model) but does NOT touch the bank_line's shadow match
            # fields. This is the load-bearing line between "AI/heuristic
            # proposes" and "operator/rule confirms" — see
            # docs/finance_event_first_policy.md §1 + ENGINEERING_PROTOCOL.md §1.5.
            data = event.get_data()
            logger.info(
                "Reconciliation: proposal recorded (no shadow write) — "
                "bank_line=%s journal_line=%s proposer=%s confidence=%s",
                data.get("bank_line_public_id"),
                data.get("journal_line_public_id"),
                data.get("proposer"),
                data.get("confidence"),
            )
        elif et == EventTypes.RECONCILIATION_MATCH_REJECTED:
            # Rejection of a Proposed match: no state change (the bank
            # line was never Confirmed, so there's nothing to clear).
            data = event.get_data()
            logger.info(
                "Reconciliation: rejection recorded (no shadow write) — bank_line=%s journal_line=%s",
                data.get("bank_line_public_id"),
                data.get("journal_line_public_id"),
            )
        elif et in (
            EventTypes.RECONCILIATION_EXCEPTION_RAISED,
            EventTypes.RECONCILIATION_EXCEPTION_RESOLVED,
        ):
            # A86.3: exception read model isn't built yet. The events are
            # consumed (bookmark advances + ProjectionAppliedEvent row
            # written) so the framework treats them as processed, but no
            # downstream write happens until the exception read model
            # lands in a later chunk. Recording here so the event is
            # observably consumed at /finance/exceptions integration time.
            data = event.get_data()
            logger.info(
                "Reconciliation: exception event consumed (read model TBD) — type=%s exception_public_id=%s",
                et,
                data.get("exception_public_id"),
            )
        else:
            # Unreachable under normal config (process_pending filters by
            # self.consumes), but defensive against routing bugs.
            raise ProjectionInvalidDataError(f"ReconciliationProjection received unexpected event_type {et!r}")

    # -------------------------------------------------------------------------
    # MatchConfirmed: write shadow fields on the bank line
    # -------------------------------------------------------------------------

    def _handle_match_confirmed(self, event: BusinessEvent) -> None:
        data = event.get_data()

        bank_line_public_id = data.get("bank_line_public_id") or ""
        journal_line_public_id = data.get("journal_line_public_id") or ""
        confirmation_kind = data.get("confirmation_kind") or ""

        if not bank_line_public_id:
            raise ProjectionInvalidDataError("ReconciliationMatchConfirmed event missing bank_line_public_id")
        if not journal_line_public_id:
            raise ProjectionInvalidDataError("ReconciliationMatchConfirmed event missing journal_line_public_id")

        # Locate the bank line. Missing here means the event arrived
        # before the bank-statement-import command committed the line —
        # in current code that's impossible (import_bank_statement
        # commits BankStatementLines before any reconciliation command
        # runs), so treat it as a hard projection state error per
        # docs/finance_event_first_policy.md §8.
        try:
            bank_line = BankStatementLine.objects.get(
                company=event.company,
                public_id=bank_line_public_id,
            )
        except BankStatementLine.DoesNotExist as exc:
            raise ProjectionInvalidDataError(
                f"ReconciliationMatchConfirmed references unknown bank_line_public_id={bank_line_public_id!r}"
            ) from exc

        try:
            journal_line = JournalLine.objects.get(
                company=event.company,
                public_id=journal_line_public_id,
            )
        except JournalLine.DoesNotExist as exc:
            raise ProjectionInvalidDataError(
                f"ReconciliationMatchConfirmed references unknown journal_line_public_id={journal_line_public_id!r}"
            ) from exc

        # Map confirmation_kind → MatchStatus. A16 difference flow flips
        # the read-model status to MATCHED_WITH_DIFFERENCE when difference
        # is non-zero, regardless of confirmation_kind.
        difference_amount_raw = data.get("difference_amount") or "0"
        try:
            difference_amount = Decimal(str(difference_amount_raw))
        except (ValueError, ArithmeticError):
            difference_amount = Decimal("0")

        if difference_amount != 0:
            status = BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE
        elif confirmation_kind == "manual":
            status = BankStatementLine.MatchStatus.MANUAL_MATCHED
        else:
            # "auto", "rule", "platform_payout_reconcile" all map to
            # AUTO_MATCHED in the read model — the audit trail of WHICH
            # path confirmed lives in the event log + future audit read.
            status = BankStatementLine.MatchStatus.AUTO_MATCHED

        # Confidence — payload carries Decimal-as-string per BaseEventData
        # convention. Default 0 if missing or unparseable.
        confidence_raw = data.get("confidence") or "0"
        try:
            confidence = Decimal(str(confidence_raw))
        except (ValueError, ArithmeticError):
            confidence = Decimal("0")

        confirmed_at = data.get("confirmed_at") or None

        # Write the shadow fields. The legacy match_status /
        # matched_journal_line / match_confidence fields are NOT touched
        # here — that's the direct-mutation path's job until A86.7.
        BankStatementLine.objects.filter(pk=bank_line.pk).update(
            event_match_status=status,
            event_matched_journal_line=journal_line,
            event_match_confidence=confidence,
            event_last_match_event_id=event.id,
            event_confirmed_at=confirmed_at if confirmed_at else None,
        )

        logger.info(
            "Reconciliation shadow write: bank_line=%s status=%s confidence=%s via confirmation_kind=%s event_id=%s",
            bank_line_public_id,
            status,
            confidence,
            confirmation_kind,
            event.id,
        )

    # -------------------------------------------------------------------------
    # MatchUnmatched: clear shadow fields on the bank line
    # -------------------------------------------------------------------------

    def _handle_match_unmatched(self, event: BusinessEvent) -> None:
        data = event.get_data()

        bank_line_public_id = data.get("bank_line_public_id") or ""
        final_status = data.get("final_status") or ""

        if not bank_line_public_id:
            raise ProjectionInvalidDataError("ReconciliationMatchUnmatched event missing bank_line_public_id")

        try:
            bank_line = BankStatementLine.objects.get(
                company=event.company,
                public_id=bank_line_public_id,
            )
        except BankStatementLine.DoesNotExist as exc:
            raise ProjectionInvalidDataError(
                f"ReconciliationMatchUnmatched references unknown bank_line_public_id={bank_line_public_id!r}"
            ) from exc

        # Two paths through Unmatched:
        # - "UNMATCHED" — return to needs-review queue (shadow cleared back
        #   to UNMATCHED, FK cleared, confidence cleared)
        # - "EXCLUDED" — operator marks the line as out-of-scope (shadow
        #   set to EXCLUDED; FK + confidence still cleared since the
        #   match itself is being reversed)
        if final_status == BankStatementLine.MatchStatus.EXCLUDED:
            target_status = BankStatementLine.MatchStatus.EXCLUDED
        else:
            target_status = BankStatementLine.MatchStatus.UNMATCHED

        BankStatementLine.objects.filter(pk=bank_line.pk).update(
            event_match_status=target_status,
            event_matched_journal_line=None,
            event_match_confidence=None,
            event_last_match_event_id=event.id,
            event_confirmed_at=None,
        )

        logger.info(
            "Reconciliation shadow clear: bank_line=%s final_status=%s event_id=%s",
            bank_line_public_id,
            target_status,
            event.id,
        )
