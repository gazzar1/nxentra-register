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
from unittest.mock import patch

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
