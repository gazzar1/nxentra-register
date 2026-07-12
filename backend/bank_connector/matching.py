# bank_connector/matching.py
"""
Payout discovery reads for the exception queue (matcher engine retired, A166).

The legacy /banking matcher — auto_match_transactions, manual_match,
_reconcile_payout_je, _create_payout_je and their views — was retired in
A166: it double-posted against the canonical settlement path (A158) and
its raw unmatch stranded reconciled journal lines with no event trail.
The canonical engine lives in reconciliation/ + accounting/bank_views.py.

What remains here is read-only:
- payout discovery (_get_stripe_payouts / _get_shopify_payouts /
  get_all_payouts) — consumed by bank_connector/exceptions.py detectors
  and pinned by the ADR-0002 S2 read-contract tests
- _is_payout_already_matched — legacy-state read used by the same
- payout explainers (_explain_*) — pinned by the S2 read-contract tests

Historical note: RECONCILIATION_MATCH_CONFIRMED events with
confirmation_kind='platform_payout_reconcile' exist in production event
logs; their projection consumer branch in reconciliation/projections.py
must survive for rebuild/replay even though the emitter is gone.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from .models import BankTransaction

# Lazy imports for accounting models (avoid circular imports)
# These are imported inside functions that need them.

logger = logging.getLogger(__name__)


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


def _is_payout_already_matched(company, payout):
    """Check if a payout is already matched to a bank transaction."""
    content_type = f"{payout['platform']}_payout"
    return BankTransaction.objects.filter(
        company=company,
        matched_content_type=content_type,
        matched_object_id=payout["id"],
        status="MATCHED",
    ).exists()


# =============================================================================
# Payout Explainer
# =============================================================================


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
