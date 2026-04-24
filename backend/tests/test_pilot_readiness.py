# tests/test_pilot_readiness.py
"""
Tests for the Gate C pilot_readiness management command.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from shopify_connector.models import (
    ShopifyOrder,
    ShopifyPayout,
    ShopifyPayoutTransaction,
    ShopifyStore,
)

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def store(db, company):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="pilot-store.myshopify.com",
        status=ShopifyStore.Status.ACTIVE,
        webhooks_registered=True,
    )


@pytest.fixture
def order(db, company, store):
    return ShopifyOrder.objects.create(
        company=company,
        store=store,
        shopify_order_id=1001,
        shopify_order_number="1001",
        shopify_order_name="#1001",
        total_price=Decimal("100.00"),
        subtotal_price=Decimal("95.00"),
        total_tax=Decimal("5.00"),
        currency="USD",
        financial_status="paid",
        shopify_created_at=datetime(2026, 3, 1, tzinfo=UTC),
        order_date=date(2026, 3, 1),
        status=ShopifyOrder.Status.PROCESSED,
    )


@pytest.fixture
def settled_payout(db, company, store, order):
    """A fully reconciled payout with one matching charge."""
    payout = ShopifyPayout.objects.create(
        company=company,
        store=store,
        shopify_payout_id=7001,
        gross_amount=Decimal("100.00"),
        fees=Decimal("3.00"),
        net_amount=Decimal("97.00"),
        currency="USD",
        shopify_status="paid",
        payout_date=date(2026, 3, 5),
    )
    ShopifyPayoutTransaction.objects.create(
        company=company,
        payout=payout,
        shopify_transaction_id=80001,
        transaction_type=ShopifyPayoutTransaction.TransactionType.CHARGE,
        amount=Decimal("100.00"),
        fee=Decimal("-3.00"),
        net=Decimal("97.00"),
        currency="USD",
        source_order_id=1001,
        source_type="order",
        processed_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    return payout


# ── Tests ────────────────────────────────────────────────────────


class TestPilotReadinessCommand:
    def test_runs_without_error(self, db, company, store):
        """Command executes and produces output."""
        out = StringIO()
        call_command(
            "pilot_readiness",
            company=company.slug,
            year=2026,
            month=3,
            stdout=out,
        )
        output = out.getvalue()
        assert "Gate C" in output

    def test_json_output(self, db, company, store):
        """JSON mode produces valid JSON with expected fields."""
        import json

        out = StringIO()
        call_command(
            "pilot_readiness",
            company=company.slug,
            year=2026,
            month=3,
            json=True,
            stdout=out,
        )
        data = json.loads(out.getvalue())
        assert data["company"] == company.slug
        assert data["period"] == "2026-03"
        assert "checks" in data
        assert "gate_c" in data
        assert data["passed"] + data["warned"] + data["failed"] == len(data["checks"])

    def test_store_check_passes(self, db, company, store):
        """Active store with webhooks passes the store check."""
        import json

        out = StringIO()
        call_command(
            "pilot_readiness",
            company=company.slug,
            year=2026,
            month=3,
            json=True,
            stdout=out,
        )
        data = json.loads(out.getvalue())
        store_check = next(c for c in data["checks"] if c["check"] == "shopify_store")
        assert store_check["status"] == "PASS"

    def test_no_store_fails(self, db, company):
        """No Shopify store connected results in FAIL."""
        import json

        out = StringIO()
        call_command(
            "pilot_readiness",
            company=company.slug,
            year=2026,
            month=3,
            json=True,
            stdout=out,
        )
        data = json.loads(out.getvalue())
        store_check = next(c for c in data["checks"] if c["check"] == "shopify_store")
        assert store_check["status"] == "FAIL"

    def test_trial_balance_passes_with_no_entries(self, db, company, store):
        """Trial balance is trivially balanced when there are no entries."""
        import json

        out = StringIO()
        call_command(
            "pilot_readiness",
            company=company.slug,
            year=2026,
            month=3,
            json=True,
            stdout=out,
        )
        data = json.loads(out.getvalue())
        tb = next(c for c in data["checks"] if c["check"] == "trial_balance")
        assert tb["status"] == "PASS"

    def test_reconciliation_warns_with_no_payouts(self, db, company, store):
        """No payouts in period gives a WARN, not FAIL."""
        import json

        out = StringIO()
        call_command(
            "pilot_readiness",
            company=company.slug,
            year=2026,
            month=3,
            json=True,
            stdout=out,
        )
        data = json.loads(out.getvalue())
        recon = next(c for c in data["checks"] if c["check"] == "reconciliation")
        assert recon["status"] == "WARN"

    def test_strict_mode_exits_on_failure(self, db, company):
        """Strict mode exits with code 1 when there are failures."""
        out = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            call_command(
                "pilot_readiness",
                company=company.slug,
                year=2026,
                month=3,
                strict=True,
                stdout=out,
            )
        assert exc_info.value.code == 1

    def test_eight_checks_run(self, db, company, store):
        """All 8 checks are present in the report."""
        import json

        out = StringIO()
        call_command(
            "pilot_readiness",
            company=company.slug,
            year=2026,
            month=3,
            json=True,
            stdout=out,
        )
        data = json.loads(out.getvalue())
        assert len(data["checks"]) == 8
        check_names = {c["check"] for c in data["checks"]}
        expected = {
            "shopify_store",
            "account_mapping",
            "projection_lag",
            "reconciliation",
            "clearing_balance",
            "subledger_tieout",
            "trial_balance",
            "draft_entries",
        }
        assert check_names == expected
