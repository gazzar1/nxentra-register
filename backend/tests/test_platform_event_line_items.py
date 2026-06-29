# tests/test_platform_event_line_items.py
"""
Regression: the generic platform webhook→event converter must keep list/dict
fields (notably `line_items`) as their native type. A Stripe charge.captured has
an empty line_items[]; `_canonical_to_event_data` previously str()'d every field,
turning it into the string "[]", so `platform.order_paid` validation raised
InvalidEventPayload and the webhook 500'd before the charge could be stored.

Note: the test suite disables event-payload validation by default
(conftest `_testing_settings`), which is exactly why the original webhook tests
passed while production 500'd — so the integration test here re-enables it.
"""

import hashlib
import hmac
import json
import time
from decimal import Decimal
from uuid import uuid4

import pytest

from events.types import PlatformOrderPaidData
from platform_connectors.canonical import ParsedOrder
from platform_connectors.views import _canonical_to_event_data
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


def _charge_payload(charge_id="ch_validate_1"):
    return {
        "id": f"evt_{uuid4().hex[:12]}",
        "type": "charge.captured",
        "data": {
            "object": {
                "id": charge_id,
                "amount": 10000,
                "currency": "usd",
                "billing_details": {"email": "buyer@example.com", "name": "Buyer"},
                "created": int(time.time()),
                "payment_intent": "pi_test_1",
                "description": "Test charge",
            }
        },
    }


def test_canonical_to_event_data_keeps_line_items_as_list():
    parsed = ParsedOrder(
        platform_order_id="ch_1",
        order_number="ch_1",
        order_name="ch_1",
        total_price=Decimal("100.00"),
        subtotal=Decimal("100.00"),
        currency="USD",
    )
    data = _canonical_to_event_data(parsed, PlatformOrderPaidData, "stripe")
    assert isinstance(data.line_items, list)  # not the string "[]"
    # Scalar fields are still stringified as the schema expects.
    assert isinstance(data.subtotal, str)


@pytest.mark.django_db
def test_signed_charge_passes_event_validation(settings, client, company, stripe_account):
    """With validation ENABLED (as in production), a signed charge.captured must
    succeed (200) and store the charge — previously it 500'd on line_items."""
    settings.DISABLE_EVENT_VALIDATION = False

    body = json.dumps(_charge_payload("ch_validate_1")).encode()
    resp = client.post(
        WEBHOOK_URL, data=body, content_type="application/json", HTTP_STRIPE_SIGNATURE=_sign(WHSEC, body)
    )
    assert resp.status_code == 200
    assert StripeCharge.objects.filter(company=company, stripe_charge_id="ch_validate_1").exists()
