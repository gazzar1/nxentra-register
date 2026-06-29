# tests/test_a137_account_inquiry.py
"""
A137 — Account Inquiry (read-only GL account drilldown).

Covers the pure query module (``accounting.account_inquiry``) for balance /
running-balance math, debit- vs credit-normal accounts, date-range filtering,
posted-only semantics (incl. reversed entries), dimension display + filtering,
empty accounts and pagination; plus the API view for permissioning, tenant
isolation, and the read-only guarantee (no events / no mutation).
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.urls import reverse
from django.utils import timezone

from accounting.account_drilldown import build_account_drilldown
from accounting.models import (
    Account,
    AnalysisDimension,
    AnalysisDimensionValue,
    JournalEntry,
    JournalLine,
    JournalLineAnalysis,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _account(company, code, name, account_type):
    """Create a postable account. normal_balance is derived from the type."""
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code=code,
        name=name,
        account_type=account_type,
        status=Account.Status.ACTIVE,
    )


def _je(company, user, d, lines, *, status=JournalEntry.Status.POSTED, kind=JournalEntry.Kind.NORMAL, reverses=None):
    """Post a balanced journal entry.

    ``lines`` is a list of ``(account, debit, credit, dim_value_or_None)``.
    """
    entry = JournalEntry.objects.create(
        public_id=uuid4(),
        company=company,
        date=d,
        period=d.month,
        memo=f"Entry {d.isoformat()}",
        status=status,
        kind=kind,
        reverses_entry=reverses,
        posted_at=timezone.now() if status == JournalEntry.Status.POSTED else None,
        posted_by=user if status == JournalEntry.Status.POSTED else None,
        created_by=user,
        entry_number=f"JE-{uuid4().hex[:8]}",
    )
    for idx, (account, debit, credit, dim_value) in enumerate(lines, start=1):
        line = JournalLine.objects.create(
            entry=entry,
            company=company,
            line_no=idx,
            account=account,
            description=f"line {idx}",
            debit=Decimal(debit),
            credit=Decimal(credit),
        )
        if dim_value is not None:
            JournalLineAnalysis.objects.create(
                journal_line=line,
                company=company,
                dimension=dim_value.dimension,
                dimension_value=dim_value,
            )
    return entry


def _provider_dimension(company):
    dim = AnalysisDimension.objects.create(
        public_id=uuid4(),
        company=company,
        code="SETTLEMENT_PROVIDER",
        name="Settlement Provider",
    )
    stripe = AnalysisDimensionValue.objects.create(
        public_id=uuid4(),
        company=company,
        dimension=dim,
        code="STRIPE",
        name="Stripe",
    )
    paymob = AnalysisDimensionValue.objects.create(
        public_id=uuid4(),
        company=company,
        dimension=dim,
        code="PAYMOB",
        name="Paymob",
    )
    return dim, stripe, paymob


@pytest.fixture
def chart(db, company):
    """A minimal chart: a debit-normal clearing asset, a credit-normal VAT
    liability, a sales revenue account, and a bank asset."""
    return {
        "clearing": _account(company, "11510", "Stripe Clearing", Account.AccountType.ASSET),
        "bank": _account(company, "11000", "Bank", Account.AccountType.ASSET),
        "sales": _account(company, "41000", "Sales Revenue", Account.AccountType.REVENUE),
        "vat": _account(company, "22000", "VAT Payable", Account.AccountType.LIABILITY),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Balance math (debit-normal)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDebitNormalMath:
    def test_opening_period_closing_and_running_balance(self, company, user, chart):
        clearing, sales, bank = chart["clearing"], chart["sales"], chart["bank"]
        _je(company, user, date(2026, 1, 5), [(clearing, "100", "0", None), (sales, "0", "100", None)])
        _je(company, user, date(2026, 1, 10), [(clearing, "50", "0", None), (sales, "0", "50", None)])
        _je(company, user, date(2026, 1, 20), [(bank, "30", "0", None), (clearing, "0", "30", None)])

        result = build_account_drilldown(
            company=company,
            account=clearing,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
        )

        summary = result["summary"]
        assert summary["opening_balance"] == "0.00"
        assert summary["period_debits"] == "150.00"
        assert summary["period_credits"] == "30.00"
        assert summary["closing_balance"] == "120.00"
        assert summary["closing_balance_side"] == "DEBIT"

        running = [(r["debit"], r["credit"], r["running_balance"]) for r in result["rows"]]
        assert running == [
            ("100.00", "0.00", "100.00"),
            ("50.00", "0.00", "150.00"),
            ("0.00", "30.00", "120.00"),
        ]
        assert all(r["running_balance_side"] == "DEBIT" for r in result["rows"])

    def test_opening_balance_carries_prior_movement(self, company, user, chart):
        clearing, sales, bank = chart["clearing"], chart["sales"], chart["bank"]
        _je(company, user, date(2026, 1, 5), [(clearing, "100", "0", None), (sales, "0", "100", None)])
        _je(company, user, date(2026, 1, 10), [(clearing, "50", "0", None), (sales, "0", "50", None)])
        _je(company, user, date(2026, 1, 20), [(bank, "30", "0", None), (clearing, "0", "30", None)])

        # Period starts mid-month: the first two entries become the opening.
        result = build_account_drilldown(
            company=company,
            account=clearing,
            date_from=date(2026, 1, 15),
            date_to=date(2026, 1, 31),
        )

        assert result["summary"]["opening_balance"] == "150.00"
        assert result["summary"]["period_debits"] == "0.00"
        assert result["summary"]["period_credits"] == "30.00"
        assert result["summary"]["closing_balance"] == "120.00"
        assert [r["running_balance"] for r in result["rows"]] == ["120.00"]


# ─────────────────────────────────────────────────────────────────────────────
# Credit-normal accounts
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestCreditNormalMath:
    def test_credit_balance_reported_on_normal_side(self, company, user, chart):
        vat, bank = chart["vat"], chart["bank"]
        _je(company, user, date(2026, 2, 1), [(bank, "200", "0", None), (vat, "0", "200", None)])
        _je(company, user, date(2026, 2, 10), [(vat, "50", "0", None), (bank, "0", "50", None)])

        result = build_account_drilldown(company=company, account=vat)

        summary = result["summary"]
        # Credit-normal: a credit balance is a POSITIVE normal-side amount.
        assert summary["closing_balance"] == "150.00"
        assert summary["closing_balance_side"] == "CREDIT"
        assert result["account"]["normal_side"] == "CREDIT"
        # Running balance: +200 credit, then -50 → 150 credit.
        assert [(r["running_balance"], r["running_balance_side"]) for r in result["rows"]] == [
            ("200.00", "CREDIT"),
            ("150.00", "CREDIT"),
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Date-range, posted-only, reversals
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestFilters:
    def test_date_range_excludes_out_of_range(self, company, user, chart):
        clearing, sales = chart["clearing"], chart["sales"]
        _je(company, user, date(2026, 1, 5), [(clearing, "100", "0", None), (sales, "0", "100", None)])
        _je(company, user, date(2026, 3, 5), [(clearing, "70", "0", None), (sales, "0", "70", None)])

        result = build_account_drilldown(
            company=company,
            account=clearing,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
        )
        assert result["pagination"]["count"] == 1
        assert result["summary"]["period_debits"] == "100.00"

    def test_posted_only_excludes_drafts_by_default(self, company, user, chart):
        clearing, sales = chart["clearing"], chart["sales"]
        _je(company, user, date(2026, 1, 5), [(clearing, "100", "0", None), (sales, "0", "100", None)])
        _je(
            company,
            user,
            date(2026, 1, 6),
            [(clearing, "999", "0", None), (sales, "0", "999", None)],
            status=JournalEntry.Status.DRAFT,
        )

        posted = build_account_drilldown(company=company, account=clearing)
        assert posted["pagination"]["count"] == 1
        assert posted["summary"]["closing_balance"] == "100.00"

        with_drafts = build_account_drilldown(company=company, account=clearing, posted_only=False)
        assert with_drafts["pagination"]["count"] == 2
        assert with_drafts["summary"]["closing_balance"] == "1099.00"

    def test_reversed_entry_is_counted_so_balance_reconciles(self, company, user, chart):
        """A reversed original keeps its lines (status REVERSED) and a POSTED
        REVERSAL negates it; both must be counted, netting to zero — matching
        AccountBalanceProjection. Counting POSTED-only would wrongly show -100."""
        clearing, sales = chart["clearing"], chart["sales"]
        original = _je(
            company,
            user,
            date(2026, 1, 5),
            [(clearing, "100", "0", None), (sales, "0", "100", None)],
        )
        # Mark original reversed (mirrors projections.accounting on reversal).
        JournalEntry.objects.filter(pk=original.pk).update(status=JournalEntry.Status.REVERSED)
        _je(
            company,
            user,
            date(2026, 1, 6),
            [(clearing, "0", "100", None), (sales, "100", "0", None)],
            kind=JournalEntry.Kind.REVERSAL,
            reverses=original,
        )

        result = build_account_drilldown(company=company, account=clearing)
        assert result["pagination"]["count"] == 2  # both legs visible
        assert result["summary"]["closing_balance"] == "0.00"


# ─────────────────────────────────────────────────────────────────────────────
# Dimensions
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDimensions:
    def test_dimension_display_on_rows(self, company, user, chart):
        clearing, sales = chart["clearing"], chart["sales"]
        _dim, stripe, _paymob = _provider_dimension(company)
        _je(company, user, date(2026, 1, 5), [(clearing, "100", "0", stripe), (sales, "0", "100", None)])

        result = build_account_drilldown(company=company, account=clearing)
        dims = result["rows"][0]["dimensions"]
        assert dims == [
            {
                "type": "SETTLEMENT_PROVIDER",
                "label": "Settlement Provider",
                "value": "STRIPE",
                "display": "Stripe",
            }
        ]

    def test_row_without_dimensions_is_empty_list(self, company, user, chart):
        clearing, sales = chart["clearing"], chart["sales"]
        _je(company, user, date(2026, 1, 5), [(clearing, "100", "0", None), (sales, "0", "100", None)])
        result = build_account_drilldown(company=company, account=clearing)
        assert result["rows"][0]["dimensions"] == []

    def test_dimension_filter_restricts_rows_and_balance(self, company, user, chart):
        clearing, sales = chart["clearing"], chart["sales"]
        _dim, stripe, paymob = _provider_dimension(company)
        _je(company, user, date(2026, 1, 5), [(clearing, "100", "0", stripe), (sales, "0", "100", None)])
        _je(company, user, date(2026, 1, 6), [(clearing, "40", "0", paymob), (sales, "0", "40", None)])

        result = build_account_drilldown(
            company=company,
            account=clearing,
            dimension_type="SETTLEMENT_PROVIDER",
            dimension_value="STRIPE",
        )
        assert result["pagination"]["count"] == 1
        assert result["summary"]["closing_balance"] == "100.00"
        assert result["rows"][0]["dimensions"][0]["value"] == "STRIPE"


# ─────────────────────────────────────────────────────────────────────────────
# Empty account + pagination
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestEmptyAndPagination:
    def test_empty_account_zero_summary_empty_rows(self, company, chart):
        result = build_account_drilldown(company=company, account=chart["clearing"])
        assert result["rows"] == []
        assert result["pagination"]["count"] == 0
        assert result["pagination"]["total_pages"] == 1
        assert result["summary"]["opening_balance"] == "0.00"
        assert result["summary"]["period_debits"] == "0.00"
        assert result["summary"]["period_credits"] == "0.00"
        assert result["summary"]["closing_balance"] == "0.00"

    def test_pagination_keeps_running_balance_continuous(self, company, user, chart):
        clearing, sales = chart["clearing"], chart["sales"]
        for i in range(1, 6):  # 5 entries, +10 each
            _je(company, user, date(2026, 1, i), [(clearing, "10", "0", None), (sales, "0", "10", None)])

        p1 = build_account_drilldown(company=company, account=clearing, page=1, page_size=2)
        assert p1["pagination"] == {"page": 1, "page_size": 2, "count": 5, "total_pages": 3}
        assert [r["running_balance"] for r in p1["rows"]] == ["10.00", "20.00"]

        p2 = build_account_drilldown(company=company, account=clearing, page=2, page_size=2)
        assert [r["running_balance"] for r in p2["rows"]] == ["30.00", "40.00"]

        p3 = build_account_drilldown(company=company, account=clearing, page=3, page_size=2)
        assert [r["running_balance"] for r in p3["rows"]] == ["50.00"]


# ─────────────────────────────────────────────────────────────────────────────
# API view: auth, tenant isolation, read-only guarantee
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestInquiryAPI:
    def _url(self, code):
        return reverse("accounting:account-drilldown", kwargs={"code": code})

    def test_happy_path(self, authenticated_client, company, user, owner_membership, chart):
        clearing, sales = chart["clearing"], chart["sales"]
        _je(company, user, date(2026, 1, 5), [(clearing, "100", "0", None), (sales, "0", "100", None)])

        resp = authenticated_client.get(self._url("11510"))
        assert resp.status_code == 200
        assert resp.data["account"]["code"] == "11510"
        assert resp.data["account"]["currency"] == company.functional_currency
        assert resp.data["summary"]["closing_balance"] == "100.00"
        assert len(resp.data["rows"]) == 1

    def test_unknown_code_returns_404(self, authenticated_client, owner_membership):
        resp = authenticated_client.get(self._url("99999"))
        assert resp.status_code == 404

    def test_invalid_date_returns_400(self, authenticated_client, owner_membership, chart):
        resp = authenticated_client.get(self._url("11510"), {"date_from": "not-a-date"})
        assert resp.status_code == 400

    def test_requires_authentication(self, api_client, chart):
        resp = api_client.get(self._url("11510"))
        assert resp.status_code in (401, 403)

    def test_company_a_cannot_see_company_b(
        self, authenticated_client, company, second_company, user, owner_membership
    ):
        # Same account code in both companies, different data.
        clearing_a = _account(company, "11510", "Clearing A", Account.AccountType.ASSET)
        sales_a = _account(company, "41000", "Sales A", Account.AccountType.REVENUE)
        clearing_b = _account(second_company, "11510", "Clearing B", Account.AccountType.ASSET)
        sales_b = _account(second_company, "41000", "Sales B", Account.AccountType.REVENUE)
        _je(company, user, date(2026, 1, 5), [(clearing_a, "100", "0", None), (sales_a, "0", "100", None)])
        _je(second_company, user, date(2026, 1, 5), [(clearing_b, "777", "0", None), (sales_b, "0", "777", None)])

        resp = authenticated_client.get(self._url("11510"))
        assert resp.status_code == 200
        # Only company A's data — B's 777 must never appear.
        assert resp.data["summary"]["closing_balance"] == "100.00"
        assert resp.data["pagination"]["count"] == 1

    def test_endpoint_is_read_only(self, authenticated_client, company, user, owner_membership, chart):
        from events.models import BusinessEvent

        clearing, sales = chart["clearing"], chart["sales"]
        _je(company, user, date(2026, 1, 5), [(clearing, "100", "0", None), (sales, "0", "100", None)])

        before = (
            BusinessEvent.objects.count(),
            JournalEntry.objects.count(),
            JournalLine.objects.count(),
        )
        resp = authenticated_client.get(self._url("11510"), {"posted_only": "false", "page_size": "10"})
        assert resp.status_code == 200
        after = (
            BusinessEvent.objects.count(),
            JournalEntry.objects.count(),
            JournalLine.objects.count(),
        )
        assert before == after
