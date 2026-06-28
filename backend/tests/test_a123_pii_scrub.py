# tests/test_a123_pii_scrub.py
"""A123 — the Sentry before_send scrubber redacts customer PII (email, phone,
card numbers) that can leak through exception messages, log arguments, and
breadcrumbs, even though ``send_default_pii=False`` stops auto-captured PII.

Guarantees:
  1. PII in event VALUES (exception value, logentry message, breadcrumb message,
     request body) is redacted wherever it appears.
  2. PII-named FIELDS are redacted by key, anywhere in the event.
  3. No over-redaction: opaque IDs, unix timestamps, non-Luhn long numbers, and
     the generic ``name`` field survive — events stay debuggable.
  4. Regression: the pre-existing credential/secret scrubbing still fires.
"""

import json
import time

from ops.sentry_scrub import REDACTED, _luhn_ok, scrub_event


def _blob(event):
    """Scrub then serialize, so a leak anywhere in the structure is detectable."""
    return json.dumps(scrub_event(event))


# ── 1. PII redacted in event VALUES (the real leak paths) ─────────────


def test_email_in_exception_value_is_redacted():
    event = {
        "exception": {
            "values": [{"type": "IntegrityError", "value": "duplicate key for customer john.doe@example.com"}]
        }
    }
    out = scrub_event(event)
    val = out["exception"]["values"][0]["value"]
    assert "john.doe@example.com" not in val
    assert REDACTED in val
    # The non-PII part of the message is preserved for debugging.
    assert "duplicate key for customer" in val
    assert out["exception"]["values"][0]["type"] == "IntegrityError"


def test_email_in_logentry_message_is_redacted():
    event = {"logentry": {"message": "sync failed for jane@acme.co", "params": []}}
    blob = _blob(event)
    assert "jane@acme.co" not in blob
    assert REDACTED in blob


def test_phone_in_breadcrumb_message_is_redacted():
    event = {"breadcrumbs": {"values": [{"message": "sent SMS to +20 100 123 4567", "category": "sms"}]}}
    blob = _blob(event)
    assert "+20 100 123 4567" not in blob
    assert "100 123 4567" not in blob
    assert REDACTED in blob


def test_egyptian_mobile_is_redacted():
    event = {"message": "customer mobile 01012345678 unreachable"}
    blob = _blob(event)
    assert "01012345678" not in blob
    assert REDACTED in blob


def test_separator_formatted_phone_is_redacted():
    event = {"message": "call 415-555-1234 for support"}
    blob = _blob(event)
    assert "415-555-1234" not in blob
    assert REDACTED in blob


def test_luhn_valid_card_number_is_redacted():
    # 4242 4242 4242 4242 is the canonical Luhn-valid test PAN.
    event = {"extra": {"detail": "charge on card 4242 4242 4242 4242 failed"}}
    blob = _blob(event)
    assert "4242 4242 4242 4242" not in blob
    assert "4242424242424242" not in blob
    assert REDACTED in blob


# ── 2. PII redacted by FIELD NAME, anywhere ───────────────────────────


def test_pii_named_fields_are_redacted_anywhere():
    event = {
        "extra": {
            "email": "secret@person.com",
            "phone": "01112223334",
            "billing_address": "12 Tahrir St, Cairo",
            "customer_name": "Jane Doe",
            "note": "ok to keep",
        },
        "contexts": {"order": {"shipping_address": "5 Nile Ave", "card_number": "4111111111111111"}},
    }
    out = scrub_event(event)
    assert out["extra"]["email"] == REDACTED
    assert out["extra"]["phone"] == REDACTED
    assert out["extra"]["billing_address"] == REDACTED
    assert out["extra"]["customer_name"] == REDACTED
    assert out["extra"]["note"] == "ok to keep"
    assert out["contexts"]["order"]["shipping_address"] == REDACTED
    assert out["contexts"]["order"]["card_number"] == REDACTED


# ── 3. No over-redaction — events stay debuggable ─────────────────────


def test_generic_name_field_is_not_redacted():
    # Bare `name` is deliberately excluded — redacting it would gut benign fields.
    event = {"transaction": "GET /api/x", "extra": {"name": "process_company_projections"}}
    out = scrub_event(event)
    assert out["extra"]["name"] == "process_company_projections"
    assert out["transaction"] == "GET /api/x"


def test_opaque_ids_and_timestamps_survive():
    event = {
        "extra": {
            "company_id": 41,
            "unix_ts": "1719500000",  # 10-digit timestamp, no separators
            "shopify_order_id": "5000000000123",  # 13-digit opaque id
            "long_non_luhn": "1234567890123",  # 13 digits, not Luhn-valid
        }
    }
    out = scrub_event(event)
    assert out["extra"]["company_id"] == 41
    assert out["extra"]["unix_ts"] == "1719500000"
    assert out["extra"]["shopify_order_id"] == "5000000000123"
    assert out["extra"]["long_non_luhn"] == "1234567890123"


def test_long_non_luhn_number_is_not_redacted_as_pan():
    # 1234567890123 fails Luhn → must survive (only real cards are redacted).
    assert not _luhn_ok("1234567890123")
    assert _luhn_ok("4242424242424242")
    event = {"message": "ref 1234567890123 processed"}
    assert "1234567890123" in _blob(event)


def test_benign_event_is_unchanged():
    event = {"message": "something broke", "extra": {"count": 3, "ok": True}}
    assert scrub_event(event) == event


# ── 4. Regression — existing secret scrubbing still fires ─────────────


def test_secret_value_still_redacted_alongside_pii():
    event = {"message": "connect failed rk_live_leaked for user bob@x.io"}
    blob = _blob(event)
    assert "rk_live_leaked" not in blob
    assert "bob@x.io" not in blob
    assert REDACTED in blob


def test_credential_named_field_still_redacted():
    event = {"extra": {"credential": "anything", "api_key": "whatever"}}
    out = scrub_event(event)
    assert out["extra"]["credential"] == REDACTED
    assert out["extra"]["api_key"] == REDACTED


def test_non_dict_passthrough():
    assert scrub_event(None) is None
    assert scrub_event("plain") == "plain"


# ── 5. adversarial-review hardening ───────────────────────────────────


def test_email_regex_is_linear_not_redos():
    # A crafted "a@a.a.a.…" string used to drive O(n^2) backtracking through the
    # before_send hook (a DoS, since Sentry runs it inline). The bounded local
    # part keeps it linear — this must complete near-instantly.
    payload = {"exception": {"values": [{"type": "ValueError", "value": "bad " + "a." * 40000}]}}
    start = time.perf_counter()
    scrub_event(payload)
    assert time.perf_counter() - start < 1.0


def test_iban_value_is_redacted():
    event = {"message": "transfer to EG380019000500000000263180002 returned"}
    blob = _blob(event)
    assert "EG380019000500000000263180002" not in blob
    assert REDACTED in blob


def test_bank_iban_and_account_fields_are_redacted():
    event = {"extra": {"bank_account": "1000200030004001", "iban": "EG38001900050000", "account_number": "12345678"}}
    out = scrub_event(event)
    assert out["extra"]["bank_account"] == REDACTED
    assert out["extra"]["iban"] == REDACTED
    assert out["extra"]["account_number"] == REDACTED


def test_egyptian_national_id_value_is_redacted():
    event = {"message": "KYC failed for NID 29801011234567"}
    blob = _blob(event)
    assert "29801011234567" not in blob
    assert REDACTED in blob


def test_network_address_keys_are_preserved_postal_is_not():
    # ip/mac addresses are triage signal, not customer PII — keep them; a postal
    # address field must still be redacted.
    event = {
        "extra": {"ip_address": "203.0.113.5", "mac_address": "AA-BB-CC-DD-EE-FF", "billing_address": "12 Tahrir St"}
    }
    out = scrub_event(event)
    assert out["extra"]["ip_address"] == "203.0.113.5"
    assert out["extra"]["mac_address"] == "AA-BB-CC-DD-EE-FF"
    assert out["extra"]["billing_address"] == REDACTED


def test_more_phone_formats_are_redacted():
    cases = {
        "00201001234567": "201001234567",  # international with 00 trunk prefix
        "(415) 555-1234": "555-1234",  # parenthesized US
        "0225354000": "0225354000",  # Egyptian landline
    }
    for text, leak in cases.items():
        blob = _blob({"message": text})
        assert leak not in blob, text
        assert REDACTED in blob


def test_signed_decimals_and_grouped_ids_are_not_redacted():
    # The phone regex must not eat financial numerics / opaque grouped IDs.
    for s in [
        "delta +14.000000 applied",
        "lat +30.044420 lng +31.235712",
        "git diff +123 -456 lines",
        "build 123 4567 8901 ok",
        "order 999-8888-7777 shipped",
    ]:
        out = scrub_event({"message": s})
        assert out["message"] == s, s


def test_query_string_pii_params_are_redacted():
    event = {
        "request": {
            "url": "https://app.nxentra.com/api/customers",
            "query_string": "customer_name=Jane+Doe&national_id=29801011234567&email=a@b.com&page=2",
        }
    }
    qs = scrub_event(event)["request"]["query_string"]
    assert "Jane" not in qs
    assert "29801011234567" not in qs
    assert "a@b.com" not in qs and "a%40b.com" not in qs
    assert "page=2" in qs  # benign param preserved for debugging
