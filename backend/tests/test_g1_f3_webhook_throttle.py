# tests/test_g1_f3_webhook_throttle.py
"""
G1 preflight F3 — dedicated throttle posture for the webhook ingress routes.

Pins the whole F3 contract:

  1. BOTH Shopify-capable webhook views (the same set the runbook's §I4
     enumeration lists) carry exactly the one dedicated
     ``platform_webhook`` scope — the shared anon bucket no longer applies
     to those routes.
  2. Every other endpoint keeps today's default throttle stack untouched.
  3. Over-limit on a webhook route is a retryable 429 (with Retry-After),
     never a discarding 200, and the budget frees again when the window
     rolls over.
  4. Exhausting the webhook budget does not consume the general anon
     bucket (and vice versa the buckets are keyed by different scopes).

Throttling runs in DRF's ``initial()`` BEFORE the view body — before HMAC
verification — so the probes here deliberately post WITHOUT a valid HMAC:
a 401 response still proves the request passed the throttle; a 429 proves
it did not.
"""

import json

import pytest
from django.core.cache import cache
from django.test import Client
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from accounts.views import CsrfTokenView
from platform_connectors.throttles import PlatformWebhookThrottle
from platform_connectors.views import PlatformWebhookView
from shopify_connector.views import ShopifyWebhookView

SHOPIFY_WEBHOOK_URL = "/api/shopify/webhooks/"
SHOPIFY_WEBHOOK_URL_NO_SLASH = "/api/shopify/webhooks"
PLATFORM_WEBHOOK_URL = "/api/platforms/shopify/webhooks/"
CSRF_URL = "/api/auth/csrf/"


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
def tiny_rate(monkeypatch):
    """Shrink the dedicated scope to 3/min and take control of the throttle
    clock, so over-limit and window-expiry are deterministic (no sleeps).
    Returns the clock dict — bump ``clock["now"]`` to roll the window."""
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(PlatformWebhookThrottle, "THROTTLE_RATES", {"platform_webhook": "3/min"})
    monkeypatch.setattr(PlatformWebhookThrottle, "timer", lambda self: clock["now"])
    return clock


def _post_webhook(path):
    """An unauthenticated probe post (no HMAC): 401 = passed the throttle,
    429 = throttled. Bodies never reach a handler."""
    return Client().post(path, data=json.dumps({}), content_type="application/json")


# =============================================================================
# 1. Scope assignment — the lockstep pin
# =============================================================================


def test_both_webhook_views_use_only_the_dedicated_scope(settings):
    # The set of views pinned here must stay in lockstep with the runbook's
    # §I4 Shopify-capable webhook route enumeration:
    #   /api/shopify/webhooks[/]        -> ShopifyWebhookView
    #   /api/platforms/<slug>/webhooks/ -> PlatformWebhookView
    # If a new Shopify-capable webhook route appears, it must join BOTH the
    # enumeration and this throttle scope.
    assert ShopifyWebhookView.throttle_classes == [PlatformWebhookThrottle]
    assert PlatformWebhookView.throttle_classes == [PlatformWebhookThrottle]
    assert PlatformWebhookThrottle.scope == "platform_webhook"
    # The rate is registered (the founder-confirmed number).
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["platform_webhook"] == "120/minute"


def test_other_endpoints_keep_todays_default_throttle_stack(settings):
    # F3 changes ONLY the webhook routes. The global default stack and the
    # shared anon bucket stay exactly as they were.
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] == [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ]
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["anon"] == "100/hour"
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["user"] == "1000/hour"
    # A representative anonymous endpoint has no per-view override — it
    # rides the defaults, untouched by this PR.
    assert "throttle_classes" not in CsrfTokenView.__dict__
    assert CsrfTokenView.throttle_classes == APIView.throttle_classes


# =============================================================================
# 2. Over-limit behavior on both routes
# =============================================================================


@pytest.mark.django_db
def test_shopify_webhook_over_limit_is_retryable_429_then_allowed_again(tiny_rate):
    # Under the limit: the throttle admits the probes (the view 401s them —
    # HMAC is still the authenticity gate, AFTER the throttle).
    for _ in range(3):
        assert _post_webhook(SHOPIFY_WEBHOOK_URL).status_code == 401

    # Over the limit: a retryable non-success. Shopify treats any non-2xx as
    # a failed delivery and redelivers (8 retries over 4 hours) — a 200 here
    # would silently discard the delivery instead.
    throttled = _post_webhook(SHOPIFY_WEBHOOK_URL)
    assert throttled.status_code == 429
    assert throttled.headers.get("Retry-After") is not None

    # Window rolls over -> the route admits requests again.
    tiny_rate["now"] += 61.0
    assert _post_webhook(SHOPIFY_WEBHOOK_URL).status_code == 401


@pytest.mark.django_db
def test_platform_webhook_over_limit_is_retryable_429(tiny_rate):
    for _ in range(3):
        assert _post_webhook(PLATFORM_WEBHOOK_URL).status_code == 401

    throttled = _post_webhook(PLATFORM_WEBHOOK_URL)
    assert throttled.status_code == 429
    assert throttled.headers.get("Retry-After") is not None


@pytest.mark.django_db
def test_no_slash_shopify_route_shares_the_same_budget(tiny_rate):
    # Shopify's admin registers the no-slash form (the urls.py comment); both
    # bindings resolve to the same view and must burn ONE budget, not two.
    assert _post_webhook(SHOPIFY_WEBHOOK_URL_NO_SLASH).status_code == 401
    assert _post_webhook(SHOPIFY_WEBHOOK_URL).status_code == 401
    assert _post_webhook(SHOPIFY_WEBHOOK_URL_NO_SLASH).status_code == 401
    assert _post_webhook(SHOPIFY_WEBHOOK_URL).status_code == 429


# =============================================================================
# 3. Bucket isolation
# =============================================================================


@pytest.mark.django_db
def test_webhook_exhaustion_leaves_the_general_anon_bucket_untouched(tiny_rate):
    # Drive the webhook scope to 429 from this client IP...
    for _ in range(3):
        _post_webhook(SHOPIFY_WEBHOOK_URL)
    assert _post_webhook(SHOPIFY_WEBHOOK_URL).status_code == 429

    # ...and the anon-scope counter for this ident recorded NONE of it —
    # the discriminating proof that webhook volume no longer drains the
    # bucket login/register/OAuth-callback traffic depends on. (Key built
    # from DRF's own cache_format so a format change can't silently
    # vacuate this assertion into a None-is-None pass.)
    anon_key = AnonRateThrottle.cache_format % {"scope": AnonRateThrottle.scope, "ident": "127.0.0.1"}
    webhook_key = PlatformWebhookThrottle.cache_format % {
        "scope": PlatformWebhookThrottle.scope,
        "ident": "127.0.0.1",
    }
    assert cache.get(webhook_key), "sanity: the webhook scope did record its history"
    assert cache.get(anon_key) is None

    # ...and the SAME IP still passes an ordinary anon endpoint: the scopes
    # key different cache entries, so webhook volume can no longer starve
    # login/register/OAuth-callback traffic (or the reverse).
    assert Client().get(CSRF_URL).status_code == 200
