# inventory/costing.py
"""
F18 — pure receipt-costing math, shared by the inventory commands and the
InventoryBalance projection so command-time state and rebuilds can never
diverge (the A154 rebuild path replays through the projection).

The defect this replaces: every receipt site computed

    new_avg = (old_value + receipt_value) / new_qty  if new_qty > 0
              else unit_cost

which silently DISCARDS the carried value of a negative balance whenever
the receipt lands at or below zero (qty -1 @ 100 + receipt 1 @ 77 →
stock_value jumped from -100 to 0, vaporizing 23 of booked COGS), and
POISONS avg_cost when crossing to positive (qty -5 @ 10 + 10 @ 12 →
avg 14, higher than any real purchase price).

Correct semantics for a receipt onto a negative balance:
- the receipt first EXTINGUISHES the hole at its carried cost (the cost
  its COGS was already booked at);
- the difference between the extinguishment at receipt cost and at
  carried cost is a REAL P&L variance (replacement cost of stock sold
  while below zero) — returned so GL-bearing callers can book it;
- any remainder above zero starts fresh at the receipt's own unit cost.

No ORM, no Django imports — safe to call from anywhere.
"""

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")


@dataclass(frozen=True)
class ReceiptApplication:
    new_qty: Decimal
    new_avg_cost: Decimal
    new_stock_value: Decimal
    # Units of the negative hole this receipt filled (0 on a normal receipt).
    extinguished_qty: Decimal
    # The avg cost the hole was carried at (== COGS already booked).
    carried_cost: Decimal
    # extinguished_qty * (unit_cost - carried_cost).
    # > 0: refilled at a higher cost than the hole was booked at → extra
    #      COGS (Dr COGS / Cr Inventory).
    # < 0: refilled cheaper → COGS relief (Dr Inventory / Cr COGS).
    variance_value: Decimal


def apply_receipt_to_balance(
    qty_on_hand: Decimal,
    avg_cost: Decimal,
    receipt_qty: Decimal,
    unit_cost: Decimal,
) -> ReceiptApplication:
    """Apply a positive-quantity receipt to a weighted-average balance.

    Value-continuity invariant (what the old formula broke):
        new_stock_value == old_stock_value + receipt_qty * unit_cost
                           - variance_value
    """
    if receipt_qty <= 0:
        raise ValueError(f"receipt_qty must be positive, got {receipt_qty}")

    new_qty = qty_on_hand + receipt_qty

    if qty_on_hand >= 0:
        # Classic weighted-average blend — value-continuous as-is.
        old_value = qty_on_hand * avg_cost
        new_stock_value = old_value + receipt_qty * unit_cost
        new_avg_cost = new_stock_value / new_qty
        return ReceiptApplication(
            new_qty=new_qty,
            new_avg_cost=new_avg_cost,
            new_stock_value=new_stock_value,
            extinguished_qty=ZERO,
            carried_cost=avg_cost,
            variance_value=ZERO,
        )

    extinguished_qty = min(receipt_qty, -qty_on_hand)
    variance_value = extinguished_qty * (unit_cost - avg_cost)
    remainder = receipt_qty - extinguished_qty

    if remainder > 0:
        # Hole fully extinguished; the surplus starts fresh at its own cost.
        new_avg_cost = unit_cost
        new_stock_value = remainder * unit_cost
    else:
        # Still (or exactly) at/below zero: the remaining hole keeps the
        # cost its COGS was booked at — never the new receipt's cost.
        new_avg_cost = avg_cost
        new_stock_value = new_qty * avg_cost

    return ReceiptApplication(
        new_qty=new_qty,
        new_avg_cost=new_avg_cost,
        new_stock_value=new_stock_value,
        extinguished_qty=extinguished_qty,
        carried_cost=avg_cost,
        variance_value=variance_value,
    )


def apply_receipt_value_continuous(
    qty_on_hand: Decimal,
    avg_cost: Decimal,
    receipt_qty: Decimal,
    unit_cost: Decimal,
) -> ReceiptApplication:
    """Fallback for callers that CANNOT book the variance anywhere (item
    without a COGS account): never discard value — blend it all into the
    balance instead. stock_value may then deviate from qty * avg_cost
    around zero; the caller must log that loudly."""
    if receipt_qty <= 0:
        raise ValueError(f"receipt_qty must be positive, got {receipt_qty}")

    old_value = qty_on_hand * avg_cost
    new_qty = qty_on_hand + receipt_qty
    new_stock_value = old_value + receipt_qty * unit_cost
    new_avg_cost = new_stock_value / new_qty if new_qty > 0 else avg_cost
    return ReceiptApplication(
        new_qty=new_qty,
        new_avg_cost=new_avg_cost,
        new_stock_value=new_stock_value,
        extinguished_qty=ZERO,
        carried_cost=avg_cost,
        variance_value=ZERO,
    )
