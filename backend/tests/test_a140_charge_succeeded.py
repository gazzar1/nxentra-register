# tests/test_a140_charge_succeeded.py
"""A140 — charge.succeeded must book charges.

Stripe fires ``charge.captured`` ONLY for auth-then-capture flows; every
immediate-capture charge (normal PaymentIntents / Checkout / Charges-API
traffic) fires ``charge.succeeded`` — which the connector previously dropped
silently (unmapped topic → 200 ack, no event, no StripeCharge). The June test
charges only booked because ``stripe trigger charge.captured`` simulates the
auth+capture flow. Live evidence 2026-07-02: curl charge
ch_3TohReGWqh44OsSL00DzM8cb (USD 100) — no row, no JE.

Pinned here:
  * a signed charge.succeeded books the charge AND emits PLATFORM_ORDER_PAID;
  * double delivery (succeeded then captured, same charge) books exactly once
    (emit idempotency ``stripe.order_paid:<charge_id>`` + store_charge's
    (company, stripe_charge_id) unique constraint);
  * webhook_topics advertises charge.succeeded (keeps the merchant subscribe
    instructions and any future programmatic registration honest).
"""

import hashlib
import hmac
import json
import time
from decimal import Decimal
from uuid import uuid4

import pytest

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


def _charge_event(event_type: str, charge_id: str):
    """The shape a direct Charges-API charge produces (optional strings null,
    mirroring the test_stripe_charge_null_billing fixture)."""
    return {
        "id": f"evt_{uuid4().hex[:12]}",
        "type": event_type,
        "data": {
            "object": {
                "id": charge_id,
                "amount": 10000,
                "currency": "usd",
                "billing_details": {"email": None, "name": None, "address": None, "phone": None},
                "created": 1700000000,
                "payment_intent": None,
                "description": "C3 gate funding Jul 2",
                "receipt_email": None,
            }
        },
    }


def _post(client, event: dict):
    body = json.dumps(event).encode()
    return client.post(
        WEBHOOK_URL, data=body, content_type="application/json", HTTP_STRIPE_SIGNATURE=_sign(WHSEC, body)
    )


@pytest.mark.django_db
def test_charge_succeeded_books_charge_and_emits(settings, client, company, stripe_account):
    """The regression: an immediate-capture charge's only event must book."""
    from events.models import BusinessEvent

    settings.DISABLE_EVENT_VALIDATION = False

    resp = _post(client, _charge_event("charge.succeeded", "ch_succeeded_e2e"))
    assert resp.status_code == 200

    charge = StripeCharge.objects.get(company=company, stripe_charge_id="ch_succeeded_e2e")
    assert charge.amount == Decimal("100.00")
    assert BusinessEvent.objects.filter(company=company, idempotency_key="stripe.order_paid:ch_succeeded_e2e").exists()


@pytest.mark.django_db
def test_succeeded_then_captured_books_exactly_once(settings, client, company, stripe_account):
    """Auth+capture flows can deliver BOTH events for one charge — one booking."""
    from events.models import BusinessEvent

    settings.DISABLE_EVENT_VALIDATION = False

    assert _post(client, _charge_event("charge.succeeded", "ch_double")).status_code == 200
    assert _post(client, _charge_event("charge.captured", "ch_double")).status_code == 200

    assert StripeCharge.objects.filter(company=company, stripe_charge_id="ch_double").count() == 1
    assert BusinessEvent.objects.filter(company=company, idempotency_key="stripe.order_paid:ch_double").count() == 1


def test_webhook_topics_advertise_charge_succeeded():
    from stripe_connector.connector import STRIPE_TOPIC_MAP, StripeConnector

    assert STRIPE_TOPIC_MAP["charge.succeeded"] == "order_paid"
    assert "charge.succeeded" in StripeConnector().webhook_topics
