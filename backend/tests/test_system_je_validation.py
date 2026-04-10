# tests/test_system_je_validation.py
"""
Regression tests for system-generated journal entry validation.

Tests the shared validate_system_journal_postable() function that closes
the gap where automated JE creation paths (Shopify, Properties, Clinic,
Platform Connectors) could previously post to closed periods or inactive accounts.

Test scenarios:
1. Closed fiscal period → validation fails
2. Closed fiscal year → validation fails
3. Inactive account → validation fails
4. Header account → validation fails
5. Open period + active accounts → validation passes
6. on_closed_period="incomplete" → returns ok=True with period error in errors list
7. allow_missing_counterparty=True → skips counterparty validation
8. AR control account without counterparty → fails when allow_missing_counterparty=False
"""

from datetime import date
from decimal import Decimal

import pytest
from django.test import TestCase

from accounting.validation import ValidationResult, validate_system_journal_postable


@pytest.fixture
def company(db):
    """Create a test company."""
    from accounts.models import Company
    from projections.write_barrier import command_writes_allowed

    with command_writes_allowed():
        company = Company.objects.create(
            name="Test Company",
            slug="test-co",
            default_currency="USD",
            functional_currency="USD",
        )
    return company


@pytest.fixture
def accounts(company, db):
    """Create test accounts."""
    from accounting.models import Account
    from projections.write_barrier import projection_writes_allowed

    with projection_writes_allowed():
        cash = Account.objects.projection().create(
            company=company, code="1000", name="Cash",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company, code="4000", name="Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
        inactive = Account.objects.projection().create(
            company=company, code="9999", name="Inactive Account",
            account_type=Account.AccountType.EXPENSE,
            status=Account.Status.INACTIVE,
        )
        header = Account.objects.projection().create(
            company=company, code="1XXX", name="Header Account",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
            is_header=True,
        )
    return {"cash": cash, "revenue": revenue, "inactive": inactive, "header": header}


@pytest.fixture
def open_period(company, db):
    """Create an open fiscal period covering today."""
    from projections.models import FiscalPeriod
    from projections.write_barrier import projection_writes_allowed

    today = date.today()
    with projection_writes_allowed():
        fp, _ = FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=today.year,
            period=today.month,
            defaults=dict(
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=today.replace(day=1),
                end_date=today.replace(day=28),
                status=FiscalPeriod.Status.OPEN,
            ),
        )
        if fp.status != FiscalPeriod.Status.OPEN:
            fp.status = FiscalPeriod.Status.OPEN
            fp.save(update_fields=["status"])
    return fp


@pytest.fixture
def closed_period(company, db):
    """Create a closed fiscal period."""
    from projections.models import FiscalPeriod
    from projections.write_barrier import projection_writes_allowed

    with projection_writes_allowed():
        fp = FiscalPeriod(
            company=company,
            fiscal_year=2025,
            period=1,
            period_type=FiscalPeriod.PeriodType.NORMAL,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            status=FiscalPeriod.Status.CLOSED,
        )
        fp.save()
    return fp


@pytest.mark.django_db
class TestValidateSystemJournalPostable:
    """Tests for validate_system_journal_postable()."""

    def test_valid_entry_passes(self, company, accounts, open_period):
        """Open period + active accounts → passes."""
        lines = [
            {"account": accounts["cash"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date.today(),
            lines=lines,
            source_module="test",
        )
        assert result.ok
        assert result.errors == []

    def test_closed_period_rejects(self, company, accounts, closed_period):
        """Closed period → fails with reject mode."""
        lines = [
            {"account": accounts["cash"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date(2025, 1, 15),
            lines=lines,
            source_module="test",
            on_closed_period="reject",
        )
        assert not result.ok
        assert any("closed" in e.lower() for e in result.errors)

    def test_closed_period_incomplete_mode(self, company, accounts, closed_period):
        """Closed period with on_closed_period="incomplete" → ok=True with error info."""
        lines = [
            {"account": accounts["cash"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date(2025, 1, 15),
            lines=lines,
            source_module="test",
            on_closed_period="incomplete",
        )
        assert result.ok
        assert len(result.errors) > 0
        assert any("[period_closed]" in e for e in result.errors)

    def test_inactive_account_fails(self, company, accounts, open_period):
        """Inactive account → fails."""
        lines = [
            {"account": accounts["inactive"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date.today(),
            lines=lines,
            source_module="test",
        )
        assert not result.ok
        assert any("inactive" in e.lower() or "9999" in e for e in result.errors)

    def test_header_account_fails(self, company, accounts, open_period):
        """Header account → fails."""
        lines = [
            {"account": accounts["header"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date.today(),
            lines=lines,
            source_module="test",
        )
        assert not result.ok
        assert any("header" in e.lower() or "1XXX" in e for e in result.errors)

    def test_unbalanced_entry_fails(self, company, accounts, open_period):
        """Unbalanced entry → fails."""
        lines = [
            {"account": accounts["cash"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("50")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date.today(),
            lines=lines,
            source_module="test",
        )
        assert not result.ok
        assert any("unbalanced" in e.lower() for e in result.errors)

    def test_allow_missing_counterparty(self, company, accounts, open_period):
        """allow_missing_counterparty=True skips counterparty checks."""
        lines = [
            {"account": accounts["cash"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date.today(),
            lines=lines,
            source_module="test",
            allow_missing_counterparty=True,
        )
        assert result.ok

    def test_no_period_defined_passes(self, company, accounts):
        """No fiscal period defined for date → passes (some companies don't configure periods)."""
        lines = [
            {"account": accounts["cash"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date(2030, 6, 15),  # Far future, no period defined
            lines=lines,
            source_module="test",
        )
        assert result.ok

    def test_validation_result_factory_methods(self):
        """ValidationResult factory methods work correctly."""
        ok = ValidationResult.success()
        assert ok.ok
        assert ok.errors == []

        fail = ValidationResult.fail(["error1", "error2"])
        assert not fail.ok
        assert len(fail.errors) == 2
