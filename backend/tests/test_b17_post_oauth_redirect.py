# tests/test_b17_post_oauth_redirect.py
"""
B17 (2026-06-07) — Post-OAuth success redirects to the Shopify admin
embedded app URL, not standalone Nxentra.

Discovered in the B13 live test on 2026-06-07: a merchant who clicked
Disconnect → Connect from inside the iframe was correctly broken out to
top-level for OAuth (B13), but our callback then redirected to
`app.nxentra.com/shopify/settings?connected=true` — standalone Nxentra
without the merchant's iframe-scoped session. AppLayout bounced them to
`/login`. After logging in they landed at standalone Nxentra, lost from
the Shopify admin context they started in.

Fix: when OAuth completes successfully for a company that has already
finished onboarding, redirect to
`https://admin.shopify.com/store/<shop_subdomain>/apps/<client_id>`.
Shopify re-opens our app in the embedded iframe, embedded.tsx runs the
session-login + token-exchange handshake, and the merchant lands on the
Connected Store page — same UX as a fresh App Store launch.

Tests pin:
  - the URL builder formats the admin link correctly
  - the callback redirects to the admin URL on success
  - errors STILL go to standalone Nxentra so the merchant can read them
  - onboarding-incomplete companies STILL land on the wizard
"""

import pytest
from django.urls import reverse

from shopify_connector.models import ShopifyStore
from shopify_connector.views import _shopify_admin_app_url

# =============================================================================
# URL builder
# =============================================================================


def test_admin_app_url_strips_myshopify_suffix():
    from django.test import override_settings

    with override_settings(SHOPIFY_API_KEY="2258d6303a3672a381fe7606c2d2917b"):
        url = _shopify_admin_app_url("nxentra-reviewer-test-1.myshopify.com")
    assert url == ("https://admin.shopify.com/store/nxentra-reviewer-test-1/apps/2258d6303a3672a381fe7606c2d2917b")


def test_admin_app_url_handles_no_suffix():
    """Shouldn't ever happen in practice — every Shopify shop_domain ends
    in .myshopify.com — but the helper shouldn't crash if given a stripped
    value."""
    from django.test import override_settings

    with override_settings(SHOPIFY_API_KEY="abc123"):
        url = _shopify_admin_app_url("merchant.myshopify.com")
    assert url == "https://admin.shopify.com/store/merchant/apps/abc123"


# =============================================================================
# Callback redirect targets
# =============================================================================


@pytest.mark.django_db
def test_callback_success_with_embedded_state_redirects_to_shopify_admin(api_client, company, monkeypatch):
    """B17.2: an OAuth state ending with `.embedded` means the merchant
    started the install from inside the Shopify admin iframe. Send them
    back to the admin embedded app URL so the iframe re-opens at the
    connected state."""
    company.onboarding_completed = True
    company.save(update_fields=["onboarding_completed"])

    nonce = "test-nonce-b17-embedded"
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="b17merchant.myshopify.com",
        oauth_nonce=nonce,
        status=ShopifyStore.Status.PENDING,
    )

    def _fake_complete_oauth(c, shop, code, state):
        store.status = ShopifyStore.Status.ACTIVE
        store.save(update_fields=["status"])
        from shopify_connector.commands import CommandResult

        return CommandResult.ok({"store": store})

    monkeypatch.setattr("shopify_connector.commands.complete_oauth", _fake_complete_oauth)

    from django.test import override_settings

    with override_settings(SHOPIFY_API_KEY="cid_b17"):
        response = api_client.get(
            reverse("shopify-callback"),
            {
                "code": "fake_code",
                "shop": "b17merchant.myshopify.com",
                "state": nonce + ".embedded",
            },
        )

    assert response.status_code == 302
    assert response.url == "https://admin.shopify.com/store/b17merchant/apps/cid_b17", response.url


@pytest.mark.django_db
def test_callback_success_without_suffix_stays_on_standalone(api_client, company, monkeypatch):
    """B17.2: a bare-nonce state (no `.embedded` suffix) means the
    merchant started OAuth from standalone Nxentra. After completion
    they stay on standalone /shopify/settings — teleporting them into
    the Shopify admin iframe would yank them out of the context they
    were just in."""
    company.onboarding_completed = True
    company.save(update_fields=["onboarding_completed"])

    nonce = "test-nonce-b17-standalone"
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="b17standalone.myshopify.com",
        oauth_nonce=nonce,
        status=ShopifyStore.Status.PENDING,
    )

    def _fake_complete_oauth(c, shop, code, state):
        store.status = ShopifyStore.Status.ACTIVE
        store.save(update_fields=["status"])
        from shopify_connector.commands import CommandResult

        return CommandResult.ok({"store": store})

    monkeypatch.setattr("shopify_connector.commands.complete_oauth", _fake_complete_oauth)

    response = api_client.get(
        reverse("shopify-callback"),
        {"code": "fake_code", "shop": "b17standalone.myshopify.com", "state": nonce},
    )

    assert response.status_code == 302
    assert response.url == "/shopify/settings?connected=true", response.url
    assert "admin.shopify.com" not in response.url


@pytest.mark.django_db
def test_install_url_appends_embedded_suffix_to_state(db, company):
    """B17.2: get_install_url with embedded=True must put the
    `.embedded` suffix on the OAuth `state` query parameter so the
    callback can detect the iframe context. The stored
    ShopifyStore.oauth_nonce stays bare (no DB-schema change)."""
    from shopify_connector.commands import EMBEDDED_STATE_SUFFIX, get_install_url

    result = get_install_url(company, "embedflag.myshopify.com", embedded=True)

    assert f"&state={result['nonce']}{EMBEDDED_STATE_SUFFIX}" in result["url"]
    # The stored row has the bare nonce only — suffix isn't persisted.
    row = ShopifyStore.objects.get(company=company, shop_domain="embedflag.myshopify.com")
    assert row.oauth_nonce == result["nonce"]
    assert EMBEDDED_STATE_SUFFIX not in row.oauth_nonce


@pytest.mark.django_db
def test_install_url_without_embedded_flag_omits_suffix(db, company):
    """B17.2: the default (embedded=False) keeps the bare nonce in the
    state — matches all pre-B17.2 install URLs."""
    from shopify_connector.commands import EMBEDDED_STATE_SUFFIX, get_install_url

    result = get_install_url(company, "noflag.myshopify.com")

    assert f"&state={result['nonce']}" in result["url"]
    assert EMBEDDED_STATE_SUFFIX not in result["url"]


@pytest.mark.django_db
def test_callback_error_stays_on_standalone_so_merchant_sees_error(api_client, company, monkeypatch):
    """When OAuth completion fails, the merchant has to see the error
    message. The admin embedded app can't surface server-side error
    strings, so errors stay on standalone Nxentra."""
    company.onboarding_completed = True
    company.save(update_fields=["onboarding_completed"])

    nonce = "test-nonce-b17-err"
    ShopifyStore.objects.create(
        company=company,
        shop_domain="b17err.myshopify.com",
        oauth_nonce=nonce,
        status=ShopifyStore.Status.PENDING,
    )

    def _fake_complete_oauth(*_a, **_kw):
        from shopify_connector.commands import CommandResult

        return CommandResult.fail("Token exchange failed")

    monkeypatch.setattr("shopify_connector.commands.complete_oauth", _fake_complete_oauth)

    response = api_client.get(
        reverse("shopify-callback"),
        {"code": "fake", "shop": "b17err.myshopify.com", "state": nonce},
    )

    assert response.status_code == 302
    assert response.url.startswith("/shopify/settings?error="), response.url
    assert "admin.shopify.com" not in response.url


@pytest.mark.django_db
def test_callback_onboarding_incomplete_stays_on_wizard(api_client, company, monkeypatch):
    """A merchant mid-onboarding wizard who just connected Shopify should
    land back on the wizard, not be teleported into the Shopify admin
    embedded app where they have no way to finish onboarding."""
    company.onboarding_completed = False
    company.save(update_fields=["onboarding_completed"])

    nonce = "test-nonce-b17-onb"
    ShopifyStore.objects.create(
        company=company,
        shop_domain="b17onboard.myshopify.com",
        oauth_nonce=nonce,
        status=ShopifyStore.Status.PENDING,
    )

    def _fake_complete_oauth(*_a, **_kw):
        from shopify_connector.commands import CommandResult

        return CommandResult.ok({})

    monkeypatch.setattr("shopify_connector.commands.complete_oauth", _fake_complete_oauth)

    response = api_client.get(
        reverse("shopify-callback"),
        {"code": "fake", "shop": "b17onboard.myshopify.com", "state": nonce},
    )

    assert response.status_code == 302
    assert response.url == "/onboarding/setup?shopify_connected=true"
    assert "admin.shopify.com" not in response.url
