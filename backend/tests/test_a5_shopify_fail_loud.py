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
- K#2: a transient `process_order_paid` failure is marked retryable so the
  webhook view answers 503 (Shopify redelivers) instead of a silent 200.
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


# =============================================================================
# K#1 handled-zero markers — durable trace, never a silent consume, no false alert
# =============================================================================


def test_order_zero_total_marks_processed_no_journal(company, owner_membership):
    """A zero-total order books no JE, but must not be silently consumed: the
    source row is marked PROCESSED (the handled-zero marker) with NO journal and
    NO failure log (so a benign zero never trips /_health/alerts)."""
    from sales.models import SalesInvoice

    store = _shopify_setup(company)
    _make_order_row(company, store, order_id="9900020", total="0")
    event = _emit_order(company, order_id="9900020", amount="0", store=store)

    ShopifyAccountingHandler().handle(event)

    order = ShopifyOrder.objects.get(company=company, shopify_order_id=9900020)
    assert order.status == ShopifyOrder.Status.PROCESSED
    assert not SalesInvoice.objects.filter(company=company, source="shopify", source_document_id="9900020").exists()
    assert not ProjectionFailureLog.objects.filter(company=company, event=event).exists()


def test_refund_zero_amount_marks_processed_no_credit_note(company, owner_membership):
    """A zero-value refund books no credit note, but the source row is marked
    PROCESSED (handled-zero marker) rather than silently consumed."""
    from sales.models import SalesCreditNote

    store = _shopify_setup(company)
    order = _make_order_row(company, store, order_id="9900030", total="100.00")
    ShopifyRefund.objects.create(
        company=company,
        order=order,
        shopify_refund_id=9900031,
        amount=Decimal("0"),
        currency="EGP",
        shopify_created_at="2026-05-04T00:00:00Z",
    )
    event = _emit_refund(company, refund_id="9900031", order_id="9900030", amount="0")

    ShopifyAccountingHandler().handle(event)

    refund = ShopifyRefund.objects.get(company=company, shopify_refund_id=9900031)
    assert refund.status == ShopifyRefund.Status.PROCESSED
    assert not SalesCreditNote.objects.filter(company=company, source="shopify", source_document_id="9900031").exists()
    assert not ProjectionFailureLog.objects.filter(company=company, event=event).exists()


# =============================================================================
# K#2 — webhook ingress: transient order-paid failures are retryable (→ 503)
# =============================================================================


def test_process_order_paid_marks_transient_failure_retryable(company, owner_membership):
    """A transient failure inside process_order_paid must return
    retryable=True so the webhook view answers 503 and Shopify redelivers,
    instead of a silent 200 that loses the order until the 48h poller."""
    import shopify_connector.commands as cmd

    store = _shopify_setup(company)

    def _boom(*args, **kwargs):
        raise RuntimeError("transient DB blip")

    with patch.object(cmd, "_prepare_order_item_metadata", side_effect=_boom):
        result = cmd.process_order_paid(store, {"id": 9900040, "currency": "EGP"})

    assert not result.success
    assert result.data and result.data.get("retryable") is True
