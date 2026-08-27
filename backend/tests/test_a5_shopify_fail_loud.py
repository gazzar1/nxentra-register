# tests/test_a5_shopify_fail_loud.py
"""A5-PR2 — Shopify order + refund fail-loud closure.

Pre-A5 the refund handler (`_handle_refund_created`) carried three silent
`logger.* + return` branches that the order path had already fixed under A80:

- missing SALES_REVENUE mapping,
- an aged-orphan original invoice (past the A41 defer window),
- a credit-note command failure.

Each consumed the refund event (applied marker written), booked no journal,
wrote no `ProjectionFailureLog`, and raised no alert — a refund silently lost
with no operator signal, unrecoverable under the pilot (rebuild is blocked).

This locks in:
- the three branches now raise (fail-loud → durable, operator-visible outcome);
- K#1: a legitimately zero-value order/refund leaves a durable handled-zero
  marker (source row → PROCESSED, no JE) instead of a silent consume;
- negative refunds get a durable, operator-visible REJECTED outcome at ingress
  (A5-PR2c: as ShopifyRejectedEvidence — health-paged, queue-listed, healable
  by a corrected redelivery — replacing the invisible ShopifyRefund ERROR row;
  the full lifecycle battery lives in test_a5_pr2c_negative_refund_evidence).

(The webhook-retryability / malformed-order-payload durable-rejection work is
deferred to A5-PR2b by founder decision — see NEXT_TASKS.)
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from projections.exceptions import (
    ProjectionCommandFailedError,
    ProjectionStateError,
)
from projections.models import ProjectionFailureLog
from shopify_connector.models import ShopifyOrder, ShopifyRefund, ShopifyStore
from shopify_connector.projections import ROLE_SALES_REVENUE, ShopifyAccountingHandler

pytestmark = pytest.mark.django_db


def _shopify_setup(company, *, shop="a5-fail-loud.myshopify.com"):
    """Full shopify accounting setup (mapping + store + sales routing)."""
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain=shop,
        access_token="t",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    return store


def _emit_refund(company, *, refund_id, order_id, amount, currency="EGP"):
    from events.emitter import emit_event_no_actor
    from events.types import EventTypes
    from shopify_connector.event_types import ShopifyRefundCreatedData

    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.SHOPIFY_REFUND_CREATED,
        aggregate_type="ShopifyRefund",
        aggregate_id=str(refund_id),
        idempotency_key=f"shopify.refund_created:{refund_id}",
        data=ShopifyRefundCreatedData(
            shopify_order_id=str(order_id),
            shopify_refund_id=str(refund_id),
            order_number=str(order_id),
            transaction_date="2026-05-04",
            currency=currency,
            amount=amount,
            reason="test",
        ),
    )


def _emit_order(company, *, order_id, amount, store, currency="EGP"):
    from events.emitter import emit_event_no_actor
    from events.types import EventTypes
    from shopify_connector.event_types import ShopifyOrderPaidData

    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.SHOPIFY_ORDER_PAID,
        aggregate_type="ShopifyOrder",
        aggregate_id=str(order_id),
        idempotency_key=f"shopify.order_paid:{order_id}",
        data=ShopifyOrderPaidData(
            shopify_order_id=str(order_id),
            order_number=str(order_id),
            order_name=f"#{order_id}",
            transaction_date="2026-05-04",
            currency=currency,
            amount=amount,
            subtotal=amount,
            total_tax="0",
            total_shipping="0",
            gateway="Paymob",
            store_public_id=str(store.public_id),
        ),
    )


def _make_order_row(company, store, *, order_id, total="0"):
    return ShopifyOrder.objects.create(
        company=company,
        store=store,
        shopify_order_id=int(order_id),
        shopify_order_number=str(order_id),
        shopify_order_name=f"#{order_id}",
        total_price=Decimal(total),
        subtotal_price=Decimal(total),
        currency="EGP",
        gateway="Paymob",
        order_date=date(2026, 5, 4),
        shopify_created_at="2026-05-04T00:00:00Z",
    )


# =============================================================================
# Refund fail-loud: the three pre-A5 silent branches now raise
# =============================================================================


def test_refund_missing_revenue_mapping_raises_state_error(company, owner_membership):
    """Missing SALES_REVENUE → ProjectionStateError (mirrors the order path),
    so the refund surfaces in /finance/exceptions and self-heals once wired —
    instead of being consumed with no journal. A role-less mapping exercises the
    per-role guard directly (handle() guards a wholly-absent mapping separately)."""
    _shopify_setup(company)
    event = _emit_refund(company, refund_id="9900001", order_id="9900000", amount="50.00")
    handler = ShopifyAccountingHandler()
    with pytest.raises(ProjectionStateError):
        handler._handle_refund_created(event, event.get_data(), {})


def test_refund_credit_note_failure_raises_command_failed(company, owner_membership):
    """A downstream credit-note refusal (open period) → ProjectionCommandFailedError
    (mirrors the order path), so the event stays unprocessed and retries — not a
    silent consume."""
    from accounting.models import Account
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from sales.commands import create_and_post_invoice_for_platform
    from sales.models import Customer, PostingProfile

    store = _shopify_setup(company)
    order = _make_order_row(company, store, order_id="9900010", total="100.00")

    with projection_writes_allowed():
        ar_control = Account.objects.projection().create(
            company=company,
            code="11490",
            name="A5 AR Control",
            account_type=Account.AccountType.ASSET,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company,
            code="41090",
            name="A5 Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    with command_writes_allowed():
        customer = Customer.objects.create(company=company, code="A5-CUST", name="A5 Cust")
        profile = PostingProfile.objects.create(
            company=company,
            code="A5-PROFILE",
            name="A5 Profile",
            profile_type=PostingProfile.ProfileType.CUSTOMER,
            control_account=ar_control,
        )
    inv = create_and_post_invoice_for_platform(
        company=company,
        customer_id=customer.id,
        posting_profile_id=profile.id,
        lines=[
            {
                "account_id": revenue.id,
                "description": "o",
                "quantity": "1",
                "unit_price": "100.00",
                "discount_amount": "0",
            }
        ],
        invoice_date=date(2026, 4, 30),
        source="shopify",
        source_document_id="9900010",
    )
    assert inv.success, f"invoice setup failed: {inv.error!r}"

    ShopifyRefund.objects.create(
        company=company,
        order=order,
        shopify_refund_id=9900011,
        amount=Decimal("30.00"),
        currency="EGP",
        shopify_created_at="2026-05-04T00:00:00Z",
    )
    event = _emit_refund(company, refund_id="9900011", order_id="9900010", amount="30.00")
    handler = ShopifyAccountingHandler()
    mapping = {ROLE_SALES_REVENUE: revenue}

    with patch(
        "sales.commands.create_and_post_credit_note_for_platform",
        return_value=SimpleNamespace(success=False, error="downstream boom", data={}),
    ):
        with pytest.raises(ProjectionCommandFailedError):
            handler._handle_refund_created(event, event.get_data(), mapping)


def test_refund_aged_orphan_with_pending_order_stays_retryable(company, owner_membership):
    """Codex round-1 P1 (refined round-3): an aged refund whose order's order_paid
    event is still PENDING (received, not yet applied — invoice not posted) stays
    RETRYABLE (DeferEvent), NOT consumed — a later-posting invoice would otherwise
    leave it permanently unbooked (dedup blocks re-ingest; rebuild is pilot-blocked)."""
    from datetime import timedelta

    from django.utils import timezone

    from events.models import BusinessEvent
    from projections.base import DeferEvent

    store = _shopify_setup(company)
    order_event = _emit_order(company, order_id="9900045", amount="200.00", store=store)  # received, NOT applied
    order = _make_order_row(company, store, order_id="9900045", total="200.00")
    order.event_id = order_event.id
    order.save(update_fields=["event_id"])
    event = _emit_refund(company, refund_id="9900046", order_id="9900045", amount="50.00")
    # Age past 24h so only the pending-order discriminator (not age) keeps it retryable.
    BusinessEvent.objects.filter(pk=event.pk).update(recorded_at=timezone.now() - timedelta(hours=25))
    event.refresh_from_db()

    import shopify_connector.projections as proj_module

    handler = ShopifyAccountingHandler()
    original = proj_module._INVOICE_LOOKUP_DELAY_SECONDS
    proj_module._INVOICE_LOOKUP_DELAY_SECONDS = 0.001
    try:
        with pytest.raises(DeferEvent) as exc:
            handler.handle(event)
        assert "order event pending" in str(exc.value)
    finally:
        proj_module._INVOICE_LOOKUP_DELAY_SECONDS = original


def test_refund_orphaned_by_terminally_applied_order_quarantines(company, owner_membership):
    """Codex round-3 P1: a refund whose order was TERMINALLY APPLIED without an
    invoice (e.g. a closed-period ProjectionTerminalSkip on the order) must NOT
    defer forever — the invoice can never post (rebuild is pilot-blocked), so the
    refund is a dead-end and gets a VISIBLE terminal quarantine."""
    from projections.exceptions import ProjectionTerminalSkip
    from projections.models import ProjectionAppliedEvent
    from projections.write_barrier import projection_writes_allowed

    store = _shopify_setup(company)
    order_event = _emit_order(company, order_id="9900070", amount="100.00", store=store)
    order = _make_order_row(company, store, order_id="9900070", total="100.00")
    order.event_id = order_event.id
    order.save(update_fields=["event_id"])
    # The order's order_paid event APPLIED (consumed) with no invoice — the
    # closed-period TerminalSkip shape.
    with projection_writes_allowed():
        ProjectionAppliedEvent.objects.create(
            company=company, projection_name=ShopifyAccountingHandler().name, event=order_event
        )

    refund_event = _emit_refund(company, refund_id="9900071", order_id="9900070", amount="40.00")
    import shopify_connector.projections as proj_module

    handler = ShopifyAccountingHandler()
    original = proj_module._INVOICE_LOOKUP_DELAY_SECONDS
    proj_module._INVOICE_LOOKUP_DELAY_SECONDS = 0.001
    try:
        with pytest.raises(ProjectionTerminalSkip):
            handler.handle(refund_event)
    finally:
        proj_module._INVOICE_LOOKUP_DELAY_SECONDS = original


# =============================================================================
# K#1 handled-zero markers — durable trace, never a silent consume, no false alert
# =============================================================================


def test_order_zero_total_marks_processed_no_journal(company, owner_membership):
    """A zero-total order books no JE, but must not be silently consumed: the
    source row is marked PROCESSED (the handled-zero marker) with NO journal and
    NO failure log (so a benign zero never trips /_health/alerts). Codex round-7 P2:
    it must be handled even with NO SALES_REVENUE mapping — classification precedes
    the revenue-mapping guard, so a zero order never falsely stalls the stream."""
    from accounting.mappings import ModuleAccountMapping
    from sales.models import SalesInvoice

    store = _shopify_setup(company)
    # Remove the ENTIRE Shopify mapping to prove the zero order is classified before
    # BOTH the empty-mapping guard (handle(), Codex round-8) and the revenue guard
    # (_handle_order_paid, round-7) — a zero order needs no mapping at all.
    ModuleAccountMapping.objects.filter(company=company, module="shopify_connector").delete()
    _make_order_row(company, store, order_id="9900020", total="0")
    event = _emit_order(company, order_id="9900020", amount="0", store=store)

    ShopifyAccountingHandler().handle(event)

    order = ShopifyOrder.objects.get(company=company, shopify_order_id=9900020)
    assert order.status == ShopifyOrder.Status.PROCESSED
    assert not SalesInvoice.objects.filter(company=company, source="shopify", source_document_id="9900020").exists()
    assert not ProjectionFailureLog.objects.filter(company=company, event=event).exists()


def test_order_negative_total_raises_invalid_data(company, owner_membership):
    """Codex P2b: a NEGATIVE order total is invalid provider data — the handler
    must raise ProjectionInvalidDataError, not mark it PROCESSED like a benign
    zero (which would hide invalid evidence as a handled order)."""
    from projections.exceptions import ProjectionInvalidDataError

    store = _shopify_setup(company)
    event = _emit_order(company, order_id="9900050", amount="-10.00", store=store)
    with pytest.raises(ProjectionInvalidDataError):
        ShopifyAccountingHandler().handle(event)


def test_process_refund_zero_amount_persists_handled_zero_marker(company, owner_membership):
    """K#1 / Codex P2a: the canonical ingress (process_refund) persists the durable
    handled-zero marker for a zero-value refund — a PROCESSED ShopifyRefund row
    with NO accounting event — instead of a bare fail the webhook acks and loses.
    (Pre-fix the row/event were never created, so the projection marker was
    unreachable for a real refund.)"""
    from events.models import BusinessEvent
    from shopify_connector import commands as cmd

    store = _shopify_setup(company)
    _make_order_row(company, store, order_id="9900030", total="100.00")

    result = cmd.process_refund(
        store,
        {
            "id": 9900031,
            "order_id": 9900030,
            "created_at": "2026-05-04T00:00:00Z",
            "transactions": [],
            "refund_line_items": [],
        },
    )
    assert result.success
    assert result.data.get("handled_zero") is True

    refund = ShopifyRefund.objects.get(company=company, shopify_refund_id=9900031)
    assert refund.status == ShopifyRefund.Status.PROCESSED
    assert refund.event_id is None, "a zero refund must emit no accounting event"
    assert not BusinessEvent.objects.filter(company=company, idempotency_key="shopify.refund.created:9900031").exists()


# =============================================================================
# A5-PR2c — negative refunds reroute to durable ShopifyRejectedEvidence
# =============================================================================


def test_process_refund_negative_amount_persists_rejected_evidence(company, owner_membership):
    """A5-PR2c (supersedes the Codex round-2/round-4 ERROR-row pins): a NEGATIVE
    aggregate refund is invalid provider data classified by the up-front
    validator as MALFORMED_MONEY — durable ShopifyRejectedEvidence through the
    same boundary as every other malformed payload. NO ShopifyRefund row (the
    old ERROR row was invisible to /finance/exceptions and /_health/alerts, and
    its refund-id dedup foreclosed the corrected redelivery), no event, and the
    evidence writer's own first-sighting notification (no bespoke one)."""
    from accounts.models import Notification
    from events.models import BusinessEvent
    from shopify_connector import commands as cmd
    from shopify_connector.models import ShopifyRejectedEvidence

    store = _shopify_setup(company)
    _make_order_row(company, store, order_id="9900060", total="100.00")

    result = cmd.process_refund(
        store,
        {
            "id": 9900061,
            "order_id": 9900060,
            "created_at": "2026-05-04T00:00:00Z",
            "transactions": [{"kind": "refund", "status": "success", "amount": "-5.00"}],
            "refund_line_items": [],
        },
    )
    assert not result.success
    assert result.data.get("rejected") is True
    assert result.data.get("retryable") is not True  # permanent — webhook acks 200

    evidence = ShopifyRejectedEvidence.objects.get(company=company)
    assert evidence.resource_kind == ShopifyRejectedEvidence.ResourceKind.REFUND
    assert evidence.rejection_code == ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY
    assert evidence.external_id == "9900061"
    assert any(e.get("field") == "refund_amount" for e in evidence.validation_errors)

    assert not ShopifyRefund.objects.filter(company=company, shopify_refund_id=9900061).exists()
    assert not BusinessEvent.objects.filter(company=company, idempotency_key="shopify.refund.created:9900061").exists()

    # The evidence writer's first-sighting notification is the ONE delivery
    # mechanism — no bespoke negative-refund notification exists any more.
    notes = Notification.objects.filter(company=company, source_module="shopify_connector")
    assert notes.count() == 1
    assert "malformed payload" in notes.get().title


def test_error_refund_excluded_from_total_refunded(company, owner_membership):
    """Codex round-3 P2, retained as the LEGACY defense: production no longer
    writes ShopifyRefund(status=ERROR) (A5-PR2c reroutes negatives to rejected
    evidence), but historical/synthetic ERROR rows are not rewritten — every
    financial reader must keep excluding them from totals."""
    from shopify_connector.serializers import ShopifyOrderSerializer

    store = _shopify_setup(company)
    order = _make_order_row(company, store, order_id="9900080", total="100.00")
    ShopifyRefund.objects.create(
        company=company,
        order=order,
        shopify_refund_id=9900081,
        amount=Decimal("10.00"),
        currency="EGP",
        shopify_created_at="2026-05-04T00:00:00Z",
        status=ShopifyRefund.Status.PROCESSED,
    )
    ShopifyRefund.objects.create(
        company=company,
        order=order,
        shopify_refund_id=9900082,
        amount=Decimal("-5.00"),
        currency="EGP",
        shopify_created_at="2026-05-04T00:00:00Z",
        status=ShopifyRefund.Status.ERROR,
    )

    # Un-annotated instance exercises the serializer's direct-sum fallback path.
    fresh = ShopifyOrder.objects.get(pk=order.pk)
    data = ShopifyOrderSerializer(fresh).data
    # Compare numerically — the fallback path str()s the raw Sum (no fixed scale).
    # The point is the ERROR -5 is excluded (10, not 5).
    assert Decimal(data["total_refunded"]) == Decimal("10.00"), "ERROR refund must be excluded from total_refunded"
