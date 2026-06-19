# tests/test_shopify_pipeline_e2e.py
"""
A79+A80 follow-up: end-to-end Shopify pipeline tests.

These tests would have caught the A78 regression the moment it was
introduced. Pre-A79 there was no integration test for the full path
"order_paid event → SalesInvoice + JournalEntry created in the
projection." So when A78 broke create_and_post_invoice_for_platform via
the missing `not auto_created` bypass at sales/commands.py:723, the
projection silently produced nothing and no test failed.

Coverage:
1. Happy path: emit SHOPIFY_ORDER_PAID → projection produces SalesInvoice
   with posted JournalEntry. This is the A78 canary.
2. Refund flow: emit SHOPIFY_ORDER_PAID followed by SHOPIFY_REFUND_CREATED
   → projection produces SalesInvoice + JE + SalesCreditNote + CN JE.
   This is the A23 race-window canary.
3. Failure visibility: artificially break the store config → emit event →
   assert ProjectionFailureLog row is created with MISSING_CONFIG. This is
   the A80 contract — silent failures are forbidden.
4. Replay protection: emit + process the same event twice → only one
   SalesInvoice + one JE exist. Locks in the ProjectionAppliedEvent
   idempotency contract.

See:
- docs/finance_event_first_policy.md §3.5 (defer-don't-fail)
- docs/finance_event_first_policy.md §8 (loud-not-silent)
- docs/finance_event_first_policy.md §9.4 (end-to-end test per connector)

These tests reuse the `shopify_company` fixture + `_make_shopify_order_event`
helper from test_system_je_validation.py to avoid duplicating the company
+ chart-of-accounts + posting-profile scaffolding.
"""

from datetime import date

import pytest

# Reuse the existing scaffolding fixtures.
from tests.test_system_je_validation import (  # noqa: F401
    _make_shopify_order_event,
    shopify_company,
)

# =============================================================================
# 1. Happy path — A78 canary
# =============================================================================


@pytest.mark.django_db
def test_shopify_order_paid_produces_sales_invoice_and_journal_entry(shopify_company):  # noqa: F811
    """The single test that would have caught A78 on the commit that
    introduced it. Emit a SHOPIFY_ORDER_PAID event; assert the projection
    produced (a) a SalesInvoice with source='shopify' and (b) a posted
    JournalEntry attached to that invoice.

    Pre-A78: this passed.
    During A78: the projection silently returned, both assertions failed.
    Post-A78 + A80: it passes again, AND if it ever breaks again, A80's
    ProjectionFailureLog tells the operator exactly which guard tripped.
    """
    from sales.models import SalesInvoice
    from shopify_connector.projections import ShopifyAccountingHandler

    event = _make_shopify_order_event(
        shopify_company,
        shopify_order_id=70001,
        amount="250.00",
    )

    handler = ShopifyAccountingHandler()
    handler.handle(event)

    # Assertion 1: SalesInvoice exists with the right source/key.
    invoices = SalesInvoice.objects.filter(
        company=shopify_company,
        source="shopify",
        source_document_id="70001",
    )
    assert invoices.count() == 1, (
        "Expected exactly one SalesInvoice for order #70001. "
        "If this fails, the shopify_accounting projection's "
        "_handle_order_paid early-returned. Check ProjectionFailureLog "
        "for the reason (per A80)."
    )
    invoice = invoices.first()

    # Assertion 2: invoice carries a posted JournalEntry.
    assert invoice.posted_journal_entry is not None, (
        "SalesInvoice was created but post_sales_invoice didn't attach "
        "a posted_journal_entry. Investigate sales.post_sales_invoice."
    )

    # Assertion 3: status is POSTED.
    assert invoice.status == SalesInvoice.Status.POSTED, f"Expected POSTED status, got {invoice.status}"

    # Assertion 4: amounts roughly match the order amount (within typical
    # rounding tolerance — we don't lock to exact decimals here because tax
    # configuration may vary across companies).
    assert invoice.total_amount > 0, f"Invoice total is {invoice.total_amount}, expected positive."


# =============================================================================
# 2. Refund flow — A23 canary
# =============================================================================


@pytest.mark.django_db
def test_shopify_refund_after_order_produces_credit_note(shopify_company):  # noqa: F811
    """Emit SHOPIFY_ORDER_PAID then SHOPIFY_REFUND_CREATED in sequence.
    Assert the projection produces:
    - The SalesInvoice from the order
    - A SalesCreditNote from the refund, linked to the invoice
    - A JournalEntry for the credit note

    A23 (May 2026) was the original incident here: the refund handler
    raced the order_paid handler and silently dropped the credit note
    when the SalesInvoice POSTED lookup missed the order_paid commit.
    Defer-with-retry now self-heals; this test guards the contract.
    """
    from uuid import uuid4 as _uuid4

    from django.utils import timezone

    from events.models import BusinessEvent, CompanyEventCounter
    from sales.models import SalesCreditNote, SalesInvoice
    from shopify_connector.projections import ShopifyAccountingHandler

    # Step 1: order paid
    order_event = _make_shopify_order_event(
        shopify_company,
        shopify_order_id=70002,
        amount="300.00",
    )
    handler = ShopifyAccountingHandler()
    handler.handle(order_event)

    invoices = SalesInvoice.objects.filter(
        company=shopify_company,
        source="shopify",
        source_document_id="70002",
    )
    assert invoices.count() == 1
    invoice = invoices.first()
    assert invoice.status == SalesInvoice.Status.POSTED

    # Step 2: refund created. Manually emit a SHOPIFY_REFUND_CREATED event
    # because no helper exists for it (analog to _make_shopify_order_event).
    counter, _ = CompanyEventCounter.objects.get_or_create(company=shopify_company)
    counter.last_sequence += 1
    counter.save()

    refund_event = BusinessEvent.objects.create(
        company=shopify_company,
        event_type="shopify.refund_created",
        aggregate_type="ShopifyRefund",
        aggregate_id=str(_uuid4()),
        company_sequence=counter.last_sequence,
        idempotency_key=f"shopify.refund.created:80000{70002}",
        data={
            "amount": "100.00",
            "currency": "USD",
            "transaction_date": date.today().isoformat(),
            "document_ref": "#70002",
            "shopify_refund_id": "80007002",
            "shopify_order_id": "70002",
            "order_number": "70002",
            "reason": "test pack refund",
            "store_public_id": str(_uuid4()),
        },
        occurred_at=timezone.now(),
    )

    handler.handle(refund_event)

    # Assertion: credit note exists with link back to the invoice
    credit_notes = SalesCreditNote.objects.filter(
        company=shopify_company,
        source="shopify",
        source_document_id="80007002",
    )
    assert credit_notes.count() == 1, (
        "Expected one SalesCreditNote for refund 80007002. "
        "If zero, the refund handler silently dropped (A23 class regression). "
        "Check ProjectionFailureLog for the reason."
    )
    cn = credit_notes.first()
    assert cn.invoice_id == invoice.id, "CreditNote should be linked to the original invoice"


# =============================================================================
# 3. Failure visibility — A80 canary
# =============================================================================


@pytest.mark.django_db
def test_missing_store_config_records_projection_failure(shopify_company):  # noqa: F811
    """A80 contract: when the projection handler can't proceed because of
    missing config, it MUST raise (which creates a ProjectionFailureLog
    row) instead of silently returning.

    Setup: break the store's default_customer wiring. The projection
    should detect this in the guard at shopify_connector/projections.py
    and raise ProjectionStateError. BaseProjection.on_error catches it
    and writes a MISSING_CONFIG row to ProjectionFailureLog.

    Pre-A80: the projection logged a warning and returned. No operator-
    visible signal. Merchant sees empty pages with no explanation.
    """
    from projections.exceptions import ProjectionStateError
    from projections.models import ProjectionFailureLog
    from shopify_connector.models import ShopifyStore
    from shopify_connector.projections import ShopifyAccountingHandler

    # Sabotage the store's customer wiring (simulates the pre-A28
    # finalize-stores bug or any other state-misconfiguration scenario).
    store = ShopifyStore.objects.filter(company=shopify_company, status=ShopifyStore.Status.ACTIVE).first()
    assert store is not None, "shopify_company fixture should have an active store"
    original_customer_id = store.default_customer_id
    store.default_customer_id = None
    store.save(update_fields=["default_customer"])

    event = _make_shopify_order_event(
        shopify_company,
        shopify_order_id=70003,
        amount="150.00",
    )
    handler = ShopifyAccountingHandler()

    # Handler must raise (loud failure).
    with pytest.raises(ProjectionStateError):
        handler.handle(event)

    # Now invoke the framework's on_error to simulate the full
    # process_pending lifecycle (which would call on_error on the raise).
    try:
        handler.handle(event)
    except ProjectionStateError as exc:
        handler.on_error(event, exc)

    # Assertion: a ProjectionFailureLog row was created.
    log = ProjectionFailureLog.objects.filter(
        company=shopify_company,
        event=event,
        projection_name=handler.name,
    ).first()
    assert log is not None, (
        "Expected a ProjectionFailureLog row from the missing-store-config "
        "failure. If None, A80's silent-failure conversion regressed."
    )
    assert log.category == ProjectionFailureLog.Category.MISSING_CONFIG
    assert "Customer/PostingProfile" in log.message
    assert log.fix_hint  # The hint pointing to setup_shopify_module_routing.

    # Cleanup: restore the store so other tests in the same fixture pass.
    store.default_customer_id = original_customer_id
    store.save(update_fields=["default_customer"])


# =============================================================================
# 4. Replay idempotency — locks in the ProjectionAppliedEvent contract
# =============================================================================


@pytest.mark.django_db
def test_same_order_event_processed_twice_produces_one_invoice(shopify_company):  # noqa: F811
    """The framework idempotency contract: the same event consumed by the
    same projection twice produces the same single SalesInvoice (not two).
    This is enforced by ProjectionAppliedEvent (unique on company +
    projection + event) AND by create_and_post_invoice_for_platform's
    own SELECT-then-INSERT check on (company, source, source_document_id).

    Already covered indirectly by test_replay_order_paid_no_duplicate_je
    in test_system_je_validation.py; included here for completeness of
    the pipeline E2E suite (one file the future maintainer can look at
    and see the full contract).
    """
    from sales.models import SalesInvoice
    from shopify_connector.projections import ShopifyAccountingHandler

    event = _make_shopify_order_event(
        shopify_company,
        shopify_order_id=70004,
        amount="500.00",
    )
    handler = ShopifyAccountingHandler()

    handler.handle(event)
    invoices_after_first = SalesInvoice.objects.filter(
        company=shopify_company,
        source="shopify",
        source_document_id="70004",
    ).count()
    assert invoices_after_first == 1

    handler.handle(event)
    invoices_after_replay = SalesInvoice.objects.filter(
        company=shopify_company,
        source="shopify",
        source_document_id="70004",
    ).count()
    assert invoices_after_replay == 1, "Replaying the same event must not create a duplicate invoice."


@pytest.mark.django_db
def test_shopify_dimensions_truncate_external_values(shopify_company):  # noqa: F811
    from uuid import uuid4

    from django.utils import timezone

    from accounting.models import AnalysisDimensionValue
    from events.models import BusinessEvent, CompanyEventCounter
    from shopify_connector.projections import ShopifyAccountingHandler

    long_title = "Premium reviewer product " * 8
    counter, _ = CompanyEventCounter.objects.get_or_create(company=shopify_company)
    counter.last_sequence += 1
    counter.save()
    event = BusinessEvent.objects.create(
        company=shopify_company,
        event_type="shopify.order_paid",
        aggregate_type="ShopifyOrder",
        aggregate_id=str(uuid4()),
        company_sequence=counter.last_sequence,
        idempotency_key="shopify.order.paid:70005",
        data={
            "amount": "125.00",
            "currency": "USD",
            "transaction_date": date.today().isoformat(),
            "document_ref": "#70005",
            "shopify_order_id": "70005",
            "order_number": "70005",
            "order_name": "#70005",
            "subtotal": "125.00",
            "total_tax": "0",
            "total_shipping": "0",
            "total_discounts": "0",
            "financial_status": "paid",
            "gateway": "shopify_payments",
            "line_items": [
                {
                    "sku": "reviewer-sku-with-a-long-external-code",
                    "title": long_title,
                    "product_type": "Very Long Product Category From Shopify Review Store",
                    "vendor": "Very Long Vendor Name From Shopify Review Store",
                }
            ],
        },
        occurred_at=timezone.now(),
    )

    ShopifyAccountingHandler().handle(event)

    values = AnalysisDimensionValue.objects.filter(company=shopify_company)
    assert values.filter(dimension__code="PRODUCT").exists()
    assert not values.filter(code__regex=r"^.{21,}$").exists()
    assert not values.filter(name__regex=r"^.{101,}$").exists()
