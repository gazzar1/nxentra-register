# tests/test_f18_inventory_zero_crossing.py
"""
F18 — receipts onto negative inventory balances must be value-continuous.

The old formula (`new_avg = (old+new)/qty if qty > 0 else unit_cost`)
silently DISCARDED the carried value of a negative balance whenever a
receipt landed at or below zero (-1 @ 100 + 1 @ 77 → stock_value jumped
-100 → 0, vaporizing 23 of booked COGS and unpinning GL inventory from
the subledger forever), and POISONED avg_cost when crossing to positive
(-5 @ 10 + 10 @ 12 → avg 14, above any real purchase price).

Negative balances are a mainstream path: every Shopify fulfillment
forces allow_negative_inventory, and the buy-1/return-1 restock flow is
0 → -1 → 0 through record_stock_receipt.

Fix: shared pure math in inventory/costing.py (used by commands AND the
InventoryBalance projection so rebuilds converge). A receipt first
extinguishes the hole at its carried cost; the replacement-cost
difference books to P&L as a variance JE on GL-bearing receipts.
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.commands import create_journal_entry, post_journal_entry, save_journal_entry_complete
from accounting.models import Account, JournalEntry
from accounts.authz import ActorContext
from inventory.commands import adjust_inventory, record_stock_issue, record_stock_receipt
from inventory.models import FifoLayer, StockLedgerEntry, Warehouse
from projections.models import InventoryBalance
from projections.write_barrier import command_writes_allowed
from sales.models import Item

pytestmark = pytest.mark.django_db


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


@pytest.fixture
def accounts(db, company):
    from projections.write_barrier import projection_writes_allowed

    with projection_writes_allowed():
        inventory = Account.objects.projection().create(
            company=company,
            code="12000",
            name="F18 Inventory",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        cogs = Account.objects.projection().create(
            company=company,
            code="51000",
            name="F18 COGS",
            account_type=Account.AccountType.EXPENSE,
            status=Account.Status.ACTIVE,
        )
    return {"inventory": inventory, "cogs": cogs}


@pytest.fixture
def warehouse(db, company):
    with command_writes_allowed():
        return Warehouse.objects.create(company=company, code="MAIN", name="Main", is_default=True, is_active=True)


def _make_item(company, accounts, code="MUG-01", costing="WEIGHTED_AVERAGE", with_cogs=True):
    with command_writes_allowed():
        return Item.objects.create(
            company=company,
            code=code,
            name=f"Item {code}",
            item_type=Item.ItemType.INVENTORY,
            default_cost=Decimal("0"),
            costing_method=costing,
            is_active=True,
            inventory_account=accounts["inventory"],
            cogs_account=accounts["cogs"] if with_cogs else None,
        )


@pytest.fixture
def item(db, company, accounts, warehouse):
    return _make_item(company, accounts)


def _anchor_je(actor, accounts, amount):
    """A posted receipt-shaped JE (Dr Inventory / Cr COGS) to anchor the
    stock receipt — mirrors the restock JE the Shopify path passes in."""
    res = create_journal_entry(
        actor=actor,
        date=date(2026, 5, 10),
        memo="F18 anchor receipt JE",
        lines=[
            {"account_id": accounts["inventory"].id, "description": "receipt", "debit": str(amount), "credit": "0"},
            {"account_id": accounts["cogs"].id, "description": "receipt", "debit": "0", "credit": str(amount)},
        ],
    )
    assert res.success, res.error
    res = save_journal_entry_complete(actor, res.data.id)
    assert res.success, res.error
    res = post_journal_entry(actor, res.data.id)
    assert res.success, res.error
    return res.data


def _receive(actor, item, warehouse, qty, unit_cost, journal_entry=None, source_id=None):
    import uuid as _uuid

    return record_stock_receipt(
        actor=actor,
        source_type=StockLedgerEntry.SourceType.PURCHASE_BILL,
        source_id=source_id or str(_uuid.uuid4()),
        lines=[{"item": item, "warehouse": warehouse, "qty": Decimal(qty), "unit_cost": Decimal(unit_cost)}],
        journal_entry=journal_entry,
    )


def _issue(actor, item, warehouse, qty):
    import uuid as _uuid

    actor.company.allow_negative_inventory = True
    actor.company.save(update_fields=["allow_negative_inventory"])
    return record_stock_issue(
        actor=actor,
        source_type=StockLedgerEntry.SourceType.SALES_INVOICE,
        source_id=str(_uuid.uuid4()),
        lines=[{"item": item, "warehouse": warehouse, "qty": Decimal(qty)}],
    )


def _balance(company, item):
    return InventoryBalance.objects.get(company=company, item=item)


def _variance_jes(company):
    return JournalEntry.objects.filter(company=company, memo__startswith="Inventory variance:")


def _drive_to(actor, item, warehouse, accounts, qty, cost):
    """Drive the balance to a NEGATIVE qty at a known carried cost."""
    seed = Decimal("10")
    res = _receive(actor, item, warehouse, seed, cost, journal_entry=_anchor_je(actor, accounts, seed * Decimal(cost)))
    assert res.success, res.error
    res = _issue(actor, item, warehouse, seed - Decimal(qty))
    assert res.success, res.error
    bal = _balance(actor.company, item)
    assert bal.qty_on_hand == Decimal(qty)
    assert bal.avg_cost == Decimal(cost)
    return bal


class TestZeroCrossing:
    def test_receipt_landing_exactly_on_zero_preserves_value(self, actor, company, item, warehouse, accounts):
        """The F18 repro: -1 @ 100 + 1 @ 77 must NOT vaporize 23."""
        _drive_to(actor, item, warehouse, accounts, "-1", "100")
        assert _balance(company, item).stock_value == Decimal("-100")

        anchor = _anchor_je(actor, accounts, Decimal("77"))
        res = _receive(actor, item, warehouse, "1", "77", journal_entry=anchor)
        assert res.success, res.error

        bal = _balance(company, item)
        assert bal.qty_on_hand == Decimal("0")
        assert bal.stock_value == Decimal("0")

        # The 23 lands in P&L, not the void: hole booked at 100, refilled
        # at 77 → favorable variance, Dr Inventory / Cr COGS.
        variance = _variance_jes(company).get()
        assert variance.status == JournalEntry.Status.POSTED
        inv_line = variance.lines.get(account=accounts["inventory"])
        cogs_line = variance.lines.get(account=accounts["cogs"])
        assert inv_line.debit == Decimal("23")
        assert cogs_line.credit == Decimal("23")

        # SLE value continuity: value_after == 0 (not the old fabricated 77*0).
        sle = StockLedgerEntry.objects.filter(company=company, item=item).order_by("-sequence").first()
        assert sle.value_balance_after == Decimal("0")

    def test_partial_restock_stays_negative_at_carried_cost(self, actor, company, item, warehouse, accounts):
        _drive_to(actor, item, warehouse, accounts, "-10", "10")

        anchor = _anchor_je(actor, accounts, Decimal("12"))
        res = _receive(actor, item, warehouse, "1", "12", journal_entry=anchor)
        assert res.success, res.error

        bal = _balance(company, item)
        assert bal.qty_on_hand == Decimal("-9")
        assert bal.avg_cost == Decimal("10"), "the remaining hole keeps the cost its COGS was booked at"
        assert bal.stock_value == Decimal("-90"), "old formula fabricated -108"

        # Unfavorable: refilled at 12 against a 10 hole → Dr COGS / Cr Inventory 2.
        variance = _variance_jes(company).get()
        assert variance.lines.get(account=accounts["cogs"]).debit == Decimal("2")
        assert variance.lines.get(account=accounts["inventory"]).credit == Decimal("2")

    def test_negative_to_positive_crossing_preserves_value_and_avg(self, actor, company, item, warehouse, accounts):
        _drive_to(actor, item, warehouse, accounts, "-5", "10")

        anchor = _anchor_je(actor, accounts, Decimal("120"))
        res = _receive(actor, item, warehouse, "10", "12", journal_entry=anchor)
        assert res.success, res.error

        bal = _balance(company, item)
        assert bal.qty_on_hand == Decimal("5")
        assert bal.avg_cost == Decimal("12"), "old formula blended through the hole to a fictitious 14"
        assert bal.stock_value == Decimal("60")

        # 5 extinguished units x (12 - 10) = 10 extra COGS.
        variance = _variance_jes(company).get()
        assert variance.lines.get(account=accounts["cogs"]).debit == Decimal("10")

    def test_positive_balance_receipt_unchanged(self, actor, company, item, warehouse, accounts):
        """Regression pin: the classic blend is byte-identical, no variance."""
        _receive(actor, item, warehouse, "5", "10", journal_entry=_anchor_je(actor, accounts, Decimal("50")))
        _receive(actor, item, warehouse, "5", "20", journal_entry=_anchor_je(actor, accounts, Decimal("100")))

        bal = _balance(company, item)
        assert bal.qty_on_hand == Decimal("10")
        assert bal.avg_cost == Decimal("15")
        assert bal.stock_value == Decimal("150")
        assert not _variance_jes(company).exists()


class TestFallbacksAndGuards:
    def test_missing_cogs_account_never_discards(self, actor, company, accounts, warehouse):
        item = _make_item(company, accounts, code="NOCOGS-01", with_cogs=False)
        # Drive negative without anchor JEs (issue path needs no cogs account
        # in record_stock_issue itself).
        res = _receive(actor, item, warehouse, "1", "100")
        assert res.success, res.error
        res = _issue(actor, item, warehouse, "2")
        assert res.success, res.error
        assert _balance(company, item).qty_on_hand == Decimal("-1")

        res = _receive(actor, item, warehouse, "1", "77")
        assert res.success, res.error

        bal = _balance(company, item)
        assert bal.qty_on_hand == Decimal("0")
        # Value-continuous fallback: -100 + 77 = -23 kept on the balance,
        # never vaporized (and no variance JE — nowhere to book it).
        assert bal.stock_value == Decimal("-23")
        assert not _variance_jes(company).exists()

    def test_journal_less_receipt_keeps_subledger_continuous(self, actor, company, item, warehouse, accounts):
        """Transfers/goods-receipts pass journal_entry=None: subledger math
        is still value-continuous, variance is logged but unbooked."""
        _drive_to(actor, item, warehouse, accounts, "-1", "100")
        res = _receive(actor, item, warehouse, "1", "77", journal_entry=None)
        assert res.success, res.error
        bal = _balance(company, item)
        assert bal.qty_on_hand == Decimal("0")
        assert bal.stock_value == Decimal("0")
        assert not _variance_jes(company).exists()


class TestFifoNetting:
    def test_fifo_receipt_on_negative_balance_nets_layers(self, actor, company, accounts, warehouse):
        item = _make_item(company, accounts, code="FIFO-01", costing="FIFO")
        _receive(actor, item, warehouse, "1", "10", journal_entry=_anchor_je(actor, accounts, Decimal("10")))
        _issue(actor, item, warehouse, "3")
        assert _balance(company, item).qty_on_hand == Decimal("-2")

        res = _receive(actor, item, warehouse, "5", "12", journal_entry=_anchor_je(actor, accounts, Decimal("60")))
        assert res.success, res.error

        bal = _balance(company, item)
        assert bal.qty_on_hand == Decimal("3")
        layers = FifoLayer.objects.filter(company=company, item=item, qty_remaining__gt=0)
        # Only the above-zero remainder becomes a layer — sum(remaining)
        # must equal qty_on_hand, not exceed it.
        assert sum(layer.qty_remaining for layer in layers) == bal.qty_on_hand
        newest = layers.order_by("-sequence").first()
        assert newest.qty_original == Decimal("3")


class TestAdjustment:
    def test_adjustment_onto_negative_balance_ties_gl_to_subledger(self, actor, company, item, warehouse, accounts):
        from projections.write_barrier import projection_writes_allowed

        with projection_writes_allowed():
            adjustment_account = Account.objects.projection().create(
                company=company,
                code="59000",
                name="F18 Shrinkage",
                account_type=Account.AccountType.EXPENSE,
                status=Account.Status.ACTIVE,
            )

        _drive_to(actor, item, warehouse, accounts, "-1", "100")
        pre_value = _balance(company, item).stock_value

        res = adjust_inventory(
            actor=actor,
            adjustment_account_id=adjustment_account.id,
            adjustment_date=date(2026, 5, 11),
            reason="F18 count correction",
            lines=[{"item": item, "warehouse": warehouse, "qty_delta": Decimal("1"), "unit_cost": Decimal("77")}],
        )
        assert res.success, res.error

        bal = _balance(company, item)
        assert bal.qty_on_hand == Decimal("0")
        assert bal.stock_value == Decimal("0")

        # The adjustment JE moves exactly the SUBLEDGER delta (100), so
        # GL inventory keeps tying — the variance rides in the same JE.
        je = JournalEntry.objects.filter(company=company, memo__startswith="Inventory Adjustment:").latest("id")
        inv_line = je.lines.get(account=accounts["inventory"])
        assert inv_line.debit == bal.stock_value - pre_value == Decimal("100")


class TestRebuildParity:
    def test_rebuild_converges_with_command_state(self, actor, company, item, warehouse, accounts):
        from projections.inventory_balance import InventoryBalanceProjection

        _receive(actor, item, warehouse, "1", "100", journal_entry=_anchor_je(actor, accounts, Decimal("100")))
        _issue(actor, item, warehouse, "3")
        _receive(actor, item, warehouse, "4", "120", journal_entry=_anchor_je(actor, accounts, Decimal("480")))

        bal = _balance(company, item)
        # Sequence sanity: 1@100 → -2@100 (value -200) → +4@120: extinguish
        # 2 @ carried 100, remainder 2 @ 120.
        assert bal.qty_on_hand == Decimal("2")
        assert bal.avg_cost == Decimal("120")
        assert bal.stock_value == Decimal("240")
        snapshot = (bal.qty_on_hand, bal.avg_cost, bal.stock_value)

        projection = InventoryBalanceProjection()
        projection.rebuild(company)

        bal.refresh_from_db()
        assert (bal.qty_on_hand, bal.avg_cost, bal.stock_value) == snapshot, (
            "rebuild must reproduce command-time balances (shared costing math)"
        )

        verification = projection.verify_all_balances(company)
        assert verification["mismatches"] == [], verification
