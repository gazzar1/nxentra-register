# tests/test_s1d_credential_no_leak.py
"""S1 hardening — credentials must never leak, and scope guidance is canonical.

Three guarantees:
  1. The required READ scopes come from ONE canonical list, used by both
     rejection messages (no more sk_-vs-access-denied disagreement).
  2. The connect endpoint never echoes a submitted credential into its response
     or logs.
  3. The Sentry before_send scrubber redacts credential-like fields and
     secret-looking values, and drops the connect endpoint's request body.
"""

import json
import logging

import pytest

from ops.sentry_scrub import REDACTED, scrub_event
from stripe_connector.commands import (
    REQUIRED_READ_SCOPES,
    connect_stripe_account,
    required_scopes_phrase,
)


@pytest.fixture
def _no_async(monkeypatch):
    """Don't enqueue the real backfill (no broker / no live Stripe call)."""
    import stripe_connector.tasks as t

    monkeypatch.setattr(t.initial_stripe_sync, "delay", lambda *a, **k: None)


def _mock_probe(monkeypatch, acct):
    monkeypatch.setattr("stripe_connector.api_client.StripeApiClient.retrieve_account", lambda self: acct)
    monkeypatch.setattr("stripe_connector.api_client.StripeApiClient.probe", lambda self: True)


# ── 1. canonical scope list ───────────────────────────────────────────


def test_required_scopes_phrase_is_canonical():
    assert REQUIRED_READ_SCOPES == ("Balance", "Payouts")
    assert required_scopes_phrase() == "Balance and Payouts"
    # We never ask a merchant for account/KYC read just to connect, nor for
    # Charges (read via webhooks, not the API) or Disputes (Phase 3).
    phrase = required_scopes_phrase()
    for scope in ("Account", "Basic Business", "Charges", "Disputes"):
        assert scope not in phrase


def test_sk_rejection_uses_canonical_scope_phrase(db, company):
    result = connect_stripe_account(company, "sk_live_x")
    assert not result.success
    assert required_scopes_phrase() in result.error
    assert "Account" not in result.error
    assert "Disputes" not in result.error


def test_access_denied_uses_canonical_scope_phrase(db, company, monkeypatch):
    # The pull-scope probe (Payouts + Balance) is the gate — its denial is what
    # rejects a key, and the message names the canonical scopes.
    from stripe_connector.api_client import StripeAccessDenied

    def _denied(self):
        raise StripeAccessDenied("insufficient read scope")

    monkeypatch.setattr("stripe_connector.api_client.StripeApiClient.probe", _denied)
    result = connect_stripe_account(company, "rk_test_badscope")
    assert not result.success
    assert required_scopes_phrase() in result.error


# ── 2. no credential leak: responses + logs ───────────────────────────


def test_connect_success_response_excludes_credential(
    db, company, authenticated_client, owner_membership, monkeypatch, _no_async
):
    _mock_probe(monkeypatch, {"id": "acct_test_1", "livemode": False})
    secret = "rk_test_topsecret123"
    resp = authenticated_client.post(
        "/api/stripe/connect/",
        data=json.dumps({"credential": secret}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    assert secret not in resp.content.decode()


def test_connect_rejection_response_does_not_echo_submitted_key(db, company, authenticated_client, owner_membership):
    secret = "sk_live_should_not_appear"
    resp = authenticated_client.post(
        "/api/stripe/connect/",
        data=json.dumps({"credential": secret}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert secret not in resp.content.decode()


def test_connect_does_not_log_the_credential(db, company, monkeypatch, _no_async, caplog):
    _mock_probe(monkeypatch, {"id": "acct_log_1", "livemode": False})
    secret = "rk_test_neverlogme456"
    with caplog.at_level(logging.DEBUG):
        result = connect_stripe_account(company, secret)
    assert result.success
    for record in caplog.records:
        assert secret not in record.getMessage()
        assert secret not in str(record.args or "")


# ── 3. telemetry helper: the Sentry scrubber ──────────────────────────


def test_scrub_event_drops_connect_request_body():
    event = {
        "request": {
            "url": "https://app.nxentra.com/api/stripe/connect/",
            "method": "POST",
            "data": {"credential": "rk_live_realkey"},
        },
    }
    scrubbed = scrub_event(event)
    assert scrubbed["request"]["data"] == REDACTED


def test_scrub_event_redacts_credential_named_fields_anywhere():
    event = {
        "extra": {"credential": "anything", "note": "ok"},
        "contexts": {"foo": {"api_key": "whatever"}},
    }
    scrubbed = scrub_event(event)
    assert scrubbed["extra"]["credential"] == REDACTED
    assert scrubbed["extra"]["note"] == "ok"
    assert scrubbed["contexts"]["foo"]["api_key"] == REDACTED


def test_scrub_event_redacts_secret_value_patterns_under_benign_keys():
    event = {
        "message": "connect failed for rk_live_leakedinmessage",
        "extra": {"detail": "key was sk_live_alsoleaked"},
    }
    blob = json.dumps(scrub_event(event))
    assert "rk_live_leakedinmessage" not in blob
    assert "sk_live_alsoleaked" not in blob
    assert REDACTED in blob


def test_scrub_event_leaves_benign_events_untouched():
    event = {"message": "something broke", "extra": {"count": 3, "ok": True}}
    assert scrub_event(event) == event


def test_scrub_event_non_dict_passthrough():
    assert scrub_event(None) is None
    assert scrub_event("plain") == "plain"
