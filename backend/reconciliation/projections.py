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

from django.conf import settings

from accounting.models import BankStatementLine, JournalLine
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection
from projections.exceptions import ProjectionInvalidDataError

logger = logging.getLogger(__name__)


PROJECTION_NAME = "reconciliation"


def _event_driven_state_enabled() -> bool:
    """A86.7a (2026-05-26): runtime check of the
    RECONCILIATION_EVENT_DRIVEN_STATE feature flag. Read every call so
    `django.test.override_settings` (used heavily in the cutover tests)
    can flip behavior per-test without import-time caching.
    """
    return bool(getattr(settings, "RECONCILIATION_EVENT_DRIVEN_STATE", False))


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

        # A86.6 (2026-05-26): platform_payout_reconcile is a separate
        # surface (bank_connector's BankTransaction ↔ payout JE) that
        # doesn't involve a BankStatementLine. No BSL shadow write
        # happens here.
        #
        # A86.7a (2026-05-26): when the cutover flag is on, this branch
        # also owns the JL.reconciled flip — moving the protocol violation
        # in bank_connector/matching.py:288 (direct projection_writes_allowed
        # write from a non-projection module) into the canonical
        # event-projection layer. Flag off: the legacy path in
        # _reconcile_payout_je already did the flip; this branch is pure
        # audit-trail recording.
        if confirmation_kind == "platform_payout_reconcile":
            if _event_driven_state_enabled() and journal_line_public_id:
                # Look up + flip the JL. Idempotent (True→True is a no-op).
                # confirmed_at on the event payload is the bank tx date.
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
                    "Reconciliation: platform_payout_reconcile canonical flip — "
                    "journal_line=%s reconciled_date=%s event_id=%s",
                    journal_line_public_id,
                    reconciled_date_value,
                    event.id,
                )
            else:
                logger.info(
                    "Reconciliation: platform_payout_reconcile event consumed "
                    "(legacy direct flip owns canonical) — journal_line=%s event_id=%s",
                    journal_line_public_id,
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

        confirmed_at = data.get("confirmed_at") or None

        # A86.7a (2026-05-26): write the shadow fields ALWAYS. When the
        # cutover flag is on, also write the canonical fields — the
        # projection becomes the sole writer to match_status /
        # matched_journal_line / match_confidence, and the legacy
        # direct-mutation paths skip the same fields. Keeping shadow
        # writes in cutover mode preserves observability + provides a
        # rollback safety net until A86.7b drops the shadow fields.
        update_kwargs = {
            "event_match_status": status,
            "event_matched_journal_line": journal_line,
            "event_match_confidence": confidence,
            "event_last_match_event_id": event.id,
            "event_confirmed_at": confirmed_at if confirmed_at else None,
        }
        if _event_driven_state_enabled():
            update_kwargs.update(
                {
                    "match_status": status,
                    "matched_journal_line": journal_line,
                    "match_confidence": confidence,
                }
            )
        BankStatementLine.objects.filter(pk=bank_line.pk).update(**update_kwargs)

        logger.info(
            "Reconciliation %s write: bank_line=%s status=%s confidence=%s via confirmation_kind=%s event_id=%s",
            "canonical+shadow" if _event_driven_state_enabled() else "shadow",
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

        # A86.7a: shadow always cleared; canonical also cleared when flag on.
        update_kwargs = {
            "event_match_status": target_status,
            "event_matched_journal_line": None,
            "event_match_confidence": None,
            "event_last_match_event_id": event.id,
            "event_confirmed_at": None,
        }
        if _event_driven_state_enabled():
            update_kwargs.update(
                {
                    "match_status": target_status,
                    "matched_journal_line": None,
                    "match_confidence": None,
                }
            )
        BankStatementLine.objects.filter(pk=bank_line.pk).update(**update_kwargs)

        logger.info(
            "Reconciliation %s clear: bank_line=%s final_status=%s event_id=%s",
            "canonical+shadow" if _event_driven_state_enabled() else "shadow",
            bank_line_public_id,
            target_status,
            event.id,
        )
