# tests/test_a142_settlement_je_header_rate.py
"""A142 — the settlement JE header must carry the FX rate that converted its
lines.

Live evidence (JE-000070, 2026-07-03): the first real Stripe payout posted a
settlement drain whose lines converted USD→EGP @48 (convert-or-quarantine,
PR #34) while the header displayed "1 USD = 1.000000 EGP" — post_journal_entry
looked the rate up PER LINE but stamped the stale aggregate default on the
JOURNAL_ENTRY_POSTED payload, which is what the read model materializes.

Also pinned: the settlement memo only carries a provenance suffix when a CSV
source_filename exists — API-pulled settlements are not "(manual)".
"""

import calendar
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from accounts.models import Company, CompanyMembership
from projections.write_barrier import projection_writes_allowed

USD_EGP_RATE = Decimal("48")


@pytest.fixture
def egp_books_company(db):
    """USD presentation / EGP functional books (the live Shopify_R shape),
    with a USD→EGP 48 SPOT rate on file and an open fiscal period."""
    from accounting.models import ExchangeRate
    from projections.models import FiscalPeriod

    User = get_user_model()
    uid = uuid4().hex[:8]
    company = Company.objects.create(
        public_id=uuid4(),
        name=f"A142 Co {uid}",
        slug=f"a142-{uid}",
        default_currency="USD",
        functional_currency="EGP",
        is_active=True,
    )
    user = User.objects.create_user(
        public_id=uuid4(),
        email=f"owner-a142-{uid}@test.com",
        password="Testpass123!",
        name="A142 Owner",
    )
    user.active_company = company
    user.save()
    CompanyMembership.objects.create(
        public_id=uuid4(),
        company=company,
        user=user,
        role=CompanyMembership.Role.OWNER,
        is_active=True,
    )

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

    ExchangeRate.objects.create(
        company=company,
        from_currency="USD",
        to_currency="EGP",
        rate=USD_EGP_RATE,
        effective_date=today.replace(day=1),
        rate_type="SPOT",
    )
    return company


def _emit_settlement(company, batch_id: str, currency: str, source_filename: str = ""):
    from accounting.payment_settlement_projection import PaymentSettlementProjection
    from events.emitter import emit_event_no_actor
    from events.types import EventTypes, PaymentSettlementReceivedData

    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
        aggregate_type="PaymentSettlement",
        aggregate_id=f"stripe:{batch_id}",
        idempotency_key=f"payment.settlement.received:stripe:{batch_id}",
        data=PaymentSettlementReceivedData(
            amount="103.20",
            currency=currency,
            transaction_date=date.today().isoformat(),
            document_ref=batch_id,
            provider_normalized_code="stripe",
            external_system="stripe",
            payout_batch_id=batch_id,
            gross_amount="103.20",
            fees="6.40",
            net_amount="96.80",
            uncollected_amount="0",
            payment_method="card",
            payout_date=date.today().isoformat(),
            source_filename=source_filename,
            line_items=[
                {"order_id": "ch_a", "gross": "51.60", "fee": "3.20", "net": "48.40", "status": "charge"},
                {"order_id": "ch_b", "gross": "51.60", "fee": "3.20", "net": "48.40", "status": "charge"},
            ],
            provider_status="paid",
        ),
    )
    PaymentSettlementProjection().process_pending(company)


def _get_settlement_je(company, batch_id: str):
    from accounting.models import JournalEntry

    return JournalEntry.objects.get(
        company=company, source_module="payment_settlement", source_document=f"stripe:{batch_id}"
    )


@pytest.mark.django_db
def test_foreign_settlement_stamps_header_rate(egp_books_company):
    """THE regression: a USD settlement on EGP books must show the rate that
    converted its lines — not the 1.0 default (JE-000070)."""
    from stripe_connector.seed import setup_stripe_platform

    company = egp_books_company
    setup_stripe_platform(company)
    _emit_settlement(company, "po_a142", currency="USD")

    je = _get_settlement_je(company, "po_a142")
    assert je.status == je.Status.POSTED
    assert je.currency == "USD"
    assert Decimal(str(je.exchange_rate)) == USD_EGP_RATE  # pre-fix: 1.0

    # Lines really converted at that rate (the part that was already right).
    ebd_line = je.lines.get(debit__gt=0, account__code="11610")
    assert ebd_line.debit == (Decimal("96.80") * USD_EGP_RATE).quantize(Decimal("0.01"))

    # API-pulled settlement: no "(manual)" suffix.
    assert je.memo == "Settlement: Stripe batch po_a142"


@pytest.mark.django_db
def test_functional_currency_settlement_keeps_rate_1(egp_books_company):
    """An EGP settlement on EGP books needs no conversion — header stays 1.0."""
    from stripe_connector.seed import setup_stripe_platform

    company = egp_books_company
    setup_stripe_platform(company)
    _emit_settlement(company, "po_egp", currency="EGP")

    je = _get_settlement_je(company, "po_egp")
    assert je.status == je.Status.POSTED
    assert Decimal(str(je.exchange_rate)) == Decimal("1.0")
    ebd_line = je.lines.get(debit__gt=0, account__code="11610")
    assert ebd_line.debit == Decimal("96.80")


@pytest.mark.django_db
def test_csv_settlement_memo_keeps_filename_provenance(egp_books_company):
    """CSV imports keep their filename suffix — only the false 'manual' label
    for API pulls was dropped."""
    from stripe_connector.seed import setup_stripe_platform

    company = egp_books_company
    setup_stripe_platform(company)
    _emit_settlement(company, "po_csv", currency="USD", source_filename="paymob_jul.csv")

    je = _get_settlement_je(company, "po_csv")
    assert je.memo == "Settlement: Stripe batch po_csv (paymob_jul.csv)"
