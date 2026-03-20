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

from django.db.models import Q, Sum
from django.utils import timezone

from .models import BankAccount, BankTransaction

logger = logging.getLogger(__name__)

# Confidence thresholds
CONFIDENCE_EXACT = 100       # Amount + date + reference all match
CONFIDENCE_HIGH = 90         # Amount + date match (within 1 day)
CONFIDENCE_MEDIUM = 75       # Amount + date proximity (within 5 days)
CONFIDENCE_LOW = 55          # Amount match only
AUTO_MATCH_THRESHOLD = 75    # Minimum confidence for auto-match


# =============================================================================
# Payout Discovery
# =============================================================================

def _get_stripe_payouts(company, date_from=None, date_to=None):
    """Get Stripe payouts for matching."""
    try:
        from stripe_connector.models import StripePayout
    except ImportError:
        return []

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
            suggestions.append({
                **payout,
                "confidence": confidence,
                "payout_date": str(payout["payout_date"]),
            })

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

            matches.append({
                "bank_transaction_id": tx.id,
                "payout_platform": best_payout["platform"],
                "payout_id": best_payout["payout_id"],
                "confidence": best_confidence,
                "amount": str(tx.amount),
            })

            # Remove from available payouts to prevent double-matching
            payouts.remove(best_payout)
            matched_count += 1

    logger.info(
        "Auto-matched %d/%d bank transactions for company %s",
        matched_count, len(unmatched), company.id,
    )

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

    return {"status": "matched", "bank_transaction_id": tx.id}


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
    except ImportError:
        return {"transactions": [], "summary": {}}

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
    unmatched_deposits = bank_qs.filter(
        status="UNMATCHED", amount__gt=0
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    # Unmatched withdrawal total
    unmatched_withdrawals = bank_qs.filter(
        status="UNMATCHED", amount__lt=0
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

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
