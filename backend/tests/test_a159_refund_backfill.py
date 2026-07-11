# tests/test_a159_refund_backfill.py
"""
A159 — missed/failed refund webhooks get a durable automated recovery path
(2026-07-11 dual audit).

Before the fix:
- The webhook view returned 200 even when process_refund failed (Shopify
  treats 2xx as delivered and never redelivers) — a refund racing its
  order was permanently lost.
- The 4h poller synced orders/payouts/products but had NO refund path,
  and _pick_order_handler skipped orders first seen already-refunded
  entirely: neither their revenue nor their refunds were ever booked.
- iter_orders filters by created_at, so a refund issued today against an
  order created months ago was invisible to any catch-up.

After the fix:
- ShopifyAdminClient.get_order_refunds: dedicated per-order GraphQL query
  (off iter_orders — 1000-point ceiling), REST-shaped for process_refund,
  with UPPERCASE GraphQL enums lowercased.
- _sync_orders routes refunded/partially_refunded orders through the
  idempotent process_order_paid and backfills their refunds.
- _sync_refunds: updated_at + financial_status search catches refunds on
  old orders and dropped webhooks; orders book BEFORE refund events emit.
- The webhook view answers 503 on retryable failures (Shopify redelivers
  ~48h) and 500 on unexpected exceptions; permanent validation failures
  still ack 200 (don't burn the subscription's failure budget).
"""

import base64
import hashlib
import hmac
import json

import pytest
from django.test import Client

from shopify_connector import commands
from shopify_connector.graphql_client import ShopifyAdminClient
from shopify_connector.models import ShopifyOrder, ShopifyRefund, ShopifyStore

pytestmark = pytest.mark.django_db

TEST_SECRET = "test-shopify-shared-secret"
WEBHOOK_URL = "/api/shopify/webhooks/"
SHOP_DOMAIN = "a159-test.myshopify.com"


@pytest.fixture(autouse=True)
def _patch_shopify_secret(monkeypatch):
    monkeypatch.setattr(commands, "SHOPIFY_API_SECRET", TEST_SECRET)


@pytest.fixture
def shopify_store(db, company):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain=SHOP_DOMAIN,
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )


def _sign(body: bytes) -> str:
    digest = hmac.new(TEST_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _post_webhook(topic: str, body: dict):
    raw = json.dumps(body).encode("utf-8")
    return Client().post(
        WEBHOOK_URL,
        data=raw,
        content_type="application/json",
        HTTP_X_SHOPIFY_HMAC_SHA256=_sign(raw),
        HTTP_X_SHOPIFY_TOPIC=topic,
        HTTP_X_SHOPIFY_SHOP_DOMAIN=SHOP_DOMAIN,
    )


def _order_payload(order_id=9000001, financial_status="refunded"):
    return {
        "id": order_id,
        "order_number": 1001,
        "name": "#1001",
        "created_at": "2026-04-28T08:30:00Z",
        "total_price": "500.00",
        "subtotal_price": "500.00",
        "total_tax": "0.00",
        "total_discounts": "0.00",
        "currency": "EGP",
        "financial_status": financial_status,
        "customer": None,
        "line_items": [],
        "shipping_lines": [],
        "transactions": [],
    }


def _refund_payload(refund_id=777001, order_id=9000001):
    return {
        "id": refund_id,
        "order_id": order_id,
        "created_at": "2026-04-29T10:00:00Z",
        "note": "damaged item",
        "transactions": [{"kind": "refund", "status": "success", "amount": "50.00"}],
        "refund_line_items": [],
    }


class _FakeClient:
    def __init__(self, orders=None, refunded_orders=None, refunds_by_order=None):
        self._orders = orders or []
        self._refunded_orders = refunded_orders or []
        self._refunds = refunds_by_order or {}

    def iter_orders(self, created_at_min, created_at_max):
        yield from self._orders

    def iter_refunded_orders(self, updated_at_min, updated_at_max):
        yield from self._refunded_orders

    def get_order_fulfillments(self, order_id):
        return []

    def get_order_refunds(self, order_id):
        return self._refunds.get(order_id, [])


# ---------------------------------------------------------------------------
# GraphQL → REST shaping
# ---------------------------------------------------------------------------


def test_get_order_refunds_shapes_graphql_payload_and_lowercases_enums():
    """GraphQL enums come back UPPERCASE; process_refund compares
    lowercase REST strings — forgetting to lowercase silently yields
    refund_amount=0."""
    client = ShopifyAdminClient(SHOP_DOMAIN, "token")
    client.execute = lambda *args, **kwargs: {
        "order": {
            "refunds": [
                {
                    "legacyResourceId": "777001",
                    "createdAt": "2026-04-29T10:00:00Z",
                    "note": "damaged item",
                    "transactions": {
                        "nodes": [
                            {"kind": "REFUND", "status": "SUCCESS", "amountSet": {"shopMoney": {"amount": "50.00"}}}
                        ]
                    },
                    "refundLineItems": {
                        "nodes": [
                            {
                                "quantity": 1,
                                "restockType": "RETURN",
                                "subtotalSet": {"shopMoney": {"amount": "50.00"}},
                                "lineItem": {"sku": "TSH-001", "title": "T-Shirt"},
                            }
                        ]
                    },
                }
            ]
        }
    }

    out = client.get_order_refunds(9000001)

    assert out == [
        {
            "id": 777001,
            "order_id": 9000001,
            "created_at": "2026-04-29T10:00:00Z",
            "note": "damaged item",
            "transactions": [{"kind": "refund", "status": "success", "amount": "50.00"}],
            "refund_line_items": [
                {
                    "quantity": 1,
                    "restock_type": "return",
                    "subtotal": "50.00",
                    "line_item": {"sku": "TSH-001", "title": "T-Shirt"},
                }
            ],
        }
    ]


# ---------------------------------------------------------------------------
# Poller: first-seen-refunded orders + refund backfill
# ---------------------------------------------------------------------------


def test_sync_orders_books_first_seen_refunded_order_and_its_refunds(shopify_store, monkeypatch):
    """An order first seen already-refunded was previously skipped
    entirely: neither its revenue invoice nor its refund ever hit the
    books. It must now book via process_order_paid and backfill refunds."""
    from shopify_connector import tasks

    fake = _FakeClient(
        orders=[_order_payload()],
        refunds_by_order={9000001: [_refund_payload()]},
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    result = tasks._sync_orders(shopify_store, "2026-04-01T00:00:00", "2026-05-01T00:00:00")

    assert result["status"] == "ok", result
    assert ShopifyOrder.objects.filter(company=shopify_store.company, shopify_order_id=9000001).exists(), (
        "first-seen-refunded order must book its revenue invoice"
    )
    refund = ShopifyRefund.objects.filter(company=shopify_store.company, shopify_refund_id=777001).first()
    assert refund is not None, "the order's refunds must be backfilled in the same pass"
    assert result["refunds_backfilled"] == 1

    from events.models import BusinessEvent

    assert BusinessEvent.objects.filter(
        company=shopify_store.company,
        idempotency_key="shopify.refund.created:777001",
    ).exists()


def test_sync_refunds_recovers_dropped_webhook_on_old_order(shopify_store, monkeypatch):
    """The durable safety net: a refund whose webhook was dropped, against
    an order outside any created_at lookback, is found by the updated_at
    search, its parent order is booked FIRST, then the refund."""
    from shopify_connector import tasks

    fake = _FakeClient(
        refunded_orders=[_order_payload(financial_status="partially_refunded")],
        refunds_by_order={9000001: [_refund_payload()]},
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    result = tasks._sync_refunds(shopify_store, "2026-04-01T00:00:00", "2026-05-01T00:00:00")

    assert result["status"] == "ok", result
    assert result["refunds_created"] == 1
    assert ShopifyOrder.objects.filter(company=shopify_store.company, shopify_order_id=9000001).exists()
    assert ShopifyRefund.objects.filter(company=shopify_store.company, shopify_refund_id=777001).exists()


def test_backfill_order_refunds_is_idempotent(shopify_store, monkeypatch):
    from shopify_connector import tasks

    fake = _FakeClient(refunds_by_order={9000001: [_refund_payload()]})
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    # Parent order must exist for process_refund.
    assert commands.process_order_paid(shopify_store, _order_payload(financial_status="paid")).success

    first = tasks._backfill_order_refunds(shopify_store, fake, 9000001)
    second = tasks._backfill_order_refunds(shopify_store, fake, 9000001)

    assert first == 1
    assert second == 0, "re-runs must skip already-booked refunds"
    assert ShopifyRefund.objects.filter(company=shopify_store.company).count() == 1


# ---------------------------------------------------------------------------
# Webhook view: retryable vs permanent failures
# ---------------------------------------------------------------------------


def test_refund_before_order_webhook_is_not_acked(shopify_store):
    """A refund racing its order must NOT be 200-acked — Shopify treats
    2xx as delivered and never redelivers, permanently losing the refund
    until the poller runs. 5xx makes Shopify redeliver with backoff."""
    resp = _post_webhook("refunds/create", _refund_payload(order_id=424242))

    assert resp.status_code in (500, 503), f"transient refund failure must be retryable, got {resp.status_code}"
    assert not ShopifyRefund.objects.filter(company=shopify_store.company).exists()


def test_permanent_refund_failure_still_acks_200(shopify_store):
    """Validation failures (missing id) must keep acking 200 so Shopify
    doesn't hammer us / cancel the subscription over unfixable payloads."""
    payload = _refund_payload()
    payload.pop("id")
    resp = _post_webhook("refunds/create", payload)
    assert resp.status_code == 200


def test_successful_refund_webhook_acks_200(shopify_store):
    assert commands.process_order_paid(shopify_store, _order_payload(financial_status="paid")).success
    resp = _post_webhook("refunds/create", _refund_payload())
    assert resp.status_code == 200
    assert ShopifyRefund.objects.filter(company=shopify_store.company, shopify_refund_id=777001).exists()
