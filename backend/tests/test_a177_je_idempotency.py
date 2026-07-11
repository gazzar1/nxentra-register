# tests/test_a177_je_idempotency.py
"""
A177 — JE command idempotency must use a caller request identity
(2026-07-11 dual audit).

Before the fix, create_journal_entry minted a fresh aggregate UUID but
derived its idempotency key from a CONTENT SUBSET (date/memo/kind/
currency/rate/lines) that omitted period, source document/module,
dimensions, and counterparties:

- A true retry (or a byte-identical legitimate second entry) returned the
  OLD event, then looked up the fresh UUID and reported
  "Journal entry could not be created. Projection may have failed." —
  a false failure blaming projections.
- Distinct legitimate entries differing only in the omitted fields
  collided and could never be created (the FX revaluation task even
  carries a uuid4-nonce-in-memo workaround for this).

After the fix:
- No request_id: the key is aggregate-scoped — every invocation is a new
  aggregate; nothing collides.
- request_id provided: a true retry returns the ORIGINAL entry; reusing
  the same request_id with a different payload is rejected loudly.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from accounting.commands import create_journal_entry
from accounting.models import Account, Customer, JournalEntry
from events.models import BusinessEvent
from events.types import EventTypes
from projections.write_barrier import projection_writes_allowed

pytestmark = pytest.mark.django_db


def _lines(cash, revenue, amount="100.00"):
    return [
        {"account_id": cash.id, "description": "cash", "debit": Decimal(amount), "credit": Decimal("0")},
        {"account_id": revenue.id, "description": "rev", "debit": Decimal("0"), "credit": Decimal(amount)},
    ]


def _created_events(company):
    return BusinessEvent.objects.filter(company=company, event_type=EventTypes.JOURNAL_ENTRY_CREATED)


class TestLegacyPathNoCollisions:
    def test_identical_recall_creates_a_second_entry(self, actor_context, company, cash_account, revenue_account):
        """Byte-identical legitimate entries (two same-day cash sales) must
        both be creatable. Before the fix the second call false-failed with
        'Projection may have failed.'"""
        r1 = create_journal_entry(
            actor_context, date=date.today(), memo="daily cash", lines=_lines(cash_account, revenue_account)
        )
        assert r1.success, r1.error

        r2 = create_journal_entry(
            actor_context, date=date.today(), memo="daily cash", lines=_lines(cash_account, revenue_account)
        )
        assert r2.success, f"identical legitimate entry must not collide: {r2.error}"
        assert r2.data.public_id != r1.data.public_id
        assert JournalEntry.objects.filter(company=company).count() == 2
        assert _created_events(company).count() == 2

    def test_different_source_document_never_collides(self, actor_context, company, cash_account, revenue_account):
        r1 = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="settlement",
            lines=_lines(cash_account, revenue_account),
            source_module="payment_settlement",
            source_document="stripe:po_1",
        )
        assert r1.success, r1.error
        r2 = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="settlement",
            lines=_lines(cash_account, revenue_account),
            source_module="payment_settlement",
            source_document="stripe:po_2",
        )
        assert r2.success, f"entries differing only in source_document must not collide: {r2.error}"
        assert _created_events(company).count() == 2

    def test_different_counterparty_never_collides(self, actor_context, company, revenue_account):
        ar = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="1200",
            name="AR",
            account_type=Account.AccountType.ASSET,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            requires_counterparty=True,
            counterparty_kind="CUSTOMER",
            status=Account.Status.ACTIVE,
        )
        with projection_writes_allowed():
            c1 = Customer.objects.create(public_id=uuid4(), company=company, code="C1", name="C1")
            c2 = Customer.objects.create(public_id=uuid4(), company=company, code="C2", name="C2")

        def _ar_lines(customer):
            return [
                {
                    "account_id": ar.id,
                    "description": "ar",
                    "debit": Decimal("50.00"),
                    "credit": Decimal("0"),
                    "customer_public_id": str(customer.public_id),
                },
                {
                    "account_id": revenue_account.id,
                    "description": "rev",
                    "debit": Decimal("0"),
                    "credit": Decimal("50.00"),
                },
            ]

        r1 = create_journal_entry(actor_context, date=date.today(), memo="inv", lines=_ar_lines(c1))
        assert r1.success, r1.error
        r2 = create_journal_entry(actor_context, date=date.today(), memo="inv", lines=_ar_lines(c2))
        assert r2.success, f"entries differing only in counterparty must not collide: {r2.error}"
        assert _created_events(company).count() == 2

    def test_different_period_never_collides(self, actor_context, company, cash_account, revenue_account):
        r1 = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="periodic",
            lines=_lines(cash_account, revenue_account),
            period=1,
        )
        assert r1.success, r1.error
        r2 = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="periodic",
            lines=_lines(cash_account, revenue_account),
            period=2,
        )
        assert r2.success, f"entries differing only in period must not collide: {r2.error}"
        assert _created_events(company).count() == 2


class TestRequestIdRetry:
    def test_true_retry_returns_the_original_entry(self, actor_context, company, cash_account, revenue_account):
        r1 = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="retryable",
            lines=_lines(cash_account, revenue_account),
            request_id="req-A",
        )
        assert r1.success, r1.error

        r2 = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="retryable",
            lines=_lines(cash_account, revenue_account),
            request_id="req-A",
        )
        assert r2.success, f"a true retry must succeed and return the original: {r2.error}"
        assert r2.data.public_id == r1.data.public_id, "retry must return the ORIGINAL aggregate"
        assert JournalEntry.objects.filter(company=company).count() == 1
        assert _created_events(company).count() == 1
        assert r2.event.id == r1.event.id

    def test_same_key_different_payload_is_rejected(self, actor_context, company, cash_account, revenue_account):
        r1 = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="original",
            lines=_lines(cash_account, revenue_account, amount="100.00"),
            request_id="req-B",
        )
        assert r1.success, r1.error

        r2 = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="original",
            lines=_lines(cash_account, revenue_account, amount="999.00"),
            request_id="req-B",
        )
        assert not r2.success, "same request_id with a different payload must be refused"
        assert "conflict" in r2.error.lower()
        assert "projection" not in r2.error.lower(), "the error must name the real cause, not blame projections"
        assert JournalEntry.objects.filter(company=company).count() == 1
        assert _created_events(company).count() == 1

        original = JournalEntry.objects.get(company=company)
        assert original.public_id == r1.data.public_id, "the original entry must be unmodified"

    def test_request_id_key_shape(self, actor_context, company, cash_account, revenue_account):
        create_journal_entry(
            actor_context,
            date=date.today(),
            memo="keyed",
            lines=_lines(cash_account, revenue_account),
            request_id="req-C",
        )
        event = _created_events(company).get()
        assert event.idempotency_key.startswith("journal_entry.created:req:")
        assert event.metadata.get("content_hash", "").startswith("journal_entry.content:")
