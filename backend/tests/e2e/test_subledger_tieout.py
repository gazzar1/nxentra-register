# tests/e2e/test_subledger_tieout.py
"""
End-to-end tests for subledger tie-out invariants.

These tests verify that:
1. AR control account balance equals sum of customer balances
2. AP control account balance equals sum of vendor balances
3. validate_subledger_tieout policy function works correctly
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
from accounting.policies import validate_subledger_tieout
from projections.models import AccountBalance, CustomerBalance


@pytest.mark.django_db(transaction=True)
class TestSubledgerTieout:
    """End-to-end tests for subledger tie-out validation."""

    @pytest.fixture
    def ar_control(self, db, company):
        """Create AR control account."""
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
    def ap_control(self, db, company):
        """Create AP control account."""
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
    def customers(self, db, company):
        """Create multiple test customers."""
        return [
            Customer.objects.create(
                public_id=uuid4(),
                company=company,
                code=f"CUST{i:03d}",
                name=f"Customer {i}",
                status=Customer.Status.ACTIVE,
            )
            for i in range(1, 4)
        ]

    @pytest.fixture
    def vendors(self, db, company):
        """Create multiple test vendors."""
        return [
            Vendor.objects.create(
                public_id=uuid4(),
                company=company,
                code=f"VEND{i:03d}",
                name=f"Vendor {i}",
                status=Vendor.Status.ACTIVE,
            )
            for i in range(1, 4)
        ]

    def test_empty_company_is_balanced(self, company):
        """A company with no transactions should be balanced."""
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"Empty company should be balanced: {errors}"

    def test_ar_tieout_after_customer_invoices(self, actor_context, company, ar_control, revenue_account, customers):
        """AR control should match sum of customer balances after invoices."""
        # Create invoices for multiple customers
        amounts = [Decimal("100.00"), Decimal("250.00"), Decimal("175.00")]
        expected_total = sum(amounts)

        for customer, amount in zip(customers, amounts):
            result = create_journal_entry(
                actor_context,
                date=date.today(),
                memo=f"Invoice {customer.code}",
                lines=[
                    {
                        "account_id": ar_control.id,
                        "description": f"AR for {customer.code}",
                        "debit": amount,
                        "credit": Decimal("0"),
                        "customer_public_id": str(customer.public_id),
                    },
                    {
                        "account_id": revenue_account.id,
                        "description": "Revenue",
                        "debit": Decimal("0"),
                        "credit": amount,
                    },
                ],
            )
            assert result.success, f"Failed to create entry: {result.error}"
            entry = result.data

            save_result = save_journal_entry_complete(actor_context, entry.id)
            assert save_result.success, f"Failed to save: {save_result.error}"

            post_result = post_journal_entry(actor_context, entry.id)
            assert post_result.success, f"Failed to post: {post_result.error}"

        # Verify tie-out
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"AR should tie out: {errors}"

        # Verify individual balances
        ar_balance = AccountBalance.objects.get(company=company, account=ar_control)
        assert ar_balance.balance == expected_total

        customer_total = sum(CustomerBalance.objects.filter(company=company).values_list("balance", flat=True))
        assert customer_total == expected_total

    def test_ap_tieout_after_vendor_bills(self, actor_context, company, ap_control, expense_account, vendors):
        """AP control should match sum of vendor balances after bills."""
        amounts = [Decimal("500.00"), Decimal("300.00"), Decimal("200.00")]
        expected_total = sum(amounts)

        for vendor, amount in zip(vendors, amounts):
            result = create_journal_entry(
                actor_context,
                date=date.today(),
                memo=f"Bill {vendor.code}",
                lines=[
                    {
                        "account_id": expense_account.id,
                        "description": "Expense",
                        "debit": amount,
                        "credit": Decimal("0"),
                    },
                    {
                        "account_id": ap_control.id,
                        "description": f"AP for {vendor.code}",
                        "debit": Decimal("0"),
                        "credit": amount,
                        "vendor_public_id": str(vendor.public_id),
                    },
                ],
            )
            assert result.success, f"Failed to create entry: {result.error}"
            entry = result.data

            save_result = save_journal_entry_complete(actor_context, entry.id)
            assert save_result.success, f"Failed to save: {save_result.error}"

            post_result = post_journal_entry(actor_context, entry.id)
            assert post_result.success, f"Failed to post: {post_result.error}"

        # Verify tie-out
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"AP should tie out: {errors}"

        # Verify totals
        ap_balance = AccountBalance.objects.get(company=company, account=ap_control)
        # AP is credit-normal, so balance is negative from GL perspective
        assert abs(ap_balance.balance) == expected_total

    def test_partial_payment_maintains_tieout(self, actor_context, company, ar_control, cash_account, customers):
        """Partial customer payment should maintain tie-out."""
        customer = customers[0]
        invoice_amount = Decimal("1000.00")
        payment_amount = Decimal("400.00")

        # Create invoice
        result = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="Invoice",
            lines=[
                {
                    "account_id": ar_control.id,
                    "description": "AR Invoice",
                    "debit": invoice_amount,
                    "credit": Decimal("0"),
                    "customer_public_id": str(customer.public_id),
                },
                {
                    "account_id": cash_account.id,
                    "description": "Cash",
                    "debit": Decimal("0"),
                    "credit": invoice_amount,
                },
            ],
        )
        assert result.success, f"Failed to create invoice: {result.error}"
        entry = result.data
        save_journal_entry_complete(actor_context, entry.id)
        post_journal_entry(actor_context, entry.id)

        # Create partial payment
        result = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="Payment",
            lines=[
                {
                    "account_id": cash_account.id,
                    "description": "Cash received",
                    "debit": payment_amount,
                    "credit": Decimal("0"),
                },
                {
                    "account_id": ar_control.id,
                    "description": "AR Payment",
                    "debit": Decimal("0"),
                    "credit": payment_amount,
                    "customer_public_id": str(customer.public_id),
                },
            ],
        )
        assert result.success, f"Failed to create payment: {result.error}"
        entry = result.data
        save_journal_entry_complete(actor_context, entry.id)
        post_journal_entry(actor_context, entry.id)

        # Verify tie-out
        is_valid, errors = validate_subledger_tieout(company)
        assert is_valid, f"Should still tie out after payment: {errors}"

        # Verify remaining balance
        cust_balance = CustomerBalance.objects.get(company=company, customer=customer)
        expected_remaining = invoice_amount - payment_amount
        assert cust_balance.balance == expected_remaining
