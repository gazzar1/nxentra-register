# tests/test_recon_fx_bridge_stage3_inline.py
"""/finance/reconciliation trust slices (2026-07-05 critique follow-ups):

FX bridge — a foreign-currency Stage-2 payout row carries the functional
(books) equivalents AS POSTED at the settlement JE's stamped rate (A142), so
Stage-1 books-currency numbers and Stage-2 payout-currency numbers stop
requiring mental conversion. Rendered ONLY when provably coherent: settlement
JE posted, in the payout's currency, with a real (non-1.0) rate — the
accepted Paymob demo artifact (event baked USD over EGP magnitudes) must NOT
get a bridge asserting a conversion that never happened.

Stage-3 inline unmatched — the oldest unmatched bank lines render on the page
(counts alone forced a page-switch to see WHICH deposits were open), capped,
oldest-first, deep-linking to the statement workspace.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.models import Account, ExchangeRate, JournalEntry
from accounting.reconciliation_views import _STAGE3_UNMATCHED_LIMIT, _stage2_payouts, _stage3_summary
from accounts.authz import ActorContext
from projections.write_barrier import projection_writes_allowed


def _emit_stripe_settlement(company, batch_id: str):
    from accounting.payment_settlement_projection import PaymentSettlementProjection
    from events.emitter import emit_event_no_actor
    from events.types import EventTypes, PaymentSettlementReceivedData
    from platform_connectors.projections import PaymentsProjection

    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
        aggregate_type="PaymentSettlement",
        aggregate_id=f"stripe:{batch_id}",
        idempotency_key=f"payment.settlement.received:stripe:{batch_id}",
        data=PaymentSettlementReceivedData(
            amount="103.20",
            currency="USD",
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
            line_items=[{"order_id": "ch_x", "gross": "103.20", "fee": "6.40", "net": "96.80", "status": "charge"}],
            provider_status="paid",
        ),
    )
    PaymentsProjection().process_pending(company)
    PaymentSettlementProjection().process_pending(company)


@pytest.fixture
def stripe_ready(db, company, owner_membership):
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)
    return company


@pytest.fixture
def egp_books(stripe_ready, company):
    """The Shopify_R shape: default USD, functional (books) EGP, USD→EGP 48."""
    company.functional_currency = "EGP"
    company.save(update_fields=["functional_currency"])
    ExchangeRate.objects.create(
        company=company,
        from_currency="USD",
        to_currency="EGP",
        rate=Decimal("48"),
        effective_date=date.today() - timedelta(days=30),
    )
    return company


def _row(company, batch_id):
    rows = [r for r in _stage2_payouts(company) if r["batch_id"] == batch_id]
    return rows[0] if rows else None


# ── FX bridge ───────────────────────────────────────────────────────


def test_foreign_payout_carries_functional_bridge(egp_books, company):
    _emit_stripe_settlement(company, "po_fx_bridge")

    row = _row(company, "po_fx_bridge")
    assert row is not None
    assert row["status"] == "posted"  # the settlement JE posted (rate existed)
    assert row["currency"] == "USD"
    assert row["exchange_rate"] == "48"
    assert row["gross_functional"] == "4953.60"
    assert row["fees_functional"] == "307.20"
    assert row["net_functional"] == "4646.40"


def test_no_bridge_when_payout_is_in_books_currency(stripe_ready, company):
    """Default company: USD payout on USD books — nothing to bridge."""
    _emit_stripe_settlement(company, "po_fx_same")

    row = _row(company, "po_fx_same")
    assert row["exchange_rate"] is None
    assert row["net_functional"] is None


def test_no_bridge_without_settlement_je(egp_books, company):
    """Pending rows have no posted conversion to report — no bridge."""
    from events.emitter import emit_event_no_actor
    from events.types import EventTypes, PaymentSettlementReceivedData
    from platform_connectors.projections import PaymentsProjection

    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
        aggregate_type="PaymentSettlement",
        aggregate_id="stripe:po_fx_pending",
        idempotency_key="payment.settlement.received:stripe:po_fx_pending",
        data=PaymentSettlementReceivedData(
            amount="10.00",
            currency="USD",
            transaction_date=date.today().isoformat(),
            document_ref="po_fx_pending",
            provider_normalized_code="stripe",
            external_system="stripe",
            payout_batch_id="po_fx_pending",
            gross_amount="10.00",
            fees="0",
            net_amount="10.00",
            uncollected_amount="0",
            payment_method="card",
            payout_date=date.today().isoformat(),
            line_items=[],
            provider_status="paid",
        ),
    )
    PaymentsProjection().process_pending(company)  # header only, no drain JE

    row = _row(company, "po_fx_pending")
    assert row["status"] == "pending"
    assert row["exchange_rate"] is None


def test_no_bridge_when_je_currency_disagrees_with_payout(egp_books, company):
    """The Paymob-artifact shape: the row says USD but the JE was posted in
    EGP — bridging would assert a conversion that never happened."""
    _emit_stripe_settlement(company, "po_fx_mismatch")
    JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement",
        source_document="stripe:po_fx_mismatch",
    ).update(currency="EGP")

    row = _row(company, "po_fx_mismatch")
    assert row["exchange_rate"] is None
    assert row["net_functional"] is None


def test_no_bridge_when_rate_is_the_1_0_default(egp_books, company):
    """Pre-A142 JEs carry the 1.0 header default while their lines converted
    at the real rate — a 1:1 bridge would be a lie."""
    _emit_stripe_settlement(company, "po_fx_rate1")
    JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement",
        source_document="stripe:po_fx_rate1",
    ).update(exchange_rate=Decimal("1.0"))

    row = _row(company, "po_fx_rate1")
    assert row["exchange_rate"] is None


def test_summary_envelope_names_the_books_currency(egp_books, company, authenticated_client, owner_membership):
    resp = authenticated_client.get("/api/accounting/reconciliation/summary/")
    assert resp.status_code == 200
    assert resp.json()["stage2"]["functional_currency"] == "EGP"


# ── Stage-3 inline unmatched lines ──────────────────────────────────


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


@pytest.fixture
def merchant_bank(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="Merchant Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


def _import_lines(company, actor, merchant_bank, lines):
    from accounting.bank_reconciliation import import_bank_statement

    today = date.today()
    result = import_bank_statement(
        actor=actor,
        account_id=merchant_bank.id,
        statement_date=today,
        period_start=today - timedelta(days=60),
        period_end=today,
        opening_balance=Decimal("0"),
        closing_balance=sum(Decimal(ln["amount"]) for ln in lines),
        lines_data=lines,
        source="MANUAL",
        currency="USD",
    )
    assert result.success, result.error
    return result.data["statement"]


def test_unmatched_items_oldest_first_with_deep_link(db, company, actor, merchant_bank):
    today = date.today()
    statement = _import_lines(
        company,
        actor,
        merchant_bank,
        [
            {
                "line_date": (today - timedelta(days=40)).isoformat(),
                "value_date": (today - timedelta(days=40)).isoformat(),
                "amount": "500.00",
                "description": "OLD WIRE",
                "reference": "REF-OLD",
                "transaction_type": "credit",
            },
            {
                "line_date": (today - timedelta(days=2)).isoformat(),
                "value_date": (today - timedelta(days=2)).isoformat(),
                "amount": "120.00",
                "description": "FRESH DEPOSIT",
                "reference": "",
                "transaction_type": "credit",
            },
        ],
    )

    stage3 = _stage3_summary(company)
    assert stage3["unmatched_lines"] == 2
    items = stage3["unmatched_items"]
    assert [it["description"] for it in items] == ["OLD WIRE", "FRESH DEPOSIT"]  # oldest first
    oldest = items[0]
    assert oldest["statement_id"] == statement.id
    assert oldest["amount"] == "500.00"
    assert oldest["currency"] == "USD"
    assert oldest["reference"] == "REF-OLD"
    assert oldest["age_days"] == 40


def test_unmatched_items_capped_but_count_is_full(db, company, actor, merchant_bank):
    today = date.today()
    lines = [
        {
            "line_date": (today - timedelta(days=i)).isoformat(),
            "value_date": (today - timedelta(days=i)).isoformat(),
            "amount": "10.00",
            "description": f"LINE {i}",
            "reference": "",
            "transaction_type": "credit",
        }
        for i in range(_STAGE3_UNMATCHED_LIMIT + 3)
    ]
    _import_lines(company, actor, merchant_bank, lines)

    stage3 = _stage3_summary(company)
    assert stage3["unmatched_lines"] == _STAGE3_UNMATCHED_LIMIT + 3
    assert len(stage3["unmatched_items"]) == _STAGE3_UNMATCHED_LIMIT


def test_matched_lines_stay_out_of_the_inline_queue(db, company, actor, merchant_bank):
    from accounting.models import BankStatementLine

    today = date.today()
    statement = _import_lines(
        company,
        actor,
        merchant_bank,
        [
            {
                "line_date": today.isoformat(),
                "value_date": today.isoformat(),
                "amount": "77.00",
                "description": "WILL BE EXCLUDED",
                "reference": "",
                "transaction_type": "credit",
            }
        ],
    )
    BankStatementLine.objects.filter(statement=statement).update(match_status=BankStatementLine.MatchStatus.EXCLUDED)

    stage3 = _stage3_summary(company)
    assert stage3["unmatched_items"] == []
