# tests/test_a39_settlement_double_count.py
"""
A39 — settlement importer must not double-credit clearing when the
order already has a posted credit note from the platform's refund flow.

Canonical case (Aljazeera8 dry-run, BST-701 / order 1007):
1. Shopify fires `refund_created` on a COD failed delivery -> the
   shopify_accounting projection posts CN-000002 (credits Bosta clearing
   1,200 via the credit-note JE).
2. Bosta's settlement statement later reports the same order as
   `returned_uncollected_amount=1,200, status=returned` -> pre-A39 the
   PaymentSettlementProjection would credit Bosta clearing another
   1,200 via the Sales Returns line.
3. Net effect: clearing over-drained by 1,200 per affected order.

Fix: `_detect_already_credited_lines` checks each settlement line's
`order_id` against posted SalesCreditNote rows whose original invoice
has the matching `(source='shopify', source_document_id)`. Skipped
lines are subtracted from the JE's gross + uncollected (and the
provider_breakdown when present), so the JE lands a balanced reduction:
DR Sales Returns drops, CR Clearing drops, net + fees unchanged.
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.models import JournalEntry
from accounting.payment_settlement_projection import (
    PaymentSettlementProjection,
    _detect_already_credited_lines,
)
from accounting.settlement_imports import import_settlement_csv
from accounting.settlement_provider import SettlementProvider


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    """Bootstrap Shopify accounts + providers."""
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a39-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return {"store": store}


def _ensure_invoice_chain(company):
    """Idempotent variant of test_a23's _setup_simple_invoice_chain so
    multiple credit notes can share one customer/profile/revenue triple
    in the same test."""
    from accounting.models import Account
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from sales.models import Customer, PostingProfile

    ar_control = Account.objects.filter(company=company, code="11401").first()
    revenue = Account.objects.filter(company=company, code="41001").first()
    if not ar_control or not revenue:
        with projection_writes_allowed():
            if not ar_control:
                ar_control = Account.objects.projection().create(
                    company=company,
                    code="11401",
                    name="A39 Test AR Control",
                    account_type=Account.AccountType.ASSET,
                    role=Account.AccountRole.RECEIVABLE_CONTROL,
                    status=Account.Status.ACTIVE,
                )
            if not revenue:
                revenue = Account.objects.projection().create(
                    company=company,
                    code="41001",
                    name="A39 Test Revenue",
                    account_type=Account.AccountType.REVENUE,
                    status=Account.Status.ACTIVE,
                )

    customer = Customer.objects.filter(company=company, code="A39-CUSTOMER").first()
    posting_profile = PostingProfile.objects.filter(company=company, code="A39-PROFILE").first()
    if not customer or not posting_profile:
        with command_writes_allowed():
            if not customer:
                customer = Customer.objects.create(
                    company=company,
                    code="A39-CUSTOMER",
                    name="A39 Test Customer",
                )
            if not posting_profile:
                posting_profile = PostingProfile.objects.create(
                    company=company,
                    code="A39-PROFILE",
                    name="A39 Test Profile",
                    profile_type=PostingProfile.ProfileType.CUSTOMER,
                    control_account=ar_control,
                )
    return customer, posting_profile, revenue


def _post_credit_note_for_shopify_order(company, order_id: str, amount: str):
    """Create a posted SalesInvoice tagged source='shopify' + a posted
    SalesCreditNote against it. Mirrors the live shopify_accounting
    projection chain that fires on order_paid -> refund_created.
    """
    from sales.commands import (
        create_and_post_credit_note_for_platform,
        create_and_post_invoice_for_platform,
    )

    customer, posting_profile, revenue = _ensure_invoice_chain(company)

    inv_res = create_and_post_invoice_for_platform(
        company=company,
        customer_id=customer.id,
        posting_profile_id=posting_profile.id,
        lines=[
            {
                "account_id": revenue.id,
                "description": f"Order {order_id} revenue",
                "quantity": "1",
                "unit_price": amount,
                "discount_amount": "0",
            }
        ],
        invoice_date=date(2026, 4, 30),
        source="shopify",
        source_document_id=order_id,
    )
    assert inv_res.success, f"invoice setup failed: {inv_res.error!r}"
    invoice = inv_res.data["invoice"]

    cn_res = create_and_post_credit_note_for_platform(
        company=company,
        invoice_id=invoice.id,
        lines=[
            {
                "account_id": revenue.id,
                "description": f"Refund for order {order_id}",
                "quantity": "1",
                "unit_price": amount,
                "discount_amount": "0",
            }
        ],
        credit_note_date=date(2026, 4, 30),
        source="shopify",
        source_document_id=f"REFUND-{order_id}",
        reason="RETURN",
    )
    assert cn_res.success, f"credit note setup failed: {cn_res.error!r}"
    return invoice, cn_res.data.get("credit_note")


# =============================================================================
# _detect_already_credited_lines — unit tests
# =============================================================================


def test_detect_returns_zero_when_no_credit_notes_exist(db, company):
    """No CN posted -> nothing to skip; returns (0, 0, {})."""
    line_items = [
        {"order_id": "ORD-100", "status": "returned", "uncollected": "500.00"},
        {"order_id": "ORD-101", "status": "delivered", "uncollected": "0"},
    ]
    total, count, per_gw = _detect_already_credited_lines(company, line_items)
    assert total == Decimal("0")
    assert count == 0
    assert per_gw == {}


def test_detect_flags_returned_line_when_cn_already_posted(db, company, owner_membership):
    """The BST-701 case: a returned line whose order has a posted CN
    must be flagged with its uncollected amount."""
    _post_credit_note_for_shopify_order(company, "ORD-1007", "1200.00")
    line_items = [
        {"order_id": "ORD-1007", "status": "returned", "uncollected": "1200.00"},
        {"order_id": "ORD-1008", "status": "delivered", "uncollected": "0"},
    ]
    total, count, _ = _detect_already_credited_lines(company, line_items)
    assert total == Decimal("1200.00")
    assert count == 1


def test_detect_ignores_delivered_lines_even_when_cn_exists(db, company, owner_membership):
    """A delivered line is genuine — even if a CN exists for that order
    (partial refund, separate flow), the settlement collection happened.
    Don't drop it."""
    _post_credit_note_for_shopify_order(company, "ORD-2000", "100.00")
    line_items = [
        {"order_id": "ORD-2000", "status": "delivered", "uncollected": "0"},
    ]
    total, count, _ = _detect_already_credited_lines(company, line_items)
    assert total == Decimal("0")
    assert count == 0


def test_detect_aggregates_per_gateway_when_breakdown_present(db, company, owner_membership):
    """When a multi-gateway batch has multiple already-credited lines,
    skipped totals are tracked per gateway code so the provider_breakdown
    can be reduced symmetrically."""
    _post_credit_note_for_shopify_order(company, "ORD-3001", "300.00")
    _post_credit_note_for_shopify_order(company, "ORD-3002", "200.00")
    line_items = [
        {
            "order_id": "ORD-3001",
            "status": "refunded",
            "refund": "300.00",
            "gateway": "paymob",
        },
        {
            "order_id": "ORD-3002",
            "status": "refunded",
            "refund": "200.00",
            "gateway": "paymob_accept",
        },
    ]
    total, count, per_gw = _detect_already_credited_lines(company, line_items)
    assert total == Decimal("500.00")
    assert count == 2
    assert per_gw == {
        "paymob": Decimal("300.00"),
        "paymob_accept": Decimal("200.00"),
    }


# =============================================================================
# End-to-end: settlement projection skips clearing CR for already-credited orders
# =============================================================================


def test_e2e_bosta_settlement_skips_already_credited_returned_order(shopify_setup, company, owner_membership):
    """Aljazeera8 BST-701 reproduced as a test:
    - Bosta CSV has 2 delivered orders + 1 returned order in COD-A.
    - The returned order ALREADY has a posted CN (Shopify refund flow).
    - The settlement JE must drop the returned line: gross 2,700 (not
      3,500), no Sales Returns DR, clearing CR 2,700.
    """
    # The returned line in the CSV is ORD-103. Pre-create its CN.
    _post_credit_note_for_shopify_order(company, "ORD-103", "800.00")

    csv = b"""shipment_id,order_id,collected,courier_fee,net,batch_id,payout_date,status,returned_uncollected_amount
SHIP-1,ORD-101,1500.00,100.00,1400.00,COD-A,2026-04-26,delivered,
SHIP-2,ORD-102,1200.00,80.00,1120.00,COD-A,2026-04-26,delivered,
SHIP-3,ORD-103,0,0.00,0.00,COD-A,2026-04-26,returned,800.00
"""
    import_settlement_csv(
        company=company,
        provider_normalized_code="bosta",
        file_content=csv,
        source_filename="bosta_a39.csv",
    )
    PaymentSettlementProjection().process_pending(company)

    entry = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement",
        source_document="bosta:COD-A",
    )
    lines_by_code = {line.account.code: line for line in entry.lines.all()}

    # Net to bank = 2,520 (1,400 + 1,120) — unchanged by A39.
    assert lines_by_code["11600"].debit == Decimal("2520.00")
    # Fees = 180 (100 + 80) — unchanged by A39.
    assert lines_by_code["53000"].debit == Decimal("180.00")
    # CR Clearing = 2,700 (3,500 gross - 800 already-credited).
    bosta = SettlementProvider.objects.get(company=company, normalized_code="bosta")
    bosta_clearing_code = bosta.posting_profile.control_account.code
    assert lines_by_code[bosta_clearing_code].credit == Decimal("2700.00")
    # Sales Returns DR (41200) MUST NOT be present — pre-A39 it would
    # have been 800 here, double-counting the CN's reversal.
    assert "41200" not in lines_by_code


def test_e2e_skip_does_not_break_balance(shopify_setup, company, owner_membership):
    """After A39 mutates gross + uncollected, the JE must still balance
    (sum of debits = sum of credits). Same scenario as above."""
    _post_credit_note_for_shopify_order(company, "ORD-201", "500.00")
    csv = b"""shipment_id,order_id,collected,courier_fee,net,batch_id,payout_date,status,returned_uncollected_amount
SHIP-A,ORD-200,1000.00,50.00,950.00,COD-X,2026-04-26,delivered,
SHIP-B,ORD-201,0,0.00,0.00,COD-X,2026-04-26,returned,500.00
"""
    import_settlement_csv(
        company=company,
        provider_normalized_code="bosta",
        file_content=csv,
        source_filename="bosta_a39_balance.csv",
    )
    PaymentSettlementProjection().process_pending(company)

    entry = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement",
        source_document="bosta:COD-X",
    )
    total_debit = sum((line.debit for line in entry.lines.all()), Decimal("0"))
    total_credit = sum((line.credit for line in entry.lines.all()), Decimal("0"))
    assert total_debit == total_credit, f"A39 reduction broke JE balance: DR {total_debit} != CR {total_credit}"


def test_e2e_full_batch_already_credited_posts_no_je(shopify_setup, company, owner_membership):
    """Edge case: every line in the batch was already credited via CN.
    Subtracting reduces gross to 0 — there is literally nothing to post.
    The projection must short-circuit without raising."""
    _post_credit_note_for_shopify_order(company, "ORD-301", "750.00")

    csv = b"""shipment_id,order_id,collected,courier_fee,net,batch_id,payout_date,status,returned_uncollected_amount
SHIP-Z,ORD-301,0,0.00,0.00,COD-Z,2026-04-27,returned,750.00
"""
    import_settlement_csv(
        company=company,
        provider_normalized_code="bosta",
        file_content=csv,
        source_filename="bosta_a39_full_skip.csv",
    )
    PaymentSettlementProjection().process_pending(company)

    posted = JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement",
        source_document="bosta:COD-Z",
    )
    assert not posted.exists(), "Expected no JE when every line was already credited"
