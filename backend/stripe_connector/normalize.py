# stripe_connector/normalize.py
"""Pure normalization of Stripe Payouts + Balance Transactions into the
canonical settlement shape (ADR-0002 S1).

Kept free of any Stripe SDK / network I/O so the fee-derivation logic — the
correctness-critical part, since ``payout.paid`` alone reports fees=0 — is
unit-testable with synthetic Stripe dicts.

Money convention: Stripe amounts are integer minor units (cents). We ground
``net`` in the payout's own ``amount`` (what actually hits the bank), derive
``fees`` from the sum of per-transaction Balance-Transaction fees, and compute
``gross = net + fees`` so the settlement projection's
``net + fees + uncollected == gross`` balance guard holds by construction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal


def _cents(value) -> Decimal:
    """Integer minor units → a 2-dp Decimal major-unit amount."""
    return (Decimal(int(value or 0)) / 100).quantize(Decimal("0.01"))


def _ts_to_iso_date(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%d")


def derive_payout_breakdown(payout: dict, balance_txns: list[dict]) -> dict:
    """Normalize one Stripe payout + its balance transactions.

    Args:
        payout: a Stripe Payout object dict — ``id``, ``amount`` (net, cents),
            ``currency``, ``arrival_date`` (unix), ``status``.
        balance_txns: the Balance Transactions belonging to the payout
            (``BalanceTransaction.list(payout=<id>)``). The payout's own
            ``type == "payout"`` transaction is excluded from the breakdown.

    Returns a dict with Decimal ``gross`` / ``fees`` / ``net`` (major units),
    ``currency``, ``payout_date`` (ISO), and ``line_items`` — a per-transaction
    breakdown ``[{order_id, gross, fee, net, status}]``.
    """
    fee_cents = 0
    line_items: list[dict] = []
    for bt in balance_txns:
        if bt.get("type") == "payout":
            continue  # the payout transaction itself, not a constituent
        amount = int(bt.get("amount", 0))
        fee = int(bt.get("fee", 0))
        fee_cents += fee
        line_items.append(
            {
                "order_id": bt.get("source") or bt.get("id", ""),
                "gross": str(_cents(amount)),
                "fee": str(_cents(fee)),
                "net": str(_cents(amount - fee)),
                "status": bt.get("type", ""),
            }
        )

    net = _cents(payout.get("amount", 0))
    fees = _cents(fee_cents)
    gross = (net + fees).quantize(Decimal("0.01"))

    return {
        "gross": gross,
        "fees": fees,
        "net": net,
        "currency": (payout.get("currency") or "usd").upper(),
        "payout_date": _ts_to_iso_date(payout.get("arrival_date")),
        "status": payout.get("status", ""),
        "line_items": line_items,
    }
