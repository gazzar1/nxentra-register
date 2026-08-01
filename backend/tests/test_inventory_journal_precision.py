# tests/test_inventory_journal_precision.py
"""A3-PR2 final inventory-precision pass (§9).

Inventory adjustments and opening balances keep full-precision quantity and
unit-cost evidence while the General Ledger stores per-MOVEMENT two-decimal
books amounts (quantize_current_ledger_amount: 0.01, ROUND_HALF_EVEN — the
current constrained-pilot/schema contract, not the global policy). Founder
zero-books policy: a valid stock movement whose books amount rounds to 0.00
still moves quantity and writes stock evidence, but creates NO journal entry
and consumes no journal identity."""

from decimal import Decimal

import pytest
from django.utils import timezone

from accounting.models import Account, JournalEntry
from events.models import BusinessEvent
from inventory.commands import adjust_inventory, record_opening_balance
from inventory.models import StockLedgerEntry, Warehouse
from projections.models import InventoryBalance
from projections.write_barrier import command_writes_allowed, projection_writes_allowed
from sales.models import Item

pytestmark = pytest.mark.django_db


@pytest.fixture
def inv_setup(company, actor_context):
    """Warehouse + adjustment/equity accounts + an item factory."""
    with command_writes_allowed():
        warehouse = Warehouse.objects.create(company=company, code="MAIN", name="Main", is_default=True, is_active=True)
    with projection_writes_allowed():
        adjustment_account = Account.objects.projection().create(
            company=company,
            code="5900",
            name="Inventory Adjustment",
            account_type=Account.AccountType.EXPENSE,
            status=Account.Status.ACTIVE,
        )
        equity_account = Account.objects.projection().create(
            company=company,
            code="3900",
            name="Opening Balance Equity",
            account_type=Account.AccountType.EQUITY,
            status=Account.Status.ACTIVE,
        )

    def make_item(code):
        with projection_writes_allowed():
            inventory_account = Account.objects.projection().create(
                company=company,
                code=f"1{code[-3:]}",
                name=f"Inventory {code}",
                account_type=Account.AccountType.ASSET,
                status=Account.Status.ACTIVE,
            )
        with command_writes_allowed():
            return Item.objects.create(
                company=company,
                code=code,
                name=f"Item {code}",
                item_type=Item.ItemType.INVENTORY,
                default_cost=Decimal("0"),
                costing_method="WEIGHTED_AVERAGE",
                is_active=True,
                inventory_account=inventory_account,
            )

    return warehouse, adjustment_account, equity_account, make_item


def _je_totals(je):
    debits = sum((line.debit for line in je.lines.all()), Decimal("0"))
    credits = sum((line.credit for line in je.lines.all()), Decimal("0"))
    return debits, credits


class TestAdjustmentPrecision:
    def test_six_decimal_cost_books_two_decimal_je(self, company, actor_context, inv_setup):
        """§9.1: qty 1 @ 3.333333 → JE books 3.33 balanced; stock keeps 6dp."""
        warehouse, adjustment_account, _eq, make_item = inv_setup
        item = make_item("P901")

        result = adjust_inventory(
            actor_context,
            adjustment_date=timezone.now().date(),
            reason="precision",
            lines=[{"item": item, "warehouse": warehouse, "qty_delta": Decimal("1"), "unit_cost": Decimal("3.333333")}],
            adjustment_account_id=adjustment_account.id,
        )
        assert result.success, result.error
        je = result.data["journal_entry"]
        assert je is not None
        je.refresh_from_db()  # the command returns the pre-post instance
        assert je.status == JournalEntry.Status.POSTED
        debits, credits = _je_totals(je)
        assert debits == Decimal("3.33") and credits == Decimal("3.33")

        balance = InventoryBalance.objects.get(company=company, item=item)
        assert balance.qty_on_hand == Decimal("1")
        assert balance.avg_cost == Decimal("3.333333")
        sle = StockLedgerEntry.objects.get(company=company, item=item)
        assert sle.value_delta == Decimal("3.33")
        assert sle.unit_cost == Decimal("3.333333")

    def test_per_movement_quantization_not_aggregate(self, company, actor_context, inv_setup):
        """§9.3: two movements of raw 0.335 each — JE totals 0.68
        (sum of individually quantized movements), not quantize(0.67)."""
        warehouse, adjustment_account, _eq, make_item = inv_setup
        item_a, item_b = make_item("P90A"), make_item("P90B")

        result = adjust_inventory(
            actor_context,
            adjustment_date=timezone.now().date(),
            reason="per-movement",
            lines=[
                {"item": item_a, "warehouse": warehouse, "qty_delta": Decimal("1"), "unit_cost": Decimal("0.335")},
                {"item": item_b, "warehouse": warehouse, "qty_delta": Decimal("1"), "unit_cost": Decimal("0.335")},
            ],
            adjustment_account_id=adjustment_account.id,
        )
        assert result.success, result.error
        je = result.data["journal_entry"]
        debits, credits = _je_totals(je)
        assert debits == Decimal("0.68") and credits == Decimal("0.68")
        for sle in StockLedgerEntry.objects.filter(company=company):
            assert sle.value_delta == Decimal("0.34")

    def test_sub_cent_adjustment_moves_stock_without_je(self, company, actor_context, inv_setup):
        """§9.4: qty 1 @ 0.004 — quantity moves, stored delta 0.00, NO journal
        entry, no posted-journal event, success."""
        warehouse, adjustment_account, _eq, make_item = inv_setup
        item = make_item("P904")

        result = adjust_inventory(
            actor_context,
            adjustment_date=timezone.now().date(),
            reason="sub-cent",
            lines=[{"item": item, "warehouse": warehouse, "qty_delta": Decimal("1"), "unit_cost": Decimal("0.004")}],
            adjustment_account_id=adjustment_account.id,
        )
        assert result.success, result.error
        assert result.data["journal_entry"] is None
        assert not JournalEntry.objects.filter(company=company).exists()
        assert not BusinessEvent.objects.filter(company=company, event_type="journal_entry.posted").exists()
        # Stock truth preserved.
        balance = InventoryBalance.objects.get(company=company, item=item)
        assert balance.qty_on_hand == Decimal("1")
        assert balance.avg_cost == Decimal("0.004")
        sle = StockLedgerEntry.objects.get(company=company, item=item)
        assert sle.value_delta == Decimal("0.00")
        assert sle.journal_entry_id is None
        # The adjustment event still records truthfully (empty JE reference).
        event = BusinessEvent.objects.get(company=company, event_type="inventory.adjusted")
        assert event.get_data()["journal_entry_public_id"] == ""

    def test_mixed_zero_and_nonzero_movements(self, company, actor_context, inv_setup):
        """§9.5: all quantities move; JE contains only the nonzero books
        amounts; offsetting line matches; no phantom stock."""
        warehouse, adjustment_account, _eq, make_item = inv_setup
        item_zero, item_real = make_item("P90Z"), make_item("P90R")

        result = adjust_inventory(
            actor_context,
            adjustment_date=timezone.now().date(),
            reason="mixed",
            lines=[
                {"item": item_zero, "warehouse": warehouse, "qty_delta": Decimal("1"), "unit_cost": Decimal("0.004")},
                {"item": item_real, "warehouse": warehouse, "qty_delta": Decimal("2"), "unit_cost": Decimal("5.00")},
            ],
            adjustment_account_id=adjustment_account.id,
        )
        assert result.success, result.error
        je = result.data["journal_entry"]
        debits, credits = _je_totals(je)
        assert debits == Decimal("10.00") and credits == Decimal("10.00")
        assert je.lines.count() == 2  # one inventory line + one offset — no zero-value lines
        assert InventoryBalance.objects.get(company=company, item=item_zero).qty_on_hand == Decimal("1")
        assert InventoryBalance.objects.get(company=company, item=item_real).qty_on_hand == Decimal("2")
        assert StockLedgerEntry.objects.filter(company=company).count() == 2


class TestOpeningBalancePrecision:
    def test_six_decimal_cost_books_two_decimal_je(self, company, actor_context, inv_setup):
        """§9.2: qty 1 @ 3.333333 opening → JE 3.33 balanced; 6dp preserved."""
        warehouse, _adj, equity_account, make_item = inv_setup
        item = make_item("P902")

        result = record_opening_balance(
            actor_context,
            as_of_date=timezone.now().date(),
            lines=[{"item": item, "warehouse": warehouse, "qty": Decimal("1"), "unit_cost": Decimal("3.333333")}],
            opening_balance_equity_account_id=equity_account.id,
        )
        assert result.success, result.error
        je = result.data["journal_entry"]
        assert je is not None
        je.refresh_from_db()  # the command returns the pre-post instance
        assert je.status == JournalEntry.Status.POSTED
        debits, credits = _je_totals(je)
        assert debits == Decimal("3.33") and credits == Decimal("3.33")
        balance = InventoryBalance.objects.get(company=company, item=item)
        assert balance.avg_cost == Decimal("3.333333")
        sle = StockLedgerEntry.objects.get(company=company, item=item)
        assert sle.value_delta == Decimal("3.33")

    def test_sub_cent_opening_records_stock_without_je(self, company, actor_context, inv_setup):
        """§9.4 (opening variant): quantity recorded, no JE, no posted event,
        truthful inventory event with empty JE reference."""
        warehouse, _adj, equity_account, make_item = inv_setup
        item = make_item("P90S")

        result = record_opening_balance(
            actor_context,
            as_of_date=timezone.now().date(),
            lines=[{"item": item, "warehouse": warehouse, "qty": Decimal("1"), "unit_cost": Decimal("0.004")}],
            opening_balance_equity_account_id=equity_account.id,
        )
        assert result.success, result.error
        assert result.data["journal_entry"] is None
        assert not JournalEntry.objects.filter(company=company).exists()
        assert not BusinessEvent.objects.filter(company=company, event_type="journal_entry.posted").exists()
        balance = InventoryBalance.objects.get(company=company, item=item)
        assert balance.qty_on_hand == Decimal("1")
        assert balance.avg_cost == Decimal("0.004")
        event = BusinessEvent.objects.get(company=company, event_type="inventory.opening_balance")
        assert event.get_data()["journal_entry_public_id"] == ""

    def test_per_line_quantization_grouped_equity_credit(self, company, actor_context, inv_setup):
        """§9.3 (opening variant): equity credit equals the sum of the
        individually quantized stored inventory lines (0.68, not 0.67)."""
        warehouse, _adj, equity_account, make_item = inv_setup
        item_a, item_b = make_item("P90C"), make_item("P90D")

        result = record_opening_balance(
            actor_context,
            as_of_date=timezone.now().date(),
            lines=[
                {"item": item_a, "warehouse": warehouse, "qty": Decimal("1"), "unit_cost": Decimal("0.335")},
                {"item": item_b, "warehouse": warehouse, "qty": Decimal("1"), "unit_cost": Decimal("0.335")},
            ],
            opening_balance_equity_account_id=equity_account.id,
        )
        assert result.success, result.error
        je = result.data["journal_entry"]
        equity_line = je.lines.get(account=equity_account)
        assert equity_line.credit == Decimal("0.68")
