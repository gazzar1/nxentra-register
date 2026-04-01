# stripe_connector/reconciliation.py
"""
Stripe payout reconciliation engine.

Matches payout transactions (balance transactions) to local charges/refunds
and computes reconciliation status at payout level.

Mirrors shopify_connector/reconciliation.py for consistency.

Usage:
    from stripe_connector.reconciliation import reconcile_payout, reconciliation_summary
    result = reconcile_payout(company, payout)
    summary = reconciliation_summary(company, date_from, date_to)
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .models import (
    StripeCharge,
    StripePayout,
    StripePayoutTransaction,
    StripeRefund,
)

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────

@dataclass
class TransactionMatch:
    """Result of matching a single payout transaction."""
    stripe_balance_txn_id: str
    transaction_type: str
    amount: Decimal
    fee: Decimal
    net: Decimal
    matched: bool = False
    matched_to: str = ""
    variance: Decimal = Decimal("0")


@dataclass
class PayoutReconciliation:
    """Reconciliation result for a single Stripe payout."""
    stripe_payout_id: str
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


# ── Core reconciliation ─────────────────────────────────────────

def reconcile_payout(company, payout):
    """
    Reconcile a single Stripe payout by matching its balance transactions
    to local charges and refunds.

    Returns a PayoutReconciliation with variance computations.
    """
    txns = StripePayoutTransaction.objects.filter(
        payout=payout,
    ).select_related("local_charge")

    recon = PayoutReconciliation(
        stripe_payout_id=payout.stripe_payout_id,
        payout_date=payout.payout_date,
        gross_amount=payout.gross_amount,
        fees=payout.fees,
        net_amount=payout.net_amount,
        currency=payout.currency,
    )

    if not txns.exists():
        recon.status = "no_transactions"
        return recon

    recon.total_transactions = txns.count()
    gross_sum = Decimal("0")
    fee_sum = Decimal("0")
    net_sum = Decimal("0")

    for txn in txns:
        gross_sum += txn.amount
        fee_sum += txn.fee
        net_sum += txn.net

        match = TransactionMatch(
            stripe_balance_txn_id=txn.stripe_balance_txn_id,
            transaction_type=txn.transaction_type,
            amount=txn.amount,
            fee=txn.fee,
            net=txn.net,
        )

        if txn.verified and txn.local_charge:
            match.matched = True
            match.matched_to = f"Charge {txn.local_charge.stripe_charge_id}"
            recon.matched_transactions += 1
        elif txn.transaction_type == "charge" and txn.source_id:
            # Try to auto-match
            try:
                charge = StripeCharge.objects.get(
                    company=company,
                    stripe_charge_id=txn.source_id,
                )
                match.matched = True
                match.matched_to = f"Charge {charge.stripe_charge_id}"
                recon.matched_transactions += 1
                # Update the txn record
                txn.local_charge = charge
                txn.verified = True
                txn.save(update_fields=["local_charge", "verified"])
            except StripeCharge.DoesNotExist:
                pass
        elif txn.transaction_type == "refund" and txn.source_id:
            # Match refunds via the refund's charge
            try:
                refund = StripeRefund.objects.get(
                    company=company,
                    stripe_refund_id=txn.source_id,
                )
                match.matched = True
                match.matched_to = f"Refund {refund.stripe_refund_id}"
                recon.matched_transactions += 1
                txn.verified = True
                txn.save(update_fields=["verified"])
            except StripeRefund.DoesNotExist:
                pass
        elif txn.transaction_type in ("adjustment", "payout", "other"):
            # Adjustments and payout fees are auto-verified
            match.matched = True
            match.matched_to = f"{txn.transaction_type}: {txn.source_id or 'n/a'}"
            recon.matched_transactions += 1

        recon.transaction_matches.append(match)

    recon.unmatched_transactions = recon.total_transactions - recon.matched_transactions
    recon.transactions_gross_sum = gross_sum
    recon.transactions_fee_sum = fee_sum
    recon.transactions_net_sum = net_sum

    # Compute variances
    recon.gross_variance = payout.gross_amount - gross_sum
    recon.fee_variance = payout.fees - fee_sum
    recon.net_variance = payout.net_amount - net_sum

    # Determine status
    discrepancies = []
    if recon.gross_variance != 0:
        discrepancies.append(f"Gross variance: {recon.gross_variance}")
    if recon.fee_variance != 0:
        discrepancies.append(f"Fee variance: {recon.fee_variance}")
    if recon.net_variance != 0:
        discrepancies.append(f"Net variance: {recon.net_variance}")
    if recon.unmatched_transactions > 0:
        discrepancies.append(
            f"{recon.unmatched_transactions} unmatched transaction(s)"
        )

    recon.discrepancies = discrepancies

    if discrepancies:
        recon.status = "discrepancy"
    elif recon.matched_transactions == recon.total_transactions:
        recon.status = "verified"
    else:
        recon.status = "unverified"

    return recon


def reconciliation_summary(company, date_from, date_to):
    """
    Reconciliation summary for Stripe payouts in a date range.

    Returns aggregate metrics across all payouts.
    """
    payouts = StripePayout.objects.filter(
        company=company,
        payout_date__gte=date_from,
        payout_date__lte=date_to,
    )

    total_payouts = payouts.count()
    verified = 0
    discrepancy = 0
    unverified = 0
    total_gross = Decimal("0")
    total_fees = Decimal("0")
    total_net = Decimal("0")
    total_txns = 0
    matched_txns = 0

    for payout in payouts:
        recon = reconcile_payout(company, payout)
        total_gross += recon.gross_amount
        total_fees += recon.fees
        total_net += recon.net_amount
        total_txns += recon.total_transactions
        matched_txns += recon.matched_transactions

        if recon.status == "verified":
            verified += 1
        elif recon.status == "discrepancy":
            discrepancy += 1
        else:
            unverified += 1

    match_rate = round(matched_txns / total_txns * 100, 1) if total_txns else 0

    return {
        "platform": "stripe",
        "date_from": str(date_from),
        "date_to": str(date_to),
        "total_payouts": total_payouts,
        "verified": verified,
        "discrepancy": discrepancy,
        "unverified": unverified,
        "total_gross": str(total_gross),
        "total_fees": str(total_fees),
        "total_net": str(total_net),
        "total_transactions": total_txns,
        "matched_transactions": matched_txns,
        "match_rate": match_rate,
    }
