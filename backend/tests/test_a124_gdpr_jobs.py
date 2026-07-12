# tests/test_a124_gdpr_jobs.py
"""
A124 — GDPR export + deletion jobs (2026-07-11 dual audit, COMPLIANCE).

Before the fix, the three Shopify-mandatory GDPR webhooks only wrote
PENDING GdprRequest audit rows — the published app promises 30/90-day
SLAs, but nothing ever produced an export or removed PII. Shopper PII
persisted indefinitely in ShopifyOrder/Fulfillment/Refund raw_payloads
and in the GDPR payloads themselves.

Policy under test (owner decision, 2026-07-11): every MUTABLE store is
scrubbed; the append-only BusinessEvent ledger keeps a documented
lawful-basis retention exception — matching events are COUNTED into
evidence (events_exempted), never rewritten (they carry SHA-256
integrity hashes).
"""

import json
from datetime import date, timedelta

import pytest
from django.utils import timezone

from shopify_connector.gdpr import (
    execute_customer_data_request,
    execute_customer_redact,
    execute_shop_redact,
    process_gdpr_request,
)
from shopify_connector.models import (
    GdprRequest,
    PendingShopifyInstall,
    ShopifyOrder,
    ShopifyStore,
)

pytestmark = pytest.mark.django_db

SHOP = "merchant.myshopify.com"
CUSTOMER_EMAIL = "john@example.com"
CUSTOMER_ID = 191167


@pytest.fixture
def store(company):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain=SHOP,
        access_token="secret-token",
        status=ShopifyStore.Status.ACTIVE,
    )


def _order(store, order_id=299938):
    return ShopifyOrder.objects.create(
        company=store.company,
        store=store,
        shopify_order_id=order_id,
        shopify_order_number=str(order_id),
        shopify_order_name=f"#{order_id}",
        total_price="100.00",
        subtotal_price="100.00",
        total_tax="0.00",
        currency="EGP",
        financial_status="paid",
        shopify_created_at=timezone.now(),
        order_date=date(2026, 5, 1),
        raw_payload={
            "id": order_id,
            "total_price": "100.00",
            "financial_status": "paid",
            "customer": {"id": CUSTOMER_ID, "email": CUSTOMER_EMAIL, "phone": "555-625-1199"},
            "email": CUSTOMER_EMAIL,
            "billing_address": {"address1": "1 Main St", "city": "Cairo"},
            "shipping_address": {"address1": "1 Main St", "city": "Cairo"},
            "line_items": [{"sku": "TSH-001", "quantity": 1}],
        },
    )


def _gdpr_request(topic, payload, customer_id=None, customer_email=""):
    return GdprRequest.objects.create(
        topic=topic,
        shop_domain=SHOP,
        shop_id=954889,
        customer_id=customer_id,
        customer_email=customer_email,
        payload=payload,
        payload_signature=f"sig-{topic.replace('/', '-')}",
        status=GdprRequest.Status.PENDING,
    )


class TestCustomerRedact:
    def test_redact_scrubs_all_mutable_pii_and_completes_with_evidence(self, company, store, user):
        order = _order(store)
        req = _gdpr_request(
            GdprRequest.Topic.CUSTOMERS_REDACT,
            {"shop_domain": SHOP, "customer": {"id": CUSTOMER_ID, "email": CUSTOMER_EMAIL}, "orders_to_redact": []},
            customer_id=CUSTOMER_ID,
            customer_email=CUSTOMER_EMAIL,
        )

        assert process_gdpr_request(req)

        order.refresh_from_db()
        flat = json.dumps(order.raw_payload)
        assert CUSTOMER_EMAIL not in flat, "shopper email must be scrubbed from raw_payload"
        assert "555-625-1199" not in flat
        assert "1 Main St" not in flat
        # Financial fields survive — projections/reconciliation depend on them.
        assert order.raw_payload["id"] == 299938
        assert order.raw_payload["total_price"] == "100.00"
        assert order.raw_payload["line_items"] == [{"sku": "TSH-001", "quantity": 1}]

        req.refresh_from_db()
        assert req.status == GdprRequest.Status.COMPLETED
        assert req.processed_at is not None
        assert req.evidence["orders_matched"] == 1
        assert req.evidence["records_scrubbed"] >= 1

        from events.models import BusinessEvent

        assert BusinessEvent.objects.filter(company=company, event_type="shopify.gdpr_request_completed").exists(), (
            "completion must emit the audit event"
        )

    def test_redact_is_idempotent(self, company, store, user):
        order = _order(store)
        req = _gdpr_request(
            GdprRequest.Topic.CUSTOMERS_REDACT,
            {"shop_domain": SHOP, "customer": {"id": CUSTOMER_ID, "email": CUSTOMER_EMAIL}},
            customer_id=CUSTOMER_ID,
            customer_email=CUSTOMER_EMAIL,
        )
        assert process_gdpr_request(req)
        order.refresh_from_db()
        first_payload = json.dumps(order.raw_payload, sort_keys=True)

        # Second pass over already-scrubbed data must be a no-op.
        req.refresh_from_db()
        execute_customer_redact(req)
        order.refresh_from_db()
        assert json.dumps(order.raw_payload, sort_keys=True) == first_payload

        from events.models import BusinessEvent

        assert (
            BusinessEvent.objects.filter(company=company, event_type="shopify.gdpr_request_completed").count() == 1
        ), "the completion event must dedupe on its idempotency key"

    def test_immutable_events_are_counted_not_rewritten(self, company, store, user):
        """Pins the lawful-basis exception as code."""
        from events.emitter import emit_event_no_actor
        from events.models import BusinessEvent
        from events.types import EventTypes
        from shopify_connector.event_types import ShopifyOrderPaidData

        _order(store)
        emit_event_no_actor(
            company=company,
            event_type=EventTypes.SHOPIFY_ORDER_PAID,
            aggregate_type="ShopifyOrder",
            aggregate_id="299938",
            idempotency_key="test.a124.orderpaid:299938",
            data=ShopifyOrderPaidData(
                amount="100.00",
                currency="EGP",
                transaction_date="2026-05-01",
                document_ref="#299938",
                store_public_id=str(store.public_id),
                shopify_order_id="299938",
                order_number="299938",
                customer_email=CUSTOMER_EMAIL,
                customer_name="John Doe",
            ),
        )

        req = _gdpr_request(
            GdprRequest.Topic.CUSTOMERS_REDACT,
            {"shop_domain": SHOP, "customer": {"id": CUSTOMER_ID, "email": CUSTOMER_EMAIL}},
            customer_id=CUSTOMER_ID,
            customer_email=CUSTOMER_EMAIL,
        )
        assert process_gdpr_request(req)

        event = BusinessEvent.objects.get(company=company, event_type=EventTypes.SHOPIFY_ORDER_PAID)
        assert event.get_data()["customer_email"] == CUSTOMER_EMAIL, (
            "immutable events must NOT be rewritten (lawful-basis exception)"
        )
        req.refresh_from_db()
        assert req.evidence["events_exempted"] >= 1, "exempted events must be counted into evidence"


class TestCustomerDataRequest:
    def test_export_assembled_and_admins_notified(self, company, store, user, owner_membership):
        _order(store)
        req = _gdpr_request(
            GdprRequest.Topic.CUSTOMERS_DATA_REQUEST,
            {
                "shop_domain": SHOP,
                "customer": {"id": CUSTOMER_ID, "email": CUSTOMER_EMAIL},
                "orders_requested": [299938],
            },
            customer_id=CUSTOMER_ID,
            customer_email=CUSTOMER_EMAIL,
        )

        evidence = execute_customer_data_request(req)

        req.refresh_from_db()
        assert req.status == GdprRequest.Status.COMPLETED
        assert evidence["orders_matched"] == 1
        export_row = evidence["export"][0]
        assert export_row["order_id"] == 299938
        assert export_row["customer"]["email"] == CUSTOMER_EMAIL

        from accounts.models import Notification

        assert Notification.objects.filter(company=company, source_module="shopify_connector").exists(), (
            "company admins must be notified so the merchant can serve the 30-day SLA"
        )


class TestShopRedact:
    def test_multi_company_scrub_tokens_and_pending_installs(self, company, second_company, user):
        active = ShopifyStore.objects.create(
            company=company, shop_domain=SHOP, access_token="tok-a", status=ShopifyStore.Status.ACTIVE
        )
        disconnected = ShopifyStore.objects.create(
            company=second_company, shop_domain=SHOP, access_token="tok-b", status=ShopifyStore.Status.DISCONNECTED
        )
        _order(active)
        _order(disconnected, order_id=299939)
        PendingShopifyInstall.objects.create(
            shop_domain=SHOP, access_token="tok-pending", expires_at=timezone.now() + timedelta(hours=1)
        )

        req = _gdpr_request(GdprRequest.Topic.SHOP_REDACT, {"shop_domain": SHOP, "shop_id": 954889})
        evidence = execute_shop_redact(req)

        assert evidence["companies_matched"] == 2
        for order_id, comp in ((299938, company), (299939, second_company)):
            order = ShopifyOrder.objects.get(company=comp, shopify_order_id=order_id)
            assert CUSTOMER_EMAIL not in json.dumps(order.raw_payload)

        active.refresh_from_db()
        disconnected.refresh_from_db()
        assert active.access_token == "" and disconnected.access_token == ""
        assert not PendingShopifyInstall.objects.filter(shop_domain=SHOP).exists()

        req.refresh_from_db()
        assert req.status == GdprRequest.Status.COMPLETED

    def test_zero_company_shop_redact_completes_with_evidence(self, company):
        req = _gdpr_request(GdprRequest.Topic.SHOP_REDACT, {"shop_domain": "long-gone.myshopify.com", "shop_id": 1})
        req.shop_domain = "long-gone.myshopify.com"
        req.save(update_fields=["shop_domain"])

        assert process_gdpr_request(req)
        req.refresh_from_db()
        assert req.status == GdprRequest.Status.COMPLETED
        assert req.evidence["companies_matched"] == 0


class TestFailureIsLoud:
    def test_executor_failure_marks_failed_with_notes(self, company, store, user, monkeypatch):
        import shopify_connector.gdpr as gdpr_mod

        _order(store)
        req = _gdpr_request(
            GdprRequest.Topic.CUSTOMERS_REDACT,
            {"shop_domain": SHOP, "customer": {"id": CUSTOMER_ID, "email": CUSTOMER_EMAIL}},
            customer_id=CUSTOMER_ID,
            customer_email=CUSTOMER_EMAIL,
        )
        monkeypatch.setitem(
            gdpr_mod._EXECUTORS,
            GdprRequest.Topic.CUSTOMERS_REDACT,
            lambda r: (_ for _ in ()).throw(RuntimeError("scrubber exploded")),
        )

        assert not process_gdpr_request(req)
        req.refresh_from_db()
        assert req.status == GdprRequest.Status.FAILED
        assert "scrubber exploded" in req.processing_notes


class TestBeatCatchup:
    def test_beat_task_drains_pending_rows(self, company, store, user):
        _order(store)
        _gdpr_request(
            GdprRequest.Topic.CUSTOMERS_REDACT,
            {"shop_domain": SHOP, "customer": {"id": CUSTOMER_ID, "email": CUSTOMER_EMAIL}},
            customer_id=CUSTOMER_ID,
            customer_email=CUSTOMER_EMAIL,
        )

        from shopify_connector.tasks import process_gdpr_requests

        result = process_gdpr_requests()
        assert result["processed"] == 1
        assert not GdprRequest.objects.filter(status=GdprRequest.Status.PENDING).exists()
