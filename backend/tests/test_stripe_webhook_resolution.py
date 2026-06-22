# tests/test_stripe_webhook_resolution.py
"""S0 — auth-agnostic StripeAccount + webhook resolution re-keyed off the
Connect-account-id (ADR-0002).

A single-merchant restricted-key account carries no Connect `account` id on its
webhook payloads, so the connector must resolve the company by the account whose
webhook secret verifies the request signature.
"""

import hashlib
import hmac
import time

from django.test import RequestFactory

from stripe_connector.connector import StripeConnector
from stripe_connector.models import StripeAccount

_rf = RequestFactory()


def _signed_request(secret: str, payload: bytes):
    """Build a Stripe-signed webhook request (v1 scheme) for `secret`."""
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.{payload.decode()}".encode(), hashlib.sha256).hexdigest()
    req = _rf.post("/api/stripe/webhooks/", data=payload, content_type="application/json")
    req.META["HTTP_STRIPE_SIGNATURE"] = f"t={ts},v1={sig}"
    return req


# A single-merchant payload: NO top-level "account" (Connect) field.
_PAYLOAD = b'{"type":"payout.paid","data":{"object":{"id":"po_1"}}}'


def test_auth_fields_default(db, company):
    acct = StripeAccount.objects.create(company=company, stripe_account_id="acct_x")
    assert acct.auth_type == StripeAccount.AuthType.RESTRICTED_KEY
    assert acct.credential_ref == ""


def test_resolves_company_by_webhook_secret_without_connect_account_id(db, company):
    StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        status=StripeAccount.Status.ACTIVE,
        webhook_secret="whsec_abc",
    )
    req = _signed_request("whsec_abc", _PAYLOAD)
    conn = StripeConnector()
    assert conn.resolve_company_from_webhook(req) == company
    assert conn.verify_webhook(req) is True


def test_wrong_secret_neither_verifies_nor_resolves(db, company):
    StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        status=StripeAccount.Status.ACTIVE,
        webhook_secret="whsec_abc",
    )
    req = _signed_request("whsec_WRONG", _PAYLOAD)
    conn = StripeConnector()
    assert conn.resolve_company_from_webhook(req) is None
    assert conn.verify_webhook(req) is False


def test_inactive_account_is_not_resolved(db, company):
    StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        status=StripeAccount.Status.DISCONNECTED,
        webhook_secret="whsec_abc",
    )
    req = _signed_request("whsec_abc", _PAYLOAD)
    conn = StripeConnector()
    assert conn.resolve_company_from_webhook(req) is None
