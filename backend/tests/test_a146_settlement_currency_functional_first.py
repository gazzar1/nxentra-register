# tests/test_a146_settlement_currency_functional_first.py
"""A146 — settlement currency guesses are functional-first, never default-first.

Settlement CSVs carry no currency column, so the importer stamps a GUESS into
the immutable PAYMENT_SETTLEMENT_RECEIVED event. It guessed
``company.default_currency`` — but the books truth is the FUNCTIONAL currency:
post_journal_entry converts every foreign line into functional currency at a
real rate or quarantines (FX sweep #34). On the live default=USD/functional=EGP
shape (Shopify_R), an EGP Paymob batch was stamped "USD", which post-#34 means
converting EGP magnitudes at the USD→EGP rate — a 48× books error, not a label.

Three sites move to functional-first (matching create_journal_entry/je_builder
since the 2026-06-04 FX sweep):
- accounting/settlement_imports.py     — the emitter (the behavior change)
- accounting/payment_settlement_projection.py — JE-consumer fallback (hardening;
  both production emitters always set currency)
- platform_connectors/projections.py   — read-model fallback (hardening; the
  "5,848.88 USD" Paymob header on the Stage-2 ledger was this guess surfacing)

The old PAYMOB-BATCH-DEMO-001 event keeps its baked "USD" — immutable event,
accepted demo artifact; these tests pin that the class is closed going forward.
"""

import calendar
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from accounting.models import ExchangeRate, JournalEntry
from accounting.payment_settlement_projection import PaymentSettlementProjection
from accounting.settlement_imports import import_settlement_csv
from accounts.models import Company, CompanyMembership
from events.models import BusinessEvent
from events.types import EventTypes, PaymentSettlementReceivedData
from projections.write_barrier import projection_writes_allowed

USD_EGP_RATE = Decimal("48")


@pytest.fixture
def egp_books_company(db):
    """default USD / functional EGP (the live Shopify_R shape), with a
    USD→EGP 48 SPOT rate on file — so a default-first regression converts
    (loud wrong amounts) instead of quarantining (silent no-JE)."""
    from projections.models import FiscalPeriod

    User = get_user_model()
    uid = uuid4().hex[:8]
    company = Company.objects.create(
        public_id=uuid4(),
        name=f"A146 Co {uid}",
        slug=f"a146-{uid}",
        default_currency="USD",
        functional_currency="EGP",
        is_active=True,
    )
    user = User.objects.create_user(
        public_id=uuid4(),
        email=f"owner-a146-{uid}@test.com",
        password="Testpass123!",
        name="A146 Owner",
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


@pytest.fixture
def egp_paymob_company(egp_books_company):
    """EGP-books company wired for the Paymob CSV path (the A16 shape:
    shopify accounts provide the EBD mapping, the store setup seeds the
    settlement providers)."""
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(egp_books_company)
    store = ShopifyStore.objects.create(
        company=egp_books_company,
        shop_domain="a146-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    return egp_books_company


# EGP amounts (functional magnitude): net 1455.00 across two orders.
PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,PMB-A146,2026-07-01
ORD-2,500.00,15.00,485.00,PMB-A146,2026-07-01
"""


@pytest.mark.django_db
def test_csv_import_emits_functional_currency_and_posts_unconverted(egp_paymob_company):
    """THE behavior change: the importer's currency guess is the books
    currency. RED under default-first: the event says "USD" and the JE
    converts the EGP magnitudes at 48 (EBD debit 69,840 instead of 1,455)."""
    company = egp_paymob_company

    batches = import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="paymob.csv",
    )
    assert len(batches) == 1

    event = BusinessEvent.objects.get(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
    )
    assert event.get_data()["currency"] == "EGP"

    PaymentSettlementProjection().process_pending(company)
    je = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement",
        source_document="paymob:PMB-A146",
    )
    assert je.status == je.Status.POSTED
    assert je.currency == "EGP"
    assert Decimal(str(je.exchange_rate)) == Decimal("1")
    # The EGP magnitudes hit the books UNCONVERTED — despite the USD→EGP 48
    # rate sitting on file, ready to multiply a mislabeled batch. (11600 =
    # the shopify-module EBD the paymob path drains into.)
    ebd_line = je.lines.get(debit__gt=0, account__code="11600")
    assert ebd_line.debit == Decimal("1455.00")


# Same batch, but the CSV SAYS the amounts are USD (Nxentra's own test pack
# carries a currency column) — the label must win over the functional guess.
PAYMOB_CSV_USD = b"""order_id,gross,fee,net,payout_batch_id,payout_date,currency
ORD-1,1000.00,30.00,970.00,PMB-A146-USD,2026-07-01,USD
ORD-2,500.00,15.00,485.00,PMB-A146-USD,2026-07-01,USD
"""

PAYMOB_CSV_MIXED = b"""order_id,gross,fee,net,payout_batch_id,payout_date,currency
ORD-1,1000.00,30.00,970.00,PMB-A146-MIX,2026-07-01,USD
ORD-2,500.00,15.00,485.00,PMB-A146-MIX,2026-07-01,EUR
"""


@pytest.mark.django_db
def test_csv_currency_column_wins_and_converts(egp_paymob_company):
    """An explicit USD label on EGP books flows through the convert path:
    the event stamps USD and the JE converts at the 48 rate on file."""
    company = egp_paymob_company

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV_USD,
        source_filename="paymob_usd.csv",
    )
    event = BusinessEvent.objects.get(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
    )
    assert event.get_data()["currency"] == "USD"

    PaymentSettlementProjection().process_pending(company)
    je = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement",
        source_document="paymob:PMB-A146-USD",
    )
    assert je.status == je.Status.POSTED
    assert je.currency == "USD"
    assert Decimal(str(je.exchange_rate)) == USD_EGP_RATE
    ebd_line = je.lines.get(debit__gt=0, account__code="11600")
    assert ebd_line.debit == (Decimal("1455.00") * USD_EGP_RATE).quantize(Decimal("0.01"))


@pytest.mark.django_db
def test_csv_mixed_currencies_in_one_batch_rejected(egp_paymob_company):
    from accounting.settlement_imports import SettlementImportError

    with pytest.raises(SettlementImportError, match="mixes currencies"):
        import_settlement_csv(
            company=egp_paymob_company,
            provider_normalized_code="paymob",
            file_content=PAYMOB_CSV_MIXED,
            source_filename="paymob_mixed.csv",
        )


@pytest.mark.django_db
def test_bosta_parser_captures_currency_column():
    from accounting.settlement_imports import parse_bosta_csv

    csv_bytes = b"""tracking_number,order_id,cod_amount,courier_fee,net,batch_id,payout_date,status,currency
AWB-1,ORD-1,500.00,20.00,480.00,BST-A146,2026-07-01,delivered,EGP
"""
    batches = parse_bosta_csv(csv_bytes)
    assert len(batches) == 1
    assert batches[0]["currency"] == "EGP"


def _emit_currency_less_settlement(company, batch_id: str):
    """A hand-built event with currency omitted — unreachable from today's
    emitters (both always set it), which is exactly why the fallbacks are
    hardening: the next emitter that forgets must land on the books currency."""
    from events.emitter import emit_event_no_actor

    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
        aggregate_type="PaymentSettlement",
        aggregate_id=f"stripe:{batch_id}",
        idempotency_key=f"payment.settlement.received:stripe:{batch_id}",
        data=PaymentSettlementReceivedData(
            amount="103.20",
            currency="",
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
            line_items=[
                {"order_id": "ch_a", "gross": "51.60", "fee": "3.20", "net": "48.40", "status": "charge"},
                {"order_id": "ch_b", "gross": "51.60", "fee": "3.20", "net": "48.40", "status": "charge"},
            ],
            provider_status="paid",
        ),
    )


@pytest.mark.django_db
def test_currency_less_event_read_model_falls_back_to_functional(egp_books_company):
    """PaymentsProjection hardening: header + lines land in the BOOKS currency,
    not the presentation default (the "5,848.88 USD" Stage-2 artifact class)."""
    from platform_connectors.models import ProviderPayout, ProviderPayoutLine
    from platform_connectors.projections import PaymentsProjection
    from stripe_connector.seed import setup_stripe_platform

    company = egp_books_company
    setup_stripe_platform(company)
    _emit_currency_less_settlement(company, "po_a146_rm")
    PaymentsProjection().process_pending(company)

    header = ProviderPayout.objects.get(company=company, provider="stripe", payout_batch_id="po_a146_rm")
    assert header.currency == "EGP"
    lines = ProviderPayoutLine.objects.filter(company=company, payout_batch_id="po_a146_rm")
    assert lines.count() == 2
    assert {line.currency for line in lines} == {"EGP"}


@pytest.mark.django_db
def test_currency_less_event_je_posts_in_functional_currency(egp_books_company):
    """JE-consumer hardening: the settlement JE lands on the books currency at
    rate 1 — not USD-stamped-then-converted-at-48."""
    from stripe_connector.seed import setup_stripe_platform

    company = egp_books_company
    setup_stripe_platform(company)
    _emit_currency_less_settlement(company, "po_a146_je")
    PaymentSettlementProjection().process_pending(company)

    je = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement",
        source_document="stripe:po_a146_je",
    )
    assert je.status == je.Status.POSTED
    assert je.currency == "EGP"
    assert Decimal(str(je.exchange_rate)) == Decimal("1")
    ebd_line = je.lines.get(debit__gt=0, account__code="11610")
    assert ebd_line.debit == Decimal("96.80")
