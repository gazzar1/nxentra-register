# tests/test_control_invariants.py
"""
Nxentra Control Invariants.

These tests verify structural accounting controls that enforce correctness
at the system boundary — period gating, subledger tie-out, fiscal year
lifecycle, and cross-cutting replay consistency.

If any of these fail, the system's control layer is compromised.

Invariants:
1. Closed period blocks posting via can_post_to_period
2. Projections in a closed period are frozen (no new events leak through)
3. Fiscal year close + reopen preserves truth
4. AR/AP subledger totals tie to control accounts after mixed operations
5. Full replay after mixed operations (inline, external, reversals, rebuild) matches
"""

import pytest
from decimal import Decimal
from datetime import date
from calendar import monthrange
from uuid import uuid4

from django.utils import timezone

from accounts.models import Company, CompanyMembership
from accounts.authz import ActorContext
from accounts.permissions import grant_role_defaults
from accounting.models import Account, Customer, Vendor
from accounting.policies import can_post_to_period, validate_subledger_tieout
from events.emitter import emit_event
from events.models import BusinessEvent
from events.types import EventTypes
from events.payload_policy import INLINE_MAX_SIZE
from events.serialization import estimate_json_size
from projections.account_balance import AccountBalanceProjection
from projections.subledger_balance import SubledgerBalanceProjection
from projections.models import (
    AccountBalance,
    CustomerBalance,
    VendorBalance,
    FiscalPeriod,
    FiscalPeriodConfig,
    FiscalYear as FiscalYearModel,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _emit_posted(company, user, lines, memo="Control test", entry_id=None):
    """Emit a JOURNAL_ENTRY_POSTED event."""
    entry_id = entry_id or uuid4()
    total_debit = sum(Decimal(l.get("debit", "0")) for l in lines)
    total_credit = sum(Decimal(l.get("credit", "0")) for l in lines)

    return emit_event(
        company=company,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=str(entry_id),
        data={
            "entry_public_id": str(entry_id),
            "entry_number": f"JE-CTRL-{uuid4().hex[:6]}",
            "date": date.today().isoformat(),
            "memo": memo,
            "kind": "NORMAL",
            "posted_at": timezone.now().isoformat(),
            "posted_by_id": user.id,
            "posted_by_email": user.email,
            "total_debit": str(total_debit),
            "total_credit": str(total_credit),
            "lines": lines,
        },
        caused_by_user=user,
        idempotency_key=f"ctrl:{entry_id}",
    )


def _line(account, debit="0.00", credit="0.00", line_no=1,
          customer_public_id=None, vendor_public_id=None):
    """Build a journal line dict with optional counterparty."""
    d = {
        "line_no": line_no,
        "account_public_id": str(account.public_id),
        "account_code": account.code,
        "description": f"Control line {line_no}",
        "debit": str(debit),
        "credit": str(credit),
    }
    if customer_public_id:
        d["customer_public_id"] = str(customer_public_id)
    if vendor_public_id:
        d["vendor_public_id"] = str(vendor_public_id)
    return d


def _snapshot_balances(company):
    """Capture all AccountBalance records as dict keyed by account code."""
    return {
        b.account.code: {
            "balance": b.balance,
            "debit_total": b.debit_total,
            "credit_total": b.credit_total,
            "entry_count": b.entry_count,
        }
        for b in AccountBalance.objects.filter(company=company).select_related("account")
    }


def _make_actor(company, user, membership):
    """Create an ActorContext."""
    perms = frozenset(membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=membership, perms=perms)


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 1: Closed period blocks posting
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestClosedPeriodBlocksPosting:
    """
    When a fiscal period is CLOSED, can_post_to_period must reject any
    attempt to post an entry dated within that period.

    This is the gatekeeper for period integrity.
    """

    def test_open_period_allows_posting(self, company, user, owner_membership):
        """An open period should allow posting."""
        actor = _make_actor(company, user, owner_membership)
        today = date.today()

        allowed, reason = can_post_to_period(actor, today)
        assert allowed, f"Open period should allow posting: {reason}"

    def test_closed_period_rejects_posting(self, company, user, owner_membership):
        """A closed period must reject posting."""
        actor = _make_actor(company, user, owner_membership)
        today = date.today()

        # Close the current period
        current_period = FiscalPeriod.objects.get(
            company=company,
            fiscal_year=today.year,
            period=today.month,
        )
        current_period.status = FiscalPeriod.Status.CLOSED
        current_period.save()

        allowed, reason = can_post_to_period(actor, today)
        assert not allowed, "Closed period should reject posting"
        assert "closed" in reason.lower()

    def test_closed_fiscal_year_rejects_posting(self, company, user, owner_membership):
        """A closed fiscal year must reject posting even if the period is open."""
        actor = _make_actor(company, user, owner_membership)
        today = date.today()

        # Create a closed fiscal year
        FiscalYearModel.objects.create(
            company=company,
            fiscal_year=today.year,
            status=FiscalYearModel.Status.CLOSED,
        )

        allowed, reason = can_post_to_period(actor, today)
        assert not allowed, "Closed fiscal year should reject posting"
        assert "closed" in reason.lower()


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 2: Projections in closed period are frozen
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestClosedPeriodProjectionFreeze:
    """
    After closing a period and processing projections, the balances must
    not change when no new events are added. And the period closing itself
    does not corrupt existing balances.
    """

    def test_balances_stable_after_period_close(
        self, company, user, cash_account, revenue_account, owner_membership
    ):
        # Post entries while period is open
        _emit_posted(company, user, [
            _line(cash_account, debit="500.00", line_no=1),
            _line(revenue_account, credit="500.00", line_no=2),
        ])

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        before_close = _snapshot_balances(company)

        # Close the current period
        today = date.today()
        current_period = FiscalPeriod.objects.get(
            company=company,
            fiscal_year=today.year,
            period=today.month,
        )
        current_period.status = FiscalPeriod.Status.CLOSED
        current_period.save()

        # Process projections again (should be no-op for balance projection)
        projection.process_pending(company)

        after_close = _snapshot_balances(company)

        assert before_close == after_close, (
            "Closing a period must not change existing balances"
        )


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 3: Fiscal year close + reopen preserves truth
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestFiscalYearCloseReopenPreservesTruth:
    """
    Closing a fiscal year, then reopening it, must leave all balances
    and the trial balance in the exact same state as before the close.

    The close/reopen cycle must be lossless.
    """

    def test_close_reopen_cycle_preserves_balances(
        self, company, user, cash_account, revenue_account, expense_account,
        owner_membership
    ):
        # Post some entries
        _emit_posted(company, user, [
            _line(cash_account, debit="1000.00", line_no=1),
            _line(revenue_account, credit="1000.00", line_no=2),
        ])
        _emit_posted(company, user, [
            _line(expense_account, debit="300.00", line_no=1),
            _line(cash_account, credit="300.00", line_no=2),
        ])

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        before_close = _snapshot_balances(company)
        tb_before = projection.get_trial_balance(company)

        # Close fiscal year
        today = date.today()
        fy, _ = FiscalYearModel.objects.get_or_create(
            company=company,
            fiscal_year=today.year,
            defaults={"status": FiscalYearModel.Status.OPEN},
        )
        fy.status = FiscalYearModel.Status.CLOSED
        fy.closed_at = timezone.now()
        fy.save()

        # Reopen fiscal year
        fy.status = FiscalYearModel.Status.OPEN
        fy.closed_at = None
        fy.save()

        # Process projections again
        projection.process_pending(company)

        after_reopen = _snapshot_balances(company)
        tb_after = projection.get_trial_balance(company)

        assert before_close == after_reopen, (
            "Close+reopen cycle must not change balances"
        )
        assert tb_before["total_debit"] == tb_after["total_debit"]
        assert tb_before["total_credit"] == tb_after["total_credit"]
        assert tb_after["is_balanced"]


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 4: AR/AP subledger totals tie to control accounts
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestSubledgerTieOut:
    """
    After posting journal entries that affect AR/AP control accounts with
    customer/vendor counterparties, the subledger totals must exactly match
    the control account balances.

    This is the fundamental accounting integrity check between GL and subledgers.
    """

    def test_ar_tieout_after_invoice_and_payment(self, company, user):
        # Create AR control account
        ar_control = Account.objects.create(
            public_id=uuid4(), company=company, code="1200",
            name="Accounts Receivable",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.create(
            public_id=uuid4(), company=company, code="4100",
            name="Service Revenue",
            account_type=Account.AccountType.REVENUE,
            normal_balance=Account.NormalBalance.CREDIT,
            status=Account.Status.ACTIVE,
        )
        cash = Account.objects.create(
            public_id=uuid4(), company=company, code="1100",
            name="Cash",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )

        # Create customers
        cust1 = Customer.objects.create(
            public_id=uuid4(), company=company, code="C001",
            name="Customer One", status=Customer.Status.ACTIVE,
        )
        cust2 = Customer.objects.create(
            public_id=uuid4(), company=company, code="C002",
            name="Customer Two", status=Customer.Status.ACTIVE,
        )

        # Invoice to customer 1: debit AR, credit revenue
        _emit_posted(company, user, [
            _line(ar_control, debit="1000.00", line_no=1,
                  customer_public_id=cust1.public_id),
            _line(revenue, credit="1000.00", line_no=2),
        ], memo="Invoice C001")

        # Invoice to customer 2: debit AR, credit revenue
        _emit_posted(company, user, [
            _line(ar_control, debit="500.00", line_no=1,
                  customer_public_id=cust2.public_id),
            _line(revenue, credit="500.00", line_no=2),
        ], memo="Invoice C002")

        # Payment from customer 1: debit cash, credit AR
        _emit_posted(company, user, [
            _line(cash, debit="600.00", line_no=1),
            _line(ar_control, credit="600.00", line_no=2,
                  customer_public_id=cust1.public_id),
        ], memo="Payment C001")

        # Process both projections
        acct_proj = AccountBalanceProjection()
        sub_proj = SubledgerBalanceProjection()
        acct_proj.process_pending(company)
        sub_proj.process_pending(company)

        # AR control balance should equal sum of customer balances
        ar_balance = AccountBalance.objects.get(
            company=company, account=ar_control
        )
        cust1_bal = CustomerBalance.objects.get(company=company, customer=cust1)
        cust2_bal = CustomerBalance.objects.get(company=company, customer=cust2)

        assert ar_balance.balance == cust1_bal.balance + cust2_bal.balance, (
            f"AR tie-out failed: AR control={ar_balance.balance}, "
            f"C001={cust1_bal.balance}, C002={cust2_bal.balance}, "
            f"sum={cust1_bal.balance + cust2_bal.balance}"
        )

        # Also verify via the policy function
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"Subledger tie-out validation failed: {errors}"

    def test_ap_tieout_after_bill_and_payment(self, company, user):
        # Create AP control account
        ap_control = Account.objects.create(
            public_id=uuid4(), company=company, code="2100",
            name="Accounts Payable",
            account_type=Account.AccountType.LIABILITY,
            normal_balance=Account.NormalBalance.CREDIT,
            role=Account.AccountRole.PAYABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        expense = Account.objects.create(
            public_id=uuid4(), company=company, code="5100",
            name="Supplies Expense",
            account_type=Account.AccountType.EXPENSE,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        cash = Account.objects.create(
            public_id=uuid4(), company=company, code="1150",
            name="Cash",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )

        vendor1 = Vendor.objects.create(
            public_id=uuid4(), company=company, code="V001",
            name="Vendor One", status=Vendor.Status.ACTIVE,
        )

        # Bill from vendor: debit expense, credit AP
        _emit_posted(company, user, [
            _line(expense, debit="750.00", line_no=1),
            _line(ap_control, credit="750.00", line_no=2,
                  vendor_public_id=vendor1.public_id),
        ], memo="Bill V001")

        # Partial payment: debit AP, credit cash
        _emit_posted(company, user, [
            _line(ap_control, debit="300.00", line_no=1,
                  vendor_public_id=vendor1.public_id),
            _line(cash, credit="300.00", line_no=2),
        ], memo="Payment V001")

        acct_proj = AccountBalanceProjection()
        sub_proj = SubledgerBalanceProjection()
        acct_proj.process_pending(company)
        sub_proj.process_pending(company)

        # AP control balance should equal vendor balance
        ap_balance = AccountBalance.objects.get(
            company=company, account=ap_control
        )
        vendor_bal = VendorBalance.objects.get(company=company, vendor=vendor1)

        assert ap_balance.balance == vendor_bal.balance, (
            f"AP tie-out failed: AP control={ap_balance.balance}, "
            f"V001={vendor_bal.balance}"
        )

        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"Subledger tie-out validation failed: {errors}"

    def test_tieout_with_multi_line_same_customer(self, company, user):
        """Multiple lines to same customer in one event must still tie out."""
        ar_control = Account.objects.create(
            public_id=uuid4(), company=company, code="1250",
            name="AR Control",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.create(
            public_id=uuid4(), company=company, code="4200",
            name="Revenue",
            account_type=Account.AccountType.REVENUE,
            normal_balance=Account.NormalBalance.CREDIT,
            status=Account.Status.ACTIVE,
        )

        cust = Customer.objects.create(
            public_id=uuid4(), company=company, code="C010",
            name="Multi-line Customer", status=Customer.Status.ACTIVE,
        )

        # Two debit lines to same customer on same AR control (allocation pattern)
        _emit_posted(company, user, [
            _line(ar_control, debit="400.00", line_no=1,
                  customer_public_id=cust.public_id),
            _line(ar_control, debit="200.00", line_no=2,
                  customer_public_id=cust.public_id),
            _line(revenue, credit="600.00", line_no=3),
        ], memo="Multi-line invoice")

        acct_proj = AccountBalanceProjection()
        sub_proj = SubledgerBalanceProjection()
        acct_proj.process_pending(company)
        sub_proj.process_pending(company)

        ar_bal = AccountBalance.objects.get(company=company, account=ar_control)
        cust_bal = CustomerBalance.objects.get(company=company, customer=cust)

        # AR control = 600 (debit-normal: 600 - 0)
        assert ar_bal.balance == Decimal("600.00")
        # Customer balance = 600 (debit_total - credit_total = 600 - 0)
        assert cust_bal.balance == Decimal("600.00")
        assert ar_bal.balance == cust_bal.balance, (
            f"Multi-line AR tie-out failed: AR={ar_bal.balance}, "
            f"Customer={cust_bal.balance}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 5: Mixed-operation replay consistency
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestMixedOperationReplayConsistency:
    """
    After a sequence of normal postings, multi-line entries, and reversals,
    rebuilding from zero must produce identical state.

    This is the ultimate cross-cutting consistency check combining
    the truth invariants (replay=incremental) with the control invariants
    (subledger tie-out still holds after rebuild).
    """

    def test_rebuild_after_mixed_operations_preserves_everything(
        self, company, user, cash_account, revenue_account, expense_account
    ):
        # Normal entry
        _emit_posted(company, user, [
            _line(cash_account, debit="2000.00", line_no=1),
            _line(revenue_account, credit="2000.00", line_no=2),
        ], memo="Sale")

        # Multi-line same account
        _emit_posted(company, user, [
            _line(expense_account, debit="300.00", line_no=1),
            _line(expense_account, debit="200.00", line_no=2),
            _line(cash_account, credit="500.00", line_no=3),
        ], memo="Split expense")

        # Reversal
        _emit_posted(company, user, [
            _line(cash_account, credit="200.00", line_no=1),
            _line(revenue_account, debit="200.00", line_no=2),
        ], memo="Partial refund")

        # Another entry
        _emit_posted(company, user, [
            _line(cash_account, debit="800.00", line_no=1),
            _line(cash_account, debit="100.00", line_no=2),
            _line(revenue_account, credit="900.00", line_no=3),
        ], memo="Multi-line sale")

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        incremental = _snapshot_balances(company)
        tb_incremental = projection.get_trial_balance(company)

        # Verify incremental state is balanced
        assert tb_incremental["is_balanced"]

        # Verify via event replay
        verify_result = projection.verify_all_balances(company)
        assert verify_result["mismatches"] == [], (
            f"Pre-rebuild verification failed: {verify_result['mismatches']}"
        )

        # Rebuild from zero
        projection.rebuild(company)

        rebuilt = _snapshot_balances(company)
        tb_rebuilt = projection.get_trial_balance(company)

        # State must be identical
        assert incremental == rebuilt, (
            f"Rebuild diverged from incremental after mixed operations.\n"
            f"Incremental: {incremental}\n"
            f"Rebuilt: {rebuilt}"
        )
        assert tb_rebuilt["is_balanced"]
        assert tb_incremental["total_debit"] == tb_rebuilt["total_debit"]

        # Post-rebuild verification must also pass
        verify_after = projection.verify_all_balances(company)
        assert verify_after["mismatches"] == []

    def test_rebuild_preserves_subledger_tieout(self, company, user):
        """After rebuild, subledger tie-out must still hold."""
        ar_control = Account.objects.create(
            public_id=uuid4(), company=company, code="1300",
            name="AR Control",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.create(
            public_id=uuid4(), company=company, code="4300",
            name="Revenue",
            account_type=Account.AccountType.REVENUE,
            normal_balance=Account.NormalBalance.CREDIT,
            status=Account.Status.ACTIVE,
        )
        cash = Account.objects.create(
            public_id=uuid4(), company=company, code="1050",
            name="Cash",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )

        cust = Customer.objects.create(
            public_id=uuid4(), company=company, code="C020",
            name="Rebuild Customer", status=Customer.Status.ACTIVE,
        )

        # Invoice
        _emit_posted(company, user, [
            _line(ar_control, debit="1000.00", line_no=1,
                  customer_public_id=cust.public_id),
            _line(revenue, credit="1000.00", line_no=2),
        ])

        # Payment
        _emit_posted(company, user, [
            _line(cash, debit="400.00", line_no=1),
            _line(ar_control, credit="400.00", line_no=2,
                  customer_public_id=cust.public_id),
        ])

        acct_proj = AccountBalanceProjection()
        sub_proj = SubledgerBalanceProjection()
        acct_proj.process_pending(company)
        sub_proj.process_pending(company)

        # Verify before rebuild
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"Pre-rebuild tie-out failed: {errors}"

        # Rebuild account balance projection
        acct_proj.rebuild(company)
        # Rebuild subledger projection
        sub_proj.rebuild(company)

        # Verify after rebuild
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"Post-rebuild tie-out failed: {errors}"

        # Check actual values
        ar_bal = AccountBalance.objects.get(company=company, account=ar_control)
        cust_bal = CustomerBalance.objects.get(company=company, customer=cust)
        assert ar_bal.balance == Decimal("600.00")
        assert cust_bal.balance == Decimal("600.00")


# ═════════════════════════════════════════════════════════════════════════════
# INVARIANT 6: Mixed inline/external payloads + reversals + close/reopen
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestMixedPayloadCloseReopenReplay:
    """
    After a sequence that includes:
    - inline payload events (small JEs)
    - external payload events (large JEs >64KB via LEPH)
    - reversals
    - fiscal year close + reopen cycle

    ...rebuilding from zero must produce identical state to incremental
    processing, the trial balance must be balanced, and subledger
    tie-out must hold.

    This is the strongest cross-cutting invariant: it proves that storage
    mode, reversal logic, fiscal year lifecycle, and projection rebuild
    are all mutually compatible.
    """

    def test_mixed_payload_close_reopen_rebuild_matches_incremental(
        self, company, user, owner_membership
    ):
        # ─── Create accounts ──────────────────────────────────────────
        cash = Account.objects.create(
            public_id=uuid4(), company=company, code="1010",
            name="Cash", account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        ar_control = Account.objects.create(
            public_id=uuid4(), company=company, code="1210",
            name="AR Control", account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.create(
            public_id=uuid4(), company=company, code="4010",
            name="Revenue", account_type=Account.AccountType.REVENUE,
            normal_balance=Account.NormalBalance.CREDIT,
            status=Account.Status.ACTIVE,
        )

        # Create 300 expense accounts for the large external-payload event
        expense_accounts = []
        for i in range(300):
            acct = Account.objects.create(
                public_id=uuid4(), company=company, code=f"{7000 + i}",
                name=f"Mixed Expense {i}",
                account_type=Account.AccountType.EXPENSE,
                normal_balance=Account.NormalBalance.DEBIT,
                status=Account.Status.ACTIVE,
            )
            expense_accounts.append(acct)

        cust = Customer.objects.create(
            public_id=uuid4(), company=company, code="C030",
            name="Mixed Test Customer", status=Customer.Status.ACTIVE,
        )

        # ─── Event 1: Small inline invoice ────────────────────────────
        _emit_posted(company, user, [
            _line(ar_control, debit="2000.00", line_no=1,
                  customer_public_id=cust.public_id),
            _line(revenue, credit="2000.00", line_no=2),
        ], memo="Inline invoice")

        # ─── Event 2: Large external-payload event (>64KB) ───────────
        lines = []
        for i, acct in enumerate(expense_accounts):
            lines.append(_line(
                acct, debit="50.00", line_no=i + 1,
            ))
            # Pad description to push payload past 64KB
            lines[-1]["description"] = f"Expense line {i + 1} " + ("x" * 100)

        total_expense = Decimal("50.00") * 300  # 15000.00
        lines.append(_line(cash, credit=str(total_expense), line_no=301))

        # Verify this payload is actually external
        entry_id = uuid4()
        data = {
            "entry_public_id": str(entry_id),
            "entry_number": "JE-MIXED-EXT",
            "date": date.today().isoformat(),
            "memo": "Large external expense batch",
            "kind": "NORMAL",
            "posted_at": timezone.now().isoformat(),
            "posted_by_id": user.id,
            "posted_by_email": user.email,
            "total_debit": str(total_expense),
            "total_credit": str(total_expense),
            "lines": lines,
        }
        assert estimate_json_size(data) > INLINE_MAX_SIZE, (
            "Payload must exceed inline threshold for external storage"
        )

        ext_event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data=data,
            caused_by_user=user,
            idempotency_key=f"ctrl-mixed-ext:{entry_id}",
        )
        assert ext_event.payload_storage == "external"

        # ─── Event 3: Partial payment (inline) ───────────────────────
        _emit_posted(company, user, [
            _line(cash, debit="800.00", line_no=1),
            _line(ar_control, credit="800.00", line_no=2,
                  customer_public_id=cust.public_id),
        ], memo="Partial payment")

        # ─── Event 4: Reversal of part of the invoice ────────────────
        _emit_posted(company, user, [
            _line(revenue, debit="500.00", line_no=1),
            _line(ar_control, credit="500.00", line_no=2,
                  customer_public_id=cust.public_id),
        ], memo="Credit note / partial reversal")

        # ─── Process projections incrementally ────────────────────────
        acct_proj = AccountBalanceProjection()
        sub_proj = SubledgerBalanceProjection()
        acct_proj.process_pending(company)
        sub_proj.process_pending(company)

        incremental_balances = _snapshot_balances(company)
        tb_incremental = acct_proj.get_trial_balance(company)

        # Sanity: trial balance must be balanced
        assert tb_incremental["is_balanced"], (
            f"Incremental TB not balanced: "
            f"debit={tb_incremental['total_debit']}, "
            f"credit={tb_incremental['total_credit']}"
        )

        # Sanity: subledger tie-out must hold
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"Pre-close tie-out failed: {errors}"

        # Expected AR balance: 2000 invoice - 800 payment - 500 reversal = 700
        ar_bal = AccountBalance.objects.get(company=company, account=ar_control)
        cust_bal = CustomerBalance.objects.get(company=company, customer=cust)
        assert ar_bal.balance == Decimal("700.00"), (
            f"AR control expected 700.00, got {ar_bal.balance}"
        )
        assert cust_bal.balance == Decimal("700.00"), (
            f"Customer balance expected 700.00, got {cust_bal.balance}"
        )

        # ─── Fiscal year close + reopen cycle ─────────────────────────
        today = date.today()
        fy, _ = FiscalYearModel.objects.get_or_create(
            company=company,
            fiscal_year=today.year,
            defaults={"status": FiscalYearModel.Status.OPEN},
        )
        fy.status = FiscalYearModel.Status.CLOSED
        fy.closed_at = timezone.now()
        fy.save()

        fy.status = FiscalYearModel.Status.OPEN
        fy.closed_at = None
        fy.save()

        # Process again after close/reopen (should be no-op)
        acct_proj.process_pending(company)
        sub_proj.process_pending(company)

        after_reopen = _snapshot_balances(company)
        assert incremental_balances == after_reopen, (
            "Close/reopen cycle must not change balances"
        )

        # ─── Rebuild from zero ────────────────────────────────────────
        acct_proj.rebuild(company)
        sub_proj.rebuild(company)

        rebuilt_balances = _snapshot_balances(company)
        tb_rebuilt = acct_proj.get_trial_balance(company)

        # State must be identical to incremental
        assert incremental_balances == rebuilt_balances, (
            f"Rebuild diverged after mixed inline/external + reversals + close/reopen.\n"
            f"Incremental: {incremental_balances}\n"
            f"Rebuilt: {rebuilt_balances}"
        )

        # Trial balance must still be balanced
        assert tb_rebuilt["is_balanced"], (
            f"Rebuilt TB not balanced: "
            f"debit={tb_rebuilt['total_debit']}, "
            f"credit={tb_rebuilt['total_credit']}"
        )
        assert tb_incremental["total_debit"] == tb_rebuilt["total_debit"]
        assert tb_incremental["total_credit"] == tb_rebuilt["total_credit"]

        # Subledger tie-out must still hold after rebuild
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"Post-rebuild tie-out failed: {errors}"

        # Verify via event replay
        verify_result = acct_proj.verify_all_balances(company)
        assert verify_result["mismatches"] == [], (
            f"Post-rebuild verification failed: {verify_result['mismatches']}"
        )
