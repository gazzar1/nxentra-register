# tests/test_money_bridge.py
"""Unification U1 — Money Bridge breakdown (_money_flow).

The named segments (Settled / Refunded / Open) must always sum back to Sold —
'every residual has a name' — regardless of per-row rounding, because Open is
derived as sold − settled − refunded rather than read from the stored balance.
"""

from decimal import Decimal

from accounting.reconciliation_views import _money_flow


def _segments(mf):
    return {s["key"]: Decimal(s["amount"]) for s in mf["segments"]}


def test_money_flow_segments_sum_to_sold():
    totals = {
        "total_expected": "1000.00",
        "total_settled": "600.00",
        "total_refunded": "150.00",
        "open_balance": "250.00",
        "aged_30_plus": "100.00",
    }
    mf = _money_flow(totals, {"available": True, "settled_total": "580.00"}, "EGP")

    seg = _segments(mf)
    assert mf["total_sold"] == "1000.00"
    assert seg["settled"] == Decimal("600.00")
    assert seg["refunded"] == Decimal("150.00")
    assert seg["open"] == Decimal("250.00")  # derived: 1000 - 600 - 150
    assert Decimal(mf["banked"]) == Decimal("580.00")
    assert Decimal(mf["aged_over_30d"]) == Decimal("100.00")
    assert mf["currency"] == "EGP"
    assert mf["balanced"] is True
    # Every unit named: the segments reconstruct Sold.
    assert sum(seg.values()) == Decimal(mf["total_sold"])


def test_money_flow_open_is_derived_so_it_always_balances():
    # A stored open_balance could round to 33.34; the bridge derives open from
    # sold - settled - refunded, so the bar still balances exactly.
    totals = {
        "total_expected": "100.00",
        "total_settled": "33.33",
        "total_refunded": "33.33",
        "open_balance": "33.34",
        "aged_30_plus": "0.00",
    }
    mf = _money_flow(totals, {"available": False}, "EGP")
    seg = _segments(mf)
    assert seg["open"] == Decimal("33.34")  # 100 - 33.33 - 33.33
    assert mf["balanced"] is True
    assert sum(seg.values()) == Decimal("100.00")


def test_money_flow_handles_unavailable_stage2_and_negative_open():
    # Over-drained provider (settled+refunded > sold) → negative open; the bar
    # must still "balance" (segments reconstruct sold) and banked falls back to 0.
    totals = {
        "total_expected": "100.00",
        "total_settled": "120.00",
        "total_refunded": "0.00",
        "open_balance": "-20.00",
        "aged_30_plus": "0.00",
    }
    mf = _money_flow(totals, {"available": False}, "USD")
    seg = _segments(mf)
    assert seg["open"] == Decimal("-20.00")
    assert Decimal(mf["banked"]) == Decimal("0")
    assert mf["balanced"] is True
