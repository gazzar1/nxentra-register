# tests/test_a5_pr2c_negative_refund_evidence.py
"""A5-PR2c — negative refunds route through rejected source evidence.

Settled founder decision (assessment finding C4): a structurally valid refund
whose aggregate amount is negative previously produced a ShopifyRefund
status=ERROR row + one bespoke Notification — durable, but invisible to
/finance/exceptions and /_health/alerts, and its refund-id dedup foreclosed the
corrected redelivery forever. It now takes the already-merged rejected-evidence
contract:

    negative aggregate refund -> ShopifyRejectedEvidence (MALFORMED_MONEY)
    -> no ShopifyRefund row, no BusinessEvent, no sequence, no journal
    -> webhook 200 (permanent; no 503 loop); evidence-write failure -> 503
    -> identical redelivery re-sights ONE row (no duplicate Notification)
    -> corrected redelivery creates the canonical ShopifyRefund exactly once
       and supersedes every matching open evidence row.

One canonical aggregate (payload_validation.refund_aggregate_amount) is shared
by the validator's bounds and process_refund's amount — no drift possible.
Positive/zero aggregates (including ones containing negative components) keep
their exact prior behavior.
"""

import base64
import hashlib
import hmac
import inspect
import json

import pytest
from django.test import Client

from accounts.models import Notification
from events.models import BusinessEvent, CompanyEventCounter
from shopify_connector import commands
from shopify_connector.models import (
    ShopifyOrder,
    ShopifyRefund,
    ShopifyRejectedEvidence,
    ShopifyStore,
)

pytestmark = pytest.mark.django_db

TEST_SECRET = "test-shopify-shared-secret"
WEBHOOK_URL = "/api/shopify/webhooks/"
SHOP_DOMAIN = "a5pr2c-test.myshopify.com"


@pytest.fixture(autouse=True)
def _patch_shopify_secret(monkeypatch):
    monkeypatch.setattr(commands, "SHOPIFY_API_SECRET", TEST_SECRET)


@pytest.fixture(autouse=True)
def _throttle_neutral():
    """Anonymous-webhook-heavy file: clear the process-wide AnonRateThrottle
    cache around each test (the battery-wide 100/hour counter would otherwise
    spill 429s into later anonymous-endpoint tests — the PR2b lesson)."""
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


def _order_payload(order_id=8100001, financial_status="paid", **overrides):
    payload = {
        "id": order_id,
        "order_number": 3001,
        "name": "#3001",
        "created_at": "2026-08-27T08:30:00Z",
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


def _negative_refund_payload(refund_id=8200001, order_id=8100001, amount="-5.00", **overrides):
    payload = {
        "id": refund_id,
        "order_id": order_id,
        "created_at": "2026-08-27T10:00:00Z",
        "note": "provider data corruption",
        "transactions": [{"kind": "refund", "status": "success", "amount": amount}],
        "refund_line_items": [],
    }
    payload.update(overrides)
    return payload


def _refund_payload(refund_id=8200001, order_id=8100001, amount="50.00", **overrides):
    return _negative_refund_payload(refund_id=refund_id, order_id=order_id, amount=amount, **overrides)


def _evidence(company):
    return ShopifyRejectedEvidence.objects.get(company=company)


def _last_sequence(company) -> int:
    counter = CompanyEventCounter.objects.filter(company=company).first()
    return counter.last_sequence if counter else 0


# =============================================================================
# H1/H3/H4/H5/H6/H7 — the negative aggregate becomes durable evidence with
# ZERO side effects, and the webhook acknowledges after durable persistence
# =============================================================================


def test_h1_negative_transaction_aggregate_creates_one_evidence(shopify_store, company, owner_membership):
    from accounting.models import JournalEntry

    seq_before = _last_sequence(company)
    resp = _post_webhook("refunds/create", _negative_refund_payload())
    assert resp.status_code == 200  # H7: acknowledged only after durable persistence

    row = _evidence(company)
    assert row.resource_kind == ShopifyRejectedEvidence.ResourceKind.REFUND
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY
    assert row.external_id == "8200001"
    assert row.parent_external_id == "8100001"
    assert any(
        e.get("field") == "refund_amount" and "non-negative" in e.get("message", "") for e in row.validation_errors
    )

    # H3/H4/H5/H6 — zero side effects beyond the evidence row:
    assert ShopifyRefund.objects.filter(company=company).count() == 0
    assert BusinessEvent.objects.filter(company=company).count() == 0
    assert _last_sequence(company) == seq_before
    assert JournalEntry.objects.filter(company=company).count() == 0


def test_h2_negative_refund_line_fallback_creates_evidence(shopify_store, company):
    payload = _negative_refund_payload(refund_id=8200002)
    payload["transactions"] = []
    payload["refund_line_items"] = [{"subtotal": "-4.00"}]
    resp = _post_webhook("refunds/create", payload)
    assert resp.status_code == 200

    row = _evidence(company)
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY
    assert any(e.get("field") == "refund_amount" for e in row.validation_errors)
    assert ShopifyRefund.objects.filter(company=company).count() == 0


# =============================================================================
# H8 — never acknowledge unstored evidence
# =============================================================================


def test_h8_evidence_write_failure_yields_retryable_503(shopify_store, company, monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(commands.rejected_evidence, "record_rejected_evidence", _boom)
    resp = _post_webhook("refunds/create", _negative_refund_payload(refund_id=8200003))
    assert resp.status_code == 503
    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 0


# =============================================================================
# H9/H10 — identical redelivery re-sights ONE row; no duplicate Notification;
# acknowledgment reopens per the merged contract
# =============================================================================


def test_h9_h10_identical_redelivery_resights_one_row(shopify_store, company, owner_membership, user):
    assert _post_webhook("refunds/create", _negative_refund_payload(refund_id=8200004), "d-1").status_code == 200
    row = _evidence(company)
    assert row.occurrence_count == 1
    assert Notification.objects.filter(company=company, source_module="shopify_connector").count() == 1

    # Operator acknowledges; the payload then arrives AGAIN still-negative —
    # the merged contract reopens (a stale ack must not hide a live defect).
    from django.utils import timezone

    ShopifyRejectedEvidence.objects.filter(pk=row.pk).update(
        acknowledged=True, acknowledged_at=timezone.now(), acknowledged_by=user, acknowledgment_note="seen"
    )
    assert _post_webhook("refunds/create", _negative_refund_payload(refund_id=8200004), "d-2").status_code == 200

    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 1
    row.refresh_from_db()
    assert row.occurrence_count == 2
    assert row.acknowledged is False  # reopened
    assert row.acknowledgment_note == "seen"  # note preserved as history
    assert row.last_delivery_id == "d-2"
    # H10: notification fires on FIRST sighting only.
    assert Notification.objects.filter(company=company, source_module="shopify_connector").count() == 1


def test_h11_changed_negative_payload_creates_distinct_evidence(shopify_store, company):
    assert _post_webhook("refunds/create", _negative_refund_payload(refund_id=8200005)).status_code == 200
    changed = _negative_refund_payload(refund_id=8200005, note="different corrupt delivery")
    assert _post_webhook("refunds/create", changed).status_code == 200

    rows = list(ShopifyRejectedEvidence.objects.filter(company=company))
    assert len(rows) == 2  # distinct payload hash => distinct evidence identity
    assert rows[0].payload_hash != rows[1].payload_hash
    assert {r.external_id for r in rows} == {"8200005"}


# =============================================================================
# H12/H13/H14 — corrected redelivery: canonical row exactly once, every open
# evidence row for the identity superseded, historical evidence retained
# =============================================================================


def test_h12_h14_corrected_positive_redelivery_processes_once_and_supersedes(shopify_store, company):
    assert commands.process_order_paid(shopify_store, _order_payload(order_id=8100010)).success

    # Two DISTINCT open negative-evidence rows for the same refund identity.
    assert (
        _post_webhook("refunds/create", _negative_refund_payload(refund_id=8200010, order_id=8100010)).status_code
        == 200
    )
    assert (
        _post_webhook(
            "refunds/create", _negative_refund_payload(refund_id=8200010, order_id=8100010, note="second variant")
        ).status_code
        == 200
    )
    assert ShopifyRejectedEvidence.objects.filter(company=company, superseded_at__isnull=True).count() == 2
    assert ShopifyRefund.objects.filter(company=company).count() == 0  # no dedup row to foreclose healing

    # Corrected (positive) redelivery under the SAME refund id.
    assert (
        _post_webhook(
            "refunds/create", _refund_payload(refund_id=8200010, order_id=8100010, amount="25.00")
        ).status_code
        == 200
    )
    refund = ShopifyRefund.objects.get(company=company, shopify_refund_id=8200010)
    assert refund.event_id is not None
    assert BusinessEvent.objects.filter(company=company, idempotency_key="shopify.refund.created:8200010").count() == 1

    # EVERY open evidence row for the identity is superseded; history retained.
    rows = list(ShopifyRejectedEvidence.objects.filter(company=company))
    assert len(rows) == 2
    for row in rows:
        assert row.superseded_at is not None
        assert row.superseded_by_refund_id == refund.pk
        assert row.superseded_target_public_id == refund.public_id
    superseded_at_values = {r.superseded_at for r in rows}

    # H14: a subsequent valid retry is idempotent — no duplicate refund, event,
    # journal or supersession side effect.
    assert (
        _post_webhook(
            "refunds/create", _refund_payload(refund_id=8200010, order_id=8100010, amount="25.00")
        ).status_code
        == 200
    )
    assert ShopifyRefund.objects.filter(company=company, shopify_refund_id=8200010).count() == 1
    assert BusinessEvent.objects.filter(company=company, idempotency_key="shopify.refund.created:8200010").count() == 1
    assert {r.superseded_at for r in ShopifyRejectedEvidence.objects.filter(company=company)} == superseded_at_values


def test_h13_corrected_zero_redelivery_handled_zero_supersedes(shopify_store, company):
    assert commands.process_order_paid(shopify_store, _order_payload(order_id=8100011)).success
    assert (
        _post_webhook("refunds/create", _negative_refund_payload(refund_id=8200011, order_id=8100011)).status_code
        == 200
    )
    evidence = _evidence(company)
    assert evidence.superseded_at is None

    zero = _negative_refund_payload(refund_id=8200011, order_id=8100011)
    zero["transactions"] = []
    zero["refund_line_items"] = []
    assert _post_webhook("refunds/create", zero).status_code == 200

    refund = ShopifyRefund.objects.get(company=company, shopify_refund_id=8200011)
    assert refund.status == ShopifyRefund.Status.PROCESSED  # the existing handled-zero canonical row
    assert refund.event_id is None
    evidence.refresh_from_db()
    assert evidence.superseded_at is not None
    assert evidence.superseded_by_refund_id == refund.pk


# =============================================================================
# H15/H16 — positive/zero regression unchanged; a negative COMPONENT inside a
# non-negative aggregate keeps the exact aggregate-based behavior
# =============================================================================


def test_h15_positive_refund_regression_unchanged(shopify_store, company):
    assert commands.process_order_paid(shopify_store, _order_payload(order_id=8100012)).success
    assert (
        _post_webhook(
            "refunds/create", _refund_payload(refund_id=8200012, order_id=8100012, amount="30.00")
        ).status_code
        == 200
    )
    refund = ShopifyRefund.objects.get(company=company, shopify_refund_id=8200012)
    assert str(refund.amount) == "30.00"
    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 0


def test_h16_negative_component_with_nonnegative_aggregate_processes(shopify_store, company):
    assert commands.process_order_paid(shopify_store, _order_payload(order_id=8100013)).success
    payload = _refund_payload(refund_id=8200013, order_id=8100013)
    payload["transactions"] = [
        {"kind": "refund", "status": "success", "amount": "100.00"},
        {"kind": "refund", "status": "success", "amount": "-30.00"},
    ]
    assert _post_webhook("refunds/create", payload).status_code == 200
    refund = ShopifyRefund.objects.get(company=company, shopify_refund_id=8200013)
    assert str(refund.amount) == "70.00"
    assert ShopifyRejectedEvidence.objects.filter(company=company).count() == 0


# =============================================================================
# H17/H18 — visibility rides the merged PR #130 mechanisms; financial readers
# stay clean
# =============================================================================


def test_h17_visible_in_exceptions_queue_and_health_alerts(
    shopify_store, company, owner_membership, authenticated_client, settings
):
    from ops.health import compute_alert_state

    settings.ALERT_UNRESOLVED_FAILURES_MAX = 0
    assert _post_webhook("refunds/create", _negative_refund_payload(refund_id=8200014)).status_code == 200

    resp = authenticated_client.get("/api/shopify/rejected-evidence/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 1
    assert body["results"][0]["rejection_code"] == "MALFORMED_MONEY"
    assert body["results"][0]["external_id"] == "8200014"

    state = compute_alert_state()
    assert state["open_rejected_evidence"] == 1
    assert state["rejected_evidence_by_source"].get("shopify") == 1
    assert state["status"] == "unhealthy"


def test_h18_financial_readers_contain_no_rejected_negative_refund(shopify_store, company, owner_membership):
    from shopify_connector.serializers import ShopifyOrderSerializer

    assert commands.process_order_paid(shopify_store, _order_payload(order_id=8100015)).success
    events_before = BusinessEvent.objects.filter(company=company).count()

    assert (
        _post_webhook("refunds/create", _negative_refund_payload(refund_id=8200015, order_id=8100015)).status_code
        == 200
    )

    order = ShopifyOrder.objects.get(company=company, shopify_order_id=8100015)
    data = ShopifyOrderSerializer(order).data
    assert not data["total_refunded"] or str(data["total_refunded"]) in ("0", "0.00", "None")
    assert ShopifyRefund.objects.filter(company=company).count() == 0
    assert BusinessEvent.objects.filter(company=company).count() == events_before


# =============================================================================
# H19 — profile-NONE vs constrained-pilot: the rejection happens at the pure
# pre-admission validator, so the outcome is identical under both profiles
# =============================================================================


def test_h19_pilot_profile_rejection_identical(shopify_store, company):
    from accounts.models import Company

    company.pilot_profile = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1
    company.save(update_fields=["pilot_profile"])

    assert _post_webhook("refunds/create", _negative_refund_payload(refund_id=8200016)).status_code == 200
    row = _evidence(company)
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY
    assert ShopifyRefund.objects.filter(company=company).count() == 0


# =============================================================================
# F — poller/catch-up path: same validator, same evidence writer, permanent
# (not a transient infrastructure failure), poller provenance recorded
# =============================================================================


def test_f_poller_direct_command_negative_is_permanent_with_poller_provenance(shopify_store, company):
    result = commands.process_refund(shopify_store, _negative_refund_payload(refund_id=8200017))
    assert not result.success
    assert result.data.get("rejected") is True
    assert result.data.get("retryable") is not True

    row = _evidence(company)
    assert row.ingress_kind == ShopifyRejectedEvidence.IngressKind.POLLER
    assert row.source_topic == "poller/refunds"
    assert row.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY
    assert ShopifyRefund.objects.filter(company=company).count() == 0


# =============================================================================
# Section I — architecture ratchet: one aggregate owner, one evidence boundary,
# no ERROR-refund writer, no second negative-refund mechanism
# =============================================================================


def test_ratchet_process_refund_never_writes_error_refunds():
    source = inspect.getsource(commands.process_refund)
    # No WRITER: the creation kwarg pattern must be gone. (The dedup guard's
    # READER comparison `!= ShopifyRefund.Status.ERROR` legitimately remains —
    # legacy/synthetic ERROR rows must never supersede evidence.)
    assert "status=ShopifyRefund.Status.ERROR" not in source, (
        "process_refund must not create ShopifyRefund(status=ERROR) again"
    )
    assert "error_message=" not in source, "the bespoke ERROR-row construction must stay gone"
    assert "notify_company_admins" not in source, "no bespoke negative-refund notification mechanism"
    assert "_reject_malformed_payload" in source, "permanent malformed refunds route through the ONE evidence boundary"


def test_ratchet_single_canonical_aggregate_owner():
    from shopify_connector import payload_validation

    command_source = inspect.getsource(commands.process_refund)
    assert "refund_aggregate_amount(" in command_source, "process_refund must use the shared canonical aggregate"
    assert 'kind == "refund"' not in command_source, "no local re-aggregation in the command path"

    validator_source = inspect.getsource(payload_validation.validate_refund_payload)
    assert "refund_aggregate_amount(" in validator_source, "the validator bounds the same canonical aggregate"

    # Negative classification is OWNED by the validator (behavior pin).
    verdict = payload_validation.validate_refund_payload(_negative_refund_payload())
    assert not verdict.ok
    assert verdict.rejection_code == payload_validation.MALFORMED_MONEY
    assert any(e.get("field") == "refund_amount" for e in verdict.errors)

    # Exact aggregate semantics (kind/status filter; exact-zero fallback).
    from decimal import Decimal

    assert payload_validation.refund_aggregate_amount(
        {"transactions": [{"kind": "refund", "status": "success", "amount": "7.50"}]}
    ) == Decimal("7.50")
    assert payload_validation.refund_aggregate_amount(
        {"transactions": [{"kind": "refund", "status": "failure", "amount": "7.50"}]}
    ) == Decimal("0")
    assert payload_validation.refund_aggregate_amount(
        {"transactions": [], "refund_line_items": [{"subtotal": "3.25"}]}
    ) == Decimal("3.25")
    assert payload_validation.refund_aggregate_amount(
        {
            "transactions": [{"kind": "refund", "status": "success", "amount": "1.00"}],
            "refund_line_items": [{"subtotal": "3.25"}],
        }
    ) == Decimal("1.00")  # fallback ONLY on an exactly-zero transaction total
