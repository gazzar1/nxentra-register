# tests/test_a120_shopify_sync_403_resilience.py
"""
A120 — Shopify App Store rejection (ref 114779, 2026-06-01).

The reviewer's screencast showed two red "Failed to sync" toasts on a fresh
dev store (mec3xu-zd.myshopify.com):
  - "Failed to sync products." — Sentry: 403 from /admin/api/.../products.json
  - "Failed to sync payouts." — same 403 path on /shopify_payments/payouts.json

Both 403s are expected on a bare reviewer dev store:
  - read_products may not be granted by the install flow on every store
  - shopify_payments/* always 403s on stores without Shopify Payments enabled

The fix makes both sync commands return a successful CommandResult with
status="unavailable" + a human-readable message so the dashboard shows a
neutral informational toast instead of a destructive one. The user can
diagnose from the message; nothing about the app appears broken.
"""

from unittest.mock import patch

import pytest
import requests

from shopify_connector import commands, tasks
from shopify_connector.models import ShopifyStore


@pytest.fixture
def active_store(db, company):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="a120-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )


def _http_error_response(status_code: int) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = b'{"errors": "Forbidden"}'
    return resp


def _raise_http_error(status_code: int):
    """Return a callable that mocks the GraphQL POST to raise HTTPError(status_code)."""

    def _do(*args, **kwargs):
        resp = _http_error_response(status_code)
        err = requests.HTTPError(f"{status_code} Client Error: Forbidden", response=resp)
        raise err

    return _do


class _FakeGraphQLResponse:
    """A 200 response whose body carries a GraphQL errors array."""

    def __init__(self, body: dict):
        self._body = body
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def _graphql_access_denied(message: str):
    """Mock the GraphQL POST to return 200 + ACCESS_DENIED errors (the
    GraphQL equivalent of the old REST 403)."""

    def _do(*args, **kwargs):
        return _FakeGraphQLResponse(
            {
                "data": None,
                "errors": [
                    {
                        "message": message,
                        "extensions": {"code": "ACCESS_DENIED"},
                    }
                ],
            }
        )

    return _do


# All Admin API data reads go through the single GraphQL client now.
GRAPHQL_POST = "shopify_connector.graphql_client.requests.post"


# --------------------------------------------------------------------------
# sync_payouts
# --------------------------------------------------------------------------


def test_sync_payouts_403_returns_unavailable_not_failure(active_store):
    """Shopify Payments not enabled (the dev-store default) must not look like a crash."""
    with patch(GRAPHQL_POST, side_effect=_raise_http_error(403)):
        result = commands.sync_payouts(active_store)

    assert result.success, "403 on payouts must surface as a successful 'unavailable' result"
    assert result.data["status"] == "unavailable"
    assert "Shopify Payments" in result.data["message"]
    assert result.data["created"] == 0


def test_sync_payouts_5xx_still_fails(active_store):
    """Real Shopify outages should still surface as failures (so we don't hide bugs)."""
    with patch(GRAPHQL_POST, side_effect=_raise_http_error(503)):
        result = commands.sync_payouts(active_store)

    assert not result.success
    assert "Shopify API error" in result.error


# --------------------------------------------------------------------------
# sync_products
# --------------------------------------------------------------------------


def test_sync_products_403_returns_unavailable_not_failure(active_store):
    """read_products not granted on this install must not show 'Failed to sync products'."""
    with patch(GRAPHQL_POST, side_effect=_raise_http_error(403)):
        result = commands.sync_products(active_store)

    assert result.success
    assert result.data["status"] == "unavailable"
    assert "read_products" in result.data["message"] or "reconnect" in result.data["message"].lower()
    assert result.data["created"] == 0


def test_sync_products_5xx_still_fails(active_store):
    with patch(GRAPHQL_POST, side_effect=_raise_http_error(503)):
        result = commands.sync_products(active_store)

    assert not result.success


# --------------------------------------------------------------------------
# _sync_orders (used by the "Re-sync Orders (7d)" button)
# --------------------------------------------------------------------------


def test_sync_orders_403_returns_unavailable_status(active_store):
    """
    Mid-2026: Shopify started returning 403 on REST endpoints whose API
    version is past its support window. The Re-sync Orders button used to
    show a misleading "0 new, 0 already synced" success toast because the
    frontend didn't look at the status field. With A120, _sync_orders
    returns status="unavailable" + a message and the frontend branches on
    that.
    """
    with patch(GRAPHQL_POST, side_effect=_raise_http_error(403)):
        result = tasks._sync_orders(
            active_store,
            "2026-05-25T00:00:00+00:00",
            "2026-06-01T00:00:00+00:00",
        )

    assert result["status"] == "unavailable"
    assert result["created"] == 0
    assert result["fetched"] == 0
    assert "read_orders" in result["message"] or "reconnect" in result["message"].lower()


def test_sync_orders_5xx_still_returns_error_status(active_store):
    with patch(GRAPHQL_POST, side_effect=_raise_http_error(503)):
        result = tasks._sync_orders(
            active_store,
            "2026-05-25T00:00:00+00:00",
            "2026-06-01T00:00:00+00:00",
        )

    assert result["status"] == "error"
    assert "error" in result


# --------------------------------------------------------------------------
# GraphQL-level denials (post-REST-migration equivalents of the 403s above)
# --------------------------------------------------------------------------


def test_sync_products_graphql_access_denied_returns_unavailable(active_store):
    """GraphQL signals missing scope as 200 + ACCESS_DENIED, not HTTP 403."""
    with patch(
        GRAPHQL_POST,
        side_effect=_graphql_access_denied("Access denied for products field. Required access: read_products"),
    ):
        result = commands.sync_products(active_store)

    assert result.success
    assert result.data["status"] == "unavailable"


def test_sync_orders_graphql_access_denied_returns_unavailable(active_store):
    with patch(
        GRAPHQL_POST,
        side_effect=_graphql_access_denied("Access denied for orders field. Required access: read_orders"),
    ):
        result = tasks._sync_orders(
            active_store,
            "2026-05-25T00:00:00+00:00",
            "2026-06-01T00:00:00+00:00",
        )

    assert result["status"] == "unavailable"
    assert result["fetched"] == 0


def test_sync_payouts_null_payments_account_returns_unavailable(active_store):
    """No Shopify Payments comes back as shopifyPaymentsAccount: null, not an error."""

    def _null_account(*args, **kwargs):
        return _FakeGraphQLResponse({"data": {"shopifyPaymentsAccount": None}})

    with patch(GRAPHQL_POST, side_effect=_null_account):
        result = commands.sync_payouts(active_store)

    assert result.success
    assert result.data["status"] == "unavailable"
    assert "Shopify Payments" in result.data["message"]


# --------------------------------------------------------------------------
# API version constant
# --------------------------------------------------------------------------


def test_api_version_is_not_stale():
    """
    Guard against re-introducing the hardcoded 2025-01 version that triggered
    the rejection. Anything older than 2025-04 is past the 12-month support
    window as of 2026-06-01.
    """
    assert commands.SHOPIFY_API_VERSION >= "2025-04", (
        f"Bumped past Shopify's 12-month window: {commands.SHOPIFY_API_VERSION}"
    )


def test_api_root_helper_uses_constant(active_store):
    root = commands._shopify_api_root(active_store.shop_domain)
    assert active_store.shop_domain in root
    assert commands.SHOPIFY_API_VERSION in root
    assert "/admin/api/" in root
