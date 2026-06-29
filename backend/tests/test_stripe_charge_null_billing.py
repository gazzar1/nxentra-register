# tests/test_stripe_charge_null_billing.py
"""
Regression: a real Stripe charge.captured sends optional string fields as JSON
null (notably billing_details.name). store_charge used dict.get(key, "") which
returns None for a present-but-null key — the default only applies when the key
is ABSENT — so customer_name=None hit the NOT NULL CharField and the INSERT
raised IntegrityError, which the broad `except IntegrityError: "already exists"`
then masked, silently dropping the charge while the webhook still returned 200.

Live-observed 2026-06-29: Shopify_R showed 0 charges despite repeated 200 OK
charge.captured deliveries; the StripeCharge table was empty.

Note: the suite disables event-payload validation by default (conftest), so the
end-to-end test re-enables it to mirror production.
"""

import hashlib
import hmac
import json
import time
from decimal import Decimal
from uuid import uuid4

import pytest

from stripe_connector.commands import store_charge
from stripe_connector.models import StripeAccount, StripeCharge

WHSEC = "whsec_test_secret_abcdefghijklmnop"
WEBHOOK_URL = "/api/platforms/stripe/webhooks/"


@pytest.fixture
def stripe_account(db, company):
    return StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        status=StripeAccount.Status.ACTIVE,
        credential_ref="rk_test_dummy",
        webhook_secret=WHSEC,
    )


def _sign(secret: str, body: bytes) -> str:
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _null_billing_payload(charge_id: str):
    """A charge.captured with every optional string field sent as null — exactly
    what `stripe trigger charge.captured` produces for a bare sandbox charge."""
    return {
        "id": f"evt_{uuid4().hex[:12]}",
        "type": "charge.captured",
        "data": {
            "object": {
                "id": charge_id,
                "amount": 2000,
                "currency": "usd",
                "billing_details": {"email": None, "name": None, "address": None, "phone": None},
                "created": 1700000000,
                "payment_intent": None,
                "description": None,
                "receipt_email": None,
            }
        },
    }


@pytest.mark.django_db
def test_store_charge_coalesces_null_billing_fields(company, stripe_account):
    """store_charge must turn Stripe's null string fields into '' so the NOT NULL
    CharFields accept them — previously raised IntegrityError and dropped the charge."""
    store_charge(company, None, _null_billing_payload("ch_null_unit"), event_id=None)

    charge = StripeCharge.objects.get(company=company, stripe_charge_id="ch_null_unit")
    assert charge.customer_name == ""
    assert charge.customer_email == ""
    assert charge.description == ""
    assert charge.stripe_payment_intent_id == ""
    assert charge.amount == Decimal("20.00")


@pytest.mark.django_db
def test_store_charge_duplicate_redelivery_is_clean(company, stripe_account):
    """A genuine re-delivery (same charge id) must hit the unique constraint and
    be skipped cleanly — the savepoint keeps the surrounding transaction usable so
    the duplicate check runs (it would raise TransactionManagementError otherwise)."""
    payload = _null_billing_payload("ch_dup")
    store_charge(company, None, payload, event_id=None)
    # Second delivery of the same charge — must not raise, must not duplicate.
    store_charge(company, None, payload, event_id=None)

    assert StripeCharge.objects.filter(company=company, stripe_charge_id="ch_dup").count() == 1


@pytest.mark.django_db
def test_signed_charge_with_null_billing_is_stored(settings, client, company, stripe_account):
    """End-to-end: a signed charge.captured with null billing_details.name must
    return 200 AND store the charge (validation enabled, as in production)."""
    settings.DISABLE_EVENT_VALIDATION = False

    body = json.dumps(_null_billing_payload("ch_null_e2e")).encode()
    resp = client.post(
        WEBHOOK_URL, data=body, content_type="application/json", HTTP_STRIPE_SIGNATURE=_sign(WHSEC, body)
    )
    assert resp.status_code == 200
    assert StripeCharge.objects.filter(company=company, stripe_charge_id="ch_null_e2e").exists()
