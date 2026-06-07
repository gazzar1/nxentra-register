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
