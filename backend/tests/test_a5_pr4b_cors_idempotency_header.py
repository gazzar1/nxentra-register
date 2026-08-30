"""A5-PR4b — CORS transport for the A177 ``Idempotency-Key`` header.

PR #134 wired the manual journal create door to read the ``Idempotency-Key``
request header, but the browser only sends a custom header after a successful
CORS preflight, and django-cors-headers' default allow-list does not include
it.  The founder-authorized PR4b configuration exception appends exactly that
one header to the defaults (no wildcard, defaults preserved).

These tests exercise the REAL CorsMiddleware preflight behaviour through the
Django test client — not merely the Python tuple value.
"""

from corsheaders.defaults import default_headers
from django.conf import settings
from django.test import Client

JOURNAL_ENTRIES_PATH = "/api/accounting/journal-entries/"
ALLOWED_ORIGIN = "http://localhost:3000"
DISALLOWED_ORIGIN = "https://evil.example.com"


def _preflight(origin: str) -> "object":
    """A browser-shaped preflight for the journal-entry create request."""
    return Client().options(
        JOURNAL_ENTRIES_PATH,
        HTTP_ORIGIN=origin,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type, x-csrftoken, idempotency-key",
    )


def test_settings_allow_idempotency_key_and_preserve_every_default():
    allow = [h.lower() for h in settings.CORS_ALLOW_HEADERS]
    assert "idempotency-key" in allow
    # Every django-cors-headers default survives — the exception is additive.
    for default in default_headers:
        assert default.lower() in allow
    # Including at least the ones the app's requests actually ride on.
    for required in ("authorization", "content-type", "x-csrftoken"):
        assert required in allow
    # No wildcard allowance sneaks in.
    assert "*" not in allow


def test_preflight_from_allowed_origin_permits_the_idempotency_key_header():
    response = _preflight(ALLOWED_ORIGIN)
    # CorsMiddleware short-circuits an allowed preflight with 200.
    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    allowed_headers = response["Access-Control-Allow-Headers"].lower()
    assert "idempotency-key" in allowed_headers
    assert "content-type" in allowed_headers
    assert "x-csrftoken" in allowed_headers
    # Credentials mode stays on (cookie-JWT), never wildcarded.
    assert response["Access-Control-Allow-Credentials"] == "true"
    assert response["Access-Control-Allow-Origin"] != "*"


def test_preflight_from_disallowed_origin_gains_no_cors_grant():
    response = _preflight(DISALLOWED_ORIGIN)
    assert "Access-Control-Allow-Origin" not in response
    assert "Access-Control-Allow-Headers" not in response
