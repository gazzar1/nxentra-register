# tests/test_a44_gdpr_webhooks.py
"""
A44 — Tests for the three Shopify-mandatory GDPR compliance webhooks:
customers/data_request, customers/redact, shop/redact.

Coverage:
- 200 on valid signed payload + audit row written
- 401 on invalid signature (handled by shared HMAC verifier; spot-checked here)
- Idempotent on retry (duplicate body → same audit row, no duplicate insert)
- shop/redact accepted even when no ShopifyStore record exists for the domain
"""

import base64
import hashlib
import hmac
import json

import pytest
from django.test import Client

from shopify_connector import commands
from shopify_connector.models import GdprRequest

TEST_SECRET = "test-shopify-shared-secret"
WEBHOOK_URL = "/api/shopify/webhooks/"


@pytest.fixture(autouse=True)
def _patch_shopify_secret(monkeypatch):
    monkeypatch.setattr(commands, "SHOPIFY_API_SECRET", TEST_SECRET)


def _sign(body: bytes, secret: str = TEST_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _post(client: Client, topic: str, body: dict, *, sign_with: str = TEST_SECRET):
    raw = json.dumps(body).encode("utf-8")
    return client.post(
        WEBHOOK_URL,
        data=raw,
        content_type="application/json",
        HTTP_X_SHOPIFY_HMAC_SHA256=_sign(raw, sign_with),
        HTTP_X_SHOPIFY_TOPIC=topic,
        HTTP_X_SHOPIFY_SHOP_DOMAIN=body.get("shop_domain", ""),
    )


@pytest.mark.django_db
def test_customers_data_request_audited():
    payload = {
        "shop_id": 954889,
        "shop_domain": "merchant.myshopify.com",
        "orders_requested": [299938, 280263, 220458],
        "customer": {
            "id": 191167,
            "email": "john@example.com",
            "phone": "555-625-1199",
        },
        "data_request": {"id": 9999},
    }
    resp = _post(Client(), "customers/data_request", payload)

    assert resp.status_code == 200
    row = GdprRequest.objects.get(topic="customers/data_request")
    assert row.shop_domain == "merchant.myshopify.com"
    assert row.shop_id == 954889
    assert row.customer_id == 191167
    assert row.customer_email == "john@example.com"
    assert row.status == GdprRequest.Status.PENDING
    assert row.payload == payload


@pytest.mark.django_db
def test_customers_redact_audited():
    payload = {
        "shop_id": 954889,
        "shop_domain": "merchant.myshopify.com",
        "customer": {"id": 191167, "email": "john@example.com"},
        "orders_to_redact": [299938],
    }
    resp = _post(Client(), "customers/redact", payload)

    assert resp.status_code == 200
    row = GdprRequest.objects.get(topic="customers/redact")
    assert row.customer_id == 191167


@pytest.mark.django_db
def test_shop_redact_audited_without_existing_store():
    # shop/redact fires 48h after uninstall; the ShopifyStore record may already
    # be hard-deleted. The handler must still 200 and audit.
    payload = {"shop_id": 954889, "shop_domain": "long-gone.myshopify.com"}
    resp = _post(Client(), "shop/redact", payload)

    assert resp.status_code == 200
    row = GdprRequest.objects.get(topic="shop/redact")
    assert row.shop_domain == "long-gone.myshopify.com"
    assert row.customer_id is None


@pytest.mark.django_db
def test_invalid_signature_rejected():
    payload = {"shop_id": 1, "shop_domain": "merchant.myshopify.com"}
    resp = _post(Client(), "shop/redact", payload, sign_with="wrong-secret")

    assert resp.status_code == 401
    assert GdprRequest.objects.count() == 0


@pytest.mark.django_db
def test_retry_with_identical_body_is_idempotent():
    payload = {
        "shop_id": 954889,
        "shop_domain": "merchant.myshopify.com",
        "customer": {"id": 191167, "email": "john@example.com"},
        "orders_to_redact": [299938],
    }
    client = Client()

    first = _post(client, "customers/redact", payload)
    second = _post(client, "customers/redact", payload)

    assert first.status_code == 200
    assert second.status_code == 200
    # The unique constraint on (topic, payload_signature) guarantees a single row.
    assert GdprRequest.objects.filter(topic="customers/redact").count() == 1
