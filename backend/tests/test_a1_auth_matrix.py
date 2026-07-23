# tests/test_a1_auth_matrix.py
"""
A1 (2026-07-23) — authentication matrix, explicit Shopify binding, bearer-
exclusive precedence, strict claim validation.

Authorization for embedded Shopify is an explicit `(store, sub) -> membership`
binding (never "the first OWNER"). `CookieJWTAuthentication` is bearer-exclusive:
when an Authorization header is present the cookie is never consulted.
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
from shopify_connector.models import ShopifyStore, ShopifyUserBinding
from shopify_connector.session_auth import ShopifyAuthOutcome, resolve_session_token
from shopify_connector.user_binding import bind_shopify_user, resolve_bound_membership

TEST_SECRET = "shpss_a1_test_secret_at_least_32_bytes_long"
TEST_API_KEY = "test_client_id_a1"
SUB = "shopify-user-98765"


def _make_session_token(
    *, shop="merchant.myshopify.com", secret=TEST_SECRET, audience=TEST_API_KEY, expires_in=60, sub=SUB, dest_shop=None
):
    now = int(time.time())
    dest = dest_shop or shop
    payload = {
        "iss": f"https://{shop}/admin",
        "dest": f"https://{dest}",
        "aud": audience,
        "sub": sub,
        "exp": now + expires_in,
        "nbf": now - 5,
        "iat": now,
        "jti": f"jti-{uuid4().hex[:8]}",
        "sid": f"sid-{uuid4().hex[:8]}",
    }
    if sub is None:
        payload.pop("sub")
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
        company=company, shop_domain=shop, access_token="shpat_active", status=ShopifyStore.Status.ACTIVE
    )


def _bind(store, membership, sub=SUB, user=None):
    return bind_shopify_user(store=store, shopify_sub=sub, membership=membership, actor_user=user)


# =============================================================================
# Strict claim validation (point 4)
# =============================================================================


def test_validated_shop_requires_iss_and_dest_same_shop():
    good = {"iss": "https://acme.myshopify.com/admin", "dest": "https://acme.myshopify.com"}
    assert sc_commands.validated_shop_from_claims(good) == "acme.myshopify.com"


def test_validated_shop_rejects_iss_dest_mismatch():
    mismatch = {"iss": "https://acme.myshopify.com/admin", "dest": "https://evil.myshopify.com"}
    assert sc_commands.validated_shop_from_claims(mismatch) is None


@pytest.mark.parametrize(
    "host",
    ["evil-myshopify.com", "acme.myshopify.com.evil.com", "sub.acme.myshopify.com", "acme.myshopify.io", ""],
)
def test_normalize_shop_rejects_suffix_tricks(host):
    claims = {"iss": f"https://{host}/admin", "dest": f"https://{host}"}
    assert sc_commands.validated_shop_from_claims(claims) is None


@pytest.mark.django_db
def test_verify_requires_sub_and_nbf(company, user, owner_membership):
    s1, s2 = _patched_secret()
    with s1, s2:
        # No sub -> pyjwt "require" fails -> None.
        assert sc_commands.verify_shopify_session_token(_make_session_token(sub=None)) is None


# =============================================================================
# Binding commands
# =============================================================================


@pytest.mark.django_db
def test_bind_and_resolve_membership(company, user, owner_membership):
    store = _active_store(company)
    binding = _bind(store, owner_membership, user=user)
    assert binding.is_active and binding.created_by_id == user.id
    resolved = resolve_bound_membership(store=store, shopify_sub=SUB)
    assert resolved is not None and resolved.id == owner_membership.id


@pytest.mark.django_db
def test_bind_refuses_different_sub_while_active(company, user, owner_membership):
    store = _active_store(company)
    _bind(store, owner_membership, sub="sub-A", user=user)
    from shopify_connector.user_binding import BindingError

    with pytest.raises(BindingError):
        _bind(store, owner_membership, sub="sub-B", user=user)


@pytest.mark.django_db
def test_bind_refuses_membership_from_other_company(company, second_company, user, owner_membership):
    store = _active_store(second_company)  # store in second_company
    from shopify_connector.user_binding import BindingError

    with pytest.raises(BindingError):
        _bind(store, owner_membership, user=user)  # owner_membership is company, not second_company


@pytest.mark.django_db
def test_resolve_returns_none_for_inactive_membership(company, user, owner_membership):
    store = _active_store(company)
    _bind(store, owner_membership, user=user)
    owner_membership.is_active = False
    owner_membership.save(update_fields=["is_active"])
    assert resolve_bound_membership(store=store, shopify_sub=SUB) is None


@pytest.mark.django_db
def test_unbind_revokes(company, user, owner_membership):
    store = _active_store(company)
    _bind(store, owner_membership, user=user)
    from shopify_connector.user_binding import unbind_shopify_user

    unbind_shopify_user(store=store, shopify_sub=SUB, actor_user=user)
    assert resolve_bound_membership(store=store, shopify_sub=SUB) is None


# =============================================================================
# Resolver 3-state (point 3)
# =============================================================================


@pytest.mark.django_db
def test_resolver_valid_and_bound(company, user, owner_membership):
    store = _active_store(company)
    _bind(store, owner_membership, user=user)
    s1, s2 = _patched_secret()
    with s1, s2:
        outcome, ruser, company_id = resolve_session_token(_make_session_token())
    assert outcome == ShopifyAuthOutcome.VALID_AND_BOUND
    assert ruser.pk == user.pk and company_id == company.id
    assert ruser.active_company_id == company.id


@pytest.mark.django_db
def test_resolver_valid_but_denied_when_unbound(company, user, owner_membership):
    _active_store(company)  # store exists but no binding
    s1, s2 = _patched_secret()
    with s1, s2:
        outcome, ruser, _ = resolve_session_token(_make_session_token())
    assert outcome == ShopifyAuthOutcome.VALID_BUT_DENIED and ruser is None


@pytest.mark.django_db
def test_resolver_valid_but_denied_unknown_shop(company, user, owner_membership):
    s1, s2 = _patched_secret()
    with s1, s2:
        outcome, _, _ = resolve_session_token(_make_session_token(shop="nope.myshopify.com"))
    assert outcome == ShopifyAuthOutcome.VALID_BUT_DENIED


@pytest.mark.django_db
def test_resolver_valid_but_denied_iss_dest_mismatch(company, user, owner_membership):
    store = _active_store(company)
    _bind(store, owner_membership, user=user)
    s1, s2 = _patched_secret()
    with s1, s2:
        outcome, _, _ = resolve_session_token(_make_session_token(dest_shop="evil.myshopify.com"))
    assert outcome == ShopifyAuthOutcome.VALID_BUT_DENIED


@pytest.mark.django_db
def test_resolver_not_shopify_token_for_garbage(company):
    s1, s2 = _patched_secret()
    with s1, s2:
        outcome, _, _ = resolve_session_token("not.a.jwt")
    assert outcome == ShopifyAuthOutcome.NOT_SHOPIFY_TOKEN


@pytest.mark.django_db
def test_resolver_not_shopify_token_for_nxentra_jwt(company, user, owner_membership):
    nxentra = _access_for(user, company.id)
    s1, s2 = _patched_secret()
    with s1, s2:
        outcome, _, _ = resolve_session_token(nxentra)
    assert outcome == ShopifyAuthOutcome.NOT_SHOPIFY_TOKEN


@pytest.mark.django_db
def test_resolver_tenant_scoped_by_binding(company, second_company, user, owner_membership):
    """Binding lives on second_company's store -> resolves second_company's member."""
    other = user.__class__.objects.create_user(
        public_id=uuid4(), email="other@test.com", password="Testpass123!", name="Other"
    )
    other.active_company = second_company
    other.save()
    other_m = CompanyMembership.objects.create(
        public_id=uuid4(), company=second_company, user=other, role=CompanyMembership.Role.OWNER, is_active=True
    )
    store = _active_store(second_company, shop="tenant-b.myshopify.com")
    _bind(store, other_m, user=other)
    s1, s2 = _patched_secret()
    with s1, s2:
        outcome, ruser, company_id = resolve_session_token(_make_session_token(shop="tenant-b.myshopify.com"))
    assert outcome == ShopifyAuthOutcome.VALID_AND_BOUND
    assert company_id == second_company.id and ruser.pk == other.pk


# =============================================================================
# Bearer-exclusive precedence (point 2 — the 5 required cases)
# =============================================================================


@pytest.mark.django_db
def test_invalid_bearer_plus_valid_cookie_and_csrf_is_401(company, user, owner_membership):
    """Invalid bearer + valid cookie + valid CSRF -> 401. No cookie fallback."""
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user)
    client.get(reverse("accounts:csrf-token"))
    csrf = client.cookies["csrftoken"].value
    resp = client.post(
        reverse("accounts:logout"),
        {},
        format="json",
        HTTP_AUTHORIZATION="Bearer not-a-real-token",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert resp.status_code == 401, resp.data


@pytest.mark.django_db
def test_valid_nxentra_bearer_beats_other_users_cookie(company, user, owner_membership):
    """Valid Nxentra bearer + another user's cookie -> bearer user wins."""
    other = user.__class__.objects.create_user(
        public_id=uuid4(), email="cookie-user@test.com", password="Testpass123!", name="Cookie User"
    )
    CompanyMembership.objects.create(
        public_id=uuid4(), company=company, user=other, role=CompanyMembership.Role.ADMIN, is_active=True
    )
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(other)  # cookie = other
    resp = client.get(reverse("accounts:me"), HTTP_AUTHORIZATION=f"Bearer {_access_for(user)}")  # bearer = user
    assert resp.status_code == 200, resp.data
    assert user.email in str(resp.data) and other.email not in str(resp.data)


@pytest.mark.django_db
def test_valid_shopify_unbound_plus_cookie_is_denied(company, user, owner_membership):
    """Valid Shopify token, unbound sub, + valid cookie -> denied (401), not cookie."""
    _active_store(company)  # no binding
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user)
    s1, s2 = _patched_secret()
    with s1, s2:
        resp = client.get(reverse("accounts:me"), HTTP_AUTHORIZATION=f"Bearer {_make_session_token()}")
    assert resp.status_code == 401, resp.data


@pytest.mark.django_db
def test_disconnected_shop_token_plus_cookie_is_denied(company, user, owner_membership):
    ShopifyStore.objects.create(
        company=company, shop_domain="merchant.myshopify.com", access_token="x", status=ShopifyStore.Status.DISCONNECTED
    )
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user)
    s1, s2 = _patched_secret()
    with s1, s2:
        resp = client.get(reverse("accounts:me"), HTTP_AUTHORIZATION=f"Bearer {_make_session_token()}")
    assert resp.status_code == 401, resp.data


@pytest.mark.django_db
def test_resolver_db_error_cannot_downgrade_to_cookie(company, user, owner_membership):
    """A resolver/DB error on a valid Shopify token must fail closed (401), never
    fall back to the cookie."""
    store = _active_store(company)
    _bind(store, owner_membership, user=user)
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user)
    s1, s2 = _patched_secret()
    with (
        s1,
        s2,
        patch("shopify_connector.user_binding.resolve_bound_membership", side_effect=RuntimeError("db down")),
    ):
        resp = client.get(reverse("accounts:me"), HTTP_AUTHORIZATION=f"Bearer {_make_session_token()}")
    assert resp.status_code == 401, resp.data


# =============================================================================
# Cookie path + CSRF (no Authorization header)
# =============================================================================


@pytest.mark.django_db
def test_session_token_bound_bearer_authenticates_get(company, user, owner_membership):
    store = _active_store(company)
    _bind(store, owner_membership, user=user)
    client = APIClient(enforce_csrf_checks=True)
    s1, s2 = _patched_secret()
    with s1, s2:
        resp = client.get(reverse("accounts:me"), HTTP_AUTHORIZATION=f"Bearer {_make_session_token()}")
    assert resp.status_code == 200, resp.data
    assert user.email in str(resp.data)


@pytest.mark.django_db
def test_cookie_get_allowed_without_csrf(company, user, owner_membership):
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user)
    assert client.get(reverse("accounts:me")).status_code == 200


@pytest.mark.django_db
def test_cookie_post_without_csrf_forbidden(company, user, owner_membership):
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user)
    resp = client.post(reverse("accounts:logout"), {}, format="json")
    assert resp.status_code == 403 and "CSRF" in str(resp.data)


@pytest.mark.django_db
def test_cookie_post_with_csrf_passes(company, user, owner_membership):
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user)
    client.get(reverse("accounts:csrf-token"))
    csrf = client.cookies["csrftoken"].value
    resp = client.post(reverse("accounts:logout"), {}, format="json", HTTP_X_CSRFTOKEN=csrf)
    assert resp.status_code != 403, resp.data


@pytest.mark.django_db
def test_csrf_bootstrap_sets_cookie(company):
    resp = APIClient(enforce_csrf_checks=True).get(reverse("accounts:csrf-token"))
    assert resp.status_code == 200 and "csrftoken" in resp.cookies


@pytest.mark.django_db
def test_login_sets_csrftoken_cookie(company, user, owner_membership):
    user.email_verified = True
    user.save(update_fields=["email_verified"])
    resp = APIClient().post(
        reverse("accounts:login"),
        {"email": user.email, "password": "Testpass123!", "company_id": company.id},
        format="json",
    )
    assert resp.status_code == 200 and "csrftoken" in resp.cookies


# =============================================================================
# Owner-link ceremony: nonce create + redeem
# =============================================================================


@pytest.mark.django_db
def test_nonce_ceremony_binds_and_enables_session_login(company, user, owner_membership):
    store = _active_store(company)
    # 1) Standalone owner creates a nonce (cookie-authenticated).
    _grant_settings_edit(company, owner_membership)
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user, company.id)
    client.get(reverse("accounts:csrf-token"))
    csrf = client.cookies["csrftoken"].value
    resp = client.post(reverse("shopify-linking-nonce"), {}, format="json", HTTP_X_CSRFTOKEN=csrf)
    assert resp.status_code == 200, resp.data
    nonce = resp.data["nonce"]

    # 2) Embedded app redeems it with a session token (no prior auth).
    redeem = APIClient()
    s1, s2 = _patched_secret()
    with s1, s2:
        r = redeem.post(
            reverse("shopify-redeem-linking-nonce"),
            {"nonce": nonce, "session_token": _make_session_token()},
            format="json",
        )
    assert r.status_code == 200, r.data
    assert ShopifyUserBinding.objects.filter(store=store, shopify_sub=SUB, is_active=True).exists()

    # 3) Now session-login succeeds for the bound user.
    with s1, s2:
        login = APIClient().post(
            reverse("accounts:shopify-session-login"), {"session_token": _make_session_token()}, format="json"
        )
    assert login.status_code == 200, login.data
    assert login.data["company_id"] == company.id


@pytest.mark.django_db
def test_redeem_rejects_wrong_shop(company, user, owner_membership):
    _active_store(company, shop="merchant.myshopify.com")
    _grant_settings_edit(company, owner_membership)
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user, company.id)
    client.get(reverse("accounts:csrf-token"))
    csrf = client.cookies["csrftoken"].value
    nonce = client.post(reverse("shopify-linking-nonce"), {}, format="json", HTTP_X_CSRFTOKEN=csrf).data["nonce"]
    s1, s2 = _patched_secret()
    with s1, s2:
        r = APIClient().post(
            reverse("shopify-redeem-linking-nonce"),
            {"nonce": nonce, "session_token": _make_session_token(shop="other.myshopify.com")},
            format="json",
        )
    assert r.status_code == 400


@pytest.mark.django_db
def test_redeem_rejects_reused_nonce(company, user, owner_membership):
    _active_store(company)
    _grant_settings_edit(company, owner_membership)
    client = APIClient(enforce_csrf_checks=True)
    client.cookies["nxentra_access"] = _access_for(user, company.id)
    client.get(reverse("accounts:csrf-token"))
    csrf = client.cookies["csrftoken"].value
    nonce = client.post(reverse("shopify-linking-nonce"), {}, format="json", HTTP_X_CSRFTOKEN=csrf).data["nonce"]
    s1, s2 = _patched_secret()
    with s1, s2:
        first = APIClient().post(
            reverse("shopify-redeem-linking-nonce"),
            {"nonce": nonce, "session_token": _make_session_token()},
            format="json",
        )
        assert first.status_code == 200
        second = APIClient().post(
            reverse("shopify-redeem-linking-nonce"),
            {"nonce": nonce, "session_token": _make_session_token()},
            format="json",
        )
    assert second.status_code == 400


def _grant_settings_edit(company, membership):
    from accounts.models import CompanyMembershipPermission, NxPermission

    perm, _ = NxPermission.objects.get_or_create(code="settings.edit", defaults={"name": "Edit settings"})
    CompanyMembershipPermission.objects.get_or_create(company=company, membership=membership, permission=perm)
