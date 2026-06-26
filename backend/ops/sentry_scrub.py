"""Sentry ``before_send`` scrubbing — keep secrets out of error telemetry.

A merchant's Stripe restricted key first enters the system at ``POST
/stripe/connect/`` as ``{"credential": "rk_live_…"}``. The Django Sentry
integration captures POST bodies on errors by default (``max_request_body_size``
defaults to "medium"), so without scrubbing an unhandled exception during connect
would ship the live key to Sentry. This module redacts:

  * the request body of sensitive endpoints (the connect endpoint) entirely;
  * any field whose NAME looks credential-like (credential, api_key, secret,
    token, password, authorization, …) anywhere in the event; and
  * any string VALUE that matches a known secret pattern (rk_/sk_/pk_/whsec_/
    shpat_/shprt_…), even under a benign field name or inside a stack-frame local.

The scrubbers are pure functions so they can be unit-tested without a live
Sentry transport. Wire ``before_send`` into ``sentry_sdk.init``.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[redacted]"

# Field names whose VALUE is secret regardless of its content.
_SECRET_KEY_RE = re.compile(
    r"(credential|api[_-]?key|secret|token|password|passwd|authorization|"
    r"access[_-]?key|webhook[_-]?secret|client[_-]?secret|credential_ref)",
    re.IGNORECASE,
)

# Value patterns that look like a live secret even under a benign field name.
# Stripe (rk_/sk_/pk_/whsec_) + Shopify (shpat_/shprt_/shpss_/shpca_) prefixes.
# Underscores are part of the token body (rk_live_<random>) so the FULL key is
# consumed — matching only `rk_live` would leave the secret random tail exposed.
_SECRET_VALUE_RE = re.compile(r"\b(rk|sk|pk|whsec|shpat|shprt|shpss|shpca)_[A-Za-z0-9_]{4,}")

# Endpoints whose request body is secret in its entirety.
_SENSITIVE_PATHS = ("/stripe/connect/",)


def _scrub(value: Any) -> Any:
    """Recursively redact secret-named keys and secret-looking string values.

    Rebuilds containers (returns new dicts/lists) rather than mutating in place,
    so the caller's original structures are left untouched.
    """
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub(REDACTED, value)
    if isinstance(value, dict):
        return {k: (REDACTED if _SECRET_KEY_RE.search(str(k)) else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in value]
    return value


def scrub_event(event: Any, hint: Any = None) -> Any:
    """Sentry ``before_send`` hook. Returns the scrubbed event (never None — we
    redact, we don't drop the error)."""
    if not isinstance(event, dict):
        return event

    # Drop the whole request body for sensitive endpoints before generic scrubbing
    # (a connect body is secret in its entirety, not just the credential field).
    request = event.get("request")
    if isinstance(request, dict):
        url = str(request.get("url") or "")
        if any(p in url for p in _SENSITIVE_PATHS) and request.get("data") is not None:
            request["data"] = REDACTED

    return _scrub(event)


# Convenience alias for sentry_sdk.init(before_send=...).
before_send = scrub_event
