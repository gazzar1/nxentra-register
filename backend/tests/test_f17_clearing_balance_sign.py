"""
F17 — Shopify clearing balance sign.

The clearing account is a current ASSET (debit-normal): money sold into it and
awaiting settlement. A +650 DR balance must read POSITIVE. The old
`credit - debit` inverted it, so System Health / Month-End Close showed
"-650.00" for a +650 DR. compute_clearing_balance is now debit-normal.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.utils import timezone

from accounting.mappings import ModuleAccountMapping
from accounting.models import Account, JournalEntry, JournalLine
from projections.write_barrier import projection_writes_allowed
from shopify_connector.management.commands.check_clearing_balance import compute_clearing_balance


def _acct(company, code, name, atype):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code=code,
            name=name,
            account_type=atype,
            status=Account.Status.ACTIVE,
        )


def _post(company, user, lines):
    with projection_writes_allowed():
        entry = JournalEntry.objects.projection().create(
            public_id=uuid4(),
            company=company,
            date=date.today(),
            period=date.today().month,
            memo="F17 test",
            status=JournalEntry.Status.POSTED,
            posted_at=timezone.now(),
            posted_by=user,
            created_by=user,
            entry_number=f"JE-{uuid4().hex[:6]}",
        )
        for i, (account, debit, credit) in enumerate(lines, start=1):
            JournalLine.objects.projection().create(
                entry=entry,
                company=company,
                line_no=i,
                account=account,
                description="l",
                debit=Decimal(debit),
                credit=Decimal(credit),
            )


@pytest.mark.django_db
def test_positive_dr_clearing_reads_positive(company, user):
    clearing = _acct(company, "11500", "Shopify Clearing", Account.AccountType.ASSET)
    revenue = _acct(company, "41000", "Revenue", Account.AccountType.REVENUE)
    ModuleAccountMapping.objects.create(
        company=company, module="shopify_connector", role="SHOPIFY_CLEARING", account=clearing
    )
    # Sale into clearing: DR clearing 650 / CR revenue 650.
    _post(company, user, [(clearing, "650.00", "0"), (revenue, "0", "650.00")])

    data = compute_clearing_balance(company)
    assert Decimal(data["balance"]) == Decimal("650.00")  # F17: was -650.00
    assert data["is_zero"] is False


@pytest.mark.django_db
def test_settled_clearing_reads_zero(company, user):
    clearing = _acct(company, "11500", "Shopify Clearing", Account.AccountType.ASSET)
    revenue = _acct(company, "41000", "Revenue", Account.AccountType.REVENUE)
    bank = _acct(company, "10100", "Bank", Account.AccountType.ASSET)
    ModuleAccountMapping.objects.create(
        company=company, module="shopify_connector", role="SHOPIFY_CLEARING", account=clearing
    )
    _post(company, user, [(clearing, "650.00", "0"), (revenue, "0", "650.00")])  # sold
    _post(company, user, [(bank, "650.00", "0"), (clearing, "0", "650.00")])  # settled

    data = compute_clearing_balance(company)
    assert Decimal(data["balance"]) == Decimal("0")
    assert data["is_zero"] is True
