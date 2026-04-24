from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import models

from accounting.models import Account, JournalEntry, JournalLine
from events.emitter import emit_event
from events.types import EventTypes
from projections.base import projection_registry
from properties.event_types import RentDuePostedData
from properties.models import PropertyAccountMapping


@pytest.mark.django_db
def test_property_projection_registered_and_rebuild_stable(actor_context, company):
    projection = projection_registry.get("property_accounting")
    assert projection is not None, "property_accounting projection must be registered at startup"

    ar_account = Account.objects.create(
        company=company,
        public_id=uuid4(),
        code="1100",
        name="Accounts Receivable",
        account_type=Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        status=Account.Status.ACTIVE,
    )
    rent_income_account = Account.objects.create(
        company=company,
        public_id=uuid4(),
        code="4100",
        name="Rental Income",
        account_type=Account.AccountType.REVENUE,
        normal_balance=Account.NormalBalance.CREDIT,
        status=Account.Status.ACTIVE,
    )

    PropertyAccountMapping.objects.create(
        company=company,
        rental_income_account=rent_income_account,
        accounts_receivable_account=ar_account,
    )

    emit_event(
        actor=actor_context,
        event_type=EventTypes.RENT_DUE_POSTED,
        aggregate_type="RentScheduleLine",
        aggregate_id=str(uuid4()),
        idempotency_key=f"test.rent_due_posted:{uuid4()}",
        data=RentDuePostedData(
            schedule_line_public_id=str(uuid4()),
            lease_public_id=str(uuid4()),
            contract_no="LEASE-001",
            installment_no=1,
            due_date="2026-03-01",
            total_due="1000.00",
            currency="USD",
        ).to_dict(),
    )

    processed = projection.process_pending(company)
    assert processed >= 1

    entries = JournalEntry.objects.filter(company=company, memo="Rent due: LEASE-001 #1")
    assert entries.count() == 1
    entry = entries.first()
    assert entry is not None
    assert entry.status == JournalEntry.Status.POSTED

    lines = list(JournalLine.objects.filter(company=company, entry=entry).order_by("line_no"))
    assert len(lines) == 2
    assert lines[0].account_id == ar_account.id
    assert lines[0].debit == Decimal("1000.00")
    assert lines[0].credit == Decimal("0")
    assert lines[1].account_id == rent_income_account.id
    assert lines[1].debit == Decimal("0")
    assert lines[1].credit == Decimal("1000.00")

    before_count = JournalEntry.objects.filter(company=company).count()
    before_debits = JournalLine.objects.filter(company=company, entry__status=JournalEntry.Status.POSTED).aggregate(
        total=models.Sum("debit")
    ).get("total") or Decimal("0")
    before_credits = JournalLine.objects.filter(company=company, entry__status=JournalEntry.Status.POSTED).aggregate(
        total=models.Sum("credit")
    ).get("total") or Decimal("0")

    rebuilt = projection.rebuild(company)
    assert rebuilt >= 1

    after_count = JournalEntry.objects.filter(company=company).count()
    after_debits = JournalLine.objects.filter(company=company, entry__status=JournalEntry.Status.POSTED).aggregate(
        total=models.Sum("debit")
    ).get("total") or Decimal("0")
    after_credits = JournalLine.objects.filter(company=company, entry__status=JournalEntry.Status.POSTED).aggregate(
        total=models.Sum("credit")
    ).get("total") or Decimal("0")

    assert after_count == before_count
    assert after_debits == before_debits
    assert after_credits == before_credits
