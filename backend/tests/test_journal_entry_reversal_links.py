# tests/test_journal_entry_reversal_links.py
"""
Journal-entry reversal cross-links + memo.

The reversal memo used to embed the internal pk ("Reversal of JE#1677"), which is
meaningless to the user, and the serializer never exposed the original↔reversal
link by entry NUMBER. These tests pin:
  * the reversal memo references the user-facing entry number (JE-000001), and
  * JournalEntrySerializer exposes the cross-links both ways
    (reverses_entry_number on the reversal; reversed_by_entry[_number] on the
    original), so the UI can link the two entries.
"""

import calendar
from datetime import date
from decimal import Decimal

import pytest

from accounting.commands import (
    create_journal_entry,
    post_journal_entry,
    reverse_journal_entry,
    save_journal_entry_complete,
)
from accounting.models import Account
from accounting.serializers import JournalEntrySerializer
from accounts.authz import system_actor_for_company
from projections.models import FiscalPeriod
from projections.write_barrier import projection_writes_allowed


@pytest.fixture
def posted_entry(company, owner_membership):
    """A balanced, POSTED journal entry (+ its actor) ready to reverse."""
    actor = system_actor_for_company(company)
    today = date.today()
    last_day = calendar.monthrange(today.year, today.month)[1]
    with projection_writes_allowed():
        FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=today.year,
            period=today.month,
            defaults=dict(
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=today.replace(day=1),
                end_date=today.replace(day=last_day),
                status=FiscalPeriod.Status.OPEN,
            ),
        )
        bank = Account.objects.projection().create(
            company=company,
            code="1000",
            name="Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company,
            code="4000",
            name="Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )

    created = create_journal_entry(
        actor=actor,
        date=today,
        memo="Original entry",
        lines=[
            {"account_id": bank.id, "description": "DR", "debit": Decimal("100"), "credit": Decimal("0")},
            {"account_id": revenue.id, "description": "CR", "debit": Decimal("0"), "credit": Decimal("100")},
        ],
    )
    assert created.success, created.error
    assert save_journal_entry_complete(actor, created.data.id).success
    posted = post_journal_entry(actor, created.data.id)
    assert posted.success, posted.error
    return actor, posted.data


@pytest.mark.django_db
def test_reversal_memo_uses_entry_number_not_pk(posted_entry):
    actor, original = posted_entry
    result = reverse_journal_entry(actor, original.id)
    assert result.success, result.error
    reversal = result.data["reversal"]

    # Memo references the user-facing number (JE-000001…), never "JE#<pk>".
    assert reversal.memo.startswith(f"Reversal of {original.entry_number}:")
    assert f"JE#{original.id}" not in reversal.memo


@pytest.mark.django_db
def test_serializer_exposes_reversal_cross_links_both_ways(posted_entry):
    actor, original = posted_entry
    result = reverse_journal_entry(actor, original.id)
    assert result.success, result.error
    reversal = result.data["reversal"]

    original.refresh_from_db()
    reversal.refresh_from_db()

    # The reversal points back to the original by number; it wasn't itself reversed.
    rev_data = JournalEntrySerializer(reversal).data
    assert rev_data["reverses_entry"] == original.id
    assert rev_data["reverses_entry_number"] == original.entry_number
    assert rev_data["reversed_by_entry"] is None
    assert rev_data["reversed_by_entry_number"] is None

    # The original is REVERSED and links forward to the reversal by number.
    orig_data = JournalEntrySerializer(original).data
    assert orig_data["status"] == "REVERSED"
    assert orig_data["reversed_by_entry"] == reversal.id
    assert orig_data["reversed_by_entry_number"] == reversal.entry_number
    assert orig_data["reverses_entry"] is None
    assert orig_data["reverses_entry_number"] is None
