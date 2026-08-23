# tests/test_a5_pr2b_rejected_evidence.py
"""A5-PR2b — durable rejection of malformed Shopify provider payloads.

ShopifyRejectedEvidence owns authenticated ORDER/REFUND payloads from which an
honest ShopifyOrder/ShopifyRefund cannot be constructed (founder spec
2026-08-23): the up-front validator classifies them PERMANENT → durable
evidence + webhook 200 (no 503 loop, no repeat notification), while everything
post-validation stays TRANSIENT → retryable 503. No order/refund row, no
event, no journal, no financial-reader entry is ever created for them.

Covers the founder's 12 enumerated proofs one-to-one (T1–T12; T10 RLS lives in
tests/e2e/test_a5_pr2b_rejected_evidence_rls.py) plus the extension proofs:
malformed-JSON capture, reopen-on-resight, superseded-stays-superseded,
redaction persistence, the five supersession DB constraints, the adapter-owned
queue endpoints, and ingress provenance.
"""

import base64
import hashlib
import hmac
import json
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.test import Client

from accounts.models import Notification
from events.models import BusinessEvent
from shopify_connector import commands
from shopify_connector import rejected_evidence as re_mod
from shopify_connector.models import (
    ShopifyOrder,
    ShopifyRefund,
    ShopifyRejectedEvidence,
    ShopifyStore,
)

pytestmark = pytest.mark.django_db

TEST_SECRET = "test-shopify-shared-secret"
WEBHOOK_URL = "/api/shopify/webhooks/"
SHOP_DOMAIN = "a5pr2b-test.myshopify.com"


@pytest.fixture(autouse=True)
def _patch_shopify_secret(monkeypatch):
    monkeypatch.setattr(commands, "SHOPIFY_API_SECRET", TEST_SECRET)


@pytest.fixture(autouse=True)
def _throttle_neutral():
    """The global AnonRateThrottle (anon: 100/hour) counts EVERY anonymous
    request in the whole battery process against one cache-backed counter —
    this file's webhook volume alone would push it over the ceiling and spill
    429s into every later anonymous-endpoint test (register, OAuth callback,
    session-login, Stripe webhooks). Clear the throttle cache around each test
    so the file neither inherits earlier files' counts nor leaks its own.
    In-test throttle behavior (a test making N requests) is unaffected."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


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


def _post_webhook(topic: str, body: dict, delivery_id: str = "wh-1"):
    raw = json.dumps(body).encode("utf-8")
    return Client().post(
        WEBHOOK_URL,
        data=raw,
        content_type="application/json",
        HTTP_X_SHOPIFY_HMAC_SHA256=_sign(raw),
        HTTP_X_SHOPIFY_TOPIC=topic,
        HTTP_X_SHOPIFY_SHOP_DOMAIN=SHOP_DOMAIN,
        HTTP_X_SHOPIFY_WEBHOOK_ID=delivery_id,
    )


def _post_raw_webhook(topic: str, raw: bytes, delivery_id: str = "wh-raw-1", shop_domain: str = SHOP_DOMAIN):
    return Client().post(
        WEBHOOK_URL,
        data=raw,
        content_type="application/json",
        HTTP_X_SHOPIFY_HMAC_SHA256=_sign(raw),
        HTTP_X_SHOPIFY_TOPIC=topic,
        HTTP_X_SHOPIFY_SHOP_DOMAIN=shop_domain,
        HTTP_X_SHOPIFY_WEBHOOK_ID=delivery_id,
    )


def _order_payload(order_id=9100001, financial_status="paid", **overrides):
    payload = {
        "id": order_id,
        "order_number": 2001,
        "name": "#2001",
        "created_at": "2026-08-20T08:30:00Z",
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
    payload.update(overrides)
    return payload


def _refund_payload(refund_id=9200001, order_id=9100001, **overrides):
    payload = {
        "id": refund_id,
        "order_id": order_id,
        "created_at": "2026-08-21T10:00:00Z",
        "note": "damaged item",
        "transactions": [{"kind": "refund", "status": "success", "amount": "50.00"}],
        "refund_line_items": [],
    }
    payload.update(overrides)
    return payload


def _evidence(company):
    return ShopifyRejectedEvidence.objects.get(company=company)


# =============================================================================
# T1 — malformed total_price with a valid order id persists ONE rejection
# =============================================================================


def test_t1_malformed_total_price_with_valid_id_persists_one_rejection(shopify_store, company, owner_membership):
    payload = _order_payload(order_id=5100001, total_price="abc")
    resp = _post_webhook("orders/paid", payload)

    # Permanent provider-data error: acknowledged, never a 503 loop.
    assert resp.status_code == 200

    row = _evidence(company)
    assert row.resource_kind == ShopifyRejectedEvidence.ResourceKind.ORDER
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY
    assert row.external_id == "5100001"
    assert row.occurrence_count == 1
    assert row.ingress_kind == ShopifyRejectedEvidence.IngressKind.WEBHOOK
    assert row.source_topic == "orders/paid"
    assert row.parsed_payload == payload
    assert row.payload_hash == re_mod.canonical_payload_hash(payload)
    assert row.transport_hash  # exact webhook bytes were available
    assert row.raw_body_b64
    assert row.store_public_id == shopify_store.public_id
    assert row.shop_domain == SHOP_DOMAIN
    assert row.validation_errors and row.validation_errors[0]["code"] == "MALFORMED_MONEY"

    # Operator-visible notification on first sighting.
    assert Notification.objects.filter(
        company=company, source_module="shopify_connector", title__icontains="rejected"
    ).exists()


def test_process_order_paid_permanent_payload_error_not_retryable(shopify_store, company):
    """Restores the PR #127 round-5 pin (reverted to this PR by founder
    decision): a permanent provider-data error must NOT carry the retryable
    flag — the webhook acks instead of 503-looping for ~48h."""
    result = commands.process_order_paid(shopify_store, _order_payload(order_id=5100002, total_price="not-a-number"))
    assert not result.success
    assert result.data and result.data.get("rejected") is True
    assert not result.data.get("retryable")
    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 1


# =============================================================================
# T2 — malformed or missing order id persists evidence via payload-hash identity
# =============================================================================


def test_t2_missing_order_id_persists_evidence_with_payload_hash_identity(shopify_store, company):
    payload = _order_payload()
    payload.pop("id")
    resp = _post_webhook("orders/paid", payload)
    assert resp.status_code == 200

    row = _evidence(company)
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MISSING_EXTERNAL_ID
    assert row.external_id is None  # a malformed id is never promoted
    assert row.dedup_hash == re_mod.compute_dedup_hash(
        company.id,
        shopify_store.public_id,
        ShopifyRejectedEvidence.ResourceKind.ORDER,
        re_mod.canonical_payload_hash(payload),
    )


def test_t2b_untrustworthy_order_id_is_not_promoted(shopify_store, company):
    resp = _post_webhook("orders/paid", _order_payload(id="not-a-number"))
    assert resp.status_code == 200
    row = _evidence(company)
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MISSING_EXTERNAL_ID
    assert row.external_id is None
    assert row.parsed_payload["id"] == "not-a-number"  # raw evidence keeps it


# =============================================================================
# T3 — non-string SKU is permanent REJECTED, not an endless 503
# =============================================================================


def test_t3_non_string_sku_is_permanent_rejected_not_503(shopify_store, company):
    payload = _order_payload(
        order_id=5100003,
        line_items=[{"sku": 12345, "title": "Widget", "price": "10.00", "quantity": 1}],
    )
    resp = _post_webhook("orders/paid", payload)
    assert resp.status_code == 200, "a permanently malformed payload must never 503-loop"

    row = _evidence(company)
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_STRUCTURE
    assert row.external_id == "5100003"
    assert ShopifyOrder.objects.filter(company=company).count() == 0


# =============================================================================
# T4 — repeated identical delivery increments occurrence_count, no duplicates,
#      no repeat notification
# =============================================================================


def test_t4_repeated_identical_delivery_dedupes(shopify_store, company, owner_membership):
    payload = _order_payload(order_id=5100004, total_price="abc")
    assert _post_webhook("orders/paid", payload, delivery_id="wh-first").status_code == 200
    first = _evidence(company)
    notifications_after_first = Notification.objects.filter(company=company).count()

    assert _post_webhook("orders/paid", payload, delivery_id="wh-second").status_code == 200

    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 1
    row = _evidence(company)
    assert row.pk == first.pk
    assert row.occurrence_count == 2
    assert row.last_seen_at >= first.last_seen_at
    assert row.last_delivery_id == "wh-second"
    # Notification fires only on row creation — never on a re-sight.
    assert Notification.objects.filter(company=company).count() == notifications_after_first


# =============================================================================
# T5 — corrected delivery creates the canonical order and supersedes
# =============================================================================


def test_t5_corrected_delivery_creates_order_and_supersedes(shopify_store, company):
    bad = _order_payload(order_id=5100005, total_price="abc")
    assert _post_webhook("orders/paid", bad).status_code == 200
    evidence = _evidence(company)
    assert evidence.superseded_at is None

    good = _order_payload(order_id=5100005)
    assert _post_webhook("orders/paid", good).status_code == 200

    order = ShopifyOrder.objects.get(company=company, shopify_order_id=5100005)
    assert order.event_id is not None
    assert order.total_price == Decimal("500.00")

    evidence.refresh_from_db()
    assert evidence.superseded_at is not None
    assert evidence.superseded_by_order_id == order.pk
    assert evidence.superseded_target_public_id == order.public_id
    # The historical rejected payload is never deleted.
    assert evidence.parsed_payload == bad


def test_t5b_superseded_evidence_leaves_the_open_queue(shopify_store, company):
    bad = _order_payload(order_id=5100015, total_price="abc")
    _post_webhook("orders/paid", bad)
    _post_webhook("orders/paid", _order_payload(order_id=5100015))
    open_rows = ShopifyRejectedEvidence.objects.filter(company=company, acknowledged=False, superseded_at__isnull=True)
    assert open_rows.count() == 0


# =============================================================================
# T6 — database failure while storing the rejection stays retryable
# =============================================================================


def test_t6_evidence_persistence_failure_stays_retryable(shopify_store, company, monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(commands.rejected_evidence, "record_rejected_evidence", _boom)
    resp = _post_webhook("orders/paid", _order_payload(order_id=5100006, total_price="abc"))
    # Never acknowledge malformed evidence whose durable record was not stored.
    assert resp.status_code == 503
    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 0


# =============================================================================
# T7 — transient processing errors stay retryable, never permanently rejected
# =============================================================================


def test_t7_transient_network_error_stays_retryable(shopify_store, company, monkeypatch):
    import requests as requests_lib

    def _net_down(store, payload):
        raise requests_lib.RequestException("network down")

    monkeypatch.setattr(commands, "_prepare_order_item_metadata", _net_down)
    resp = _post_webhook("orders/paid", _order_payload(order_id=5100007))
    assert resp.status_code == 503, "post-validation failures must 503 so Shopify redelivers"
    # A transient failure is NOT evidence and NOT an order.
    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 0
    assert ShopifyOrder.objects.filter(company=company).count() == 0


def test_t7b_transient_db_error_stays_retryable_command_level(shopify_store, monkeypatch):
    from django.db import OperationalError

    def _db_down(store, payload):
        raise OperationalError("could not connect")

    monkeypatch.setattr(commands, "_prepare_order_item_metadata", _db_down)
    result = commands.process_order_paid(shopify_store, _order_payload(order_id=5100017))
    assert not result.success
    assert result.data and result.data.get("retryable") is True


# =============================================================================
# T8 — rejected evidence appears in exceptions and health alerts
# =============================================================================


def test_t8_evidence_appears_in_exceptions_queue(shopify_store, company, owner_membership, authenticated_client):
    _post_webhook("orders/paid", _order_payload(order_id=5100008, total_price="abc"))

    resp = authenticated_client.get("/api/shopify/rejected-evidence/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 1
    assert body["results"][0]["rejection_code"] == "MALFORMED_MONEY"
    assert body["results"][0]["external_id"] == "5100008"


def test_t8b_evidence_pages_health_alerts(shopify_store, company, settings):
    from ops.health import compute_alert_state

    settings.ALERT_UNRESOLVED_FAILURES_MAX = 0
    _post_webhook("orders/paid", _order_payload(order_id=5100018, total_price="abc"))

    state = compute_alert_state()
    assert state["open_rejected_evidence"] == 1
    assert state["rejected_evidence_by_source"].get("shopify") == 1
    assert state["status"] == "unhealthy"

    # Acknowledged evidence stops paging (it left the open pool).
    ShopifyRejectedEvidence.objects.filter(company=company).update(acknowledged=True)
    state = compute_alert_state()
    assert state["open_rejected_evidence"] == 0


# =============================================================================
# T9 — rejected evidence is structurally absent from every financial reader
# =============================================================================


def test_t9_evidence_absent_from_financial_readers(shopify_store, company, owner_membership, authenticated_client):
    from accounting.models import JournalEntry

    _post_webhook("orders/paid", _order_payload(order_id=5100009, total_price="abc"))
    _post_webhook("refunds/create", _refund_payload(refund_id=5200009, order_id=5100009, transactions=None))

    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 2
    # A SEPARATE table: no order row, no refund row, no event, no journal —
    # every ShopifyOrder/ShopifyRefund reader is exclusion-by-construction.
    assert ShopifyOrder.objects.filter(company=company).count() == 0
    assert ShopifyRefund.objects.filter(company=company).count() == 0
    assert BusinessEvent.objects.filter(company=company).count() == 0
    assert JournalEntry.objects.filter(company=company).count() == 0

    # The F1b/F11 dashboard reader (unfiltered ShopifyOrder list) sees nothing.
    rows = authenticated_client.get("/api/shopify/orders/").json()
    assert rows == []


# =============================================================================
# T11 — backup/restore retains the evidence (registry membership; the
#        completeness CI test enforces it stays registered)
# =============================================================================


def test_t11_registered_in_backup_registry():
    from backups.model_registry import get_export_registry

    assert "shopify_connector.ShopifyRejectedEvidence" in get_export_registry()


# =============================================================================
# T12 — no fake financial values are written to ShopifyOrder
# =============================================================================


def test_t12_no_fake_financial_values_on_shopify_order(shopify_store, company):
    _post_webhook("orders/paid", _order_payload(order_id=5100012, total_price="abc", subtotal_price=None))
    # No sentinel-valued ShopifyOrder row exists at all — the evidence table
    # holds the payload; ShopifyOrder totals can never be poisoned by it.
    assert ShopifyOrder.objects.filter(company=company).count() == 0
    assert _evidence(company).rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY


# =============================================================================
# Extension — MALFORMED_JSON capture (HMAC-valid unparseable body)
# =============================================================================


def test_malformed_json_body_persists_evidence_and_acks(shopify_store, company, owner_membership):
    raw = b'{"id": 123, "total_price": '  # authenticated, truncated body
    resp = _post_raw_webhook("orders/paid", raw)
    assert resp.status_code == 200

    row = _evidence(company)
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_JSON
    assert row.parsed_payload is None
    assert base64.b64decode(row.raw_body_b64) == raw
    expected = hashlib.sha256(raw).hexdigest()
    assert row.payload_hash == expected
    assert row.transport_hash == expected
    assert row.external_id is None


def test_malformed_json_unknown_store_keeps_400(company):
    raw = b"{broken"
    resp = _post_raw_webhook("orders/paid", raw, shop_domain="nobody-here.myshopify.com")
    assert resp.status_code == 400
    assert ShopifyRejectedEvidence.objects.count() == 0


def test_malformed_json_out_of_scope_topic_keeps_400(shopify_store, company):
    resp = _post_raw_webhook("products/create", b"{broken")
    assert resp.status_code == 400
    assert ShopifyRejectedEvidence.objects.count() == 0


def test_malformed_json_persistence_failure_is_503(shopify_store, company, monkeypatch):
    from shopify_connector import views as shopify_views

    def _boom(**kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(re_mod, "record_rejected_evidence", _boom)
    resp = _post_raw_webhook("orders/paid", b"{broken")
    assert resp.status_code == 503
    assert ShopifyRejectedEvidence.objects.count() == 0
    assert shopify_views is not None  # imported for clarity of the patched layer


# =============================================================================
# Extension — reopen on re-sight; superseded stays superseded
# =============================================================================


def test_resight_clears_acknowledgment_and_reopens(shopify_store, company, user):
    payload = _order_payload(order_id=5100020, total_price="abc")
    _post_webhook("orders/paid", payload)
    row = _evidence(company)
    from django.utils import timezone

    ShopifyRejectedEvidence.objects.filter(pk=row.pk).update(
        acknowledged=True,
        acknowledged_at=timezone.now(),
        acknowledged_by=user,
        acknowledgment_note="looked at it",
    )

    _post_webhook("orders/paid", payload, delivery_id="wh-again")
    row.refresh_from_db()
    assert row.acknowledged is False
    assert row.acknowledged_at is None
    assert row.acknowledged_by is None
    # The operator's note is history, not state — it survives the reopen.
    assert row.acknowledgment_note == "looked at it"
    assert row.occurrence_count == 2


def test_superseded_record_stays_superseded_on_stale_duplicate(shopify_store, company):
    bad = _order_payload(order_id=5100021, total_price="abc")
    _post_webhook("orders/paid", bad)
    _post_webhook("orders/paid", _order_payload(order_id=5100021))  # heals + supersedes
    row = _evidence(company)
    superseded_at = row.superseded_at
    assert superseded_at is not None

    # The old malformed bytes arrive again (a stale redelivery).
    _post_webhook("orders/paid", bad, delivery_id="wh-stale")
    row.refresh_from_db()
    assert row.superseded_at == superseded_at, "stale duplicates never reopen a superseded record"
    assert row.occurrence_count == 2  # the re-delivery is still recorded
    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 1


# =============================================================================
# Extension — redaction persistence (GDPR)
# =============================================================================


def test_redaction_clears_pii_and_resight_never_restores_it(shopify_store, company):
    payload = _order_payload(
        order_id=5100022,
        total_price="abc",
        customer={"id": 42, "email": "shopper@example.com", "first_name": "S"},
    )
    _post_webhook("orders/paid", payload)
    row = _evidence(company)
    assert row.parsed_payload["customer"]["email"] == "shopper@example.com"

    scrubbed = re_mod.redact_evidence(ShopifyRejectedEvidence.objects.filter(pk=row.pk))
    assert scrubbed == 1
    row.refresh_from_db()
    assert row.parsed_payload is None
    assert row.raw_body_b64 == ""
    assert row.redacted_at is not None
    # Defect messages quoted payload value reprs — those are scrubbed too;
    # codes and field names (interpretation, not PII) survive.
    assert "abc" not in row.rejection_message
    assert row.rejection_code in row.rejection_message
    assert all(e["message"] == "[redacted]" for e in row.validation_errors)
    assert all(e["code"] for e in row.validation_errors)
    payload_hash_before = row.payload_hash
    redacted_at_before = row.redacted_at

    # The same malformed payload arrives again: bump, but PII stays gone.
    _post_webhook("orders/paid", payload, delivery_id="wh-after-redact")
    row.refresh_from_db()
    assert row.occurrence_count == 2
    assert row.parsed_payload is None, "re-sighting must never restore PII into a redacted record"
    assert row.raw_body_b64 == ""
    assert row.redacted_at == redacted_at_before
    assert row.payload_hash == payload_hash_before

    # Idempotent: a second redact touches nothing.
    assert re_mod.redact_evidence(ShopifyRejectedEvidence.objects.filter(pk=row.pk)) == 0


def test_shop_redact_scrubs_rejected_evidence(shopify_store, company):
    from shopify_connector.gdpr import execute_shop_redact
    from shopify_connector.models import GdprRequest

    _post_webhook(
        "orders/paid",
        _order_payload(order_id=5100023, total_price="abc", customer={"id": 7, "email": "pii@example.com"}),
    )
    req = GdprRequest.objects.create(
        topic=GdprRequest.Topic.SHOP_REDACT,
        shop_domain=SHOP_DOMAIN,
        payload={"shop_domain": SHOP_DOMAIN},
        payload_signature="sig-a5pr2b-shop-redact",
        status=GdprRequest.Status.PENDING,
    )
    execute_shop_redact(req)

    row = _evidence(company)
    assert row.redacted_at is not None
    assert row.parsed_payload is None
    assert row.raw_body_b64 == ""
    # Identity + interpretation survive redaction.
    assert row.payload_hash and row.rejection_code and row.dedup_hash


# =============================================================================
# Extension — the five supersession DB constraints
# =============================================================================


def _bare_evidence_kwargs(company, store, **overrides):
    payload = {"probe": overrides.pop("probe", "x")}
    kwargs = dict(
        company=company,
        store=store,
        store_public_id=store.public_id,
        shop_domain=store.shop_domain,
        resource_kind=ShopifyRejectedEvidence.ResourceKind.ORDER,
        ingress_kind=ShopifyRejectedEvidence.IngressKind.WEBHOOK,
        source_topic="orders/paid",
        parsed_payload=payload,
        payload_hash=re_mod.canonical_payload_hash(payload),
        rejection_code=ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY,
        rejection_message="probe",
        validation_errors=[],
        dedup_hash=re_mod.compute_dedup_hash(
            company.id, store.public_id, "ORDER", re_mod.canonical_payload_hash(payload)
        ),
    )
    kwargs.update(overrides)
    return kwargs


def test_constraint_open_row_cannot_carry_supersession_links(shopify_store, company):
    from django.utils import timezone as tz

    order = ShopifyOrder.objects.create(
        company=company,
        store=shopify_store,
        shopify_order_id=5100030,
        shopify_order_number="5100030",
        total_price=Decimal("1"),
        subtotal_price=Decimal("1"),
        currency="EGP",
        order_date=tz.now().date(),
        shopify_created_at=tz.now(),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ShopifyRejectedEvidence.objects.create(
            **_bare_evidence_kwargs(company, shopify_store, probe="open-fk", superseded_by_order=order)
        )


def test_constraint_order_evidence_never_superseded_by_refund(shopify_store, company):
    from django.utils import timezone as tz

    order = ShopifyOrder.objects.create(
        company=company,
        store=shopify_store,
        shopify_order_id=5100031,
        shopify_order_number="5100031",
        total_price=Decimal("1"),
        subtotal_price=Decimal("1"),
        currency="EGP",
        order_date=tz.now().date(),
        shopify_created_at=tz.now(),
    )
    refund = ShopifyRefund.objects.create(
        company=company,
        order=order,
        shopify_refund_id=5200031,
        amount=Decimal("1"),
        currency="EGP",
        shopify_created_at=tz.now(),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ShopifyRejectedEvidence.objects.create(
            **_bare_evidence_kwargs(
                company,
                shopify_store,
                probe="order-by-refund",
                resource_kind=ShopifyRejectedEvidence.ResourceKind.ORDER,
                superseded_at=tz.now(),
                superseded_by_refund=refund,
                superseded_target_public_id=refund.public_id,
            )
        )
    # And the mirror: REFUND evidence can never carry superseded_by_order.
    with pytest.raises(IntegrityError), transaction.atomic():
        ShopifyRejectedEvidence.objects.create(
            **_bare_evidence_kwargs(
                company,
                shopify_store,
                probe="refund-by-order",
                resource_kind=ShopifyRejectedEvidence.ResourceKind.REFUND,
                superseded_at=tz.now(),
                superseded_by_order=order,
                superseded_target_public_id=order.public_id,
            )
        )
    # And both FKs at once is impossible for any kind.
    with pytest.raises(IntegrityError), transaction.atomic():
        ShopifyRejectedEvidence.objects.create(
            **_bare_evidence_kwargs(
                company,
                shopify_store,
                probe="both-fks",
                superseded_at=tz.now(),
                superseded_by_order=order,
                superseded_by_refund=refund,
                superseded_target_public_id=order.public_id,
            )
        )


def test_constraint_snapshot_survives_canonical_row_deletion(shopify_store, company):
    """The SET_NULL path: superseded_at + superseded_target_public_id legally
    outlive the FK when the canonical row is later removed."""
    _post_webhook("orders/paid", _order_payload(order_id=5100032, total_price="abc"))
    _post_webhook("orders/paid", _order_payload(order_id=5100032))
    row = _evidence(company)
    order_public_id = row.superseded_target_public_id
    assert row.superseded_by_order_id is not None

    ShopifyOrder.objects.filter(company=company, shopify_order_id=5100032).delete()
    row.refresh_from_db()
    assert row.superseded_by_order_id is None
    assert row.superseded_at is not None
    assert row.superseded_target_public_id == order_public_id


# =============================================================================
# Extension — the adapter-owned queue endpoints
# =============================================================================


def test_queue_defaults_to_open_and_keyset_paginates(shopify_store, company, owner_membership, authenticated_client):
    for n in range(3):
        _post_webhook("orders/paid", _order_payload(order_id=5100040 + n, total_price="abc"))
    # One acknowledged + one superseded — both must drop out of the default listing.
    acked = ShopifyRejectedEvidence.objects.filter(company=company).order_by("id").first()
    ShopifyRejectedEvidence.objects.filter(pk=acked.pk).update(acknowledged=True)
    _post_webhook("orders/paid", _order_payload(order_id=5100041))  # supersedes the second

    resp = authenticated_client.get("/api/shopify/rejected-evidence/", {"limit": 1})
    body = resp.json()
    assert body["total_count"] == 1  # only the third row is still open
    assert len(body["results"]) == 1
    assert body["results"][0]["external_id"] == "5100042"

    # Keyset walk: a full page yields a cursor; the next page is empty ⇒ None.
    assert body["next_cursor"] == str(body["results"][0]["id"])
    resp2 = authenticated_client.get("/api/shopify/rejected-evidence/", {"limit": 1, "cursor": body["next_cursor"]})
    body2 = resp2.json()
    assert body2["results"] == []
    assert body2["next_cursor"] is None

    # acknowledged=all surfaces everything, superseded included.
    resp3 = authenticated_client.get("/api/shopify/rejected-evidence/", {"acknowledged": "all"})
    assert resp3.json()["total_count"] == 3

    # acknowledged=true is CLOSED semantics: acknowledged OR healed/superseded —
    # a superseded row must not vanish from both Open and Resolved.
    resp4 = authenticated_client.get("/api/shopify/rejected-evidence/", {"acknowledged": "true"})
    assert resp4.json()["total_count"] == 2


def test_acknowledge_endpoint_owner_only(
    shopify_store, company, owner_membership, user, authenticated_client, regular_user, user_membership, api_client
):
    _post_webhook("orders/paid", _order_payload(order_id=5100050, total_price="abc"))
    row = _evidence(company)

    # A regular member is refused. (api_client IS authenticated_client's
    # underlying instance — re-authenticate as the owner afterwards.)
    api_client.force_authenticate(user=regular_user)
    resp = api_client.post(f"/api/shopify/rejected-evidence/{row.pk}/acknowledge/", {"acknowledgment_note": "x"})
    assert resp.status_code == 403
    row.refresh_from_db()
    assert row.acknowledged is False
    api_client.force_authenticate(user=user)

    # The owner acknowledges (a claim of review, not of processing).
    resp = authenticated_client.post(
        f"/api/shopify/rejected-evidence/{row.pk}/acknowledge/", {"acknowledgment_note": "reviewed"}
    )
    assert resp.status_code == 200
    row.refresh_from_db()
    assert row.acknowledged is True
    assert row.acknowledged_at is not None
    assert row.acknowledgment_note == "reviewed"

    # Second acknowledge is a friendly no-op.
    resp = authenticated_client.post(f"/api/shopify/rejected-evidence/{row.pk}/acknowledge/", {})
    assert resp.status_code == 200
    assert resp.json()["detail"] == "Already acknowledged."


def test_queue_is_company_scoped(shopify_store, company, second_company, owner_membership, authenticated_client):
    other_store = ShopifyStore.objects.create(
        company=second_company,
        shop_domain="other-a5pr2b.myshopify.com",
        access_token="t",
        status=ShopifyStore.Status.ACTIVE,
    )
    re_mod.record_rejected_evidence(
        store=other_store,
        resource_kind=ShopifyRejectedEvidence.ResourceKind.ORDER,
        rejection_code=ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY,
        rejection_message="other company's evidence",
        validation_errors=[],
        ingress=re_mod.poller_ingress("poller/orders-paid"),
        parsed_payload={"id": 1, "total_price": "x"},
    )
    resp = authenticated_client.get("/api/shopify/rejected-evidence/")
    assert resp.json()["total_count"] == 0


# =============================================================================
# Extension — ingress provenance + refund-side coverage
# =============================================================================


def test_direct_command_call_records_poller_provenance(shopify_store, company):
    result = commands.process_order_paid(shopify_store, _order_payload(order_id=5100060, total_price="abc"))
    assert not result.success
    row = _evidence(company)
    assert row.ingress_kind == ShopifyRejectedEvidence.IngressKind.POLLER
    assert row.source_topic == "poller/orders-paid"
    assert row.last_delivery_id == "poller"
    assert row.raw_body_b64 == ""  # poller evidence has no transport bytes
    assert row.transport_hash == ""


def test_paid_at_creation_orders_create_delegates_with_webhook_provenance(shopify_store, company):
    payload = _order_payload(order_id=5100061, total_price="abc", financial_status="paid")
    resp = _post_webhook("orders/create", payload)
    assert resp.status_code == 200
    row = _evidence(company)
    assert row.ingress_kind == ShopifyRejectedEvidence.IngressKind.WEBHOOK
    assert row.source_topic == "orders/create"


def test_malformed_refund_persists_refund_evidence(shopify_store, company, owner_membership):
    payload = _refund_payload(refund_id=5200070, order_id=5100070, transactions="not-a-list")
    resp = _post_webhook("refunds/create", payload)
    assert resp.status_code == 200
    row = _evidence(company)
    assert row.resource_kind == ShopifyRejectedEvidence.ResourceKind.REFUND
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_STRUCTURE
    assert row.external_id == "5200070"
    assert row.parent_external_id == "5100070"
    assert ShopifyRefund.objects.filter(company=company).count() == 0


def test_missing_refund_id_still_acks_200_with_evidence(shopify_store, company):
    payload = _refund_payload()
    payload.pop("id")
    resp = _post_webhook("refunds/create", payload)
    assert resp.status_code == 200  # the A159 pin, now WITH durable evidence
    row = _evidence(company)
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MISSING_EXTERNAL_ID
    assert row.external_id is None
    assert row.parent_external_id == "9100001"


def test_corrected_refund_supersedes_refund_evidence(shopify_store, company):
    # The parent order must exist and be posted for the refund to process.
    assert commands.process_order_paid(shopify_store, _order_payload(order_id=5100080)).success

    bad = _refund_payload(refund_id=5200080, order_id=5100080, transactions=None)
    assert _post_webhook("refunds/create", bad).status_code == 200
    evidence = ShopifyRejectedEvidence.objects.get(
        company=company, resource_kind=ShopifyRejectedEvidence.ResourceKind.REFUND
    )
    assert evidence.superseded_at is None

    good = _refund_payload(refund_id=5200080, order_id=5100080)
    assert _post_webhook("refunds/create", good).status_code == 200

    refund = ShopifyRefund.objects.get(company=company, shopify_refund_id=5200080)
    evidence.refresh_from_db()
    assert evidence.superseded_at is not None
    assert evidence.superseded_by_refund_id == refund.pk
    assert evidence.superseded_target_public_id == refund.public_id


def test_refund_race_still_retryable_and_evidence_free(shopify_store, company):
    """The A159 pin survives A5-PR2b: a structurally VALID refund racing its
    order is transient — 5xx, no evidence, no refund row."""
    resp = _post_webhook("refunds/create", _refund_payload(refund_id=5200090, order_id=424242))
    assert resp.status_code in (500, 503)
    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 0
    assert ShopifyRefund.objects.filter(company=company).count() == 0


# =============================================================================
# Extension — happy path writes no evidence; valid-today payloads stay valid
# =============================================================================


def test_valid_payload_writes_no_evidence(shopify_store, company):
    assert commands.process_order_paid(shopify_store, _order_payload(order_id=5100090)).success
    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 0


def test_null_customer_payload_stays_valid(shopify_store, company):
    """The live-Shopify tolerated shapes (customer: null, empty lists, absent
    transactions) must keep validating — the validator never rejects a payload
    the pipeline honestly processes (test_shopify_webhook_handlers pin)."""
    payload = _order_payload(order_id=5100091, customer=None, transactions=None)
    del payload["shipping_lines"]
    assert commands.process_order_paid(shopify_store, payload).success
    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 0


# =============================================================================
# Adversarial-review fix pins (pre-push multi-lens review, 2026-08-23)
# =============================================================================


def test_null_note_refund_is_routine_and_processes(shopify_store, company):
    """note: null is a ROUTINE Shopify refund shape (the poller's GraphQL mapper
    normalizes it with `r.get("note") or ""`); the webhook ingress must process
    it, not reject it as evidence."""
    assert commands.process_order_paid(shopify_store, _order_payload(order_id=5100100)).success

    result = commands.process_refund(shopify_store, _refund_payload(refund_id=5200100, order_id=5100100, note=None))
    assert result.success, result.error
    refund = ShopifyRefund.objects.get(company=company, shopify_refund_id=5200100)
    assert refund.reason == ""
    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 0


def test_nonfinite_money_rejects_and_evidence_is_storable(shopify_store, company):
    """json.loads accepts bare NaN/Infinity tokens; PostgreSQL jsonb refuses
    them. The verdict must be MALFORMED_MONEY and the stored parsed_payload
    must be sanitized so the evidence INSERT itself can never fail over the
    very token that made the payload malformed."""
    raw = json.dumps(_order_payload(order_id=5100101)).replace('"500.00"', "NaN", 1).encode()
    assert json.loads(raw)["total_price"] != json.loads(raw)["total_price"]  # NaN round-trips

    resp = _post_raw_webhook("orders/paid", raw)
    assert resp.status_code == 200
    row = _evidence(company)
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY
    assert row.parsed_payload["total_price"] == "nan"  # sanitized for storage
    # Identity was hashed from the ORIGINAL parsed value, not the sanitized one.
    assert row.payload_hash == re_mod.canonical_payload_hash(json.loads(raw))


def test_exotic_id_strings_classify_instead_of_crashing(shopify_store, company):
    """str.isdigit() accepts superscripts that make int() raise, and CPython
    caps int() conversion length — both must classify as untrustworthy ids, not
    crash the validator into a 500/503 loop."""
    superscript_two = "²"
    arabic_digits = "١٢٣"
    for bad_id in (superscript_two, "9" * 5000, arabic_digits):
        resp = _post_webhook("orders/paid", _order_payload(id=bad_id))
        assert resp.status_code == 200, f"id {bad_id[:10]!r} must be classified, not crash"
    rows = ShopifyRejectedEvidence.objects.filter(company=company)
    assert rows.count() == 3  # three distinct payloads -> three evidence rows
    assert all(r.rejection_code == ShopifyRejectedEvidence.RejectionCode.MISSING_EXTERNAL_ID for r in rows)
    assert all(r.external_id is None for r in rows)


def test_null_nested_money_is_permanent_not_retry_loop(shopify_store, company):
    """A present-as-null amount inside transactions crashes the live
    Decimal(str(None)) coercion — it must classify as MALFORMED_MONEY evidence,
    never fall through to the transient 503 path."""
    payload = _order_payload(
        order_id=5100102,
        transactions=[{"kind": "sale", "status": "success", "amount": None}],
    )
    resp = _post_webhook("orders/paid", payload)
    assert resp.status_code == 200
    assert _evidence(company).rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY


def test_truthy_garbage_created_at_is_malformed_date(shopify_store, company):
    """The live writer stores the RAW created_at into shopify_created_at when
    truthy (`order_date_str or now()`) — the date FALLBACK only covers the
    falsy case. A truthy unparseable value must be MALFORMED_DATE evidence, not
    a post-validation crash into the endless retry loop."""
    resp = _post_webhook("orders/paid", _order_payload(order_id=5100103, created_at="not-a-date"))
    assert resp.status_code == 200
    assert _evidence(company).rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_DATE
    assert ShopifyOrder.objects.filter(company=company).count() == 0


def test_falsy_or_absent_created_at_keeps_the_fallback(shopify_store, company):
    """The documented fallback shapes stay VALID: absent or empty created_at
    books on now() exactly as today."""
    p1 = _order_payload(order_id=5100104, created_at="")
    assert commands.process_order_paid(shopify_store, p1).success
    p2 = _order_payload(order_id=5100105)
    del p2["created_at"]
    assert commands.process_order_paid(shopify_store, p2).success
    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 0


def test_money_bound_guards_database_rounding(shopify_store, company):
    """A value the database would ROUND over the 16-integer-digit ceiling must
    reject as MALFORMED_MONEY, not overflow numeric(18,2) post-validation."""
    resp = _post_webhook("orders/paid", _order_payload(order_id=5100106, total_price="9999999999999999.999"))
    assert resp.status_code == 200
    assert _evidence(company).rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY


def test_skip_guard_supersedes_open_order_evidence(shopify_store, company):
    """A malformed duplicate arriving AFTER the order already processed leaves
    open evidence; the NEXT valid redelivery hits the idempotency skip-guard —
    which must supersede that evidence, not strand it open forever."""
    good = _order_payload(order_id=5100107)
    assert commands.process_order_paid(shopify_store, good).success

    bad = _order_payload(order_id=5100107, total_price="abc")
    assert _post_webhook("orders/paid", bad).status_code == 200
    evidence = _evidence(company)
    assert evidence.superseded_at is None

    resp = _post_webhook("orders/paid", good)
    assert resp.status_code == 200  # skipped (already processed)
    evidence.refresh_from_db()
    order = ShopifyOrder.objects.get(company=company, shopify_order_id=5100107)
    assert evidence.superseded_at is not None
    assert evidence.superseded_by_order_id == order.pk
    assert evidence.superseded_target_public_id == order.public_id


def test_skip_guard_supersedes_open_refund_evidence(shopify_store, company):
    """The refund-side mirror: a processed canonical refund heals open evidence
    for its identity via the dedup skip-guard."""
    assert commands.process_order_paid(shopify_store, _order_payload(order_id=5100108)).success
    good = _refund_payload(refund_id=5200108, order_id=5100108)
    assert commands.process_refund(shopify_store, good).success

    bad = _refund_payload(refund_id=5200108, order_id=5100108, transactions=None)
    assert _post_webhook("refunds/create", bad).status_code == 200
    evidence = ShopifyRejectedEvidence.objects.get(
        company=company, resource_kind=ShopifyRejectedEvidence.ResourceKind.REFUND
    )
    assert evidence.superseded_at is None

    assert _post_webhook("refunds/create", good).status_code == 200  # skipped
    evidence.refresh_from_db()
    refund = ShopifyRefund.objects.get(company=company, shopify_refund_id=5200108)
    assert evidence.superseded_at is not None
    assert evidence.superseded_by_refund_id == refund.pk


def test_shop_redact_reaches_evidence_after_store_deletion(shopify_store, company):
    """Evidence survives ShopifyStore deletion by design (SET_NULL + snapshots);
    shop/redact must still scrub it via the shop_domain snapshot sweep."""
    from shopify_connector.gdpr import execute_shop_redact
    from shopify_connector.models import GdprRequest

    _post_webhook(
        "orders/paid",
        _order_payload(order_id=5100109, total_price="abc", customer={"id": 9, "email": "gone@example.com"}),
    )
    row = _evidence(company)
    ShopifyStore.objects.filter(pk=shopify_store.pk).delete()
    row.refresh_from_db()
    assert row.store_id is None  # SET_NULL fired; snapshots remain

    req = GdprRequest.objects.create(
        topic=GdprRequest.Topic.SHOP_REDACT,
        shop_domain=SHOP_DOMAIN,
        payload={"shop_domain": SHOP_DOMAIN},
        payload_signature="sig-a5pr2b-store-deleted",
        status=GdprRequest.Status.PENDING,
    )
    execute_shop_redact(req)
    row.refresh_from_db()
    assert row.redacted_at is not None
    assert row.parsed_payload is None
    assert row.shop_domain == SHOP_DOMAIN  # attribution snapshot survives


def test_invalid_utf8_body_is_captured_as_malformed_json(shopify_store, company):
    """json.loads raises UnicodeDecodeError (not JSONDecodeError) for an
    HMAC-valid non-UTF-8 body — it must be captured as MALFORMED_JSON evidence,
    not escape as an unhandled 500 loop."""
    raw = b'\xff\xfe{"id": 1}'
    resp = _post_raw_webhook("orders/paid", raw)
    assert resp.status_code == 200
    row = _evidence(company)
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_JSON
    assert base64.b64decode(row.raw_body_b64) == raw


# =============================================================================
# Codex round-1 fix pins (PR #130, 2026-08-23)
# =============================================================================


def test_non_string_event_bound_fields_reject_permanently(shopify_store, company):
    """Codex round-1 P2: values copied verbatim into typed event payloads must
    be actual strings — Django str()-coerces at the column, but
    validate_event_payload rejects a non-str at EMISSION, after the row write,
    rolling back into the retryable loop. Each is a permanent provider-data
    error → evidence + 200."""
    # Order: financial_status rides ShopifyOrderPaidData.financial_status: str.
    resp = _post_webhook("orders/paid", _order_payload(order_id=5100110, financial_status=123))
    assert resp.status_code == 200
    # Order: name rides the event as order_name/document_ref verbatim.
    resp = _post_webhook("orders/paid", _order_payload(order_id=5100111, name=123))
    assert resp.status_code == 200
    # Order: a non-str customer email rides customer_email: str.
    resp = _post_webhook("orders/paid", _order_payload(order_id=5100112, customer={"email": 5}))
    assert resp.status_code == 200
    rows = ShopifyRejectedEvidence.objects.filter(company=company)
    assert rows.count() == 3
    assert all(r.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_STRUCTURE for r in rows)
    assert ShopifyOrder.objects.filter(company=company).count() == 0

    # Refund: note rides ShopifyRefundCreatedData.reason: str via the in-memory
    # model attribute (get_prep_value only coerces at the SQL layer).
    resp = _post_webhook("refunds/create", _refund_payload(refund_id=5200110, note=123))
    assert resp.status_code == 200
    refund_evidence = ShopifyRejectedEvidence.objects.get(
        company=company, resource_kind=ShopifyRejectedEvidence.ResourceKind.REFUND
    )
    assert refund_evidence.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_STRUCTURE


def test_currency_must_match_the_event_contract(shopify_store, company):
    """Codex round-1 class sweep: the event contract requires exactly 3
    uppercase letters (events/types.py currency_fields) — anything looser
    passes storage but fails emission into the loop. Absent stays valid (the
    live default)."""
    for bad_currency in ("egp", "EG1", "", "EGPX"):
        resp = _post_webhook("orders/paid", _order_payload(order_id=5100113, currency=bad_currency))
        assert resp.status_code == 200, f"currency {bad_currency!r} must reject, not loop"
    rows = ShopifyRejectedEvidence.objects.filter(company=company)
    assert rows.count() == 4  # four distinct payloads
    assert all(r.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_CURRENCY for r in rows)

    payload = _order_payload(order_id=5100114)
    del payload["currency"]
    assert commands.process_order_paid(shopify_store, payload).success  # absent ⇒ live default


def test_null_customer_email_is_routine_and_normalized(shopify_store, company):
    """A present-null customer email (guest checkout) is an honest order — the
    event build normalizes it to "" instead of handing None to the typed
    payload (which rejects None at emission)."""
    payload = _order_payload(order_id=5100115, customer={"email": None, "first_name": "Guest"})
    result = commands.process_order_paid(shopify_store, payload)
    assert result.success, result.error
    event = BusinessEvent.objects.get(company=company, idempotency_key="shopify.order.paid:5100115")
    assert event.get_data()["customer_email"] == ""
    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 0


def test_aggregate_refund_amount_is_bounded(shopify_store, company):
    """Codex round-1 P2: two amounts that each fit numeric(18,2) can SUM over
    it — the overflow would hit the ShopifyRefund.amount write after
    validation. The aggregate (and the refund_line_items fallback aggregate)
    must reject as MALFORMED_MONEY."""
    payload = _refund_payload(
        refund_id=5200111,
        transactions=[
            {"kind": "refund", "status": "success", "amount": "6000000000000000"},
            {"kind": "refund", "status": "success", "amount": "6000000000000000"},
        ],
    )
    resp = _post_webhook("refunds/create", payload)
    assert resp.status_code == 200
    row = _evidence(company)
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY
    assert ShopifyRefund.objects.filter(company=company).count() == 0
    row.delete()

    fallback = _refund_payload(
        refund_id=5200112,
        transactions=[],
        refund_line_items=[{"subtotal": "6000000000000000"}, {"subtotal": "6000000000000000"}],
    )
    resp = _post_webhook("refunds/create", fallback)
    assert resp.status_code == 200
    assert _evidence(company).rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY


# =============================================================================
# Codex round-2 fix pins (PR #130, 2026-08-23)
# =============================================================================


def test_nonfinite_float_anywhere_rejects_permanently(shopify_store, company):
    """Codex round-2 P2: json.loads accepts NaN/Infinity tokens anywhere;
    PostgreSQL jsonb refuses them at the CANONICAL raw_payload write — a
    non-finite value in even an unchecked metadata field must reject as
    evidence up front, never crash post-validation into the retry loop."""
    raw = json.dumps(_order_payload(order_id=5100120, note_attributes=[{"weight": 1.5}])).replace("1.5", "Infinity", 1)
    resp = _post_raw_webhook("orders/paid", raw.encode())
    assert resp.status_code == 200
    row = _evidence(company)
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_STRUCTURE
    assert any("non-finite" in e["message"] for e in row.validation_errors)
    assert ShopifyOrder.objects.filter(company=company).count() == 0


def test_data_request_export_includes_rejected_evidence(shopify_store, company):
    """Codex round-2 P2: a shopper whose data exists ONLY as rejected evidence
    (the malformed order never became a canonical row) must still appear in the
    customers/data_request export."""
    from shopify_connector.gdpr import execute_customer_data_request
    from shopify_connector.models import GdprRequest

    _post_webhook(
        "orders/paid",
        _order_payload(
            order_id=5100121,
            total_price="abc",
            customer={"id": 4242, "email": "only-evidence@example.com"},
        ),
    )
    assert ShopifyOrder.objects.filter(company=company).count() == 0

    req = GdprRequest.objects.create(
        topic=GdprRequest.Topic.CUSTOMERS_DATA_REQUEST,
        shop_domain=SHOP_DOMAIN,
        customer_id=4242,
        customer_email="only-evidence@example.com",
        payload={"customer": {"id": 4242, "email": "only-evidence@example.com"}},
        payload_signature="sig-a5pr2b-data-request",
        status=GdprRequest.Status.PENDING,
    )
    result = execute_customer_data_request(req)
    assert result["rejected_evidence_matched"] == 1
    exported = [e for e in result["export"] if e.get("rejected_evidence")]
    assert len(exported) == 1
    assert exported[0]["rejection_code"] == "MALFORMED_MONEY"
    assert exported[0]["customer"]["email"] == "only-evidence@example.com"
    assert exported[0]["payload"]["id"] == 5100121


def test_redact_matcher_survives_non_string_email_in_evidence(shopify_store, company):
    """Codex round-2 P2: evidence deliberately preserves malformed shapes — a
    non-str customer.email must not crash the redact job's matcher; the row
    still matches by customer id."""
    from shopify_connector.gdpr import execute_customer_redact
    from shopify_connector.models import GdprRequest

    _post_webhook(
        "orders/paid",
        _order_payload(order_id=5100122, customer={"id": 777, "email": 12345}),
    )
    row = _evidence(company)
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_STRUCTURE

    req = GdprRequest.objects.create(
        topic=GdprRequest.Topic.CUSTOMERS_REDACT,
        shop_domain=SHOP_DOMAIN,
        customer_id=777,
        customer_email="someone@example.com",
        payload={"customer": {"id": 777, "email": "someone@example.com"}},
        payload_signature="sig-a5pr2b-nonstr-email",
        status=GdprRequest.Status.PENDING,
    )
    execute_customer_redact(req)  # must not raise
    row.refresh_from_db()
    assert row.redacted_at is not None
    assert row.parsed_payload is None


def test_notification_failure_never_blocks_committed_evidence(shopify_store, company, owner_membership, monkeypatch):
    """Codex round-2 P2: once the evidence transaction has committed, a failing
    notification must not 503 the delivery — every redelivery would take the
    dedup path (created=False) and could never retry the notification anyway.
    Notification is delivery-only; /_health/alerts pages on the open evidence."""
    from accounts.models import Notification as NotificationModel
    from ops.health import compute_alert_state

    def _boom(**kwargs):
        raise RuntimeError("notification backend down")

    monkeypatch.setattr(NotificationModel, "notify_company_admins", _boom)
    resp = _post_webhook("orders/paid", _order_payload(order_id=5100123, total_price="abc"))
    assert resp.status_code == 200
    row = _evidence(company)
    assert row.occurrence_count == 1
    assert NotificationModel.objects.filter(company=company).count() == 0
    # The guaranteed pager still fires on the durable record.
    state = compute_alert_state()
    assert state["open_rejected_evidence"] == 1


# =============================================================================
# Codex round-3 fix pins (PR #130, 2026-08-23)
# =============================================================================


def test_notification_db_error_never_rolls_back_evidence(shopify_store, company, owner_membership, monkeypatch):
    """Codex round-3 P1: inside process_refund's outer @transaction.atomic the
    evidence write is only a SAVEPOINT — a real database error raised by the
    notification, caught without its own savepoint, would mark the outer
    transaction rollback-only and silently discard the evidence while the
    webhook still acks 200. The notification's inner atomic isolates it."""
    from django.db import connection as db_connection

    from accounts.models import Notification as NotificationModel

    def _broken_sql(**kwargs):
        # A GENUINE failed query (not a raised-from-Python exception) — the
        # only thing that marks the enclosing atomic rollback-only.
        with db_connection.cursor() as cur:
            cur.execute("SELECT * FROM nonexistent_table_a5pr2b")

    monkeypatch.setattr(NotificationModel, "notify_company_admins", _broken_sql)

    # The refund path runs the writer INSIDE @transaction.atomic.
    payload = _refund_payload(refund_id=5200120, transactions="not-a-list")
    resp = _post_webhook("refunds/create", payload)
    assert resp.status_code == 200

    row = ShopifyRejectedEvidence.objects.get(company=company)
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_STRUCTURE
    assert row.occurrence_count == 1  # the evidence survived the notification failure


def test_storable_json_is_iteration_safe(shopify_store):
    """Codex round-3 P2: the evidence sanitizer must never RecursionError on a
    deeply nested but parseable payload — that would strand the delivery in the
    retry loop with no preserved evidence."""
    deep: list = [float("inf")]
    for _ in range(5000):
        deep = [deep]
    result = re_mod._storable_json({"id": 1, "metadata": deep})
    node = result["metadata"]
    for _ in range(5000):
        node = node[0]
    assert node == ["inf"]  # the non-finite leaf was sanitized at full depth


def test_moderately_deep_malformed_payload_persists_evidence(shopify_store, company):
    """End-to-end: a malformed payload with a few hundred nesting levels still
    becomes durable evidence (webhook 200), not a retry loop."""
    nested: list = []
    for _ in range(300):
        nested = [nested]
    payload = _order_payload(order_id=5100130, total_price="abc", note_attributes=nested)
    resp = _post_webhook("orders/paid", payload)
    assert resp.status_code == 200
    assert _evidence(company).rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY


def test_aggregate_shipping_amount_is_bounded(shopify_store, company):
    """Codex round-3 P2: two individually storable shipping prices can SUM past
    numeric(18,2) — the sum rides the event as total_shipping and becomes a
    projection unit_price that deterministically fails to post. Reject as
    MALFORMED_MONEY evidence instead of emitting an unpostable canonical order."""
    payload = _order_payload(
        order_id=5100131,
        shipping_lines=[{"price": "6000000000000000"}, {"price": "6000000000000000"}],
    )
    resp = _post_webhook("orders/paid", payload)
    assert resp.status_code == 200
    assert _evidence(company).rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY
    assert ShopifyOrder.objects.filter(company=company).count() == 0
    assert BusinessEvent.objects.filter(company=company).count() == 0


def test_redaction_clears_acknowledgment_note(shopify_store, company, user):
    """Codex round-1 P2: the acknowledgment note is free-form operator input
    shown next to the raw evidence — an operator can copy shopper PII into it,
    so redaction clears it (the acknowledged/at/by audit facts survive)."""
    from django.utils import timezone as tz

    _post_webhook("orders/paid", _order_payload(order_id=5100116, total_price="abc"))
    row = _evidence(company)
    ShopifyRejectedEvidence.objects.filter(pk=row.pk).update(
        acknowledged=True,
        acknowledged_at=tz.now(),
        acknowledged_by=user,
        acknowledgment_note="shopper Jane Doe, jane@example.com — contacted",
    )

    assert re_mod.redact_evidence(ShopifyRejectedEvidence.objects.filter(pk=row.pk)) == 1
    row.refresh_from_db()
    assert row.acknowledgment_note == ""
    assert row.acknowledged is True
    assert row.acknowledged_by_id == user.pk
