# tests/test_shopify_reconciliation.py
"""
Tests for the Shopify payout reconciliation engine.

Covers:
- Transaction-level matching (charge → order, refund → refund)
- Payout-level reconciliation (verified, partial, discrepancy)
- Summary aggregation across multiple payouts
- API endpoints
"""

import pytest
from decimal import Decimal
from datetime import date, datetime, timezone as tz
from uuid import uuid4

from shopify_connector.models import (
    ShopifyStore,
    ShopifyOrder,
    ShopifyRefund,
    ShopifyPayout,
    ShopifyPayoutTransaction,
)
from shopify_connector.reconciliation import (
    reconcile_payout,
    reconciliation_summary,
    _match_transaction,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def store(db, company):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="test-store.myshopify.com",
        status=ShopifyStore.Status.ACTIVE,
    )


@pytest.fixture
def order_1001(db, company, store):
    return ShopifyOrder.objects.create(
        company=company,
        store=store,
        shopify_order_id=9001,
        shopify_order_number="1001",
        shopify_order_name="#1001",
        total_price=Decimal("100.00"),
        subtotal_price=Decimal("95.00"),
        total_tax=Decimal("5.00"),
        currency="USD",
        financial_status="paid",
        shopify_created_at=datetime(2026, 3, 1, tzinfo=tz.utc),
        order_date=date(2026, 3, 1),
        status=ShopifyOrder.Status.PROCESSED,
    )


@pytest.fixture
def order_1002(db, company, store):
    return ShopifyOrder.objects.create(
        company=company,
        store=store,
        shopify_order_id=9002,
        shopify_order_number="1002",
        shopify_order_name="#1002",
        total_price=Decimal("50.00"),
        subtotal_price=Decimal("50.00"),
        currency="USD",
        financial_status="paid",
        shopify_created_at=datetime(2026, 3, 2, tzinfo=tz.utc),
        order_date=date(2026, 3, 2),
        status=ShopifyOrder.Status.PROCESSED,
    )


@pytest.fixture
def refund_1001(db, company, order_1001):
    return ShopifyRefund.objects.create(
        company=company,
        order=order_1001,
        shopify_refund_id=5001,
        amount=Decimal("25.00"),
        currency="USD",
        reason="Customer request",
        shopify_created_at=datetime(2026, 3, 3, tzinfo=tz.utc),
        status=ShopifyRefund.Status.PROCESSED,
    )


@pytest.fixture
def payout(db, company, store):
    return ShopifyPayout.objects.create(
        company=company,
        store=store,
        shopify_payout_id=7001,
        gross_amount=Decimal("125.00"),
        fees=Decimal("5.00"),
        net_amount=Decimal("120.00"),
        currency="USD",
        shopify_status="paid",
        payout_date=date(2026, 3, 5),
    )


@pytest.fixture
def payout_with_transactions(db, company, payout, order_1001, order_1002, refund_1001):
    """Create a payout with 3 transactions: 2 charges + 1 refund."""
    ShopifyPayoutTransaction.objects.create(
        company=company,
        payout=payout,
        shopify_transaction_id=80001,
        transaction_type=ShopifyPayoutTransaction.TransactionType.CHARGE,
        amount=Decimal("100.00"),
        fee=Decimal("-3.00"),
        net=Decimal("97.00"),
        currency="USD",
        source_order_id=9001,
        source_type="order",
        processed_at=datetime(2026, 3, 1, tzinfo=tz.utc),
    )
    ShopifyPayoutTransaction.objects.create(
        company=company,
        payout=payout,
        shopify_transaction_id=80002,
        transaction_type=ShopifyPayoutTransaction.TransactionType.CHARGE,
        amount=Decimal("50.00"),
        fee=Decimal("-2.00"),
        net=Decimal("48.00"),
        currency="USD",
        source_order_id=9002,
        source_type="order",
        processed_at=datetime(2026, 3, 2, tzinfo=tz.utc),
    )
    ShopifyPayoutTransaction.objects.create(
        company=company,
        payout=payout,
        shopify_transaction_id=80003,
        transaction_type=ShopifyPayoutTransaction.TransactionType.REFUND,
        amount=Decimal("-25.00"),
        fee=Decimal("0"),
        net=Decimal("-25.00"),
        currency="USD",
        source_order_id=9001,
        source_type="refund",
        processed_at=datetime(2026, 3, 3, tzinfo=tz.utc),
    )
    return payout


# ── Unit Tests ────────────────────────────────────────────────────

class TestReconcilePayout:

    def test_no_transactions_returns_unverified(self, company, payout):
        result = reconcile_payout(company, payout)
        assert result.status == "no_transactions"
        assert result.total_transactions == 0

    def test_all_transactions_matched(self, company, payout_with_transactions):
        result = reconcile_payout(company, payout_with_transactions)
        assert result.matched_transactions == 3
        assert result.unmatched_transactions == 0
        assert result.total_transactions == 3

    def test_verified_status_when_amounts_balance(self, company, payout_with_transactions):
        result = reconcile_payout(company, payout_with_transactions)
        assert result.status == "verified"
        assert len(result.discrepancies) == 0

    def test_charge_matched_to_order(self, company, payout_with_transactions):
        result = reconcile_payout(company, payout_with_transactions)
        charge_match = next(
            m for m in result.transaction_matches
            if m.shopify_transaction_id == 80001
        )
        assert charge_match.matched is True
        assert "#1001" in charge_match.matched_to

    def test_refund_matched_to_refund_record(self, company, payout_with_transactions):
        result = reconcile_payout(company, payout_with_transactions)
        refund_match = next(
            m for m in result.transaction_matches
            if m.shopify_transaction_id == 80003
        )
        assert refund_match.matched is True
        assert "Refund" in refund_match.matched_to

    def test_discrepancy_when_net_doesnt_balance(self, db, company, store):
        """Create a payout where transaction net doesn't match payout net."""
        payout = ShopifyPayout.objects.create(
            company=company,
            store=store,
            shopify_payout_id=7099,
            gross_amount=Decimal("100.00"),
            fees=Decimal("3.00"),
            net_amount=Decimal("97.00"),
            currency="USD",
            shopify_status="paid",
            payout_date=date(2026, 3, 10),
        )
        # Transaction with wrong net (should be 97, reporting 95)
        ShopifyPayoutTransaction.objects.create(
            company=company,
            payout=payout,
            shopify_transaction_id=90001,
            transaction_type=ShopifyPayoutTransaction.TransactionType.CHARGE,
            amount=Decimal("100.00"),
            fee=Decimal("-3.00"),
            net=Decimal("95.00"),  # Wrong!
            currency="USD",
            processed_at=datetime(2026, 3, 10, tzinfo=tz.utc),
        )

        result = reconcile_payout(company, payout)
        assert result.status == "discrepancy"
        assert len(result.discrepancies) > 0
        assert result.net_variance != Decimal("0")

    def test_unmatched_charge_gives_partial_status(self, db, company, store):
        """A charge with no local order results in partial status."""
        payout = ShopifyPayout.objects.create(
            company=company,
            store=store,
            shopify_payout_id=7098,
            gross_amount=Decimal("50.00"),
            fees=Decimal("2.00"),
            net_amount=Decimal("48.00"),
            currency="USD",
            shopify_status="paid",
            payout_date=date(2026, 3, 11),
        )
        ShopifyPayoutTransaction.objects.create(
            company=company,
            payout=payout,
            shopify_transaction_id=90002,
            transaction_type=ShopifyPayoutTransaction.TransactionType.CHARGE,
            amount=Decimal("50.00"),
            fee=Decimal("-2.00"),
            net=Decimal("48.00"),
            currency="USD",
            source_order_id=99999,  # No matching local order
            source_type="order",
            processed_at=datetime(2026, 3, 11, tzinfo=tz.utc),
        )

        result = reconcile_payout(company, payout)
        assert result.status == "partial"
        assert result.unmatched_transactions == 1

    def test_verification_state_updated(self, company, payout_with_transactions):
        """Reconciliation updates the verified flag on transactions."""
        txn = ShopifyPayoutTransaction.objects.get(shopify_transaction_id=80001)
        assert txn.verified is False  # Before reconciliation

        reconcile_payout(company, payout_with_transactions)

        txn.refresh_from_db()
        assert txn.verified is True
        assert txn.local_order is not None


class TestReconciliationSummary:

    def test_summary_aggregates_payouts(self, company, payout_with_transactions):
        summary = reconciliation_summary(
            company,
            date_from=date(2026, 3, 1),
            date_to=date(2026, 3, 31),
        )
        assert summary.total_payouts == 1
        assert summary.verified_payouts == 1
        assert summary.total_net == Decimal("120.00")
        assert summary.match_rate > Decimal("0")

    def test_summary_with_no_payouts(self, company):
        summary = reconciliation_summary(
            company,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
        )
        assert summary.total_payouts == 0
        assert summary.match_rate == Decimal("0")

    def test_unmatched_orders_counted(self, db, company, store, order_1001, order_1002):
        """Orders not in any payout transaction count as unmatched."""
        summary = reconciliation_summary(
            company,
            date_from=date(2026, 3, 1),
            date_to=date(2026, 3, 31),
        )
        # Both orders are unmatched since there are no payout transactions
        assert summary.unmatched_order_total == Decimal("150.00")


class TestReconciliationAPI:

    def test_payouts_list(self, authenticated_client, company, payout, owner_membership):
        resp = authenticated_client.get("/api/shopify/payouts/")
        assert resp.status_code == 200
        assert resp.data["total"] == 1
        assert resp.data["results"][0]["shopify_payout_id"] == 7001

    def test_reconciliation_summary_requires_dates(self, authenticated_client, owner_membership):
        resp = authenticated_client.get("/api/shopify/reconciliation/")
        assert resp.status_code == 400

    def test_reconciliation_summary_with_dates(self, authenticated_client, company, payout, owner_membership):
        resp = authenticated_client.get(
            "/api/shopify/reconciliation/",
            {"date_from": "2026-03-01", "date_to": "2026-03-31"},
        )
        assert resp.status_code == 200
        assert resp.data["total_payouts"] == 1

    def test_payout_detail_reconciliation(self, authenticated_client, company, payout_with_transactions, owner_membership):
        resp = authenticated_client.get("/api/shopify/reconciliation/7001/")
        assert resp.status_code == 200
        assert resp.data["status"] == "verified"
        assert len(resp.data["transactions"]) == 3

    def test_payout_not_found(self, authenticated_client, owner_membership):
        resp = authenticated_client.get("/api/shopify/reconciliation/99999/")
        assert resp.status_code == 404

    def test_transactions_include_variance(self, authenticated_client, company, payout_with_transactions, owner_membership):
        resp = authenticated_client.get(f"/api/shopify/payouts/{payout_with_transactions.shopify_payout_id}/transactions/")
        assert resp.status_code == 200
        for txn in resp.data["transactions"]:
            assert "variance" in txn
            assert "matched" in txn


class TestNegativePayout:
    """Test reconciliation with negative payouts (deductions exceed charges)."""

    def test_negative_payout_reconciles(self, db, company, store):
        """A payout with negative net should still reconcile."""
        payout = ShopifyPayout.objects.create(
            company=company,
            store=store,
            shopify_payout_id=7200,
            gross_amount=Decimal("-50.00"),
            fees=Decimal("0"),
            net_amount=Decimal("-50.00"),
            currency="USD",
            shopify_status="paid",
            payout_date=date(2026, 3, 15),
        )
        ShopifyPayoutTransaction.objects.create(
            company=company,
            payout=payout,
            shopify_transaction_id=95001,
            transaction_type=ShopifyPayoutTransaction.TransactionType.REFUND,
            amount=Decimal("-50.00"),
            fee=Decimal("0"),
            net=Decimal("-50.00"),
            currency="USD",
            source_order_id=9001,
            source_type="refund",
            processed_at=datetime(2026, 3, 15, tzinfo=tz.utc),
        )

        result = reconcile_payout(company, payout)
        assert result.total_transactions == 1
        assert result.net_variance == Decimal("0")
        assert result.status in ("verified", "partial")


class TestMultipleRefunds:
    """Test matching when an order has multiple refunds."""

    def test_multiple_refunds_matched_by_amount(self, db, company, store, order_1001):
        """When multiple refunds exist, match to closest by amount."""
        from shopify_connector.models import ShopifyRefund

        # Two refunds on same order: $10 and $15
        ShopifyRefund.objects.create(
            company=company, order=order_1001,
            shopify_refund_id=5010, amount=Decimal("10.00"),
            currency="USD", shopify_created_at=datetime(2026, 3, 3, tzinfo=tz.utc),
        )
        ShopifyRefund.objects.create(
            company=company, order=order_1001,
            shopify_refund_id=5011, amount=Decimal("15.00"),
            currency="USD", shopify_created_at=datetime(2026, 3, 4, tzinfo=tz.utc),
        )

        payout = ShopifyPayout.objects.create(
            company=company, store=store,
            shopify_payout_id=7300,
            gross_amount=Decimal("75.00"), fees=Decimal("2.00"),
            net_amount=Decimal("73.00"), currency="USD",
            shopify_status="paid", payout_date=date(2026, 3, 10),
        )
        # Refund txn for $15 — should match the $15 refund, not the $10 one
        txn = ShopifyPayoutTransaction.objects.create(
            company=company, payout=payout,
            shopify_transaction_id=96001,
            transaction_type=ShopifyPayoutTransaction.TransactionType.REFUND,
            amount=Decimal("-15.00"), fee=Decimal("0"), net=Decimal("-15.00"),
            currency="USD", source_order_id=9001, source_type="refund",
            processed_at=datetime(2026, 3, 10, tzinfo=tz.utc),
        )

        result = reconcile_payout(company, payout)
        refund_match = next(
            m for m in result.transaction_matches
            if m.shopify_transaction_id == 96001
        )
        assert refund_match.matched is True
        assert refund_match.variance == Decimal("0")
        assert "5011" in refund_match.matched_to
