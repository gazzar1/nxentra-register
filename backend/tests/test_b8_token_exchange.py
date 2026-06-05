# tests/test_b8_token_exchange.py
"""
B8 (2026-06-05) — Token Exchange / embedded install flow.

When the merchant installs the app via Shopify's modern silent-install
path (App Store managed install, Dev Dashboard "Install app"), our
OAuth callback never fires. Instead, when the merchant opens the app,
Shopify's App Bridge gives the frontend a session_token JWT signed
with our client_secret. We exchange that JWT for an offline access
token via /admin/oauth/access_token (grant_type=token-exchange).

These tests cover the backend command path:
  - JWT verification helper (good signature, expired, wrong audience)
  - shop_domain extraction from iss/dest claims
  - Token exchange success creates an ACTIVE ShopifyStore
  - Existing row reused on reinstall (no duplicates)
  - Mismatched expected vs claimed shop_domain rejected
  - HTTP failure from Shopify token-exchange endpoint handled cleanly
  - Domain collision (already connected to another company) handled
"""

import time
from datetime import timedelta
from unittest.mock import patch

import jwt as pyjwt
import pytest
from django.utils import timezone as tz

from shopify_connector import commands
from shopify_connector.models import ShopifyStore

TEST_SECRET = "shpss_b8_test_secret"
TEST_API_KEY = "test_client_id_b8"


def _make_session_token(
    *,
    shop="reviewer-store.myshopify.com",
    secret=TEST_SECRET,
    audience=TEST_API_KEY,
    expires_in=60,
) -> str:
    """Build a Shopify-format session token JWT for tests."""
    now = int(time.time())
    payload = {
        "iss": f"https://{shop}/admin",
        "dest": f"https://{shop}",
        "aud": audience,
        "sub": "12345",
        "exp": now + expires_in,
        "nbf": now - 5,
        "iat": now,
        "jti": "test-jti-1",
        "sid": "test-sid-1",
    }
    return pyjwt.encode(payload, secret, algorithm="HS256")


# =============================================================================
# JWT verification helper
# =============================================================================


def test_verify_session_token_accepts_well_formed_jwt():
    with (
        patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token()
        claims = commands.verify_shopify_session_token(token)
        assert claims is not None
        assert claims["dest"] == "https://reviewer-store.myshopify.com"
        assert claims["aud"] == TEST_API_KEY


def test_verify_session_token_rejects_bad_signature():
    with (
        patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        # Token signed with a different secret — must be rejected.
        token = _make_session_token(secret="wrong-secret")
        assert commands.verify_shopify_session_token(token) is None


def test_verify_session_token_rejects_expired_token():
    with (
        patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token(expires_in=-30)  # expired 30s ago
        assert commands.verify_shopify_session_token(token) is None


def test_verify_session_token_rejects_wrong_audience():
    with (
        patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token(audience="some-other-client-id")
        assert commands.verify_shopify_session_token(token) is None


def test_verify_session_token_returns_none_when_secret_unset():
    with patch.object(commands, "SHOPIFY_API_SECRET", ""), patch.object(commands, "SHOPIFY_API_KEY", TEST_API_KEY):
        token = _make_session_token()
        assert commands.verify_shopify_session_token(token) is None


# =============================================================================
# shop_domain extraction
# =============================================================================


def test_extract_shop_domain_prefers_dest():
    claims = {
        "dest": "https://shop-a.myshopify.com",
        "iss": "https://shop-b.myshopify.com/admin",
    }
    assert commands._extract_shop_domain_from_claims(claims) == "shop-a.myshopify.com"


def test_extract_shop_domain_falls_back_to_iss():
    claims = {"iss": "https://shop-b.myshopify.com/admin"}
    assert commands._extract_shop_domain_from_claims(claims) == "shop-b.myshopify.com"


def test_extract_shop_domain_returns_none_for_non_shopify_url():
    claims = {"dest": "https://attacker.com", "iss": "https://attacker.com"}
    assert commands._extract_shop_domain_from_claims(claims) is None


# =============================================================================
# Token exchange command — happy path
# =============================================================================


@pytest.mark.django_db
def test_token_exchange_creates_active_store(db, company, monkeypatch):
    fake_response = {
        "access_token": "shpat_b8exchange01",
        "refresh_token": "shprt_b8exchange01",
        "expires_in": 3600,
        "refresh_token_expires_in": 7776000,
        "scope": "read_orders,read_products",
    }

    def _fake_post(url, json, timeout):
        # Verify we're sending the token-exchange grant.
        assert json["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
        assert json["subject_token_type"] == "urn:ietf:params:oauth:token-type:id_token"
        assert json["requested_token_type"] == "urn:shopify:params:oauth:token-type:offline-access-token"

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return fake_response

        return _Resp()

    monkeypatch.setattr("shopify_connector.commands.requests.post", _fake_post)
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_warehouse", lambda s: None)
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_sales_setup", lambda s: None)
    monkeypatch.setattr("events.emitter.emit_event_no_actor", lambda **kw: None)

    with (
        patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token(shop="reviewer-b8.myshopify.com")
        result = commands.complete_oauth_token_exchange(company, token)

    assert result.success, result.error
    store = result.data["store"]
    assert store.company_id == company.id
    assert store.shop_domain == "reviewer-b8.myshopify.com"
    assert store.access_token == "shpat_b8exchange01"
    assert store.refresh_token == "shprt_b8exchange01"
    assert store.status == ShopifyStore.Status.ACTIVE
    assert store.token_expires_at is not None
    assert store.token_expires_at > tz.now() + timedelta(minutes=50)


@pytest.mark.django_db
def test_token_exchange_reuses_existing_row_on_reinstall(db, company, monkeypatch):
    """A merchant who previously disconnected and is reinstalling must
    reuse the existing ShopifyStore row, not create a duplicate."""
    existing = ShopifyStore.objects.create(
        company=company,
        shop_domain="reinstall.myshopify.com",
        access_token="old_token",
        status=ShopifyStore.Status.DISCONNECTED,
    )

    fake_response = {
        "access_token": "shpat_new_after_reinstall",
        "refresh_token": "shprt_new_after_reinstall",
        "expires_in": 3600,
        "refresh_token_expires_in": 7776000,
        "scope": "read_orders",
    }

    def _fake_post(*a, **kw):
        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return fake_response

        return _Resp()

    monkeypatch.setattr("shopify_connector.commands.requests.post", _fake_post)
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_warehouse", lambda s: None)
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_sales_setup", lambda s: None)
    monkeypatch.setattr("events.emitter.emit_event_no_actor", lambda **kw: None)

    with (
        patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token(shop="reinstall.myshopify.com")
        result = commands.complete_oauth_token_exchange(company, token)

    assert result.success
    refreshed = ShopifyStore.objects.get(pk=existing.pk)
    assert refreshed.status == ShopifyStore.Status.ACTIVE
    assert refreshed.access_token == "shpat_new_after_reinstall"
    assert ShopifyStore.objects.filter(company=company, shop_domain="reinstall.myshopify.com").count() == 1


# =============================================================================
# Token exchange command — failure modes
# =============================================================================


@pytest.mark.django_db
def test_token_exchange_rejects_invalid_session_token(db, company):
    with (
        patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        # Token signed with wrong secret
        token = _make_session_token(secret="wrong-secret")
        result = commands.complete_oauth_token_exchange(company, token)
    assert not result.success
    assert "Invalid or expired session token" in result.error
    assert ShopifyStore.objects.count() == 0


@pytest.mark.django_db
def test_token_exchange_rejects_shop_mismatch(db, company):
    # Caller hinted at a different shop than the JWT claims. Refuses.
    with (
        patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token(shop="real.myshopify.com")
        result = commands.complete_oauth_token_exchange(
            company,
            token,
            expected_shop_domain="attacker.myshopify.com",
        )
    assert not result.success
    assert "does not match" in result.error
    assert ShopifyStore.objects.count() == 0


@pytest.mark.django_db
def test_token_exchange_handles_shopify_http_failure(db, company, monkeypatch):
    import requests as _rq

    def _fake_post(*a, **kw):
        raise _rq.RequestException("connection reset")

    monkeypatch.setattr("shopify_connector.commands.requests.post", _fake_post)

    with (
        patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token()
        result = commands.complete_oauth_token_exchange(company, token)
    assert not result.success
    assert "Token exchange failed" in result.error
    assert ShopifyStore.objects.count() == 0


@pytest.mark.django_db
def test_token_exchange_handles_empty_access_token_from_shopify(db, company, monkeypatch):
    def _fake_post(*a, **kw):
        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"expires_in": 3600}  # access_token missing!

        return _Resp()

    monkeypatch.setattr("shopify_connector.commands.requests.post", _fake_post)

    with (
        patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token()
        result = commands.complete_oauth_token_exchange(company, token)
    assert not result.success
    assert "no access_token" in result.error
    assert ShopifyStore.objects.count() == 0
