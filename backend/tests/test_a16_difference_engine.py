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

from accounting.models import (
    Account,
    BankStatementLine,
    JournalEntry,
)
from accounting.reconciliation_views import _build_narrative, _needs_review_queue
from accounting.settlement_imports import import_settlement_csv
from accounts.authz import ActorContext
from projections.write_barrier import projection_writes_allowed
from reconciliation.commands import auto_match_statement, resolve_difference
from reconciliation.matching import _difference_tolerance

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


def test_difference_tolerance_uses_15_percent_capped_at_10000():
    # A35 widened tolerance from 2%/500 to 15%/10000 so real-merchant
    # short-payments (5-15% gap is common for Egyptian COD couriers)
    # auto-flag as MATCHED_WITH_DIFFERENCE instead of staying Unmatched.
    # 15% of 1,000 = 150 → use 150 (under cap).
    assert _difference_tolerance(Decimal("1000")) == Decimal("150.00")
    # 15% of 100,000 = 15,000 → cap at 10,000.
    assert _difference_tolerance(Decimal("100000")) == Decimal("10000")
    # 15% of 66,667 ≈ 10,000 → exactly the cap.
    assert _difference_tolerance(Decimal("66667")) == Decimal("10000")


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


def test_a35_widened_tolerance_catches_realistic_short_payment(shopify_setup, company, actor, merchant_bank):
    """A35: BNK-003-style scenario — 200 EGP gap on a 2050 EGP expected
    (~9.76%) used to fall outside the old 2% tolerance and stay
    Unmatched. After widening to 15%, it auto-flags as
    MATCHED_WITH_DIFFERENCE so the merchant can resolve via A16."""
    # Use a 9550 EGP expected so 950 short = 9.95% gap. The default
    # paymob CSV in this file expects 1455; create a custom one.
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    big_csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-X,10000.00,450.00,9550.00,PMB-BIG,2026-04-25
"""
    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=big_csv,
        source_filename="paymob.csv",
    )
    PaymentSettlementProjection().process_pending(company)

    # Bank short by 950 (9.95%, would have been outside 2% tolerance).
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("8600.00"),
        line_description="PMB-BIG wire",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE
    assert bank_line.difference_amount == Decimal("950.00")


def test_outside_tolerance_does_not_match(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)

    # A35: 1455 expected, bank shows 1000 → 455.00 gap. Tolerance =
    # 15% of 1455 = 218.25. 455 > 218.25 → must NOT match.
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1000.00"),
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
    assert "No sales activity yet" in text
    assert "Connect a store or payment provider" in text


def test_narrative_includes_currency_and_amounts():
    stage1_totals = {
        "total_expected": "150000.00",
        "total_settled": "147900.00",
        "open_balance": "2100.00",
        "aged_30_plus": "0.00",
    }
    needs_review = {"unresolved_difference_count": 0, "unresolved_difference_amount": "0.00"}
    text = _build_narrative(stage1_totals, {}, {}, needs_review, "EGP")
    # No stage1_rows supplied → the channel-neutral fallback subject.
    assert "Your sales channels say 150,000.00 EGP sold" in text
    assert "147,900.00" in text  # settled
    assert "2,100.00" in text  # still expected


def test_narrative_names_single_provider():
    """A143: Stage 1 includes every SETTLEMENT_PROVIDER-tagged channel since
    A139, so the subject names the channel(s) that actually sold instead of
    hardcoding 'Shopify'."""
    stage1_totals = {
        "total_expected": "150000.00",
        "total_settled": "147900.00",
        "open_balance": "2100.00",
        "aged_30_plus": "0.00",
    }
    needs_review = {"unresolved_difference_count": 0, "unresolved_difference_amount": "0.00"}
    stage1_rows = [
        {"provider_name": "Shopify Payments", "total_debit": "150000.00", "open_balance": "2100.00"},
    ]
    text = _build_narrative(stage1_totals, {}, {}, needs_review, "EGP", stage1_rows=stage1_rows)
    assert "Shopify Payments says 150,000.00 EGP sold" in text


def test_narrative_names_multiple_providers():
    stage1_totals = {
        "total_expected": "155760.00",
        "total_settled": "147900.00",
        "open_balance": "7860.00",
        "aged_30_plus": "0.00",
    }
    needs_review = {"unresolved_difference_count": 0, "unresolved_difference_amount": "0.00"}
    stage1_rows = [
        {"provider_name": "Shopify Payments", "total_debit": "150000.00", "open_balance": "2100.00"},
        {"provider_name": "Stripe", "total_debit": "5760.00", "open_balance": "5760.00"},
        # Zero-sales channels stay out of the subject.
        {"provider_name": "Bosta", "total_debit": "0.00", "open_balance": "0.00"},
    ]
    text = _build_narrative(stage1_totals, {}, {}, needs_review, "EGP", stage1_rows=stage1_rows)
    assert "Shopify Payments + Stripe say 155,760.00 EGP sold" in text
    assert "Bosta say" not in text


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


def test_narrative_warns_on_negative_clearing():
    """A35: when any provider's clearing has been over-drained
    (settlement-without-original-order or refund-already-credit-noted),
    the narrative banner must lead with a red callout."""
    stage1_totals = {
        "total_expected": "16950.00",
        "total_settled": "18200.00",
        "open_balance": "-1250.00",
        "aged_30_plus": "0.00",
    }
    needs_review = {"unresolved_difference_count": 0, "unresolved_difference_amount": "0.00"}
    stage1_rows = [
        {"provider_name": "Bosta", "open_balance": "-2200.00"},
        {"provider_name": "Paymob", "open_balance": "950.00"},
        {"provider_name": "Paymob Accept", "open_balance": "0.00"},
    ]
    text = _build_narrative(
        stage1_totals,
        {},
        {},
        needs_review,
        "EGP",
        stage1_rows=stage1_rows,
    )
    assert "Bosta clearing is negative" in text
    assert "2,200.00 EGP" in text
    assert "Investigate Bosta drilldown" in text
    # Paymob (positive open) should NOT be flagged.
    assert "Paymob clearing is negative" not in text


def test_narrative_no_negative_warning_when_all_providers_positive():
    """When every open balance is >= 0, the warning callout doesn't fire."""
    stage1_totals = {
        "total_expected": "10000.00",
        "total_settled": "5000.00",
        "open_balance": "5000.00",
        "aged_30_plus": "0.00",
    }
    needs_review = {"unresolved_difference_count": 0, "unresolved_difference_amount": "0.00"}
    stage1_rows = [
        {"provider_name": "Paymob", "open_balance": "5000.00"},
        {"provider_name": "Bosta", "open_balance": "0.00"},
    ]
    text = _build_narrative(
        stage1_totals,
        {},
        {},
        needs_review,
        "EGP",
        stage1_rows=stage1_rows,
    )
    assert "is negative" not in text
    assert "Investigate" not in text


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
