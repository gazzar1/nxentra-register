# tests/test_a14b_settlement_prepass.py
"""
A14b — bank-rec settlement prepass.

When a bank deposit lands and matches a PaymentSettlement JE (created by
A14's CSV import), the auto-match flow:
1. finds the settlement JE by amount + payout_batch_id substring (or
   amount + date proximity fallback)
2. creates a clearance JE: DR Merchant Bank / CR Expected Bank Deposit
3. links the bank statement line to the clearance JE's bank-side line
4. marks the original settlement JE's EBD line as reconciled

End result: the merchant's books correctly show the deposit landing on
their bank account, and the Expected Bank Deposit balance drains to zero
for that batch.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import auto_match_statement
from accounting.models import (
    Account,
    BankStatementLine,
    JournalEntry,
)
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
        shop_domain="a14b-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return {"store": store}


@pytest.fixture
def merchant_bank(db, company):
    """A separate bank account that the bank statement is loaded against
    — distinct from the EXPECTED_BANK_DEPOSIT clearing account."""
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
ORD-1,1000.00,30.00,970.00,PMB-555,2026-04-25
ORD-2,500.00,15.00,485.00,PMB-555,2026-04-25
"""


def _import_paymob_and_post(company):
    """Import a Paymob CSV + run the projection so the settlement JE
    is in the DB and reconciliation can match against it."""
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="paymob.csv",
    )
    PaymentSettlementProjection().process_pending(company)


def _make_statement(company, actor, merchant_bank, *, line_amount, line_description, line_date):
    """Create a BankStatement + one BankStatementLine for the given bank."""
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
# Auto-match: payout_batch_id in description
# =============================================================================


def test_settlement_match_via_batch_id_substring_creates_clearance_je(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)

    settlement_je = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement",
        source_document="paymob:PMB-555",
    )
    assert settlement_je.status == JournalEntry.Status.POSTED

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),  # net = 970 + 485
        line_description="WIRE FROM PAYMOB SETTLEMENT REF: PMB-555",
        line_date=date(2026, 4, 26),
    )

    result = auto_match_statement(actor, statement.id)
    assert result.success
    assert result.data["settlement_matched"] == 1

    # Bank line is now AUTO_MATCHED, linked to the clearance JE's bank-side line.
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.AUTO_MATCHED
    assert bank_line.matched_journal_line is not None
    assert bank_line.matched_journal_line.account == merchant_bank
    assert bank_line.matched_journal_line.debit == Decimal("1455.00")

    # Clearance JE created with the right shape: DR Bank 1455 / CR EBD 1455.
    clearance_je = bank_line.matched_journal_line.entry
    assert clearance_je.source_module == "payment_settlement_clearance"
    assert clearance_je.source_document == "paymob:PMB-555"
    assert clearance_je.status == JournalEntry.Status.POSTED

    ebd = Account.objects.get(company=company, code="11600")
    cr_ebd = clearance_je.lines.get(account=ebd)
    assert cr_ebd.credit == Decimal("1455.00")

    # Original settlement JE's EBD DR line is now reconciled.
    settlement_ebd = settlement_je.lines.get(account=ebd)
    assert settlement_ebd.reconciled is True


def test_settlement_match_via_amount_date_fallback_when_no_batch_in_description(
    shopify_setup, company, actor, merchant_bank
):
    _import_paymob_and_post(company)

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="Generic deposit, no batch id",
        line_date=date(2026, 4, 25),  # same as payout_date
    )

    result = auto_match_statement(actor, statement.id)
    assert result.success
    # Amount + date proximity is enough to match.
    assert result.data["settlement_matched"] == 1

    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.AUTO_MATCHED
    assert bank_line.matched_journal_line.account == merchant_bank


def test_settlement_match_skips_when_amount_does_not_align(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("999.99"),  # Doesn't match any settlement net
        line_description="Random deposit",
        line_date=date(2026, 4, 26),
    )

    result = auto_match_statement(actor, statement.id)
    assert result.data["settlement_matched"] == 0
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED


def test_settlement_match_drains_ebd_balance(shopify_setup, company, actor, merchant_bank):
    # Full GL outcome check: after match, EBD net balance should be zero
    # for the settled batch, and Merchant Bank should reflect the deposit.
    _import_paymob_and_post(company)

    ebd = Account.objects.get(company=company, code="11600")
    # Pre-match: EBD has DR 1455 from the settlement JE.
    pre_match_ebd_debit = sum(line.debit for line in ebd.journal_lines.filter(entry__status=JournalEntry.Status.POSTED))
    pre_match_ebd_credit = sum(
        line.credit for line in ebd.journal_lines.filter(entry__status=JournalEntry.Status.POSTED)
    )
    assert pre_match_ebd_debit - pre_match_ebd_credit == Decimal("1455.00")

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="PMB-555 wire transfer",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)

    # Post-match: EBD has DR 1455 (settlement) and CR 1455 (clearance) → net zero.
    post_match_ebd_debit = sum(
        line.debit for line in ebd.journal_lines.filter(entry__status=JournalEntry.Status.POSTED)
    )
    post_match_ebd_credit = sum(
        line.credit for line in ebd.journal_lines.filter(entry__status=JournalEntry.Status.POSTED)
    )
    assert post_match_ebd_debit - post_match_ebd_credit == Decimal("0")

    # Merchant Bank now shows the 1455 DR.
    bank_balance = sum(
        line.debit - line.credit
        for line in merchant_bank.journal_lines.filter(entry__status=JournalEntry.Status.POSTED)
    )
    assert bank_balance == Decimal("1455.00")
