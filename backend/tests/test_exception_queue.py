# tests/test_exception_queue.py
"""
Tests for the reconciliation exception queue.

Covers:
1. Exception creation and deduplication
2. Auto-detection of unmatched bank transactions
3. Auto-resolution when underlying issues are fixed
4. Company-scoped isolation (RLS)
5. Exception status transitions
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from bank_connector.exceptions import (
    _create_exception,
    auto_resolve_matched,
    detect_unmatched_bank_transactions,
    scan_all,
)
from bank_connector.models import (
    BankAccount,
    BankStatement,
    BankTransaction,
    ReconciliationException,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def bank_account(db, company):
    return BankAccount.objects.create(
        company=company,
        bank_name="Test Bank",
        account_name="Main Account",
        currency="USD",
    )


@pytest.fixture
def bank_statement(db, company, bank_account):
    return BankStatement.objects.create(
        company=company,
        bank_account=bank_account,
        filename="test.csv",
        status="PROCESSED",
    )


@pytest.fixture
def unmatched_deposit(db, company, bank_account, bank_statement):
    """Create an unmatched bank deposit older than 7 days."""
    return BankTransaction.objects.create(
        company=company,
        bank_account=bank_account,
        statement=bank_statement,
        transaction_date=date.today() - timedelta(days=10),
        description="SHOPIFY PAYOUT",
        amount=Decimal("500.00"),
        transaction_type="CREDIT",
        status="UNMATCHED",
    )


@pytest.fixture
def fresh_deposit(db, company, bank_account, bank_statement):
    """Create an unmatched deposit only 2 days old (within grace period)."""
    return BankTransaction.objects.create(
        company=company,
        bank_account=bank_account,
        statement=bank_statement,
        transaction_date=date.today() - timedelta(days=2),
        description="STRIPE PAYOUT",
        amount=Decimal("200.00"),
        transaction_type="CREDIT",
        status="UNMATCHED",
    )


# =============================================================================
# Test Exception Creation & Deduplication
# =============================================================================


class TestExceptionCreation:
    def test_create_exception(self, db, company):
        exc = _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.UNMATCHED_BANK_TX,
            severity=ReconciliationException.Severity.MEDIUM,
            title="Test exception",
            description="Test description",
            amount=Decimal("100.00"),
            currency="USD",
            exception_date=date.today(),
            reference_type="bank_transaction",
            reference_id=1,
        )
        assert exc.id is not None
        assert exc.status == ReconciliationException.Status.OPEN
        assert exc.company == company

    def test_deduplication_same_reference(self, db, company):
        """Creating two exceptions with same ref should return the existing one."""
        exc1 = _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.UNMATCHED_BANK_TX,
            severity=ReconciliationException.Severity.MEDIUM,
            title="Exception 1",
            description="Desc 1",
            exception_date=date.today(),
            reference_type="bank_transaction",
            reference_id=42,
        )
        exc2 = _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.UNMATCHED_BANK_TX,
            severity=ReconciliationException.Severity.MEDIUM,
            title="Exception 2",
            description="Desc 2",
            exception_date=date.today(),
            reference_type="bank_transaction",
            reference_id=42,
        )
        assert exc1.id == exc2.id
        assert (
            ReconciliationException.objects.filter(
                company=company,
                reference_type="bank_transaction",
                reference_id=42,
            ).count()
            == 1
        )

    def test_different_types_not_deduplicated(self, db, company):
        """Different exception types for same ref should create separate records."""
        exc1 = _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.UNMATCHED_BANK_TX,
            severity=ReconciliationException.Severity.MEDIUM,
            title="Exception 1",
            exception_date=date.today(),
            reference_type="bank_transaction",
            reference_id=42,
        )
        exc2 = _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.FEE_VARIANCE,
            severity=ReconciliationException.Severity.LOW,
            title="Exception 2",
            exception_date=date.today(),
            reference_type="bank_transaction",
            reference_id=42,
        )
        assert exc1.id != exc2.id

    def test_resolved_exception_allows_new_creation(self, db, company):
        """Resolved exceptions don't block new ones for the same reference."""
        exc1 = _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.UNMATCHED_BANK_TX,
            severity=ReconciliationException.Severity.MEDIUM,
            title="Exception 1",
            exception_date=date.today(),
            reference_type="bank_transaction",
            reference_id=99,
        )
        exc1.status = ReconciliationException.Status.RESOLVED
        exc1.save()

        exc2 = _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.UNMATCHED_BANK_TX,
            severity=ReconciliationException.Severity.MEDIUM,
            title="Exception 2",
            exception_date=date.today(),
            reference_type="bank_transaction",
            reference_id=99,
        )
        assert exc1.id != exc2.id


# =============================================================================
# Test Detection Logic
# =============================================================================


class TestDetection:
    def test_detect_stale_unmatched(self, db, company, unmatched_deposit, fresh_deposit):
        """Only deposits older than age_days should be flagged."""
        exceptions = detect_unmatched_bank_transactions(company, age_days=7)
        assert len(exceptions) == 1
        assert exceptions[0].reference_id == unmatched_deposit.id

    def test_severity_escalation_by_amount(self, db, company, bank_account, bank_statement):
        """Large amounts should get HIGH severity."""
        BankTransaction.objects.create(
            company=company,
            bank_account=bank_account,
            statement=bank_statement,
            transaction_date=date.today() - timedelta(days=10),
            description="BIG DEPOSIT",
            amount=Decimal("5000.00"),
            transaction_type="CREDIT",
            status="UNMATCHED",
        )
        exceptions = detect_unmatched_bank_transactions(company, age_days=7)
        high = [e for e in exceptions if e.severity == ReconciliationException.Severity.HIGH]
        assert len(high) >= 1

    def test_severity_escalation_by_age(self, db, company, bank_account, bank_statement):
        """Very old unmatched deposits should get CRITICAL severity."""
        BankTransaction.objects.create(
            company=company,
            bank_account=bank_account,
            statement=bank_statement,
            transaction_date=date.today() - timedelta(days=45),
            description="OLD DEPOSIT",
            amount=Decimal("100.00"),
            transaction_type="CREDIT",
            status="UNMATCHED",
        )
        exceptions = detect_unmatched_bank_transactions(company, age_days=7)
        critical = [e for e in exceptions if e.severity == ReconciliationException.Severity.CRITICAL]
        assert len(critical) >= 1

    def test_withdrawals_not_flagged(self, db, company, bank_account, bank_statement):
        """Withdrawals (negative amounts) should not be flagged."""
        BankTransaction.objects.create(
            company=company,
            bank_account=bank_account,
            statement=bank_statement,
            transaction_date=date.today() - timedelta(days=10),
            description="WITHDRAWAL",
            amount=Decimal("-500.00"),
            transaction_type="DEBIT",
            status="UNMATCHED",
        )
        exceptions = detect_unmatched_bank_transactions(company, age_days=7)
        assert len(exceptions) == 0


# =============================================================================
# Test Auto-Resolution
# =============================================================================


class TestAutoResolve:
    def test_auto_resolve_matched_bank_tx(self, db, company, unmatched_deposit):
        """When a bank tx is matched, its exception should auto-resolve."""
        # Create exception
        exc = _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.UNMATCHED_BANK_TX,
            severity=ReconciliationException.Severity.MEDIUM,
            title="Unmatched deposit",
            exception_date=unmatched_deposit.transaction_date,
            reference_type="bank_transaction",
            reference_id=unmatched_deposit.id,
        )
        assert exc.status == ReconciliationException.Status.OPEN

        # Match the bank transaction
        unmatched_deposit.status = "MATCHED"
        unmatched_deposit.save()

        # Run auto-resolve
        resolved = auto_resolve_matched(company)
        assert resolved == 1

        exc.refresh_from_db()
        assert exc.status == ReconciliationException.Status.RESOLVED
        assert "Auto-resolved" in exc.resolution_note


# =============================================================================
# Test Company Isolation (RLS)
# =============================================================================


class TestCompanyIsolation:
    def test_exceptions_scoped_to_company(self, db, company, second_company):
        """Exceptions for one company should not appear for another."""
        _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.UNMATCHED_BANK_TX,
            severity=ReconciliationException.Severity.MEDIUM,
            title="Company 1 exception",
            exception_date=date.today(),
            reference_type="test",
            reference_id=1,
        )
        _create_exception(
            second_company,
            exception_type=ReconciliationException.ExceptionType.UNMATCHED_BANK_TX,
            severity=ReconciliationException.Severity.MEDIUM,
            title="Company 2 exception",
            exception_date=date.today(),
            reference_type="test",
            reference_id=2,
        )

        c1 = ReconciliationException.objects.filter(company=company).count()
        c2 = ReconciliationException.objects.filter(company=second_company).count()
        assert c1 == 1
        assert c2 == 1

    def test_scan_all_scoped(self, db, company, second_company, bank_account, bank_statement, unmatched_deposit):
        """scan_all should only create exceptions for the target company."""
        result = scan_all(company)
        assert result["created"] >= 1

        # Second company should have 0 exceptions (no bank data)
        c2_count = ReconciliationException.objects.filter(company=second_company).count()
        assert c2_count == 0


# =============================================================================
# Test Status Transitions
# =============================================================================


class TestStatusTransitions:
    def test_resolve_sets_fields(self, db, company):
        exc = _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.UNMATCHED_BANK_TX,
            severity=ReconciliationException.Severity.MEDIUM,
            title="Test",
            exception_date=date.today(),
        )
        assert exc.status == ReconciliationException.Status.OPEN

        exc.status = ReconciliationException.Status.RESOLVED
        exc.resolution_note = "Manually resolved"
        exc.save()
        exc.refresh_from_db()
        assert exc.status == ReconciliationException.Status.RESOLVED
        assert exc.resolution_note == "Manually resolved"

    def test_escalate_status(self, db, company):
        exc = _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.PAYOUT_DISCREPANCY,
            severity=ReconciliationException.Severity.HIGH,
            title="Discrepancy",
            exception_date=date.today(),
        )
        exc.status = ReconciliationException.Status.ESCALATED
        exc.save()
        exc.refresh_from_db()
        assert exc.status == ReconciliationException.Status.ESCALATED
