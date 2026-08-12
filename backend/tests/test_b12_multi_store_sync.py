# tests/test_b12_multi_store_sync.py
"""
B12 (2026-06-07) — Sync endpoints must not crash when a company has more
than one ACTIVE ShopifyStore.

Discovered during the B8.5 live iframe smoke test on `shopify_r`:
clicking Sync Products in the embedded iframe returned a 500 because
`ShopifyStore.objects.get(company=..., status=ACTIVE)` raised
MultipleObjectsReturned for that company (it had two ACTIVE rows from
older test reconnections). The DB allows multiple ACTIVE rows for a
single company across different shop_domains; the partial unique
constraint only forbids two ACTIVE rows for the *same* shop_domain.

Fix: views fall back to the freshest ACTIVE row via the new
`_get_active_store_for_actor` helper. These tests pin that behavior:

  - With zero ACTIVE stores → 404, no crash
  - With one ACTIVE store → that store is used
  - With two ACTIVE stores on different shop_domains → no
    MultipleObjectsReturned; the most recently updated wins
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from shopify_connector.commands import CommandResult
from shopify_connector.models import ShopifyStore


def _auth(api_client, user):
    """Force-authenticate the DRF test client as `user` (bypasses cookies)."""
    api_client.force_authenticate(user=user)


@pytest.mark.django_db
def test_sync_products_returns_404_when_no_active_store(api_client, company, user, owner_membership):
    _auth(api_client, user)
    response = api_client.post(reverse("shopify-sync-products"), {}, format="json")
    assert response.status_code == 404
    assert "No active Shopify store" in response.data["error"]


@pytest.mark.django_db
def test_sync_products_uses_only_active_store(api_client, company, user, owner_membership, monkeypatch):
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="solo.myshopify.com",
        access_token="shpat_solo",
        status=ShopifyStore.Status.ACTIVE,
    )

    captured = {}

    def _fake_sync(store_arg, **kwargs):
        captured["store_id"] = store_arg.id
        return CommandResult.ok({"created": 0, "linked": 0})

    monkeypatch.setattr("shopify_connector.commands.sync_products", _fake_sync)

    _auth(api_client, user)
    response = api_client.post(reverse("shopify-sync-products"), {}, format="json")
    assert response.status_code == 200, response.data
    assert captured["store_id"] == store.id


@pytest.mark.django_db
def test_sync_products_picks_freshest_when_company_has_multiple_active_stores(
    api_client, company, user, owner_membership, monkeypatch
):
    """The pre-fix crash: two ACTIVE stores caused MultipleObjectsReturned.
    Post-fix: freshest-updated wins, no exception."""
    older = ShopifyStore.objects.create(
        company=company,
        shop_domain="older.myshopify.com",
        access_token="shpat_older",
        status=ShopifyStore.Status.ACTIVE,
    )
    newer = ShopifyStore.objects.create(
        company=company,
        shop_domain="newer.myshopify.com",
        access_token="shpat_newer",
        status=ShopifyStore.Status.ACTIVE,
    )
    # `auto_now` already gives `newer` a later updated_at, but be explicit
    # so the test isn't sensitive to row-creation timing on fast machines.
    ShopifyStore.objects.filter(pk=older.pk).update(updated_at=timezone.now() - timedelta(hours=1))

    captured = {}

    def _fake_sync(store_arg, **kwargs):
        captured["store_id"] = store_arg.id
        return CommandResult.ok({"created": 0, "linked": 0})

    monkeypatch.setattr("shopify_connector.commands.sync_products", _fake_sync)

    _auth(api_client, user)
    response = api_client.post(reverse("shopify-sync-products"), {}, format="json")
    assert response.status_code == 200, response.data
    assert captured["store_id"] == newer.id


@pytest.mark.django_db
def test_sync_payouts_handles_multiple_active_stores(api_client, company, user, owner_membership, monkeypatch):
    """Same crash class on the Sync Payouts endpoint."""
    older = ShopifyStore.objects.create(
        company=company,
        shop_domain="older.myshopify.com",
        access_token="shpat_older",
        status=ShopifyStore.Status.ACTIVE,
    )
    newer = ShopifyStore.objects.create(
        company=company,
        shop_domain="newer.myshopify.com",
        access_token="shpat_newer",
        status=ShopifyStore.Status.ACTIVE,
    )
    ShopifyStore.objects.filter(pk=older.pk).update(updated_at=timezone.now() - timedelta(hours=1))

    captured = {}

    def _fake_sync(store_arg, **kwargs):
        captured["store_id"] = store_arg.id
        return CommandResult.ok({"created": 0, "skipped": 0})

    monkeypatch.setattr("shopify_connector.commands.sync_payouts", _fake_sync)

    _auth(api_client, user)
    response = api_client.post(reverse("shopify-sync-payouts"), {}, format="json")
    assert response.status_code == 200, response.data
    assert captured["store_id"] == newer.id


@pytest.mark.django_db
def test_resync_orders_handles_multiple_active_stores(api_client, company, user, owner_membership, monkeypatch):
    """Same crash class on the Re-sync Orders endpoint."""
    older = ShopifyStore.objects.create(
        company=company,
        shop_domain="older.myshopify.com",
        access_token="shpat_older",
        status=ShopifyStore.Status.ACTIVE,
    )
    newer = ShopifyStore.objects.create(
        company=company,
        shop_domain="newer.myshopify.com",
        access_token="shpat_newer",
        status=ShopifyStore.Status.ACTIVE,
    )
    ShopifyStore.objects.filter(pk=older.pk).update(updated_at=timezone.now() - timedelta(hours=1))

    captured = {}

    def _fake_sync_orders(store_arg, *_args, **_kwargs):
        captured["store_id"] = store_arg.id
        return {"created": 0, "skipped": 0}

    monkeypatch.setattr("shopify_connector.tasks._sync_orders", _fake_sync_orders)

    _auth(api_client, user)
    response = api_client.post(
        reverse("shopify-resync-orders"),
        {"days": 7},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert captured["store_id"] == newer.id


@pytest.mark.django_db
def test_disconnected_store_does_not_mask_active_one(api_client, company, user, owner_membership, monkeypatch):
    """DISCONNECTED rows must not be considered. With one ACTIVE and one
    DISCONNECTED, the ACTIVE one is used regardless of updated_at order."""
    active = ShopifyStore.objects.create(
        company=company,
        shop_domain="active.myshopify.com",
        access_token="shpat_active",
        status=ShopifyStore.Status.ACTIVE,
    )
    # Make the disconnected row freshest — proves status filter wins.
    disconnected = ShopifyStore.objects.create(
        company=company,
        shop_domain="dead.myshopify.com",
        access_token="shpat_dead",
        status=ShopifyStore.Status.DISCONNECTED,
    )
    ShopifyStore.objects.filter(pk=active.pk).update(updated_at=timezone.now() - timedelta(hours=1))
    assert disconnected.updated_at >= active.updated_at  # sanity

    captured = {}

    def _fake_sync(store_arg, **kwargs):
        captured["store_id"] = store_arg.id
        return CommandResult.ok({"created": 0, "linked": 0})

    monkeypatch.setattr("shopify_connector.commands.sync_products", _fake_sync)

    _auth(api_client, user)
    response = api_client.post(reverse("shopify-sync-products"), {}, format="json")
    assert response.status_code == 200, response.data
    assert captured["store_id"] == active.id


# =============================================================================
# A4 — the scheduled tenant/RLS execution context is a functional no-op on
# SQLite (the local battery), so it never changes task behavior there. The real
# RLS enforcement is proven on PostgreSQL in
# tests/e2e/test_a4_shopify_scheduled_rls.py.
# =============================================================================


@pytest.mark.django_db
def test_shopify_tenant_execution_is_a_noop_on_sqlite(company):
    """On SQLite the RLS session primitives are no-ops, so
    `_shopify_tenant_execution` must still route (via the tenant contextvar),
    yield the tenant info, and leave the Company readable without touching any
    Postgres session GUC."""
    from django.db import connection

    from accounts.models import Company
    from shopify_connector.tasks import _reassert_shopify_rls, _shopify_tenant_execution

    if connection.vendor == "postgresql":
        pytest.skip("SQLite-only behavior check; PostgreSQL is covered by the e2e RLS suite")

    with _shopify_tenant_execution(company.id) as tenant_info:
        assert tenant_info["is_shared"] is True
        # RLS session readers are no-ops on SQLite (return None / False), never raise.
        from accounts import rls

        assert rls.get_current_company_id(conn=connection) is None
        assert rls.is_rls_bypassed(conn=connection) is False
        # Ordinary ORM access is unaffected.
        assert Company.objects.get(pk=company.id).id == company.id
        _reassert_shopify_rls()  # no-op, must not raise
        assert Company.objects.get(pk=company.id).id == company.id


@pytest.mark.django_db
def test_scheduled_orders_task_still_processes_orders_on_sqlite(company, monkeypatch):
    """The real sync_shopify_store_orders task — now wrapped in
    `_shopify_tenant_execution` — still processes multiple orders end to end on
    SQLite, proving the tenant-context wrapping did not change task behavior."""
    import events.emitter as emitter
    from shopify_connector import commands as cmds
    from shopify_connector.models import ShopifyOrder, ShopifyStore
    from shopify_connector.tasks import sync_shopify_store_orders

    # Focus on the command layer (admission + row/event write); suppress the
    # downstream projection dispatch (it owns its own context and needs a full
    # chart of accounts, out of scope for this behavior check).
    monkeypatch.setattr(emitter, "_schedule_projection_processing", lambda *a, **k: None)

    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="sqlite-sched.myshopify.com",
        access_token="tok",
        status=ShopifyStore.Status.ACTIVE,
    )

    def _order(oid, amount):
        return {
            "id": oid,
            "currency": company.default_currency,
            "created_at": "2026-08-01T10:00:00Z",
            "updated_at": "2026-08-01T10:00:00Z",
            "total_price": amount,
            "subtotal_price": amount,
            "total_tax": "0",
            "total_discounts": "0",
            "financial_status": "paid",
            "order_number": str(oid),
            "name": f"#{oid}",
            "line_items": [],
            "customer": {},
            "transactions": [],
            "shipping_lines": [],
        }

    class _FakeClient:
        def iter_orders(self, a, b):
            yield from [_order(95001, "100.00"), _order(95002, "150.00")]

        def get_order_fulfillments(self, oid):
            return []

        def get_order_refunds(self, oid):
            return []

    monkeypatch.setattr(cmds, "_admin_client", lambda s: _FakeClient())

    result = sync_shopify_store_orders(store.id)

    assert result["status"] == "ok", result
    assert result["created"] == 2, result
    assert ShopifyOrder.objects.filter(company=company, shopify_order_id__in=[95001, 95002]).count() == 2
