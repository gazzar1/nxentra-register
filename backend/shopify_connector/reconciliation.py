# shopify_connector/reconciliation.py
"""
Shopify payout reconciliation engine.

Matches payout transactions to local orders/refunds and computes
reconciliation status at payout, store, and company levels.

Three reconciliation layers:
    Layer 1 — Payout-level: does payout.gross match sum of order JEs in the period?
    Layer 2 — Transaction-level: each ShopifyPayoutTransaction matched to a local order/refund?
    Layer 3 — Bank-level: payout JE Cash/Bank line matched to bank statement line?
              (handled by accounting.bank_reconciliation)

Usage:
    from shopify_connector.reconciliation import reconcile_payout, reconciliation_summary
    result = reconcile_payout(company, payout)
    summary = reconciliation_summary(company, date_from, date_to)
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Sum, Count, Q

from .models import (
    ShopifyPayout,
    ShopifyPayoutTransaction,
    ShopifyOrder,
    ShopifyRefund,
)

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────

@dataclass
class TransactionMatch:
    """Result of matching a single payout transaction."""
    shopify_transaction_id: int
    transaction_type: str
    amount: Decimal
    fee: Decimal
    net: Decimal
    matched: bool = False
    matched_to: str = ""  # e.g. "Order #1001" or "Refund #1001-R1"
    variance: Decimal = Decimal("0")


@dataclass
class PayoutReconciliation:
    """Reconciliation result for a single payout."""
    shopify_payout_id: int
    payout_date: date
    gross_amount: Decimal
    fees: Decimal
    net_amount: Decimal
    currency: str

    # Transaction matching
    total_transactions: int = 0
    matched_transactions: int = 0
    unmatched_transactions: int = 0

    # Amount verification
    transactions_gross_sum: Decimal = Decimal("0")
    transactions_fee_sum: Decimal = Decimal("0")
    transactions_net_sum: Decimal = Decimal("0")
    gross_variance: Decimal = Decimal("0")
    fee_variance: Decimal = Decimal("0")
    net_variance: Decimal = Decimal("0")

    # Status
    status: str = "unverified"  # unverified | verified | discrepancy | no_transactions
    discrepancies: list = field(default_factory=list)
    transaction_matches: list = field(default_factory=list)


@dataclass
class ReconciliationSummary:
    """Summary of reconciliation across multiple payouts."""
    date_from: date
    date_to: date
    total_payouts: int = 0
    verified_payouts: int = 0
    discrepancy_payouts: int = 0
    unverified_payouts: int = 0

    total_gross: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    total_net: Decimal = Decimal("0")

    total_transactions: int = 0
    matched_transactions: int = 0
    unmatched_transactions: int = 0
    match_rate: Decimal = Decimal("0")

    # Clearing account health
    expected_clearing_balance: Decimal = Decimal("0")
    unmatched_order_total: Decimal = Decimal("0")

    payouts: list = field(default_factory=list)


# ── Core reconciliation ──────────────────────────────────────────

def reconcile_payout(company, payout: ShopifyPayout) -> PayoutReconciliation:
    """
    Reconcile a single payout by matching its transactions to local records.

    If transactions haven't been fetched yet, returns status='no_transactions'.
    """
    result = PayoutReconciliation(
        shopify_payout_id=payout.shopify_payout_id,
        payout_date=payout.payout_date,
        gross_amount=payout.gross_amount,
        fees=payout.fees,
        net_amount=payout.net_amount,
        currency=payout.currency,
    )

    transactions = list(payout.transactions.all())
    if not transactions:
        result.status = "no_transactions"
        return result

    result.total_transactions = len(transactions)

    # Match each transaction
    for txn in transactions:
        match = _match_transaction(company, txn)
        result.transaction_matches.append(match)

        result.transactions_gross_sum += txn.amount
        result.transactions_fee_sum += abs(txn.fee)
        result.transactions_net_sum += txn.net

        if match.matched:
            result.matched_transactions += 1
        else:
            result.unmatched_transactions += 1

    # Compute variances
    result.gross_variance = result.transactions_gross_sum - payout.gross_amount
    result.fee_variance = result.transactions_fee_sum - payout.fees
    result.net_variance = result.transactions_net_sum - payout.net_amount

    # Determine status
    has_amount_discrepancy = (
        result.net_variance != 0
        or result.fee_variance != 0
    )
    if has_amount_discrepancy:
        result.status = "discrepancy"
        if result.net_variance != 0:
            result.discrepancies.append(
                f"Net variance: {result.net_variance} "
                f"(transactions={result.transactions_net_sum}, payout={payout.net_amount})"
            )
        if result.fee_variance != 0:
            result.discrepancies.append(
                f"Fee variance: {result.fee_variance} "
                f"(transactions={result.transactions_fee_sum}, payout={payout.fees})"
            )
    elif result.unmatched_transactions > 0:
        result.status = "partial"
    else:
        result.status = "verified"

    return result


def _match_transaction(company, txn: ShopifyPayoutTransaction) -> TransactionMatch:
    """
    Match a single payout transaction to a local order or refund.

    Charge transactions match to ShopifyOrder by shopify_order_id.
    Refund transactions match to ShopifyRefund by order_id + amount.
    Adjustments and payout-type transactions are auto-verified.
    """
    match = TransactionMatch(
        shopify_transaction_id=txn.shopify_transaction_id,
        transaction_type=txn.transaction_type,
        amount=txn.amount,
        fee=txn.fee,
        net=txn.net,
    )

    # Already verified in previous pass
    if txn.verified and txn.local_order:
        match.matched = True
        match.matched_to = f"Order {txn.local_order.shopify_order_name}"
        return match

    # Payout-type transactions are settlement records, auto-verify
    if txn.transaction_type in ("payout", "other"):
        match.matched = True
        match.matched_to = f"Settlement ({txn.transaction_type})"
        return match

    if not txn.source_order_id:
        return match

    if txn.transaction_type == ShopifyPayoutTransaction.TransactionType.CHARGE:
        order = ShopifyOrder.objects.filter(
            company=company,
            shopify_order_id=txn.source_order_id,
        ).first()
        if order:
            match.matched = True
            match.matched_to = f"Order {order.shopify_order_name}"
            match.variance = txn.amount - order.total_price

            # Update verification state if not already set
            if not txn.verified:
                txn.verified = True
                txn.local_order = order
                txn.save(update_fields=["verified", "local_order"])
        return match

    if txn.transaction_type == ShopifyPayoutTransaction.TransactionType.REFUND:
        # Try to match to a refund record — pick the one closest in amount
        refunds = list(ShopifyRefund.objects.filter(
            company=company,
            order__shopify_order_id=txn.source_order_id,
        ).order_by("shopify_created_at"))
        txn_abs = abs(txn.amount)
        if refunds:
            # Best match: smallest variance by amount
            best = min(refunds, key=lambda r: abs(r.amount - txn_abs))
            match.matched = True
            match.matched_to = f"Refund {best.shopify_refund_id} on Order {best.order.shopify_order_name}"
            match.variance = txn_abs - best.amount
        else:
            # Fall back to order match
            order = ShopifyOrder.objects.filter(
                company=company,
                shopify_order_id=txn.source_order_id,
            ).first()
            if order:
                match.matched = True
                match.matched_to = f"Refund on Order {order.shopify_order_name} (no refund record)"
        return match

    if txn.transaction_type == ShopifyPayoutTransaction.TransactionType.ADJUSTMENT:
        match.matched = True
        match.matched_to = f"Adjustment (order {txn.source_order_id})"
        return match

    return match


# ── Summary / reporting ──────────────────────────────────────────

def reconciliation_summary(
    company,
    date_from: date,
    date_to: date,
    store=None,
) -> ReconciliationSummary:
    """
    Compute reconciliation summary for a date range.

    Args:
        company: Company instance
        date_from: Start date (inclusive)
        date_to: End date (inclusive)
        store: Optional ShopifyStore to filter by
    """
    summary = ReconciliationSummary(
        date_from=date_from,
        date_to=date_to,
    )

    payout_qs = ShopifyPayout.objects.filter(
        company=company,
        payout_date__gte=date_from,
        payout_date__lte=date_to,
    )
    if store:
        payout_qs = payout_qs.filter(store=store)

    payouts = list(payout_qs.order_by("-payout_date"))
    summary.total_payouts = len(payouts)

    for payout in payouts:
        recon = reconcile_payout(company, payout)
        summary.payouts.append(recon)

        summary.total_gross += payout.gross_amount
        summary.total_fees += payout.fees
        summary.total_net += payout.net_amount

        summary.total_transactions += recon.total_transactions
        summary.matched_transactions += recon.matched_transactions
        summary.unmatched_transactions += recon.unmatched_transactions

        if recon.status == "verified":
            summary.verified_payouts += 1
        elif recon.status == "discrepancy":
            summary.discrepancy_payouts += 1
        else:
            summary.unverified_payouts += 1

    if summary.total_transactions > 0:
        summary.match_rate = Decimal(str(
            round(summary.matched_transactions / summary.total_transactions * 100, 1)
        ))

    # Compute unmatched orders (orders without a matching payout charge)
    summary.unmatched_order_total = _unmatched_orders_total(
        company, date_from, date_to, store,
    )

    return summary


def _unmatched_orders_total(company, date_from, date_to, store=None):
    """
    Sum of orders in the period that don't appear in any payout transaction.

    These orders have been recorded as revenue but haven't settled yet
    (they contribute to the Clearing account balance).
    """
    order_qs = ShopifyOrder.objects.filter(
        company=company,
        order_date__gte=date_from,
        order_date__lte=date_to,
        status=ShopifyOrder.Status.PROCESSED,
    )
    if store:
        order_qs = order_qs.filter(store=store)

    # Find orders that have been matched in payout transactions
    matched_order_ids = set(
        ShopifyPayoutTransaction.objects.filter(
            company=company,
            transaction_type=ShopifyPayoutTransaction.TransactionType.CHARGE,
            verified=True,
            local_order__isnull=False,
        ).values_list("local_order__shopify_order_id", flat=True)
    )

    unmatched_total = Decimal("0")
    for order in order_qs:
        if order.shopify_order_id not in matched_order_ids:
            unmatched_total += order.total_price

    return unmatched_total


# ── Serialization helpers ────────────────────────────────────────

def payout_recon_to_dict(recon: PayoutReconciliation) -> dict:
    """Convert a PayoutReconciliation to a JSON-serializable dict."""
    return {
        "shopify_payout_id": recon.shopify_payout_id,
        "payout_date": str(recon.payout_date),
        "gross_amount": str(recon.gross_amount),
        "fees": str(recon.fees),
        "net_amount": str(recon.net_amount),
        "currency": recon.currency,
        "status": recon.status,
        "total_transactions": recon.total_transactions,
        "matched_transactions": recon.matched_transactions,
        "unmatched_transactions": recon.unmatched_transactions,
        "gross_variance": str(recon.gross_variance),
        "fee_variance": str(recon.fee_variance),
        "net_variance": str(recon.net_variance),
        "discrepancies": recon.discrepancies,
        "transactions": [
            {
                "shopify_transaction_id": m.shopify_transaction_id,
                "transaction_type": m.transaction_type,
                "amount": str(m.amount),
                "fee": str(m.fee),
                "net": str(m.net),
                "matched": m.matched,
                "matched_to": m.matched_to,
                "variance": str(m.variance),
            }
            for m in recon.transaction_matches
        ],
    }


def summary_to_dict(summary: ReconciliationSummary) -> dict:
    """Convert a ReconciliationSummary to a JSON-serializable dict."""
    return {
        "date_from": str(summary.date_from),
        "date_to": str(summary.date_to),
        "total_payouts": summary.total_payouts,
        "verified_payouts": summary.verified_payouts,
        "discrepancy_payouts": summary.discrepancy_payouts,
        "unverified_payouts": summary.unverified_payouts,
        "total_gross": str(summary.total_gross),
        "total_fees": str(summary.total_fees),
        "total_net": str(summary.total_net),
        "total_transactions": summary.total_transactions,
        "matched_transactions": summary.matched_transactions,
        "unmatched_transactions": summary.unmatched_transactions,
        "match_rate": str(summary.match_rate),
        "unmatched_order_total": str(summary.unmatched_order_total),
        "payouts": [
            {
                "shopify_payout_id": p.shopify_payout_id,
                "payout_date": str(p.payout_date),
                "net_amount": str(p.net_amount),
                "fees": str(p.fees),
                "status": p.status,
                "matched": p.matched_transactions,
                "total": p.total_transactions,
            }
            for p in summary.payouts
        ],
    }
