# tests/test_stage2_payout_ledger.py
"""Stage-2 payout ledger — per-payout rows on /finance/reconciliation driven
by the canonical ProviderPayout read-models (the redesign's first slice).

Status chip contract:
  pending   — canonical header exists, no settlement JE (surfaced, not hidden)
  posted    — settlement JE posted, deposit not yet bank-matched
  banked    — a POSTED clearance JE exists for {provider}:{batch}
  attention — the matched deposit carries an unresolved difference

Built on the A144 rule: every join is keyed by the payout's own
provider/batch source_document — no module-hardcoded accounts.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.models import Account
from accounting.reconciliation_views import _stage2_payouts
from accounts.authz import ActorContext
from projections.write_barrier import projection_writes_allowed

STRIPE_NET = Decimal("96.80")


def _emit_stripe_settlement(company, batch_id: str, run_settlement_projection: bool = True):
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
    PaymentsProjection().process_pending(company)  # canonical header/lines
    if run_settlement_projection:
        PaymentSettlementProjection().process_pending(company)  # drain JE


@pytest.fixture
def stripe_ready(db, company, owner_membership):
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)
    return company


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


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


def _import_and_match(company, actor, merchant_bank, *, amount, description):
    from accounting.bank_reconciliation import import_bank_statement
    from reconciliation.commands import auto_match_statement

    line_date = date.today()
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
        currency="USD",
    )
    assert result.success, result.error
    matched = auto_match_statement(actor, result.data["statement"].id)
    assert matched.success, matched.error
    return matched.data["matched"]


def _row(company, batch_id):
    rows = [r for r in _stage2_payouts(company) if r["batch_id"] == batch_id]
    return rows[0] if rows else None


def test_posted_payout_before_bank_match(stripe_ready, company):
    _emit_stripe_settlement(company, "po_ledger_posted")

    row = _row(company, "po_ledger_posted")
    assert row is not None
    assert row["provider"] == "stripe"
    assert row["provider_name"] == "Stripe"
    assert row["status"] == "posted"
    assert (row["gross_amount"], row["fees"], row["net_amount"]) == ("103.20", "6.40", "96.80")
    assert row["currency"] == "USD"
    assert row["settlement_entry_number"].startswith("JE-")
    assert row["clearance_entry_id"] is None


def test_banked_payout_after_auto_match(stripe_ready, company, actor, merchant_bank):
    _emit_stripe_settlement(company, "po_ledger_banked")
    assert (
        _import_and_match(company, actor, merchant_bank, amount=STRIPE_NET, description="STRIPE po_ledger_banked") >= 1
    )

    row = _row(company, "po_ledger_banked")
    assert row["status"] == "banked"
    assert row["clearance_entry_number"].startswith("JE-")
    assert row["clearance_entry_id"] is not None


def test_attention_when_difference_unresolved(stripe_ready, company, actor, merchant_bank):
    _emit_stripe_settlement(company, "po_ledger_diff")
    short_paid = STRIPE_NET - Decimal("1.00")
    assert _import_and_match(company, actor, merchant_bank, amount=short_paid, description="STRIPE po_ledger_diff") >= 1

    row = _row(company, "po_ledger_diff")
    assert row["status"] == "attention"


def test_pending_when_no_settlement_je(stripe_ready, company):
    """Canonical header without a drain JE (e.g. projection posted the header
    but the settlement JE was skipped) must be SURFACED as pending."""
    _emit_stripe_settlement(company, "po_ledger_pending", run_settlement_projection=False)

    row = _row(company, "po_ledger_pending")
    assert row is not None
    assert row["status"] == "pending"
    assert row["settlement_entry_id"] is None


def test_summary_endpoint_carries_the_ledger(stripe_ready, company, authenticated_client, owner_membership):
    _emit_stripe_settlement(company, "po_ledger_api")

    resp = authenticated_client.get("/api/accounting/reconciliation/summary/")
    assert resp.status_code == 200
    payouts = resp.json()["stage2"]["payouts"]
    assert any(p["batch_id"] == "po_ledger_api" for p in payouts)
