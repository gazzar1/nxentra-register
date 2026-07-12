# tests/test_f13_cod_cogs_deferral.py
"""
F13 — COD orders book COGS at COLLECTION, not at fulfillment.

Egypt COD reality: an order is fulfilled (parcel ships) in month M and
collected — or refused — weeks later. Before this fix:
- COGS posted at fulfillment_date with NO payment gate, while revenue
  waited for mark-as-paid → margin split across periods;
- a refused parcel (orders/cancelled on a PENDING_CAPTURE order) never
  reversed the already-booked COGS: the zero-amount refund webhook died
  in process_refund and the restock path required a POSTED invoice that
  never existed. COGS JE stood, stock stayed decremented, forever.

Owner decision (2026-07-12): defer the whole COGS booking (JE + stock
issue) for unpaid-at-fulfillment orders to the paid moment, dated
paid_date; promote COD revenue to the same collection date. Refused
parcels then book nothing and reverse nothing. Already-paid orders
(cards, historical imports, A125 backfill) keep fulfillment-date COGS.
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.models import Account, JournalEntry
from inventory.models import StockLedgerEntry, Warehouse
from projections.models import InventoryBalance
from projections.write_barrier import command_writes_allowed, projection_writes_allowed
from sales.models import Item
from shopify_connector.commands import (
    process_fulfillment,
    process_order_cancelled,
    process_order_paid,
    process_order_pending,
)
from shopify_connector.models import ShopifyFulfillment, ShopifyOrder, ShopifyStore

pytestmark = pytest.mark.django_db


def _sweep(store):
    # Imported lazily: the sweep only exists post-F13, and a module-level
    # import would turn the pre-fix RED run into a collection error.
    from shopify_connector.tasks import _sweep_deferred_cogs

    return _sweep_deferred_cogs(store)


@pytest.fixture
def store(db, company, owner_membership):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="f13-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )


@pytest.fixture
def stocked_item(db, company):
    with projection_writes_allowed():
        cogs_account = Account.objects.projection().create(
            company=company,
            code="51000",
            name="F13 COGS",
            account_type=Account.AccountType.EXPENSE,
            status=Account.Status.ACTIVE,
        )
        inventory_account = Account.objects.projection().create(
            company=company,
            code="12000",
            name="F13 Inventory",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
    with command_writes_allowed():
        warehouse = Warehouse.objects.create(
            company=company,
            code="MAIN",
            name="Main Warehouse",
            is_default=True,
            is_active=True,
        )
        item = Item.objects.create(
            company=company,
            code="MUG-01",
            name="Coffee Mug",
            item_type=Item.ItemType.INVENTORY,
            default_unit_price=Decimal("250.00"),
            default_cost=Decimal("100.00"),
            costing_method="WEIGHTED_AVERAGE",
            is_active=True,
            cogs_account=cogs_account,
            inventory_account=inventory_account,
        )
    with projection_writes_allowed():
        InventoryBalance.objects.create(
            company=company,
            item=item,
            warehouse=warehouse,
            qty_on_hand=Decimal("5"),
            avg_cost=Decimal("100.00"),
            stock_value=Decimal("500.00"),
        )
    return item


ORDER_ID = 9130001
FULFILLMENT_ID = 9230001


def _order_payload(financial_status: str, created_at="2026-03-15T09:00:00Z", updated_at=None):
    payload = {
        "id": ORDER_ID,
        "order_number": 1301,
        "name": "#1301",
        "created_at": created_at,
        "total_price": "250.00",
        "subtotal_price": "250.00",
        "total_tax": "0.00",
        "total_discounts": "0.00",
        "currency": "EGP",
        "financial_status": financial_status,
        "gateway": "cash_on_delivery",
        "customer": None,
        "line_items": [{"sku": "MUG-01", "title": "Coffee Mug", "quantity": 1, "price": "250.00"}],
        "shipping_lines": [],
        "transactions": [],
    }
    if updated_at:
        payload["updated_at"] = updated_at
    return payload


def _fulfillment_payload(created_at="2026-03-20T10:00:00Z"):
    return {
        "id": FULFILLMENT_ID,
        "order_id": ORDER_ID,
        "created_at": created_at,
        "status": "success",
        "line_items": [{"sku": "MUG-01", "title": "Coffee Mug", "quantity": 1}],
    }


def _cogs_jes(company):
    return JournalEntry.objects.filter(company=company, memo__startswith="Shopify COGS:")


def _stock_issues(fulfillment):
    return StockLedgerEntry.objects.filter(company=fulfillment.company, source_id=str(fulfillment.public_id))


def _qty_on_hand(company, item):
    return InventoryBalance.objects.get(company=company, item=item).qty_on_hand


class TestCodDeferral:
    def test_cod_fulfillment_defers_cogs_until_collection(self, store, company, stocked_item):
        """Fulfilled in March, collected in April: COGS and revenue both
        date to April; nothing books in March."""
        res = process_order_pending(store, _order_payload("pending"))
        assert res.success and res.data.get("captured_pending"), res.error

        res = process_fulfillment(store, _fulfillment_payload("2026-03-20T10:00:00Z"))
        assert res.success, res.error
        fulfillment = ShopifyFulfillment.objects.get(company=company, shopify_fulfillment_id=FULFILLMENT_ID)

        # Nothing booked at fulfillment: no JE, no stock issue, qty intact.
        assert fulfillment.status == ShopifyFulfillment.Status.COGS_PENDING
        assert not _cogs_jes(company).exists()
        assert not _stock_issues(fulfillment).exists()
        assert _qty_on_hand(company, stocked_item) == Decimal("5")

        # Collection lands in April.
        res = process_order_paid(store, _order_payload("paid", updated_at="2026-04-05T16:00:00Z"))
        assert res.success, res.error
        assert res.data.get("deferred_cogs_booked") == 1

        je = _cogs_jes(company).get()
        assert je.status == JournalEntry.Status.POSTED
        assert je.date == date(2026, 4, 5), "COGS must date to collection, not fulfillment"

        fulfillment.refresh_from_db()
        assert fulfillment.status == ShopifyFulfillment.Status.PROCESSED
        assert fulfillment.journal_entry_id is not None
        assert _stock_issues(fulfillment).count() >= 1
        assert _qty_on_hand(company, stocked_item) == Decimal("4")

        # F13 recognition policy: promoted COD revenue dates to collection.
        order = ShopifyOrder.objects.get(company=company, shopify_order_id=ORDER_ID)
        assert order.order_date == date(2026, 4, 5)

    def test_refused_parcel_books_nothing(self, store, company, stocked_item):
        process_order_pending(store, _order_payload("pending"))
        process_fulfillment(store, _fulfillment_payload())
        fulfillment = ShopifyFulfillment.objects.get(company=company, shopify_fulfillment_id=FULFILLMENT_ID)
        assert fulfillment.status == ShopifyFulfillment.Status.COGS_PENDING

        res = process_order_cancelled(store, {"id": ORDER_ID, "cancelled_at": "2026-04-01T12:00:00Z"})
        assert res.success, res.error
        assert res.data.get("cancelled_fulfillments") == 1

        fulfillment.refresh_from_db()
        order = ShopifyOrder.objects.get(company=company, shopify_order_id=ORDER_ID)
        assert fulfillment.status == ShopifyFulfillment.Status.CANCELLED
        assert order.status == ShopifyOrder.Status.CANCELLED
        assert not _cogs_jes(company).exists(), "a refused parcel must never book COGS"
        assert _qty_on_hand(company, stocked_item) == Decimal("5")

        # The sweep must also skip cancelled fulfillments.
        assert _sweep(store) == 0
        assert not _cogs_jes(company).exists()

    def test_paid_webhook_replay_does_not_double_book(self, store, company, stocked_item):
        process_order_pending(store, _order_payload("pending"))
        process_fulfillment(store, _fulfillment_payload())
        process_order_paid(store, _order_payload("paid", updated_at="2026-04-05T16:00:00Z"))
        assert _cogs_jes(company).count() == 1

        # Webhook redelivery: idempotency skip, no second JE, no second issue.
        res = process_order_paid(store, _order_payload("paid", updated_at="2026-04-05T16:00:00Z"))
        assert res.success and res.data.get("skipped")
        assert _cogs_jes(company).count() == 1
        fulfillment = ShopifyFulfillment.objects.get(company=company, shopify_fulfillment_id=FULFILLMENT_ID)
        assert _stock_issues(fulfillment).count() == 1
        assert _qty_on_hand(company, stocked_item) == Decimal("4")

        # Sweep after everything booked: nothing left to do.
        assert _sweep(store) == 0
        assert _cogs_jes(company).count() == 1


class TestSweep:
    def test_sweep_books_stranded_pending_fulfillment(self, store, company, stocked_item):
        """Simulates a crash between the paid event and the inline deferred
        booking: order paid (event_id set) but the fulfillment is still
        COGS_PENDING. The 4h sweep must book exactly once."""
        process_order_pending(store, _order_payload("pending"))
        process_fulfillment(store, _fulfillment_payload())
        process_order_paid(store, _order_payload("paid", updated_at="2026-04-05T16:00:00Z"))

        # Rewind the fulfillment to COGS_PENDING and remove the booking —
        # equivalent state to a crash before _book_deferred_cogs ran.
        fulfillment = ShopifyFulfillment.objects.get(company=company, shopify_fulfillment_id=FULFILLMENT_ID)
        _cogs_jes(company).delete()
        _stock_issues(fulfillment).delete()
        with command_writes_allowed():
            fulfillment.status = ShopifyFulfillment.Status.COGS_PENDING
            fulfillment.journal_entry_id = None
            fulfillment.save(update_fields=["status", "journal_entry_id"])

        assert _sweep(store) == 1
        je = _cogs_jes(company).get()
        # order.order_date was promoted to the collection date — the sweep
        # dates the late booking there too.
        assert je.date == date(2026, 4, 5)
        fulfillment.refresh_from_db()
        assert fulfillment.status == ShopifyFulfillment.Status.PROCESSED

        assert _sweep(store) == 0
        assert _cogs_jes(company).count() == 1


class TestPaidOrdersUnchanged:
    def test_already_paid_order_books_cogs_at_fulfillment(self, store, company, stocked_item):
        """Card orders / historical imports / A125 backfill: no deferral —
        COGS books immediately, dated the fulfillment."""
        res = process_order_paid(store, _order_payload("paid", created_at="2026-03-15T09:00:00Z"))
        assert res.success, res.error

        res = process_fulfillment(store, _fulfillment_payload("2026-03-20T10:00:00Z"))
        assert res.success, res.error

        je = _cogs_jes(company).get()
        assert je.date == date(2026, 3, 20), "paid orders keep fulfillment-date COGS"
        fulfillment = ShopifyFulfillment.objects.get(company=company, shopify_fulfillment_id=FULFILLMENT_ID)
        assert fulfillment.status == ShopifyFulfillment.Status.PROCESSED
        assert _qty_on_hand(company, stocked_item) == Decimal("4")

    def test_direct_paid_order_keeps_created_at_dating(self, store, company):
        """No PENDING_CAPTURE stub → recognition dating unchanged."""
        res = process_order_paid(
            store, _order_payload("paid", created_at="2026-03-15T09:00:00Z", updated_at="2026-04-05T16:00:00Z")
        )
        assert res.success, res.error
        order = ShopifyOrder.objects.get(company=company, shopify_order_id=ORDER_ID)
        assert order.order_date == date(2026, 3, 15)
