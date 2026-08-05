# tests/test_pilot_inventory_inert.py
"""Constrained pilot (ISOLATED_SHADOW_LEDGER_V1) cannot reach inventory
accounting — runtime proof at the two Shopify execution boundaries.

The A4 suite already proves the static gates: item creation is forced
NON_STOCK with inventory/COGS accounts stripped, INVENTORY/COGS module
mappings cannot be armed, manual inventory items are blocked, and the
preflight detects StockLedgerEntry/FifoLayer/InventoryBalance/COGS_PENDING
residue. These tests close the remaining §runtime gap: even when the
webhook paths EXECUTE end-to-end for a pilot company, a fulfillment and a
refund restock produce zero inventory artifacts — no journal entry, no
posted-journal event, no stock ledger row, no inventory balance, no FIFO
layer, no deferred-COGS state.

The deferred inventory paths themselves are tracked as the Inventory
Books-Delta Foundation (docs/status/constrained_pilot_status.md § Open
architectural decisions); this module is the proof that the constrained
pilot does not depend on them.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.utils import timezone

from accounting.models import JournalEntry
from accounts.models import Company
from events.models import BusinessEvent
from inventory.models import FifoLayer, StockLedgerEntry
from projections.models import InventoryBalance
from sales.models import Item
from shopify_connector import commands as sc
from shopify_connector.models import ShopifyFulfillment, ShopifyOrder, ShopifyRefund, ShopifyStore

pytestmark = pytest.mark.django_db

ORDER_ID = 9410001
FULFILLMENT_ID = 9420001


def _make_pilot(company):
    company.pilot_profile = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.fiscal_year_start_month = 1
    company.save()
    from projections.models import FiscalPeriodConfig

    FiscalPeriodConfig.objects.get_or_create(company=company, fiscal_year=2026, defaults={"period_count": 13})
    return company


@pytest.fixture
def pilot_store(company, owner_membership):
    _make_pilot(company)
    return ShopifyStore.objects.create(
        company=company,
        shop_domain=f"pilot-inert-{uuid4().hex[:8]}.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
        shop_currency="EGP",
    )


@pytest.fixture
def pilot_item(company, pilot_store):
    """An item created through the REAL pilot creation path (product sync),
    which forces NON_STOCK and strips inventory/COGS accounts under A4."""
    sc._create_item_from_variant(
        company=company,
        sku="PILOT-1",
        product_title="Pilot Widget",
        variant_title="Default",
        price=Decimal("250.00"),
        cost=Decimal("100.00"),
        inv_account=None,
        cogs_account=None,
        sales_account=None,
        purchase_account=None,
    )
    item = Item.objects.get(company=company, code="PILOT-1")
    assert item.item_type == Item.ItemType.NON_STOCK
    assert item.inventory_account_id is None and item.cogs_account_id is None
    return item


def _assert_no_inventory_artifacts(company):
    assert not StockLedgerEntry.objects.filter(company=company).exists()
    assert not FifoLayer.objects.filter(company=company).exists()
    assert not InventoryBalance.objects.filter(company=company).exists()
    assert not JournalEntry.objects.filter(company=company, memo__startswith="Shopify COGS:").exists()
    assert not JournalEntry.objects.filter(company=company, memo__startswith="Shopify restock:").exists()
    assert not BusinessEvent.objects.filter(company=company, event_type="journal_entry.posted").exists()
    assert not ShopifyFulfillment.objects.filter(
        company=company, status=ShopifyFulfillment.Status.COGS_PENDING
    ).exists()


def _order_payload(financial_status):
    return {
        "id": ORDER_ID,
        "order_number": 1401,
        "name": "#1401",
        "created_at": "2026-03-15T09:00:00Z",
        "total_price": "250.00",
        "subtotal_price": "250.00",
        "total_tax": "0.00",
        "total_discounts": "0.00",
        "currency": "EGP",
        "financial_status": financial_status,
        "gateway": "cash_on_delivery",
        "customer": None,
        "line_items": [{"sku": "PILOT-1", "title": "Pilot Widget", "quantity": 1, "price": "250.00"}],
        "shipping_lines": [],
        "transactions": [],
    }


class TestPilotFulfillmentInert:
    def test_fulfillment_produces_no_inventory_artifacts(self, company, pilot_store, pilot_item):
        """A pilot fulfillment webhook executes end-to-end and books NOTHING
        inventory-shaped: the NON_STOCK item never matches a COGS line, so no
        JE, no posted event, no stock row, no balance, no FIFO layer and no
        deferred-COGS state exist afterwards — at fulfillment OR collection."""
        res = sc.process_order_pending(pilot_store, _order_payload("pending"))
        assert res.success, res.error

        res = sc.process_fulfillment(
            pilot_store,
            {
                "id": FULFILLMENT_ID,
                "order_id": ORDER_ID,
                "created_at": "2026-03-20T10:00:00Z",
                "status": "success",
                "line_items": [{"sku": "PILOT-1", "title": "Pilot Widget", "quantity": 1}],
            },
        )
        assert res.success, res.error
        assert res.data["total_cogs"] == Decimal("0")

        fulfillment = ShopifyFulfillment.objects.get(company=company, shopify_fulfillment_id=FULFILLMENT_ID)
        assert fulfillment.total_cogs == Decimal("0")
        assert fulfillment.journal_entry_id is None
        assert not BusinessEvent.objects.filter(company=company, event_type="shopify.order.fulfilled").exists(), (
            "no COGS event may be emitted for an unmatched (NON_STOCK) fulfillment"
        )
        _assert_no_inventory_artifacts(company)

        # Collection (COD paid) must not drain anything into inventory either.
        paid = _order_payload("paid")
        paid["updated_at"] = "2026-04-05T16:00:00Z"
        res = sc.process_order_paid(pilot_store, paid)
        assert res.success, res.error
        assert not res.data.get("deferred_cogs_booked")
        _assert_no_inventory_artifacts(company)


class TestPilotRestockInert:
    def test_refund_restock_produces_no_inventory_movement(self, company, pilot_store, pilot_item):
        """The refund-restock projection path executes for a pilot company and
        returns without any restock line (NON_STOCK item has no inventory/COGS
        accounts): no restock JE, no stock receipt, no posted event."""
        from shopify_connector.projections import ShopifyAccountingHandler

        order = ShopifyOrder.objects.create(
            company=company,
            store=pilot_store,
            shopify_order_id=ORDER_ID,
            shopify_order_number="1401",
            shopify_order_name="#1401",
            total_price=Decimal("250.00"),
            subtotal_price=Decimal("250.00"),
            currency="EGP",
            order_date=timezone.now(),
            shopify_created_at=timezone.now(),
        )
        refund = ShopifyRefund.objects.create(
            company=company,
            order=order,
            shopify_refund_id=9430001,
            amount=Decimal("250.00"),
            currency="EGP",
            shopify_created_at=timezone.now(),
            raw_payload={
                "refund_line_items": [
                    {
                        "restock_type": "return",
                        "quantity": 1,
                        "line_item": {"sku": "PILOT-1", "title": "Pilot Widget"},
                    }
                ]
            },
        )
        trigger = BusinessEvent.objects.create(
            company=company,
            event_type="shopify.refund.created",
            aggregate_type="ShopifyRefund",
            aggregate_id=str(refund.public_id),
            data={},
            idempotency_key=f"pilot.inert.refund:{uuid4()}",
            occurred_at=timezone.now(),
        )

        handler = ShopifyAccountingHandler()
        handler._handle_refund_restock(trigger, refund, None, date.today(), "EGP", Decimal("1.0"), False, None)

        _assert_no_inventory_artifacts(company)
        assert not JournalEntry.objects.filter(company=company).exists()
