# tests/test_shopify_oauth_setup.py
"""
Tests for the Shopify OAuth callback setup helpers (_ensure_shopify_warehouse).

Regression: the warehouse setup must complete without violating the projection
write guard. A prior bug had the is_default backfill block sitting outside the
command_writes_allowed() context, causing the OAuth callback to 500 on every
new connection (Warehouse is a projection-owned model).
"""

import pytest

from shopify_connector.commands import _ensure_shopify_warehouse
from shopify_connector.models import ShopifyStore


@pytest.fixture
def shopify_store(db, company):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="oauth-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )


def test_ensure_warehouse_falls_back_and_marks_default_when_locations_api_fails(shopify_store, monkeypatch):
    # When the Shopify locations API fails (network error / bad token), the
    # helper falls back to creating a single generic SHOPIFY warehouse and
    # then marks it as default. Both writes must succeed under the
    # projection-write guard.
    from inventory.models import Warehouse

    def _raise(*_a, **_kw):
        raise RuntimeError("simulated Shopify API failure")

    monkeypatch.setattr("shopify_connector.commands.requests.get", _raise)

    _ensure_shopify_warehouse(shopify_store)

    warehouses = Warehouse.objects.filter(company=shopify_store.company)
    assert warehouses.count() == 1
    assert warehouses.filter(is_default=True).exists()
