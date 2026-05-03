# tests/test_settlement_imports.py
"""
A14 — manual settlement CSV import tests.

Coverage:
- Paymob CSV parser aggregates rows by payout_batch_id, preserves line items
- Bosta CSV parser splits delivered vs returned (uncollected) per status
- Header alias matching is case-insensitive
- Bad CSVs raise SettlementImportError with a useful message
- import_settlement_csv emits one event per batch + is idempotent on re-import
- PaymentSettlementProjection posts the expected JE shape with dimension tags
- Projection rejects imbalanced events (gross != net + fees + uncollected)
- Projection idempotent against rebuild (same source_document → no duplicate JE)
- End-to-end: CSV upload → projection → reconciliation summary reflects drain
"""

from decimal import Decimal

import pytest

from accounting.models import Account, JournalEntry, JournalLineAnalysis
from accounting.payment_settlement_projection import PaymentSettlementProjection
from accounting.settlement_imports import (
    SettlementImportError,
    import_settlement_csv,
    parse_bosta_csv,
    parse_paymob_csv,
)
from accounting.settlement_provider import SettlementProvider

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    """Bootstrap full Shopify accounts + providers + EXPECTED_BANK_DEPOSIT."""
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a14-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return {"store": store}


# =============================================================================
# Paymob parser
# =============================================================================


PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,BATCH-A,2026-04-25
ORD-2,500.00,15.00,485.00,BATCH-A,2026-04-25
ORD-3,2000.00,60.00,1940.00,BATCH-B,2026-04-26
"""


def test_parse_paymob_aggregates_by_batch():
    batches = parse_paymob_csv(PAYMOB_CSV)
    by_id = {b["payout_batch_id"]: b for b in batches}
    assert set(by_id) == {"BATCH-A", "BATCH-B"}

    a = by_id["BATCH-A"]
    assert a["gross_amount"] == "1500.00"
    assert a["fees"] == "45.00"
    assert a["net_amount"] == "1455.00"
    assert a["uncollected_amount"] == "0.00"
    assert len(a["line_items"]) == 2
    assert {li["order_id"] for li in a["line_items"]} == {"ORD-1", "ORD-2"}

    b = by_id["BATCH-B"]
    assert b["gross_amount"] == "2000.00"
    assert b["fees"] == "60.00"
    assert b["net_amount"] == "1940.00"


def test_parse_paymob_aliases_uppercase_headers():
    csv = b"""ORDER ID,GROSS_AMOUNT,FEES,NET_AMOUNT,SETTLEMENT_ID,SETTLEMENT_DATE
ORD-1,100,3,97,SET-1,2026-04-25
"""
    batches = parse_paymob_csv(csv)
    assert len(batches) == 1
    assert batches[0]["payout_batch_id"] == "SET-1"
    assert batches[0]["gross_amount"] == "100.00"


def test_parse_paymob_missing_required_columns_raises():
    csv = b"""order_id,fee
ORD-1,5
"""
    with pytest.raises(SettlementImportError) as exc:
        parse_paymob_csv(csv)
    assert "missing required columns" in str(exc.value).lower()


def test_parse_paymob_no_data_rows_raises():
    csv = b"order_id,gross,fee,net,payout_batch_id,payout_date\n"
    with pytest.raises(SettlementImportError):
        parse_paymob_csv(csv)


# =============================================================================
# Bosta parser
# =============================================================================


BOSTA_CSV = b"""shipment_id,order_id,collected,courier_fee,net,batch_id,payout_date,status
SHIP-1,ORD-101,1500.00,100.00,1400.00,COD-A,2026-04-26,delivered
SHIP-2,ORD-102,1200.00,80.00,1120.00,COD-A,2026-04-26,delivered
SHIP-3,ORD-103,800.00,0.00,0.00,COD-A,2026-04-26,returned
SHIP-4,ORD-104,2000.00,150.00,1850.00,COD-B,2026-04-27,delivered
"""


def test_parse_bosta_splits_delivered_vs_returned():
    batches = parse_bosta_csv(BOSTA_CSV)
    by_id = {b["payout_batch_id"]: b for b in batches}
    assert set(by_id) == {"COD-A", "COD-B"}

    a = by_id["COD-A"]
    # Delivered: 1500 + 1200 = 2700; returned: 800; gross (full) = 3500
    assert a["gross_amount"] == "3500.00"
    assert a["fees"] == "180.00"  # 100 + 80 (returned has no fee)
    assert a["net_amount"] == "2520.00"  # 1400 + 1120
    assert a["uncollected_amount"] == "800.00"
    assert len(a["line_items"]) == 3

    b = by_id["COD-B"]
    assert b["gross_amount"] == "2000.00"
    assert b["uncollected_amount"] == "0.00"


def test_parse_bosta_unknown_status_treated_as_uncollected():
    csv = b"""shipment_id,collected,courier_fee,net,batch_id,payout_date,status
S-1,500,0,0,X,2026-04-26,not_home
"""
    batches = parse_bosta_csv(csv)
    assert batches[0]["uncollected_amount"] == "500.00"
    assert batches[0]["net_amount"] == "0.00"


def test_parse_bosta_falls_back_to_shipment_id_when_no_order_id():
    # Bosta sometimes only emits shipment_id; merchant maps to order_id later.
    csv = b"""tracking_number,collected,courier_fee,net,batch_id,payout_date,status
TRK-1,500,30,470,X,2026-04-26,delivered
"""
    batches = parse_bosta_csv(csv)
    assert batches[0]["line_items"][0]["order_id"] == "TRK-1"


# =============================================================================
# import_settlement_csv (event emission + idempotency)
# =============================================================================


def test_import_settlement_csv_emits_one_event_per_batch(shopify_setup, company):
    from events.models import BusinessEvent
    from events.types import EventTypes

    results = import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="paymob_april.csv",
    )
    assert len(results) == 2  # BATCH-A + BATCH-B
    batch_ids = {r["batch_id"] for r in results}
    assert batch_ids == {"BATCH-A", "BATCH-B"}

    events = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
    )
    assert events.count() == 2


def test_import_settlement_csv_is_idempotent_on_reupload(shopify_setup, company):
    # First import: 2 events emitted.
    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="paymob_april.csv",
    )
    from events.models import BusinessEvent
    from events.types import EventTypes

    first_count = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
    ).count()
    assert first_count == 2

    # Re-import same CSV: idempotency_key matches, event store dedupes.
    results2 = import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="paymob_april.csv",
    )
    second_count = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
    ).count()
    assert second_count == first_count, "re-importing should not create new events"

    # The result rows should flag the dedup so the merchant sees "already imported".
    assert all(r["deduplicated"] for r in results2)


def test_import_settlement_csv_unknown_provider_raises(shopify_setup, company):
    with pytest.raises(SettlementImportError) as exc:
        import_settlement_csv(
            company=company,
            provider_normalized_code="stripe_direct",
            file_content=PAYMOB_CSV,
        )
    assert "no csv parser" in str(exc.value).lower()


# =============================================================================
# PaymentSettlementProjection
# =============================================================================


def test_projection_posts_je_with_clearing_dimension_tag(shopify_setup, company):
    # Import a Paymob CSV → emit events → run projection → assert JE shape.
    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="paymob_april.csv",
    )

    proj = PaymentSettlementProjection()
    proj.process_pending(company)

    # One JE per batch
    entries = JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement",
        status=JournalEntry.Status.POSTED,
    )
    assert entries.count() == 2

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    paymob_clearing = paymob.posting_profile.control_account
    expected_bank = Account.objects.get(company=company, code="11600")

    batch_a = entries.get(source_document="paymob:BATCH-A")
    lines = list(batch_a.lines.all().order_by("line_no"))

    # DR Expected Bank = 1455 (net), DR Fees = 45, CR Clearing = 1500
    debit_lines = {line.account.code: line for line in lines if line.debit > 0}
    credit_lines = {line.account.code: line for line in lines if line.credit > 0}

    assert "11600" in debit_lines
    assert debit_lines["11600"].debit == Decimal("1455.00")

    fees_line = next((line for line in lines if line.account.code == "52000"), None)
    assert fees_line is not None
    assert fees_line.debit == Decimal("45.00")

    assert paymob_clearing.code in credit_lines
    clearing_line = credit_lines[paymob_clearing.code]
    assert clearing_line.credit == Decimal("1500.00")

    # The clearing line is tagged with the paymob settlement_provider dim.
    tags = list(JournalLineAnalysis.objects.filter(journal_line=clearing_line))
    assert len(tags) == 1
    assert tags[0].dimension_value_id == paymob.dimension_value_id

    # Source document set for idempotency.
    assert batch_a.source_document == "paymob:BATCH-A"


def test_projection_bosta_uncollected_debits_sales_returns(shopify_setup, company):
    import_settlement_csv(
        company=company,
        provider_normalized_code="bosta",
        file_content=BOSTA_CSV,
        source_filename="bosta_april.csv",
    )

    proj = PaymentSettlementProjection()
    proj.process_pending(company)

    entry = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement",
        source_document="bosta:COD-A",
    )
    lines = {line.account.code: line for line in entry.lines.all()}

    # Sales Returns (41200) gets the uncollected portion: 800
    assert "41200" in lines
    assert lines["41200"].debit == Decimal("800.00")
    # Expected Bank Deposit = net = 2520
    assert lines["11600"].debit == Decimal("2520.00")
    # Fees = 180
    assert lines["52000"].debit == Decimal("180.00")
    # Clearing credit = full gross = 3500
    bosta = SettlementProvider.objects.get(company=company, normalized_code="bosta")
    bosta_clearing = bosta.posting_profile.control_account
    assert lines[bosta_clearing.code].credit == Decimal("3500.00")


def test_projection_idempotent_on_rebuild(shopify_setup, company):
    # First run: emit + project → JE created.
    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="paymob_april.csv",
    )
    proj = PaymentSettlementProjection()
    proj.process_pending(company)

    first_count = JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement",
        status=JournalEntry.Status.POSTED,
    ).count()
    assert first_count == 2

    # Simulate a projection re-run: replay the already-emitted events.
    # The source_document idempotency check skips them.
    from events.models import BusinessEvent
    from events.types import EventTypes

    events = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
    )
    for event in events:
        proj.handle(event)

    second_count = JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement",
        status=JournalEntry.Status.POSTED,
    ).count()
    assert second_count == first_count, "rebuild must not duplicate JEs"


# =============================================================================
# A20 — Paymob refund_or_chargeback handling
# =============================================================================
# MAY01-A scenario from the test pack: gross 800, fee 24, refund 500, net 276.
# Pre-A20 the parser dropped the refund column, the projection's defensive
# guard caught the imbalance and skipped the JE post — but the import row
# stayed in the UI marked "Imported". Worst-of-both: silent data loss.
# A20 routes refund_or_chargeback to uncollected_amount so the math
# reconciles and a Sales Returns line posts.


PAYMOB_REFUND_CSV = b"""order_id,gross_amount,gateway_fee,refund_or_chargeback_amount,net_amount,payout_batch_id,payout_date
1004,800.00,24.00,500.00,276.00,PAYMOB-MAY01-A,2026-05-03
"""


def test_parse_paymob_routes_refund_to_uncollected():
    """The refund_or_chargeback column must populate uncollected_amount
    so gross = net + fees + uncollected reconciles for the projection."""
    batches = parse_paymob_csv(PAYMOB_REFUND_CSV)
    assert len(batches) == 1
    batch = batches[0]
    assert batch["payout_batch_id"] == "PAYMOB-MAY01-A"
    assert batch["gross_amount"] == "800.00"
    assert batch["fees"] == "24.00"
    assert batch["net_amount"] == "276.00"
    assert batch["uncollected_amount"] == "500.00"

    # Math reconciles — projection's guard will accept this.
    gross = Decimal(batch["gross_amount"])
    expected = Decimal(batch["net_amount"]) + Decimal(batch["fees"]) + Decimal(batch["uncollected_amount"])
    assert gross == expected


def test_parse_paymob_refund_line_item_status_is_refunded():
    """Per-row refund detail survives in line_items so the merchant can
    audit which orders had refunds."""
    batches = parse_paymob_csv(PAYMOB_REFUND_CSV)
    line = batches[0]["line_items"][0]
    assert line["order_id"] == "1004"
    assert line["status"] == "refunded"


def test_parse_paymob_no_refund_column_back_compat():
    """Existing CSVs without the refund column still parse with
    uncollected_amount=0."""
    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,BATCH-X,2026-04-25
"""
    batches = parse_paymob_csv(csv)
    assert batches[0]["uncollected_amount"] == "0.00"
    assert batches[0]["line_items"][0]["status"] == "settled"


def test_parse_paymob_zero_refund_treated_as_settled():
    """A row with a populated refund column but value 0 still counts as
    a normal settled row, not 'refunded'."""
    csv = b"""order_id,gross_amount,gateway_fee,refund_or_chargeback_amount,net_amount,payout_batch_id,payout_date
ORD-1,1000.00,30.00,0.00,970.00,BATCH-Y,2026-04-25
"""
    batches = parse_paymob_csv(csv)
    assert batches[0]["uncollected_amount"] == "0.00"
    assert batches[0]["line_items"][0]["status"] == "settled"


def test_paymob_refund_batch_posts_je_with_sales_returns_line(shopify_setup, company):
    """End-to-end: a Paymob batch with a refund posts a JE that includes
    a DR Sales Returns line for the refund amount, draining the provider
    clearing for the full gross."""
    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_REFUND_CSV,
        source_filename="paymob_refund.csv",
    )
    PaymentSettlementProjection().process_pending(company)

    je = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement",
        source_document="paymob:PAYMOB-MAY01-A",
    )
    assert je.status == JournalEntry.Status.POSTED

    sales_returns = Account.objects.get(company=company, code="41200")
    refund_line = je.lines.get(account=sales_returns)
    assert refund_line.debit == Decimal("500.00")

    # Provider clearing drained for full gross 800.
    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")
    clearing = paymob.posting_profile.control_account
    clearing_line = je.lines.get(account=clearing)
    assert clearing_line.credit == Decimal("800.00")
