# tests/test_a16_difference_engine.py
"""
A16 — Reconciliation Difference Engine.

When a bank deposit lands within tolerance of an expected EBD (settlement)
line but doesn't equal it exactly, the bank-rec auto-match still creates
the clearance JE (for the actual bank amount) and records the gap on the
bank line as MATCHED_WITH_DIFFERENCE / UNRESOLVED. The merchant then picks
a reason (extra fee / chargeback / write-off / rounding / etc.) which
posts the adjustment JE that drains the EBD residual.

Acceptance test for the merchant-facing question: "Shopify said X, the
bank deposited Y — where did the Z difference go?". After A16, Nxentra
shows Expected, Received, Difference, Reason, Status, and Action Needed.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import (
    _difference_tolerance,
    auto_match_statement,
    resolve_difference,
)
from accounting.models import (
    Account,
    BankStatementLine,
    JournalEntry,
)
from accounting.reconciliation_views import _build_narrative, _needs_review_queue
from accounting.settlement_imports import import_settlement_csv
from accounts.authz import ActorContext
from projections.write_barrier import projection_writes_allowed

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a16-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return {"store": store}


@pytest.fixture
def merchant_bank(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="Merchant Bank — EGP",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


# Paymob CSV: net = 1455.00 across two orders.
PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,PMB-A16,2026-04-25
ORD-2,500.00,15.00,485.00,PMB-A16,2026-04-25
"""


def _import_paymob_and_post(company):
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="paymob.csv",
    )
    PaymentSettlementProjection().process_pending(company)


def _make_statement(company, actor, merchant_bank, *, line_amount, line_description, line_date):
    from accounting.bank_reconciliation import import_bank_statement

    period_start = line_date - timedelta(days=2)
    period_end = line_date + timedelta(days=2)
    result = import_bank_statement(
        actor=actor,
        account_id=merchant_bank.id,
        statement_date=line_date,
        period_start=period_start,
        period_end=period_end,
        opening_balance=Decimal("0"),
        closing_balance=line_amount,
        lines_data=[
            {
                "line_date": line_date.isoformat(),
                "value_date": line_date.isoformat(),
                "amount": str(line_amount),
                "description": line_description,
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="EGP",
    )
    assert result.success, f"statement import failed: {result.error}"
    return result.data["statement"]


# =============================================================================
# Tolerance helper
# =============================================================================


def test_difference_tolerance_uses_2_percent_capped_at_500():
    # 2% of 1,000 = 20 → use 20 (under cap).
    assert _difference_tolerance(Decimal("1000")) == Decimal("20.00")
    # 2% of 30,000 = 600 → cap at 500.
    assert _difference_tolerance(Decimal("30000")) == Decimal("500")
    # 2% of 25,000 = 500 → exactly the cap.
    assert _difference_tolerance(Decimal("25000")) == Decimal("500.00")


# =============================================================================
# Near-match detection (within tolerance)
# =============================================================================


def test_near_match_within_tolerance_creates_matched_with_difference(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)

    # Settlement EBD = 1455.00. Bank shows 1450.00 → 5.00 short (within 2% = 29.10).
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1450.00"),
        line_description="WIRE FROM PAYMOB SETTLEMENT REF: PMB-A16",
        line_date=date(2026, 4, 26),
    )

    result = auto_match_statement(actor, statement.id)
    assert result.success
    assert result.data["settlement_matched"] == 1

    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE
    assert bank_line.difference_amount == Decimal("5.00")
    assert bank_line.difference_reason == BankStatementLine.DifferenceReason.UNRESOLVED

    # Clearance JE was posted for the ACTUAL bank amount (1450), not the
    # expected EBD (1455).
    clearance_je = bank_line.matched_journal_line.entry
    assert clearance_je.source_module == "payment_settlement_clearance"
    assert bank_line.matched_journal_line.debit == Decimal("1450.00")

    # EBD residual still open (1455 expected − 1450 cleared = 5 left).
    ebd = Account.objects.get(company=company, code="11600")
    settlement_je = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement",
        source_document="paymob:PMB-A16",
    )
    settlement_ebd = settlement_je.lines.get(account=ebd)
    assert settlement_ebd.reconciled is False


def test_outside_tolerance_does_not_match(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)

    # 1455 expected, bank shows 1400 → 55.00 gap. Tolerance = 2% of 1455
    # = 29.10. 55 > 29.10 → must NOT match.
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1400.00"),
        line_description="PMB-A16 wire",
        line_date=date(2026, 4, 26),
    )

    result = auto_match_statement(actor, statement.id)
    assert result.data["settlement_matched"] == 0

    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED
    assert bank_line.difference_amount == Decimal("0")

    # Settlement EBD also stays unreconciled.
    ebd = Account.objects.get(company=company, code="11600")
    settlement_je = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement",
        source_document="paymob:PMB-A16",
    )
    settlement_ebd = settlement_je.lines.get(account=ebd)
    assert settlement_ebd.reconciled is False


def test_over_paid_near_match_records_negative_difference(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)

    # 1455 expected, bank shows 1460 → 5 over.
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1460.00"),
        line_description="PMB-A16 wire",
        line_date=date(2026, 4, 26),
    )

    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE
    # difference = expected - bank = 1455 - 1460 = -5.
    assert bank_line.difference_amount == Decimal("-5.00")


# =============================================================================
# resolve_difference: short-paid (positive diff)
# =============================================================================


def test_resolve_difference_extra_fee_short_paid_posts_adjustment_je(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1450.00"),  # 5.00 short
        line_description="PMB-A16 wire",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.difference_amount == Decimal("5.00")

    result = resolve_difference(
        actor,
        bank_line.id,
        reason=BankStatementLine.DifferenceReason.EXTRA_FEE,
        notes="Paymob support ticket #123",
    )
    assert result.success, result.error

    bank_line.refresh_from_db()
    assert bank_line.difference_reason == BankStatementLine.DifferenceReason.EXTRA_FEE
    assert bank_line.difference_resolved_at is not None
    assert bank_line.difference_adjustment_entry_id == result.data["adjustment_entry_id"]
    assert bank_line.difference_notes == "Paymob support ticket #123"

    # Adjustment JE should be DR Payment Processing Fees / CR EBD for 5.00.
    adj = JournalEntry.objects.get(pk=result.data["adjustment_entry_id"])
    assert adj.status == JournalEntry.Status.POSTED
    assert adj.source_module == "payment_settlement_difference"
    assert adj.source_document == "paymob:PMB-A16"

    ebd = Account.objects.get(company=company, code="11600")
    fee_account = Account.objects.get(company=company, code="53000")  # PAYMENT_PROCESSING_FEES
    assert adj.lines.get(account=fee_account).debit == Decimal("5.00")
    assert adj.lines.get(account=ebd).credit == Decimal("5.00")

    # Settlement EBD line is now drained: 1455 DR (settlement) =
    # 1450 CR (clearance) + 5 CR (adjustment). Mark reconciled.
    settlement_je = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement",
        source_document="paymob:PMB-A16",
    )
    settlement_ebd = settlement_je.lines.get(account=ebd)
    settlement_ebd.refresh_from_db()
    assert settlement_ebd.reconciled is True


# =============================================================================
# resolve_difference: over-paid (negative diff) reverses JE direction
# =============================================================================


def test_resolve_difference_over_paid_reverses_je_direction(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)

    # Bank over-pays by 5.00.
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1460.00"),
        line_description="PMB-A16 wire",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.difference_amount == Decimal("-5.00")

    result = resolve_difference(
        actor,
        bank_line.id,
        reason=BankStatementLine.DifferenceReason.ROUNDING,
    )
    assert result.success, result.error

    adj = JournalEntry.objects.get(pk=result.data["adjustment_entry_id"])
    ebd = Account.objects.get(company=company, code="11600")
    fee_account = Account.objects.get(company=company, code="53000")

    # Over-paid: DR EBD / CR reason_account for |diff| = 5.00.
    assert adj.lines.get(account=ebd).debit == Decimal("5.00")
    assert adj.lines.get(account=fee_account).credit == Decimal("5.00")


# =============================================================================
# resolve_difference: rejection paths
# =============================================================================


def test_resolve_difference_rejects_invalid_reason(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1450.00"),
        line_description="PMB-A16 wire",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)

    result = resolve_difference(actor, bank_line.id, reason="MADE_UP_REASON")
    assert not result.success
    assert "Reason must be one of" in result.error


def test_resolve_difference_rejects_unmatched_bank_line(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)
    # Bank line that doesn't match anything.
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("999.99"),
        line_description="random",
        line_date=date(2026, 4, 26),
    )
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED

    result = resolve_difference(
        actor,
        bank_line.id,
        reason=BankStatementLine.DifferenceReason.EXTRA_FEE,
    )
    assert not result.success
    assert "MATCHED_WITH_DIFFERENCE" in result.error


def test_resolve_difference_rejects_already_resolved(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1450.00"),
        line_description="PMB-A16 wire",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)

    first = resolve_difference(
        actor,
        bank_line.id,
        reason=BankStatementLine.DifferenceReason.EXTRA_FEE,
    )
    assert first.success

    second = resolve_difference(
        actor,
        bank_line.id,
        reason=BankStatementLine.DifferenceReason.BANK_CHARGE,
    )
    assert not second.success
    assert "already resolved" in second.error.lower()


# =============================================================================
# Narrative: 'Tell me the story'
# =============================================================================


def test_narrative_zero_activity_prompts_to_connect_store():
    stage1_totals = {
        "total_expected": "0.00",
        "total_settled": "0.00",
        "open_balance": "0.00",
        "aged_30_plus": "0.00",
    }
    needs_review = {"unresolved_difference_count": 0, "unresolved_difference_amount": "0.00"}
    text = _build_narrative(stage1_totals, {}, {}, needs_review, "USD")
    assert "No Shopify activity yet" in text


def test_narrative_includes_currency_and_amounts():
    stage1_totals = {
        "total_expected": "150000.00",
        "total_settled": "147900.00",
        "open_balance": "2100.00",
        "aged_30_plus": "0.00",
    }
    needs_review = {"unresolved_difference_count": 0, "unresolved_difference_amount": "0.00"}
    text = _build_narrative(stage1_totals, {}, {}, needs_review, "EGP")
    assert "Shopify says 150,000.00 EGP sold" in text
    assert "147,900.00" in text  # settled
    assert "2,100.00" in text  # still expected


def test_narrative_flags_unresolved_differences():
    stage1_totals = {
        "total_expected": "10000.00",
        "total_settled": "9500.00",
        "open_balance": "500.00",
        "aged_30_plus": "0.00",
    }
    needs_review = {
        "unresolved_difference_count": 2,
        "unresolved_difference_amount": "75.00",
    }
    text = _build_narrative(stage1_totals, {}, {}, needs_review, "EGP")
    assert "2 bank deposits matched within tolerance" in text
    assert "75.00" in text
    assert "Needs Review queue" in text


def test_narrative_flags_aged_open_balance():
    stage1_totals = {
        "total_expected": "20000.00",
        "total_settled": "0.00",
        "open_balance": "20000.00",
        "aged_30_plus": "8000.00",
    }
    needs_review = {"unresolved_difference_count": 0, "unresolved_difference_amount": "0.00"}
    text = _build_narrative(stage1_totals, {}, {}, needs_review, "USD")
    assert "8,000.00 USD is over 30 days old" in text


# =============================================================================
# Needs Review queue
# =============================================================================


def test_needs_review_queue_lists_unresolved_difference_rows(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1450.00"),
        line_description="PMB-A16 wire",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)

    queue = _needs_review_queue(company)
    assert queue["unresolved_difference_count"] == 1
    assert queue["unresolved_difference_amount"] == "5.00"

    item = queue["items"][0]
    assert item["kind"] == "bank_line_difference"
    assert item["provider_code"] == "paymob"
    assert item["batch_id"] == "PMB-A16"
    assert item["expected"] == "1455.00"
    assert item["received"] == "1450.00"
    assert item["difference"] == "5.00"
    assert item["difference_direction"] == "short_paid"
    assert item["age_days"] >= 0

    # Available reasons must enumerate every choice except UNRESOLVED.
    reason_values = {r["value"] for r in item["available_reasons"]}
    assert BankStatementLine.DifferenceReason.UNRESOLVED.value not in reason_values
    assert BankStatementLine.DifferenceReason.EXTRA_FEE.value in reason_values
    assert BankStatementLine.DifferenceReason.CHARGEBACK.value in reason_values


def test_needs_review_queue_excludes_resolved_rows(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1450.00"),
        line_description="PMB-A16 wire",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)
    resolve_difference(
        actor,
        bank_line.id,
        reason=BankStatementLine.DifferenceReason.EXTRA_FEE,
    )

    queue = _needs_review_queue(company)
    assert queue["unresolved_difference_count"] == 0
    assert queue["items"] == []
