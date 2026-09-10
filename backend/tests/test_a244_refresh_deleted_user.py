# tests/test_a244_refresh_deleted_user.py
"""
A244 (2026-09-09) — stale-but-valid credentials for a principal that no
longer exists, or is deactivated, are rejected with 401 — never 500, never a
token pair.

Observed on the rehearsal deployment (Sentry PYTHON-DJANGO-22, 2026-09-08):
a browser tab still holding a validly signed, unexpired refresh token POSTed
/api/auth/refresh/ after the database behind it had been replaced. SimpleJWT's
``TokenRefreshSerializer.validate`` looks the user up with a bare
``objects.get`` and ``NxentraTokenRefreshSerializer`` called it BEFORE its own
"User not found" guard, so ``User.DoesNotExist`` escaped as a 500. The same
path fires in production for any deleted user holding a refresh token.

The sibling sweep pinned here (same class — a dead principal behind a valid
credential — in the auth adapter layer):
- the cookie authenticator swallowed only ``InvalidToken``; a stale access
  cookie for a deleted/deactivated user turned every AllowAny view (logout
  included, so the cookies could never be cleared) into a 401;
- the pending-login-token exchange and the multi-company chooser never
  applied the inactive-account rule, so a deactivated user could still be
  minted a token pair.
"""

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import CompanyMembership

PASSWORD = "Testpass123!"


@pytest.fixture(autouse=True)
def _throttle_neutral():
    """Throttle counters live in the default cache and survive across tests
    in one battery process (feedback_sqlite_battery_throttle). Clear around
    each test so this file neither inherits earlier files' counts nor leaks
    its own into later anonymous-endpoint tests."""
    cache.clear()
    yield
    cache.clear()


def _refresh_for(user, company_id):
    r = RefreshToken.for_user(user)
    r["company_id"] = str(company_id)
    return r


def _csrf(client):
    client.get(reverse("accounts:csrf-token"))
    return client.cookies["csrftoken"].value


def _deactivate(user):
    user.is_active = False
    user.save(update_fields=["is_active"])


def _assert_rejected_without_tokens(resp):
    # The contract is the status and the absence of a token pair. The response
    # ``code`` is deliberately not pinned: it is owned by whichever SimpleJWT
    # release the floating ``>=5.3`` pin resolves to (5.5.1 lets the miss escape
    # and this repo maps it to ``token_not_valid``; upstream master maps it to
    # ``no_active_account`` itself).
    assert resp.status_code == 401, (resp.status_code, getattr(resp, "data", None))
    assert "detail" in resp.data, resp.data
    assert "access" not in resp.data and "refresh" not in resp.data, resp.data
    assert "nxentra_access" not in resp.cookies and "nxentra_refresh" not in resp.cookies


# =============================================================================
# /api/auth/refresh/ — the reported 500
# =============================================================================


@pytest.mark.django_db
def test_body_token_refresh_for_deleted_user_is_401(company, user, owner_membership):
    token = str(_refresh_for(user, company.id))
    user.delete()

    # raise_request_exception=False so a regression shows as "500 != 401"
    # instead of an exception escaping the test client.
    client = APIClient(raise_request_exception=False)
    resp = client.post(reverse("accounts:token-refresh"), {"refresh": token}, format="json")

    _assert_rejected_without_tokens(resp)


@pytest.mark.django_db
def test_cookie_refresh_for_deleted_user_is_401(company, user, owner_membership):
    token = str(_refresh_for(user, company.id))
    user.delete()

    client = APIClient(enforce_csrf_checks=True, raise_request_exception=False)
    csrf = _csrf(client)
    client.cookies["nxentra_refresh"] = token
    resp = client.post(reverse("accounts:token-refresh"), {}, format="json", HTTP_X_CSRFTOKEN=csrf)

    _assert_rejected_without_tokens(resp)


@pytest.mark.django_db
def test_refresh_for_inactive_user_keeps_simplejwt_rejection(company, user, owner_membership):
    """The narrow ``except`` must not touch the existing-but-inactive path:
    SimpleJWT's own ``no_active_account`` rejection still surfaces."""
    token = str(_refresh_for(user, company.id))
    _deactivate(user)

    resp = APIClient(raise_request_exception=False).post(
        reverse("accounts:token-refresh"), {"refresh": token}, format="json"
    )

    _assert_rejected_without_tokens(resp)
    # SimpleJWT raises DRF's plain AuthenticationFailed here (no ``code`` key
    # on the wire) — distinct from this repo's ``token_not_valid`` rejections.
    assert "No active account" in str(resp.data["detail"]), resp.data
    assert resp.data.get("code") != "token_not_valid", resp.data


@pytest.mark.django_db
def test_refresh_with_revoked_membership_is_401_and_burns_presented_token(company, user, owner_membership):
    """Ordering guard: SimpleJWT's rotation runs BEFORE the membership check on
    purpose, so a revoked member's token is blacklisted on its first failed
    use. Anyone hoisting the membership check above ``super().validate``
    breaks this test."""
    refresh = _refresh_for(user, company.id)
    jti = refresh["jti"]
    owner_membership.is_active = False
    owner_membership.save(update_fields=["is_active"])

    resp = APIClient(raise_request_exception=False).post(
        reverse("accounts:token-refresh"), {"refresh": str(refresh)}, format="json"
    )

    _assert_rejected_without_tokens(resp)
    assert resp.data["code"] == "token_not_valid", resp.data
    assert BlacklistedToken.objects.filter(token__jti=jti).exists()


# =============================================================================
# Cookie authenticator — a dead principal's access cookie is anonymous, not 401
# =============================================================================


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["deleted", "inactive"])
def test_logout_with_dead_principal_access_cookie_clears_cookies(company, user, owner_membership, state):
    refresh = _refresh_for(user, company.id)
    access = str(refresh.access_token)
    if state == "deleted":
        user.delete()
    else:
        _deactivate(user)

    client = APIClient(enforce_csrf_checks=True, raise_request_exception=False)
    csrf = _csrf(client)
    client.cookies["nxentra_access"] = access
    client.cookies["nxentra_refresh"] = str(refresh)
    resp = client.post(reverse("accounts:logout"), {}, format="json", HTTP_X_CSRFTOKEN=csrf)

    assert resp.status_code == 204, (resp.status_code, getattr(resp, "data", None))
    assert resp.cookies["nxentra_access"]["max-age"] == 0
    assert resp.cookies["nxentra_refresh"]["max-age"] == 0


@pytest.mark.django_db
def test_dead_principal_access_cookie_does_not_authenticate(company, user, owner_membership):
    """Anonymous is strictly less privilege: an IsAuthenticated view still
    answers 401 for the same stale cookie."""
    access = str(_refresh_for(user, company.id).access_token)
    user.delete()

    client = APIClient(enforce_csrf_checks=True, raise_request_exception=False)
    csrf = _csrf(client)
    client.cookies["nxentra_access"] = access
    resp = client.get(reverse("accounts:me"), HTTP_X_CSRFTOKEN=csrf)

    assert resp.status_code == 401, (resp.status_code, getattr(resp, "data", None))
    assert "email" not in (resp.data or {})


# =============================================================================
# Login — the pending-login-token exchange and the multi-company chooser
# =============================================================================


@pytest.fixture
def multi_company_user(user, company, second_company, owner_membership):
    user.email_verified = True
    user.save(update_fields=["email_verified"])
    CompanyMembership.objects.create(
        company=second_company, user=user, role=CompanyMembership.Role.OWNER, is_active=True
    )
    return user


@pytest.mark.django_db
def test_pending_login_token_exchange_for_deactivated_user_is_401(multi_company_user, second_company):
    user = multi_company_user
    client = APIClient(raise_request_exception=False)
    step1 = client.post(reverse("accounts:login"), {"email": user.email, "password": PASSWORD}, format="json")
    assert step1.status_code == 200 and step1.data["detail"] == "choose_company", step1.data

    _deactivate(user)  # inside the 5-minute window

    step2 = client.post(
        reverse("accounts:login"),
        {"pending_login_token": step1.data["pending_login_token"], "company_id": second_company.id},
        format="json",
    )

    _assert_rejected_without_tokens(step2)
    assert step2.data["detail"] == "invalid_pending_token", step2.data


@pytest.mark.django_db
def test_password_login_for_deactivated_multi_company_user_is_401(multi_company_user):
    """The chooser branch never reached SimpleJWT's inactive-account rule, so a
    deactivated user with the right password was shown their companies and
    handed a pending token. Now the same 401 as the single-company path."""
    user = multi_company_user
    _deactivate(user)

    resp = APIClient(raise_request_exception=False).post(
        reverse("accounts:login"), {"email": user.email, "password": PASSWORD}, format="json"
    )

    _assert_rejected_without_tokens(resp)
    assert "pending_login_token" not in resp.data and "companies" not in resp.data, resp.data
