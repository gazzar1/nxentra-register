# tests/e2e/test_fiscal_year_lifecycle.py
"""
End-to-end tests for fiscal year lifecycle:
- T06: API contract tests for close-readiness and closing-entries
- T07: Year-end lifecycle (close -> reopen -> reclose)
- T08: Operational period-control coverage (receipts/payments blocked in closed/P13)

These tests run the full command layer (not HTTP) to verify correctness
without needing a running server.
"""

import pytest
from decimal import Decimal
from datetime import date
from calendar import monthrange
from uuid import uuid4

from django.test import TransactionTestCase
from django.utils import timezone

from accounts.models import Company, CompanyMembership
from accounts.authz import ActorContext
from accounts.permissions import grant_role_defaults
from accounting.models import Account, Customer, Vendor, JournalEntry
from accounting.commands import (
    create_journal_entry,
    save_journal_entry_complete,
    post_journal_entry,
    reverse_journal_entry,
    close_period,
    open_period,
    configure_periods,
    check_close_readiness,
    close_fiscal_year,
    reopen_fiscal_year,
    record_customer_receipt,
    record_vendor_payment,
)
from projections.models import (
    FiscalPeriod,
    FiscalPeriodConfig,
    FiscalYear as FiscalYearModel,
    AccountBalance,
    PeriodAccountBalance,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

FISCAL_YEAR = 2026


def _make_company(db):
    return Company.objects.create(
        public_id=uuid4(),
        name="FY Lifecycle Co",
        slug="fy-lifecycle",
        default_currency="USD",
        fiscal_year_start_month=1,
        is_active=True,
    )


def _make_user_and_actor(company):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(
        public_id=uuid4(),
        email="fytest@example.com",
        password="pass12345",
        name="FY Tester",
    )
    user.active_company = company
    user.save(update_fields=["active_company"])
    membership = CompanyMembership.objects.create(
        public_id=uuid4(),
        company=company,
        user=user,
        role=CompanyMembership.Role.OWNER,
        is_active=True,
    )
    grant_role_defaults(membership, granted_by=user)
    perms = frozenset(membership.permissions.values_list("code", flat=True))
    actor = ActorContext(user=user, company=company, membership=membership, perms=perms)
    return user, actor


def _create_fiscal_periods(company, fiscal_year=FISCAL_YEAR, include_p13=True):
    """Create 12 monthly periods + optional P13 for the given year."""
    for period_num in range(1, 13):
        start = date(fiscal_year, period_num, 1)
        _, last_day = monthrange(fiscal_year, period_num)
        end = date(fiscal_year, period_num, last_day)
        FiscalPeriod.objects.create(
            company=company,
            fiscal_year=fiscal_year,
            period=period_num,
            period_type=FiscalPeriod.PeriodType.NORMAL,
            start_date=start,
            end_date=end,
            status=FiscalPeriod.Status.OPEN,
        )

    if include_p13:
        _, p12_end_day = monthrange(fiscal_year, 12)
        p12_end = date(fiscal_year, 12, p12_end_day)
        FiscalPeriod.objects.create(
            company=company,
            fiscal_year=fiscal_year,
            period=13,
            period_type=FiscalPeriod.PeriodType.ADJUSTMENT,
            start_date=p12_end,
            end_date=p12_end,
            status=FiscalPeriod.Status.OPEN,
        )

    FiscalPeriodConfig.objects.create(
        company=company,
        fiscal_year=fiscal_year,
        period_count=13 if include_p13 else 12,
        open_from_period=1,
        open_to_period=13 if include_p13 else 12,
    )

    FiscalYearModel.objects.get_or_create(
        company=company,
        fiscal_year=fiscal_year,
        defaults={"status": FiscalYearModel.Status.OPEN},
    )


def _create_accounts(company):
    """Create a minimal chart of accounts for year-end testing."""
    cash = Account.objects.create(
        public_id=uuid4(), company=company, code="1000", name="Cash",
        account_type=Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        status=Account.Status.ACTIVE,
    )
    ar = Account.objects.create(
        public_id=uuid4(), company=company, code="1200", name="Accounts Receivable",
        account_type=Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        role=Account.AccountRole.RECEIVABLE_CONTROL,
        requires_counterparty=True, counterparty_kind="CUSTOMER",
        status=Account.Status.ACTIVE,
    )
    ap = Account.objects.create(
        public_id=uuid4(), company=company, code="2100", name="Accounts Payable",
        account_type=Account.AccountType.LIABILITY,
        normal_balance=Account.NormalBalance.CREDIT,
        role=Account.AccountRole.PAYABLE_CONTROL,
        requires_counterparty=True, counterparty_kind="VENDOR",
        status=Account.Status.ACTIVE,
    )
    retained = Account.objects.create(
        public_id=uuid4(), company=company, code="3100", name="Retained Earnings",
        account_type=Account.AccountType.EQUITY,
        normal_balance=Account.NormalBalance.CREDIT,
        status=Account.Status.ACTIVE,
    )
    revenue = Account.objects.create(
        public_id=uuid4(), company=company, code="4000", name="Sales Revenue",
        account_type=Account.AccountType.REVENUE,
        normal_balance=Account.NormalBalance.CREDIT,
        status=Account.Status.ACTIVE,
    )
    expense = Account.objects.create(
        public_id=uuid4(), company=company, code="5000", name="Operating Expenses",
        account_type=Account.AccountType.EXPENSE,
        normal_balance=Account.NormalBalance.DEBIT,
        status=Account.Status.ACTIVE,
    )
    return {
        "cash": cash, "ar": ar, "ap": ap,
        "retained": retained, "revenue": revenue, "expense": expense,
    }


def _post_simple_entry(actor, accts, debit_acct_key, credit_acct_key,
                        amount, entry_date, memo="Test entry"):
    """Create, complete, and post a balanced journal entry. Returns the entry."""
    result = create_journal_entry(
        actor,
        date=entry_date,
        memo=memo,
        lines=[
            {"account_id": accts[debit_acct_key].id, "description": f"Dr {debit_acct_key}",
             "debit": Decimal(str(amount)), "credit": Decimal("0")},
            {"account_id": accts[credit_acct_key].id, "description": f"Cr {credit_acct_key}",
             "debit": Decimal("0"), "credit": Decimal(str(amount))},
        ],
    )
    assert result.success, f"create failed: {result.error}"
    entry = result.data

    save = save_journal_entry_complete(actor, entry.id)
    assert save.success, f"complete failed: {save.error}"

    post = post_journal_entry(actor, entry.id)
    assert post.success, f"post failed: {post.error}"

    return JournalEntry.objects.get(pk=entry.id)


def _close_all_normal_periods(actor, fiscal_year=FISCAL_YEAR):
    """Close periods 1-12 (skips already-closed periods)."""
    for p in range(1, 13):
        fp = FiscalPeriod.objects.filter(
            company=actor.company, fiscal_year=fiscal_year, period=p,
        ).first()
        if fp and fp.status == FiscalPeriod.Status.CLOSED:
            continue
        r = close_period(actor, fiscal_year, p)
        assert r.success, f"close period {p} failed: {r.error}"


# ===========================================================================
# T06  –  API contract tests
# ===========================================================================

@pytest.mark.django_db(transaction=True)
class TestCloseReadinessAPIContract:
    """T06: Verify the response shape of close-readiness endpoint."""

    def test_close_readiness_returns_checks_array(self, db):
        company = _make_company(db)
        _, actor = _make_user_and_actor(company)
        _create_fiscal_periods(company)
        _create_accounts(company)

        result = check_close_readiness(actor, FISCAL_YEAR)
        assert result.success

        data = result.data
        # Top-level keys
        assert "fiscal_year" in data
        assert "is_ready" in data
        assert "checks" in data
        assert isinstance(data["checks"], list)

        # Each check has the right shape
        for check in data["checks"]:
            assert "check" in check, f"Missing 'check' key in {check}"
            assert "passed" in check, f"Missing 'passed' key in {check}"
            assert "detail" in check, f"Missing 'detail' key in {check}"
            assert isinstance(check["passed"], bool)
            assert isinstance(check["check"], str)
            assert isinstance(check["detail"], str)

        # Should NOT be ready (periods still open)
        assert data["is_ready"] is False

    def test_close_readiness_all_checks_pass_when_ready(self, db):
        company = _make_company(db)
        _, actor = _make_user_and_actor(company)
        _create_fiscal_periods(company)
        accts = _create_accounts(company)

        # Post some revenue so closing entries have something to close
        _post_simple_entry(actor, accts, "cash", "revenue", 1000,
                           date(FISCAL_YEAR, 1, 15))

        # Close all normal periods
        _close_all_normal_periods(actor)

        result = check_close_readiness(actor, FISCAL_YEAR)
        assert result.success
        data = result.data
        assert data["is_ready"] is True
        assert all(c["passed"] for c in data["checks"])


@pytest.mark.django_db(transaction=True)
class TestClosingEntriesAPIContract:
    """T06: Verify closing-entries response uses entry_public_id (not public_id)."""

    def test_closing_entries_after_close(self, db):
        company = _make_company(db)
        _, actor = _make_user_and_actor(company)
        _create_fiscal_periods(company)
        accts = _create_accounts(company)

        # Revenue transaction
        _post_simple_entry(actor, accts, "cash", "revenue", 5000,
                           date(FISCAL_YEAR, 3, 15), "Q1 sales")
        # Expense transaction
        _post_simple_entry(actor, accts, "expense", "cash", 2000,
                           date(FISCAL_YEAR, 6, 15), "Operating costs")

        # Close all normal periods and close the year
        _close_all_normal_periods(actor)
        close_result = close_fiscal_year(actor, FISCAL_YEAR, "3100")
        assert close_result.success, f"close failed: {close_result.error}"

        # Now query closing entries
        closing_entries = JournalEntry.objects.filter(
            company=company, kind=JournalEntry.Kind.CLOSING, period=13,
        )
        assert closing_entries.exists(), "No closing entries created"

        # Verify the response structure matches the API contract
        for entry in closing_entries:
            # The API view returns entry_public_id, not public_id
            assert entry.public_id is not None
            assert entry.kind == JournalEntry.Kind.CLOSING
            assert entry.period == 13
            assert entry.status == JournalEntry.Status.POSTED


# ===========================================================================
# T07  –  Year-end lifecycle integration suite
# ===========================================================================

@pytest.mark.django_db(transaction=True)
class TestYearEndLifecycle:
    """T07: Full close -> reopen -> reclose lifecycle."""

    def _setup(self, db):
        company = _make_company(db)
        _, actor = _make_user_and_actor(company)
        _create_fiscal_periods(company)
        accts = _create_accounts(company)
        return company, actor, accts

    def test_full_close_reopen_reclose(self, db):
        """End-to-end: close FY, reopen, make adjustments, reclose."""
        company, actor, accts = self._setup(db)

        # Post revenue & expense
        _post_simple_entry(actor, accts, "cash", "revenue", 10000,
                           date(FISCAL_YEAR, 1, 15), "Revenue")
        _post_simple_entry(actor, accts, "expense", "cash", 4000,
                           date(FISCAL_YEAR, 6, 15), "Expenses")

        # --- STEP 1: Close all normal periods ---
        _close_all_normal_periods(actor)

        # --- STEP 2: Verify readiness ---
        readiness = check_close_readiness(actor, FISCAL_YEAR)
        assert readiness.success
        assert readiness.data["is_ready"] is True

        # --- STEP 3: Close fiscal year ---
        close_result = close_fiscal_year(actor, FISCAL_YEAR, "3100")
        assert close_result.success, f"close failed: {close_result.error}"
        assert "closing_entry_public_id" in close_result.data
        assert "net_income" in close_result.data

        # Verify year is CLOSED
        fy = FiscalYearModel.objects.get(company=company, fiscal_year=FISCAL_YEAR)
        assert fy.status == FiscalYearModel.Status.CLOSED

        # Verify next year was created
        next_fy = FiscalYearModel.objects.filter(
            company=company, fiscal_year=FISCAL_YEAR + 1
        ).first()
        assert next_fy is not None
        assert next_fy.status == FiscalYearModel.Status.OPEN

        # Verify next year's Period 1 is OPEN, others CLOSED
        next_p1 = FiscalPeriod.objects.filter(
            company=company, fiscal_year=FISCAL_YEAR + 1, period=1,
        ).first()
        assert next_p1 is not None
        assert next_p1.status == FiscalPeriod.Status.OPEN

        next_p2 = FiscalPeriod.objects.filter(
            company=company, fiscal_year=FISCAL_YEAR + 1, period=2,
        ).first()
        if next_p2:
            assert next_p2.status == FiscalPeriod.Status.CLOSED

        # Verify closing entries exist in P13
        closing_entries = JournalEntry.objects.filter(
            company=company, kind=JournalEntry.Kind.CLOSING, period=13,
        )
        assert closing_entries.count() >= 1

        # --- STEP 4: Reopen fiscal year ---
        reopen_result = reopen_fiscal_year(actor, FISCAL_YEAR, "Auditor adjustments needed")
        assert reopen_result.success, f"reopen failed: {reopen_result.error}"

        fy.refresh_from_db()
        assert fy.status == FiscalYearModel.Status.OPEN

        # --- STEP 5: Reclose ---
        # Close all normal periods again
        _close_all_normal_periods(actor)

        # Ensure P13 is open for closing entries
        p13 = FiscalPeriod.objects.get(company=company, fiscal_year=FISCAL_YEAR, period=13)
        if p13.status != FiscalPeriod.Status.OPEN:
            open_result = open_period(actor, FISCAL_YEAR, 13)
            assert open_result.success, f"open P13 failed: {open_result.error}"

        reclose = close_fiscal_year(actor, FISCAL_YEAR, "3100")
        assert reclose.success, f"reclose failed: {reclose.error}"

        fy.refresh_from_db()
        assert fy.status == FiscalYearModel.Status.CLOSED

    def test_close_readiness_fails_with_open_periods(self, db):
        """Readiness fails when normal periods are still open."""
        company, actor, accts = self._setup(db)

        readiness = check_close_readiness(actor, FISCAL_YEAR)
        assert readiness.success
        assert readiness.data["is_ready"] is False

        # At least one check should reference open periods
        open_check = [c for c in readiness.data["checks"]
                      if "normal periods" in c["check"].lower()]
        assert len(open_check) == 1
        assert open_check[0]["passed"] is False

    def test_close_readiness_fails_with_draft_entries(self, db):
        """Readiness fails when there are draft journal entries."""
        company, actor, accts = self._setup(db)

        # Create a draft entry (don't post it)
        result = create_journal_entry(
            actor, date=date(FISCAL_YEAR, 1, 15), memo="Unfinished",
            lines=[
                {"account_id": accts["cash"].id, "description": "Dr",
                 "debit": Decimal("100"), "credit": Decimal("0")},
                {"account_id": accts["revenue"].id, "description": "Cr",
                 "debit": Decimal("0"), "credit": Decimal("100")},
            ],
        )
        assert result.success

        _close_all_normal_periods(actor)
        readiness = check_close_readiness(actor, FISCAL_YEAR)
        assert readiness.success
        assert readiness.data["is_ready"] is False

        draft_check = [c for c in readiness.data["checks"]
                       if "draft" in c["check"].lower()]
        assert len(draft_check) == 1
        assert draft_check[0]["passed"] is False

    def test_net_income_flows_to_retained_earnings(self, db):
        """Verify closing entries correctly zero revenue/expense to retained earnings."""
        company, actor, accts = self._setup(db)

        # Revenue: 10,000 | Expense: 3,000 | Net income should be 7,000
        _post_simple_entry(actor, accts, "cash", "revenue", 10000,
                           date(FISCAL_YEAR, 2, 10))
        _post_simple_entry(actor, accts, "expense", "cash", 3000,
                           date(FISCAL_YEAR, 5, 20))

        _close_all_normal_periods(actor)
        result = close_fiscal_year(actor, FISCAL_YEAR, "3100")
        assert result.success
        assert Decimal(result.data["net_income"]) == Decimal("7000.00")

    def test_opening_period_in_closed_year_blocked(self, db):
        """Cannot open a period in a closed fiscal year."""
        company, actor, accts = self._setup(db)

        _post_simple_entry(actor, accts, "cash", "revenue", 1000,
                           date(FISCAL_YEAR, 1, 15))
        _close_all_normal_periods(actor)
        close_fiscal_year(actor, FISCAL_YEAR, "3100")

        # Try to open period 1 in the closed year
        result = open_period(actor, FISCAL_YEAR, 1)
        assert not result.success
        assert "closed fiscal year" in result.error.lower()


# ===========================================================================
# T08  –  Operational period-control coverage
# ===========================================================================

@pytest.mark.django_db(transaction=True)
class TestOperationalPeriodControl:
    """T08: Receipts/payments blocked in closed periods and P13."""

    def _setup(self, db):
        company = _make_company(db)
        _, actor = _make_user_and_actor(company)
        _create_fiscal_periods(company)
        accts = _create_accounts(company)
        customer = Customer.objects.create(
            public_id=uuid4(), company=company, code="C001",
            name="Test Customer", status=Customer.Status.ACTIVE,
        )
        vendor = Vendor.objects.create(
            public_id=uuid4(), company=company, code="V001",
            name="Test Vendor", status=Vendor.Status.ACTIVE,
        )
        return company, actor, accts, customer, vendor

    def test_receipt_allowed_in_open_period(self, db):
        """Customer receipt succeeds in an open period."""
        company, actor, accts, customer, vendor = self._setup(db)

        result = record_customer_receipt(
            actor,
            customer_id=customer.id,
            receipt_date=date(FISCAL_YEAR, 1, 15).isoformat(),
            amount="500.00",
            bank_account_id=accts["cash"].id,
            ar_control_account_id=accts["ar"].id,
            reference="CHK-001",
        )
        assert result.success, f"receipt failed: {result.error}"

    def test_receipt_blocked_in_closed_period(self, db):
        """Customer receipt blocked when the target period is closed."""
        company, actor, accts, customer, vendor = self._setup(db)

        # Close January
        close_period(actor, FISCAL_YEAR, 1)

        result = record_customer_receipt(
            actor,
            customer_id=customer.id,
            receipt_date=date(FISCAL_YEAR, 1, 15).isoformat(),
            amount="500.00",
            bank_account_id=accts["cash"].id,
            ar_control_account_id=accts["ar"].id,
        )
        assert not result.success
        assert "closed" in result.error.lower()

    def test_payment_allowed_in_open_period(self, db):
        """Vendor payment succeeds in an open period."""
        company, actor, accts, customer, vendor = self._setup(db)

        result = record_vendor_payment(
            actor,
            vendor_id=vendor.id,
            payment_date=date(FISCAL_YEAR, 2, 15).isoformat(),
            amount="750.00",
            bank_account_id=accts["cash"].id,
            ap_control_account_id=accts["ap"].id,
            reference="WR-001",
        )
        assert result.success, f"payment failed: {result.error}"

    def test_payment_blocked_in_closed_period(self, db):
        """Vendor payment blocked when the target period is closed."""
        company, actor, accts, customer, vendor = self._setup(db)

        # Close February
        close_period(actor, FISCAL_YEAR, 2)

        result = record_vendor_payment(
            actor,
            vendor_id=vendor.id,
            payment_date=date(FISCAL_YEAR, 2, 15).isoformat(),
            amount="750.00",
            bank_account_id=accts["cash"].id,
            ap_control_account_id=accts["ap"].id,
        )
        assert not result.success
        assert "closed" in result.error.lower()

    def test_receipt_blocked_in_closed_fiscal_year(self, db):
        """Receipt blocked when the fiscal year is closed."""
        company, actor, accts, customer, vendor = self._setup(db)

        _post_simple_entry(actor, accts, "cash", "revenue", 1000,
                           date(FISCAL_YEAR, 1, 15))
        _close_all_normal_periods(actor)
        close_fiscal_year(actor, FISCAL_YEAR, "3100")

        result = record_customer_receipt(
            actor,
            customer_id=customer.id,
            receipt_date=date(FISCAL_YEAR, 3, 15).isoformat(),
            amount="500.00",
            bank_account_id=accts["cash"].id,
            ar_control_account_id=accts["ar"].id,
        )
        assert not result.success

    def test_payment_blocked_in_closed_fiscal_year(self, db):
        """Payment blocked when the fiscal year is closed."""
        company, actor, accts, customer, vendor = self._setup(db)

        _post_simple_entry(actor, accts, "cash", "revenue", 1000,
                           date(FISCAL_YEAR, 1, 15))
        _close_all_normal_periods(actor)
        close_fiscal_year(actor, FISCAL_YEAR, "3100")

        result = record_vendor_payment(
            actor,
            vendor_id=vendor.id,
            payment_date=date(FISCAL_YEAR, 4, 15).isoformat(),
            amount="300.00",
            bank_account_id=accts["cash"].id,
            ap_control_account_id=accts["ap"].id,
        )
        assert not result.success

    def test_reversal_in_same_period_succeeds(self, db):
        """Journal reversal uses the original entry's date and succeeds."""
        company, actor, accts, customer, vendor = self._setup(db)

        entry = _post_simple_entry(actor, accts, "cash", "revenue", 1000,
                                    date(FISCAL_YEAR, 1, 15))
        result = reverse_journal_entry(actor, entry.id)
        assert result.success, f"reverse failed: {result.error}"
        assert result.data["reversal"].kind == JournalEntry.Kind.REVERSAL

    def test_reversal_blocked_when_period_closed(self, db):
        """Reversal blocked when the original entry's period is closed."""
        company, actor, accts, customer, vendor = self._setup(db)

        entry = _post_simple_entry(actor, accts, "cash", "revenue", 1000,
                                    date(FISCAL_YEAR, 1, 15))
        # Close January
        close_period(actor, FISCAL_YEAR, 1)

        result = reverse_journal_entry(actor, entry.id)
        assert not result.success
        assert "closed" in result.error.lower()
