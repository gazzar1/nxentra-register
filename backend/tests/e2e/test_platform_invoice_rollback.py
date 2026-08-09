# tests/e2e/test_platform_invoice_rollback.py
"""A3-PR2 invoice caller-chain fix, PostgreSQL proof: a canonical-invariant
rejection escapes create_and_post_invoice_for_platform and rolls back the
COMPLETE platform attempt — the staged SalesInvoice, its lines, its created
events, all journal rows/events, and every consumed sequence — under real
PostgreSQL savepoint semantics, leaving the source_document_id unreserved.
The DRAFT-recovery branch posts a stranded invoice instead of reporting fake
success."""

from datetime import date
from uuid import uuid4

import pytest
from django.db import transaction

from accounting.journal_invariant import PostedJournalInvalid
from accounting.models import Account, CompanySequence, JournalEntry
from events.models import BusinessEvent

pytestmark = [pytest.mark.django_db]


def _seq(company, name):
    row = CompanySequence.objects.filter(company=company, name=name).first()
    return row.next_value if row else None


def _statistical(company, code):
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code=code,
        name="Statistical",
        account_type=Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        ledger_domain=Account.LedgerDomain.STATISTICAL,
        unit_of_measure="EA",
        status=Account.Status.ACTIVE,
    )


def _scaffold(company):
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from sales.models import Customer, PostingProfile

    with projection_writes_allowed():
        ar_control = Account.objects.projection().create(
            company=company,
            code="11405",
            name="PG Invoice AR Control",
            account_type=Account.AccountType.ASSET,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company,
            code="41005",
            name="PG Invoice Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    with command_writes_allowed():
        customer = Customer.objects.create(company=company, code="PGIV-CUST", name="PG Invoice Customer")
        profile = PostingProfile.objects.create(
            company=company,
            code="PGIV-PROFILE",
            name="PG Invoice Profile",
            profile_type=PostingProfile.ProfileType.CUSTOMER,
            control_account=ar_control,
        )
    return customer, profile, revenue


def _lines(account_id):
    return [
        {
            "account_id": account_id,
            "description": "PG order line",
            "quantity": "1",
            "unit_price": "100.00",
            "discount_amount": "0",
        }
    ]


def test_invalid_platform_invoice_rolls_back_completely_on_postgres(company, owner_membership):
    from sales.commands import create_and_post_invoice_for_platform
    from sales.models import SalesInvoice

    customer, profile, _revenue = _scaffold(company)
    statistical = _statistical(company, "9560")
    je_rows = JournalEntry.objects.filter(company=company).count()
    events = BusinessEvent.objects.filter(company=company).count()
    seq_je = _seq(company, "journal_entry_number")
    seq_inv = _seq(company, "sales_invoice_number")

    with pytest.raises(PostedJournalInvalid):
        with transaction.atomic():
            create_and_post_invoice_for_platform(
                company=company,
                customer_id=customer.id,
                posting_profile_id=profile.id,
                lines=_lines(statistical.id),
                invoice_date=date.today(),
                source="shopify",
                source_document_id="PGIV-ORDER-1",
            )

    assert not SalesInvoice.objects.filter(company=company).exists()
    assert JournalEntry.objects.filter(company=company).count() == je_rows
    assert BusinessEvent.objects.filter(company=company).count() == events
    assert _seq(company, "journal_entry_number") == seq_je
    assert _seq(company, "sales_invoice_number") == seq_inv


def test_stranded_draft_invoice_fails_visibly_on_postgres(company, owner_membership, actor_context):
    """A3-PR2b §9.4: the Counter-owning platform wrapper never auto-posts a
    pre-existing DRAFT invoice — visible manual-review failure, nothing
    touched, nothing emitted."""
    from sales.commands import create_and_post_invoice_for_platform, create_sales_invoice
    from sales.models import SalesInvoice

    customer, profile, revenue = _scaffold(company)
    created = create_sales_invoice(
        actor=actor_context,
        customer_id=customer.id,
        posting_profile_id=profile.id,
        lines=_lines(revenue.id),
        invoice_date=date.today(),
        source="shopify",
        source_document_id="PGIV-ORDER-2",
    )
    assert created.success, created.error
    draft = created.data["invoice"]
    je_rows = JournalEntry.objects.filter(company=company).count()
    events = BusinessEvent.objects.filter(company=company).count()

    result = create_and_post_invoice_for_platform(
        company=company,
        customer_id=customer.id,
        posting_profile_id=profile.id,
        lines=_lines(revenue.id),
        invoice_date=date.today(),
        source="shopify",
        source_document_id="PGIV-ORDER-2",
    )

    assert not result.success
    assert "inconsistent" in result.error and "DRAFT" in result.error
    rows = SalesInvoice.objects.filter(company=company, source_document_id="PGIV-ORDER-2")
    assert rows.count() == 1
    untouched = rows.first()
    assert untouched.pk == draft.pk
    assert untouched.status == SalesInvoice.Status.DRAFT
    assert untouched.posted_journal_entry_id is None
    assert JournalEntry.objects.filter(company=company).count() == je_rows
    assert BusinessEvent.objects.filter(company=company).count() == events
