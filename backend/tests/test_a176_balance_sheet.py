# tests/test_a176_balance_sheet.py
"""
A176 — balance sheet omits current-year earnings and misstates period
"as of" semantics (2026-07-11 dual audit).

Before the fix:
- BalanceSheetView grouped only asset/liability/equity accounts; unclosed
  REVENUE/EXPENSE activity was silently dropped, so an ordinary
  Dr Asset / Cr Revenue posting made the statement report itself out of
  balance (is_balanced=False) until year-end close.
- Period mode filtered FROM period_from instead of accumulating through
  period_to — a period-3 balance sheet was period-3 MOVEMENT, dropping
  all prior history (opening cash/AR/AP/equity wrong).

The fix folds unclosed net income into equity as a synthetic Current
Year Earnings row (presentation-only — no JE, no writes) and makes
period mode a true cumulative "as of end of period_to" statement.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from events.models import BusinessEvent
from events.types import EventTypes
from projections.account_balance import AccountBalanceProjection

pytestmark = pytest.mark.django_db

THIS_YEAR = date.today().year


def _post_je_event(company, user, lines, entry_date):
    entry_pid = str(uuid4())
    total = sum(Decimal(str(line["debit"])) for line in lines)
    return BusinessEvent.objects.create(
        company=company,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=entry_pid,
        idempotency_key=f"test.a176.jepost:{entry_pid}",
        caused_by_user=user,
        data={
            "entry_public_id": entry_pid,
            "entry_number": f"JE-{entry_pid[:8]}",
            "date": entry_date.isoformat(),
            "memo": "A176 test entry",
            "kind": "NORMAL",
            "period": entry_date.month,
            "posted_at": f"{entry_date.isoformat()}T12:00:00+00:00",
            "posted_by_id": user.id,
            "posted_by_email": user.email,
            "total_debit": str(total),
            "total_credit": str(total),
            "lines": [
                {
                    "line_no": i + 1,
                    "account_public_id": str(line["account"].public_id),
                    "account_code": line["account"].code,
                    "description": line.get("description", "line"),
                    "debit": str(line["debit"]),
                    "credit": str(line["credit"]),
                }
                for i, line in enumerate(lines)
            ],
        },
    )


def _cash_revenue_lines(cash_account, revenue_account, amount):
    return [
        {"account": cash_account, "debit": amount, "credit": "0.00"},
        {"account": revenue_account, "debit": "0.00", "credit": amount},
    ]


class TestCurrentModeFoldsUnclosedPnl:
    def test_mid_year_balance_sheet_balances_with_open_pnl(
        self, authenticated_client, company, user, owner_membership, cash_account, revenue_account
    ):
        _post_je_event(company, user, _cash_revenue_lines(cash_account, revenue_account, "1000.00"), date.today())
        AccountBalanceProjection().process_pending(company)

        resp = authenticated_client.get("/api/reports/balance-sheet/")
        assert resp.status_code == 200, resp.content
        body = resp.json()

        assert body["total_assets"] == "1000.00"
        assert body["total_equity"] == "1000.00", "unclosed revenue must appear in equity as Current Year Earnings"
        assert body["is_balanced"] is True, "the accounting equation must hold mid-year"
        cye_rows = [a for a in body["equity"]["accounts"] if a.get("is_synthetic")]
        assert len(cye_rows) == 1
        assert cye_rows[0]["balance"] == "1000.00"
        assert body["current_year_earnings"] == "1000.00"

    def test_expense_nets_against_revenue(
        self,
        authenticated_client,
        company,
        user,
        owner_membership,
        cash_account,
        revenue_account,
        expense_account,
    ):
        _post_je_event(company, user, _cash_revenue_lines(cash_account, revenue_account, "1000.00"), date.today())
        _post_je_event(
            company,
            user,
            [
                {"account": expense_account, "debit": "300.00", "credit": "0.00"},
                {"account": cash_account, "debit": "0.00", "credit": "300.00"},
            ],
            date.today(),
        )
        AccountBalanceProjection().process_pending(company)

        resp = authenticated_client.get("/api/reports/balance-sheet/")
        body = resp.json()
        assert body["total_assets"] == "700.00"
        assert body["current_year_earnings"] == "700.00"
        assert body["is_balanced"] is True

    def test_no_synthetic_row_when_pnl_is_zero(
        self, authenticated_client, company, user, owner_membership, cash_account, accounts_payable
    ):
        _post_je_event(
            company,
            user,
            [
                {"account": cash_account, "debit": "500.00", "credit": "0.00"},
                {"account": accounts_payable, "debit": "0.00", "credit": "500.00"},
            ],
            date.today(),
        )
        AccountBalanceProjection().process_pending(company)

        resp = authenticated_client.get("/api/reports/balance-sheet/")
        body = resp.json()
        assert not any(a.get("is_synthetic") for a in body["equity"]["accounts"]), (
            "no noisy 0.00 Current Year Earnings row"
        )
        assert body["is_balanced"] is True


class TestPeriodModeCumulativeAsOf:
    def test_selected_period_means_as_of_not_movement(
        self, authenticated_client, company, user, owner_membership, cash_account, revenue_account
    ):
        _post_je_event(
            company, user, _cash_revenue_lines(cash_account, revenue_account, "1000.00"), date(THIS_YEAR, 1, 15)
        )
        _post_je_event(
            company, user, _cash_revenue_lines(cash_account, revenue_account, "500.00"), date(THIS_YEAR, 3, 10)
        )

        resp = authenticated_client.get(
            f"/api/reports/balance-sheet/?fiscal_year={THIS_YEAR}&period_from=3&period_to=3"
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()

        assert body["total_assets"] == "1500.00", (
            "a period-3 balance sheet is cumulative through end of period 3, not period-3 movement"
        )
        assert body["current_year_earnings"] == "1500.00"
        assert body["total_equity"] == "1500.00"
        assert body["is_balanced"] is True

    def test_activity_after_period_to_is_excluded(
        self, authenticated_client, company, user, owner_membership, cash_account, revenue_account
    ):
        _post_je_event(
            company, user, _cash_revenue_lines(cash_account, revenue_account, "1000.00"), date(THIS_YEAR, 1, 15)
        )
        _post_je_event(
            company, user, _cash_revenue_lines(cash_account, revenue_account, "999.00"), date(THIS_YEAR, 6, 10)
        )

        resp = authenticated_client.get(
            f"/api/reports/balance-sheet/?fiscal_year={THIS_YEAR}&period_from=1&period_to=3"
        )
        body = resp.json()
        assert body["total_assets"] == "1000.00", "activity after period_to must not leak in"
        assert body["is_balanced"] is True
