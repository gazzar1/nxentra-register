# tests/test_stripe_webhook_secret.py
"""
Stripe webhook signing-secret setup.

Covers the write-only, encrypted webhook-secret config + the signature gate it
unlocks:
- saving the secret encrypts it at rest (A47) and never returns it,
- the account GET exposes only a masked `webhook_secret_configured` flag,
- whsec_ prefix validation,
- a correctly-signed charge.captured webhook is accepted and ingests a charge,
- wrong / missing secret is rejected (401),
- the charge path creates no StripePayout (no payout/C3 involvement),
- the Sentry scrubber already redacts whsec_ values + webhook_secret fields.
"""

import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest
from django.db import connection

from stripe_connector.models import StripeAccount, StripeCharge, StripePayout

WHSEC = "whsec_test_secret_abcdefghijklmnop"
SET_URL = "/api/stripe/account/webhook-secret/"
ACCOUNT_URL = "/api/stripe/account/"
WEBHOOK_URL = "/api/platforms/stripe/webhooks/"


@pytest.fixture
def stripe_account(db, company):
    return StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        status=StripeAccount.Status.ACTIVE,
        credential_ref="rk_test_dummy",
    )


def _sign(secret: str, body: bytes) -> str:
    """Build a Stripe-Signature header (t=<ts>,v1=<hmac>) — mirrors Stripe's scheme."""
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _charge_payload(charge_id="ch_test_001"):
    """A minimal charge.captured event. No top-level "account" → the connector
    resolves the company by matching the webhook signing secret."""
    return {
        "id": f"evt_{uuid4().hex[:12]}",
        "type": "charge.captured",
        "data": {
            "object": {
                "id": charge_id,
                "amount": 10000,
                "application_fee_amount": 300,
                "currency": "usd",
                "billing_details": {"email": "buyer@example.com", "name": "Buyer"},
                "created": int(time.time()),
                "payment_intent": "pi_test_001",
                "description": "Test charge",
            }
        },
    }


@pytest.mark.django_db
class TestSetWebhookSecret:
    def test_encrypts_at_rest(self, authenticated_client, owner_membership, stripe_account):
        resp = authenticated_client.post(SET_URL, {"webhook_secret": WHSEC}, format="json")
        assert resp.status_code == 200
        assert resp.data == {"webhook_secret_configured": True}

        # Raw DB column holds ciphertext, not the plaintext secret.
        with connection.cursor() as cur:
            cur.execute(
                f"SELECT webhook_secret FROM {StripeAccount._meta.db_table} WHERE id = %s",
                [stripe_account.id],
            )
            raw = cur.fetchone()[0]
        assert raw.startswith("enc:v1:")
        assert WHSEC not in raw

        # Round-trips back to plaintext on read (EncryptedTextField decrypt).
        stripe_account.refresh_from_db()
        assert stripe_account.webhook_secret == WHSEC

    def test_response_and_account_get_never_return_secret(self, authenticated_client, owner_membership, stripe_account):
        set_resp = authenticated_client.post(SET_URL, {"webhook_secret": WHSEC}, format="json")
        assert WHSEC not in json.dumps(set_resp.data)

        get_resp = authenticated_client.get(ACCOUNT_URL)
        assert get_resp.status_code == 200
        assert get_resp.data["webhook_secret_configured"] is True
        # The masked flag is exposed, never the secret itself.
        assert "webhook_secret" not in get_resp.data
        assert WHSEC not in json.dumps(get_resp.data)

    def test_account_get_flag_false_when_unset(self, authenticated_client, owner_membership, stripe_account):
        get_resp = authenticated_client.get(ACCOUNT_URL)
        assert get_resp.data["webhook_secret_configured"] is False

    def test_invalid_prefix_rejected(self, authenticated_client, owner_membership, stripe_account):
        resp = authenticated_client.post(SET_URL, {"webhook_secret": "sk_live_nope_not_a_whsec"}, format="json")
        assert resp.status_code == 400
        stripe_account.refresh_from_db()
        assert stripe_account.webhook_secret == ""

    def test_too_short_rejected(self, authenticated_client, owner_membership, stripe_account):
        resp = authenticated_client.post(SET_URL, {"webhook_secret": "whsec_"}, format="json")
        assert resp.status_code == 400
        stripe_account.refresh_from_db()
        assert stripe_account.webhook_secret == ""

    def test_requires_connected_account(self, authenticated_client, owner_membership):
        # No StripeAccount for this company.
        resp = authenticated_client.post(SET_URL, {"webhook_secret": WHSEC}, format="json")
        assert resp.status_code == 400


@pytest.mark.django_db
class TestWebhookSignatureGate:
    def _set_secret(self, account, secret):
        account.webhook_secret = secret
        account.save(update_fields=["webhook_secret"])

    def test_signed_charge_accepted_creates_charge(self, client, company, stripe_account):
        """Correct secret → signature verifies → charge.captured ingested.
        (The downstream JE is posted by the existing accounting projection, unchanged.)"""
        self._set_secret(stripe_account, WHSEC)
        body = json.dumps(_charge_payload("ch_accept_1")).encode()
        resp = client.post(
            WEBHOOK_URL, data=body, content_type="application/json", HTTP_STRIPE_SIGNATURE=_sign(WHSEC, body)
        )
        assert resp.status_code == 200
        assert StripeCharge.objects.filter(company=company, stripe_charge_id="ch_accept_1").exists()

    def test_wrong_secret_rejected(self, client, company, stripe_account):
        self._set_secret(stripe_account, WHSEC)
        body = json.dumps(_charge_payload("ch_reject_1")).encode()
        resp = client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=_sign("whsec_wrong_secret_value_xyz", body),
        )
        assert resp.status_code == 401
        assert not StripeCharge.objects.filter(company=company, stripe_charge_id="ch_reject_1").exists()

    def test_missing_secret_rejected(self, client, company, stripe_account):
        # webhook_secret left empty → no signed webhook can verify.
        body = json.dumps(_charge_payload("ch_missing_1")).encode()
        resp = client.post(
            WEBHOOK_URL, data=body, content_type="application/json", HTTP_STRIPE_SIGNATURE=_sign(WHSEC, body)
        )
        assert resp.status_code == 401
        assert not StripeCharge.objects.filter(company=company, stripe_charge_id="ch_missing_1").exists()

    def test_charge_path_creates_no_payout(self, client, company, stripe_account):
        """Guard: the charge webhook path must not create/modify payouts (no C3 involvement)."""
        self._set_secret(stripe_account, WHSEC)
        body = json.dumps(_charge_payload("ch_nopayout_1")).encode()
        client.post(WEBHOOK_URL, data=body, content_type="application/json", HTTP_STRIPE_SIGNATURE=_sign(WHSEC, body))
        assert StripePayout.objects.count() == 0


@pytest.mark.django_db
class TestSentryScrubCoversWhsec:
    def test_whsec_value_and_field_are_redacted(self):
        from ops.sentry_scrub import REDACTED, scrub_event

        event = {
            "logentry": {"message": f"verifying webhook with {WHSEC}"},
            "extra": {"webhook_secret": WHSEC, "note": "ok"},
        }
        out = json.dumps(scrub_event(event))
        assert WHSEC not in out
        assert REDACTED in out
