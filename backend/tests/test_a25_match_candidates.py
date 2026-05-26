# tests/test_a25_match_candidates.py
"""
A25 — Manual-match picker surfaces settlement EBD lines as candidates.

Pre-A25 the picker only returned un-reconciled JournalLines on the
bank account itself. A merchant whose auto-match missed (because of
amount tolerance > 2% or date proximity > 7 days) had no UI path to
manually link the bank line to the original settlement JE's EBD line —
which is required for A16's difference-resolution flow to fire.

A25 adds a `get_match_candidates_for_bank_line` helper plus a new
`/bank-statements/lines/<pk>/candidates/` endpoint that returns the
union of (a) un-reconciled bank-account lines and (b) un-reconciled
EBD lines from settlement JEs, sorted by amount-proximity.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import (
    get_match_candidates_for_bank_line,
    import_bank_statement,
)
from accounting.models import (
    Account,
    BankStatementLine,
    JournalEntry,
)
from accounting.settlement_imports import import_settlement_csv
from accounts.authz import ActorContext
from projections.write_barrier import projection_writes_allowed


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a25-test.myshopify.com",
        access_token="t",
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
ORD-1,1000.00,30.00,970.00,PMB-A25,2026-04-25
ORD-2,500.00,15.00,485.00,PMB-A25,2026-04-25
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


def _make_bank_line(company, actor, merchant_bank, *, amount, description, line_date):
    period_start = line_date - timedelta(days=2)
    period_end = line_date + timedelta(days=2)
    result = import_bank_statement(
        actor=actor,
        account_id=merchant_bank.id,
        statement_date=line_date,
        period_start=period_start,
        period_end=period_end,
        opening_balance=Decimal("0"),
        closing_balance=amount,
        lines_data=[
            {
                "line_date": line_date.isoformat(),
                "value_date": line_date.isoformat(),
                "amount": str(amount),
                "description": description,
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="EGP",
    )
    assert result.success, result.error
    return BankStatementLine.objects.get(statement=result.data["statement"])


def test_candidates_include_settlement_ebd_lines(shopify_setup, company, actor, merchant_bank):
    """The new helper surfaces un-reconciled EBD lines from settlement
    JEs. Pre-A25, BNK-003-style merchants couldn't reach A16 from the
    UI because the picker hid these candidates."""
    _import_paymob_and_post(company)
    bank_line = _make_bank_line(
        company,
        actor,
        merchant_bank,
        amount=Decimal("1455.00"),
        description="WIRE PMB-A25",
        line_date=date(2026, 4, 26),
    )

    candidates = get_match_candidates_for_bank_line(bank_line)
    ebd = Account.objects.get(company=company, code="11600")
    settlement_ebd_jls = [c for c in candidates if c.account_id == ebd.id]
    assert len(settlement_ebd_jls) == 1
    ebd_line = settlement_ebd_jls[0]
    assert ebd_line.entry.source_module == "payment_settlement"
    assert ebd_line.debit == Decimal("1455.00")


def test_candidates_sorted_by_amount_proximity(shopify_setup, company, actor, merchant_bank):
    """The picker returns candidates sorted by amount-proximity to the
    bank line, so the closest match appears first in the UI."""
    # Two settlement JEs: one for 1455.00, one for 970.00 (different
    # batch). The picker should rank the 1455 candidate first when the
    # bank line is 1450 (5 short).
    _import_paymob_and_post(company)
    second_csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-3,1000.00,30.00,970.00,PMB-A25-2,2026-04-25
"""
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=second_csv,
        source_filename="paymob2.csv",
    )
    PaymentSettlementProjection().process_pending(company)

    bank_line = _make_bank_line(
        company,
        actor,
        merchant_bank,
        amount=Decimal("1450.00"),
        description="WIRE",
        line_date=date(2026, 4, 26),
    )

    candidates = get_match_candidates_for_bank_line(bank_line)
    assert len(candidates) >= 2
    ebd = Account.objects.get(company=company, code="11600")
    ebd_candidates = [c for c in candidates if c.account_id == ebd.id]
    # First EBD candidate must be the 1455 one (closest to 1450).
    assert ebd_candidates[0].debit == Decimal("1455.00")


def test_candidates_excludes_reconciled_ebd_lines(shopify_setup, company, actor, merchant_bank):
    """Already-reconciled EBD lines (post-auto-match) must not appear
    in the picker — paired with A19, this prevents orphan suggestions."""
    from reconciliation.commands import auto_match_statement

    _import_paymob_and_post(company)
    bank_line = _make_bank_line(
        company,
        actor,
        merchant_bank,
        amount=Decimal("1455.00"),
        description="WIRE PMB-A25",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, bank_line.statement.id)
    bank_line.refresh_from_db()
    assert bank_line.match_status != BankStatementLine.MatchStatus.UNMATCHED

    # New unmatched bank line — the EBD line is now reconciled and
    # shouldn't appear as a candidate for the second line.
    second_line = _make_bank_line(
        company,
        actor,
        merchant_bank,
        amount=Decimal("1455.00"),
        description="ANOTHER",
        line_date=date(2026, 4, 27),
    )
    candidates = get_match_candidates_for_bank_line(second_line)
    ebd = Account.objects.get(company=company, code="11600")
    ebd_candidates = [c for c in candidates if c.account_id == ebd.id]
    assert ebd_candidates == []


def test_candidates_excludes_reversed_clearance_je_lines(shopify_setup, company, actor, merchant_bank):
    """Clearance JEs reversed by A19 unmatch (status=REVERSED) must
    not appear in the picker. Otherwise the merchant sees stale
    suggestions for accounting that no longer exists."""
    from reconciliation.commands import auto_match_statement, unmatch_line

    _import_paymob_and_post(company)
    bank_line = _make_bank_line(
        company,
        actor,
        merchant_bank,
        amount=Decimal("1455.00"),
        description="WIRE PMB-A25",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, bank_line.statement.id)
    bank_line.refresh_from_db()
    unmatch_line(actor, bank_line.id)
    bank_line.refresh_from_db()

    candidates = get_match_candidates_for_bank_line(bank_line)
    # No candidate should reference a REVERSED entry.
    for c in candidates:
        assert c.entry.status != JournalEntry.Status.REVERSED


def test_candidates_endpoint_returns_combined_list(
    shopify_setup,
    company,
    actor,
    merchant_bank,
    authenticated_client,
    owner_membership,
):
    """The HTTP endpoint at /bank-statements/lines/<pk>/candidates/
    returns the candidate list with required fields (account_code,
    source_module, etc.) so the frontend picker can render rich rows
    that distinguish settlement-EBD candidates from bank-account ones."""
    _import_paymob_and_post(company)
    bank_line = _make_bank_line(
        company,
        actor,
        merchant_bank,
        amount=Decimal("1455.00"),
        description="WIRE PMB-A25",
        line_date=date(2026, 4, 26),
    )

    response = authenticated_client.get(f"/api/accounting/bank-statements/lines/{bank_line.id}/candidates/")
    assert response.status_code == 200, response.content
    data = response.json()
    assert isinstance(data, list)
    # At least the EBD settlement candidate is present.
    ebd_candidates = [c for c in data if c["source_module"] == "payment_settlement"]
    assert len(ebd_candidates) == 1
    candidate = ebd_candidates[0]
    assert candidate["account_code"] == "11600"
    assert Decimal(candidate["debit"]) == Decimal("1455.00")
    assert candidate["entry_id"]


def test_candidates_endpoint_404s_for_unknown_bank_line(authenticated_client, owner_membership):
    response = authenticated_client.get("/api/accounting/bank-statements/lines/999999/candidates/")
    assert response.status_code == 404
