# tests/test_a14c_per_order_drilldown.py
"""
A14c — per-Shopify-order drilldown on the Reconciliation Control Center.

Each row joins:
- SalesInvoice (Shopify-imported)
- the order's clearing-line dimension tag (provider routing)
- PaymentSettlement event line_items (settled / unsettled)
- payment_settlement_clearance JEs (bank match landed)

Status:
  expected  — clearing exists, no settlement event for the order yet
  settled   — settlement imported (batch known) but no clearance JE yet
  banked    — clearance JE exists for the batch (bank deposit matched)
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.models import Account
from accounting.payment_settlement_projection import PaymentSettlementProjection
from accounting.settlement_imports import import_settlement_csv
from accounting.settlement_provider import SettlementProvider
from projections.write_barrier import projection_writes_allowed


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a14c-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return {"store": store}


def _create_shopify_invoice(company, shopify_order_id, amount, gateway="paymob", invoice_date=None):
    """Create a posted Shopify SalesInvoice tagged with the gateway's
    settlement_provider dimension. Mimics what A12's projection does for
    SHOPIFY_ORDER_PAID."""
    from accounting.settlement_provider import SettlementProvider
    from sales.commands import create_and_post_invoice_for_platform

    if invoice_date is None:
        invoice_date = date.today() - timedelta(days=5)

    provider = SettlementProvider.objects.get(company=company, normalized_code=gateway)
    revenue = Account.objects.get(company=company, code="41000")

    tags = [
        {
            "dimension_public_id": str(provider.dimension_value.dimension.public_id),
            "value_public_id": str(provider.dimension_value.public_id),
        }
    ]

    from shopify_connector.models import ShopifyStore

    store = ShopifyStore.objects.get(company=company)
    result = create_and_post_invoice_for_platform(
        company=company,
        customer_id=store.default_customer_id,
        posting_profile_id=provider.posting_profile_id,
        lines=[
            {
                "account_id": revenue.id,
                "description": f"Shopify order {shopify_order_id}",
                "quantity": "1",
                "unit_price": str(amount),
                "discount_amount": "0",
            }
        ],
        invoice_date=invoice_date,
        source="shopify",
        source_document_id=shopify_order_id,
        reference=f"#{shopify_order_id}",
        notes="",
        currency=company.default_currency or "USD",
        skip_cogs=True,
        control_line_analysis_tags=tags,
    )
    assert result.success, f"invoice creation failed: {result.error}"
    return result.data["invoice"]


# =============================================================================
# Per-order drilldown
# =============================================================================


def test_orders_endpoint_returns_expected_status_when_no_settlement(
    shopify_setup, company, owner_membership, authenticated_client
):
    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    _create_shopify_invoice(company, "ORD-1", Decimal("1000.00"))
    _create_shopify_invoice(company, "ORD-2", Decimal("500.00"))

    response = authenticated_client.get(f"/api/accounting/reconciliation/orders/?provider_id={paymob.id}")
    assert response.status_code == 200
    body = response.json()
    assert len(body["orders"]) == 2
    assert {o["shopify_order_id"] for o in body["orders"]} == {"ORD-1", "ORD-2"}
    for order in body["orders"]:
        assert order["status"] == "expected"
        assert order["settled_batch_id"] is None
        assert order["is_banked"] is False
    assert body["totals"]["by_status"]["expected"] == 2
    assert body["totals"]["by_status"]["settled"] == 0
    assert body["totals"]["by_status"]["banked"] == 0


def test_orders_endpoint_returns_settled_status_after_csv_import(
    shopify_setup, company, owner_membership, authenticated_client
):
    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    _create_shopify_invoice(company, "ORD-1", Decimal("1000.00"))
    _create_shopify_invoice(company, "ORD-2", Decimal("500.00"))

    # Import CSV that lists ORD-1 in batch PMB-555 (ORD-2 is unsettled).
    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,PMB-555,2026-04-25
"""
    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=csv,
        source_filename="paymob.csv",
    )
    PaymentSettlementProjection().process_pending(company)

    response = authenticated_client.get(f"/api/accounting/reconciliation/orders/?provider_id={paymob.id}")
    body = response.json()
    by_order = {o["shopify_order_id"]: o for o in body["orders"]}

    assert by_order["ORD-1"]["status"] == "settled"
    assert by_order["ORD-1"]["settled_batch_id"] == "PMB-555"
    assert by_order["ORD-1"]["settled_amount"] == "1000.00"
    assert by_order["ORD-1"]["is_banked"] is False

    assert by_order["ORD-2"]["status"] == "expected"
    assert by_order["ORD-2"]["settled_batch_id"] is None

    assert body["totals"]["by_status"]["expected"] == 1
    assert body["totals"]["by_status"]["settled"] == 1


def test_a36_orders_endpoint_status_when_settlement_event_exists_but_je_did_not_post(
    shopify_setup, company, owner_membership, authenticated_client
):
    """A36: pre-A36 the drilldown derived status from the settlement
    EVENT's existence in line_items, so an order with a settlement
    import but no posted JE (defensive math guard rejected it pre-A20,
    or the projection just hasn't run yet) showed 'Settled' even
    though clearing hadn't drained. Post-A36 the status is derived
    from the JournalEntry state, so the order correctly shows
    'expected' until the JE actually posts."""
    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    _create_shopify_invoice(company, "ORD-1", Decimal("1000.00"))

    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,PMB-A36,2026-04-25
"""
    # Import the CSV — settlement event lands in the queue with line_items
    # mapping ORD-1 to PMB-A36. But DON'T run the projection, so no JE
    # posts. This mirrors the pre-A20 silent-failure scenario where the
    # event existed but the projection rejected the JE.
    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=csv,
        source_filename="paymob.csv",
    )

    response = authenticated_client.get(f"/api/accounting/reconciliation/orders/?provider_id={paymob.id}")
    body = response.json()
    by_order = {o["shopify_order_id"]: o for o in body["orders"]}

    # ORD-1 status MUST be 'expected' because no settlement JE has
    # posted yet — clearing hasn't drained. Pre-A36 this would have
    # incorrectly reported 'settled' based on the event's existence.
    assert by_order["ORD-1"]["status"] == "expected"
    # The settled_batch_id and amount fields stay populated from the
    # event's line_items so the merchant can see the IMPORT happened
    # — they're not status signals, they're audit context.
    assert by_order["ORD-1"]["settled_batch_id"] == "PMB-A36"
    assert by_order["ORD-1"]["is_banked"] is False
    assert body["totals"]["by_status"]["expected"] == 1
    assert body["totals"]["by_status"]["settled"] == 0

    # Now run the projection — JE posts. Status flips to 'settled'.
    PaymentSettlementProjection().process_pending(company)

    response = authenticated_client.get(f"/api/accounting/reconciliation/orders/?provider_id={paymob.id}")
    body = response.json()
    by_order = {o["shopify_order_id"]: o for o in body["orders"]}
    assert by_order["ORD-1"]["status"] == "settled"
    assert body["totals"]["by_status"]["settled"] == 1


def test_orders_endpoint_returns_banked_status_after_bank_match(
    shopify_setup, company, owner_membership, user, authenticated_client
):
    from accounting.bank_reconciliation import auto_match_statement, import_bank_statement
    from accounts.authz import ActorContext

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    _create_shopify_invoice(company, "ORD-1", Decimal("1000.00"))

    # Import CSV + project the settlement JE
    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,PMB-555,2026-04-25
"""
    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=csv,
        source_filename="paymob.csv",
    )
    PaymentSettlementProjection().process_pending(company)

    # Set up a Merchant Bank account to receive the deposit
    with projection_writes_allowed():
        merchant_bank = Account.objects.projection().create(
            company=company,
            code="10100",
            name="Merchant Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )

    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    actor = ActorContext(user=user, company=company, membership=owner_membership, perms=perms)

    # Bank statement with the deposit
    bank_result = import_bank_statement(
        actor=actor,
        account_id=merchant_bank.id,
        statement_date=date(2026, 4, 26),
        period_start=date(2026, 4, 24),
        period_end=date(2026, 4, 28),
        opening_balance=Decimal("0"),
        closing_balance=Decimal("970.00"),
        lines_data=[
            {
                "line_date": "2026-04-26",
                "value_date": "2026-04-26",
                "amount": "970.00",
                "description": "PAYMOB SETTLEMENT PMB-555",
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="EGP",
    )
    assert bank_result.success
    statement = bank_result.data["statement"]

    auto_match_result = auto_match_statement(actor, statement.id)
    assert auto_match_result.data["settlement_matched"] == 1

    # Now the order should report status=banked
    response = authenticated_client.get(f"/api/accounting/reconciliation/orders/?provider_id={paymob.id}")
    body = response.json()
    by_order = {o["shopify_order_id"]: o for o in body["orders"]}
    assert by_order["ORD-1"]["status"] == "banked"
    assert by_order["ORD-1"]["is_banked"] is True
    assert body["totals"]["by_status"]["banked"] == 1


def test_orders_endpoint_filters_to_provider(shopify_setup, company, owner_membership, authenticated_client):
    # Two providers; orders for one shouldn't show under the other.
    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    paypal = SettlementProvider.objects.get(company=company, normalized_code="paypal")
    _create_shopify_invoice(company, "ORD-PAYMOB", Decimal("100.00"), gateway="paymob")
    _create_shopify_invoice(company, "ORD-PAYPAL", Decimal("200.00"), gateway="paypal")

    paymob_resp = authenticated_client.get(f"/api/accounting/reconciliation/orders/?provider_id={paymob.id}").json()
    paypal_resp = authenticated_client.get(f"/api/accounting/reconciliation/orders/?provider_id={paypal.id}").json()

    assert {o["shopify_order_id"] for o in paymob_resp["orders"]} == {"ORD-PAYMOB"}
    assert {o["shopify_order_id"] for o in paypal_resp["orders"]} == {"ORD-PAYPAL"}


def test_orders_endpoint_multi_gateway_batch_attributes_to_sub_provider(
    shopify_setup, company, owner_membership, authenticated_client
):
    """When a Paymob settlement batch rolls up multiple gateways via
    provider_breakdown (e.g. parent 'paymob' with 'paymob' + 'paymob_accept'
    sub-rows), the drilldown for the SUB-gateway must still attribute its
    order to the multi-gateway batch.

    Pre-fix (2026-05-09 dogfood): the drilldown filtered settlement events
    by exact-match top-level provider_normalized_code, so an event whose
    parent was 'paymob' was skipped when drilling 'paymob_accept'. Result:
    order #1009 (Paymob Accept) showed status='Expected' even though
    Stage 1 read Settled 1,000 / Open 0 (because the dimension-tagged CR
    on Paymob Accept clearing came from the multi-gateway settlement JE).

    Post-fix: events match if EITHER top-level provider OR breakdown
    contains the drilldown provider. Lines are then filtered by per-row
    `gateway` field so a 'paymob' line in a multi-gateway batch doesn't
    leak into the 'paymob_accept' drilldown (and vice-versa).
    """
    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")

    # paymob_accept isn't in the bootstrap rows; create it via the same
    # lazy-create helper the production lazy-create path uses (mimics what
    # happens when a Shopify order brings in a new gateway). This gives
    # the provider a proper PostingProfile + dimension_value so the
    # drilldown query has a clearing account to filter on.
    from accounting.settlement_provider import SettlementProvider as SP

    paymob_accept = SP.lookup_or_create_for_review(
        company=company,
        external_system="shopify",
        raw_gateway="Paymob Accept",
        fallback_posting_profile=paymob.posting_profile,
    )
    assert paymob_accept is not None and paymob_accept.normalized_code == "paymob_accept"

    # Two real test orders, one routed via each sub-gateway.
    _create_shopify_invoice(company, "ORD-PMB", Decimal("3000.00"), gateway="paymob")
    _create_shopify_invoice(company, "ORD-PMA", Decimal("1000.00"), gateway="paymob_accept")

    # Multi-gateway Paymob CSV — both gateways under one parent batch.
    csv = b"""order_id,gross,fee,net,gateway,payout_batch_id,payout_date
ORD-PMB,3000.00,90.00,2910.00,Paymob,MULTIGW-001,2026-04-26
ORD-PMA,1000.00,30.00,970.00,Paymob Accept,MULTIGW-001,2026-04-26
"""
    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=csv,
        source_filename="multigw.csv",
    )
    PaymentSettlementProjection().process_pending(company)

    # Drill down for Paymob Accept — order ORD-PMA must show 'settled',
    # NOT 'expected', and must reference MULTIGW-001 even though that
    # batch's parent provider was 'paymob'.
    pma_resp = authenticated_client.get(f"/api/accounting/reconciliation/orders/?provider_id={paymob_accept.id}").json()
    pma_orders = {o["shopify_order_id"]: o for o in pma_resp["orders"]}

    # Bootstrap order shouldn't appear here — it has no Shopify SalesInvoice.
    assert "ORD-PMA" in pma_orders
    assert pma_orders["ORD-PMA"]["status"] == "settled"
    assert pma_orders["ORD-PMA"]["settled_batch_id"] == "MULTIGW-001"

    # Cross-attribution check: ORD-PMB is a Paymob (parent) order — it
    # must NOT appear in the Paymob Accept drilldown. The line-level
    # `gateway` filter ensures multi-gateway batch lines don't leak.
    assert "ORD-PMB" not in pma_orders

    # Inverse check: drill down for Paymob — ORD-PMB must appear and be
    # 'settled', and ORD-PMA must NOT appear there.
    pmb_resp = authenticated_client.get(f"/api/accounting/reconciliation/orders/?provider_id={paymob.id}").json()
    pmb_orders = {o["shopify_order_id"]: o for o in pmb_resp["orders"]}
    assert pmb_orders["ORD-PMB"]["status"] == "settled"
    assert pmb_orders["ORD-PMB"]["settled_batch_id"] == "MULTIGW-001"
    assert "ORD-PMA" not in pmb_orders


def test_orders_endpoint_400_without_provider_id(authenticated_client, owner_membership):
    response = authenticated_client.get("/api/accounting/reconciliation/orders/")
    assert response.status_code == 400


def test_orders_endpoint_404_for_unknown_provider(authenticated_client, owner_membership):
    response = authenticated_client.get("/api/accounting/reconciliation/orders/?provider_id=999999")
    assert response.status_code == 404
