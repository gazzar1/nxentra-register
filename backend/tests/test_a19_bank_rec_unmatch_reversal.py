# tests/test_a19_bank_rec_unmatch_reversal.py
"""
A19 — Bank-rec unmatch / exclude must reverse the clearance JE.

When a settlement bank deposit is auto-matched, the prepass posts a
`payment_settlement_clearance` JE (DR Bank / CR EBD) and links the bank
line to its DR Bank line. If the merchant later unmatches or excludes
that bank line, the clearance JE must reverse so the merchant's bank
account doesn't carry an orphan DR for a deposit that's no longer
considered a match. The original settlement JE's EBD residual must
also be resurrected (reconciled flag flipped back).

For matches against pre-existing JEs (platform payouts, manual matches
to user-created entries), the existing behavior is preserved — those
JEs aren't synthesized by the match step, so they're not reversed on
unmatch.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import import_bank_statement
from accounting.models import (
    Account,
    BankStatementLine,
    JournalEntry,
)
from accounting.settlement_imports import import_settlement_csv
from accounts.authz import ActorContext
from projections.write_barrier import projection_writes_allowed
from reconciliation.commands import (
    auto_match_statement,
    exclude_line,
    manual_match,
    resolve_difference,
    unmatch_line,
)


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a19-test.myshopify.com",
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


PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,PMB-A19,2026-04-25
ORD-2,500.00,15.00,485.00,PMB-A19,2026-04-25
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


_LIVE_STATUSES = (JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED)


def _bank_balance(account):
    """Net DR-CR across all live entries, including REVERSED originals
    whose lines remain on the books and are offset by their reversal
    entries (kind=REVERSAL, status=POSTED)."""
    return sum(line.debit - line.credit for line in account.journal_lines.filter(entry__status__in=_LIVE_STATUSES))


def _ebd_balance(company):
    ebd = Account.objects.get(company=company, code="11600")
    return sum(line.debit - line.credit for line in ebd.journal_lines.filter(entry__status__in=_LIVE_STATUSES))


# =============================================================================
# Settlement clearance reversal — the core A19 fix
# =============================================================================


def test_unmatch_settlement_match_reverses_clearance_je(shopify_setup, company, actor, merchant_bank):
    """Unmatching a bank line that auto-matched against a settlement JE
    must reverse the clearance JE so the merchant's bank GL nets to zero
    again."""
    _import_paymob_and_post(company)

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="WIRE PMB-A19",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)

    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.AUTO_MATCHED
    clearance_je = bank_line.matched_journal_line.entry
    assert clearance_je.source_module == "payment_settlement_clearance"
    assert _bank_balance(merchant_bank) == Decimal("1455.00")

    result = unmatch_line(actor, bank_line.id)
    assert result.success, result.error

    # Bank line is back to UNMATCHED.
    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED
    assert bank_line.matched_journal_line is None

    # Clearance JE is REVERSED, with a partner reversal entry posted.
    clearance_je.refresh_from_db()
    assert clearance_je.status == JournalEntry.Status.REVERSED
    reversal = JournalEntry.objects.get(
        company=company,
        kind=JournalEntry.Kind.REVERSAL,
        reverses_entry=clearance_je,
    )
    assert reversal.status == JournalEntry.Status.POSTED

    # Merchant Bank GL nets to zero — the original 1455 DR is offset by
    # the reversal's 1455 CR. Without A19, this would still be 1455.
    assert _bank_balance(merchant_bank) == Decimal("0")


def test_unmatch_settlement_match_resurrects_settlement_ebd_residual(shopify_setup, company, actor, merchant_bank):
    """When the clearance JE drained EBD via an exact match, unmatching
    must flip the original settlement JE's EBD line back to
    reconciled=False so the residual is visible again."""
    _import_paymob_and_post(company)

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="WIRE PMB-A19",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)

    settlement_je = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement",
        source_document="paymob:PMB-A19",
    )
    ebd_account = Account.objects.get(company=company, code="11600")
    settlement_ebd = settlement_je.lines.get(account=ebd_account)
    assert settlement_ebd.reconciled is True

    bank_line = BankStatementLine.objects.get(statement=statement)
    result = unmatch_line(actor, bank_line.id)
    assert result.success, result.error

    settlement_ebd.refresh_from_db()
    assert settlement_ebd.reconciled is False
    assert settlement_ebd.reconciled_date is None

    # And the EBD GL balance is back to the original 1455 DR (residual
    # restored: settlement DR 1455 + clearance DR 1455 - clearance CR 1455
    # - reversal CR 1455 = 1455 net).
    assert _ebd_balance(company) == Decimal("1455.00")


def test_exclude_settlement_match_reverses_clearance_je(shopify_setup, company, actor, merchant_bank):
    """exclude_line follows the same reversal path as unmatch_line —
    the bank line is marked EXCLUDED but the clearance JE still must
    reverse to avoid orphan accounting."""
    _import_paymob_and_post(company)

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="WIRE PMB-A19",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)

    bank_line = BankStatementLine.objects.get(statement=statement)
    clearance_je = bank_line.matched_journal_line.entry

    result = exclude_line(actor, bank_line.id)
    assert result.success, result.error

    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.EXCLUDED
    clearance_je.refresh_from_db()
    assert clearance_je.status == JournalEntry.Status.REVERSED
    assert _bank_balance(merchant_bank) == Decimal("0")


def test_unmatch_resolved_difference_reverses_clearance_and_adjustment(shopify_setup, company, actor, merchant_bank):
    """When a bank line was MATCHED_WITH_DIFFERENCE and the merchant
    resolved the difference (A16 adjustment JE posted), unmatching must
    reverse BOTH the clearance JE and the adjustment JE so the EBD
    residual returns to its pre-match state."""
    _import_paymob_and_post(company)

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1450.00"),  # 5.00 short
        line_description="WIRE PMB-A19",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE

    # Resolve via EXTRA_FEE → DR fees / CR EBD for 5.00.
    res = resolve_difference(
        actor,
        bank_line.id,
        reason=BankStatementLine.DifferenceReason.EXTRA_FEE,
    )
    assert res.success, res.error
    bank_line.refresh_from_db()
    adjustment_je_id = bank_line.difference_adjustment_entry_id
    assert adjustment_je_id is not None
    clearance_je = bank_line.matched_journal_line.entry

    # Pre-unmatch: bank GL = 1450, EBD GL = 0 (1455 DR - 1450 CR clearance
    # - 5 CR adjustment). After unmatch both reversals offset.
    assert _bank_balance(merchant_bank) == Decimal("1450.00")
    assert _ebd_balance(company) == Decimal("0")

    result = unmatch_line(actor, bank_line.id)
    assert result.success, result.error

    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED
    assert bank_line.matched_journal_line is None
    assert bank_line.difference_adjustment_entry_id is None
    assert bank_line.difference_amount == Decimal("0")
    assert bank_line.difference_resolved_at is None

    clearance_je.refresh_from_db()
    assert clearance_je.status == JournalEntry.Status.REVERSED
    adjustment_je = JournalEntry.objects.get(pk=adjustment_je_id)
    assert adjustment_je.status == JournalEntry.Status.REVERSED

    # Bank back to zero, EBD back to the original 1455 DR.
    assert _bank_balance(merchant_bank) == Decimal("0")
    assert _ebd_balance(company) == Decimal("1455.00")


def test_unmatch_is_idempotent(shopify_setup, company, actor, merchant_bank):
    """Calling unmatch twice on the same line: first call performs the
    reversal, second call short-circuits with the standard 'not matched'
    error (no double-reversal)."""
    _import_paymob_and_post(company)

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="WIRE PMB-A19",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)

    first = unmatch_line(actor, bank_line.id)
    assert first.success
    second = unmatch_line(actor, bank_line.id)
    assert not second.success
    assert "not matched" in second.error.lower()

    # Only one reversal exists for the clearance JE — no double-counting.
    bank_line.refresh_from_db()
    assert _bank_balance(merchant_bank) == Decimal("0")


# =============================================================================
# Non-clearance matches: existing behavior preserved
# =============================================================================


def test_unmatch_manual_match_does_not_reverse_existing_je(shopify_setup, company, actor, merchant_bank):
    """Manual match against a pre-existing user-posted JE must not
    reverse that JE on unmatch — the JE has independent meaning. Only
    the reconciled flag is reset."""
    from accounting.commands import (
        create_journal_entry,
        post_journal_entry,
        save_journal_entry_complete,
    )

    fees_account = Account.objects.get(company=company, code="53000")
    create_res = create_journal_entry(
        actor=actor,
        date=date(2026, 4, 26),
        memo="Manually-recorded bank fee",
        lines=[
            {
                "account_id": merchant_bank.id,
                "description": "Bank fee deposit reversal",
                "debit": "100.00",
                "credit": "0",
            },
            {
                "account_id": fees_account.id,
                "description": "Bank fee",
                "debit": "0",
                "credit": "100.00",
            },
        ],
        kind=JournalEntry.Kind.NORMAL,
    )
    assert create_res.success
    entry = create_res.data
    save_journal_entry_complete(actor, entry.id)
    post_res = post_journal_entry(actor, entry.id)
    assert post_res.success
    user_entry = post_res.data
    assert user_entry.status == JournalEntry.Status.POSTED

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("100.00"),
        line_description="Misc deposit",
        line_date=date(2026, 4, 26),
    )
    bank_line = BankStatementLine.objects.get(statement=statement)
    bank_dr_line = user_entry.lines.get(account=merchant_bank)

    match_res = manual_match(actor, bank_line.id, bank_dr_line.id)
    assert match_res.success, match_res.error

    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED
    bank_dr_line.refresh_from_db()
    assert bank_dr_line.reconciled is True

    result = unmatch_line(actor, bank_line.id)
    assert result.success, result.error

    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED
    bank_dr_line.refresh_from_db()
    assert bank_dr_line.reconciled is False

    # User-posted JE is untouched — still POSTED, no reversal generated.
    user_entry.refresh_from_db()
    assert user_entry.status == JournalEntry.Status.POSTED
    assert not JournalEntry.objects.filter(kind=JournalEntry.Kind.REVERSAL, reverses_entry=user_entry).exists()
