# tests/test_b8_5_shopify_session_login.py
"""
B8.5 (2026-06-05) — Shopify embedded session-login endpoint.

The embedded landing page can't authenticate via HttpOnly cookies (SameSite
blocks them inside the cross-site Shopify admin iframe). It instead grabs a
session_token from App Bridge and POSTs it to this endpoint, which verifies
the JWT against our client_secret, looks up the connected ShopifyStore by
shop_domain, and mints a Nxentra JWT pair bound to the store's company OWNER.

Coverage:
  - happy path mints tokens + sets cookies + sets active_company
  - missing session_token -> 400
  - invalid signature -> 401
  - shop with no ACTIVE ShopifyStore -> 404 no_connection
  - DISCONNECTED store ignored (same as no store)
  - store exists but no OWNER membership -> 500
  - multiple ACTIVE stores -> picks most recently updated
  - inactive OWNER ignored, falls back to no_owner
"""

import time
from unittest.mock import patch
from uuid import uuid4

import jwt as pyjwt
import pytest
from django.urls import reverse

from accounts.models import CompanyMembership
from shopify_connector import commands as sc_commands
from shopify_connector.models import ShopifyStore
from shopify_connector.user_binding import bind_shopify_user

TEST_SECRET = "shpss_b85_test_secret"
TEST_API_KEY = "test_client_id_b85"
SUB = "98765"  # matches the `sub` claim minted by _make_session_token


def _bind(store, membership):
    """A1: session-login now authenticates the explicitly bound Shopify user."""
    return bind_shopify_user(store=store, shopify_sub=SUB, membership=membership, actor_user=membership.user)


def _make_session_token(
    *,
    shop="merchant.myshopify.com",
    secret=TEST_SECRET,
    audience=TEST_API_KEY,
    expires_in=60,
) -> str:
    now = int(time.time())
    payload = {
        "iss": f"https://{shop}/admin",
        "dest": f"https://{shop}",
        "aud": audience,
        "sub": "98765",
        "exp": now + expires_in,
        "nbf": now - 5,
        "iat": now,
        "jti": f"jti-{uuid4().hex[:8]}",
        "sid": f"sid-{uuid4().hex[:8]}",
    }
    return pyjwt.encode(payload, secret, algorithm="HS256")


def _url():
    return reverse("accounts:shopify-session-login")


# =============================================================================
# Happy path
# =============================================================================


@pytest.mark.django_db
def test_session_login_mints_tokens_for_owner(api_client, company, user, owner_membership):
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="merchant.myshopify.com",
        access_token="shpat_active",
        status=ShopifyStore.Status.ACTIVE,
    )
    _bind(store, owner_membership)

    with (
        patch.object(sc_commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(sc_commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token(shop="merchant.myshopify.com")
        response = api_client.post(_url(), {"session_token": token}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["access"]
    assert response.data["refresh"]
    assert response.data["shop_domain"] == "merchant.myshopify.com"
    assert response.data["company_id"] == company.id
    assert response.data["user_public_id"] == str(user.public_id)

    # Cookies set
    assert "nxentra_access" in response.cookies or any("access" in name for name in response.cookies.keys())

    # active_company aligned with the store's company
    user.refresh_from_db()
    assert user.active_company_id == company.id


# =============================================================================
# Input validation
# =============================================================================


@pytest.mark.django_db
def test_session_login_rejects_missing_session_token(api_client):
    response = api_client.post(_url(), {}, format="json")
    assert response.status_code == 400
    assert response.data["detail"] == "missing_session_token"


@pytest.mark.django_db
def test_session_login_rejects_blank_session_token(api_client):
    response = api_client.post(_url(), {"session_token": "   "}, format="json")
    assert response.status_code == 400
    assert response.data["detail"] == "missing_session_token"


@pytest.mark.django_db
def test_session_login_rejects_invalid_signature(api_client, company, user, owner_membership):
    ShopifyStore.objects.create(
        company=company,
        shop_domain="merchant.myshopify.com",
        access_token="shpat_active",
        status=ShopifyStore.Status.ACTIVE,
    )

    with (
        patch.object(sc_commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(sc_commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        bad = _make_session_token(secret="wrong-secret")
        response = api_client.post(_url(), {"session_token": bad}, format="json")

    assert response.status_code == 401
    assert response.data["detail"] == "invalid_session_token"


@pytest.mark.django_db
def test_session_login_rejects_expired_token(api_client, company, user, owner_membership):
    ShopifyStore.objects.create(
        company=company,
        shop_domain="merchant.myshopify.com",
        access_token="shpat_active",
        status=ShopifyStore.Status.ACTIVE,
    )

    with (
        patch.object(sc_commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(sc_commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        expired = _make_session_token(expires_in=-30)
        response = api_client.post(_url(), {"session_token": expired}, format="json")

    assert response.status_code == 401
    assert response.data["detail"] == "invalid_session_token"


# =============================================================================
# Store lookup
# =============================================================================


@pytest.mark.django_db
def test_session_login_returns_404_when_no_store_for_shop(api_client):
    with (
        patch.object(sc_commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(sc_commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token(shop="never-connected.myshopify.com")
        response = api_client.post(_url(), {"session_token": token}, format="json")

    assert response.status_code == 404
    assert response.data["detail"] == "no_connection"
    assert response.data["shop_domain"] == "never-connected.myshopify.com"


@pytest.mark.django_db
def test_session_login_ignores_disconnected_store(api_client, company, user, owner_membership):
    ShopifyStore.objects.create(
        company=company,
        shop_domain="merchant.myshopify.com",
        access_token="shpat_dead",
        status=ShopifyStore.Status.DISCONNECTED,
    )

    with (
        patch.object(sc_commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(sc_commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token(shop="merchant.myshopify.com")
        response = api_client.post(_url(), {"session_token": token}, format="json")

    assert response.status_code == 404
    assert response.data["detail"] == "no_connection"


@pytest.mark.django_db
def test_session_login_prefers_active_over_disconnected_history(
    api_client, company, second_company, user, owner_membership
):
    """The DB enforces uniq(shop_domain, status=ACTIVE) so only one ACTIVE
    row can exist per shop, but a merchant who moved between companies can
    leave behind DISCONNECTED history on the old company. We must follow
    the ACTIVE row to the new company, not the historical one."""
    # Set up an OWNER for second_company
    other_user = user.__class__.objects.create_user(
        public_id=uuid4(),
        email="other-owner@test.com",
        password="Testpass123!",
        name="Other Owner",
    )
    other_user.active_company = second_company
    other_user.save()
    other_membership = CompanyMembership.objects.create(
        public_id=uuid4(),
        company=second_company,
        user=other_user,
        role=CompanyMembership.Role.OWNER,
        is_active=True,
    )

    # Old DISCONNECTED row on `company`
    ShopifyStore.objects.create(
        company=company,
        shop_domain="moved.myshopify.com",
        access_token="shpat_old",
        status=ShopifyStore.Status.DISCONNECTED,
    )
    # Current ACTIVE row on second_company
    active_store = ShopifyStore.objects.create(
        company=second_company,
        shop_domain="moved.myshopify.com",
        access_token="shpat_new",
        status=ShopifyStore.Status.ACTIVE,
    )
    _bind(active_store, other_membership)

    with (
        patch.object(sc_commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(sc_commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token(shop="moved.myshopify.com")
        response = api_client.post(_url(), {"session_token": token}, format="json")

    assert response.status_code == 200
    assert response.data["company_id"] == second_company.id
    assert response.data["user_public_id"] == str(other_user.public_id)


# =============================================================================
# Owner membership invariants
# =============================================================================


@pytest.mark.django_db
def test_session_login_denies_unbound_user(api_client, company, owner_membership):
    """A1: a connected store with NO ShopifyUserBinding for the token's sub is
    denied (403 not_bound) — never logged in as "the first owner"."""
    ShopifyStore.objects.create(
        company=company,
        shop_domain="unbound.myshopify.com",
        access_token="shpat_x",
        status=ShopifyStore.Status.ACTIVE,
    )

    with (
        patch.object(sc_commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(sc_commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token(shop="unbound.myshopify.com")
        response = api_client.post(_url(), {"session_token": token}, format="json")

    assert response.status_code == 403
    assert response.data["detail"] == "not_bound"


@pytest.mark.django_db
def test_session_login_denies_binding_to_inactive_membership(api_client, company, user):
    """A binding to a membership later flipped is_active=False resolves to None
    -> denied (not_bound), not logged in."""
    membership = CompanyMembership.objects.create(
        public_id=uuid4(),
        company=company,
        user=user,
        role=CompanyMembership.Role.OWNER,
        is_active=True,
    )
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="merchant.myshopify.com",
        access_token="shpat_active",
        status=ShopifyStore.Status.ACTIVE,
    )
    _bind(store, membership)
    membership.is_active = False
    membership.save(update_fields=["is_active"])

    with (
        patch.object(sc_commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(sc_commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token(shop="merchant.myshopify.com")
        response = api_client.post(_url(), {"session_token": token}, format="json")

    assert response.status_code == 403
    assert response.data["detail"] == "not_bound"
