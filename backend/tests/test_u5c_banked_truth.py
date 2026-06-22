# tests/test_u5c_banked_truth.py
"""U5c truth gate — characterize the Stage-1 "Banked" computation BEFORE any
rewrite, so a number-changing refactor is gated on a must-match test.

Central question the redesign memo raised: should Stage-1 Banked be re-sourced
from `ReconciliationLink.provider_normalized_code` (the durable leg) instead of
the JE `source_document` join in `_banked_by_provider`?

These tests establish the ground truth:

  1. `_banked_by_provider` joins settlement-JE ⇄ clearance-JE on the FULL
     `source_document` (`{provider}:{batch}`), and attributes via the settlement
     JE's clearing-credit analysis tag (the *actual* tagged provider). So two
     providers that happen to share a batch *suffix* stay correctly separated.
     A naive "group by link.provider_normalized_code" (the *parent* code) would
     regress this — hence this is a must-match guard, not a thing to rewrite.

  2. `_settlement_je_for_batch` (Money-Trace helper) matches on the batch
     *suffix* only, so it CANNOT disambiguate providers that share a batch id —
     the genuinely non-provider-scoped piece U5c removes.

Modeled on the realistic settlement→bank→auto-match→clearance flow in
`test_a14b_settlement_prepass.py`.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.models import (
    Account,
    AnalysisDimension,
    JournalEntry,
)
from accounting.reconciliation_views import _banked_by_provider, _settlement_je_for_batch
from accounting.settlement_imports import import_settlement_csv
from accounting.settlement_provider import (
    SETTLEMENT_PROVIDER_DIMENSION_CODE,
    _provider_dimension_value_code,
)
from accounts.authz import ActorContext
from projections.write_barrier import projection_writes_allowed
from reconciliation.commands import auto_match_statement

# =============================================================================
# Fixtures (mirrors test_a14b_settlement_prepass.py)
# =============================================================================


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="u5c-test.myshopify.com",
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


# Two providers, SAME batch id "SHARED-1" — paymob nets 1455 (970+485),
# bosta nets 760 (collected 800 − courier 40). Distinct amounts so auto-match
# is unambiguous; identical batch id so the suffix-vs-full-string question bites.
PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-P1,1000.00,30.00,970.00,SHARED-1,2026-04-25
ORD-P2,500.00,15.00,485.00,SHARED-1,2026-04-25
"""

BOSTA_CSV = b"""shipment_id,order_id,collected,courier_fee,net,batch_id,payout_date,status
SHIP-B1,ORD-B1,800.00,40.00,760.00,SHARED-1,2026-04-25,delivered
"""


def _import_and_post(company, *, provider, content):
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    import_settlement_csv(
        company=company,
        provider_normalized_code=provider,
        file_content=content,
        source_filename=f"{provider}.csv",
    )
    PaymentSettlementProjection().process_pending(company)


def _deposit_and_match(company, actor, merchant_bank, *, amount, description, line_date):
    from accounting.bank_reconciliation import import_bank_statement

    result = import_bank_statement(
        actor=actor,
        account_id=merchant_bank.id,
        statement_date=line_date,
        period_start=line_date - timedelta(days=2),
        period_end=line_date + timedelta(days=2),
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
    assert result.success, f"statement import failed: {result.error}"
    statement = result.data["statement"]
    match = auto_match_statement(actor, statement.id)
    assert match.success
    assert match.data["settlement_matched"] == 1, f"expected 1 settlement match, got {match.data}"
    return statement


def _dim_value_id(company, normalized_code):
    dimension = AnalysisDimension.objects.get(company=company, code=SETTLEMENT_PROVIDER_DIMENSION_CODE)
    return dimension.values.get(code=_provider_dimension_value_code(normalized_code)).id


@pytest.fixture
def two_providers_shared_batch(shopify_setup, company, actor, merchant_bank):
    """Paymob + Bosta settlements that share batch id SHARED-1, each banked."""
    _import_and_post(company, provider="paymob", content=PAYMOB_CSV)
    _import_and_post(company, provider="bosta", content=BOSTA_CSV)

    # Sanity: both settlement JEs exist with provider-prefixed source_documents.
    assert JournalEntry.objects.filter(
        company=company, source_module="payment_settlement", source_document="paymob:SHARED-1"
    ).exists()
    assert JournalEntry.objects.filter(
        company=company, source_module="payment_settlement", source_document="bosta:SHARED-1"
    ).exists()

    _deposit_and_match(
        company,
        actor,
        merchant_bank,
        amount=Decimal("1455.00"),
        description="PAYMOB SHARED-1 wire",
        line_date=date(2026, 4, 26),
    )
    _deposit_and_match(
        company,
        actor,
        merchant_bank,
        amount=Decimal("760.00"),
        description="BOSTA SHARED-1 payout",
        line_date=date(2026, 4, 26),
    )
    return company


# =============================================================================
# Truth #1 — _banked_by_provider is already provider-scoped (must-match guard)
# =============================================================================


def test_banked_by_provider_keeps_shared_batch_suffix_providers_separate(two_providers_shared_batch):
    company = two_providers_shared_batch
    dimension = AnalysisDimension.objects.get(company=company, code=SETTLEMENT_PROVIDER_DIMENSION_CODE)

    banked = _banked_by_provider(company, dimension)

    paymob_dv = _dim_value_id(company, "paymob")
    bosta_dv = _dim_value_id(company, "bosta")

    # The crux: despite the shared "SHARED-1" batch suffix, the full
    # source_document join (paymob:SHARED-1 vs bosta:SHARED-1) keeps each
    # provider's banked amount correctly attributed. A rewrite that grouped by
    # the link's parent provider_normalized_code, or by batch suffix, would
    # collapse or cross-contaminate these — this test forbids that regression.
    assert banked.get(paymob_dv) == Decimal("1455.00")
    assert banked.get(bosta_dv) == Decimal("760.00")


# =============================================================================
# Truth #2 — _settlement_je_for_batch is the genuinely non-provider-scoped bit
# =============================================================================


def test_settlement_je_for_batch_cannot_disambiguate_shared_suffix(two_providers_shared_batch):
    company = two_providers_shared_batch

    # Two distinct settlement JEs share the batch suffix SHARED-1.
    matches = list(
        JournalEntry.objects.filter(
            company=company, source_module="payment_settlement", status=JournalEntry.Status.POSTED
        ).filter(source_document__endswith="SHARED-1")
    )
    assert {je.source_document for je in matches} == {"paymob:SHARED-1", "bosta:SHARED-1"}

    # The helper takes only a bare batch_id and suffix-matches, so it returns
    # ONE of them with no way to ask for a specific provider — this is the
    # collision U5c eliminates by reading provider-scoped link legs.
    found = _settlement_je_for_batch(company, "payment_settlement", "SHARED-1")
    assert found is not None
    assert found.source_document in {"paymob:SHARED-1", "bosta:SHARED-1"}
