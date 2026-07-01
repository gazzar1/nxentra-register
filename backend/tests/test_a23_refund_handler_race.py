# tests/test_a23_refund_handler_race.py
"""
A23 — Refund handler projection race.

When a Shopify ShopifyOrderPaidData event and its corresponding
ShopifyRefundCreatedData event land in the same projection pass, the
refund handler's `SalesInvoice.objects.filter(... status=POSTED)`
lookup can fail to see the order_paid handler's commit due to
transaction-isolation lag. Pre-A23, the refund handler silently
early-returned and `ProjectionAppliedEvent` recorded the event as
"processed" — permanent data loss with no merchant signal.

A23 fix: bounded retry on the SalesInvoice lookup. If the order_paid
handler's commit lands during the retry window, the refund handler
self-heals. Idempotency on credit-note creation
(SalesCreditNote.objects.filter(source, source_document_id))
already exists; A23 adds a test confirming it.

Tests use a plain (non-dimensioned) AR control account to avoid
pulling in the Shopify settlement-provider dimension validation —
the focus is on the retry helper's contract, not the full Shopify
JE shape (covered by other tests).
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from sales.models import SalesCreditNote, SalesInvoice
from shopify_connector.projections import (
    _INVOICE_LOOKUP_MAX_ATTEMPTS,
    _find_posted_shopify_invoice,
)


def _setup_simple_invoice_chain(company):
    """Customer + PostingProfile + revenue account — minimum scaffolding
    for create_and_post_invoice_for_platform to succeed without pulling
    in the Shopify dimension chain."""
    from accounting.models import Account
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from sales.models import Customer, PostingProfile

    with projection_writes_allowed():
        ar_control = Account.objects.projection().create(
            company=company,
            code="11401",
            name="A23 Test AR Control",
            account_type=Account.AccountType.ASSET,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company,
            code="41001",
            name="A23 Test Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    with command_writes_allowed():
        customer = Customer.objects.create(
            company=company,
            code="A23-CUSTOMER",
            name="A23 Test Customer",
        )
        posting_profile = PostingProfile.objects.create(
            company=company,
            code="A23-PROFILE",
            name="A23 Test Profile",
            profile_type=PostingProfile.ProfileType.CUSTOMER,
            control_account=ar_control,
        )
    return customer, posting_profile, revenue


def _make_posted_invoice(company, source_document_id, *, amount="100.00"):
    """Create + post a SalesInvoice tagged source='shopify'. Returns
    the CommandResult."""
    from sales.commands import create_and_post_invoice_for_platform

    customer, posting_profile, revenue = _setup_simple_invoice_chain(company)
    return create_and_post_invoice_for_platform(
        company=company,
        customer_id=customer.id,
        posting_profile_id=posting_profile.id,
        lines=[
            {
                "account_id": revenue.id,
                "description": "Test order revenue",
                "quantity": "1",
                "unit_price": amount,
                "discount_amount": "0",
            }
        ],
        invoice_date=date(2026, 4, 30),
        source="shopify",
        source_document_id=source_document_id,
    )


# =============================================================================
# Retry helper unit tests
# =============================================================================


def test_find_invoice_returns_none_when_no_invoice(db, company):
    """Helper returns None after retries when no invoice exists."""
    invoice = _find_posted_shopify_invoice(
        company,
        "ORDER-DOES-NOT-EXIST",
        max_attempts=2,
        delay=0.001,
    )
    assert invoice is None


def test_find_invoice_returns_existing_immediately(db, company, owner_membership):
    """When the invoice already exists POSTED, the helper returns on
    first attempt."""
    res = _make_posted_invoice(company, "SHOP-RACE-1")
    assert res.success, f"setup failed: {res.error!r}"

    invoice = _find_posted_shopify_invoice(
        company,
        "SHOP-RACE-1",
        max_attempts=3,
        delay=0.001,
    )
    assert invoice is not None
    assert invoice.source_document_id == "SHOP-RACE-1"
    assert invoice.status == SalesInvoice.Status.POSTED


def test_find_invoice_self_heals_on_concurrent_commit(db, company, owner_membership):
    """Simulate the race: SalesInvoice.filter returns empty on the
    first call, then the real invoice on the second. The retry helper
    picks it up on the second attempt rather than giving up."""
    res = _make_posted_invoice(company, "SHOP-RACE-2")
    assert res.success, f"setup failed: {res.error!r}"

    real_filter = SalesInvoice.objects.filter
    call_count = {"n": 0}

    class _EmptyQS:
        def first(self):
            return None

    def fake_filter(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _EmptyQS()
        return real_filter(*args, **kwargs)

    with patch.object(SalesInvoice.objects, "filter", side_effect=fake_filter):
        invoice = _find_posted_shopify_invoice(
            company,
            "SHOP-RACE-2",
            max_attempts=3,
            delay=0.001,
        )

    assert invoice is not None
    assert invoice.source_document_id == "SHOP-RACE-2"
    assert call_count["n"] >= 2


def test_invoice_lookup_max_attempts_constant_is_at_least_3():
    """Sanity: the production retry budget should be >= 3 so a single
    transaction-visibility lag doesn't cause permanent data loss."""
    assert _INVOICE_LOOKUP_MAX_ATTEMPTS >= 3


def test_find_invoice_skips_unposted_invoices(db, company, owner_membership):
    """The retry helper requires status=POSTED — a DRAFT invoice for the
    same order_id must NOT match (otherwise the refund handler would
    credit-note an unposted invoice and post into a hole in the GL)."""
    from accounts.authz import system_actor_for_company
    from sales.commands import create_sales_invoice

    customer, posting_profile, revenue = _setup_simple_invoice_chain(company)
    actor = system_actor_for_company(company)
    create_res = create_sales_invoice(
        actor=actor,
        customer_id=customer.id,
        posting_profile_id=posting_profile.id,
        lines=[
            {
                "account_id": revenue.id,
                "description": "Draft only",
                "quantity": "1",
                "unit_price": "100.00",
                "discount_amount": "0",
            }
        ],
        invoice_date=date(2026, 4, 30),
        source="shopify",
        source_document_id="SHOP-RACE-DRAFT",
    )
    assert create_res.success
    assert create_res.data["invoice"].status != SalesInvoice.Status.POSTED

    invoice = _find_posted_shopify_invoice(
        company,
        "SHOP-RACE-DRAFT",
        max_attempts=2,
        delay=0.001,
    )
    assert invoice is None


# =============================================================================
# Credit-note idempotency (existing behavior — A23 just confirms it)
# =============================================================================


# =============================================================================
# A41 — DeferEvent semantics: refund handler defers when order_paid hasn't
#       arrived yet, projection re-attempts on next pass
# =============================================================================


def test_a41_refund_handler_defers_when_invoice_missing_and_event_fresh(db, company, owner_membership):
    """A41: when the SalesInvoice POSTED lookup retries exhaust AND the
    refund event is < 24h old, the handler must raise DeferEvent so the
    projection rewinds the bookmark and re-attempts on the next pass —
    instead of silently dropping the refund (pre-A41 behavior).
    """
    from accounts.commands import _setup_shopify_accounts
    from events.emitter import emit_event_no_actor
    from events.types import EventTypes
    from projections.base import DeferEvent
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.event_types import ShopifyRefundCreatedData
    from shopify_connector.models import ShopifyStore
    from shopify_connector.projections import ShopifyAccountingHandler

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a41-test.myshopify.com",
        access_token="t",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)

    # Emit a refund event for an order that doesn't exist yet (no
    # order_paid emitted in this test). With A23 retry helper exhausting
    # in 500ms and event age ~0 seconds, the handler should DEFER.
    event = emit_event_no_actor(
        company=company,
        event_type=EventTypes.SHOPIFY_REFUND_CREATED,
        aggregate_type="ShopifyRefund",
        aggregate_id="A41-REFUND-1",
        idempotency_key="shopify.refund_created:A41-REFUND-1",
        data=ShopifyRefundCreatedData(
            shopify_order_id="999991",
            shopify_refund_id="A41-REFUND-1",
            order_number="A41-1",
            transaction_date="2026-05-04",
            currency="EGP",
            amount="50.00",
            reason="customer_changed_mind",
        ),
    )

    handler = ShopifyAccountingHandler()
    # Speed up the per-event retry to make this test fast.
    import shopify_connector.projections as proj_module

    original = proj_module._INVOICE_LOOKUP_DELAY_SECONDS
    proj_module._INVOICE_LOOKUP_DELAY_SECONDS = 0.001
    try:
        with pytest.raises(DeferEvent) as exc:
            handler.handle(event)
    finally:
        proj_module._INVOICE_LOOKUP_DELAY_SECONDS = original

    assert "999991" in str(exc.value)


def test_a41_old_orphan_refund_logs_warning_does_not_defer(db, company, owner_membership):
    """A41: refund events older than 24h with no matching invoice are
    treated as truly orphan — log a warning and accept (pre-A41 silent-
    return behavior). Otherwise we'd loop forever on an order that
    really doesn't exist (e.g. predates the module-routing refactor).
    """
    from datetime import timedelta

    from django.utils import timezone

    from accounts.commands import _setup_shopify_accounts
    from events.emitter import emit_event_no_actor
    from events.models import BusinessEvent
    from events.types import EventTypes
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.event_types import ShopifyRefundCreatedData
    from shopify_connector.models import ShopifyStore
    from shopify_connector.projections import ShopifyAccountingHandler

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a41-test.myshopify.com",
        access_token="t",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)

    event = emit_event_no_actor(
        company=company,
        event_type=EventTypes.SHOPIFY_REFUND_CREATED,
        aggregate_type="ShopifyRefund",
        aggregate_id="A41-REFUND-OLD",
        idempotency_key="shopify.refund_created:A41-REFUND-OLD",
        data=ShopifyRefundCreatedData(
            shopify_order_id="999992",
            shopify_refund_id="A41-REFUND-OLD",
            order_number="A41-OLD",
            transaction_date="2026-05-04",
            currency="EGP",
            amount="100.00",
            reason="customer_changed_mind",
        ),
    )

    # Backdate the event to 25h ago so the 24h freshness check fails.
    BusinessEvent.objects.filter(pk=event.pk).update(
        recorded_at=timezone.now() - timedelta(hours=25),
    )
    event.refresh_from_db()

    handler = ShopifyAccountingHandler()
    import shopify_connector.projections as proj_module

    original = proj_module._INVOICE_LOOKUP_DELAY_SECONDS
    proj_module._INVOICE_LOOKUP_DELAY_SECONDS = 0.001
    try:
        # Should NOT raise — old orphan returns silently per legacy.
        handler.handle(event)
    finally:
        proj_module._INVOICE_LOOKUP_DELAY_SECONDS = original


def test_a41_process_pending_rewinds_bookmark_on_defer(db, company, owner_membership):
    """End-to-end A41: process_pending catches DeferEvent, doesn't mark
    the event processed, and rewinds the bookmark so the next pass
    re-attempts. After the order_paid event arrives + processes, the
    refund succeeds on the second pass."""
    from accounts.commands import _setup_shopify_accounts
    from events.emitter import emit_event_no_actor
    from events.models import EventBookmark
    from events.types import EventTypes
    from sales.models import SalesCreditNote
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.event_types import (
        ShopifyOrderPaidData,
        ShopifyRefundCreatedData,
    )
    from shopify_connector.models import ShopifyOrder, ShopifyStore
    from shopify_connector.projections import ShopifyAccountingHandler

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a41-e2e.myshopify.com",
        access_token="t",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)

    # This EGP order rides on the USD-functional `company` fixture, so its
    # invoice/credit-note lines are FOREIGN. post_journal_entry no longer posts a
    # foreign line at a silent 1:1 (it quarantines when no rate is on file), so
    # seed a 1.0 EGP→USD rate — this test is about bookmark rewind, not FX, and a
    # 1.0 rate leaves its amounts unchanged.
    from accounting.models import ExchangeRate

    ExchangeRate.objects.create(
        company=company,
        from_currency="EGP",
        to_currency="USD",
        rate=Decimal("1.0"),
        effective_date=date(2026, 5, 1),
        rate_type="SPOT",
    )

    ShopifyOrder.objects.create(
        company=company,
        store=store,
        shopify_order_id=4141,
        shopify_order_number="4141",
        shopify_order_name="#4141",
        total_price=Decimal("200.00"),
        subtotal_price=Decimal("200.00"),
        currency="EGP",
        gateway="Paymob",
        order_date=date(2026, 5, 4),
        shopify_created_at="2026-05-04T00:00:00Z",
    )

    # Emit refund FIRST — simulates Shopify webhook re-ordering or seed
    # bug. Refund handler will defer because no SalesInvoice exists yet.
    emit_event_no_actor(
        company=company,
        event_type=EventTypes.SHOPIFY_REFUND_CREATED,
        aggregate_type="ShopifyRefund",
        aggregate_id="999941",
        idempotency_key="shopify.refund_created:999941",
        data=ShopifyRefundCreatedData(
            shopify_order_id="4141",
            shopify_refund_id="999941",
            order_number="4141",
            transaction_date="2026-05-04",
            currency="EGP",
            amount="50.00",
            reason="partial",
        ),
    )

    handler = ShopifyAccountingHandler()
    import shopify_connector.projections as proj_module

    original = proj_module._INVOICE_LOOKUP_DELAY_SECONDS
    proj_module._INVOICE_LOOKUP_DELAY_SECONDS = 0.001
    try:
        # Pass 1: only the refund is in the queue. Handler defers.
        # process_pending catches it and rewinds the bookmark.
        handler.process_pending(company)
    finally:
        proj_module._INVOICE_LOOKUP_DELAY_SECONDS = original

    # No credit note yet — refund deferred, not silently dropped.
    assert SalesCreditNote.objects.filter(company=company, source="shopify").count() == 0

    # The bookmark wasn't advanced past the deferred refund event.
    bookmark = EventBookmark.objects.get(consumer_name=handler.name, company=company)
    if bookmark.last_event:
        # last_event must be strictly BEFORE the refund event's sequence
        # so the next pass picks it up.
        from events.models import BusinessEvent

        refund = BusinessEvent.objects.get(idempotency_key="shopify.refund_created:A41-E2E-REFUND")
        assert bookmark.last_event.company_sequence < refund.company_sequence

    # Now emit the order_paid event (the precondition the refund was
    # waiting on).
    emit_event_no_actor(
        company=company,
        event_type=EventTypes.SHOPIFY_ORDER_PAID,
        aggregate_type="ShopifyOrder",
        aggregate_id="A41-E2E-ORDER",
        idempotency_key="shopify.order_paid:A41-E2E-ORDER",
        data=ShopifyOrderPaidData(
            shopify_order_id="4141",
            order_number="4141",
            order_name="#4141",
            transaction_date="2026-05-04",
            currency="EGP",
            amount="200.00",
            subtotal="200.00",
            total_tax="0",
            total_shipping="0",
            gateway="Paymob",
            store_public_id=str(store.public_id),
        ),
    )

    proj_module._INVOICE_LOOKUP_DELAY_SECONDS = 0.001
    try:
        # Pass 2 onwards: each call simulates a Celery beat tick. Refund
        # may defer once or twice more; eventually the SalesInvoice is
        # POSTED and the refund handler succeeds.
        for _ in range(5):
            handler.process_pending(company)
            from sales.models import SalesCreditNote as _SCN

            if _SCN.objects.filter(company=company, source="shopify").exists():
                break
    finally:
        proj_module._INVOICE_LOOKUP_DELAY_SECONDS = original

    # Credit note should now exist.
    credit_notes = SalesCreditNote.objects.filter(company=company, source="shopify")
    assert credit_notes.count() == 1
    assert credit_notes.first().source_document_id == "999941"


def test_credit_note_idempotent_on_repeat_invocation(db, company, owner_membership):
    """Calling create_and_post_credit_note_for_platform twice with the
    same (source, source_document_id) returns the existing credit note
    on the second call rather than creating a duplicate."""
    from sales.commands import create_and_post_credit_note_for_platform

    inv_res = _make_posted_invoice(company, "SHOP-RACE-3")
    assert inv_res.success
    invoice = inv_res.data["invoice"]

    cn_lines = [
        {
            "account_id": invoice.lines.first().account_id,
            "description": "Refund R-3",
            "quantity": "1",
            "unit_price": "30.00",
            "discount_amount": "0",
        }
    ]
    first = create_and_post_credit_note_for_platform(
        company=company,
        invoice_id=invoice.id,
        lines=cn_lines,
        credit_note_date=date(2026, 5, 1),
        source="shopify",
        source_document_id="REFUND-R-3",
        reason="RETURN",
    )
    assert first.success, f"first cn failed: {first.error!r}"
    first_cn = first.data["credit_note"]

    second = create_and_post_credit_note_for_platform(
        company=company,
        invoice_id=invoice.id,
        lines=cn_lines,
        credit_note_date=date(2026, 5, 1),
        source="shopify",
        source_document_id="REFUND-R-3",
        reason="RETURN",
    )
    assert second.success, f"second cn failed: {second.error!r}"
    second_cn = second.data["credit_note"]

    assert first_cn.id == second_cn.id
    assert (
        SalesCreditNote.objects.filter(company=company, source="shopify", source_document_id="REFUND-R-3").count() == 1
    )
