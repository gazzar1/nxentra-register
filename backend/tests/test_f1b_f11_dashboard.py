"""
F1b + F11 (from the Run-1 fresh-merchant E2E ledger).

- F1b: the dashboard Cash Position tile counted gateway clearing (seeded
  role=LIQUIDITY) as cash — money still AT the processor. It must exclude the
  payment-in-transit roles and show only real bank/cash.
- F11a: the Shopify dashboard had no returns/refunds visibility — orders now
  carry ``total_refunded`` so the dashboard can show a Refunded tile + net.
- F11b: a Shopify refund credit note was always labelled RETURN ("Goods
  returned") even for a money-only refund with no units back; the reason is now
  derived from whether goods were actually restocked.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from accounting.mappings import ModuleAccountMapping
from accounting.models import Account
from projections.models import AccountBalance
from projections.write_barrier import command_writes_allowed, projection_writes_allowed


def _liquidity_account(company, code, name):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code=code,
            name=name,
            account_type=Account.AccountType.ASSET,
            role=Account.AccountRole.LIQUIDITY,
            status=Account.Status.ACTIVE,
        )


def _set_balance(company, account, amount):
    with projection_writes_allowed():
        AccountBalance.objects.update_or_create(
            company=company,
            account=account,
            defaults={"balance": Decimal(amount)},
        )


# ── F1b: Cash Position excludes payment-in-transit ──────────────────────────


@pytest.mark.django_db
def test_cash_position_excludes_gateway_clearing(company, owner_membership, authenticated_client):
    bank = _liquidity_account(company, "11201", "CIB Bank")
    clearing = _liquidity_account(company, "11500", "Shopify Clearing")
    ebd = _liquidity_account(company, "11600", "Expected Bank Deposit")
    _set_balance(company, bank, "1790.00")
    _set_balance(company, clearing, "650.00")
    _set_balance(company, ebd, "0.00")
    with command_writes_allowed():
        ModuleAccountMapping.objects.create(
            company=company, module="shopify_connector", role="SHOPIFY_CLEARING", account=clearing
        )
        ModuleAccountMapping.objects.create(
            company=company, module="shopify_connector", role="EXPECTED_BANK_DEPOSIT", account=ebd
        )

    body = authenticated_client.get("/api/reports/dashboard-widgets/").json()
    cash = body["cash_position"]
    codes = {a["code"] for a in cash["accounts"]}

    # Real cash only — the 650 clearing (and EBD) are NOT counted.
    assert Decimal(cash["total"]) == Decimal("1790.00")
    assert "11201" in codes
    assert "11500" not in codes
    assert "11600" not in codes


# ── F11a: orders carry total_refunded for the dashboard tile ────────────────


def _order_with_refunds(company):
    from shopify_connector.models import ShopifyOrder, ShopifyRefund, ShopifyStore

    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="f11-test.myshopify.com",
        access_token="tok",
        status=ShopifyStore.Status.ACTIVE,
    )
    order = ShopifyOrder.objects.create(
        company=company,
        store=store,
        shopify_order_id=71006,
        shopify_order_number="1006",
        shopify_order_name="#1006",
        total_price=Decimal("250.00"),
        subtotal_price=Decimal("250.00"),
        currency="EGP",
        financial_status="partially_refunded",
        shopify_created_at=datetime(2026, 7, 6, tzinfo=UTC),
        order_date=date(2026, 7, 6),
        status=ShopifyOrder.Status.PROCESSED,
    )
    for i, amt in enumerate(("50.00", "30.00")):
        ShopifyRefund.objects.create(
            company=company,
            order=order,
            shopify_refund_id=90000 + i,
            amount=Decimal(amt),
            currency="EGP",
            shopify_created_at=datetime(2026, 7, 6, tzinfo=UTC),
            status=ShopifyRefund.Status.PROCESSED,
        )
    return order


@pytest.mark.django_db
def test_order_serializer_reports_total_refunded(company):
    from shopify_connector.serializers import ShopifyOrderSerializer

    order = _order_with_refunds(company)
    # Un-annotated instance → the serializer falls back to summing the refunds.
    data = ShopifyOrderSerializer(order).data
    assert Decimal(data["total_refunded"]) == Decimal("80.00")


@pytest.mark.django_db
def test_orders_endpoint_annotates_total_refunded(company, owner_membership, authenticated_client):
    _order_with_refunds(company)
    rows = authenticated_client.get("/api/shopify/orders/").json()
    row = next(r for r in rows if r["shopify_order_name"] == "#1006")
    assert Decimal(row["total_refunded"]) == Decimal("80.00")


# ── F11b: credit-note reason reflects whether goods were restocked ──────────


def _handler():
    from shopify_connector.projections import ShopifyAccountingHandler

    return ShopifyAccountingHandler()


class _Refund:
    def __init__(self, raw_payload):
        self.raw_payload = raw_payload


@pytest.mark.django_db
def test_restocked_refund_is_a_return():
    h = _handler()
    r = _Refund({"refund_line_items": [{"restock_type": "return", "quantity": 1, "line_item": {"sku": "MUG"}}]})
    assert h._refund_has_restocked_goods(r) is True


@pytest.mark.django_db
def test_money_only_refund_is_not_a_return():
    h = _handler()
    assert h._refund_has_restocked_goods(None) is False
    # No restock lines at all (pure money-only refund).
    assert h._refund_has_restocked_goods(_Refund({"refund_line_items": []})) is False
    # Line present but explicitly not restocked (customer kept the item).
    assert (
        h._refund_has_restocked_goods(_Refund({"refund_line_items": [{"restock_type": "no_restock", "quantity": 1}]}))
        is False
    )
    # Restock flagged but zero quantity.
    assert (
        h._refund_has_restocked_goods(_Refund({"refund_line_items": [{"restock_type": "return", "quantity": 0}]}))
        is False
    )
