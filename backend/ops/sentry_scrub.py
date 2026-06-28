"""Sentry ``before_send`` scrubbing — keep secrets AND customer PII out of error
telemetry.

Two classes of sensitive data must never ride along in an error event:

1. **Provider credentials.** A merchant's Stripe restricted key first enters the
   system at ``POST /stripe/connect/`` as ``{"credential": "rk_live_…"}``. The
   Django Sentry integration captures POST bodies on errors by default
   (``max_request_body_size`` defaults to "medium"), so without scrubbing an
   unhandled exception during connect would ship the live key to Sentry.

2. **Customer PII** (A123). ``send_default_pii=False`` stops the SDK from
   auto-attaching user/request PII, but PII can still leak through the *content*
   of an exception message or log argument — e.g. a database error whose
   parameter list contains a customer's email, a breadcrumb carrying a phone
   number, or a GET query string with ``?customer_name=…``. Those land in
   ``event['logentry']['message']``, ``event['exception']['values'][*]['value']``,
   breadcrumb messages, and ``event['request']`` (body + query string).

This module redacts, anywhere in the event (it walks the whole structure):

  * the request body of sensitive endpoints (the connect endpoint) entirely;
  * the request query string, parsed into pairs so PII-named params are caught;
  * any field whose NAME looks credential- or PII-like (credential, api_key … /
    email, phone, address, ssn, card_number, iban, bank_account …);
  * any string VALUE matching a known secret pattern (rk_/sk_/pk_/whsec_/shpat_…)
    or a PII pattern (email, phone, Luhn-valid card number, IBAN, or an Egyptian
    14-digit national ID), even under a benign field name or inside a stack frame.

PII scrubbing deliberately errs toward over-redaction (privacy > debuggability)
for a financial app handling customer data. Guardrails keep it from gutting
useful events: PII key matching is anchored to whole segments (so ``mobile`` does
not redact ``is_mobile`` and ``address`` does not redact ``ip_address``/
``mac_address``); the generic key ``name`` is NOT treated as PII; and card-number
detection is gated by a Luhn check (note: ~10% of random 13–19 digit IDs pass
Luhn and will be redacted — an accepted, safe-direction false positive). Two
residuals are inherent and out of scope: a personal name appearing in free text
with no field hint cannot be regex-matched, and the pre-existing secret-key
matcher uses substring matching.

``_scrub`` is non-mutating (it rebuilds dicts/lists). ``scrub_event`` may mutate
``event['request']`` in place — acceptable because Sentry owns the event and
discards it after ``before_send``.

The scrubbers are pure functions so they can be unit-tested without a live Sentry
transport. Wire ``before_send`` into ``sentry_sdk.init``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode

REDACTED = "[redacted]"

# Field names whose VALUE is a secret regardless of its content (pre-existing;
# substring match is intentionally broad here).
_SECRET_KEY_RE = re.compile(
    r"(credential|api[_-]?key|secret|token|password|passwd|authorization|"
    r"access[_-]?key|webhook[_-]?secret|client[_-]?secret|credential_ref)",
    re.IGNORECASE,
)

# Field names whose VALUE is customer PII (A123). Wrapped so each term matches
# only as a whole "segment" (bounded by start/end or a `_ - .` separator), which
# stops short tokens from swallowing benign keys (e.g. `pan` no longer hits
# `company`, `tax_id` no longer hits `syntax_id`, `mobile` requires a phone-ish
# suffix so `is_mobile` survives). Bare `name` is intentionally absent.
_PII_KEY_RE = re.compile(
    r"(?:^|[_\-.])"
    r"(?:e[-_]?mail"
    r"|phone|telephone|mobile[_-]?(?:number|phone|no)"
    r"|address|address[12]|street|postal|post[_-]?code|zip|zip[_-]?code"
    r"|ssn|social[_-]?security|national[_-]?id|tax[_-]?id|vat[_-]?(?:number|no|id)"
    r"|first[_-]?name|last[_-]?name|full[_-]?name|customer[_-]?name|account[_-]?holder(?:[_-]?name)?|card[_-]?holder"
    r"|date[_-]?of[_-]?birth|dob"
    r"|card[_-]?number|pan"
    r"|iban|bank[_-]?account|account[_-]?number|routing[_-]?number|sort[_-]?code|swift|bic)"
    r"(?:$|[_\-.])",
    re.IGNORECASE,
)

# Network-address keys are NOT customer PII — keep them for incident triage even
# though `address` is a PII term (this overrides the `address` match).
_NETWORK_ADDR_RE = re.compile(
    r"(?:^|[_\-.])(?:ip|ipv4|ipv6|mac|remote|peer)[_-]?addr(?:ess)?(?:$|[_\-.])", re.IGNORECASE
)

# Value patterns that look like a live secret even under a benign field name.
# Stripe (rk_/sk_/pk_/whsec_) + Shopify (shpat_/shprt_/shpss_/shpca_) prefixes.
# Underscores are part of the token body (rk_live_<random>) so the FULL key is
# consumed — matching only `rk_live` would leave the secret random tail exposed.
_SECRET_VALUE_RE = re.compile(r"\b(rk|sk|pk|whsec|shpat|shprt|shpss|shpca)_[A-Za-z0-9_]{4,}")

# PII value patterns (A123). All bounded → linear time (no catastrophic
# backtracking on attacker-influenceable exception text; before_send runs inline
# on the capturing thread, so a quadratic regex here would be a DoS vector).
# The local part is length-capped at 64 (RFC 5321) — that bound, not a possessive
# quantifier, is what neutralises the ReDoS, so it stays portable across Pythons.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]{1,64}@(?:[A-Za-z0-9\-]{1,63}\.){1,10}[A-Za-z]{2,24}\b")
# Phone: international (+ or 00), parenthesized, Egyptian mobile/landline, or
# NANP-grouped. The "+" branches require digit GROUPS (or ≥8 contiguous digits)
# so signed decimals/deltas like "+14.000000" or "+123 -456" are not eaten.
_PHONE_RE = re.compile(
    # `(?<!\d)` keeps the +/00 prefix from matching mid-number (else "00" inside an
    # opaque id like 5000000000123 would be partially redacted).
    r"(?<!\d)(?:\+|00)\d{8,14}(?!\d)"  # compact intl: +201001234567 / 00201001234567
    r"|(?<!\d)(?:\+|00)\d{1,3}(?:[\s\-]\d{2,4}){2,5}"  # grouped intl:  +1 415 555 1234
    r"|\(\d{2,4}\)[\s\-]?\d{3,4}[\s\-]?\d{4}"  # parenthesized: (415) 555-1234
    r"|\b01[0125]\d{8}\b"  # Egyptian mobile: 01X XXXXXXXX
    r"|\b0[23]\d{7,8}\b"  # Egyptian landline: 02/03 + 7-8 digits
    r"|\b\d{3}[.\-\s]\d{3}[.\-\s]\d{4}\b"  # NANP: 415-555-1234 / 415.555.1234
)
# IBAN: 2-letter country + 2 check digits + 11–30 alphanumerics (Egypt = 29).
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
# Egyptian national ID: 14 digits, century digit 2 (1900s) or 3 (2000s). Distinct
# from the Luhn-PAN path (national IDs aren't Luhn-structured) and from 13-digit
# Shopify IDs (this is exactly 14 digits).
_NATIONAL_ID_RE = re.compile(r"\b[23]\d{13}\b")
# Card-number candidate: 13–19 digits with optional single space/dash separators,
# gated by a Luhn check (below) so random long IDs aren't redacted as PANs.
_PAN_CANDIDATE_RE = re.compile(r"\b\d(?:[ \-]?\d){12,18}\b")

# Endpoints whose request body is secret in its entirety.
_SENSITIVE_PATHS = ("/stripe/connect/",)


def _luhn_ok(digits: str) -> bool:
    """True if ``digits`` passes the Luhn checksum (real card numbers do)."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _redact_pan(match: re.Match) -> str:
    """Redact a digit run only if it's a Luhn-valid 13–19 digit card number."""
    digits = re.sub(r"\D", "", match.group(0))
    if 13 <= len(digits) <= 19 and _luhn_ok(digits):
        return REDACTED
    return match.group(0)


def _scrub_text(text: str) -> str:
    """Redact secret- and PII-looking substrings from one string value."""
    text = _SECRET_VALUE_RE.sub(REDACTED, text)
    text = _EMAIL_RE.sub(REDACTED, text)
    text = _IBAN_RE.sub(REDACTED, text)
    text = _NATIONAL_ID_RE.sub(REDACTED, text)
    text = _PAN_CANDIDATE_RE.sub(_redact_pan, text)
    text = _PHONE_RE.sub(REDACTED, text)
    return text


def _is_sensitive_key(key: Any) -> bool:
    """True if a field NAME implies its value is a secret or customer PII."""
    k = str(key)
    if _SECRET_KEY_RE.search(k):
        return True
    # PII by name, but never redact network addresses (ip/mac) — those are triage
    # signal, not customer PII, and only collide via the `address` term.
    return bool(_PII_KEY_RE.search(k) and not _NETWORK_ADDR_RE.search(k))


def _scrub_query_string(qs: str) -> str:
    """Redact PII/secret params from a raw URL query string, by name and value."""
    pairs = parse_qsl(qs, keep_blank_values=True)
    if not pairs:  # not a standard k=v query string — fall back to value scrubbing
        return _scrub_text(qs)
    return urlencode([(k, REDACTED if _is_sensitive_key(k) else _scrub_text(v)) for k, v in pairs])


def _scrub(value: Any) -> Any:
    """Recursively redact secret/PII-named keys and secret/PII-looking values.

    Rebuilds containers (returns new dicts/lists) rather than mutating in place,
    so the caller's original structures are left untouched.
    """
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, dict):
        return {k: (REDACTED if _is_sensitive_key(k) else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in value]
    return value


def scrub_event(event: Any, hint: Any = None) -> Any:
    """Sentry ``before_send`` hook. Returns the scrubbed event (never None — we
    redact, we don't drop the error)."""
    if not isinstance(event, dict):
        return event

    request = event.get("request")
    if isinstance(request, dict):
        # Drop the whole request body for sensitive endpoints before generic
        # scrubbing (a connect body is secret in its entirety).
        url = str(request.get("url") or "")
        if any(p in url for p in _SENSITIVE_PATHS) and request.get("data") is not None:
            request["data"] = REDACTED
        # Query strings arrive as ONE raw string, so the key-name half of the
        # scrubber can't see into them — parse and scrub the params explicitly.
        qs = request.get("query_string")
        if isinstance(qs, str) and qs:
            request["query_string"] = _scrub_query_string(qs)

    return _scrub(event)


# Convenience alias for sentry_sdk.init(before_send=...).
before_send = scrub_event
