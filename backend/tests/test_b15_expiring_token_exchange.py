# tests/test_b15_expiring_token_exchange.py
"""
B15 (2026-06-07) — Token-exchange must request expiring offline tokens.

Discovered by direct Shopify API probe on 2026-06-07: every store ever
connected via the embedded token-exchange path (B8) ended up with a
non-expiring `shpat_*` token. Shopify's Admin API now rejects those:

    [API] Non-expiring access tokens are no longer accepted for the Admin API.
    Start using expiring offline tokens.

A122 fixed this for the OAuth code-grant path by sending `expiring=1` on
the token exchange request. Token-exchange (B8) was shipped after A122
but never picked up the same flag — so every embedded launch was minting
deprecated tokens silently. The A120 resilient handler then mis-classified
the resulting 403 as "scope not granted on this install", which sent us
down a rabbit hole assuming the install was the problem.

Two complementary fixes:
  1. complete_oauth_token_exchange now sends `expiring=1`.
  2. _shopify_denial_reason inspects Shopify's error body so future 403s
     are tagged with the actual cause (non_expiring_token vs scope_missing
     vs payments_disabled), and the user-facing toast in sync_products is
     wired to surface the right remediation.
"""

import time
from unittest.mock import patch

import jwt as pyjwt
import pytest

from shopify_connector import commands

TEST_SECRET = "shpss_b15_test_secret"
TEST_API_KEY = "test_client_id_b15"


def _make_session_token(shop="b15.myshopify.com", expires_in=60) -> str:
    now = int(time.time())
    payload = {
        "iss": f"https://{shop}/admin",
        "dest": f"https://{shop}",
        "aud": TEST_API_KEY,
        "sub": "1",
        "exp": now + expires_in,
        "nbf": now - 5,
        "iat": now,
        "jti": "b15-jti",
        "sid": "b15-sid",
    }
    return pyjwt.encode(payload, TEST_SECRET, algorithm="HS256")


# =============================================================================
# B15: token-exchange now requests expiring tokens
# =============================================================================


@pytest.mark.django_db
def test_token_exchange_requests_expiring_flag(db, company, monkeypatch):
    """Token-exchange POST body must include `expiring=1` so Shopify returns
    a refreshable token instead of the deprecated non-expiring `shpat_*`."""
    captured = {}

    def _fake_post(url, json, timeout):
        captured["json"] = json

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "access_token": "shpat_b15_test",
                    "refresh_token": "shprt_b15_test",
                    "expires_in": 3600,
                    "refresh_token_expires_in": 7776000,
                    "scope": "read_products",
                }

        return _Resp()

    monkeypatch.setattr("shopify_connector.commands.requests.post", _fake_post)
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_warehouse", lambda s: None)
    monkeypatch.setattr("shopify_connector.commands._ensure_shopify_sales_setup", lambda s: None)
    monkeypatch.setattr("events.emitter.emit_event_no_actor", lambda **kw: None)

    with (
        patch.object(commands, "SHOPIFY_API_SECRET", TEST_SECRET),
        patch.object(commands, "SHOPIFY_API_KEY", TEST_API_KEY),
    ):
        token = _make_session_token()
        result = commands.complete_oauth_token_exchange(company, token)

    assert result.success, result.error
    assert captured["json"].get("expiring") == 1, (
        "token-exchange must request expiring tokens; without expiring=1 "
        "Shopify returns deprecated non-expiring `shpat_*` tokens that the "
        "Admin API now rejects"
    )


# =============================================================================
# B15: denial-reason classifier
# =============================================================================


class _FakeResp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def _fake_exc(status_code: int, body: str):
    import requests as _rq

    exc = _rq.HTTPError(f"{status_code} error")
    exc.response = _FakeResp(status_code, body)
    return exc


def test_denial_reason_detects_non_expiring_token():
    body = (
        '{"errors":"[API] Non-expiring access tokens are no longer accepted '
        'for the Admin API. Start using expiring offline tokens"}'
    )
    assert commands._shopify_denial_reason(_fake_exc(403, body)) == "non_expiring_token"


def test_denial_reason_detects_scope_missing():
    body = '{"errors":"This action requires merchant approval for read_products scope."}'
    assert commands._shopify_denial_reason(_fake_exc(403, body)) == "scope_missing"


def test_denial_reason_returns_none_for_unknown_body():
    body = '{"errors":"Internal server error"}'
    assert commands._shopify_denial_reason(_fake_exc(500, body)) is None


def test_denial_reason_returns_not_found_for_404():
    body = '{"errors":"Not Found"}'
    assert commands._shopify_denial_reason(_fake_exc(404, body)) == "not_found"


def test_denial_reason_no_response_returns_none():
    import requests as _rq

    exc = _rq.ConnectionError("network down")
    assert commands._shopify_denial_reason(exc) is None
