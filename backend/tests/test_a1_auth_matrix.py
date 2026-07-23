# tests/test_a1_auth_matrix.py
"""
A1 (2026-07-23) — four-mode authentication matrix + cookie-path CSRF.

`CookieJWTAuthentication` (used by both DRF and TenantRlsMiddleware) resolves,
per request, in precedence order:

  1. Embedded Shopify — App Bridge session-token bearer -> store company
     owner/admin. CSRF-exempt. No third-party-cookie dependence.
  2. Standalone browser — HttpOnly `nxentra_access` cookie. Django CSRF enforced.
  3. Explicit API client — `Authorization: Bearer <Nxentra JWT>`. CSRF-exempt.

Webhooks authenticate via HMAC (authentication_classes = []) and are untouched.

These tests prove: the session-token resolver is correct and fail-closed; the
cookie path enforces CSRF (cross-site POST -> 403) while safe methods and the
bearer modes are exempt; an invalid bearer never downgrades a cookie request
out of CSRF; and the csrftoken cookie is issued on bootstrap and login.
"""

import time
from unittest.mock import patch
from uuid import uuid4

import jwt as pyjwt
import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import CompanyMembership
from shopify_connector import commands as sc_commands
from shopify_connector.models import ShopifyStore
from shopify_connector.session_auth import resolve_session_token

TEST_SECRET = "shpss_a1_test_secret"
TEST_API_KEY = "test_client_id_a1"


def _make_session_token(*, shop="merchant.myshopify.com", secret=TEST_SECRET, audience=TEST_API_KEY, expires_in=60):
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


def _patched_secret():
    return (
        patch.object(sc_commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(sc_commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    )


def _access_for(user, company_id=None):
    refresh = RefreshToken.for_user(user)
    refresh["company_id"] = str(company_id) if company_id else None
    return str(refresh.access_token)


def _active_store(company, shop="merchant.myshopify.com"):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain=shop,
        access_token="shpat_active",
        status=ShopifyStore.Status.ACTIVE,
    )


# =============================================================================
# Part A — session-token resolver (fail-closed shop -> owner mapping)
# =============================================================================


@pytest.mark.django_db
def test_resolver_maps_valid_token_to_owner_and_company(company, user, owner_membership):
    _active_store(company)
    s1, s2 = _patched_secret()
    with s1, s2:
        resolved = resolve_session_token(_make_session_token())
    assert resolved is not None
    resolved_user, company_id = resolved
    assert resolved_user.pk == user.pk
    assert company_id == company.id
    # active_company is pinned in-memory to the store's company (not persisted)
    assert resolved_user.active_company_id == company.id


@pytest.mark.django_db
def test_resolver_returns_none_for_unknown_shop(company, user, owner_membership):
    _active_store(company)
    s1, s2 = _patched_secret()
    with s1, s2:
        assert resolve_session_token(_make_session_token(shop="nope.myshopify.com")) is None


@pytest.mark.django_db
def test_resolver_returns_none_for_disconnected_store(company, user, owner_membership):
    ShopifyStore.objects.create(
        company=company, shop_domain="merchant.myshopify.com", access_token="x", status=ShopifyStore.Status.DISCONNECTED
    )
    s1, s2 = _patched_secret()
    with s1, s2:
        assert resolve_session_token(_make_session_token()) is None


@pytest.mark.django_db
def test_resolver_returns_none_for_bad_signature(company, user, owner_membership):
    _active_store(company)
    s1, s2 = _patched_secret()
    with s1, s2:
        assert resolve_session_token(_make_session_token(secret="wrong")) is None


@pytest.mark.django_db
def test_resolver_returns_none_for_expired_token(company, user, owner_membership):
    _active_store(company)
    s1, s2 = _patched_secret()
    with s1, s2:
        assert resolve_session_token(_make_session_token(expires_in=-30)) is None


@pytest.mark.django_db
def test_resolver_returns_none_for_wrong_audience(company, user, owner_membership):
    _active_store(company)
    s1, s2 = _patched_secret()
    with s1, s2:
        assert resolve_session_token(_make_session_token(audience="someone-elses-app")) is None


@pytest.mark.django_db
def test_resolver_returns_none_when_no_owner(company):
    _active_store(company)  # no membership created
    s1, s2 = _patched_secret()
    with s1, s2:
        assert resolve_session_token(_make_session_token()) is None


@pytest.mark.django_db
def test_resolver_returns_none_for_inactive_owner(company, user):
    CompanyMembership.objects.create(
        public_id=uuid4(), company=company, user=user, role=CompanyMembership.Role.OWNER, is_active=False
    )
    _active_store(company)
    s1, s2 = _patched_secret()
    with s1, s2:
        assert resolve_session_token(_make_session_token()) is None


@pytest.mark.django_db
def test_resolver_is_tenant_scoped_by_shop(company, second_company, user, owner_membership):
    """A session token for a shop connected to second_company must resolve to
    second_company's owner — never leak to the first company."""
    other = user.__class__.objects.create_user(
        public_id=uuid4(), email="other@test.com", password="Testpass123!", name="Other"
    )
    other.active_company = second_company
    other.save()
    CompanyMembership.objects.create(
        public_id=uuid4(), company=second_company, user=other, role=CompanyMembership.Role.OWNER, is_active=True
    )
    _active_store(second_company, shop="tenant-b.myshopify.com")
    s1, s2 = _patched_secret()
    with s1, s2:
        resolved = resolve_session_token(_make_session_token(shop="tenant-b.myshopify.com"))
    assert resolved is not None
    resolved_user, company_id = resolved
    assert company_id == second_company.id
    assert resolved_user.pk == other.pk


# =============================================================================
# Part B — CookieJWTAuthentication precedence + CSRF (HTTP integration)
# =============================================================================


@pytest.mark.django_db
def test_session_token_bearer_authenticates_get(company, user, owner_membership):
    _active_store(company)
    client = APIClient(enforce_csrf_checks=True)
    s1, s2 = _patched_secret()
    with s1, s2:
        token = _make_session_token()
        resp = client.get(reverse("accounts:me"), HTTP_AUTHORIZATION=f"Bearer {token}")
    assert resp.status_code == 200, resp.data
    # The session-token bearer authenticated as the store's owner.
    assert user.email in str(resp.data)


@pytest.mark.django_db
def test_cookie_get_is_allowed_without_csrf(company, user, owner_membership):
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user)
    resp = client.get(reverse("accounts:me"))
    assert resp.status_code == 200, resp.data


@pytest.mark.django_db
def test_cookie_post_without_csrf_is_forbidden(company, user, owner_membership):
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user)
    resp = client.post(reverse("accounts:logout"), {}, format="json")
    assert resp.status_code == 403
    assert "CSRF" in str(resp.data)


@pytest.mark.django_db
def test_cookie_post_with_csrf_passes(company, user, owner_membership):
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user)
    # Bootstrap the csrftoken cookie, then echo it back in the header.
    client.get(reverse("accounts:csrf-token"))
    csrf = client.cookies["csrftoken"].value
    resp = client.post(reverse("accounts:logout"), {}, format="json", HTTP_X_CSRFTOKEN=csrf)
    assert resp.status_code != 403, resp.data


@pytest.mark.django_db
def test_nxentra_bearer_post_is_csrf_exempt(company, user, owner_membership):
    client = APIClient(enforce_csrf_checks=True)
    token = _access_for(user)
    resp = client.post(reverse("accounts:logout"), {}, format="json", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert resp.status_code != 403, resp.data


@pytest.mark.django_db
def test_invalid_bearer_plus_cookie_still_enforces_csrf(company, user, owner_membership):
    """An invalid Authorization header must not downgrade a cookie-authenticated
    request out of CSRF — the cookie branch always enforces it."""
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user)
    resp = client.post(reverse("accounts:logout"), {}, format="json", HTTP_AUTHORIZATION="Bearer not-a-real-token")
    assert resp.status_code == 403
    assert "CSRF" in str(resp.data)


@pytest.mark.django_db
def test_csrf_bootstrap_endpoint_sets_cookie(company):
    client = APIClient(enforce_csrf_checks=True)
    resp = client.get(reverse("accounts:csrf-token"))
    assert resp.status_code == 200
    assert "csrftoken" in resp.cookies
    assert resp.cookies["csrftoken"].value


@pytest.mark.django_db
def test_login_response_sets_csrftoken_cookie(company, user, owner_membership):
    # Login is a public, csrf-exempt endpoint — use the ordinary client (a
    # real browser's first login carries no CSRF token). The response must
    # seed the csrftoken cookie for subsequent cookie-path mutations.
    user.email_verified = True
    user.save(update_fields=["email_verified"])
    client = APIClient()
    resp = client.post(
        reverse("accounts:login"),
        {"email": user.email, "password": "Testpass123!", "company_id": company.id},
        format="json",
    )
    assert resp.status_code == 200, resp.data
    assert "csrftoken" in resp.cookies
