# tests/test_a122_rotating_tokens.py
"""
A122 (2026-06-02): Shopify deprecated non-expiring offline access tokens
(deadline 2027-01-01, partially enforced now). Our OAuth flow now requests
expiring offline tokens via `expiring=1`, stores `refresh_token` +
`token_expires_at` + `refresh_token_expires_at`, and refreshes the access
token before each Admin API call.

Also covers the new launch-handshake endpoint at /api/shopify/launch/
that Shopify hits when a merchant clicks "Open app" from their admin.
Without this endpoint, Shopify reports our app as
"application_cant_be_loaded_misconfigured" — which is what got us
re-rejected as ref 114779 on 2026-06-02.
"""

import hashlib
import hmac as hmac_lib
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone as tz

from shopify_connector import commands
from shopify_connector.models import ShopifyStore


@pytest.fixture
def store_with_legacy_token(db, company):
    """Pre-A122 store: permanent access_token, no expiry, no refresh_token."""
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="legacy.myshopify.com",
        access_token="shpat_legacy_permanent",
        status=ShopifyStore.Status.ACTIVE,
    )


@pytest.fixture
def store_with_rotating_token(db, company):
    """Post-A122 store: expiring access_token + refresh_token."""
    now = tz.now()
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="rotating.myshopify.com",
        access_token="shpat_current_access",
        refresh_token="shprt_current_refresh",
        token_expires_at=now + timedelta(seconds=3600),
        refresh_token_expires_at=now + timedelta(days=90),
        status=ShopifyStore.Status.ACTIVE,
    )


# =============================================================================
# _get_valid_access_token() behavior
# =============================================================================


def test_legacy_store_returns_access_token_unchanged(store_with_legacy_token):
    """Legacy non-expiring tokens shouldn't trigger a refresh — they have no
    expiry set, so the helper just returns access_token as-is. They keep
    working until Shopify cuts off permanent tokens entirely (deadline
    2027-01-01)."""
    token = commands._get_valid_access_token(store_with_legacy_token)
    assert token == "shpat_legacy_permanent"


def test_rotating_token_returned_when_not_yet_expiring(store_with_rotating_token):
    token = commands._get_valid_access_token(store_with_rotating_token)
    assert token == "shpat_current_access"


def test_rotating_token_triggers_refresh_when_expired(store_with_rotating_token):
    """When token_expires_at is in the past (or within 60-second buffer),
    a refresh call to Shopify is made before returning the token."""
    store_with_rotating_token.token_expires_at = tz.now() - timedelta(seconds=10)
    store_with_rotating_token.save(update_fields=["token_expires_at"])

    fake_response = {
        "access_token": "shpat_NEW_after_refresh",
        "refresh_token": "shprt_NEW_after_refresh",
        "expires_in": 3600,
        "refresh_token_expires_in": 7776000,
    }

    with patch("shopify_connector.commands.requests.post") as post:
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = fake_response

        token = commands._get_valid_access_token(store_with_rotating_token)

    assert token == "shpat_NEW_after_refresh"
    store_with_rotating_token.refresh_from_db()
    assert store_with_rotating_token.access_token == "shpat_NEW_after_refresh"
    assert store_with_rotating_token.refresh_token == "shprt_NEW_after_refresh"


def test_refresh_fails_when_refresh_token_itself_expired(store_with_rotating_token):
    store_with_rotating_token.token_expires_at = tz.now() - timedelta(seconds=10)
    store_with_rotating_token.refresh_token_expires_at = tz.now() - timedelta(days=1)
    store_with_rotating_token.save(update_fields=["token_expires_at", "refresh_token_expires_at"])

    # No request to Shopify should be made when refresh_token is already
    # known to be expired.
    with patch("shopify_connector.commands.requests.post") as post:
        token = commands._get_valid_access_token(store_with_rotating_token)
        assert token is None
        assert post.call_count == 0


def test_refresh_fails_when_no_refresh_token(store_with_rotating_token):
    """If somehow the store has token_expires_at set but no refresh_token
    (shouldn't happen in normal flow), refresh returns False without making
    a request, and the helper returns None."""
    store_with_rotating_token.refresh_token = ""
    store_with_rotating_token.token_expires_at = tz.now() - timedelta(seconds=10)
    store_with_rotating_token.save(update_fields=["refresh_token", "token_expires_at"])

    with patch("shopify_connector.commands.requests.post") as post:
        token = commands._get_valid_access_token(store_with_rotating_token)
        assert token is None
        assert post.call_count == 0


# =============================================================================
# complete_oauth requests expiring=1
# =============================================================================


def test_complete_oauth_sends_expiring_param_and_stores_refresh_token(db, company, monkeypatch):
    """OAuth code exchange must send `expiring=1` in the request body, and
    the response's refresh_token / expires_in must be persisted on the
    store row."""
    nonce = "test-nonce-1234"
    ShopifyStore.objects.create(
        company=company,
        shop_domain="newconnect.myshopify.com",
        oauth_nonce=nonce,
        status=ShopifyStore.Status.PENDING,
    )

    fake_response = {
        "access_token": "shpat_initial",
        "refresh_token": "shprt_initial",
        "scope": "read_orders,read_products",
        "expires_in": 3600,
        "refresh_token_expires_in": 7776000,
    }

    captured = {}

    def _fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        from types import SimpleNamespace

        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: fake_response,
        )

    monkeypatch.setattr("shopify_connector.commands.requests.post", _fake_post)
    # The OAuth completion also calls warehouse + sales-routing setup —
    # stub those out so this test stays focused on token handling.
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_warehouse", lambda _s: None)
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_sales_setup", lambda _s: None)
    monkeypatch.setattr("events.emitter.emit_event_no_actor", lambda **_kw: None)

    result = commands.complete_oauth(company, "newconnect.myshopify.com", "the-code", nonce)

    assert result.success
    assert captured["body"]["expiring"] == 1
    assert captured["body"]["code"] == "the-code"

    store = ShopifyStore.objects.get(shop_domain="newconnect.myshopify.com")
    assert store.access_token == "shpat_initial"
    assert store.refresh_token == "shprt_initial"
    assert store.token_expires_at is not None
    assert store.refresh_token_expires_at is not None
    # token_expires_at should be roughly now + 3600s
    delta = (store.token_expires_at - tz.now()).total_seconds()
    assert 3590 < delta < 3610


# =============================================================================
# Shopify launch handshake (/api/shopify/launch/)
# =============================================================================


def _hmac_for(params: dict, secret: str) -> str:
    """Build the HMAC the same way the launch view does."""
    sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_lib.new(
        secret.encode("utf-8"),
        sorted_params.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def test_launch_handshake_rejects_missing_hmac(client):
    resp = client.get("/api/shopify/launch/", {"shop": "x.myshopify.com"})
    assert resp.status_code == 400


def test_launch_handshake_rejects_bad_hmac(client, settings):
    settings.SHOPIFY_API_SECRET = "test-secret"
    resp = client.get(
        "/api/shopify/launch/",
        {"shop": "x.myshopify.com", "hmac": "deadbeef"},
    )
    assert resp.status_code == 400


def test_launch_handshake_redirects_to_settings_when_store_connected(client, settings, db, company):
    settings.SHOPIFY_API_SECRET = "test-secret"
    ShopifyStore.objects.create(
        company=company,
        shop_domain="connected.myshopify.com",
        access_token="shpat_x",
        status=ShopifyStore.Status.ACTIVE,
    )

    params = {"shop": "connected.myshopify.com", "timestamp": "1234567890"}
    params["hmac"] = _hmac_for(params, "test-secret")

    resp = client.get("/api/shopify/launch/", params)
    assert resp.status_code == 302
    assert "/shopify/settings" in resp["Location"]
    assert "shop=connected.myshopify.com" in resp["Location"]


def test_launch_handshake_redirects_with_shop_hint_when_not_connected(client, settings):
    settings.SHOPIFY_API_SECRET = "test-secret"
    params = {"shop": "fresh.myshopify.com", "timestamp": "1234567890"}
    params["hmac"] = _hmac_for(params, "test-secret")

    resp = client.get("/api/shopify/launch/", params)
    assert resp.status_code == 302
    assert "/shopify/settings" in resp["Location"]
    assert "shop=fresh.myshopify.com" in resp["Location"]


def test_launch_handshake_decodes_host_when_shop_missing(client, settings, db, company):
    """When Shopify only sends `host` (base64 of admin URL) without `shop`,
    the view decodes it to derive the shop domain."""
    import base64

    settings.SHOPIFY_API_SECRET = "test-secret"
    ShopifyStore.objects.create(
        company=company,
        shop_domain="decoded.myshopify.com",
        access_token="shpat_x",
        status=ShopifyStore.Status.ACTIVE,
    )

    host_plain = "admin.shopify.com/store/decoded"
    host_encoded = base64.urlsafe_b64encode(host_plain.encode("utf-8")).decode("utf-8").rstrip("=")
    params = {"host": host_encoded, "timestamp": "1234567890"}
    params["hmac"] = _hmac_for(params, "test-secret")

    resp = client.get("/api/shopify/launch/", params)
    assert resp.status_code == 302
    assert "decoded.myshopify.com" in resp["Location"]
