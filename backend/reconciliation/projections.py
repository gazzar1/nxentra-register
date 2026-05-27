# reconciliation/projections.py
"""ReconciliationProjection — derives bank-line match state from events.

A86.7b (2026-05-26): CANONICAL writer. ReconciliationProjection now
owns the BankStatementLine match fields (match_status,
matched_journal_line, match_confidence) and the JournalLine.reconciled
flip for the platform-payout-reconcile path. The legacy direct-mutation
code in accounting/bank_reconciliation.py + bank_connector/matching.py
has been removed; the event stream is the only path to a canonical
write. Replay convergence is therefore a guaranteed property of the
system (see tests/test_a86_7a_cutover.py:test_replay_convergence_full_lifecycle).

What this projection handles
============================

- ReconciliationMatchConfirmed   → write match state on bank_line OR
                                   flip JL.reconciled (depending on
                                   confirmation_kind)
- ReconciliationMatchUnmatched   → clear match state on bank_line
- ReconciliationMatchProposed    → recorded, NO state change
                                   (advisory-vs-canonical contract)
- ReconciliationMatchRejected    → recorded, NO state change
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
    """Canonical projection that derives bank-line match state from
    ReconciliationMatch* events. A86.7b made this the sole writer for
    match_status / matched_journal_line / match_confidence; the operator
    UI reads those fields and they are guaranteed to be a deterministic
    fold over the event log (proven by test_replay_convergence_full_lifecycle)."""

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
            # model) but does NOT touch the bank_line's canonical match
            # fields. This is the load-bearing line between "AI/heuristic
            # proposes" and "operator/rule confirms" — see
            # docs/finance_event_first_policy.md §1 + ENGINEERING_PROTOCOL.md §1.5.
            data = event.get_data()
            logger.info(
                "Reconciliation: proposal recorded (no state write) — "
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
                "Reconciliation: rejection recorded (no state write) — bank_line=%s journal_line=%s",
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
    # MatchConfirmed: write match state on the bank line (or flip JL for
    # the platform_payout_reconcile branch).
    # -------------------------------------------------------------------------

    def _handle_match_confirmed(self, event: BusinessEvent) -> None:
        data = event.get_data()

        bank_line_public_id = data.get("bank_line_public_id") or ""
        journal_line_public_id = data.get("journal_line_public_id") or ""
        confirmation_kind = data.get("confirmation_kind") or ""

        # A86.6 / A86.7b (2026-05-26): platform_payout_reconcile is the
        # bank_connector surface (BankTransaction ↔ payout JE). It does
        # NOT involve a BankStatementLine — the projection's job here
        # is to flip JL.reconciled. This branch is the canonical owner
        # of that flip: bank_connector/matching.py no longer mutates
        # JL.reconciled directly (closes the Codex-flagged protocol
        # violation).
        if confirmation_kind == "platform_payout_reconcile":
            if journal_line_public_id:
                # Look up + flip the JL. Idempotent (True→True is a no-op).
                confirmed_at_raw = data.get("confirmed_at") or ""
                statement_date_raw = data.get("statement_date") or ""
                # Prefer statement_date (the bank-tx transaction_date stamped
                # into the event payload by _emit_platform_payout_reconcile_event)
                # for reconciled_date; fall back to confirmed_at.
                reconciled_date_value = statement_date_raw or confirmed_at_raw[:10]
                JournalLine.objects.filter(
                    company=event.company,
                    public_id=journal_line_public_id,
                ).update(
                    reconciled=True,
                    reconciled_date=reconciled_date_value if reconciled_date_value else None,
                )
                logger.info(
                    "Reconciliation: platform_payout_reconcile flip — journal_line=%s reconciled_date=%s event_id=%s",
                    journal_line_public_id,
                    reconciled_date_value,
                    event.id,
                )
            return

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

        # A86.7b: canonical match-state write. A99 (2026-05-26): now also
        # writes A16 difference fields and flips JL.reconciled — both used
        # to be direct mutations in reconciliation/commands.py. The shadow
        # fields (event_match_status, event_matched_journal_line, etc.) were
        # dropped in migration 0038; this projection is the sole writer.
        difference_reason_raw = data.get("difference_reason") or BankStatementLine.DifferenceReason.UNRESOLVED
        BankStatementLine.objects.filter(pk=bank_line.pk).update(
            match_status=status,
            matched_journal_line=journal_line,
            match_confidence=confidence,
            difference_amount=difference_amount,
            difference_reason=difference_reason_raw,
        )

        # A99: flip JL.reconciled=True for the primary matched line. The
        # date is the bank-line's statement date when available (preferred
        # so reversal/replay can locate the original reconciliation period)
        # or the confirmed_at date as a fallback.
        confirmed_at_raw = data.get("confirmed_at") or ""
        statement_date_raw = data.get("statement_date") or ""
        reconciled_date_value = statement_date_raw or confirmed_at_raw[:10]
        JournalLine.objects.filter(
            company=event.company,
            public_id=journal_line.public_id,
        ).update(
            reconciled=True,
            reconciled_date=reconciled_date_value if reconciled_date_value else None,
        )

        # A99: settlement-prepass path also drains a separate EBD line in
        # the same logical match — flip those too. Empty list for the common
        # one-JL match shape.
        additional_jl_public_ids = data.get("additional_journal_lines_to_reconcile") or []
        if additional_jl_public_ids:
            JournalLine.objects.filter(
                company=event.company,
                public_id__in=[str(p) for p in additional_jl_public_ids],
            ).update(
                reconciled=True,
                reconciled_date=reconciled_date_value if reconciled_date_value else None,
            )

        logger.info(
            "Reconciliation write: bank_line=%s status=%s confidence=%s difference=%s "
            "primary_jl=%s additional_jls=%d via confirmation_kind=%s event_id=%s",
            bank_line_public_id,
            status,
            confidence,
            difference_amount,
            journal_line_public_id,
            len(additional_jl_public_ids),
            confirmation_kind,
            event.id,
        )

    # -------------------------------------------------------------------------
    # MatchUnmatched: clear match state on the bank line.
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
        # - "UNMATCHED" — return to needs-review queue (FK + confidence
        #   cleared; status set back to UNMATCHED)
        # - "EXCLUDED" — operator marks the line as out-of-scope (status
        #   set to EXCLUDED; FK + confidence still cleared since the
        #   match itself is being reversed)
        if final_status == BankStatementLine.MatchStatus.EXCLUDED:
            target_status = BankStatementLine.MatchStatus.EXCLUDED
        else:
            target_status = BankStatementLine.MatchStatus.UNMATCHED

        # A99 (2026-05-26): also clear A16 difference fields. Used to be
        # a direct mutation in reconciliation/commands.py unmatch_line.
        # difference_notes / resolved_at / adjustment_entry are written
        # by the A16 resolve_difference flow; clearing them here keeps the
        # bank line consistent with "no match → no resolved difference".
        BankStatementLine.objects.filter(pk=bank_line.pk).update(
            match_status=target_status,
            matched_journal_line=None,
            match_confidence=None,
            difference_amount=Decimal("0"),
            difference_reason=BankStatementLine.DifferenceReason.UNRESOLVED,
            difference_notes="",
            difference_resolved_at=None,
            difference_adjustment_entry=None,
        )

        # A99: also un-reconcile the JL that was matched. Used to be a
        # latent bug — confirming flipped JL.reconciled=True directly, but
        # unmatch never flipped it back, so a JL could carry a stale
        # reconciled=True flag with no bank line pointing at it. Now both
        # transitions flow through the projection so the invariant holds.
        jl_public_ids_to_unreconcile: list[str] = []
        previously_matched_jl_public_id = data.get("previously_matched_journal_line_public_id") or ""
        if previously_matched_jl_public_id:
            jl_public_ids_to_unreconcile.append(str(previously_matched_jl_public_id))
        # Settlement-prepass path: the EBD line that was reconciled by the
        # original match needs to be un-reconciled when the match reverses.
        for jl_public_id in data.get("additional_journal_lines_to_unreconcile") or []:
            jl_public_ids_to_unreconcile.append(str(jl_public_id))

        if jl_public_ids_to_unreconcile:
            JournalLine.objects.filter(
                company=event.company,
                public_id__in=jl_public_ids_to_unreconcile,
            ).update(
                reconciled=False,
                reconciled_date=None,
            )

        logger.info(
            "Reconciliation clear: bank_line=%s final_status=%s unreconciled_jls=%d event_id=%s",
            bank_line_public_id,
            target_status,
            len(jl_public_ids_to_unreconcile),
            event.id,
        )
