# stripe_connector/reconciled_emit.py
"""ADR-0002 PR-D — emit PROVIDER_PAYOUT_RECONCILED snapshots for Stripe payouts.

The legacy StripePayoutTransaction.verified / local_charge match state is
mutated by direct writes with no event (reconcile_payout auto-match +
StripePayoutVerifyView) — not replay-durable, and blocking C4b. This module
gives that state an event home WITHOUT touching any legacy write:

- build_reconciled_snapshot() reads the payout's frozen settlement event +
  the CURRENT legacy line state and produces a full-state snapshot payload
  (every line verdict, header outcome, event-frozen variances). Full-state,
  not delta: replay is last-write-wins in company_sequence order, so each
  event is self-sufficient (A139) and one backfill pass captures pre-PR-D
  history.
- maybe_emit_payout_reconciled() emits it ONLY when the snapshot differs from
  the last emitted one (reconcile runs on every detail GET and 30-day
  exception scan — steady-state runs must produce zero events), then feeds
  the variance outcome to the ReconciliationException producer.

Emission is deliberately FLAG-INDEPENDENT (variances come from the settlement
event's own frozen totals vs its frozen line sums — never the flag-selected
header reconcile_payout used, never the mutable legacy header) and FAILURE-
ISOLATED (any exception is logged and swallowed; the read/verify path a site
sits on must never 500 because of the emit).

Concurrency note: the emit-on-change guard is read-then-write with no lock.
Two concurrent reconciles can double-emit identical snapshots (harmless —
last-write-wins replays to the same state). A stale interleave is also
possible (A reads pre-match state, B persists a match and emits, A then emits
its stale snapshot with a later sequence): the event tail and any exception it
opens are wrong until the NEXT reconcile of that payout, which re-reads the
matched state, emits a fresh snapshot, and auto-resolves — self-healing, and
the window only exists at a payout's one-time state transition, so no lock is
taken. Similarly, event-frozen variances can disagree with reconcile's live
header-vs-cache numbers under legacy re-sync drift; the parity report
surfaces persistent divergence, and both exception producers converge on the
next state change.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections import deque
from decimal import Decimal

from django.utils import timezone

from events.emitter import emit_event_no_actor
from events.models import BusinessEvent
from events.types import EventTypes

from .models import StripePayoutTransaction

logger = logging.getLogger(__name__)

PROVIDER = "stripe"

SOURCE_AUTO = "auto_reconcile"
SOURCE_MANUAL = "manual_verify"
SOURCE_BACKFILL = "backfill"

# Fields excluded from the emit-on-change comparison: they change on every run
# without representing a state change.
_VOLATILE_FIELDS = {"reconciled_at", "source", "triggered_by_user_id", "triggered_by_email"}

# Line types reconcile_payout treats as matched without a counterpart lookup
# (reconciliation.py: adjustment/payout/other → matched in-memory, verified
# persisted only by the verify endpoint — and only for adjustment/payout).
_AUTO_MATCHED_TYPES = (
    StripePayoutTransaction.TransactionType.ADJUSTMENT,
    StripePayoutTransaction.TransactionType.PAYOUT,
    StripePayoutTransaction.TransactionType.OTHER,
)


def _dec(value) -> Decimal:
    return Decimal(str(value or "0"))


def _aggregate_id(payout_batch_id: str) -> str:
    """`stripe:{payout_id}` (mirrors the settlement event's aggregate), with a
    hash fallback for the emitter's 64-char column guard — Stripe po_ ids are
    ~30 chars, the fallback is for future providers with long CSV batch ids."""
    raw = f"{PROVIDER}:{payout_batch_id}"
    if len(raw) <= 64:
        return raw
    digest = hashlib.sha256(payout_batch_id.encode("utf-8")).hexdigest()[:48]
    return f"{PROVIDER}:{digest}"


def build_reconciled_snapshot(company, payout) -> dict | None:
    """Full-state snapshot payload for one legacy StripePayout, or None when the
    payout has no settlement event (pre-PR-A history / seeded demo rows: no
    canonical lines exist, so there is nothing a projection could stamp).

    Correlation: the settlement event's line_items[] are FROZEN (deterministic
    idempotency key → first emit wins), so line_index is replay-stable. Each
    event line claims the first unclaimed legacy txn where
    (source_id or stripe_balance_txn_id) == order_id — exact because
    normalize.py builds order_id as (bt.source or bt.id) and sync.py stores
    both halves, and both sides exclude the type=="payout" txn. Legacy txns
    that map to no event line (cache re-synced after the event froze) are
    logged and omitted; their verified state stays legacy-only and surfaces in
    the parity report, never silently.
    """
    settlement = BusinessEvent.objects.filter(
        company=company,
        idempotency_key=f"payment.settlement.received:stripe:{payout.stripe_payout_id}",
    ).first()
    if settlement is None:
        logger.debug(
            "No settlement event for stripe payout %s (company %s) — skipping reconciled snapshot",
            payout.stripe_payout_id,
            company.id,
        )
        return None

    data = settlement.get_data()
    line_items = data.get("line_items") or []

    # Stable claim order for the legacy side (queryset order is otherwise
    # undefined); the event side drives, so duplicates of the same order_id
    # (rare: e.g. two same-source adjustments) assign deterministically.
    # Indexed by claim key so the per-line claim is O(1) — this builder runs
    # on every payout-detail GET and scan pass.
    txns = list(
        StripePayoutTransaction.objects.filter(payout=payout)
        .select_related("local_charge")
        .order_by("stripe_balance_txn_id")
    )
    unclaimed_by_key: dict[str, deque] = {}
    for txn in txns:
        unclaimed_by_key.setdefault(txn.source_id or txn.stripe_balance_txn_id, deque()).append(txn)

    verdicts = []
    matched_count = 0
    verified_count = 0
    gross_sum = Decimal("0")
    fee_sum = Decimal("0")
    net_sum = Decimal("0")

    for index, line in enumerate(line_items):
        order_id = str(line.get("order_id") or "")
        gross_sum += _dec(line.get("gross"))
        fee_sum += _dec(line.get("fee"))
        net_sum += _dec(line.get("net"))

        queue = unclaimed_by_key.get(order_id)
        twin = queue.popleft() if queue else None

        verdict = {
            "line_index": index,
            "verified": False,
            "match_kind": "none",
            "matched_ref": "",
            "matched_ref_type": "",
            "provider_line_ref": "",
        }
        if twin is not None:
            verdict["provider_line_ref"] = twin.stripe_balance_txn_id
            verdict["verified"] = bool(twin.verified)
            if twin.verified and twin.local_charge is not None:
                verdict["match_kind"] = "charge"
                verdict["matched_ref"] = twin.local_charge.stripe_charge_id
                verdict["matched_ref_type"] = "charge"
            elif twin.verified and twin.transaction_type == StripePayoutTransaction.TransactionType.REFUND:
                # Refund matches persist verified without an FK (reconcile_payout
                # matches on source_id == StripeRefund.stripe_refund_id).
                verdict["match_kind"] = "refund"
                verdict["matched_ref"] = twin.source_id
                verdict["matched_ref_type"] = "refund"
            elif twin.transaction_type in _AUTO_MATCHED_TYPES:
                verdict["match_kind"] = "auto_type"

        if verdict["match_kind"] != "none":
            matched_count += 1
        if verdict["verified"]:
            verified_count += 1
        verdicts.append(verdict)

    unclaimed = [t for q in unclaimed_by_key.values() for t in q]
    if unclaimed:
        logger.warning(
            "stripe payout %s: %d legacy line(s) not in the frozen settlement line_items "
            "(cache drift) — omitted from the reconciled snapshot: %s",
            payout.stripe_payout_id,
            len(unclaimed),
            ", ".join(t.stripe_balance_txn_id for t in unclaimed[:10]),
        )

    # Event-frozen variances: header totals vs line sums of the SAME event.
    # Identical to reconcile_payout's numbers whenever the legacy cache/header
    # mirror the event (the normal case, C2 parity-proven); under drift the
    # event stays self-consistent and the parity gate surfaces the divergence.
    gross_variance = _dec(data.get("gross_amount")) - gross_sum
    fee_variance = _dec(data.get("fees")) - fee_sum
    net_variance = _dec(data.get("net_amount")) - net_sum

    unmatched_count = len(verdicts) - matched_count
    has_discrepancy = bool(gross_variance or fee_variance or net_variance or unmatched_count)

    currency = str(data.get("currency") or "").upper()
    if not (len(currency) == 3 and currency.isalpha()):
        currency = None

    return {
        "provider": PROVIDER,
        "payout_batch_id": payout.stripe_payout_id,
        "reconciled_at": "",  # stamped by maybe_emit_payout_reconciled
        "source": "",
        "triggered_by_user_id": None,
        "triggered_by_email": "",
        "outcome": "discrepancy" if has_discrepancy else "verified",
        "matched_count": matched_count,
        "unmatched_count": unmatched_count,
        "total_count": len(verdicts),
        "verified_count": verified_count,
        "gross_variance": str(gross_variance),
        "fee_variance": str(fee_variance),
        "net_variance": str(net_variance),
        "currency": currency,
        "line_verdicts": verdicts,
        "provider_metadata": {},
    }


def _comparable(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k not in _VOLATILE_FIELDS}


def _last_reconciled_event(company, payout_batch_id):
    return (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.PROVIDER_PAYOUT_RECONCILED,
            aggregate_type="ProviderPayout",
            aggregate_id=_aggregate_id(payout_batch_id),
        )
        .order_by("-company_sequence")
        .first()
    )


def pending_snapshot(company, payout) -> tuple[dict | None, bool]:
    """(snapshot, changed): the fresh snapshot (None when the payout has no
    settlement event) and whether emitting it would record a state change.
    Read-only — the seam the backfill command's report mode uses."""
    snapshot = build_reconciled_snapshot(company, payout)
    if snapshot is None:
        return None, False
    last = _last_reconciled_event(company, payout.stripe_payout_id)
    changed = last is None or _comparable(last.get_data()) != _comparable(snapshot)
    return snapshot, changed


def maybe_emit_payout_reconciled(company, payout, *, source: str, actor=None):
    """Emit a PROVIDER_PAYOUT_RECONCILED snapshot if state changed since the
    last one; returns the BusinessEvent or None (unchanged / no settlement
    event / emit failure).

    Never raises: the emit is an additive side channel on read/verify paths
    whose legacy behavior must stay byte-identical — failures are logged.
    """
    try:
        snapshot, changed = pending_snapshot(company, payout)
        if snapshot is None or not changed:
            return None

        snapshot["reconciled_at"] = timezone.now().isoformat()
        snapshot["source"] = source
        if actor is not None:
            snapshot["triggered_by_user_id"] = actor.user.id
            snapshot["triggered_by_email"] = actor.user.email or ""

        event = emit_event_no_actor(
            company=company,
            event_type=EventTypes.PROVIDER_PAYOUT_RECONCILED,
            aggregate_type="ProviderPayout",
            aggregate_id=_aggregate_id(payout.stripe_payout_id),
            data=snapshot,
            user=actor.user if actor is not None else None,
            # uuid4, NOT deterministic-per-payout (would lock the first outcome
            # forever) and NOT content-hashed (A→B→A oscillation would dedupe
            # the third emit onto the first event; replay would land on B).
            idempotency_key=f"provider_payout.reconciled:{uuid.uuid4()}",
            metadata={"source": source},
        )
    except Exception:
        logger.exception(
            "PROVIDER_PAYOUT_RECONCILED emit failed for stripe payout %s (company %s) — "
            "legacy read/verify path unaffected",
            payout.stripe_payout_id,
            company.id,
        )
        return None

    # Separate failure domain: a queue hiccup must not masquerade as an emit
    # failure (the event IS persisted — returning None would make the backfill
    # under-count, and the emit-on-change guard would never retry the feed).
    # The backfill source is EXCLUDED: seeding months of event history must not
    # flood the live operator queue with (or re-open triaged) stale
    # discrepancies — the 30-day scan stays the bounded producer for history;
    # this feed covers live state changes only.
    if source != SOURCE_BACKFILL:
        try:
            _feed_exception_queue(company, payout, snapshot)
        except Exception:
            logger.exception(
                "exception-queue feed failed for stripe payout %s (company %s) — "
                "event emitted; the queue catches up on the next scan or state change",
                payout.stripe_payout_id,
                company.id,
            )
    return event


def _feed_exception_queue(company, payout, snapshot: dict) -> None:
    """Route the snapshot's outcome into the ReconciliationException queue.

    Lazy import: bank_connector.exceptions imports stripe_connector.reconciliation
    (lazily) — a top-level import here would create a cycle.
    """
    from bank_connector.exceptions import sync_payout_variance_exception

    sync_payout_variance_exception(
        company,
        platform=PROVIDER,
        payout_pk=payout.pk,
        payout_batch_id=payout.stripe_payout_id,
        payout_date=payout.payout_date,
        currency=payout.currency,
        snapshot=snapshot,
    )
