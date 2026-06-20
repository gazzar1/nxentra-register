# tests/test_a127b_product_webhook_price.py
"""
A127 follow-on — process_product_webhook must not silently revert a merchant's
manual price on an auto-created Item.

`auto_created` only records that WE created the Item, not that its price is
still ours. The webhook syncs Shopify's price only while the Item still matches
the last price we synced; once the merchant edits it (so the Item diverges from
the last-synced Shopify price), products/update leaves the price alone — but
still refreshes the name.
"""

from decimal import Decimal

import pytest

from projections.write_barrier import command_writes_allowed
from sales.models import Item
from shopify_connector.commands import process_product_webhook
from shopify_connector.models import ShopifyProduct, ShopifyStore


@pytest.fixture
def store(company):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="price-test.myshopify.com",
        access_token="t",
        status=ShopifyStore.Status.ACTIVE,
    )


def _make_mapped_item(company, store, *, item_price: str, last_synced_price: str):
    with command_writes_allowed():
        item = Item.objects.create(
            company=company,
            code="SNOW-1",
            name="Old name",
            item_type="INVENTORY",
            default_unit_price=Decimal(item_price),
            costing_method="WEIGHTED_AVERAGE",
            is_active=True,
        )
    ShopifyProduct.objects.create(
        company=company,
        store=store,
        shopify_product_id=555,
        shopify_variant_id=999,
        title="Snowboard",
        sku="SNOW-1",
        shopify_price=Decimal(last_synced_price),
        item=item,
        auto_created=True,
    )
    return item


def _payload(price: str) -> dict:
    return {
        "id": 555,
        "title": "Snowboard",
        "variants": [{"id": 999, "sku": "SNOW-1", "price": price, "title": "Default Title"}],
    }


@pytest.mark.django_db
def test_price_syncs_when_item_untouched(company, store):
    # Item price still equals the last-synced Shopify price → not merchant-owned.
    item = _make_mapped_item(company, store, item_price="50.00", last_synced_price="50.00")

    result = process_product_webhook(store, _payload("60.00"))

    assert result.success
    item.refresh_from_db()
    assert item.default_unit_price == Decimal("60.00")  # synced from Shopify
    assert item.name == "Snowboard"  # name refreshed too


@pytest.mark.django_db
def test_price_preserved_when_merchant_edited(company, store):
    # Merchant edited the Item price away from the last-synced Shopify price.
    item = _make_mapped_item(company, store, item_price="99.00", last_synced_price="50.00")

    result = process_product_webhook(store, _payload("70.00"))

    assert result.success
    item.refresh_from_db()
    assert item.default_unit_price == Decimal("99.00")  # merchant price preserved
    assert item.name == "Snowboard"  # name still refreshed
