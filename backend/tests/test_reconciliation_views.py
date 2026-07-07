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
            period=entry_date.month,
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
            period=entry_date.month,
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


def test_summary_labels_money_with_functional_currency(reconciliation_setup, company, user, authenticated_client):
    """A143 review: stage-1 totals are functional-currency magnitudes
    (post_journal_entry stores all balances in the books currency), so the
    narrative and Money Bridge must label them functional-first — on the
    live default=USD/functional=EGP shape the narrative used to read
    '… USD sold' over EGP book amounts."""
    from accounting.settlement_provider import SettlementProvider

    company.default_currency = "USD"
    company.functional_currency = "EGP"
    company.save(update_fields=["default_currency", "functional_currency"])

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("100.00"),
        entry_date=date.today(),
    )

    response = authenticated_client.get("/api/accounting/reconciliation/summary/")
    assert response.status_code == 200
    body = response.json()
    assert "EGP sold" in body["narrative"]
    assert "USD" not in body["narrative"]
    assert body["money_flow"]["currency"] == "EGP"


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
            period=today.month,
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


# =============================================================================
# A152 — period-windowed flows, timeless stocks, roll-forward, FIFO aging
# =============================================================================


def _make_bank(company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


def _make_clearing2(company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="11501",
            name="Shopify Clearing 2",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


def _post_platform_refund(
    company, user, clearing, debit_account, dimension_value, *, credit: Decimal, entry_date: date
):
    """A platform refund JE (source_module ``platform_*``): CR clearing tagged
    with the provider. Picked up by _refunded_by_provider's platform_ branch."""
    with projection_writes_allowed():
        entry = JournalEntry.objects.projection().create(
            public_id=uuid4(),
            company=company,
            date=entry_date,
            period=entry_date.month,
            memo=f"Platform refund {entry_date}",
            status=JournalEntry.Status.POSTED,
            source_module="platform_paymob",
            posted_at=timezone.now(),
            posted_by=user,
            created_by=user,
            entry_number=f"JE-R-{entry_date.isoformat()}-{uuid4().hex[:6]}",
        )
        JournalLine.objects.projection().create(
            entry=entry,
            company=company,
            line_no=1,
            account=debit_account,
            description="DR refund",
            debit=credit,
            credit=Decimal("0"),
        )
        clearing_line = JournalLine.objects.projection().create(
            entry=entry,
            company=company,
            line_no=2,
            account=clearing,
            description="CR clearing (refund)",
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


def test_multi_account_provider_refund_not_double_counted(reconciliation_setup, company, user, authenticated_client):
    """A152 review Finding 2: a provider spanning two clearing accounts must not
    double-count its refund total — money_flow, roll-forward, and the stock
    open_balance must all foot at the same closing figure."""
    from accounting.settlement_provider import SettlementProvider

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    clearing2 = _make_clearing2(company)

    # Account A: 200 sold, no refund. Account B: a 30 platform refund, no sale.
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("200.00"),
        entry_date=date.today() - timedelta(days=3),
    )
    _post_platform_refund(
        company,
        user,
        clearing2,
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        credit=Decimal("30.00"),
        entry_date=date.today() - timedelta(days=1),
    )

    body = authenticated_client.get("/api/accounting/reconciliation/summary/").json()
    # Refund attributed to account B only — total is 30, not 60.
    assert body["stage1"]["totals"]["total_refunded"] == "30.00"
    # Everything foots at closing = 200 − 30 = 170.
    assert body["stage1"]["totals"]["open_balance"] == "170.00"
    mf = body["money_flow"]
    open_seg = {s["key"]: s["amount"] for s in mf["segments"]}["open"]
    assert open_seg == "170.00"
    assert mf["closing_outstanding"] == "170.00"
    rf = body["roll_forward"]
    assert rf["refunded"] == "30.00"
    assert rf["closing_outstanding"] == "170.00"
    assert rf["foots"] is True


def test_default_no_params_is_all_time_and_exposes_roll_forward(
    reconciliation_setup, company, user, authenticated_client
):
    """No params => unbounded (all_time), backward-compatible, and the response
    now carries the period echo + roll-forward endpoints on money_flow."""
    from accounting.settlement_provider import SettlementProvider

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("100.00"),
        entry_date=date.today() - timedelta(days=3),
    )

    body = authenticated_client.get("/api/accounting/reconciliation/summary/").json()
    assert body["period"] == {"preset": "all_time", "start": None, "end": None}
    mf = body["money_flow"]
    assert mf["opening_outstanding"] == "0.00"  # nothing before an unbounded window
    assert mf["closing_outstanding"] == "100.00"  # == current outstanding for all_time
    rf = body["roll_forward"]
    assert rf["opening_outstanding"] == "0.00"
    assert rf["sold"] == "100.00"
    assert rf["closing_outstanding"] == "100.00"
    assert rf["foots"] is True


def test_flows_windowed_stock_timeless(reconciliation_setup, company, user, authenticated_client):
    """A June residual is still outstanding in December: windowing the flow
    columns must NOT shrink the open-balance stock."""
    from accounting.settlement_provider import SettlementProvider

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    today = date.today()
    last_month_day = today.replace(day=1) - timedelta(days=1)

    # 300 sold last month, 200 sold this month.
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("300.00"),
        entry_date=last_month_day,
    )
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("200.00"),
        entry_date=today.replace(day=1),
    )

    body = authenticated_client.get("/api/accounting/reconciliation/summary/?period=this_month").json()
    row = {r["dimension_value_code"]: r for r in body["stage1"]["providers"]}["PAYMOB"]
    # FLOW: only this month's sale counts.
    assert row["total_debit"] == "200.00"
    # STOCK: the whole outstanding position, unfiltered by the window.
    assert row["open_balance"] == "500.00"
    assert body["stage1"]["totals"]["total_expected"] == "200.00"  # windowed flow
    assert body["stage1"]["totals"]["open_balance"] == "500.00"  # timeless stock


def test_roll_forward_foots_with_opening_carryover(reconciliation_setup, company, user, authenticated_client):
    from accounting.settlement_provider import SettlementProvider

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    bank = _make_bank(company)
    today = date.today()
    last_month_day = today.replace(day=1) - timedelta(days=1)

    # Opening carryover: 300 sold last month, undrained.
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("300.00"),
        entry_date=last_month_day,
    )
    # This month: sell 200, settle 100.
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("200.00"),
        entry_date=today.replace(day=1),
    )
    _post_clearing_credit(
        company,
        user,
        reconciliation_setup["clearing"],
        bank,
        paymob.dimension_value,
        credit=Decimal("100.00"),
        entry_date=today,
    )

    rf = authenticated_client.get("/api/accounting/reconciliation/summary/?period=this_month").json()["roll_forward"]
    assert rf["opening_outstanding"] == "300.00"  # carried in from last month
    assert rf["sold"] == "200.00"
    assert rf["settled"] == "100.00"
    assert rf["refunded"] == "0.00"
    # 300 + 200 − 100 − 0 = 400, and window_end covers all activity so it equals
    # the current outstanding too.
    assert rf["closing_outstanding"] == "400.00"
    assert rf["foots"] is True


def test_roll_forward_closing_diverges_from_stock_for_past_window(
    reconciliation_setup, company, user, authenticated_client
):
    """Closing (as-of window_end) is a different, correct number from the
    timeless open-balance stock (as-of today) when the window ends in the past."""
    from accounting.settlement_provider import SettlementProvider

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    today = date.today()
    last_month_day = today.replace(day=1) - timedelta(days=1)

    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("300.00"),
        entry_date=last_month_day,
    )
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("200.00"),
        entry_date=today.replace(day=1),
    )

    body = authenticated_client.get("/api/accounting/reconciliation/summary/?period=last_month").json()
    rf = body["roll_forward"]
    assert rf["opening_outstanding"] == "0.00"
    assert rf["sold"] == "300.00"  # only last month's sale is in-window
    assert rf["closing_outstanding"] == "300.00"  # outstanding as of end of last month
    # ...but the timeless stock is the full current position.
    assert body["stage1"]["totals"]["open_balance"] == "500.00"


def test_aging_fifo_ages_oldest_unsettled_not_first_activity(reconciliation_setup, company, user, authenticated_client):
    """A152 item 2: an old, fully-settled sale plus a tiny recent residual must
    age from the residual (0-7d), NOT the provider's first-ever entry (which the
    old Min(entry__date) proxy would have reported as 30+)."""
    from accounting.settlement_provider import SettlementProvider

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    bank = _make_bank(company)
    today = date.today()

    # Old sale, fully settled a long time ago.
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("100.00"),
        entry_date=today - timedelta(days=400),
    )
    _post_clearing_credit(
        company,
        user,
        reconciliation_setup["clearing"],
        bank,
        paymob.dimension_value,
        credit=Decimal("100.00"),
        entry_date=today - timedelta(days=390),
    )
    # Recent, still-unsettled residual.
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("50.00"),
        entry_date=today - timedelta(days=2),
    )

    body = authenticated_client.get("/api/accounting/reconciliation/summary/").json()
    row = {r["dimension_value_code"]: r for r in body["stage1"]["providers"]}["PAYMOB"]
    assert row["open_balance"] == "50.00"
    assert row["aging_bucket"] == "0_7d"  # RED under the old lifetime-Min proxy
    assert row["days_outstanding"] <= 3
    assert row["oldest_entry_date"] == (today - timedelta(days=2)).isoformat()
    assert body["stage1"]["totals"]["aged_30_plus"] == "0.00"


def test_fully_covered_provider_has_no_aging(reconciliation_setup, company, user, authenticated_client):
    """Over-drained / fully-settled provider ages to 'none' (no positive residual)."""
    from accounting.settlement_provider import SettlementProvider

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    bank = _make_bank(company)
    today = date.today()

    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("100.00"),
        entry_date=today - timedelta(days=40),
    )
    _post_clearing_credit(
        company,
        user,
        reconciliation_setup["clearing"],
        bank,
        paymob.dimension_value,
        credit=Decimal("100.00"),
        entry_date=today - timedelta(days=1),
    )

    body = authenticated_client.get("/api/accounting/reconciliation/summary/").json()
    row = {r["dimension_value_code"]: r for r in body["stage1"]["providers"]}["PAYMOB"]
    assert row["open_balance"] == "0.00"
    assert row["aging_bucket"] == "none"
    assert row["oldest_entry_date"] is None


def test_custom_window_open_ended_from_only(reconciliation_setup, company, user, authenticated_client):
    """A custom window with only date_from (no date_to) must not 500 and windows
    correctly (>= from)."""
    from accounting.settlement_provider import SettlementProvider

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    today = date.today()

    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("70.00"),
        entry_date=today - timedelta(days=40),
    )
    _post_clearing_je(
        company,
        user,
        reconciliation_setup["clearing"],
        reconciliation_setup["revenue"],
        paymob.dimension_value,
        debit=Decimal("30.00"),
        entry_date=today - timedelta(days=5),
    )

    df = (today - timedelta(days=10)).isoformat()
    resp = authenticated_client.get(f"/api/accounting/reconciliation/summary/?date_from={df}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["period"]["preset"] == "custom"
    assert body["period"]["start"] == df
    assert body["period"]["end"] is None
    row = {r["dimension_value_code"]: r for r in body["stage1"]["providers"]}["PAYMOB"]
    assert row["total_debit"] == "30.00"  # only the in-window sale
    assert row["open_balance"] == "100.00"  # stock unaffected
