# tests/test_a125_cogs_backfill.py
"""
A125: COGS backfill for synced/historical Shopify orders.

Before A125, _sync_orders booked revenue (process_order_paid) but never pulled
fulfillments, so imported / re-synced orders got revenue with no COGS —
overstating gross margin. A125 adds:

- ShopifyAdminClient.get_order_fulfillments(order_id): a dedicated per-order
  GraphQL query (kept off iter_orders so the fulfillments × fulfillmentLineItems
  cost can't breach Shopify's 1000-point ceiling), REST-shaped for the
  existing process_fulfillment handler.
- _backfill_order_fulfillments(store, client, order_id): called from the sync
  loop after each booked paid order, idempotent, and BEST-EFFORT — a
  fulfillment failure must never roll back the order or break the batch.

The COGS JE / StockLedger mechanics themselves live in process_fulfillment and
are covered by test_shopify_webhook_handlers.py; these tests cover the new
shaping + wiring.
"""

import pytest

from shopify_connector.graphql_client import ShopifyAdminClient
from shopify_connector.models import ShopifyStore
from shopify_connector.tasks import _backfill_order_fulfillments


@pytest.fixture
def shopify_store(db, company):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="a125-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )


class _FakeClient:
    """Stands in for ShopifyAdminClient.get_order_fulfillments."""

    def __init__(self, fulfillments):
        self._fulfillments = fulfillments
        self.calls = []

    def get_order_fulfillments(self, order_id):
        self.calls.append(order_id)
        return self._fulfillments


class _BoomClient:
    def get_order_fulfillments(self, order_id):
        raise RuntimeError("Shopify denied / GraphQL error")


# ---------------------------------------------------------------------------
# get_order_fulfillments — GraphQL → REST shaping
# ---------------------------------------------------------------------------


def _client_returning(graphql_payload):
    client = ShopifyAdminClient("a125-test.myshopify.com", "token")
    client.execute = lambda *args, **kwargs: graphql_payload
    return client


def test_get_order_fulfillments_shapes_graphql_list():
    """The list shape ([Fulfillment!]!) maps to the REST payload
    process_fulfillment consumes: id, order_id, created_at, status,
    location_id, and line_items[{sku,title,quantity}]."""
    client = _client_returning(
        {
            "order": {
                "fulfillments": [
                    {
                        "legacyResourceId": "5500",
                        "createdAt": "2026-06-18T14:22:42Z",
                        "status": "SUCCESS",
                        "location": {"legacyResourceId": "7700"},
                        "fulfillmentLineItems": {
                            "nodes": [
                                {"quantity": 2, "lineItem": {"sku": "TSH-001", "title": "T-Shirt"}},
                                {"quantity": 1, "lineItem": {"sku": "MUG-1", "title": "Mug"}},
                            ]
                        },
                    }
                ]
            }
        }
    )

    out = client.get_order_fulfillments(9100001)

    assert out == [
        {
            "id": 5500,
            "order_id": 9100001,
            "created_at": "2026-06-18T14:22:42Z",
            "status": "success",
            "location_id": "7700",
            "line_items": [
                {"sku": "TSH-001", "title": "T-Shirt", "quantity": 2},
                {"sku": "MUG-1", "title": "Mug", "quantity": 1},
            ],
        }
    ]


def test_get_order_fulfillments_tolerates_connection_shape():
    """If a future API version returns fulfillments as a connection
    ({nodes: [...]}) instead of a list, the defensive unwrap still parses it."""
    client = _client_returning(
        {
            "order": {
                "fulfillments": {
                    "nodes": [
                        {
                            "legacyResourceId": "5501",
                            "createdAt": "2026-06-18T00:00:00Z",
                            "status": "SUCCESS",
                            "location": None,
                            "fulfillmentLineItems": {"nodes": []},
                        }
                    ]
                }
            }
        }
    )

    out = client.get_order_fulfillments(123)

    assert len(out) == 1
    assert out[0]["id"] == 5501
    assert out[0]["location_id"] == ""  # null location → empty, not a crash
    assert out[0]["line_items"] == []


def test_get_order_fulfillments_empty_when_no_order_or_fulfillments():
    assert _client_returning({"order": None}).get_order_fulfillments(1) == []
    assert _client_returning({"order": {"fulfillments": []}}).get_order_fulfillments(1) == []


# ---------------------------------------------------------------------------
# _backfill_order_fulfillments — wiring, best-effort, idempotency
# ---------------------------------------------------------------------------


def test_backfill_swallows_fetch_error(shopify_store):
    """Best-effort contract: a fetch failure returns 0 and never propagates —
    the order is already committed and must not be disturbed."""
    assert _backfill_order_fulfillments(shopify_store, _BoomClient(), 9100001) == 0


@pytest.mark.django_db
def test_backfill_processes_each_fulfillment_and_is_idempotent(shopify_store):
    """Each fulfillment is fed through process_fulfillment; a re-run skips
    already-booked ones (idempotent), so a second sync doesn't double-count."""
    from django.utils import timezone

    from shopify_connector.models import ShopifyFulfillment, ShopifyOrder

    order = ShopifyOrder.objects.create(
        company=shopify_store.company,
        store=shopify_store,
        shopify_order_id=9100002,
        shopify_order_number="1003",
        shopify_order_name="#1003",
        total_price="100.00",
        subtotal_price="100.00",
        total_tax="0.00",
        currency="EGP",
        financial_status="paid",
        shopify_created_at=timezone.now(),
        order_date=timezone.now().date(),
        raw_payload={},
    )

    # REST-shaped fulfillment (what get_order_fulfillments yields). Unmatched
    # SKU → process_fulfillment still books the fulfillment record (no COGS JE
    # needed), which is enough to exercise the loop + idempotency.
    fulfillments = [
        {
            "id": 9200003,
            "order_id": order.shopify_order_id,
            "created_at": "2026-06-18T14:22:42Z",
            "status": "success",
            "location_id": "",
            "line_items": [{"sku": "", "title": "No SKU", "quantity": 1}],
        }
    ]
    client = _FakeClient(fulfillments)

    booked = _backfill_order_fulfillments(shopify_store, client, order.shopify_order_id)
    assert booked == 1
    assert ShopifyFulfillment.objects.filter(shopify_fulfillment_id=9200003).count() == 1

    # Second pass — idempotent: process_fulfillment skips the existing record.
    booked_again = _backfill_order_fulfillments(shopify_store, client, order.shopify_order_id)
    assert booked_again == 0
    assert ShopifyFulfillment.objects.filter(shopify_fulfillment_id=9200003).count() == 1
