# tests/test_je_builder_fx_quarantine.py
"""
Regression: a FOREIGN journal entry (e.g. a USD Stripe charge in an EGP-functional
company) must NOT silently post at 1:1 when no exchange rate is configured — that
booked USD 20 as EGP 20 (live 2026-06-29, JE-000066). build_journal_entry now:

  * converts every line into the functional currency when a rate exists, and
  * quarantines the entry as INCOMPLETE ("needs FX rate") when none does,

so materially wrong amounts never enter the ledger.
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.models import Account, ExchangeRate, JournalEntry, JournalLine
from platform_connectors.je_builder import JELine, JERequest, build_journal_entry
from projections.write_barrier import projection_writes_allowed


def _egp_company(company):
    """Make `company` EGP-functional with a Stripe clearing + revenue account."""
    company.functional_currency = "EGP"
    company.save(update_fields=["functional_currency"])
    with projection_writes_allowed():
        clearing = Account.objects.create(
            company=company,
            code="11510",
            name="Stripe Clearing",
            account_type="ASSET",
            role="LIQUIDITY",
            ledger_domain="FINANCIAL",
            status="ACTIVE",
            normal_balance="DEBIT",
        )
        revenue = Account.objects.create(
            company=company,
            code="41000",
            name="Sales Revenue",
            account_type="REVENUE",
            role="SALES",
            ledger_domain="FINANCIAL",
            status="ACTIVE",
            normal_balance="CREDIT",
        )
    return clearing, revenue


def _usd_request(company, clearing, revenue, memo):
    return JERequest(
        company=company,
        entry_date=date(2026, 6, 29),
        memo=memo,
        source_module="platform_stripe",
        currency="USD",  # foreign to the EGP book currency
        lines=[
            JELine(account=clearing, description=memo, debit=Decimal("20")),
            JELine(account=revenue, description=memo, credit=Decimal("20")),
        ],
    )


@pytest.mark.django_db
def test_usd_je_in_egp_company_quarantined_when_no_fx_rate(company):
    """No USD→EGP rate → entry is INCOMPLETE, NOT posted at 1:1 (USD 20 ≠ EGP 20)."""
    clearing, revenue = _egp_company(company)

    entry = build_journal_entry(_usd_request(company, clearing, revenue, "Stripe order: ch_no_rate"))

    assert entry is not None
    assert entry.status == JournalEntry.Status.INCOMPLETE
    assert not JournalEntry.objects.filter(
        company=company, memo="Stripe order: ch_no_rate", status=JournalEntry.Status.POSTED
    ).exists()
    assert not entry.entry_number  # never assigned a posted number


@pytest.mark.django_db
def test_usd_je_in_egp_company_converts_when_fx_rate_exists(company):
    """With a USD→EGP rate on file, USD 20 posts as EGP 960 (20 × 48), not EGP 20."""
    clearing, revenue = _egp_company(company)
    ExchangeRate.objects.create(
        company=company,
        from_currency="USD",
        to_currency="EGP",
        rate=Decimal("48"),
        effective_date=date(2026, 6, 1),
        rate_type="SPOT",
    )

    entry = build_journal_entry(_usd_request(company, clearing, revenue, "Stripe order: ch_with_rate"))

    assert entry is not None
    assert entry.status == JournalEntry.Status.POSTED
    lines = {ln.account.code: ln for ln in JournalLine.objects.filter(entry=entry).select_related("account")}
    assert lines["11510"].debit == Decimal("960.00")  # 20 USD × 48 → EGP, NOT 20
    assert lines["41000"].credit == Decimal("960.00")
    assert lines["11510"].amount_currency == Decimal("20")  # foreign USD amount preserved
