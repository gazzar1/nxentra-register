# tests/test_a155_reversal_void_family.py
"""
A155 — reversal + document-void family (2026-07-11 dual audit, CRITICAL).

Audited failure modes:
1. reverse_journal_entry dropped customer/vendor counterparty on reversal
   lines — GL control reversed but CustomerBalance/VendorBalance never did,
   breaking subledger tie-out and blocking year-end close.
2. void_sales_invoice / void_purchase_bill / void_purchase_credit_note
   created a kind=REVERSAL DRAFT then called post_journal_entry, whose
   postable_kinds excludes REVERSAL — the void ALWAYS failed, and the
   return-based failure committed the orphan DRAFT + its events.
3. void_credit_note read reverse_result.data.public_id but the command
   returns a dict {"original", "reversal"} — AttributeError on every void.

Fix under test: one canonical counterparty-preserving reversal core
(_reverse_posted_journal_entry) used by the reverse endpoint and all four
voids; failed voids raise inside a savepoint so nothing survives; a
System Health check surfaces any stranded reversal drafts.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from accounting.commands import (
    create_journal_entry,
    delete_journal_entry,
    post_journal_entry,
    reverse_journal_entry,
    save_journal_entry_complete,
)
from accounting.models import Account, Customer, JournalEntry, Vendor
from accounting.policies import validate_subledger_tieout
from projections.models import CustomerBalance, VendorBalance
from projections.write_barrier import command_writes_allowed
from sales.models import PostingProfile

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def ar_control(company):
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code="1200",
        name="Accounts Receivable",
        account_type=Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        role=Account.AccountRole.RECEIVABLE_CONTROL,
        requires_counterparty=True,
        counterparty_kind="CUSTOMER",
        status=Account.Status.ACTIVE,
    )


@pytest.fixture
def ap_control(company):
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code="2100",
        name="Accounts Payable",
        account_type=Account.AccountType.LIABILITY,
        normal_balance=Account.NormalBalance.CREDIT,
        role=Account.AccountRole.PAYABLE_CONTROL,
        requires_counterparty=True,
        counterparty_kind="VENDOR",
        status=Account.Status.ACTIVE,
    )


@pytest.fixture
def customer(company):
    return Customer.objects.create(
        public_id=uuid4(),
        company=company,
        code="CUST001",
        name="Test Customer",
        status=Customer.Status.ACTIVE,
    )


@pytest.fixture
def vendor(company):
    return Vendor.objects.create(
        public_id=uuid4(),
        company=company,
        code="VEND001",
        name="Test Vendor",
        status=Vendor.Status.ACTIVE,
    )


@pytest.fixture
def customer_profile(company, ar_control):
    with command_writes_allowed():
        return PostingProfile.objects.create(
            company=company,
            code="A155-AR",
            name="A155 AR Profile",
            profile_type=PostingProfile.ProfileType.CUSTOMER,
            control_account=ar_control,
            is_active=True,
        )


@pytest.fixture
def vendor_profile(company, ap_control):
    with command_writes_allowed():
        return PostingProfile.objects.create(
            company=company,
            code="A155-AP",
            name="A155 AP Profile",
            profile_type=PostingProfile.ProfileType.VENDOR,
            control_account=ap_control,
            is_active=True,
        )


def _post_counterparty_je(actor, control, offset, *, customer=None, vendor=None, amount=Decimal("100.00")):
    """Create+complete+post a JE hitting a counterparty control account."""
    control_line = {
        "account_id": control.id,
        "description": "control",
        "debit": amount if customer else Decimal("0"),
        "credit": Decimal("0") if customer else amount,
    }
    if customer:
        control_line["customer_public_id"] = str(customer.public_id)
    if vendor:
        control_line["vendor_public_id"] = str(vendor.public_id)
    offset_line = {
        "account_id": offset.id,
        "description": "offset",
        "debit": Decimal("0") if customer else amount,
        "credit": amount if customer else Decimal("0"),
    }
    result = create_journal_entry(
        actor,
        date=date.today(),
        memo="A155 counterparty entry",
        lines=[control_line, offset_line],
    )
    assert result.success, result.error
    entry = result.data
    assert save_journal_entry_complete(actor, entry.id).success
    post_result = post_journal_entry(actor, entry.id)
    assert post_result.success, post_result.error
    return JournalEntry.objects.get(pk=entry.id)


def _customer_balance(company):
    row = CustomerBalance.objects.filter(company=company).first()
    return row.balance if row else Decimal("0")


def _vendor_balance(company):
    row = VendorBalance.objects.filter(company=company).first()
    return row.balance if row else Decimal("0")


def _orphan_reversal_count(company):
    return JournalEntry.objects.filter(
        company=company,
        kind=JournalEntry.Kind.REVERSAL,
        status__in=[JournalEntry.Status.INCOMPLETE, JournalEntry.Status.DRAFT],
    ).count()


# ─────────────────────────────────────────────────────────────────────────────
# 1+2. Reversal preserves counterparty (AR + AP)
# ─────────────────────────────────────────────────────────────────────────────


class TestReversalCounterparty:
    def test_ar_reversal_preserves_counterparty_and_tieout(
        self, actor_context, company, ar_control, revenue_account, customer
    ):
        entry = _post_counterparty_je(actor_context, ar_control, revenue_account, customer=customer)
        assert _customer_balance(company) == Decimal("100.00")

        result = reverse_journal_entry(actor_context, entry.id)
        assert result.success, result.error
        reversal = result.data["reversal"]

        # The reversal's control line must carry the customer counterparty
        control_lines = reversal.lines.filter(account=ar_control)
        assert control_lines.exists()
        assert control_lines.first().customer_id == customer.id, (
            "reversal line dropped customer counterparty — subledger will not reverse"
        )

        assert _customer_balance(company) == Decimal("0"), "CustomerBalance must reverse with the GL"
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"tie-out broken after reversal: {errors}"

    def test_ap_reversal_preserves_counterparty_and_tieout(
        self, actor_context, company, ap_control, expense_account, vendor
    ):
        entry = _post_counterparty_je(actor_context, ap_control, expense_account, vendor=vendor)
        assert _vendor_balance(company) == Decimal("100.00")

        result = reverse_journal_entry(actor_context, entry.id)
        assert result.success, result.error
        reversal = result.data["reversal"]

        control_lines = reversal.lines.filter(account=ap_control)
        assert control_lines.exists()
        assert control_lines.first().vendor_id == vendor.id, (
            "reversal line dropped vendor counterparty — subledger will not reverse"
        )

        assert _vendor_balance(company) == Decimal("0"), "VendorBalance must reverse with the GL"
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"tie-out broken after reversal: {errors}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. void_sales_invoice
# ─────────────────────────────────────────────────────────────────────────────


def _make_posted_invoice(actor, customer, profile, revenue, amount="100.00"):
    from sales.commands import create_sales_invoice, post_sales_invoice

    result = create_sales_invoice(
        actor=actor,
        customer_id=customer.id,
        posting_profile_id=profile.id,
        invoice_date=date.today(),
        lines=[
            {
                "account_id": revenue.id,
                "description": "Service",
                "quantity": "1",
                "unit_price": amount,
                "discount_amount": "0",
            }
        ],
    )
    assert result.success, result.error
    invoice = result.data["invoice"]
    post_result = post_sales_invoice(actor, invoice.id)
    assert post_result.success, post_result.error
    invoice.refresh_from_db()
    return invoice


class TestVoidSalesInvoice:
    def test_void_completes_and_nets_to_zero(
        self, actor_context, company, ar_control, revenue_account, customer, customer_profile
    ):
        invoice = _make_posted_invoice(actor_context, customer, customer_profile, revenue_account)
        assert _customer_balance(company) == Decimal("100.00")
        original_je_id = invoice.posted_journal_entry_id

        from sales.commands import void_sales_invoice
        from sales.models import SalesInvoice

        result = void_sales_invoice(actor_context, invoice.id, reason="test void")
        assert result.success, f"void must complete: {result.error}"

        invoice.refresh_from_db()
        assert invoice.status == SalesInvoice.Status.VOIDED
        assert _customer_balance(company) == Decimal("0"), "void must net subledger to zero"
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"tie-out broken after void: {errors}"
        assert _orphan_reversal_count(company) == 0

        original_je = JournalEntry.objects.get(pk=original_je_id)
        assert original_je.status == JournalEntry.Status.REVERSED, "void must mark the original document JE REVERSED"
        reversal_je = result.data["reversing_entry"]
        assert reversal_je.status == JournalEntry.Status.POSTED
        assert "test void" in reversal_je.memo

    def test_failed_void_leaves_no_orphan_and_no_status_change(
        self, actor_context, company, ar_control, revenue_account, customer, customer_profile
    ):
        """Injected failure: the invoice's period is closed, so the reversal
        cannot post. Before the fix the failed void COMMITTED an orphan
        DRAFT reversal + events; now nothing survives."""
        from projections.models import FiscalPeriod
        from sales.commands import void_sales_invoice
        from sales.models import SalesInvoice

        invoice = _make_posted_invoice(actor_context, customer, customer_profile, revenue_account)
        je_count_before = JournalEntry.objects.filter(company=company).count()

        today = date.today()
        FiscalPeriod.objects.filter(
            company=company,
            start_date__lte=today,
            end_date__gte=today,
            period_type=FiscalPeriod.PeriodType.NORMAL,
        ).update(status=FiscalPeriod.Status.CLOSED)

        result = void_sales_invoice(actor_context, invoice.id, reason="should fail")
        assert not result.success

        invoice.refresh_from_db()
        assert invoice.status == SalesInvoice.Status.POSTED, "failed void must not change document status"
        assert _orphan_reversal_count(company) == 0, "failed void must not strand a DRAFT reversal"
        assert JournalEntry.objects.filter(company=company).count() == je_count_before, (
            "failed void must not leave any new journal entries behind"
        )
        assert _customer_balance(company) == Decimal("100.00"), "failed void must not move balances"

    def test_void_after_manual_reverse_fails_cleanly(
        self, actor_context, company, ar_control, revenue_account, customer, customer_profile
    ):
        """Pin the idempotency edge: if the invoice's JE was already reversed
        via the /reverse/ endpoint, the void surfaces a clean error and
        leaves no debris."""
        from sales.commands import void_sales_invoice
        from sales.models import SalesInvoice

        invoice = _make_posted_invoice(actor_context, customer, customer_profile, revenue_account)
        reverse_result = reverse_journal_entry(actor_context, invoice.posted_journal_entry_id)
        assert reverse_result.success, reverse_result.error

        result = void_sales_invoice(actor_context, invoice.id, reason="second undo")
        assert not result.success
        # The original JE is REVERSED after the manual reverse, so the core
        # refuses ("Only POSTED entries can be reversed."). Pin that the void
        # surfaces a clean reversal-state error, whatever the exact wording.
        assert "reversed" in result.error.lower()

        invoice.refresh_from_db()
        assert invoice.status == SalesInvoice.Status.POSTED
        assert _orphan_reversal_count(company) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. void_purchase_bill
# ─────────────────────────────────────────────────────────────────────────────


def _make_posted_bill(actor, vendor, profile, expense, amount="80.00"):
    from purchases.commands import create_purchase_bill, post_purchase_bill

    result = create_purchase_bill(
        actor=actor,
        vendor_id=vendor.id,
        posting_profile_id=profile.id,
        bill_date=date.today(),
        lines=[
            {
                "account_id": expense.id,
                "description": "Supplies",
                "quantity": "1",
                "unit_price": amount,
                "discount_amount": "0",
            }
        ],
    )
    assert result.success, result.error
    bill = result.data["bill"] if isinstance(result.data, dict) else result.data
    post_result = post_purchase_bill(actor, bill.id)
    assert post_result.success, post_result.error
    bill.refresh_from_db()
    return bill


class TestVoidPurchaseBill:
    def test_void_completes_and_nets_to_zero(
        self, actor_context, company, ap_control, expense_account, vendor, vendor_profile
    ):
        bill = _make_posted_bill(actor_context, vendor, vendor_profile, expense_account)
        assert _vendor_balance(company) == Decimal("80.00")
        original_je_id = bill.posted_journal_entry_id

        from purchases.commands import void_purchase_bill
        from purchases.models import PurchaseBill

        result = void_purchase_bill(actor_context, bill.id, reason="test void")
        assert result.success, f"void must complete: {result.error}"

        bill.refresh_from_db()
        assert bill.status == PurchaseBill.Status.VOIDED
        assert _vendor_balance(company) == Decimal("0")
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"tie-out broken after void: {errors}"
        assert _orphan_reversal_count(company) == 0
        assert JournalEntry.objects.get(pk=original_je_id).status == JournalEntry.Status.REVERSED


# ─────────────────────────────────────────────────────────────────────────────
# 5. void_credit_note (sales) — the AttributeError crash
# ─────────────────────────────────────────────────────────────────────────────


class TestVoidSalesCreditNote:
    def test_void_round_trip_restores_amount_paid(
        self, actor_context, company, ar_control, revenue_account, customer, customer_profile
    ):
        from sales.commands import create_credit_note, post_credit_note, void_credit_note
        from sales.models import SalesCreditNote

        invoice = _make_posted_invoice(actor_context, customer, customer_profile, revenue_account)
        amount_paid_before_cn = invoice.amount_paid

        cn_result = create_credit_note(
            actor_context,
            invoice.id,
            lines=[
                {
                    "account_id": revenue_account.id,
                    "description": "Credit",
                    "quantity": "1",
                    "unit_price": "100.00",
                    "discount_amount": "0",
                }
            ],
            reason="OTHER",
        )
        assert cn_result.success, cn_result.error
        cn = cn_result.data["credit_note"]
        post_result = post_credit_note(actor_context, cn.id)
        assert post_result.success, post_result.error
        invoice.refresh_from_db()
        amount_paid_after_cn = invoice.amount_paid
        assert amount_paid_after_cn != amount_paid_before_cn

        # Before the fix this crashed: AttributeError('dict' object has no
        # attribute 'public_id') at sales/commands.py:2084.
        void_result = void_credit_note(actor_context, cn.id, reason="undo credit")
        assert void_result.success, f"void_credit_note must complete: {void_result.error}"

        cn.refresh_from_db()
        invoice.refresh_from_db()
        assert cn.status == SalesCreditNote.Status.VOIDED
        assert invoice.amount_paid == amount_paid_before_cn, "void must restore invoice amount_paid"
        assert _customer_balance(company) == Decimal("100.00"), "customer owes the invoice again"
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"tie-out broken after credit-note void: {errors}"
        assert _orphan_reversal_count(company) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 6. void_purchase_credit_note
# ─────────────────────────────────────────────────────────────────────────────


class TestVoidPurchaseCreditNote:
    def test_void_completes_and_nets_to_zero(
        self, actor_context, company, ap_control, expense_account, vendor, vendor_profile
    ):
        from purchases.commands import (
            create_purchase_credit_note,
            post_purchase_credit_note,
            void_purchase_credit_note,
        )
        from purchases.models import PurchaseCreditNote

        bill = _make_posted_bill(actor_context, vendor, vendor_profile, expense_account)
        assert _vendor_balance(company) == Decimal("80.00")

        cn_result = create_purchase_credit_note(
            actor_context,
            bill.id,
            lines=[
                {
                    "account_id": expense_account.id,
                    "description": "Return",
                    "quantity": "1",
                    "unit_price": "80.00",
                }
            ],
        )
        assert cn_result.success, cn_result.error
        cn = cn_result.data["credit_note"]
        post_result = post_purchase_credit_note(actor_context, cn.id)
        assert post_result.success, post_result.error
        assert _vendor_balance(company) == Decimal("0")

        void_result = void_purchase_credit_note(actor_context, cn.id, reason="undo return")
        assert void_result.success, f"void must complete: {void_result.error}"

        cn.refresh_from_db()
        assert cn.status == PurchaseCreditNote.Status.VOIDED
        assert _vendor_balance(company) == Decimal("80.00"), "vendor is owed again after CN void"
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"tie-out broken: {errors}"
        assert _orphan_reversal_count(company) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 7. System Health orphan detector
# ─────────────────────────────────────────────────────────────────────────────


class TestOrphanReversalDetector:
    def test_system_health_flags_and_clears_orphan_reversal_drafts(
        self, actor_context, authenticated_client, owner_membership, company, cash_account, revenue_account
    ):
        # Manufacture an orphan the way the old broken voids did: a DRAFT
        # kind=REVERSAL entry that will never post.
        result = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="stranded reversal",
            lines=[
                {"account_id": cash_account.id, "description": "d", "debit": Decimal("10"), "credit": Decimal("0")},
                {"account_id": revenue_account.id, "description": "c", "debit": Decimal("0"), "credit": Decimal("10")},
            ],
            kind=JournalEntry.Kind.REVERSAL,
        )
        assert result.success, result.error
        entry = result.data
        assert save_journal_entry_complete(actor_context, entry.id).success

        resp = authenticated_client.get("/api/reports/system-health/")
        assert resp.status_code == 200, resp.content
        checks = {c["check"]: c for c in resp.json()["checks"]}
        assert "orphan_reversal_drafts" in checks, "System Health must include the orphan-reversal check"
        assert checks["orphan_reversal_drafts"]["status"] == "FAIL"

        assert delete_journal_entry(actor_context, entry.id).success

        resp = authenticated_client.get("/api/reports/system-health/")
        checks = {c["check"]: c for c in resp.json()["checks"]}
        assert checks["orphan_reversal_drafts"]["status"] == "PASS"
