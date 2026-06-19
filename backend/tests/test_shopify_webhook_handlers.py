# tests/test_shopify_webhook_handlers.py
"""
Tests for Shopify webhook command handlers (process_order_paid,
process_order_pending, process_order_cancelled).

Regression coverage for payload edge cases the live Shopify integration
has surfaced.
"""

import pytest

from shopify_connector.commands import process_fulfillment, process_order_paid
from shopify_connector.models import ShopifyStore


@pytest.fixture
def shopify_store(db, company):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="webhook-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )


def test_process_order_paid_handles_null_customer(shopify_store):
    # Shopify sends "customer": null on draft orders that were marked-as-paid
    # without a customer attached (an admin can do this for B2B / wholesale
    # / test orders). dict.get("customer", {}) does NOT cover this — the key
    # exists but its value is None. The handler must coerce defensively or
    # crash with 'NoneType has no attribute get' downstream.
    payload = {
        "id": 9000001,
        "order_number": 1001,
        "name": "#1001",
        "created_at": "2026-04-28T08:30:00Z",
        "total_price": "500.00",
        "subtotal_price": "500.00",
        "total_tax": "0.00",
        "total_discounts": "0.00",
        "currency": "EGP",
        "financial_status": "paid",
        "customer": None,  # ← the null that bit us live
        "line_items": [],
        "shipping_lines": [],
        "transactions": [],
    }

    result = process_order_paid(shopify_store, payload)

    assert result.success, f"expected success, got error: {result.error}"


@pytest.mark.django_db
def test_process_fulfillment_handles_null_sku(shopify_store):
    from django.utils import timezone

    from shopify_connector.models import ShopifyFulfillment, ShopifyOrder

    order = ShopifyOrder.objects.create(
        company=shopify_store.company,
        store=shopify_store,
        shopify_order_id=9100001,
        shopify_order_number="1002",
        shopify_order_name="#1002",
        total_price="100.00",
        subtotal_price="100.00",
        total_tax="0.00",
        currency="EGP",
        financial_status="paid",
        shopify_created_at=timezone.now(),
        order_date=timezone.now().date(),
        raw_payload={},
    )
    payload = {
        "id": 9200001,
        "order_id": order.shopify_order_id,
        "created_at": "2026-06-18T14:22:42Z",
        "status": "success",
        "line_items": [{"sku": None, "title": "No SKU product", "quantity": 1}],
    }

    result = process_fulfillment(shopify_store, payload)

    assert result.success, f"expected success, got error: {result.error}"
    fulfillment = ShopifyFulfillment.objects.get(shopify_fulfillment_id=9200001)
    assert fulfillment.matched_items == 0
    assert fulfillment.total_items == 1
