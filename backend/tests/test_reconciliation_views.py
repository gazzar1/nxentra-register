# tests/test_reconciliation_views.py
"""
A13 — Reconciliation Control Center MVP backing query.

Tests the per-(account, settlement_provider_dimension_value) pivot that
the Reconciliation Control Center renders. Hand-builds journal entries
with the same dimension-tagging shape that A12's projection produces,
then exercises the views through the API client.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.utils import timezone

from accounting.models import (
    Account,
    JournalEntry,
    JournalLine,
    JournalLineAnalysis,
)
from projections.write_barrier import projection_writes_allowed


@pytest.fixture
def reconciliation_setup(db, company, owner_membership):
    """Build a SETTLEMENT_PROVIDER dimension + values + clearing account
    + the seven default SettlementProvider rows for a company.

    Pulls in `owner_membership` so authenticated API calls in the same
    test pass `resolve_actor()` (it checks for an active membership).
    """
    from accounting.mappings import ModuleAccountMapping
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    with projection_writes_allowed():
        clearing = Account.objects.projection().create(
            company=company,
            code="11500",
            name="Shopify Clearing",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company,
            code="41000",
            name="Sales Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    ModuleAccountMapping.objects.create(
        company=company,
        module="shopify_connector",
        role="SHOPIFY_CLEARING",
        account=clearing,
    )
    ModuleAccountMapping.objects.create(
        company=company,
        module="shopify_connector",
        role="SALES_REVENUE",
        account=revenue,
    )
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="recon-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    # _ensure_shopify_sales_setup bootstraps the dimension, values, and
    # SettlementProvider rows for us.
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return {"store": store, "clearing": clearing, "revenue": revenue}


def _post_clearing_je(
    company,
    user,
    clearing,
    revenue,
    dimension_value,
    *,
    debit: Decimal,
    entry_date: date,
):
    """Create a posted JE: DR clearing / CR revenue, with the clearing
    line tagged by the provider's dimension value. Mimics what A12's
    projection produces for a Shopify order."""
    with projection_writes_allowed():
        entry = JournalEntry.objects.projection().create(
            public_id=uuid4(),
            company=company,
            date=entry_date,
            memo=f"Recon test entry {entry_date}",
            status=JournalEntry.Status.POSTED,
            posted_at=timezone.now(),
            posted_by=user,
            created_by=user,
            entry_number=f"JE-{entry_date.isoformat()}-{uuid4().hex[:6]}",
        )
        clearing_line = JournalLine.objects.projection().create(
            entry=entry,
            company=company,
            line_no=1,
            account=clearing,
            description="DR clearing",
            debit=debit,
            credit=Decimal("0"),
        )
        JournalLine.objects.projection().create(
            entry=entry,
            company=company,
            line_no=2,
            account=revenue,
            description="CR revenue",
            debit=Decimal("0"),
            credit=debit,
        )
        JournalLineAnalysis.objects.projection().create(
            journal_line=clearing_line,
            company=company,
            dimension=dimension_value.dimension,
            dimension_value=dimension_value,
        )
    return entry


def _post_clearing_credit(
    company,
    user,
    clearing,
    bank_account,
    dimension_value,
    *,
    credit: Decimal,
    entry_date: date,
):
    """Create a posted JE: DR bank / CR clearing — i.e. a settlement that
    drains some of the provider's clearing balance."""
    with projection_writes_allowed():
        entry = JournalEntry.objects.projection().create(
            public_id=uuid4(),
            company=company,
            date=entry_date,
            memo=f"Settlement {entry_date}",
            status=JournalEntry.Status.POSTED,
            posted_at=timezone.now(),
            posted_by=user,
            created_by=user,
            entry_number=f"JE-S-{entry_date.isoformat()}-{uuid4().hex[:6]}",
        )
        JournalLine.objects.projection().create(
            entry=entry,
            company=company,
            line_no=1,
            account=bank_account,
            description="DR bank",
            debit=credit,
            credit=Decimal("0"),
        )
        clearing_line = JournalLine.objects.projection().create(
            entry=entry,
            company=company,
            line_no=2,
            account=clearing,
            description="CR clearing",
            debit=Decimal("0"),
            credit=credit,
        )
        JournalLineAnalysis.objects.projection().create(
            journal_line=clearing_line,
            company=company,
            dimension=dimension_value.dimension,
            dimension_value=dimension_value,
        )
    return entry


# =============================================================================
# Summary view
# =============================================================================


def test_summary_returns_per_provider_balances(reconciliation_setup, company, user, authenticated_client):
    # Two paymob orders + one paypal order, no settlements yet.
    # Expect: paymob row with 2x debit, paypal row with 1x debit.
    from accounting.settlement_provider import SettlementProvider

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    paypal = SettlementProvider.objects.get(company=company, normalized_code="paypal")
    today = date.today()

    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("100.00"),
        entry_date=today - timedelta(days=2),
    )
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("50.00"),
        entry_date=today - timedelta(days=10),
    )
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paypal.dimension_value,
        debit=Decimal("75.00"),
        entry_date=today - timedelta(days=1),
    )

    response = authenticated_client.get("/api/accounting/reconciliation/summary/")
    assert response.status_code == 200
    body = response.json()

    providers = body["stage1"]["providers"]
    by_code = {row["dimension_value_code"]: row for row in providers}

    assert "PAYMOB" in by_code
    assert by_code["PAYMOB"]["total_debit"] == "150.00"
    assert by_code["PAYMOB"]["total_credit"] == "0.00"
    assert by_code["PAYMOB"]["open_balance"] == "150.00"
    assert by_code["PAYMOB"]["provider_type"] == "gateway"
    # Oldest paymob entry is 10 days back -> aging "7_30d"
    assert by_code["PAYMOB"]["aging_bucket"] == "7_30d"

    assert "PAYPAL" in by_code
    assert by_code["PAYPAL"]["total_debit"] == "75.00"
    assert by_code["PAYPAL"]["aging_bucket"] == "0_7d"

    totals = body["stage1"]["totals"]
    assert totals["total_expected"] == "225.00"
    assert totals["total_settled"] == "0.00"
    assert totals["open_balance"] == "225.00"
    assert totals["providers_with_open_balance"] == 2
    assert totals["providers_needing_review"] == 0


def test_summary_subtracts_settlements_from_open_balance(reconciliation_setup, company, user, authenticated_client):
    # paymob has 200 in clearing; a 150 settlement drains most of it.
    from accounting.settlement_provider import SettlementProvider

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    today = date.today()

    with projection_writes_allowed():
        bank = Account.objects.projection().create(
            company=company,
            code="10100",
            name="Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )

    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("200.00"),
        entry_date=today - timedelta(days=3),
    )
    _post_clearing_credit(
        company,
        user,
        reconciliation_setup["clearing"],
        bank,
        paymob.dimension_value,
        credit=Decimal("150.00"),
        entry_date=today - timedelta(days=1),
    )

    response = authenticated_client.get("/api/accounting/reconciliation/summary/")
    body = response.json()
    by_code = {row["dimension_value_code"]: row for row in body["stage1"]["providers"]}
    assert by_code["PAYMOB"]["total_debit"] == "200.00"
    assert by_code["PAYMOB"]["total_credit"] == "150.00"
    assert by_code["PAYMOB"]["open_balance"] == "50.00"


def test_summary_aged_30_plus_bucket_and_totals(reconciliation_setup, company, user, authenticated_client):
    # An entry 35 days old surfaces as aged_30_plus in the totals roll-up.
    from accounting.settlement_provider import SettlementProvider

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    today = date.today()

    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("500.00"),
        entry_date=today - timedelta(days=35),
    )

    response = authenticated_client.get("/api/accounting/reconciliation/summary/")
    body = response.json()
    by_code = {row["dimension_value_code"]: row for row in body["stage1"]["providers"]}
    assert by_code["PAYMOB"]["aging_bucket"] == "30_plus"
    assert body["stage1"]["totals"]["aged_30_plus"] == "500.00"


def test_summary_empty_when_no_dimension(db, company, owner_membership, authenticated_client):
    # Bootstrap hasn't run for this company — no SettlementProvider
    # dimension exists. Endpoint must return an empty stage1 cleanly,
    # not 500.
    response = authenticated_client.get("/api/accounting/reconciliation/summary/")
    assert response.status_code == 200
    body = response.json()
    assert body["stage1"]["providers"] == []
    assert body["stage1"]["totals"]["total_expected"] == "0.00"


def test_summary_excludes_unposted_entries(reconciliation_setup, company, user, authenticated_client):
    # An INCOMPLETE entry must NOT contribute to the reconciliation
    # snapshot — only POSTED money moves the merchant's actual position.
    from accounting.settlement_provider import SettlementProvider

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    today = date.today()

    with projection_writes_allowed():
        entry = JournalEntry.objects.projection().create(
            public_id=uuid4(),
            company=company,
            date=today,
            memo="Draft entry",
            status=JournalEntry.Status.INCOMPLETE,
            created_by=user,
        )
        line = JournalLine.objects.projection().create(
            entry=entry,
            company=company,
            line_no=1,
            account=reconciliation_setup["clearing"],
            description="DR clearing",
            debit=Decimal("999.00"),
            credit=Decimal("0"),
        )
        JournalLineAnalysis.objects.projection().create(
            journal_line=line,
            company=company,
            dimension=paymob.dimension_value.dimension,
            dimension_value=paymob.dimension_value,
        )

    response = authenticated_client.get("/api/accounting/reconciliation/summary/")
    body = response.json()
    by_code = {row["dimension_value_code"]: row for row in body["stage1"]["providers"]}
    # paymob row should not appear at all (incomplete entry doesn't count)
    assert "PAYMOB" not in by_code


# =============================================================================
# Drilldown view
# =============================================================================


def test_drilldown_returns_lines_with_running_balance(reconciliation_setup, company, user, authenticated_client):
    from accounting.settlement_provider import SettlementProvider

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    today = date.today()

    with projection_writes_allowed():
        bank = Account.objects.projection().create(
            company=company,
            code="10100",
            name="Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )

    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("100.00"),
        entry_date=today - timedelta(days=3),
    )
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("50.00"),
        entry_date=today - timedelta(days=2),
    )
    _post_clearing_credit(
        company,
        user,
        reconciliation_setup["clearing"],
        bank,
        paymob.dimension_value,
        credit=Decimal("80.00"),
        entry_date=today - timedelta(days=1),
    )

    response = authenticated_client.get(f"/api/accounting/reconciliation/drilldown/?provider_id={paymob.id}")
    assert response.status_code == 200
    body = response.json()
    lines = body["lines"]
    assert len(lines) == 3
    # Lines come back ordered by date — running balance should be 100, 150, 70.
    assert lines[0]["debit"] == "100.00"
    assert lines[0]["running_balance"] == "100.00"
    assert lines[1]["debit"] == "50.00"
    assert lines[1]["running_balance"] == "150.00"
    assert lines[2]["credit"] == "80.00"
    assert lines[2]["running_balance"] == "70.00"
    assert body["open_balance"] == "70.00"


def test_drilldown_requires_provider_id(authenticated_client, company, owner_membership):
    response = authenticated_client.get("/api/accounting/reconciliation/drilldown/")
    assert response.status_code == 400


def test_drilldown_404_for_unknown_provider(authenticated_client, company, owner_membership):
    response = authenticated_client.get("/api/accounting/reconciliation/drilldown/?provider_id=999999")
    assert response.status_code == 404
