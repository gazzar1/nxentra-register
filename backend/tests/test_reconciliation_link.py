# tests/test_reconciliation_link.py
"""ADR-0001 prerequisite P5 — the durable ReconciliationLink read model.

A confirmed match becomes a first-class, queryable ROW written solely by the
ReconciliationProjection, with a deterministic identity so it survives a
from-scratch rebuild and unmatch→rematch reuses the same row.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import import_bank_statement
from accounting.models import Account
from accounting.settlement_imports import import_settlement_csv
from accounts.authz import ActorContext
from projections.write_barrier import projection_writes_allowed
from reconciliation.commands import auto_match_statement, unmatch_line
from reconciliation.models import ReconciliationLink, derive_link_id, derive_link_idempotency_key
from reconciliation.projections import ReconciliationProjection

PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-L1,1000.00,30.00,970.00,LINK-BATCH,2026-04-25
ORD-L2,500.00,15.00,485.00,LINK-BATCH,2026-04-25
"""
_BATCH_NET = Decimal("1455.00")
_BANK_DESC = "WIRE FROM PAYMOB SETTLEMENT REF: LINK-BATCH"


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="link-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return store


@pytest.fixture
def merchant_bank(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="Merchant Bank — link test",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


def _import_paymob_and_post(company):
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    import_settlement_csv(
        company=company, provider_normalized_code="paymob", file_content=PAYMOB_CSV, source_filename="link.csv"
    )
    PaymentSettlementProjection().process_pending(company)


def _make_statement(company, actor, merchant_bank, *, line_amount, line_date):
    result = import_bank_statement(
        actor=actor,
        account_id=merchant_bank.id,
        statement_date=line_date,
        period_start=line_date - timedelta(days=2),
        period_end=line_date + timedelta(days=2),
        opening_balance=Decimal("0"),
        closing_balance=line_amount,
        lines_data=[
            {
                "line_date": line_date.isoformat(),
                "amount": str(line_amount),
                "description": _BANK_DESC,
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="EGP",
    )
    assert result.success, result
    return result.data["statement"]


@pytest.mark.django_db
def test_link_created_on_auto_match(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)
    statement = _make_statement(company, actor, merchant_bank, line_amount=_BATCH_NET, line_date=date(2026, 4, 26))
    auto_match_statement(actor, statement.id)

    links = list(ReconciliationLink.objects.filter(company=company))
    assert len(links) == 1
    link = links[0]
    assert link.status == ReconciliationLink.Status.CONFIRMED
    assert link.bank_line_public_id and link.journal_line_public_id
    assert link.confidence is not None
    # Identity is the deterministic function of (bank_line, journal_line).
    expected_key = derive_link_idempotency_key(link.bank_line_public_id, link.journal_line_public_id)
    assert link.idempotency_key == expected_key
    assert link.id == derive_link_id(company.id, expected_key)


@pytest.mark.django_db
def test_link_marked_unmatched_on_unmatch(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)
    statement = _make_statement(company, actor, merchant_bank, line_amount=_BATCH_NET, line_date=date(2026, 4, 26))
    auto_match_statement(actor, statement.id)
    from accounting.models import BankStatementLine

    bank_line = BankStatementLine.objects.get(statement=statement)
    unmatch_line(actor, bank_line.id)

    link = ReconciliationLink.objects.get(company=company)
    assert link.status == ReconciliationLink.Status.UNMATCHED
    assert link.unmatched_at is not None


@pytest.mark.django_db
def test_link_survives_rebuild_deterministically(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)
    statement = _make_statement(company, actor, merchant_bank, line_amount=_BATCH_NET, line_date=date(2026, 4, 26))
    auto_match_statement(actor, statement.id)

    before = ReconciliationLink.objects.get(company=company)
    before_id, before_status, before_key = before.id, before.status, before.idempotency_key

    # Real rebuild path: clears (via _clear_projected_data) then replays.
    ReconciliationProjection().rebuild(company)

    after = list(ReconciliationLink.objects.filter(company=company))
    assert len(after) == 1, "rebuild must reproduce exactly one link, not duplicate or drop it"
    assert after[0].id == before_id, "link id must be deterministic across rebuild"
    assert after[0].status == before_status
    assert after[0].idempotency_key == before_key


@pytest.mark.django_db
def test_link_excluded_mirrors_bank_line_state(shopify_setup, company, actor, merchant_bank):
    from accounting.models import BankStatementLine
    from reconciliation.commands import exclude_line

    _import_paymob_and_post(company)
    statement = _make_statement(company, actor, merchant_bank, line_amount=_BATCH_NET, line_date=date(2026, 4, 26))
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)
    exclude_line(actor, bank_line.id)

    link = ReconciliationLink.objects.get(company=company)
    assert link.status == ReconciliationLink.Status.EXCLUDED


@pytest.mark.django_db
def test_link_needs_review_on_difference(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)
    # Deposit 55 short of the 1455 net (within the 15% near-match tolerance) →
    # MATCHED_WITH_DIFFERENCE → link NEEDS_REVIEW.
    statement = _make_statement(
        company, actor, merchant_bank, line_amount=Decimal("1400.00"), line_date=date(2026, 4, 26)
    )
    auto_match_statement(actor, statement.id)

    link = ReconciliationLink.objects.get(company=company)
    assert link.status == ReconciliationLink.Status.NEEDS_REVIEW
    assert link.difference_amount != 0
