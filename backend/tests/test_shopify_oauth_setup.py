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


# =============================================================================
# Onboarding finalization (accounts/commands.py::_finalize_shopify_stores)
# =============================================================================


def test_finalize_shopify_stores_wires_sales_routing_and_registers_webhooks(db, company, owner_membership, monkeypatch):
    # Why: at OAuth callback time SHOPIFY_CLEARING doesn't yet exist, so
    # _ensure_shopify_sales_setup short-circuits and the store is left
    # without a default_customer / posting_profile. The shopify_accounting
    # projection then silently no-ops on SHOPIFY_ORDER_PAID events (no
    # SalesInvoices, no JEs). complete_onboarding now invokes
    # _finalize_shopify_stores AFTER seeding the GL accounts so the store
    # is fully configured before historical import is enqueued.
    #
    # Surfaced live during A1 dry-run: Aljazeera2 + Aljazeera3 both hit
    # this and required a manual shell wire-up workaround to fix.
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account
    from accounts.authz import system_actor_for_company
    from accounts.commands import _finalize_shopify_stores
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from shopify_connector.models import ShopifyStore

    # Seed the SHOPIFY_CLEARING account that _ensure_shopify_sales_setup
    # depends on (in real onboarding this is done by _setup_shopify_accounts).
    with projection_writes_allowed():
        clearing = Account.objects.projection().create(
            company=company,
            code="11500",
            name="Shopify Clearing",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
    ModuleAccountMapping.objects.create(
        company=company,
        module="shopify_connector",
        role="SHOPIFY_CLEARING",
        account=clearing,
    )

    # Create an ACTIVE store missing both customer + webhooks (state right
    # after OAuth callback).
    with command_writes_allowed():
        store = ShopifyStore.objects.create(
            company=company,
            shop_domain="finalize-test.myshopify.com",
            access_token="test-token",
            status=ShopifyStore.Status.ACTIVE,
        )
    assert store.default_customer_id is None
    assert store.webhooks_registered is False

    # Mock the Shopify webhook-registration HTTP call so the test doesn't
    # try to talk to a real store. 201 = newly created.
    class _FakeResponse:
        status_code = 201
        text = ""

    def _post(*_a, **_kw):
        return _FakeResponse()

    monkeypatch.setattr("shopify_connector.commands.requests.post", _post)

    actor = system_actor_for_company(company)
    _finalize_shopify_stores(actor, company)

    store.refresh_from_db()
    assert store.default_customer_id is not None
    assert store.default_posting_profile_id is not None
    assert store.webhooks_registered is True


def test_finalize_shopify_stores_swallows_webhook_failures(db, company, owner_membership, monkeypatch):
    # Onboarding must not fail wholesale when Shopify's webhook API has a
    # bad day — we want the merchant's books to be set up regardless.
    # The helper should log a warning and continue.
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account
    from accounts.authz import system_actor_for_company
    from accounts.commands import _finalize_shopify_stores
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from shopify_connector.models import ShopifyStore

    with projection_writes_allowed():
        clearing = Account.objects.projection().create(
            company=company,
            code="11500",
            name="Shopify Clearing",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
    ModuleAccountMapping.objects.create(
        company=company,
        module="shopify_connector",
        role="SHOPIFY_CLEARING",
        account=clearing,
    )

    with command_writes_allowed():
        store = ShopifyStore.objects.create(
            company=company,
            shop_domain="finalize-fail.myshopify.com",
            access_token="test-token",
            status=ShopifyStore.Status.ACTIVE,
        )

    def _raise(*_a, **_kw):
        import requests

        raise requests.RequestException("simulated webhook API outage")

    monkeypatch.setattr("shopify_connector.commands.requests.post", _raise)

    actor = system_actor_for_company(company)

    # Should not raise — failures are logged as warnings.
    _finalize_shopify_stores(actor, company)

    store.refresh_from_db()
    # Sales routing should still have been wired up (it's a separate call).
    assert store.default_customer_id is not None
    # Webhooks couldn't register, so flag stays False.
    assert store.webhooks_registered is False
