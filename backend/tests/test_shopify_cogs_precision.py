# tests/test_shopify_cogs_precision.py
"""A3-PR2 final disposition pass — Shopify fulfillment COGS precision.

The financial journal and inventory valuation have DIFFERENT precision
contracts: stock costing keeps full precision (6dp average costs, exact
valuation movement) while the General Ledger books ONE two-decimal amount per
item under the current constrained-pilot ledger contract (EGP-only,
JournalLine persists (18,2), explicit ROUND_HALF_EVEN). Pre-fix, a
six-decimal average cost flowed raw into the COGS journal payload; the strict
emit representation rule then rejected the otherwise balanced journal, the
best-effort helper logged-and-returned, and the fulfillment committed with
COGS never posting.

`ShopifyFulfillment.total_cogs` is factually the BOOKED financial amount (the
column is DecimalField(18,2)): these tests pin it equal to the posted
journal's debit and credit totals — the sum of individually quantized
per-line books amounts, never a quantized aggregate.
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.models import Account, JournalEntry
from events.models import BusinessEvent
from inventory.models import StockLedgerEntry, Warehouse
from projections.models import InventoryBalance
from projections.write_barrier import command_writes_allowed, projection_writes_allowed
from sales.models import Item
from shopify_connector.commands import process_fulfillment, process_order_paid, process_order_pending
from shopify_connector.models import ShopifyFulfillment, ShopifyStore

pytestmark = pytest.mark.django_db

ORDER_ID = 9140001
FULFILLMENT_ID = 9240001


@pytest.fixture
def store(db, company, owner_membership):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="cogs-precision.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )


def _make_item(company, code, avg_cost, *, qty="10", item_type=Item.ItemType.INVENTORY):
    """An item whose InventoryBalance carries a (possibly 6dp) average cost."""
    with projection_writes_allowed():
        cogs_account = Account.objects.projection().create(
            company=company,
            code=f"5{code[-4:]}",
            name=f"COGS {code}",
            account_type=Account.AccountType.EXPENSE,
            status=Account.Status.ACTIVE,
        )
        inventory_account = Account.objects.projection().create(
            company=company,
            code=f"1{code[-4:]}",
            name=f"Inventory {code}",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
    with command_writes_allowed():
        warehouse, _ = Warehouse.objects.get_or_create(
            company=company,
            code="MAIN",
            defaults={"name": "Main Warehouse", "is_default": True, "is_active": True},
        )
        item = Item.objects.create(
            company=company,
            code=code,
            name=f"Item {code}",
            item_type=item_type,
            default_unit_price=Decimal("250.00"),
            default_cost=Decimal("0"),
            costing_method="WEIGHTED_AVERAGE",
            is_active=True,
            cogs_account=cogs_account if item_type == Item.ItemType.INVENTORY else None,
            inventory_account=inventory_account if item_type == Item.ItemType.INVENTORY else None,
        )
    if item_type == Item.ItemType.INVENTORY:
        with projection_writes_allowed():
            InventoryBalance.objects.create(
                company=company,
                item=item,
                warehouse=warehouse,
                qty_on_hand=Decimal(qty),
                avg_cost=Decimal(avg_cost),
                stock_value=Decimal(qty) * Decimal(avg_cost),
            )
    return item


def _order_payload(financial_status, line_items, order_id=ORDER_ID):
    return {
        "id": order_id,
        "order_number": 1401,
        "name": "#1401",
        "created_at": "2026-03-15T09:00:00Z",
        "total_price": "250.00",
        "subtotal_price": "250.00",
        "total_tax": "0.00",
        "total_discounts": "0.00",
        "currency": "EGP",
        "financial_status": financial_status,
        "gateway": "cash_on_delivery" if financial_status == "pending" else "card",
        "customer": None,
        "line_items": line_items,
        "shipping_lines": [],
        "transactions": [],
    }


def _fulfillment_payload(line_items, fulfillment_id=FULFILLMENT_ID, order_id=ORDER_ID):
    return {
        "id": fulfillment_id,
        "order_id": order_id,
        "created_at": "2026-03-20T10:00:00Z",
        "status": "success",
        "line_items": line_items,
    }


def _cogs_je(company):
    return JournalEntry.objects.filter(company=company, memo__startswith="Shopify COGS:")


def _assert_booked(company, fulfillment, expected: Decimal):
    """The §3 semantic-A proof: total_cogs == JE debit total == JE credit
    total == sum of persisted JournalLine amounts."""
    je = _cogs_je(company).get()
    assert je.status == JournalEntry.Status.POSTED
    debits = sum((line.debit for line in je.lines.all()), Decimal("0"))
    credits = sum((line.credit for line in je.lines.all()), Decimal("0"))
    assert debits == expected
    assert credits == expected
    fulfillment.refresh_from_db()
    assert fulfillment.total_cogs == expected
    return je


class TestSixDecimalCogs:
    def test_six_decimal_average_cost_books_two_decimal_je(self, store, company):
        """§4.1: qty 3 × 3.333333 = 9.999999 full stock cost → JE books 10.00
        on both sides; posts cleanly; stock costing keeps full precision."""
        item = _make_item(company, "PREC-01", "3.333333")
        assert process_order_paid(store, _order_payload("paid", [{"sku": "PREC-01", "quantity": 3}])).success
        res = process_fulfillment(store, _fulfillment_payload([{"sku": "PREC-01", "quantity": 3}]))
        assert res.success, res.error
        assert res.data["total_cogs"] == Decimal("10.00")

        fulfillment = ShopifyFulfillment.objects.get(company=company, shopify_fulfillment_id=FULFILLMENT_ID)
        je = _assert_booked(company, fulfillment, Decimal("10.00"))
        line_values = sorted((line.debit, line.credit) for line in je.lines.all())
        assert line_values == [(Decimal("0.00"), Decimal("10.00")), (Decimal("10.00"), Decimal("0.00"))]
        # §4.8 zero-residue: no DRAFT/INCOMPLETE journals anywhere.
        assert not JournalEntry.objects.filter(
            company=company, status__in=[JournalEntry.Status.DRAFT, JournalEntry.Status.INCOMPLETE]
        ).exists()
        assert fulfillment.status == ShopifyFulfillment.Status.PROCESSED
        # Stock evidence keeps full precision: avg_cost survives at 6dp.
        balance = InventoryBalance.objects.get(company=company, item=item)
        assert balance.avg_cost == Decimal("3.333333")
        assert balance.qty_on_hand == Decimal("7")

    def test_multi_line_per_item_quantization_not_aggregate(self, store, company):
        """§4.2: two items at raw 0.335 each — sum(quantize(each)) = 0.68,
        while quantize(sum) would be 0.67. Headers must equal the sum of the
        persisted per-line amounts (0.68) and balance exactly."""
        _make_item(company, "PREC-A1", "0.335")
        _make_item(company, "PREC-B1", "0.335")
        lines = [{"sku": "PREC-A1", "quantity": 1}, {"sku": "PREC-B1", "quantity": 1}]
        assert process_order_paid(store, _order_payload("paid", lines)).success
        res = process_fulfillment(store, _fulfillment_payload(lines))
        assert res.success, res.error

        fulfillment = ShopifyFulfillment.objects.get(company=company, shopify_fulfillment_id=FULFILLMENT_ID)
        je = _assert_booked(company, fulfillment, Decimal("0.68"))
        assert je.lines.count() == 4  # DR+CR per item, individually quantized

    def test_two_decimal_costs_unchanged(self, store, company):
        """§4.3: already-2dp costs book identically to the pre-fix behavior."""
        _make_item(company, "PREC-C1", "100.00")
        assert process_order_paid(store, _order_payload("paid", [{"sku": "PREC-C1", "quantity": 2}])).success
        res = process_fulfillment(store, _fulfillment_payload([{"sku": "PREC-C1", "quantity": 2}]))
        assert res.success, res.error
        fulfillment = ShopifyFulfillment.objects.get(company=company, shopify_fulfillment_id=FULFILLMENT_ID)
        _assert_booked(company, fulfillment, Decimal("200.00"))


class TestDeferredCodPrecision:
    def test_deferred_cod_books_two_decimal_at_collection(self, store, company):
        """§4.4: COGS stays deferred before collection; collection books the
        correct 2dp journal at the collection date; timing policy unchanged."""
        item = _make_item(company, "PREC-D1", "3.333333")
        assert process_order_pending(store, _order_payload("pending", [{"sku": "PREC-D1", "quantity": 3}])).success
        res = process_fulfillment(store, _fulfillment_payload([{"sku": "PREC-D1", "quantity": 3}]))
        assert res.success, res.error
        fulfillment = ShopifyFulfillment.objects.get(company=company, shopify_fulfillment_id=FULFILLMENT_ID)
        assert fulfillment.status == ShopifyFulfillment.Status.COGS_PENDING
        assert fulfillment.total_cogs == Decimal("10.00")  # booked-amount semantics even while deferred
        assert not _cogs_je(company).exists()

        paid = _order_payload("pending", [{"sku": "PREC-D1", "quantity": 3}])
        paid["financial_status"] = "paid"
        paid["updated_at"] = "2026-04-05T16:00:00Z"
        assert process_order_paid(store, paid).success

        je = _assert_booked(company, fulfillment, Decimal("10.00"))
        assert je.date == date(2026, 4, 5), "collection-dated COGS timing must be unchanged"
        balance = InventoryBalance.objects.get(company=company, item=item)
        assert balance.avg_cost == Decimal("3.333333")

    def test_sweep_books_stranded_fulfillment_exactly_once(self, store, company):
        """§4.5: a paid order with a stranded COGS_PENDING fulfillment is
        booked exactly once by the sweep; repeats are no-ops."""
        from shopify_connector.tasks import _sweep_deferred_cogs

        _make_item(company, "PREC-E1", "3.333333")
        assert process_order_pending(store, _order_payload("pending", [{"sku": "PREC-E1", "quantity": 3}])).success
        assert process_order_paid(
            store, _order_payload("paid", [{"sku": "PREC-E1", "quantity": 3}])
        ).success  # order collected BEFORE the fulfillment arrives
        res = process_fulfillment(store, _fulfillment_payload([{"sku": "PREC-E1", "quantity": 3}]))
        assert res.success, res.error
        fulfillment = ShopifyFulfillment.objects.get(company=company, shopify_fulfillment_id=FULFILLMENT_ID)

        if fulfillment.status != ShopifyFulfillment.Status.PROCESSED:
            assert _sweep_deferred_cogs(store) == 1
        assert _sweep_deferred_cogs(store) == 0  # idempotent re-run

        fulfillment.refresh_from_db()
        assert fulfillment.status == ShopifyFulfillment.Status.PROCESSED
        assert _cogs_je(company).count() == 1
        assert StockLedgerEntry.objects.filter(company=company, source_id=str(fulfillment.public_id)).count() == 1


class TestIdempotencyAndOptionB:
    def test_repeated_fulfillment_webhook_books_once(self, store, company):
        """§4.7: an already-booked fulfillment is skipped — one JE, one
        posted event, one stock issue, no extra sequences."""
        _make_item(company, "PREC-F1", "3.333333")
        assert process_order_paid(store, _order_payload("paid", [{"sku": "PREC-F1", "quantity": 3}])).success
        assert process_fulfillment(store, _fulfillment_payload([{"sku": "PREC-F1", "quantity": 3}])).success
        events_after_first = BusinessEvent.objects.filter(company=company).count()

        again = process_fulfillment(store, _fulfillment_payload([{"sku": "PREC-F1", "quantity": 3}]))
        assert again.success
        assert again.data.get("skipped") is True
        assert _cogs_je(company).count() == 1
        assert BusinessEvent.objects.filter(company=company).count() == events_after_first
        fulfillment = ShopifyFulfillment.objects.get(company=company, shopify_fulfillment_id=FULFILLMENT_ID)
        assert StockLedgerEntry.objects.filter(company=company, source_id=str(fulfillment.public_id)).count() == 1

    def test_non_stock_item_books_nothing(self, store, company):
        """§4.10 (Option B / ISOLATED_SHADOW_LEDGER_V1): a NON_STOCK item
        produces no COGS JE, no stock mutation, and no COGS_PENDING residue
        — the precision correction does not re-arm inventory accounting."""
        _make_item(company, "PREC-N1", "0", item_type=Item.ItemType.NON_STOCK)
        assert process_order_pending(store, _order_payload("pending", [{"sku": "PREC-N1", "quantity": 1}])).success
        res = process_fulfillment(store, _fulfillment_payload([{"sku": "PREC-N1", "quantity": 1}]))
        assert res.success, res.error

        fulfillment = ShopifyFulfillment.objects.get(company=company, shopify_fulfillment_id=FULFILLMENT_ID)
        assert fulfillment.status != ShopifyFulfillment.Status.COGS_PENDING
        assert not _cogs_je(company).exists()
        assert not StockLedgerEntry.objects.filter(company=company).exists()
        assert fulfillment.total_cogs == Decimal("0.00")
