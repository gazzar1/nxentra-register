# tests/test_shopify_b1_b2_store_filtering.py
"""
Regression: App Store rejection #3 root cause.

A reviewer's company ended up with multiple ShopifyStore rows from successive
OAuth attempts on different shop_domains (3 stale PENDING + 2 DISCONNECTED +
1 ACTIVE). The /api/shopify/store/ endpoint returned them in insertion order,
so stores[0] was the oldest PENDING row — the frontend's stores[0] pick made
the settings page show the Connect form even after a successful reconnect.

B1: the API hides PENDING rows and orders ACTIVE first.
B2: get_install_url sweeps PENDING rows older than 1h to keep that state
    from accumulating in the first place.
"""

from datetime import timedelta

import pytest
from django.utils import timezone as tz

from shopify_connector.commands import get_install_url
from shopify_connector.models import ShopifyStore


def _make_store(company, shop_domain, status, *, updated_offset=timedelta(0)):
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain=shop_domain,
        status=status,
    )
    if updated_offset:
        # auto_now overwrites updated_at on save(); set it directly.
        ShopifyStore.objects.filter(pk=store.pk).update(
            updated_at=tz.now() + updated_offset,
        )
        store.refresh_from_db()
    return store


# =============================================================================
# B1 — ShopifyStoreView filter + ordering
# =============================================================================


@pytest.mark.django_db
def test_store_view_hides_pending_rows(authenticated_client, company, owner_membership):
    # Simulates the Shopify_R state: oldest row is a stale PENDING, newest is
    # the actually-connected ACTIVE row. Before B1 stores[0] returned PENDING.
    _make_store(company, "stale-pending.myshopify.com", ShopifyStore.Status.PENDING)
    active = _make_store(company, "live-active.myshopify.com", ShopifyStore.Status.ACTIVE)

    resp = authenticated_client.get("/api/shopify/store/")
    assert resp.status_code == 200

    stores = resp.json()["stores"]
    domains = [s["shop_domain"] for s in stores]
    statuses = [s["status"] for s in stores]
    assert "stale-pending.myshopify.com" not in domains
    assert stores[0]["shop_domain"] == active.shop_domain
    assert "PENDING" not in statuses


@pytest.mark.django_db
def test_store_view_separates_inactive_stores(authenticated_client, company, owner_membership):
    # B4 contract: DISCONNECTED rows live in `inactive_stores`, never in
    # `stores`. The settings page reads `inactive_stores[0]` to render the
    # "previously connected to <shop>" hint.
    _make_store(
        company,
        "old-active.myshopify.com",
        ShopifyStore.Status.ACTIVE,
        updated_offset=timedelta(days=-2),
    )
    _make_store(
        company,
        "fresh-disconnected.myshopify.com",
        ShopifyStore.Status.DISCONNECTED,
        updated_offset=timedelta(minutes=-5),
    )

    body = authenticated_client.get("/api/shopify/store/").json()
    assert [s["status"] for s in body["stores"]] == ["ACTIVE"]
    assert [s["status"] for s in body["inactive_stores"]] == ["DISCONNECTED"]


@pytest.mark.django_db
def test_store_view_keeps_disconnected_when_no_live_row(authenticated_client, company, owner_membership):
    # No ACTIVE/ERROR row: DISCONNECTED rows surface only via
    # `inactive_stores`, and `connected` is False so callers don't treat
    # past history as a current connection.
    _make_store(company, "gone.myshopify.com", ShopifyStore.Status.DISCONNECTED)

    body = authenticated_client.get("/api/shopify/store/").json()
    assert body["connected"] is False
    assert body["stores"] == []
    assert body["inactive_stores"][0]["status"] == "DISCONNECTED"


@pytest.mark.django_db
def test_store_view_returns_empty_when_only_pending_rows(authenticated_client, company, owner_membership):
    # All-PENDING is the abandoned-OAuth case — the API should report nothing.
    _make_store(company, "a.myshopify.com", ShopifyStore.Status.PENDING)
    _make_store(company, "b.myshopify.com", ShopifyStore.Status.PENDING)

    body = authenticated_client.get("/api/shopify/store/").json()
    assert body == {"connected": False, "stores": [], "inactive_stores": []}


@pytest.mark.django_db
def test_store_view_connected_flag_requires_active_row(authenticated_client, company, owner_membership):
    # B4 contract: `connected: true` must mean an ACTIVE row exists. ERROR
    # alone doesn't count — the merchant has work to do before the
    # integration is functional.
    _make_store(company, "broken.myshopify.com", ShopifyStore.Status.ERROR)

    body = authenticated_client.get("/api/shopify/store/").json()
    assert body["connected"] is False
    # ERROR rows still surface in `stores` so the frontend can show the
    # error_message banner — they're "live" in the sense that the merchant
    # may need to re-auth, not in the sense of "we can sync right now".
    assert [s["status"] for s in body["stores"]] == ["ERROR"]


@pytest.mark.django_db
def test_store_view_active_ranks_before_error(authenticated_client, company, owner_membership):
    # When both ACTIVE and ERROR rows exist on the same company (different
    # shop_domains), the ACTIVE one must come first in `stores` so the
    # frontend picks it as the connection.
    _make_store(
        company,
        "broken.myshopify.com",
        ShopifyStore.Status.ERROR,
        updated_offset=timedelta(minutes=-1),
    )
    _make_store(
        company,
        "working.myshopify.com",
        ShopifyStore.Status.ACTIVE,
        updated_offset=timedelta(days=-1),
    )

    body = authenticated_client.get("/api/shopify/store/").json()
    assert body["connected"] is True
    assert [s["status"] for s in body["stores"]] == ["ACTIVE", "ERROR"]


# =============================================================================
# B3 — connect-twice must not downgrade an ACTIVE store
# =============================================================================


@pytest.mark.django_db
def test_connect_twice_does_not_downgrade_active_store(company):
    # The merchant re-clicks Connect on a shop_domain that's already ACTIVE
    # (scope-grant re-auth, recovery from a glitchy install). The row's
    # access_token must keep working until OAuth completes — otherwise we
    # silently disconnect them the moment they click the button.
    store = _make_store(company, "live.myshopify.com", ShopifyStore.Status.ACTIVE)
    store.access_token = "shpat_existing"
    store.save(update_fields=["access_token"])

    result = get_install_url(company, "live.myshopify.com")

    store.refresh_from_db()
    assert store.status == ShopifyStore.Status.ACTIVE
    assert store.access_token == "shpat_existing"
    # Nonce still rotates so the new OAuth callback can be matched.
    assert store.oauth_nonce == result["nonce"]


@pytest.mark.django_db
def test_connect_on_disconnected_store_goes_pending(company):
    # Reconnecting after disconnect is the legitimate "make me PENDING and
    # restart OAuth" path. The B3 guard only protects ACTIVE — DISCONNECTED
    # is treated as "no live connection" and rebuilt from scratch.
    _make_store(company, "back.myshopify.com", ShopifyStore.Status.DISCONNECTED)

    get_install_url(company, "back.myshopify.com")

    refreshed = ShopifyStore.objects.get(company=company, shop_domain="back.myshopify.com")
    assert refreshed.status == ShopifyStore.Status.PENDING


# =============================================================================
# B2 — stale PENDING sweep in get_install_url
# =============================================================================


@pytest.mark.django_db
def test_get_install_url_sweeps_stale_pending_for_other_domains(company):
    # PENDING for a previously abandoned shop_domain, older than 1h, gets
    # deleted when the merchant starts a fresh install on a different domain.
    _make_store(
        company,
        "abandoned.myshopify.com",
        ShopifyStore.Status.PENDING,
        updated_offset=timedelta(hours=-2),
    )

    get_install_url(company, "fresh.myshopify.com")

    domains = set(ShopifyStore.objects.filter(company=company).values_list("shop_domain", flat=True))
    assert domains == {"fresh.myshopify.com"}


@pytest.mark.django_db
def test_get_install_url_leaves_recent_pending_alone(company):
    # An in-flight OAuth (<1h old) on a different domain must survive: the
    # merchant might be mid-redirect and we don't want to invalidate their
    # nonce out from under them.
    _make_store(
        company,
        "in-flight.myshopify.com",
        ShopifyStore.Status.PENDING,
        updated_offset=timedelta(minutes=-10),
    )

    get_install_url(company, "fresh.myshopify.com")

    assert ShopifyStore.objects.filter(
        company=company,
        shop_domain="in-flight.myshopify.com",
    ).exists()


@pytest.mark.django_db
def test_get_install_url_does_not_sweep_active_or_disconnected(company):
    # Sweep only touches PENDING. Old ACTIVE or DISCONNECTED rows are
    # legitimate state — connector history we must preserve.
    _make_store(
        company,
        "live.myshopify.com",
        ShopifyStore.Status.ACTIVE,
        updated_offset=timedelta(days=-30),
    )
    _make_store(
        company,
        "gone.myshopify.com",
        ShopifyStore.Status.DISCONNECTED,
        updated_offset=timedelta(days=-30),
    )

    get_install_url(company, "fresh.myshopify.com")

    domains = set(ShopifyStore.objects.filter(company=company).values_list("shop_domain", flat=True))
    assert domains == {
        "live.myshopify.com",
        "gone.myshopify.com",
        "fresh.myshopify.com",
    }


@pytest.mark.django_db
def test_get_install_url_refreshes_existing_pending_for_same_domain(company):
    # Re-attempting OAuth on the same domain (e.g. user closed the Shopify
    # tab and came back hours later) must refresh the existing PENDING row's
    # nonce rather than delete-and-recreate. The current-shop exclusion in
    # the sweep is what makes this work.
    existing = _make_store(
        company,
        "retry.myshopify.com",
        ShopifyStore.Status.PENDING,
        updated_offset=timedelta(hours=-3),
    )
    existing.oauth_nonce = "old-nonce"
    existing.save(update_fields=["oauth_nonce"])

    result = get_install_url(company, "retry.myshopify.com")

    refreshed = ShopifyStore.objects.get(pk=existing.pk)
    assert refreshed.oauth_nonce == result["nonce"]
    assert refreshed.oauth_nonce != "old-nonce"
    assert refreshed.status == ShopifyStore.Status.PENDING
