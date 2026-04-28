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


def test_auto_created_item_from_shopify_line_gets_all_four_gl_accounts(db, company, monkeypatch):
    # Why: when a Shopify webhook order arrives with a SKU we don't yet
    # have an Item for, _auto_create_item_from_line creates one. The
    # newly-created Item must have sales / purchase / inventory / cogs
    # accounts auto-filled from the company's shopify_connector
    # ModuleAccountMapping — otherwise the merchant's books are
    # incomplete: COGS won't book on fulfillment, the Item edit page
    # shows None for all four, etc. The defaults are sensible starting
    # points; the merchant can override per-item later by editing the Item.
    #
    # Surfaced live during A1 dry-run: HEAD-001 auto-created from
    # Shopify orders had Sales/Purchase/Inventory/COGS = None across
    # multiple test companies (Aljazeera2, 3, 4) because the previous
    # implementation looked for accounts at codes 1300/5100 instead of
    # the 13000/51000 created by _setup_shopify_accounts, AND the
    # ModuleAccountMapping for INVENTORY/COGS roles was never read.
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from sales.models import Item
    from shopify_connector.commands import _auto_create_item_from_line
    from shopify_connector.models import ShopifyStore

    # Seed the four accounts + mappings the way _setup_shopify_accounts would.
    with projection_writes_allowed():
        sales_acct = Account.objects.projection().create(
            company=company,
            code="41000",
            name="Sales Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
        clearing_acct = Account.objects.projection().create(
            company=company,
            code="11500",
            name="Shopify Clearing",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        inventory_acct = Account.objects.projection().create(
            company=company,
            code="13000",
            name="Inventory",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        cogs_acct = Account.objects.projection().create(
            company=company,
            code="51000",
            name="Cost of Goods Sold",
            account_type=Account.AccountType.EXPENSE,
            status=Account.Status.ACTIVE,
        )
    for role, account in [
        ("SALES_REVENUE", sales_acct),
        ("SHOPIFY_CLEARING", clearing_acct),
        ("INVENTORY", inventory_acct),
        ("COGS", cogs_acct),
    ]:
        ModuleAccountMapping.objects.create(
            company=company,
            module="shopify_connector",
            role=role,
            account=account,
        )

    with command_writes_allowed():
        store = ShopifyStore.objects.create(
            company=company,
            shop_domain="auto-item-test.myshopify.com",
            access_token="test-token",
            status=ShopifyStore.Status.ACTIVE,
        )

    # Skip the cost-fetch and currency-conversion API calls.
    monkeypatch.setattr(
        "shopify_connector.commands._fetch_variant_cost",
        lambda *_a, **_kw: __import__("decimal").Decimal("250.00"),
    )
    monkeypatch.setattr(
        "shopify_connector.commands._get_shopify_store_currency",
        lambda _store: "EGP",
    )

    line_item = {
        "title": "Head-phones",
        "price": "500.00",
        "sku": "HEAD-001",
        "variant_id": 999,
        "product_id": 888,
    }
    _auto_create_item_from_line(store, "HEAD-001", line_item)

    item = Item.objects.get(company=company, code="HEAD-001")
    assert item.sales_account_id == sales_acct.id, "Sales account should default from SALES_REVENUE mapping"
    assert item.inventory_account_id == inventory_acct.id, "Inventory account should default from INVENTORY mapping"
    assert item.cogs_account_id == cogs_acct.id, "COGS account should default from COGS mapping"
    # Purchase defaults to inventory account for stocked items — user can override later.
    assert item.purchase_account_id == inventory_acct.id, (
        "Purchase account should default to inventory for stocked items"
    )


def test_auto_create_and_update_defaults_never_overwrite_user_customizations(db, company, monkeypatch):
    # Why: a merchant may edit an auto-created Item and re-point its GL
    # accounts to custom ones (e.g. "Headphones Revenue" instead of the
    # generic "Sales Revenue", or a separate inventory sub-account per
    # category). Subsequent Shopify activity — order webhooks bringing in
    # the same SKU, manual product re-syncs — must NEVER overwrite those
    # customizations. Defaults are sticky for new items, user changes are
    # sticky for existing items: fill-if-empty, never overwrite.
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from sales.models import Item
    from shopify_connector.commands import _auto_create_item_from_line, _update_item_defaults
    from shopify_connector.models import ShopifyStore

    # Seed Shopify default accounts + mappings (what onboarding would do).
    with projection_writes_allowed():
        default_sales = Account.objects.projection().create(
            company=company,
            code="41000",
            name="Sales Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
        default_inventory = Account.objects.projection().create(
            company=company,
            code="13000",
            name="Inventory",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        default_cogs = Account.objects.projection().create(
            company=company,
            code="51000",
            name="Cost of Goods Sold",
            account_type=Account.AccountType.EXPENSE,
            status=Account.Status.ACTIVE,
        )
        # Custom user-chosen accounts that DIFFER from the defaults.
        custom_sales = Account.objects.projection().create(
            company=company,
            code="41001",
            name="Headphones Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
        custom_inventory = Account.objects.projection().create(
            company=company,
            code="13001",
            name="Audio Inventory",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        custom_cogs = Account.objects.projection().create(
            company=company,
            code="51001",
            name="Audio COGS",
            account_type=Account.AccountType.EXPENSE,
            status=Account.Status.ACTIVE,
        )
    for role, account in [
        ("SALES_REVENUE", default_sales),
        ("INVENTORY", default_inventory),
        ("COGS", default_cogs),
    ]:
        ModuleAccountMapping.objects.create(
            company=company,
            module="shopify_connector",
            role=role,
            account=account,
        )

    with command_writes_allowed():
        store = ShopifyStore.objects.create(
            company=company,
            shop_domain="preserve-test.myshopify.com",
            access_token="test-token",
            status=ShopifyStore.Status.ACTIVE,
        )
        # Merchant manually created (or edited) an Item with custom accounts.
        existing_item = Item.objects.create(
            company=company,
            code="HEAD-001",
            name="Head-phones (manually configured)",
            item_type="INVENTORY",
            default_unit_price=500,
            sales_account=custom_sales,
            inventory_account=custom_inventory,
            cogs_account=custom_cogs,
            purchase_account=custom_inventory,
            costing_method="WEIGHTED_AVERAGE",
            is_active=True,
        )

    monkeypatch.setattr(
        "shopify_connector.commands._fetch_variant_cost",
        lambda *_a, **_kw: __import__("decimal").Decimal("250.00"),
    )
    monkeypatch.setattr(
        "shopify_connector.commands._get_shopify_store_currency",
        lambda _store: "EGP",
    )

    # Path 1: a Shopify webhook comes in with HEAD-001. The auto-create
    # helper must short-circuit because the Item already exists — it does
    # not touch GL accounts on existing items.
    _auto_create_item_from_line(
        store,
        "HEAD-001",
        {
            "title": "Head-phones",
            "price": "500.00",
            "sku": "HEAD-001",
            "variant_id": 999,
            "product_id": 888,
        },
    )

    # Path 2: a manual product re-sync calls _update_item_defaults against
    # the existing item with the company-level defaults. Each assignment
    # is gated on `not item.<account>` — already-set fields stay put.
    _update_item_defaults(
        existing_item,
        cost=__import__("decimal").Decimal("250.00"),
        inv_account=default_inventory,
        cogs_account=default_cogs,
        sales_account=default_sales,
        purchase_account=default_inventory,
    )

    existing_item.refresh_from_db()
    assert existing_item.sales_account_id == custom_sales.id, "Custom sales account must survive auto-create + update"
    assert existing_item.inventory_account_id == custom_inventory.id, "Custom inventory account must survive"
    assert existing_item.cogs_account_id == custom_cogs.id, "Custom COGS account must survive"
    assert existing_item.purchase_account_id == custom_inventory.id, "Custom purchase account must survive"
    # And exactly one Item exists for this SKU — no duplicate created.
    assert Item.objects.filter(company=company, code="HEAD-001").count() == 1


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
