# tests/test_a245_login_active_company_writer.py
"""
A245 (2026-09-10) — a login that selects a company other than the user's
current ``active_company`` switches it through the canonical writer.

Found by the A244 sibling sweep: the password login, the pending-login-token
exchange and the Shopify session login each did a direct
``user.save(update_fields=["active_company"])``. ``User.active_company`` is
written canonically by ``accounts.commands.switch_active_company`` (command
context + ``USER_COMPANY_SWITCHED``, re-applied by the accounts projection),
so those saves were a second writer — and outside the test bypass the write
guard refused them: every login that chose a non-active company answered
**500** (``RuntimeError: User is a projection-owned read model``). The
regular suite never saw it because ``TESTING=True`` bypasses the guard, so
every request here runs under ``override_settings(TESTING=False)``.
"""

import time
from unittest.mock import patch
from uuid import uuid4

import jwt as pyjwt
import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import CompanyMembership
from events.models import BusinessEvent
from events.types import EventTypes
from shopify_connector import commands as sc_commands
from shopify_connector.models import ShopifyStore
from shopify_connector.user_binding import bind_shopify_user

PASSWORD = "Testpass123!"
SHOP = "merchant.myshopify.com"
TEST_SECRET = "shpss_a245_test_secret"
TEST_API_KEY = "test_client_id_a245"
SUB = "98765"


@pytest.fixture(autouse=True)
def _throttle_neutral():
    """Throttle counters live in the default cache and survive across tests
    in one battery process (feedback_sqlite_battery_throttle). Clear around
    each test so this file neither inherits earlier files' counts nor leaks
    its own into later anonymous-endpoint tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def multi_company_user(user, company, second_company, owner_membership):
    """Verified user whose active company is ``company`` and who also owns
    ``second_company``."""
    user.email_verified = True
    user.save(update_fields=["email_verified"])
    CompanyMembership.objects.create(
        company=second_company, user=user, role=CompanyMembership.Role.OWNER, is_active=True
    )
    assert user.active_company_id == company.id
    return user


def _client():
    # raise_request_exception=False so a regression shows as "500 != 200"
    # instead of an exception escaping the test client.
    return APIClient(raise_request_exception=False)


def _switch_events(user):
    return BusinessEvent.objects.filter(event_type=EventTypes.USER_COMPANY_SWITCHED, aggregate_id=str(user.public_id))


def _assert_switched_to(user, company, resp):
    assert resp.status_code == 200, (resp.status_code, getattr(resp, "data", None))
    user.refresh_from_db()
    assert user.active_company_id == company.id
    assert _switch_events(user).filter(company=company).count() == 1
    claims = pyjwt.decode(resp.data["access"], options={"verify_signature": False})
    assert str(claims["company_id"]) == str(company.id)


# =============================================================================
# Password login
# =============================================================================


@pytest.mark.django_db
def test_password_login_to_non_active_company_switches_through_the_command(multi_company_user, second_company):
    user = multi_company_user
    with override_settings(TESTING=False):
        resp = _client().post(
            reverse("accounts:login"),
            {"email": user.email, "password": PASSWORD, "company_id": second_company.id},
            format="json",
        )
    _assert_switched_to(user, second_company, resp)
    assert user.last_login is not None
    assert BusinessEvent.objects.filter(event_type=EventTypes.USER_LOGGED_IN, aggregate_id=str(user.public_id)).exists()


@pytest.mark.django_db
def test_password_login_to_the_current_company_is_a_no_op_switch(multi_company_user, company):
    """The non-switching path is untouched (and documents that last_login is
    exempt from the write guard: User.AUTH_SYSTEM_FIELDS)."""
    user = multi_company_user
    with override_settings(TESTING=False):
        resp = _client().post(
            reverse("accounts:login"),
            {"email": user.email, "password": PASSWORD, "company_id": company.id},
            format="json",
        )
    assert resp.status_code == 200, (resp.status_code, getattr(resp, "data", None))
    user.refresh_from_db()
    assert user.active_company_id == company.id
    assert user.last_login is not None
    assert not _switch_events(user).exists()


@pytest.mark.django_db
def test_password_login_to_an_inactive_company_is_refused_without_tokens(multi_company_user, company, second_company):
    """The canonical writer refuses an inactive target; the login answers 403
    instead of minting a token pair for a company the user cannot enter."""
    user = multi_company_user
    second_company.is_active = False
    second_company.save(update_fields=["is_active"])
    with override_settings(TESTING=False):
        resp = _client().post(
            reverse("accounts:login"),
            {"email": user.email, "password": PASSWORD, "company_id": second_company.id},
            format="json",
        )
    assert resp.status_code == 403, (resp.status_code, getattr(resp, "data", None))
    assert resp.data["detail"] == "invalid_company"
    assert "access" not in resp.data and "nxentra_access" not in resp.cookies
    user.refresh_from_db()
    assert user.active_company_id == company.id
    assert not _switch_events(user).exists()


# =============================================================================
# Pending-login-token exchange (browser company chooser)
# =============================================================================


@pytest.mark.django_db
def test_pending_token_exchange_to_non_active_company_switches_through_the_command(multi_company_user, second_company):
    user = multi_company_user
    client = _client()
    with override_settings(TESTING=False):
        step1 = client.post(reverse("accounts:login"), {"email": user.email, "password": PASSWORD}, format="json")
        assert step1.status_code == 200 and step1.data["detail"] == "choose_company", step1.data
        step2 = client.post(
            reverse("accounts:login"),
            {"pending_login_token": step1.data["pending_login_token"], "company_id": second_company.id},
            format="json",
        )
    _assert_switched_to(user, second_company, step2)


# =============================================================================
# Shopify session login (embedded)
# =============================================================================


def _session_token(shop=SHOP, secret=TEST_SECRET, audience=TEST_API_KEY):
    now = int(time.time())
    payload = {
        "iss": f"https://{shop}/admin",
        "dest": f"https://{shop}",
        "aud": audience,
        "sub": SUB,
        "exp": now + 60,
        "nbf": now - 5,
        "iat": now,
        "jti": f"jti-{uuid4().hex[:8]}",
        "sid": f"sid-{uuid4().hex[:8]}",
    }
    return pyjwt.encode(payload, secret, algorithm="HS256")


@pytest.mark.django_db
def test_shopify_session_login_aligns_active_company_through_the_command(multi_company_user, second_company):
    """The store belongs to the user's OTHER company: session login must switch
    active_company to the store's company (the claim the recovery token
    carries) — through the canonical writer."""
    user = multi_company_user
    store = ShopifyStore.objects.create(
        company=second_company, shop_domain=SHOP, access_token="shpat_active", status=ShopifyStore.Status.ACTIVE
    )
    membership = CompanyMembership.objects.get(user=user, company=second_company)
    bind_shopify_user(store=store, shopify_sub=SUB, membership=membership, actor_user=user)

    with (
        patch.object(sc_commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(sc_commands, "SHOPIFY_API_KEY", TEST_API_KEY),
        override_settings(TESTING=False),
    ):
        resp = _client().post(
            reverse("accounts:shopify-session-login"), {"session_token": _session_token()}, format="json"
        )

    _assert_switched_to(user, second_company, resp)
    assert resp.data["company_id"] == second_company.id
