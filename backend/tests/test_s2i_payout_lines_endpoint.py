# backend/tests/test_s2i_payout_lines_endpoint.py
"""ADR-0002 PR-D3 — GET /api/accounting/reconciliation/payout-lines/.

The Stage-2 expandable detail endpoint: canonical ProviderPayoutLine rows +
the header's PROVIDER_PAYOUT_RECONCILED outcome, keyed provider+batch (A144
rule). Read-only — unlike the legacy stripe detail GET it never triggers a
reconcile pass — and flag-free (a new surface with no legacy twin).
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from events.models import BusinessEvent
from events.types import EventTypes


class _FakeClient:
    def __init__(self, payouts, txns_by_payout):
        self._payouts = payouts
        self._txns = txns_by_payout

    def list_payouts(self, arrival_date_gte=None, status=None):
        return self._payouts

    def list_balance_transactions(self, payout_id):
        return self._txns.get(payout_id, [])


@pytest.fixture
def stamped_payout(db, company, monkeypatch):
    """s2h's fixture shape: synced payout, one matched charge, snapshot processed."""
    from platform_connectors.projections import PaymentsProjection
    from stripe_connector import sync as sync_mod
    from stripe_connector.models import StripeAccount, StripeCharge, StripePayout
    from stripe_connector.reconciliation import reconcile_payout
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)
    account = StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        status=StripeAccount.Status.ACTIVE,
        credential_ref="rk_test_dummy",
    )
    arrival = int(datetime(2026, 6, 20, tzinfo=UTC).timestamp())
    payout = {"id": "po_1", "amount": 14115, "currency": "usd", "arrival_date": arrival, "status": "paid"}
    txns = [
        {"id": "txn_1", "type": "charge", "amount": 10000, "fee": 590, "source": "ch_1"},
        {"id": "txn_2", "type": "charge", "amount": 5000, "fee": 295, "source": "ch_2"},
        {"id": "txn_p", "type": "payout", "amount": -14115, "fee": 0, "source": "po_1"},
    ]
    monkeypatch.setattr(sync_mod, "_stripe_client", lambda acct: _FakeClient([payout], {"po_1": txns}))
    sync_mod.sync_payouts(account)
    StripeCharge.objects.create(
        company=company,
        account=account,
        stripe_charge_id="ch_1",
        amount=Decimal("100.00"),
        fee=Decimal("5.90"),
        net=Decimal("94.10"),
        currency="USD",
        charge_date=datetime(2026, 6, 19, tzinfo=UTC).date(),
        stripe_created_at=timezone.now(),
    )
    reconcile_payout(company, StripePayout.objects.get(company=company, stripe_payout_id="po_1"))
    PaymentsProjection().process_pending(company)
    return account


URL = "/api/accounting/reconciliation/payout-lines/"


def test_requires_provider_and_batch(db, company, authenticated_client, owner_membership):
    assert authenticated_client.get(URL).status_code == 400
    assert authenticated_client.get(URL, {"provider": "stripe"}).status_code == 400
    assert authenticated_client.get(URL, {"batch_id": "po_1"}).status_code == 400


def test_unknown_batch_404s(db, company, authenticated_client, owner_membership):
    resp = authenticated_client.get(URL, {"provider": "stripe", "batch_id": "po_nope"})
    assert resp.status_code == 404


def test_lines_and_header_shape(db, company, stamped_payout, authenticated_client, owner_membership):
    resp = authenticated_client.get(URL, {"provider": "stripe", "batch_id": "po_1"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["provider"] == "stripe"
    assert data["batch_id"] == "po_1"
    header = data["header"]
    assert header["reconciliation_outcome"] == "discrepancy"  # 1 unmatched line
    assert (header["matched_line_count"], header["unmatched_line_count"]) == (1, 1)
    assert header["verified_line_count"] == 1
    assert header["total_line_count"] == 2
    assert (header["gross_variance"], header["fee_variance"], header["net_variance"]) == ("0.00", "0.00", "0.00")
    assert header["reconciliation_source"] == "auto_reconcile"
    assert header["currency"] == "USD"
    assert header["verify_supported"] is True

    lines = data["lines"]
    assert [ln["line_index"] for ln in lines] == [0, 1]
    matched = lines[0]
    assert matched["verified"] is True
    assert matched["match_kind"] == "charge"
    assert matched["matched_ref"] == "ch_1"
    assert matched["provider_line_ref"] == "txn_1"
    assert matched["verified_at"] is not None
    assert (matched["gross_amount"], matched["fee"], matched["net_amount"]) == ("100.00", "5.90", "94.10")
    unmatched = lines[1]
    assert unmatched["verified"] is False
    assert unmatched["match_kind"] == "none"
    assert unmatched["verified_at"] is None


def test_endpoint_is_read_only(db, company, stamped_payout, authenticated_client, owner_membership):
    """Unlike the legacy stripe detail GET (which runs reconcile_payout and
    mutates match state + emits), this endpoint must not write anything."""
    events_before = BusinessEvent.objects.filter(
        company=company, event_type=EventTypes.PROVIDER_PAYOUT_RECONCILED
    ).count()

    authenticated_client.get(URL, {"provider": "stripe", "batch_id": "po_1"})

    events_after = BusinessEvent.objects.filter(
        company=company, event_type=EventTypes.PROVIDER_PAYOUT_RECONCILED
    ).count()
    assert events_after == events_before


def test_provider_agnostic_paymob(db, company, authenticated_client, owner_membership):
    """A Paymob payout (settlement event only, never reconciled) serves lines
    with outcome '' — 'not yet reconciled', not an error."""
    from events.emitter import emit_event_no_actor
    from events.types import PaymentSettlementReceivedData
    from platform_connectors.projections import PaymentsProjection

    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
        aggregate_type="PaymentSettlement",
        aggregate_id="paymob:PMB-9",
        idempotency_key="payment.settlement.received:paymob:PMB-9",
        data=PaymentSettlementReceivedData(
            amount="500.00",
            currency="EGP",
            transaction_date="2026-06-20",
            document_ref="PMB-9",
            provider_normalized_code="paymob",
            external_system="shopify",
            payout_batch_id="PMB-9",
            gross_amount="500.00",
            fees="12.00",
            net_amount="488.00",
            uncollected_amount="0",
            payment_method="card",
            payout_date="2026-06-20",
            line_items=[{"order_id": "1001", "gross": "500.00", "fee": "12.00", "net": "488.00", "status": "settled"}],
        ),
    )
    PaymentsProjection().process_pending(company)

    resp = authenticated_client.get(URL, {"provider": "paymob", "batch_id": "PMB-9"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["header"]["reconciliation_outcome"] == ""
    assert data["header"]["verify_supported"] is False
    assert len(data["lines"]) == 1
    assert data["lines"][0]["verified"] is False
    assert data["lines"][0]["currency"] == "EGP"
