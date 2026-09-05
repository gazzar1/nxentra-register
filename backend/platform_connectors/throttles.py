# platform_connectors/throttles.py
"""
Dedicated throttle scope for the platform webhook ingress (G1 preflight F3).

Both Shopify-capable webhook routes share this one scope, in lockstep with
the fresh-isolated-pilot runbook's §I4 route enumeration:

  - /api/shopify/webhooks[/]         (shopify_connector.views.ShopifyWebhookView)
  - /api/platforms/<slug>/webhooks/  (platform_connectors.views.PlatformWebhookView)

DRF throttling runs in ``APIView.initial()``, before the view body — i.e.
before either view's HMAC verification. The scope is therefore a per-client
budget for unauthenticated traffic on these routes; the HMAC check inside
each view remains the authenticity gate. Keying
(``AnonRateThrottle.get_ident``) is deliberately unchanged from how the
shared anon bucket keyed these routes before: whether an ident maps 1:1 to
a client IP depends on the deployment's proxy X-Forwarded-For posture,
which the runbook's per-deployment F2/F3 evidence records. Under a correct
proxy posture a third party burning its own budget cannot starve the
platform's delivery IPs.

An over-limit request gets DRF's standard 429 with a Retry-After header — a
retryable non-success (Shopify retries any non-2xx delivery up to 8 times
over 4 hours), never a discarding 200.
"""

from rest_framework.throttling import AnonRateThrottle


class PlatformWebhookThrottle(AnonRateThrottle):
    """
    Rate limit the webhook ingress routes, per client IP.

    Default: 120/minute per IP.
    Configured via settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['platform_webhook']
    """

    scope = "platform_webhook"
