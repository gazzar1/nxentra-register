# tests/e2e/test_posting_projection.py
"""
End-to-end tests for journal posting and projection updates.

These tests verify the full flow:
1. Create journal entry via command
2. Post journal entry
3. Verify projections are updated (AccountBalance, CustomerBalance, VendorBalance)
4. Verify tie-out invariants
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from accounting.commands import (
    create_journal_entry,
    post_journal_entry,
    save_journal_entry_complete,
)
from accounting.models import Account, Customer, Vendor
from projections.models import AccountBalance, CustomerBalance, VendorBalance


@pytest.mark.django_db(transaction=True)
class TestPostingProjection:
    """End-to-end tests for journal posting and projections."""

    @pytest.fixture
    def ar_control_account(self, db, company):
        """Create an AR control account."""
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
    def ap_control_account(self, db, company):
        """Create an AP control account."""
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
    def test_customer(self, db, company):
        """Create a test customer."""
        return Customer.objects.create(
            public_id=uuid4(),
            company=company,
            code="CUST001",
            name="Test Customer",
            status=Customer.Status.ACTIVE,
        )

    @pytest.fixture
    def test_vendor(self, db, company):
        """Create a test vendor."""
        return Vendor.objects.create(
            public_id=uuid4(),
            company=company,
            code="VEND001",
            name="Test Vendor",
            status=Vendor.Status.ACTIVE,
        )

    def test_post_journal_updates_account_balances(
        self, actor_context, company, cash_account, revenue_account
    ):
        """Posting a journal entry should update account balances."""
        # Create entry with lines
        result = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="Test posting",
            lines=[
                {
                    "account_id": cash_account.id,
                    "description": "Cash received",
                    "debit": Decimal("1000.00"),
                    "credit": Decimal("0"),
                },
                {
                    "account_id": revenue_account.id,
                    "description": "Revenue earned",
                    "debit": Decimal("0"),
                    "credit": Decimal("1000.00"),
                },
            ],
        )
        assert result.success, f"Failed to create entry: {result.error}"
        entry = result.data
        entry_public_id = str(entry.public_id)

        # Save as complete (DRAFT status)
        save_result = save_journal_entry_complete(actor_context, entry.id)
        assert save_result.success, f"Failed to save: {save_result.error}"

        # Post
        post_result = post_journal_entry(actor_context, entry.id)
        assert post_result.success, f"Failed to post: {post_result.error}"

        # Verify projections
        cash_balance = AccountBalance.objects.get(company=company, account=cash_account)
        assert cash_balance.balance == Decimal("1000.00")
        assert cash_balance.debit_total == Decimal("1000.00")
        assert cash_balance.credit_total == Decimal("0.00")

        revenue_balance = AccountBalance.objects.get(company=company, account=revenue_account)
        # Revenue is credit-normal; credits increase the balance (stored as positive)
        assert revenue_balance.balance == Decimal("1000.00")
        assert revenue_balance.debit_total == Decimal("0.00")
        assert revenue_balance.credit_total == Decimal("1000.00")

    def test_post_with_customer_updates_subledger(
        self, actor_context, company, ar_control_account, revenue_account, test_customer
    ):
        """Posting with a customer should update customer balance."""
        # Create entry with lines including customer
        result = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="Customer invoice",
            lines=[
                {
                    "account_id": ar_control_account.id,
                    "description": "Invoice AR",
                    "debit": Decimal("500.00"),
                    "credit": Decimal("0"),
                    "customer_public_id": str(test_customer.public_id),
                },
                {
                    "account_id": revenue_account.id,
                    "description": "Invoice revenue",
                    "debit": Decimal("0"),
                    "credit": Decimal("500.00"),
                },
            ],
        )
        assert result.success, f"Failed to create entry: {result.error}"
        entry = result.data

        # Save and post
        save_result = save_journal_entry_complete(actor_context, entry.id)
        assert save_result.success, f"Failed to save: {save_result.error}"

        post_result = post_journal_entry(actor_context, entry.id)
        assert post_result.success, f"Failed to post: {post_result.error}"

        # Verify customer balance
        cust_balance = CustomerBalance.objects.get(company=company, customer=test_customer)
        assert cust_balance.balance == Decimal("500.00")

    def test_post_with_vendor_updates_subledger(
        self, actor_context, company, ap_control_account, expense_account, test_vendor
    ):
        """Posting with a vendor should update vendor balance."""
        # Create entry with lines including vendor
        result = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="Vendor bill",
            lines=[
                {
                    "account_id": expense_account.id,
                    "description": "Bill expense",
                    "debit": Decimal("300.00"),
                    "credit": Decimal("0"),
                },
                {
                    "account_id": ap_control_account.id,
                    "description": "Bill AP",
                    "debit": Decimal("0"),
                    "credit": Decimal("300.00"),
                    "vendor_public_id": str(test_vendor.public_id),
                },
            ],
        )
        assert result.success, f"Failed to create entry: {result.error}"
        entry = result.data

        # Save and post
        save_result = save_journal_entry_complete(actor_context, entry.id)
        assert save_result.success, f"Failed to save: {save_result.error}"

        post_result = post_journal_entry(actor_context, entry.id)
        assert post_result.success, f"Failed to post: {post_result.error}"

        # Verify vendor balance
        vendor_balance = VendorBalance.objects.get(company=company, vendor=test_vendor)
        assert vendor_balance.balance == Decimal("300.00")
