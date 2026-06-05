# tests/test_b6_shopify_initiated_install.py
"""
Regression: B6 (2026-06-05) — Shopify-initiated install was not
supported by our OAuth callback.

When the reviewer (or any real merchant) clicked Install from the App
Store listing or Partner Dashboard test install, Shopify ran OAuth and
called /api/shopify/callback/?code=...&shop=...&state=... with a state
nonce *Shopify* generated. Our callback required a PENDING ShopifyStore
row keyed on (shop_domain, oauth_nonce) — those rows are only created
by get_install_url, which is called from the Nxentra-initiated Connect
form. No PENDING row → callback returned HTTP 400 → no store ever got
connected → reviewer would see the broken state we surfaced on
2026-06-04.

B6 fix: when no PENDING row matches, verify the callback's HMAC
(Shopify signs OAuth callbacks), exchange the code for tokens
immediately (codes are single-use, short-lived), and stash everything
in a PendingShopifyInstall record. Redirect through the auth chain to
/shopify/finalize-install?handle=<uuid>, where the now-authenticated
merchant's company is associated with the saved tokens via the
finalize endpoint.
"""

import hashlib
import hmac as hmac_lib
from datetime import timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from django.test import override_settings
from django.utils import timezone as tz

from shopify_connector import commands
from shopify_connector.models import PendingShopifyInstall, ShopifyStore

TEST_SECRET = "shpss_test_secret_for_b6"


def _signed_callback_params(shop: str, code: str, state: str = "") -> dict:
    """Build a callback URL's query params with a valid HMAC signature.
    Mirrors what Shopify generates when redirecting to our callback."""
    params = {"code": code, "shop": shop, "timestamp": "1717600000"}
    if state:
        params["state"] = state
    sorted_msg = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sig = hmac_lib.new(
        TEST_SECRET.encode("utf-8"),
        sorted_msg.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    params["hmac"] = sig
    return params


# =============================================================================
# HMAC verification helper
# =============================================================================


@override_settings(SHOPIFY_API_SECRET=TEST_SECRET)
def test_verify_oauth_hmac_accepts_valid_signature():
    # The bare HMAC helper is the security gate for Shopify-initiated
    # installs (no state nonce to validate). It must accept the exact
    # signature Shopify computes from the sorted-params canonical string.
    with patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET):
        params = _signed_callback_params("test-shop.myshopify.com", "auth-code-1")
        assert commands.verify_shopify_oauth_hmac(params) is True


def test_verify_oauth_hmac_rejects_tampered_signature():
    # If any param is tampered after signing, the recomputed HMAC must
    # differ — that's the whole point of the gate.
    with patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET):
        params = _signed_callback_params("test-shop.myshopify.com", "auth-code-1")
        params["shop"] = "attacker.myshopify.com"  # tamper
        assert commands.verify_shopify_oauth_hmac(params) is False


def test_verify_oauth_hmac_rejects_missing_hmac():
    with patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET):
        assert commands.verify_shopify_oauth_hmac({"shop": "x.myshopify.com"}) is False


# =============================================================================
# Phase 1 — complete_oauth_shopify_initiated stashes tokens in a PendingShopifyInstall
# =============================================================================


@pytest.mark.django_db
def test_complete_oauth_shopify_initiated_creates_pending_install_row(monkeypatch):
    # The Phase 1 command exchanges the OAuth code for tokens and saves
    # them in a PendingShopifyInstall for later finalization. No
    # ShopifyStore row should exist yet — that requires a company,
    # which we don't have until the merchant logs in.
    fake_response = {
        "access_token": "shpat_b6test01",
        "refresh_token": "shprt_b6test01",
        "expires_in": 3600,
        "refresh_token_expires_in": 7776000,
        "scope": "read_orders,read_products",
    }

    def _fake_post(url, json, timeout):
        class _Resp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return fake_response

        return _Resp()

    monkeypatch.setattr("shopify_connector.commands.requests.post", _fake_post)

    result = commands.complete_oauth_shopify_initiated(
        "reviewer-store.myshopify.com",
        "auth-code-xyz",
    )
    assert result.success, result.error

    pending = result.data["pending"]
    assert isinstance(pending.public_id, UUID)
    assert pending.shop_domain == "reviewer-store.myshopify.com"
    assert pending.access_token == "shpat_b6test01"
    assert pending.refresh_token == "shprt_b6test01"
    assert pending.token_expires_at is not None
    assert pending.refresh_token_expires_at is not None
    assert pending.expires_at > tz.now() + timedelta(minutes=25)  # ~30 min TTL
    assert pending.consumed_at is None

    # No ShopifyStore row should exist yet — the install isn't bound
    # to a company until finalize runs.
    assert ShopifyStore.objects.filter(shop_domain="reviewer-store.myshopify.com").count() == 0


@pytest.mark.django_db
def test_complete_oauth_shopify_initiated_fails_when_token_exchange_errors(monkeypatch):
    class _RequestErr(Exception):
        pass

    def _fake_post(*a, **kw):
        import requests as _rq

        raise _rq.RequestException("boom")

    monkeypatch.setattr("shopify_connector.commands.requests.post", _fake_post)

    result = commands.complete_oauth_shopify_initiated("x.myshopify.com", "code")
    assert not result.success
    assert "Failed to exchange OAuth code" in result.error
    assert PendingShopifyInstall.objects.count() == 0


# =============================================================================
# Phase 2 — finalize_shopify_install binds the pending tokens to a company
# =============================================================================


def _make_pending_install(shop="reviewer.myshopify.com", expired=False) -> PendingShopifyInstall:
    return PendingShopifyInstall.objects.create(
        shop_domain=shop,
        access_token="shpat_b6test02",
        refresh_token="shprt_b6test02",
        token_expires_at=tz.now() + timedelta(hours=1),
        refresh_token_expires_at=tz.now() + timedelta(days=90),
        scopes="read_orders,read_products",
        expires_at=tz.now() - timedelta(minutes=1) if expired else tz.now() + timedelta(minutes=30),
    )


@pytest.mark.django_db
def test_finalize_creates_active_store_for_company(db, company, monkeypatch):
    # Skip the heavy post-install side-effects (warehouse + sales setup
    # + event emission) — they have their own tests; here we only care
    # that the store row is created in the right state.
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_warehouse", lambda s: None)
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_sales_setup", lambda s: None)
    monkeypatch.setattr("events.emitter.emit_event_no_actor", lambda **kw: None)

    pending = _make_pending_install()
    result = commands.finalize_shopify_install(company, str(pending.public_id))

    assert result.success, result.error
    store = result.data["store"]
    assert store.company_id == company.id
    assert store.shop_domain == pending.shop_domain
    assert store.access_token == pending.access_token
    assert store.refresh_token == pending.refresh_token
    assert store.status == ShopifyStore.Status.ACTIVE

    pending.refresh_from_db()
    assert pending.consumed_at is not None
    assert pending.consumed_by_company_id == company.id


@pytest.mark.django_db
def test_finalize_reuses_existing_store_row_on_reinstall(db, company, monkeypatch):
    # If the merchant previously disconnected and is reinstalling, we
    # should reuse the existing ShopifyStore row (same id) rather than
    # creating a duplicate. Same pattern as complete_oauth.
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_warehouse", lambda s: None)
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_sales_setup", lambda s: None)
    monkeypatch.setattr("events.emitter.emit_event_no_actor", lambda **kw: None)

    existing = ShopifyStore.objects.create(
        company=company,
        shop_domain="reviewer.myshopify.com",
        access_token="old_token",
        status=ShopifyStore.Status.DISCONNECTED,
    )
    pending = _make_pending_install()

    result = commands.finalize_shopify_install(company, str(pending.public_id))
    assert result.success

    refreshed = ShopifyStore.objects.get(pk=existing.pk)
    assert refreshed.status == ShopifyStore.Status.ACTIVE
    assert refreshed.access_token == pending.access_token
    # Same row id — no duplicates
    assert ShopifyStore.objects.filter(company=company, shop_domain="reviewer.myshopify.com").count() == 1


@pytest.mark.django_db
def test_finalize_rejects_expired_pending(db, company):
    pending = _make_pending_install(expired=True)
    result = commands.finalize_shopify_install(company, str(pending.public_id))
    assert not result.success
    assert "expired" in result.error.lower()
    assert ShopifyStore.objects.filter(shop_domain=pending.shop_domain).count() == 0


@pytest.mark.django_db
def test_finalize_rejects_consumed_pending(db, company, monkeypatch):
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_warehouse", lambda s: None)
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_sales_setup", lambda s: None)
    monkeypatch.setattr("events.emitter.emit_event_no_actor", lambda **kw: None)

    pending = _make_pending_install()
    # First finalize succeeds
    r1 = commands.finalize_shopify_install(company, str(pending.public_id))
    assert r1.success
    # Second attempt with the same handle must reject
    r2 = commands.finalize_shopify_install(company, str(pending.public_id))
    assert not r2.success


@pytest.mark.django_db
def test_finalize_unknown_handle_returns_failure(db, company):
    result = commands.finalize_shopify_install(company, str(uuid4()))
    assert not result.success
