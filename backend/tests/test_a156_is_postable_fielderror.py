# tests/test_a156_is_postable_fielderror.py
"""
A156 — ``Account.objects.filter(is_postable=True)`` raises FieldError.

``is_postable`` is a Python @property (accounting/models.py), not a DB
field, so every queryset that filters on it crashes with FieldError the
moment its fallback branch is reached:

- accounting/commands.py  _fix_fx_rounding_dicts (FX rounding without core mapping)
- platform_connectors/je_builder.py _fix_fx_rounding (same, platform JE path)
- accounting/tasks.py     _revalue_company (FX gain/loss role fallback)
- accounting/views.py     CoreAccountMappingView._auto_initialize (swallowed —
                          auto-init has silently never worked)
- projections/views.py    revaluation endpoint role fallback

These tests assert the DESIRED behavior of each fallback branch, so on the
unfixed code they fail with FieldError (or with a None mapping for the
swallowed auto-init site) — proving the audited bug — and pass once the
filters use the real predicate (is_header=False, status=ACTIVE).
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.utils import timezone

from accounting.models import Account, ExchangeRate, JournalEntry, JournalLine

pytestmark = pytest.mark.django_db


def _role_account(company, code, name, account_type, role):
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code=code,
        name=name,
        account_type=account_type,
        role=role,
        status=Account.Status.ACTIVE,
    )


@pytest.fixture
def fx_rounding_account(company):
    return _role_account(company, "5990", "FX Rounding", Account.AccountType.EXPENSE, Account.AccountRole.FX_ROUNDING)


@pytest.fixture
def fx_gain_account(company):
    return _role_account(company, "4900", "FX Gain", Account.AccountType.REVENUE, Account.AccountRole.FINANCIAL_INCOME)


@pytest.fixture
def fx_loss_account(company):
    return _role_account(company, "5900", "FX Loss", Account.AccountType.EXPENSE, Account.AccountRole.FINANCIAL_EXPENSE)


class TestFxRoundingDictFallback:
    """accounting/commands.py:_fix_fx_rounding_dicts — role fallback branch."""

    def test_rounding_line_added_from_role_account_without_core_mapping(self, company, fx_rounding_account):
        from accounting.commands import _fix_fx_rounding_dicts

        je_lines = [
            {"account_id": 1, "description": "a", "debit": Decimal("100.00"), "credit": Decimal("0")},
            {"account_id": 2, "description": "b", "debit": Decimal("0"), "credit": Decimal("99.98")},
        ]

        # No ModuleAccountMapping exists -> must fall back to the
        # FX_ROUNDING-role account instead of raising FieldError.
        _fix_fx_rounding_dicts(je_lines, company)

        assert len(je_lines) == 3, "expected a rounding line to be appended"
        rounding = je_lines[-1]
        assert rounding["account_id"] == fx_rounding_account.id
        assert rounding["credit"] == Decimal("0.02")


class TestJeBuilderFxRoundingFallback:
    """platform_connectors/je_builder.py:_fix_fx_rounding — role fallback branch."""

    def test_rounding_line_added_from_role_account_without_core_mapping(
        self, company, user, fx_rounding_account, cash_account, revenue_account
    ):
        from platform_connectors.je_builder import _fix_fx_rounding

        entry = JournalEntry.objects.create(
            public_id=uuid4(),
            company=company,
            date=date.today(),
            period=date.today().month,
            memo="platform JE",
            status=JournalEntry.Status.POSTED,
            posted_at=timezone.now(),
            created_by=user,
        )
        lines = [
            JournalLine(
                entry=entry,
                company=company,
                public_id=uuid4(),
                line_no=1,
                account=cash_account,
                description="debit",
                debit=Decimal("100.00"),
                credit=Decimal("0"),
            ),
            JournalLine(
                entry=entry,
                company=company,
                public_id=uuid4(),
                line_no=2,
                account=revenue_account,
                description="credit",
                debit=Decimal("0"),
                credit=Decimal("99.97"),
            ),
        ]

        _fix_fx_rounding(lines, entry, company, "USD", Decimal("1.0"))

        assert len(lines) == 3, "expected a rounding line to be appended"
        assert lines[-1].account_id == fx_rounding_account.id
        assert lines[-1].credit == Decimal("0.03")


class TestRevaluationRoleFallback:
    """accounting/tasks.py:_revalue_company — FX gain/loss role fallback."""

    def test_revaluation_without_core_mapping_uses_role_accounts(
        self, company, user, owner_membership, fx_gain_account, fx_loss_account
    ):
        from accounting.tasks import _revalue_company

        bank_eur = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="1150",
            name="Bank EUR",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        offset = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="3000",
            name="Opening Equity",
            account_type=Account.AccountType.EQUITY,
            status=Account.Status.ACTIVE,
        )

        today = date.today()
        entry = JournalEntry.objects.create(
            public_id=uuid4(),
            company=company,
            date=today,
            period=today.month,
            memo="EUR opening",
            entry_number="JE-EUR-1",
            status=JournalEntry.Status.POSTED,
            posted_at=timezone.now(),
            posted_by=user,
            created_by=user,
        )
        JournalLine.objects.create(
            entry=entry,
            company=company,
            line_no=1,
            account=bank_eur,
            description="EUR cash",
            debit=Decimal("110.00"),
            credit=Decimal("0"),
            currency="EUR",
            amount_currency=Decimal("100.00"),
            exchange_rate=Decimal("1.10"),
        )
        JournalLine.objects.create(
            entry=entry,
            company=company,
            line_no=2,
            account=offset,
            description="offset",
            debit=Decimal("0"),
            credit=Decimal("110.00"),
        )

        ExchangeRate.objects.create(
            company=company,
            from_currency="EUR",
            to_currency="USD",
            rate=Decimal("1.20"),
            effective_date=today,
            rate_type="SPOT",
        )

        # No core mapping exists -> the FX gain/loss lookup must fall back
        # to the FINANCIAL_INCOME / FINANCIAL_EXPENSE role accounts instead
        # of raising FieldError.
        result = _revalue_company(company, today, auto_reverse=False)

        assert result["status"] not in ("error",), result
        reval_je = (
            JournalEntry.objects.filter(company=company, memo__startswith="Currency revaluation as of")
            .order_by("-id")
            .first()
        )
        assert reval_je is not None, result
        gain_lines = reval_je.lines.filter(account=fx_gain_account)
        assert gain_lines.exists(), "unrealized gain must credit the FINANCIAL_INCOME role account"
        assert gain_lines.first().credit == Decimal("10.00")


class TestCoreMappingAutoInit:
    """accounting/views.py:CoreAccountMappingView._auto_initialize — has silently never worked."""

    def test_get_auto_initializes_mappings_from_role_accounts(
        self,
        authenticated_client,
        company,
        owner_membership,
        fx_rounding_account,
        fx_gain_account,
        fx_loss_account,
    ):
        from accounting.mappings import ModuleAccountMapping

        assert not ModuleAccountMapping.objects.filter(company=company, module="core").exists()

        resp = authenticated_client.get("/api/accounting/core-account-mapping/")
        assert resp.status_code == 200

        by_role = {row["role"]: row for row in resp.json()}
        assert by_role["FX_GAIN"]["account_id"] == fx_gain_account.id
        assert by_role["FX_LOSS"]["account_id"] == fx_loss_account.id
        assert by_role["FX_ROUNDING"]["account_id"] == fx_rounding_account.id
        # REALIZED_FX_GAIN/LOSS default to the same role accounts
        assert by_role["REALIZED_FX_GAIN"]["account_id"] == fx_gain_account.id
        assert by_role["REALIZED_FX_LOSS"]["account_id"] == fx_loss_account.id

        assert ModuleAccountMapping.objects.filter(company=company, module="core").count() == 5
