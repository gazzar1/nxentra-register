# reconciliation/matching.py
"""Reconciliation matching helpers — pure planners + heuristic scoring.

A86.8 (2026-05-26): moved here from `accounting/bank_reconciliation.py`.

Contents (post-move):

- Confidence threshold constants (`CONFIDENCE_EXACT`,
  `CONFIDENCE_AMOUNT_DATE`, `CONFIDENCE_AMOUNT_ONLY`,
  `AUTO_MATCH_THRESHOLD`).
- `_difference_tolerance` — A16/A35 near-match tolerance band.
- `_compute_match_confidence` — generic-GL match scorer.
- `_plan_settlement_prepass_matches` — pure-read settlement planner.

This module is ADVISORY ONLY. Nothing in here is allowed to mutate
canonical match state, post JEs, or emit events. The output is a plan
(suggestion) that a command in `reconciliation/commands.py` turns
into a ReconciliationMatchProposed or ReconciliationMatchConfirmed
event.

Future AI-agent suggestions live here too — they emit MatchProposed,
they never confirm.

See:
- reconciliation/commands.py — the command surface that consumes these plans
- docs/finance_event_first_policy.md §1 — advisory-vs-canonical contract
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from accounting.models import BankStatement, BankStatementLine, JournalEntry, JournalLine

# Match confidence thresholds
CONFIDENCE_EXACT = Decimal("100")
CONFIDENCE_AMOUNT_DATE = Decimal("85")
CONFIDENCE_AMOUNT_ONLY = Decimal("60")
AUTO_MATCH_THRESHOLD = Decimal("80")


def _difference_tolerance(expected: Decimal) -> Decimal:
    """A16/A35: near-match tolerance for bank deposits vs expected EBD lines.

    15% of the expected amount, capped at 10,000 currency units (EGP, USD…).
    Below this gap we still match — the bank line lands as
    MATCHED_WITH_DIFFERENCE and the operator categorizes via the A16
    Resolve flow, which posts the adjustment JE that drains the EBD
    residual. Above the cap we leave both lines unmatched (likely a wrong
    pairing rather than a real near-match).

    A35 widened the original 2% / 500 tolerance to 15% / 10000 because
    the 2% threshold left real-merchant short-payments (5-15% gap is
    common for Egyptian COD couriers) unmatched, requiring manual
    intervention via the A25 picker. With 15%, the BNK-003-style
    scenario (200 EGP short on a 2,050 EGP deposit = 9.76% gap) now
    auto-flags as MATCHED_WITH_DIFFERENCE and surfaces in the Needs
    Review queue. A merchant who wants stricter behavior can resolve
    each entry manually; A45 (deferred) adds a per-merchant
    configurable threshold.
    """
    pct = (abs(expected) * Decimal("0.15")).quantize(Decimal("0.01"))
    return min(pct, Decimal("10000"))


def _compute_match_confidence(
    bank_line: BankStatementLine,
    journal_line: JournalLine,
) -> Decimal:
    """
    Compute a confidence score for a potential match.

    Factors:
    - Amount match (required — already filtered by caller)
    - Date proximity: same day = +15, within 3 days = +10, within 5 = +5
    - Reference/description overlap: keyword match = +10
    """
    confidence = Decimal("50")  # Base: amounts match

    # Date proximity
    jl_date = journal_line.entry.date
    days_diff = abs((bank_line.line_date - jl_date).days)

    if days_diff == 0:
        confidence += Decimal("30")
    elif days_diff <= 3:
        confidence += Decimal("20")
    elif days_diff <= 5:
        confidence += Decimal("10")

    # Reference matching
    bank_ref = (bank_line.reference + " " + bank_line.description).lower()
    jl_ref = (journal_line.description or "").lower()
    entry_memo = (journal_line.entry.memo or "").lower()

    if bank_ref and jl_ref:
        # Check for keyword overlap
        bank_words = set(bank_ref.split())
        jl_words = set(jl_ref.split()) | set(entry_memo.split())
        overlap = bank_words & jl_words - {"the", "a", "an", "to", "from", "for"}

        if len(overlap) >= 2:
            confidence += Decimal("20")
        elif len(overlap) >= 1:
            confidence += Decimal("10")

    return min(confidence, CONFIDENCE_EXACT)


def _plan_settlement_prepass_matches(
    company,
    statement: BankStatement,
    unmatched_bank_lines: list,
) -> list[dict]:
    """A85 chunk 2c (2026-05-26): pure-read planner for the settlement
    pre-pass. Decides which bank lines would match which settlement JEs
    and what clearance JEs would need to be created. Does NOT create
    JEs or mutate read-model state.

    The execute path (_settlement_prepass_match in
    reconciliation/commands.py) calls this and then creates the
    clearance JE + applies the read-model state per plan row.
    The preview path (preview_auto_match) calls this and returns the
    plan as-is for the operator to confirm.

    Plan ordering mirrors the original loop: bank lines processed in the
    order they were passed; candidates removed as they're picked so the
    next bank line can't double-match.

    Returns a list of plan dicts:
        {
            "bank_line_id": int,
            "bank_line_amount": Decimal,
            "bank_line_date": date,
            "bank_line_description": str,
            "settlement_entry_id": int,
            "settlement_entry_number": str,
            "settlement_entry_date": date,
            "settlement_entry_period": int | None,
            "settlement_source_document": str,
            "ebd_line_id": int,
            "batch_id": str,
            "expected_amount": Decimal,
            "actual_amount": Decimal,
            "difference": Decimal,
            "is_near": bool,
            "confidence": Decimal,
            "value_date": date,
        }
    """
    from accounting.mappings import ModuleAccountMapping

    if not unmatched_bank_lines:
        return []

    date_buffer = timedelta(days=7)
    settlement_entries = list(
        JournalEntry.objects.filter(
            company=company,
            source_module="payment_settlement",
            status=JournalEntry.Status.POSTED,
            date__gte=statement.period_start - date_buffer,
            date__lte=statement.period_end + date_buffer,
        ).order_by("date")
    )
    if not settlement_entries:
        return []

    ebd_account = ModuleAccountMapping.get_account(company, "shopify_connector", "EXPECTED_BANK_DEPOSIT")
    if not ebd_account:
        return []

    # A129a/P2-P3: batch-scoped idempotency. A settlement whose batch has
    # already been deposited+cleared (a non-reversed clearance JE exists for
    # its full `{provider}:{batch}` source_document) must NOT be offered as a
    # candidate again — even if its EBD line's `reconciled` flag was reset
    # (e.g. by a statement delete/reimport). The clearance JE's source_document
    # is the deterministic, provider-scoped key, and since P4 it lives in the
    # event payload so this guard is replay-safe (unlike the resettable flag).
    # We do this in the shared planner so preview and execute stay in lockstep.
    cleared_source_docs = set(
        JournalEntry.objects.filter(
            company=company,
            source_module="payment_settlement_clearance",
            status=JournalEntry.Status.POSTED,
        ).values_list("source_document", flat=True)
    )

    # Pre-collect candidates: (entry, ebd_line, net, batch_id)
    candidates: list[tuple] = []
    for entry in settlement_entries:
        ebd_line = entry.lines.filter(account=ebd_account, reconciled=False).first()
        if not ebd_line:
            continue
        source_doc = entry.source_document or ""
        # Provider-scoped idempotency: a non-reversed clearance for this exact
        # `{provider}:{batch}` means this batch is already cleared. Distinct
        # providers with the same batch id (e.g. paymob:123 vs bosta:123) have
        # distinct source_documents, so they never falsely collide here.
        if source_doc and source_doc in cleared_source_docs:
            continue
        batch_id = source_doc.split(":", 1)[1] if ":" in source_doc else source_doc
        candidates.append((entry, ebd_line, ebd_line.debit, batch_id))

    if not candidates:
        return []

    plans: list[dict] = []

    for bank_line in unmatched_bank_lines:
        if bank_line.match_status != BankStatementLine.MatchStatus.UNMATCHED:
            continue

        # A16 near-match logic: exact first, then within-tolerance.
        exact_matches = [c for c in candidates if c[2] == bank_line.amount]
        near_matches = []
        if not exact_matches:
            for c in candidates:
                tolerance = _difference_tolerance(c[2])
                gap = abs(c[2] - bank_line.amount)
                if gap > 0 and gap <= tolerance:
                    near_matches.append(c)

        amount_matches = exact_matches or near_matches
        if not amount_matches:
            continue
        is_near = not exact_matches

        descr = (bank_line.description or "").lower()
        batch_match = next(
            (c for c in amount_matches if c[3] and c[3].lower() in descr),
            None,
        )
        if batch_match:
            entry, ebd_line, expected_amount, batch_id = batch_match
            confidence = CONFIDENCE_EXACT
        else:
            best, best_days = None, 999
            for c in amount_matches:
                days = abs((bank_line.line_date - c[0].date).days)
                if days < best_days:
                    best_days = days
                    best = c
            if not best or best_days > 7:
                continue
            entry, ebd_line, expected_amount, batch_id = best
            confidence = CONFIDENCE_AMOUNT_DATE if best_days <= 2 else CONFIDENCE_AMOUNT_ONLY

        if confidence < AUTO_MATCH_THRESHOLD:
            continue

        difference = (expected_amount - bank_line.amount) if is_near else Decimal("0")
        plans.append(
            {
                "bank_line_id": bank_line.id,
                "bank_line_amount": bank_line.amount,
                "bank_line_date": bank_line.line_date,
                "bank_line_description": bank_line.description or "",
                "settlement_entry_id": entry.id,
                "settlement_entry_number": entry.entry_number or "",
                "settlement_entry_date": entry.date,
                "settlement_entry_period": entry.period,
                "settlement_source_document": entry.source_document or "",
                "ebd_line_id": ebd_line.id,
                "batch_id": batch_id,
                "expected_amount": expected_amount,
                "actual_amount": bank_line.amount,
                "difference": difference,
                "is_near": is_near,
                "confidence": confidence,
                "value_date": bank_line.line_date,
            }
        )

        # Remove this candidate so the next bank line can't double-match.
        candidates = [c for c in candidates if c[0].id != entry.id]

    return plans
