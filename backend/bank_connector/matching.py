# bank_connector/matching.py
"""
Payout matching engine.

Matches imported bank transactions to platform payouts (Stripe/Shopify)
using amount + date proximity + reference matching with confidence scores.

Hero feature: "Payout Explainer" — breaks down a payout into its
component charges, refunds, fees, and adjustments.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import BankTransaction

# Lazy imports for accounting models (avoid circular imports)
# These are imported inside functions that need them.

logger = logging.getLogger(__name__)

# Confidence thresholds
CONFIDENCE_EXACT = 100  # Amount + date + reference all match
CONFIDENCE_HIGH = 90  # Amount + date match (within 1 day)
CONFIDENCE_MEDIUM = 75  # Amount + date proximity (within 5 days)
CONFIDENCE_LOW = 55  # Amount match only
AUTO_MATCH_THRESHOLD = 75  # Minimum confidence for auto-match


# =============================================================================
# Payout Discovery
# =============================================================================


def _get_stripe_payouts(company, date_from=None, date_to=None):
    """Get Stripe payouts for matching."""
    try:
        from stripe_connector.models import StripePayout
        from stripe_connector.payout_reads import canonical_payout_reads_enabled
    except ImportError:
        return []

    if canonical_payout_reads_enabled():
        return _get_stripe_payouts_canonical(company, date_from, date_to)

    qs = StripePayout.objects.filter(company=company)
    if date_from:
        qs = qs.filter(payout_date__gte=date_from - timedelta(days=5))
    if date_to:
        qs = qs.filter(payout_date__lte=date_to + timedelta(days=5))
    return [
        {
            "id": p.id,
            "platform": "stripe",
            "payout_id": p.stripe_payout_id,
            "gross_amount": p.gross_amount,
            "fees": p.fees,
            "net_amount": p.net_amount,
            "currency": p.currency,
            "payout_date": p.payout_date,
            "status": p.stripe_status,
            "journal_entry_id": str(p.journal_entry_id) if p.journal_entry_id else None,
        }
        for p in qs
    ]


def _get_stripe_payouts_canonical(company, date_from=None, date_to=None):
    """C3: header money from the canonical ProviderPayout read-model.

    The dict ``id`` (the legacy int pk, persisted in
    BankTransaction.matched_object_id and round-tripped by the match/explain
    endpoints) and the ``journal_entry_id`` write-back stamp have no canonical
    home yet, so both are joined from the legacy row. A canonical header
    without a legacy twin — or without a payout date — cannot participate in
    bank matching yet; it is skipped loudly, never emitted mis-shaped.
    """
    from stripe_connector.models import StripePayout
    from stripe_connector.payout_reads import canonical_headers

    qs = canonical_headers(company).order_by("-payout_date")
    if date_from:
        qs = qs.filter(payout_date__gte=date_from - timedelta(days=5))
    if date_to:
        qs = qs.filter(payout_date__lte=date_to + timedelta(days=5))

    headers = list(qs)
    legacy = {
        p.stripe_payout_id: p
        for p in StripePayout.objects.filter(
            company=company,
            stripe_payout_id__in=[h.payout_batch_id for h in headers],
        )
    }

    rows = []
    for h in headers:
        twin = legacy.get(h.payout_batch_id)
        if twin is None or h.payout_date is None:
            logger.warning(
                "C3: skipping canonical stripe payout %s for bank matching (%s)",
                h.payout_batch_id,
                "no legacy twin row" if twin is None else "no payout_date",
            )
            continue
        rows.append(
            {
                "id": twin.id,
                "platform": "stripe",
                "payout_id": h.payout_batch_id,
                "gross_amount": h.gross_amount,
                "fees": h.fees,
                "net_amount": h.net_amount,
                "currency": h.currency,
                "payout_date": h.payout_date,
                "status": h.provider_status,
                "journal_entry_id": str(twin.journal_entry_id) if twin.journal_entry_id else None,
            }
        )
    return rows


def _get_shopify_payouts(company, date_from=None, date_to=None):
    """Get Shopify payouts for matching."""
    try:
        from shopify_connector.models import ShopifyPayout
    except ImportError:
        return []

    qs = ShopifyPayout.objects.filter(company=company)
    if date_from:
        qs = qs.filter(payout_date__gte=date_from - timedelta(days=5))
    if date_to:
        qs = qs.filter(payout_date__lte=date_to + timedelta(days=5))
    return [
        {
            "id": p.id,
            "platform": "shopify",
            "payout_id": str(p.shopify_payout_id),
            "gross_amount": p.gross_amount,
            "fees": p.fees,
            "net_amount": p.net_amount,
            "currency": p.currency,
            "payout_date": p.payout_date,
            "status": p.shopify_status,
            "journal_entry_id": str(p.journal_entry_id) if p.journal_entry_id else None,
            # Shopify has fee breakdown
            "charges_gross": p.charges_gross,
            "refunds_gross": p.refunds_gross,
            "adjustments_gross": p.adjustments_gross,
            "charges_fee": p.charges_fee,
            "refunds_fee": p.refunds_fee,
            "adjustments_fee": p.adjustments_fee,
        }
        for p in qs
    ]


def get_all_payouts(company, date_from=None, date_to=None):
    """Get all platform payouts (Stripe + Shopify) for a company."""
    payouts = []
    payouts.extend(_get_stripe_payouts(company, date_from, date_to))
    payouts.extend(_get_shopify_payouts(company, date_from, date_to))
    return payouts


# =============================================================================
# Matching Algorithm
# =============================================================================


def _compute_confidence(bank_tx, payout):
    """
    Compute match confidence between a bank transaction and a platform payout.

    Factors:
    - Amount match (required): bank amount == payout net_amount
    - Date proximity: same day = +30, within 2 days = +20, within 5 = +10
    - Reference match: payout ID found in bank description/reference = +20
    """
    # Amount must match (with tolerance for rounding — 0.05)
    bank_amount = abs(bank_tx.amount)
    payout_amount = abs(payout["net_amount"])
    if abs(bank_amount - payout_amount) > Decimal("0.05"):
        return 0

    confidence = 50  # Base: amounts match

    # Date proximity
    days_diff = abs((bank_tx.transaction_date - payout["payout_date"]).days)
    if days_diff == 0:
        confidence += 30
    elif days_diff <= 2:
        confidence += 20
    elif days_diff <= 5:
        confidence += 10
    else:
        return 0  # Too far apart

    # Reference matching — check if payout ID appears in bank description/reference
    payout_id_str = str(payout["payout_id"]).lower()
    bank_text = (bank_tx.description + " " + bank_tx.reference).lower()
    if payout_id_str in bank_text:
        confidence += 20

    # Platform name in description (weaker signal)
    platform = payout["platform"].lower()
    if platform in bank_text or "stripe" in bank_text or "shopify" in bank_text:
        confidence += 5

    return min(confidence, CONFIDENCE_EXACT)


def get_match_suggestions(company, bank_transaction_id):
    """
    Get candidate payout matches for a specific bank transaction.

    Returns list of payouts with confidence scores, sorted by confidence desc.
    """
    try:
        tx = BankTransaction.objects.get(pk=bank_transaction_id, company=company)
    except BankTransaction.DoesNotExist:
        return []

    # Only match credit (deposit) transactions to payouts
    if tx.amount <= 0:
        return []

    # Get payouts in a reasonable date range
    payouts = get_all_payouts(
        company,
        date_from=tx.transaction_date - timedelta(days=10),
        date_to=tx.transaction_date + timedelta(days=10),
    )

    suggestions = []
    for payout in payouts:
        # Skip already-matched payouts
        if _is_payout_already_matched(company, payout):
            continue

        confidence = _compute_confidence(tx, payout)
        if confidence > 0:
            suggestions.append(
                {
                    **payout,
                    "confidence": confidence,
                    "payout_date": str(payout["payout_date"]),
                }
            )

    suggestions.sort(key=lambda x: x["confidence"], reverse=True)
    return suggestions


def _is_payout_already_matched(company, payout):
    """Check if a payout is already matched to a bank transaction."""
    content_type = f"{payout['platform']}_payout"
    return BankTransaction.objects.filter(
        company=company,
        matched_content_type=content_type,
        matched_object_id=payout["id"],
        status="MATCHED",
    ).exists()


def _emit_platform_payout_reconcile_event(
    *,
    company,
    cash_line,
    bank_tx,
    platform: str,
    payout_obj,
):
    """A86.6 (2026-05-26): emit ReconciliationMatchConfirmed for a
    bank-feed-driven platform-payout reconciliation.

    Distinct from accounting/bank_reconciliation.py's emission paths
    (A86.4 + A86.5): there's no BankStatementLine here — the bank side
    is a BankTransaction row from the bank_connector bank-feed. The
    journal_line IS the canonical reference; bank_line_public_id is
    left empty and the projection routes the event to a no-shadow-write
    handler (see ReconciliationProjection._handle_match_confirmed).

    Why bank_line_public_id is empty:
    BankTransaction.public_id could be used as a polymorphic stand-in,
    but the projection currently writes shadow fields on
    BankStatementLine, not BankTransaction. Leaving the field empty
    + tagging with confirmation_kind='platform_payout_reconcile'
    cleanly discriminates the two surfaces at the projection layer.
    A future A87 chunk may unify the bank-line abstraction if a real
    use case demands it.

    Per finance_event_first_policy.md §2: emission carries the standard
    idempotency_key + aggregate_type + aggregate_id. The legacy direct
    flip in _reconcile_payout_je remains until A86.7 cutover; this is
    pure additive shadow.
    """
    import uuid as _uuid

    from django.utils import timezone

    from events.emitter import emit_event_no_actor
    from events.types import EventTypes
    from reconciliation.event_types import ReconciliationMatchConfirmedData

    payload = ReconciliationMatchConfirmedData(
        bank_line_public_id="",  # No BankStatementLine; see docstring.
        journal_line_public_id=str(cash_line.public_id),
        match_kind="platform_payout",
        confidence="100",
        confirmation_kind="platform_payout_reconcile",
        confirmed_at=timezone.now().isoformat(),
        difference_amount="0",
        difference_reason="UNRESOLVED",
        statement_date=bank_tx.transaction_date.isoformat() if bank_tx.transaction_date else "",
    )
    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        aggregate_type="ReconciliationMatch",
        aggregate_id=f"bank_tx:{bank_tx.id}:{platform}:{payout_obj.pk}",
        idempotency_key=f"reconciliation.match_confirmed:{_uuid.uuid4()}",
        data=payload,
    )


# =============================================================================
# Journal Entry Reconciliation
# =============================================================================


def _reconcile_payout_je(company, platform, payout_obj, bank_tx):
    """
    When a bank transaction is matched to a payout:
    1. If the payout has a JE, mark the Cash/Bank line as reconciled
    2. If the payout has no JE, create one, then mark reconciled

    Returns dict with je_status info.
    """
    from accounting.models import JournalEntry

    je_public_id = payout_obj.journal_entry_id
    je = None

    # Step 1: Find existing JE or create one
    if je_public_id:
        try:
            je = JournalEntry.objects.get(
                public_id=je_public_id,
                status=JournalEntry.Status.POSTED,
            )
        except JournalEntry.DoesNotExist:
            logger.warning(
                "Payout %s has journal_entry_id %s but JE not found or not POSTED",
                payout_obj.pk,
                je_public_id,
            )

    if not je and platform == "stripe":
        # A158: the canonical Stripe pull emits PAYMENT_SETTLEMENT_RECEIVED
        # and PaymentSettlementProjection posts the settlement JE — but
        # nothing stamps StripePayout.journal_entry_id, so this matcher used
        # to see it empty and post a SECOND JE (clearing credited 2× gross,
        # fees double-expensed, bogus direct DR cash). Reuse the canonical
        # settlement JE when it exists. If the settlement EVENT exists but
        # its JE hasn't posted yet (celery lag / F27 quarantine), refuse to
        # create the duplicate and report pending. Event-less payouts
        # (webhook-era rows with no canonical settlement) fall through to
        # the legacy create below.
        from events.models import BusinessEvent
        from events.types import EventTypes

        batch_id = payout_obj.stripe_payout_id
        settlement_je = JournalEntry.objects.filter(
            company=company,
            source_module="payment_settlement",
            source_document=f"stripe:{batch_id}",
            status=JournalEntry.Status.POSTED,
        ).first()
        if settlement_je:
            je = settlement_je
            # Stamp the read-model link so the payouts UI join shows the JE
            # and the step-1 lookup short-circuits next time. Same
            # established dual-write as the legacy stamp in
            # _create_payout_je (pending the A3 reactor extraction).
            payout_obj.journal_entry_id = settlement_je.public_id
            payout_obj.save(update_fields=["journal_entry_id"])
            logger.info(
                "A158: reusing canonical settlement JE %s for stripe payout %s",
                settlement_je.entry_number,
                batch_id,
            )
        elif BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
            idempotency_key=f"payment.settlement.received:stripe:{batch_id}",
        ).exists():
            logger.warning(
                "A158: stripe payout %s has a canonical settlement event but no "
                "POSTED settlement JE yet (projection lag or quarantined in "
                "/finance/exceptions) — refusing to post a duplicate legacy JE",
                batch_id,
            )
            return {"je_status": "canonical_settlement_pending", "je_id": None, "reconciled": False}

    if not je:
        # A100 (2026-05-26): _create_payout_je calls platform_connectors.je_builder
        # which uses JournalEntry.objects.projection().create() — that path
        # requires projection_writes_allowed(). Scope the context narrowly
        # here (where the projection-chain write actually happens) instead of
        # at the view layer, so views don't grant projection-write privileges
        # to the entire request. The eventual reactor extraction (A3) replaces
        # this in-line projection-chain write with a proper event-driven post,
        # at which point this context manager goes away.
        from projections.write_barrier import projection_writes_allowed

        with projection_writes_allowed():
            je = _create_payout_je(company, platform, payout_obj)
        if not je:
            logger.warning(
                "Could not create JE for %s payout %s — account mapping may be missing",
                platform,
                payout_obj.pk,
            )
            return {"je_status": "no_je", "je_id": None, "reconciled": False}

    # Step 2: Find the Cash/Bank line in the JE and mark reconciled
    # For positive payouts: Cash/Bank is the debit line
    # For negative payouts: Cash/Bank is the credit line
    # Use the LIQUIDITY role to identify the bank account line
    all_lines = list(je.lines.select_related("account").all())
    logger.info(
        "JE %s has %d lines: %s",
        je.entry_number,
        len(all_lines),
        [(l.line_no, l.account.code, l.account.role, l.reconciled) for l in all_lines],
    )

    is_settlement_je = je.source_module == "payment_settlement"

    cash_line = None
    for l in all_lines:
        if l.account.role == "LIQUIDITY" and not l.reconciled:
            # A158: on a canonical settlement JE, BOTH the EBD debit and the
            # clearing credit carry role LIQUIDITY. Only the debit (cash-in)
            # side may be flagged by a bank match — without this, a payout
            # whose EBD line the canonical engine already cleared would get
            # its clearing CREDIT line falsely reconciled.
            if is_settlement_je and not l.debit > 0:
                continue
            cash_line = l
            break

    if not cash_line and is_settlement_je:
        # The canonical engine already reconciled the EBD line — nothing
        # left for the legacy matcher to flag. Never fall through to the
        # generic debit>0 fallback (it would grab the fees expense line).
        logger.info(
            "A158: settlement JE %s already reconciled by the canonical engine — no action",
            je.entry_number,
        )
        return {
            "je_status": "already_reconciled",
            "je_id": str(je.public_id),
            "reconciled": False,
        }

    if not cash_line:
        # Fallback: find by debit > 0 (most payouts are positive)
        for l in all_lines:
            if l.debit > 0 and not l.reconciled:
                cash_line = l
                break

    if cash_line:
        logger.info(
            "Marking line %d (account %s) as reconciled for bank tx %s",
            cash_line.line_no,
            cash_line.account.code,
            bank_tx.id,
        )
        # A86.7b (2026-05-26): JL.reconciled flip is owned by the
        # ReconciliationProjection. _emit_platform_payout_reconcile_event
        # below produces a ReconciliationMatchConfirmed with
        # confirmation_kind="platform_payout_reconcile"; the projection
        # consumes it and flips reconciled. No direct read-model write
        # from this non-projection module — closes the Codex-flagged
        # protocol violation.
        _emit_platform_payout_reconcile_event(
            company=company,
            cash_line=cash_line,
            bank_tx=bank_tx,
            platform=platform,
            payout_obj=payout_obj,
        )

        # A86.7b: run the projection synchronously so the JL.reconciled
        # flip lands before this function returns — callers (auto_match_
        # transactions, manual_match) report "reconciled: True" based on
        # observable state, and the bank-feed UI poll right after this
        # call sees the updated JL.
        from reconciliation.projections import ReconciliationProjection

        ReconciliationProjection().process_pending(company)

        logger.info(
            "Reconciled JE %s line %s for %s payout %s ↔ bank tx %s",
            je.entry_number,
            cash_line.line_no,
            platform,
            payout_obj.pk,
            bank_tx.id,
        )
        return {
            "je_status": "reconciled",
            "je_id": str(je.public_id),
            "je_number": je.entry_number,
            "reconciled": True,
        }

    logger.warning(
        "JE %s has no unreconciled Cash/Bank line for payout %s",
        je.entry_number,
        payout_obj.pk,
    )
    return {
        "je_status": "no_cash_line",
        "je_id": str(je.public_id),
        "reconciled": False,
    }


def _create_payout_je(company, platform, payout_obj):
    """
    Create a journal entry for a payout that doesn't have one.

    Uses the same account mapping and structure as PlatformAccountingProjection:
      DR Cash/Bank        (net_amount)
      DR Processing Fees  (fees)
        CR Platform Clearing  (gross_amount)
    """
    from accounting.mappings import ModuleAccountMapping, module_key_for_provider
    from platform_connectors.je_builder import JELine, JERequest, build_journal_entry

    # Canonical per-provider mapping key (ADR-0002): shopify -> shopify_connector,
    # stripe -> platform_stripe. The old f"{platform}_connector" produced
    # "stripe_connector", which the projection never seeds -> Stripe payout JEs
    # silently found no mapping.
    module_key = module_key_for_provider(platform)
    mapping = ModuleAccountMapping.get_mapping(company, module_key)
    if not mapping:
        logger.warning("No account mapping found for module %s", module_key)
        return None

    # Clearing account role varies by platform: SHOPIFY_CLEARING, STRIPE_CLEARING
    clearing_role = f"{platform.upper()}_CLEARING"
    clearing = mapping.get(clearing_role)
    cash_bank = mapping.get("CASH_BANK")
    fees_account = mapping.get("PAYMENT_PROCESSING_FEES")

    if not clearing or not cash_bank:
        logger.warning(
            "Missing account mapping for %s: CLEARING=%s, CASH_BANK=%s",
            module_key,
            clearing,
            cash_bank,
        )
        return None

    net_amount = payout_obj.net_amount
    fees = payout_obj.fees
    gross_amount = payout_obj.gross_amount

    # Build payout ID for memo
    if platform == "stripe":
        payout_id_str = payout_obj.stripe_payout_id
    elif platform == "shopify":
        payout_id_str = str(payout_obj.shopify_payout_id)
    else:
        payout_id_str = str(payout_obj.pk)

    memo = f"{platform.title()} payout: {payout_id_str}"

    lines = []
    if net_amount >= 0:
        # Normal payout: DR Cash, DR Fees, CR Clearing
        lines.append(
            JELine(
                account=cash_bank,
                description=f"Payout deposit: {payout_id_str}",
                debit=net_amount,
            )
        )
        if fees > 0 and fees_account:
            lines.append(
                JELine(
                    account=fees_account,
                    description=f"Processing fees: {payout_id_str}",
                    debit=fees,
                )
            )
        lines.append(
            JELine(
                account=clearing,
                description=f"Payout settlement: {payout_id_str}",
                credit=gross_amount,
            )
        )
    else:
        # Negative payout: DR Clearing, CR Cash
        lines.append(
            JELine(
                account=clearing,
                description=f"Negative payout: {payout_id_str}",
                debit=abs(gross_amount),
            )
        )
        if fees > 0 and fees_account:
            lines.append(
                JELine(
                    account=fees_account,
                    description=f"Processing fees: {payout_id_str}",
                    debit=fees,
                )
            )
        lines.append(
            JELine(
                account=cash_bank,
                description=f"Payout withdrawal: {payout_id_str}",
                credit=abs(net_amount),
            )
        )

    je = build_journal_entry(
        JERequest(
            company=company,
            entry_date=payout_obj.payout_date,
            memo=memo,
            source_module=f"{platform}_connector",
            source_document=payout_id_str,
            currency=payout_obj.currency,
            lines=lines,
            projection_name="bank_reconciliation",
            posted_by_email="system@reconciliation",
        )
    )

    if je:
        # Store JE reference on the payout
        payout_obj.journal_entry_id = je.public_id
        payout_obj.save(update_fields=["journal_entry_id"])
        logger.info(
            "Created JE %s for %s payout %s",
            je.entry_number,
            platform,
            payout_id_str,
        )

    return je


# =============================================================================
# Auto-Matching
# =============================================================================


def auto_match_transactions(company, bank_account_id=None):
    """
    Auto-match unmatched bank deposits to platform payouts.

    Returns dict with match results.
    """
    qs = BankTransaction.objects.filter(
        company=company,
        status="UNMATCHED",
        amount__gt=0,  # Only match deposits
    )
    if bank_account_id:
        qs = qs.filter(bank_account_id=bank_account_id)

    unmatched = list(qs)
    if not unmatched:
        return {"matched": 0, "total": 0, "matches": []}

    # Get date range from unmatched transactions
    dates = [tx.transaction_date for tx in unmatched]
    date_from = min(dates)
    date_to = max(dates)

    # Get all payouts in range
    payouts = get_all_payouts(company, date_from, date_to)

    matched_count = 0
    matches = []

    for tx in unmatched:
        best_payout = None
        best_confidence = 0

        for payout in payouts:
            if _is_payout_already_matched(company, payout):
                continue

            confidence = _compute_confidence(tx, payout)
            if confidence > best_confidence:
                best_confidence = confidence
                best_payout = payout

        if best_payout and best_confidence >= AUTO_MATCH_THRESHOLD:
            content_type = f"{best_payout['platform']}_payout"
            tx.status = "MATCHED"
            tx.matched_content_type = content_type
            tx.matched_object_id = best_payout["id"]
            tx.matched_at = timezone.now()
            tx.matched_by = "auto"
            tx.save()

            # Reconcile the payout's journal entry
            payout_obj = _get_payout_object(company, best_payout["platform"], best_payout["id"])
            je_result = {}
            if payout_obj:
                je_result = _reconcile_payout_je(company, best_payout["platform"], payout_obj, tx)

            matches.append(
                {
                    "bank_transaction_id": tx.id,
                    "payout_platform": best_payout["platform"],
                    "payout_id": best_payout["payout_id"],
                    "confidence": best_confidence,
                    "amount": str(tx.amount),
                    "je_reconciled": je_result.get("reconciled", False),
                }
            )

            # Remove from available payouts to prevent double-matching
            payouts.remove(best_payout)
            matched_count += 1

    logger.info(
        "Auto-matched %d/%d bank transactions for company %s",
        matched_count,
        len(unmatched),
        company.id,
    )

    # Auto-resolve any exceptions for newly matched items
    if matched_count > 0:
        try:
            from .exceptions import auto_resolve_matched

            auto_resolve_matched(company)
        except Exception:
            logger.exception("Failed to auto-resolve exceptions after matching")

    return {
        "matched": matched_count,
        "total": len(unmatched),
        "matches": matches,
    }


def manual_match(company, bank_transaction_id, platform, payout_id):
    """Manually match a bank transaction to a payout."""
    try:
        tx = BankTransaction.objects.get(pk=bank_transaction_id, company=company)
    except BankTransaction.DoesNotExist:
        return {"error": "Bank transaction not found."}

    if tx.status == "MATCHED":
        return {"error": "Transaction is already matched."}

    content_type = f"{platform}_payout"

    # Verify payout exists
    payout_obj = _get_payout_object(company, platform, payout_id)
    if not payout_obj:
        return {"error": "Payout not found."}

    tx.status = "MATCHED"
    tx.matched_content_type = content_type
    tx.matched_object_id = payout_id
    tx.matched_at = timezone.now()
    tx.matched_by = "manual"
    tx.save()

    # Reconcile the payout's journal entry
    je_result = _reconcile_payout_je(company, platform, payout_obj, tx)

    # Auto-resolve any exceptions for this match
    try:
        from .exceptions import auto_resolve_matched

        auto_resolve_matched(company)
    except Exception:
        logger.exception("Failed to auto-resolve exceptions after manual match")

    return {
        "status": "matched",
        "bank_transaction_id": tx.id,
        **je_result,
    }


# =============================================================================
# Payout Explainer
# =============================================================================


def _get_payout_object(company, platform, payout_id):
    """Get the actual payout model instance."""
    if platform == "stripe":
        try:
            from stripe_connector.models import StripePayout

            return StripePayout.objects.get(pk=payout_id, company=company)
        except Exception:
            return None
    elif platform == "shopify":
        try:
            from shopify_connector.models import ShopifyPayout

            return ShopifyPayout.objects.get(pk=payout_id, company=company)
        except Exception:
            return None
    return None


def explain_payout(company, platform, payout_id):
    """
    Break down a payout into its component transactions.

    Returns the "Payout Explainer" data:
    - Payout summary (gross, fees, net)
    - List of component transactions (charges, refunds, adjustments)
    - Any discrepancies
    - Matched bank transaction (if any)
    """
    payout_obj = _get_payout_object(company, platform, payout_id)
    if not payout_obj:
        return {"error": "Payout not found."}

    result = {
        "platform": platform,
        "payout_id": payout_id,
    }

    if platform == "stripe":
        result.update(_explain_stripe_payout(company, payout_obj))
    elif platform == "shopify":
        result.update(_explain_shopify_payout(company, payout_obj))

    # Find matched bank transaction
    content_type = f"{platform}_payout"
    bank_tx = BankTransaction.objects.filter(
        company=company,
        matched_content_type=content_type,
        matched_object_id=payout_id,
        status="MATCHED",
    ).first()

    if bank_tx:
        result["bank_transaction"] = {
            "id": bank_tx.id,
            "date": str(bank_tx.transaction_date),
            "description": bank_tx.description,
            "amount": str(bank_tx.amount),
            "bank_account": bank_tx.bank_account.account_name,
        }
    else:
        result["bank_transaction"] = None

    return result


def _explain_stripe_payout(company, payout):
    """Break down a Stripe payout into transactions."""
    try:
        from stripe_connector.models import StripePayoutTransaction
        from stripe_connector.payout_reads import canonical_payout_reads_enabled
    except ImportError:
        return {"transactions": [], "summary": {}}

    if canonical_payout_reads_enabled():
        result = _explain_stripe_payout_canonical(company, payout)
        if result is not None:
            return result
        # No canonical rows for this payout (projection gap) — fall through to
        # the legacy read rather than rendering an empty explainer.

    txns = StripePayoutTransaction.objects.filter(
        company=company,
        payout=payout,
    ).order_by("transaction_type", "-amount")

    charges_total = Decimal("0")
    refunds_total = Decimal("0")
    fees_total = Decimal("0")
    adjustments_total = Decimal("0")
    transactions = []

    for txn in txns:
        tx_data = {
            "id": txn.id,
            "type": txn.transaction_type,
            "amount": str(txn.amount),
            "fee": str(txn.fee),
            "net": str(txn.net),
            "source_id": txn.source_id,
            "verified": txn.verified,
        }
        transactions.append(tx_data)

        if txn.transaction_type == "charge":
            charges_total += txn.amount
            fees_total += txn.fee
        elif txn.transaction_type == "refund":
            refunds_total += abs(txn.amount)
        elif txn.transaction_type == "adjustment":
            adjustments_total += txn.amount
        # payout type transactions are the payout itself, skip

    computed_net = charges_total - refunds_total - fees_total + adjustments_total
    actual_net = payout.net_amount
    discrepancy = actual_net - computed_net

    return {
        "payout_external_id": payout.stripe_payout_id,
        "gross_amount": str(payout.gross_amount),
        "fees": str(payout.fees),
        "net_amount": str(payout.net_amount),
        "currency": payout.currency,
        "payout_date": str(payout.payout_date),
        "payout_status": payout.stripe_status,
        "transactions": transactions,
        "summary": {
            "charges": str(charges_total),
            "refunds": str(refunds_total),
            "fees": str(fees_total),
            "adjustments": str(adjustments_total),
            "computed_net": str(computed_net),
            "actual_net": str(actual_net),
            "discrepancy": str(discrepancy),
            "has_discrepancy": abs(discrepancy) > Decimal("0.01"),
        },
        "transaction_count": len(transactions),
    }


def _explain_stripe_payout_canonical(company, payout):
    """C3: line money from the canonical ProviderPayoutLine read-model.

    Per-line ``verified``: canonical (``line.verified``, PR-D2) behind
    STRIPE_CANONICAL_VERIFIED_READS, else joined from the legacy line cache by
    source_id. The int row ``id`` keeps the legacy twin when present (frontend
    key + the pk namespace bank matching persists — C4 scope). Returns None
    when the payout has no canonical header, so the caller can fall back to
    the legacy read instead of rendering an empty explainer.
    """
    from stripe_connector.models import StripePayoutTransaction
    from stripe_connector.payout_reads import (
        canonical_header,
        canonical_lines,
        canonical_verified_reads_enabled,
        normalize_line_kind,
    )

    header = canonical_header(company, payout.stripe_payout_id)
    if header is None:
        logger.warning(
            "C3: no canonical header for stripe payout %s; explaining from legacy",
            payout.stripe_payout_id,
        )
        return None

    legacy_by_source = {}
    for txn in StripePayoutTransaction.objects.filter(company=company, payout=payout):
        legacy_by_source.setdefault(txn.source_id, txn)

    lines = list(canonical_lines(company, payout.stripe_payout_id))
    # Mirror the legacy ordering: (transaction_type, -amount).
    lines.sort(key=lambda ln: (normalize_line_kind(ln.kind), -ln.gross_amount))

    charges_total = Decimal("0")
    refunds_total = Decimal("0")
    fees_total = Decimal("0")
    adjustments_total = Decimal("0")
    transactions = []

    canonical_verified = canonical_verified_reads_enabled()
    for line in lines:
        kind = normalize_line_kind(line.kind)
        twin = legacy_by_source.get(line.source_id)
        transactions.append(
            {
                "id": twin.id if twin else str(line.id),
                "type": kind,
                "amount": str(line.gross_amount),
                "fee": str(line.fee),
                "net": str(line.net_amount),
                "source_id": line.source_id,
                "verified": bool(line.verified) if canonical_verified else (bool(twin.verified) if twin else False),
            }
        )

        if kind == "charge":
            charges_total += line.gross_amount
            fees_total += line.fee
        elif kind == "refund":
            refunds_total += abs(line.gross_amount)
        elif kind == "adjustment":
            adjustments_total += line.gross_amount
        # payout-kind lines are the payout itself; the emit already excludes them

    computed_net = charges_total - refunds_total - fees_total + adjustments_total
    actual_net = header.net_amount
    discrepancy = actual_net - computed_net

    return {
        "payout_external_id": header.payout_batch_id,
        "gross_amount": str(header.gross_amount),
        "fees": str(header.fees),
        "net_amount": str(header.net_amount),
        "currency": header.currency,
        "payout_date": str(header.payout_date) if header.payout_date else "",
        "payout_status": header.provider_status,
        "transactions": transactions,
        "summary": {
            "charges": str(charges_total),
            "refunds": str(refunds_total),
            "fees": str(fees_total),
            "adjustments": str(adjustments_total),
            "computed_net": str(computed_net),
            "actual_net": str(actual_net),
            "discrepancy": str(discrepancy),
            "has_discrepancy": abs(discrepancy) > Decimal("0.01"),
        },
        "transaction_count": len(transactions),
    }


def _explain_shopify_payout(company, payout):
    """Break down a Shopify payout into transactions."""
    try:
        from shopify_connector.models import ShopifyPayoutTransaction
    except ImportError:
        return {"transactions": [], "summary": {}}

    txns = ShopifyPayoutTransaction.objects.filter(
        company=company,
        payout=payout,
    ).order_by("transaction_type", "-amount")

    charges_total = Decimal("0")
    refunds_total = Decimal("0")
    fees_total = Decimal("0")
    adjustments_total = Decimal("0")
    transactions = []

    for txn in txns:
        tx_data = {
            "id": txn.id,
            "type": txn.transaction_type,
            "amount": str(txn.amount),
            "fee": str(txn.fee),
            "net": str(txn.net),
            "source_id": str(txn.shopify_transaction_id),
            "verified": txn.verified,
        }
        transactions.append(tx_data)

        if txn.transaction_type == "charge":
            charges_total += txn.amount
            fees_total += txn.fee
        elif txn.transaction_type == "refund":
            refunds_total += abs(txn.amount)
        elif txn.transaction_type == "adjustment":
            adjustments_total += txn.amount

    computed_net = charges_total - refunds_total - fees_total + adjustments_total
    actual_net = payout.net_amount
    discrepancy = actual_net - computed_net

    return {
        "payout_external_id": str(payout.shopify_payout_id),
        "gross_amount": str(payout.gross_amount),
        "fees": str(payout.fees),
        "net_amount": str(payout.net_amount),
        "currency": payout.currency,
        "payout_date": str(payout.payout_date),
        "payout_status": payout.shopify_status,
        # Shopify fee breakdown
        "fee_breakdown": {
            "charges_gross": str(payout.charges_gross),
            "charges_fee": str(payout.charges_fee),
            "refunds_gross": str(payout.refunds_gross),
            "refunds_fee": str(payout.refunds_fee),
            "adjustments_gross": str(payout.adjustments_gross),
            "adjustments_fee": str(payout.adjustments_fee),
        },
        "transactions": transactions,
        "summary": {
            "charges": str(charges_total),
            "refunds": str(refunds_total),
            "fees": str(fees_total),
            "adjustments": str(adjustments_total),
            "computed_net": str(computed_net),
            "actual_net": str(actual_net),
            "discrepancy": str(discrepancy),
            "has_discrepancy": abs(discrepancy) > Decimal("0.01"),
        },
        "transaction_count": len(transactions),
    }


# =============================================================================
# Reconciliation Overview
# =============================================================================


def get_reconciliation_overview(company):
    """
    Get a high-level reconciliation overview.

    Returns:
    - Bank transaction stats (total, matched, unmatched)
    - Platform payout stats (total, matched to bank, unmatched)
    - Unmatched amounts
    - Match rate
    """
    # Bank transaction stats
    bank_qs = BankTransaction.objects.filter(company=company)
    total_bank_txns = bank_qs.count()
    matched_bank = bank_qs.filter(status="MATCHED").count()
    unmatched_bank = bank_qs.filter(status="UNMATCHED").count()
    excluded_bank = bank_qs.filter(status="EXCLUDED").count()

    # Unmatched deposit total (potential payout matches)
    unmatched_deposits = bank_qs.filter(status="UNMATCHED", amount__gt=0).aggregate(total=Sum("amount"))[
        "total"
    ] or Decimal("0")

    # Unmatched withdrawal total
    unmatched_withdrawals = bank_qs.filter(status="UNMATCHED", amount__lt=0).aggregate(total=Sum("amount"))[
        "total"
    ] or Decimal("0")

    # Platform payout stats
    stripe_payouts = _get_stripe_payouts(company)
    shopify_payouts = _get_shopify_payouts(company)
    all_payouts = stripe_payouts + shopify_payouts

    total_payouts = len(all_payouts)
    matched_payouts = 0
    unmatched_payout_amount = Decimal("0")

    for p in all_payouts:
        if _is_payout_already_matched(company, p):
            matched_payouts += 1
        else:
            unmatched_payout_amount += p["net_amount"]

    unmatched_payouts = total_payouts - matched_payouts
    match_rate = round(matched_payouts / total_payouts * 100, 1) if total_payouts else 0

    # Exception queue summary
    from .models import ReconciliationException

    open_statuses = [
        ReconciliationException.Status.OPEN,
        ReconciliationException.Status.IN_PROGRESS,
        ReconciliationException.Status.ESCALATED,
    ]
    open_exceptions = ReconciliationException.objects.filter(
        company=company,
        status__in=open_statuses,
    )
    open_count = open_exceptions.count()
    critical_count = open_exceptions.filter(severity=ReconciliationException.Severity.CRITICAL).count()

    return {
        "bank": {
            "total": total_bank_txns,
            "matched": matched_bank,
            "unmatched": unmatched_bank,
            "excluded": excluded_bank,
            "unmatched_deposits": str(unmatched_deposits),
            "unmatched_withdrawals": str(abs(unmatched_withdrawals)),
        },
        "payouts": {
            "total": total_payouts,
            "matched": matched_payouts,
            "unmatched": unmatched_payouts,
            "unmatched_amount": str(unmatched_payout_amount),
            "stripe_count": len(stripe_payouts),
            "shopify_count": len(shopify_payouts),
        },
        "exceptions": {
            "open": open_count,
            "critical": critical_count,
        },
        "match_rate": match_rate,
    }


def get_unmatched_payouts(company):
    """Get all payouts that haven't been matched to bank transactions."""
    all_payouts = get_all_payouts(company)
    unmatched = []

    for p in all_payouts:
        if not _is_payout_already_matched(company, p):
            p["payout_date"] = str(p["payout_date"])
            # Convert Decimal fields to strings for JSON
            for key in ("gross_amount", "fees", "net_amount"):
                p[key] = str(p[key])
            unmatched.append(p)

    unmatched.sort(key=lambda x: x["payout_date"], reverse=True)
    return unmatched
