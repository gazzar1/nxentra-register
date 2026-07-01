# tests/test_fx_je_currency_fallback.py
"""
Regression: JEs were stamped with company.default_currency instead of
functional_currency when callers didn't pass an explicit currency.

This was surfaced on the App Store reviewer's Shopify_R company on
2026-06-04: company configured as default_currency=USD,
functional_currency=EGP. An EGP order #1006 produced a USD-stamped JE
even though the SalesInvoice was correctly EGP. The reviewer would have
seen "EGP 500 order → USD 500 JE", drawn the obvious conclusion the app
was broken, and rejected for a third time.

Two fixes:
  1. accounting/commands.py:create_journal_entry — fall back to
     functional_currency before default_currency.
  2. sales/commands.py:post_sales_invoice and credit-note posting —
     always pass invoice currency to create_journal_entry (don't gate
     on `is_foreign`, which is False whenever invoice currency equals
     functional, even when that differs from default).
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from accounting.commands import create_journal_entry, post_journal_entry, save_journal_entry_complete
from accounting.models import Account
from accounts.authz import system_actor_for_company
from accounts.models import Company, CompanyMembership
from projections.write_barrier import projection_writes_allowed


@pytest.fixture
def usd_default_egp_functional_company(db):
    """A legitimate multinational config: USD presentation, EGP books."""
    User = get_user_model()
    uid = uuid4().hex[:8]

    company = Company.objects.create(
        public_id=uuid4(),
        name=f"USD-EGP Co {uid}",
        slug=f"usd-egp-{uid}",
        default_currency="USD",
        functional_currency="EGP",
        is_active=True,
    )
    user = User.objects.create_user(
        public_id=uuid4(),
        email=f"owner-fx-{uid}@test.com",
        password="testpass123",
        name="FX Owner",
    )
    user.active_company = company
    user.save()
    CompanyMembership.objects.create(
        public_id=uuid4(),
        company=company,
        user=user,
        role=CompanyMembership.Role.OWNER,
        is_active=True,
    )

    # Open fiscal period for today
    import calendar

    from projections.models import FiscalPeriod

    today = date.today()
    last_day = calendar.monthrange(today.year, today.month)[1]
    with projection_writes_allowed():
        FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=today.year,
            period=today.month,
            defaults=dict(
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=today.replace(day=1),
                end_date=today.replace(day=last_day),
                status=FiscalPeriod.Status.OPEN,
            ),
        )

    # Two GL accounts so we can draft a balanced JE
    with projection_writes_allowed():
        Account.objects.projection().create(
            company=company,
            code="1010",
            name="Bank EGP",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        Account.objects.projection().create(
            company=company,
            code="4000",
            name="Sales Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )

    return company


@pytest.mark.django_db
def test_create_journal_entry_defaults_to_functional_not_presentation(
    usd_default_egp_functional_company,
):
    # Reviewer's bug: when no currency is passed, JEs used to default to
    # default_currency (USD), producing USD-stamped JEs for EGP companies.
    # The fix falls back to functional_currency first.
    company = usd_default_egp_functional_company
    actor = system_actor_for_company(company)
    bank = Account.objects.get(company=company, code="1010")
    revenue = Account.objects.get(company=company, code="4000")

    result = create_journal_entry(
        actor=actor,
        date=date.today(),
        memo="Test default currency fallback",
        lines=[
            {"account_id": bank.id, "description": "DR Bank", "debit": Decimal("500"), "credit": Decimal("0")},
            {"account_id": revenue.id, "description": "CR Revenue", "debit": Decimal("0"), "credit": Decimal("500")},
        ],
    )

    assert result.success, f"create_journal_entry failed: {result.error}"
    entry = result.data
    assert entry.currency == "EGP", (
        f"JE.currency should default to functional (EGP), "
        f"got {entry.currency!r} — the App-Store rejection bug regressed"
    )


@pytest.mark.django_db
def test_create_journal_entry_explicit_currency_still_wins(
    usd_default_egp_functional_company,
):
    # Manual JE in USD on an EGP-functional company is legitimate
    # (e.g. recording a USD-denominated bank deposit). The functional
    # fallback only applies when no currency is passed.
    company = usd_default_egp_functional_company
    actor = system_actor_for_company(company)
    bank = Account.objects.get(company=company, code="1010")
    revenue = Account.objects.get(company=company, code="4000")

    result = create_journal_entry(
        actor=actor,
        date=date.today(),
        memo="Foreign-denominated JE",
        currency="USD",
        exchange_rate="50.0",
        lines=[
            {"account_id": bank.id, "description": "DR Bank", "debit": Decimal("100"), "credit": Decimal("0")},
            {"account_id": revenue.id, "description": "CR Revenue", "debit": Decimal("0"), "credit": Decimal("100")},
        ],
    )

    assert result.success
    assert result.data.currency == "USD"


@pytest.mark.django_db
def test_post_foreign_entry_without_rate_is_quarantined(usd_default_egp_functional_company):
    # The choke point (PR-A): a foreign line (USD) in an EGP-functional company
    # with NO exchange rate on file must NOT post at a silent 1:1 (USD 20 -> EGP 20).
    # post_journal_entry now FAILS with a clear "add the rate, then repost" and
    # leaves the entry unposted (quarantined), mirroring build_journal_entry (#33).
    from accounting.models import JournalEntry

    company = usd_default_egp_functional_company
    actor = system_actor_for_company(company)
    bank = Account.objects.get(company=company, code="1010")
    revenue = Account.objects.get(company=company, code="4000")

    created = create_journal_entry(
        actor=actor,
        date=date.today(),
        memo="USD JE, no rate",
        currency="USD",  # foreign to functional EGP
        lines=[
            {"account_id": bank.id, "description": "DR Bank", "debit": Decimal("20"), "credit": Decimal("0")},
            {"account_id": revenue.id, "description": "CR Revenue", "debit": Decimal("0"), "credit": Decimal("20")},
        ],
    )
    assert created.success, created.error
    assert save_journal_entry_complete(actor, created.data.id).success  # INCOMPLETE -> DRAFT

    posted = post_journal_entry(actor, created.data.id)
    assert not posted.success
    assert "exchange rate" in (posted.error or "").lower()

    created.data.refresh_from_db()
    assert created.data.status != JournalEntry.Status.POSTED  # not booked at 1:1


@pytest.mark.django_db
def test_post_foreign_entry_with_rate_converts(usd_default_egp_functional_company):
    # With a USD->EGP rate on file, the same entry posts and every line converts
    # into the functional currency: USD 20 -> EGP 960 at 48 (NOT EGP 20).
    from accounting.models import ExchangeRate, JournalLine

    company = usd_default_egp_functional_company
    actor = system_actor_for_company(company)
    ExchangeRate.objects.create(
        company=company,
        from_currency="USD",
        to_currency="EGP",
        rate=Decimal("48"),
        effective_date=date.today().replace(day=1),
        rate_type="SPOT",
    )
    bank = Account.objects.get(company=company, code="1010")
    revenue = Account.objects.get(company=company, code="4000")

    created = create_journal_entry(
        actor=actor,
        date=date.today(),
        memo="USD JE, with rate",
        currency="USD",
        lines=[
            {"account_id": bank.id, "description": "DR Bank", "debit": Decimal("20"), "credit": Decimal("0")},
            {"account_id": revenue.id, "description": "CR Revenue", "debit": Decimal("0"), "credit": Decimal("20")},
        ],
    )
    assert created.success, created.error
    assert save_journal_entry_complete(actor, created.data.id).success  # INCOMPLETE -> DRAFT

    posted = post_journal_entry(actor, created.data.id)
    assert posted.success, posted.error

    lines = {
        ln.account.code: ln for ln in JournalLine.objects.filter(entry_id=created.data.id).select_related("account")
    }
    assert lines["1010"].debit == Decimal("960.00")  # 20 USD * 48 -> EGP, NOT 20
    assert lines["4000"].credit == Decimal("960.00")


# The full SalesInvoice → JE pipeline regression is covered indirectly by
# tests/test_shopify_pipeline_e2e.py running against the existing
# shopify_company fixture; that fixture is USD/USD so the bug wouldn't
# have surfaced there, but with the fallback corrected in
# create_journal_entry the path is structurally safe regardless of
# default/functional split. A dedicated end-to-end test against an
# EGP-functional Shopify company is tracked as a followup so we exercise
# the projection write path too, not just the accounting command.
