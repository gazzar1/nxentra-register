"""
A194 — foreign-currency receipts/payments must post BALANCED journal entries.

The realized-FX branch of ``record_customer_receipt`` / ``record_vendor_payment``
overwrote the *functional* bank line with a *foreign* amount
(``lines[0]["debit"] = str(receipt_amount + realized_fx_total)``), emitting an
UNBALANCED ``JOURNAL_ENTRY_POSTED`` straight to the projection with no
debit==credit check — silently corrupting the trial balance
(operator-safety Rule 2). These tests prove:

1. a foreign receipt/payment allocated against a foreign invoice/bill posts a
   balanced entry with the correct realized FX (bank = actual cash, counterparty
   relieved at the booked rate, difference = FX);
2. an unmapped FX account fails loud instead of dropping the offset line;
3. the unconditional pre-emit balance guard refuses any lopsided entry.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.utils import timezone

from accounting.mappings import ModuleAccountMapping
from accounting.models import Account, ExchangeRate, JournalEntry, JournalLine

pytestmark = pytest.mark.django_db

YEAR = date.today().year
INVOICE_DATE = date(YEAR, 6, 5)
RECEIPT_DATE = date(YEAR, 6, 20)


def _account(company, code, name, account_type):
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code=code,
        name=name,
        account_type=account_type,
        status=Account.Status.ACTIVE,
    )


def _rate(company, frm, to, rate, eff):
    ExchangeRate.objects.create(
        company=company,
        from_currency=frm,
        to_currency=to,
        rate=Decimal(rate),
        effective_date=eff,
    )


@pytest.fixture
def egp_company(company):
    """USD-default company made EGP-functional (the Egypt-merchant case)."""
    company.functional_currency = "EGP"
    company.save(update_fields=["functional_currency"])
    return company


@pytest.fixture
def fx_accounts(egp_company):
    return {
        "bank": _account(egp_company, "1000", "Bank", Account.AccountType.ASSET),
        "ar": _account(egp_company, "1200", "AR Control", Account.AccountType.ASSET),
        "ap": _account(egp_company, "2000", "AP Control", Account.AccountType.LIABILITY),
        "gain": _account(egp_company, "4900", "Realized FX Gain", Account.AccountType.REVENUE),
        "loss": _account(egp_company, "5900", "Realized FX Loss", Account.AccountType.EXPENSE),
    }


def _map_core(company, role, account):
    ModuleAccountMapping.objects.create(company=company, module="core", role=role, account=account)


def _trial_balance(company):
    lines = JournalLine.objects.filter(company=company)
    debit = sum((line.debit for line in lines), Decimal("0"))
    credit = sum((line.credit for line in lines), Decimal("0"))
    return debit, credit


def _posted_invoice_je(company, user, currency, rate):
    return JournalEntry.objects.create(
        public_id=uuid4(),
        company=company,
        date=INVOICE_DATE,
        memo="USD invoice",
        entry_number="JE-INV-0001",
        period=INVOICE_DATE.month,
        status=JournalEntry.Status.POSTED,
        currency=currency,
        exchange_rate=Decimal(rate),
        posted_at=timezone.now(),
        posted_by=user,
        created_by=user,
    )


def _usd_invoice(company, user, customer, total, rate, ar_account):
    from sales.models import PostingProfile, SalesInvoice

    profile = PostingProfile.objects.create(
        company=company,
        code="PP-CUST",
        name="Customer Profile",
        profile_type="CUSTOMER",
        control_account=ar_account,
    )
    je = _posted_invoice_je(company, user, "USD", rate)
    return SalesInvoice.objects.create(
        public_id=uuid4(),
        company=company,
        customer=customer,
        invoice_number="INV-0001",
        invoice_date=INVOICE_DATE,
        due_date=INVOICE_DATE,
        posting_profile=profile,
        currency="USD",
        exchange_rate=Decimal(rate),
        subtotal=Decimal(total),
        total_discount=Decimal("0"),
        total_tax=Decimal("0"),
        total_amount=Decimal(total),
        amount_paid=Decimal("0"),
        status=SalesInvoice.Status.POSTED,
        posted_journal_entry=je,
    )


def _customer(company):
    from accounting.models import Customer

    return Customer.objects.create(
        public_id=uuid4(),
        company=company,
        code="CUST-1",
        name="USD Customer",
        currency="USD",
    )


class TestCustomerReceiptFxBalance:
    def test_foreign_receipt_gain_posts_balanced_and_correct(self, actor_context, egp_company, user, fx_accounts):
        from accounting.commands import record_customer_receipt

        actor = actor_context  # egp_company mutated the shared company object
        _map_core(egp_company, "REALIZED_FX_GAIN", fx_accounts["gain"])
        _map_core(egp_company, "REALIZED_FX_LOSS", fx_accounts["loss"])
        # USD -> EGP: invoice booked @47, receipt settled @48 (a gain)
        _rate(egp_company, "USD", "EGP", "47", INVOICE_DATE)
        _rate(egp_company, "USD", "EGP", "48", RECEIPT_DATE)

        customer = _customer(egp_company)
        invoice = _usd_invoice(egp_company, user, customer, "100", "47", fx_accounts["ar"])

        result = record_customer_receipt(
            actor=actor,
            customer_id=customer.id,
            receipt_date=RECEIPT_DATE.isoformat(),
            amount="100",
            bank_account_id=fx_accounts["bank"].id,
            ar_control_account_id=fx_accounts["ar"].id,
            currency="USD",
            allocations=[{"invoice_public_id": str(invoice.public_id), "amount": "100"}],
        )

        assert result.success, result.error
        debit, credit = _trial_balance(egp_company)
        assert debit == credit, f"trial balance must balance, got debit {debit} != credit {credit}"

        by_acct = {line.account.code: line for line in JournalLine.objects.filter(company=egp_company)}
        # Bank = actual cash received (100 USD @48); AR relieved at booked rate
        # (100 USD @47 = 4700); realized FX gain = 100.
        assert by_acct["1000"].debit == Decimal("4800.00")
        assert by_acct["1200"].credit == Decimal("4700.00")
        assert by_acct["4900"].credit == Decimal("100.00")

    def test_foreign_receipt_loss_posts_balanced(self, actor_context, egp_company, user, fx_accounts):
        from accounting.commands import record_customer_receipt

        actor = actor_context  # egp_company mutated the shared company object
        _map_core(egp_company, "REALIZED_FX_GAIN", fx_accounts["gain"])
        _map_core(egp_company, "REALIZED_FX_LOSS", fx_accounts["loss"])
        # invoice @48, receipt @47 (a loss)
        _rate(egp_company, "USD", "EGP", "48", INVOICE_DATE)
        _rate(egp_company, "USD", "EGP", "47", RECEIPT_DATE)

        customer = _customer(egp_company)
        invoice = _usd_invoice(egp_company, user, customer, "100", "48", fx_accounts["ar"])

        result = record_customer_receipt(
            actor=actor,
            customer_id=customer.id,
            receipt_date=RECEIPT_DATE.isoformat(),
            amount="100",
            bank_account_id=fx_accounts["bank"].id,
            ar_control_account_id=fx_accounts["ar"].id,
            currency="USD",
            allocations=[{"invoice_public_id": str(invoice.public_id), "amount": "100"}],
        )

        assert result.success, result.error
        debit, credit = _trial_balance(egp_company)
        assert debit == credit, f"loss entry must balance, got {debit} != {credit}"
        by_acct = {line.account.code: line for line in JournalLine.objects.filter(company=egp_company)}
        assert by_acct["1000"].debit == Decimal("4700.00")  # cash @47
        assert by_acct["1200"].credit == Decimal("4800.00")  # AR booked @48
        assert by_acct["5900"].debit == Decimal("100.00")  # realized FX loss

    def test_partial_allocation_advance_balances(self, actor_context, egp_company, user, fx_accounts):
        """Receipt 100 USD, only 60 allocated: the 40 advance stays on AR at the
        receipt rate, the 60 relieves at the booked rate, and it balances."""
        from accounting.commands import record_customer_receipt

        actor = actor_context
        _map_core(egp_company, "REALIZED_FX_GAIN", fx_accounts["gain"])
        _map_core(egp_company, "REALIZED_FX_LOSS", fx_accounts["loss"])
        _rate(egp_company, "USD", "EGP", "47", INVOICE_DATE)
        _rate(egp_company, "USD", "EGP", "48", RECEIPT_DATE)

        customer = _customer(egp_company)
        invoice = _usd_invoice(egp_company, user, customer, "100", "47", fx_accounts["ar"])

        result = record_customer_receipt(
            actor=actor,
            customer_id=customer.id,
            receipt_date=RECEIPT_DATE.isoformat(),
            amount="100",
            bank_account_id=fx_accounts["bank"].id,
            ar_control_account_id=fx_accounts["ar"].id,
            currency="USD",
            allocations=[{"invoice_public_id": str(invoice.public_id), "amount": "60"}],
        )
        assert result.success, result.error
        debit, credit = _trial_balance(egp_company)
        assert debit == credit
        by_acct = {line.account.code: line for line in JournalLine.objects.filter(company=egp_company)}
        assert by_acct["1000"].debit == Decimal("4800.00")  # actual cash 100@48
        assert by_acct["1200"].credit == Decimal("4740.00")  # 60@47 + 40@48 advance
        assert by_acct["4900"].credit == Decimal("60.00")  # realized gain on the 60

    def test_role_account_fallback_when_no_core_mapping(self, actor_context, egp_company, user, fx_accounts):
        """No REALIZED_FX_* core mapping, but a FINANCIAL_INCOME role account
        exists — the receipt uses it (revaluation-path fallback) and succeeds."""
        from accounting.commands import record_customer_receipt

        actor = actor_context
        fin_income = _account(egp_company, "4910", "Financial Income", Account.AccountType.REVENUE)
        fin_income.role = Account.AccountRole.FINANCIAL_INCOME
        fin_income.save(update_fields=["role"])
        _rate(egp_company, "USD", "EGP", "47", INVOICE_DATE)
        _rate(egp_company, "USD", "EGP", "48", RECEIPT_DATE)

        customer = _customer(egp_company)
        invoice = _usd_invoice(egp_company, user, customer, "100", "47", fx_accounts["ar"])

        result = record_customer_receipt(
            actor=actor,
            customer_id=customer.id,
            receipt_date=RECEIPT_DATE.isoformat(),
            amount="100",
            bank_account_id=fx_accounts["bank"].id,
            ar_control_account_id=fx_accounts["ar"].id,
            currency="USD",
            allocations=[{"invoice_public_id": str(invoice.public_id), "amount": "100"}],
        )
        assert result.success, result.error
        debit, credit = _trial_balance(egp_company)
        assert debit == credit
        by_acct = {line.account.code: line for line in JournalLine.objects.filter(company=egp_company)}
        assert by_acct["4910"].credit == Decimal("100.00")  # gain booked to the role account

    def test_balance_guard_refuses_lopsided_entry(self, actor_context, egp_company, user, fx_accounts, monkeypatch):
        """The unconditional pre-emit guard refuses any lopsided entry regardless
        of source — proven by skewing a line inside _fix_fx_rounding_dicts."""
        import accounting.commands as cmds
        from accounting.commands import record_customer_receipt

        def _skew(je_lines, company, currency=None):
            je_lines[0]["debit"] = str(Decimal(je_lines[0]["debit"]) + Decimal("999.00"))

        monkeypatch.setattr(cmds, "_fix_fx_rounding_dicts", _skew)

        actor = actor_context
        _rate(egp_company, "USD", "EGP", "48", RECEIPT_DATE)
        customer = _customer(egp_company)  # a foreign (USD) receipt so _fix_fx_rounding_dicts runs

        result = record_customer_receipt(
            actor=actor,
            customer_id=customer.id,
            receipt_date=RECEIPT_DATE.isoformat(),
            amount="100",
            bank_account_id=fx_accounts["bank"].id,
            ar_control_account_id=fx_accounts["ar"].id,
            currency="USD",
        )
        assert not result.success
        assert "does not balance" in result.error
        assert not JournalLine.objects.filter(company=egp_company).exists()

    def test_unmapped_fx_account_fails_loud(self, actor_context, egp_company, user, fx_accounts):
        from accounting.commands import record_customer_receipt

        actor = actor_context  # egp_company mutated the shared company object
        # Deliberately map neither REALIZED_FX_GAIN nor LOSS.
        _rate(egp_company, "USD", "EGP", "47", INVOICE_DATE)
        _rate(egp_company, "USD", "EGP", "48", RECEIPT_DATE)
        customer = _customer(egp_company)
        invoice = _usd_invoice(egp_company, user, customer, "100", "47", fx_accounts["ar"])

        result = record_customer_receipt(
            actor=actor,
            customer_id=customer.id,
            receipt_date=RECEIPT_DATE.isoformat(),
            amount="100",
            bank_account_id=fx_accounts["bank"].id,
            ar_control_account_id=fx_accounts["ar"].id,
            currency="USD",
            allocations=[{"invoice_public_id": str(invoice.public_id), "amount": "100"}],
        )

        assert not result.success
        assert "FX" in result.error and "map" in result.error.lower()
        # Nothing posted.
        assert not JournalLine.objects.filter(company=egp_company).exists()

    def test_functional_currency_receipt_unaffected(self, actor_context, egp_company, user, fx_accounts):
        from accounting.commands import record_customer_receipt

        actor = actor_context  # egp_company mutated the shared company object
        from accounting.models import Customer

        customer = Customer.objects.create(
            public_id=uuid4(), company=egp_company, code="CUST-EGP", name="EGP Customer", currency="EGP"
        )
        result = record_customer_receipt(
            actor=actor,
            customer_id=customer.id,
            receipt_date=RECEIPT_DATE.isoformat(),
            amount="500",
            bank_account_id=fx_accounts["bank"].id,
            ar_control_account_id=fx_accounts["ar"].id,
            currency="EGP",
        )
        assert result.success, result.error
        debit, credit = _trial_balance(egp_company)
        assert debit == credit == Decimal("500.00")


class TestVendorPaymentFxBalance:
    def test_foreign_payment_gain_posts_balanced_and_correct(self, actor_context, egp_company, user, fx_accounts):
        from accounting.commands import record_vendor_payment
        from accounting.models import Vendor

        actor = actor_context  # egp_company mutated the shared company object
        _map_core(egp_company, "REALIZED_FX_GAIN", fx_accounts["gain"])
        _map_core(egp_company, "REALIZED_FX_LOSS", fx_accounts["loss"])
        # bill booked @48, paid @47 → we pay less functional → FX gain
        _rate(egp_company, "USD", "EGP", "48", INVOICE_DATE)
        _rate(egp_company, "USD", "EGP", "47", RECEIPT_DATE)

        vendor = Vendor.objects.create(
            public_id=uuid4(), company=egp_company, code="VEND-1", name="USD Vendor", currency="USD"
        )

        result = record_vendor_payment(
            actor=actor,
            vendor_id=vendor.id,
            payment_date=RECEIPT_DATE.isoformat(),
            amount="100",
            bank_account_id=fx_accounts["bank"].id,
            ap_control_account_id=fx_accounts["ap"].id,
            currency="USD",
            allocations=[{"bill_reference": "BILL-1", "bill_date": INVOICE_DATE.isoformat(), "amount": "100"}],
        )

        assert result.success, result.error
        debit, credit = _trial_balance(egp_company)
        assert debit == credit, f"payment entry must balance, got {debit} != {credit}"
        by_acct = {line.account.code: line for line in JournalLine.objects.filter(company=egp_company)}
        # Bank = actual cash paid (100 USD @47 = 4700); AP relieved at booked
        # rate (100 USD @48 = 4800); realized FX gain = 100.
        assert by_acct["2000"].debit == Decimal("4800.00")
        assert by_acct["1000"].credit == Decimal("4700.00")
        assert by_acct["4900"].credit == Decimal("100.00")

    def test_foreign_payment_loss_posts_balanced(self, actor_context, egp_company, user, fx_accounts):
        from accounting.commands import record_vendor_payment
        from accounting.models import Vendor

        actor = actor_context
        _map_core(egp_company, "REALIZED_FX_GAIN", fx_accounts["gain"])
        _map_core(egp_company, "REALIZED_FX_LOSS", fx_accounts["loss"])
        # bill booked @47, paid @48 → we pay more functional → FX loss
        _rate(egp_company, "USD", "EGP", "47", INVOICE_DATE)
        _rate(egp_company, "USD", "EGP", "48", RECEIPT_DATE)

        vendor = Vendor.objects.create(
            public_id=uuid4(), company=egp_company, code="VEND-2", name="USD Vendor", currency="USD"
        )
        result = record_vendor_payment(
            actor=actor,
            vendor_id=vendor.id,
            payment_date=RECEIPT_DATE.isoformat(),
            amount="100",
            bank_account_id=fx_accounts["bank"].id,
            ap_control_account_id=fx_accounts["ap"].id,
            currency="USD",
            allocations=[{"bill_reference": "BILL-2", "bill_date": INVOICE_DATE.isoformat(), "amount": "100"}],
        )
        assert result.success, result.error
        debit, credit = _trial_balance(egp_company)
        assert debit == credit, f"payment loss must balance, got {debit} != {credit}"
        by_acct = {line.account.code: line for line in JournalLine.objects.filter(company=egp_company)}
        assert by_acct["2000"].debit == Decimal("4700.00")  # AP booked @47
        assert by_acct["1000"].credit == Decimal("4800.00")  # cash paid @48
        assert by_acct["5900"].debit == Decimal("100.00")  # realized FX loss
