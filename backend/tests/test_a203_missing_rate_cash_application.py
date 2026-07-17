"""
A203 — cash application must never book a foreign amount at a guessed 1:1 rate.

``record_customer_receipt`` / ``record_vendor_payment`` resolved a missing
exchange rate as ``looked_up if looked_up else Decimal("1")`` and posted the
foreign amount unconverted (EGP 500 booked for a USD 500 receipt). This is the
silent-1:1 class the FX JE-integrity sweep (PRs #33-#36) eliminated from
``post_journal_entry`` — which refuses loudly on the same condition — but these
two paths emit ``JOURNAL_ENTRY_POSTED`` directly and never hit that refusal.

Operator-safety Rule 2: stop loudly, never guess.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from accounting.models import Account, ExchangeRate, JournalLine

pytestmark = pytest.mark.django_db

RECEIPT_DATE = date(date.today().year, 6, 20)


def _account(company, code, name, account_type):
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code=code,
        name=name,
        account_type=account_type,
        status=Account.Status.ACTIVE,
    )


@pytest.fixture
def egp_company(company):
    company.functional_currency = "EGP"
    company.save(update_fields=["functional_currency"])
    return company


@pytest.fixture
def accounts(egp_company):
    return {
        "bank": _account(egp_company, "1000", "Bank", Account.AccountType.ASSET),
        "ar": _account(egp_company, "1200", "AR Control", Account.AccountType.ASSET),
        "ap": _account(egp_company, "2000", "AP Control", Account.AccountType.LIABILITY),
    }


@pytest.fixture(autouse=True)
def _no_rate_autofetch(monkeypatch):
    """get_rate's last resort hits a live FX API; a missing rate must stay
    missing in tests."""
    monkeypatch.setattr(ExchangeRate, "_auto_fetch_rate", classmethod(lambda cls, *a, **k: None))


@pytest.fixture
def usd_customer(egp_company):
    from accounting.models import Customer

    return Customer.objects.create(
        public_id=uuid4(), company=egp_company, code="CUST-1", name="USD Customer", currency="USD"
    )


@pytest.fixture
def usd_vendor(egp_company):
    from accounting.models import Vendor

    return Vendor.objects.create(
        public_id=uuid4(), company=egp_company, code="VEND-1", name="USD Vendor", currency="USD"
    )


class TestReceiptMissingRate:
    def test_missing_rate_refuses_loudly(self, actor_context, egp_company, accounts, usd_customer):
        from accounting.commands import record_customer_receipt

        result = record_customer_receipt(
            actor=actor_context,
            customer_id=usd_customer.id,
            receipt_date=RECEIPT_DATE.isoformat(),
            amount="500",
            bank_account_id=accounts["bank"].id,
            ar_control_account_id=accounts["ar"].id,
            currency="USD",
        )
        assert not result.success
        assert "Missing USD" in result.error and "exchange rate" in result.error
        assert not JournalLine.objects.filter(company=egp_company).exists()

    def test_explicit_rate_still_posts_converted(self, actor_context, egp_company, accounts, usd_customer):
        """No rate on file, but the caller supplies one — posts at that rate."""
        from accounting.commands import record_customer_receipt

        result = record_customer_receipt(
            actor=actor_context,
            customer_id=usd_customer.id,
            receipt_date=RECEIPT_DATE.isoformat(),
            amount="500",
            bank_account_id=accounts["bank"].id,
            ar_control_account_id=accounts["ar"].id,
            currency="USD",
            exchange_rate="48",
        )
        assert result.success, result.error
        bank_line = JournalLine.objects.get(company=egp_company, account=accounts["bank"])
        assert bank_line.debit == Decimal("24000.00")  # 500 USD @48, not 500

    @pytest.mark.parametrize("bad_rate", ["0", "-5", "abc", "NaN", "Infinity"])
    def test_invalid_explicit_rate_refused(self, actor_context, egp_company, accounts, usd_customer, bad_rate):
        from accounting.commands import record_customer_receipt

        result = record_customer_receipt(
            actor=actor_context,
            customer_id=usd_customer.id,
            receipt_date=RECEIPT_DATE.isoformat(),
            amount="500",
            bank_account_id=accounts["bank"].id,
            ar_control_account_id=accounts["ar"].id,
            currency="USD",
            exchange_rate=bad_rate,
        )
        assert not result.success
        # The NEW validation must be what refuses (not a downstream failure).
        assert "exchange rate" in result.error.lower()
        assert not JournalLine.objects.filter(company=egp_company).exists()

    def test_functional_receipt_needs_no_rate(self, actor_context, egp_company, accounts):
        from accounting.commands import record_customer_receipt
        from accounting.models import Customer

        customer = Customer.objects.create(
            public_id=uuid4(), company=egp_company, code="CUST-EGP", name="EGP Customer", currency="EGP"
        )
        result = record_customer_receipt(
            actor=actor_context,
            customer_id=customer.id,
            receipt_date=RECEIPT_DATE.isoformat(),
            amount="500",
            bank_account_id=accounts["bank"].id,
            ar_control_account_id=accounts["ar"].id,
        )
        assert result.success, result.error
        bank_line = JournalLine.objects.get(company=egp_company, account=accounts["bank"])
        assert bank_line.debit == Decimal("500.00")


class TestPaymentMissingRate:
    def test_missing_rate_refuses_loudly(self, actor_context, egp_company, accounts, usd_vendor):
        from accounting.commands import record_vendor_payment

        result = record_vendor_payment(
            actor=actor_context,
            vendor_id=usd_vendor.id,
            payment_date=RECEIPT_DATE.isoformat(),
            amount="500",
            bank_account_id=accounts["bank"].id,
            ap_control_account_id=accounts["ap"].id,
            currency="USD",
        )
        assert not result.success
        assert "Missing USD" in result.error and "exchange rate" in result.error
        assert not JournalLine.objects.filter(company=egp_company).exists()

    def test_explicit_rate_still_posts_converted(self, actor_context, egp_company, accounts, usd_vendor):
        from accounting.commands import record_vendor_payment

        result = record_vendor_payment(
            actor=actor_context,
            vendor_id=usd_vendor.id,
            payment_date=RECEIPT_DATE.isoformat(),
            amount="500",
            bank_account_id=accounts["bank"].id,
            ap_control_account_id=accounts["ap"].id,
            currency="USD",
            exchange_rate="48",
        )
        assert result.success, result.error
        bank_line = JournalLine.objects.get(company=egp_company, account=accounts["bank"])
        assert bank_line.credit == Decimal("24000.00")

    @pytest.mark.parametrize("bad_rate", ["0", "-5", "abc", "NaN", "Infinity"])
    def test_invalid_explicit_rate_refused(self, actor_context, egp_company, accounts, usd_vendor, bad_rate):
        from accounting.commands import record_vendor_payment

        result = record_vendor_payment(
            actor=actor_context,
            vendor_id=usd_vendor.id,
            payment_date=RECEIPT_DATE.isoformat(),
            amount="500",
            bank_account_id=accounts["bank"].id,
            ap_control_account_id=accounts["ap"].id,
            currency="USD",
            exchange_rate=bad_rate,
        )
        assert not result.success
        assert "exchange rate" in result.error.lower()
        assert not JournalLine.objects.filter(company=egp_company).exists()
