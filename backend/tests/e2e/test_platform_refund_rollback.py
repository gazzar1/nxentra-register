# tests/e2e/test_platform_refund_rollback.py
"""A3-PR2 final caller-chain fix, PostgreSQL proof: a canonical-invariant
rejection escapes create_and_post_credit_note_for_platform and rolls back the
COMPLETE platform attempt — the DRAFT credit note, its lines, its events, all
journal rows/events, and every consumed sequence — under real PostgreSQL
savepoint semantics. The DRAFT-recovery path posts a stranded note instead of
reporting fake success."""

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


def _chain(company):
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from sales.commands import create_and_post_invoice_for_platform
    from sales.models import Customer, PostingProfile

    with projection_writes_allowed():
        ar_control = Account.objects.projection().create(
            company=company,
            code="11403",
            name="PG AR Control",
            account_type=Account.AccountType.ASSET,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company,
            code="41003",
            name="PG Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    with command_writes_allowed():
        customer = Customer.objects.create(company=company, code="PGCN-CUST", name="PG Customer")
        profile = PostingProfile.objects.create(
            company=company,
            code="PGCN-PROFILE",
            name="PG Profile",
            profile_type=PostingProfile.ProfileType.CUSTOMER,
            control_account=ar_control,
        )
    result = create_and_post_invoice_for_platform(
        company=company,
        customer_id=customer.id,
        posting_profile_id=profile.id,
        lines=[
            {
                "account_id": revenue.id,
                "description": "PG order",
                "quantity": "1",
                "unit_price": "100.00",
                "discount_amount": "0",
            }
        ],
        invoice_date=date.today(),
        source="shopify",
        source_document_id="PGCN-ORDER-1",
    )
    assert result.success, result.error
    return result.data["invoice"], revenue


def test_invalid_platform_refund_rolls_back_completely_on_postgres(company, owner_membership):
    from sales.commands import create_and_post_credit_note_for_platform
    from sales.models import SalesCreditNote

    invoice, _revenue = _chain(company)
    statistical = _statistical(company, "9540")
    lines = [
        {
            "account_id": statistical.id,
            "description": "Refund line",
            "quantity": "1",
            "unit_price": "30.00",
            "discount_amount": "0",
        }
    ]
    je_rows = JournalEntry.objects.filter(company=company).count()
    events = BusinessEvent.objects.filter(company=company).count()
    seq_je = _seq(company, "journal_entry_number")
    seq_cn = _seq(company, "credit_note_number")

    with pytest.raises(PostedJournalInvalid):
        with transaction.atomic():
            create_and_post_credit_note_for_platform(
                company=company,
                invoice_id=invoice.id,
                lines=lines,
                credit_note_date=date.today(),
                source="shopify",
                source_document_id="PGCN-REFUND-1",
            )

    assert not SalesCreditNote.objects.filter(company=company).exists()
    assert JournalEntry.objects.filter(company=company).count() == je_rows
    assert BusinessEvent.objects.filter(company=company).count() == events
    assert _seq(company, "journal_entry_number") == seq_je
    assert _seq(company, "credit_note_number") == seq_cn


def test_stranded_draft_fails_visibly_on_postgres(company, owner_membership, actor_context):
    """A3-PR2b §9.4: the Counter-owning platform wrapper never auto-posts a
    pre-existing DRAFT (that acquisition was a Counter→document deadlock
    edge) — it fails visibly for manual review and touches nothing."""
    from sales.commands import create_and_post_credit_note_for_platform, create_credit_note
    from sales.models import SalesCreditNote

    invoice, revenue = _chain(company)
    lines = [
        {
            "account_id": revenue.id,
            "description": "Refund line",
            "quantity": "1",
            "unit_price": "30.00",
            "discount_amount": "0",
        }
    ]
    created = create_credit_note(
        actor=actor_context,
        invoice_id=invoice.id,
        lines=lines,
        credit_note_date=date.today(),
        reason="RETURN",
        source="shopify",
        source_document_id="PGCN-REFUND-2",
    )
    assert created.success, created.error
    draft = created.data["credit_note"]
    je_rows = JournalEntry.objects.filter(company=company).count()
    events = BusinessEvent.objects.filter(company=company).count()

    result = create_and_post_credit_note_for_platform(
        company=company,
        invoice_id=invoice.id,
        lines=lines,
        credit_note_date=date.today(),
        source="shopify",
        source_document_id="PGCN-REFUND-2",
    )

    assert not result.success
    assert "inconsistent" in result.error and "DRAFT" in result.error
    notes = SalesCreditNote.objects.filter(company=company, source_document_id="PGCN-REFUND-2")
    assert notes.count() == 1
    untouched = notes.first()
    assert untouched.pk == draft.pk
    assert untouched.status == SalesCreditNote.Status.DRAFT
    assert untouched.posted_journal_entry_id is None
    assert JournalEntry.objects.filter(company=company).count() == je_rows
    assert BusinessEvent.objects.filter(company=company).count() == events
