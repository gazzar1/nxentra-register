# backend/tests/test_s2e_payments_canonical_backfill.py
"""ADR-0002 Phase 2 C2 — payments_canonical_backfill command.

Reproduces the company-37/41 drift (settlement events exist, canonical rows
missing because they were emitted before PaymentsProjection processed them) and
proves the command rebuilds canonical consistently + idempotently, reports the
historical enrichment gap, and never mutates in report-only mode.

In tests, event emission's projection trigger runs via transaction.on_commit,
which does NOT fire under the test transaction — so emitting a settlement event
naturally leaves canonical missing, exactly like the droplet state.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.core.management import call_command

from platform_connectors.management.commands.payments_canonical_backfill import build_summary


class _FakeClient:
    def __init__(self, payouts, txns_by_payout):
        self._payouts = payouts
        self._txns = txns_by_payout

    def list_payouts(self, arrival_date_gte=None, status=None):
        return self._payouts

    def list_balance_transactions(self, payout_id):
        return self._txns.get(payout_id, [])


@pytest.fixture
def stripe_event_no_canonical(db, company, monkeypatch):
    """Sync a Stripe payout → event + legacy StripePayout written, but canonical
    rows NOT built (on_commit projection trigger doesn't fire in tests)."""
    from stripe_connector import sync as sync_mod
    from stripe_connector.models import StripeAccount
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)
    account = StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        display_name="Acme Stripe",
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
    return account


def test_drift_exists_then_backfill_rebuilds_consistently(db, company, stripe_event_no_canonical):
    from platform_connectors.models import ProviderPayout, ProviderPayoutLine

    # Drift: event + legacy exist, canonical missing (company-41 state).
    assert ProviderPayout.objects.filter(company=company).count() == 0
    assert ProviderPayoutLine.objects.filter(company=company).count() == 0

    # Report-only must NOT mutate.
    report = build_summary(apply=False)
    assert ProviderPayout.objects.filter(company=company).count() == 0
    co = next(c for c in report["companies"] if c["company_id"] == company.id)
    assert co["header_missing"] == 1  # the report SEES the gap

    # --apply rebuilds canonical from the event.
    applied = build_summary(apply=True)
    assert ProviderPayout.objects.filter(company=company, provider="stripe", payout_batch_id="po_1").count() == 1
    assert ProviderPayoutLine.objects.filter(company=company, payout_batch_id="po_1").count() == 2
    co = next(c for c in applied["companies"] if c["company_id"] == company.id)
    assert co["header_missing"] == 0
    assert co["reconstruct_ok"] == 1
    assert co["lag"] == 0
    # Parity vs the legacy StripePayout (fully enriched — this event was emitted
    # post-PR-C1 by the fixture, so status/account propagate).
    assert co["stripe_parity_ok"] == 1
    assert co["stripe_parity_mismatch"] == []


def test_backfill_is_idempotent(db, company, stripe_event_no_canonical):
    from platform_connectors.models import ProviderPayout, ProviderPayoutLine

    build_summary(apply=True)
    build_summary(apply=True)  # re-run
    assert ProviderPayout.objects.filter(company=company, payout_batch_id="po_1").count() == 1
    assert ProviderPayoutLine.objects.filter(company=company, payout_batch_id="po_1").count() == 2


def test_reports_historical_enrichment_gap(db, company):
    """A pre-PR-C1 event (no provider_status/account) → backfill builds the header
    with blank enrichment, and the command REPORTS it rather than hiding it."""
    from events.emitter import emit_event_no_actor
    from events.types import EventTypes, PaymentSettlementReceivedData
    from platform_connectors.models import ProviderPayout

    # An event WITHOUT the PR-C1 neutral fields (simulates a pre-enrichment payout).
    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
        aggregate_type="PaymentSettlement",
        aggregate_id="stripe:po_old",
        idempotency_key="payment.settlement.received:stripe:po_old",
        data=PaymentSettlementReceivedData(
            currency="USD",
            provider_normalized_code="stripe",
            external_system="stripe",
            payout_batch_id="po_old",
            gross_amount="100.00",
            fees="5.00",
            net_amount="95.00",
            uncollected_amount="0",
            payout_date="2026-05-01",
            line_items=[{"order_id": "ch_x", "gross": "100.00", "fee": "5.00", "net": "95.00", "status": "charge"}],
            # no provider_status / provider_account_reference
        ),
    )

    applied = build_summary(apply=True)
    header = ProviderPayout.objects.get(company=company, provider="stripe", payout_batch_id="po_old")
    assert header.gross_amount == Decimal("100.00")
    assert header.provider_status == ""  # blank — event predates enrichment
    co = next(c for c in applied["companies"] if c["company_id"] == company.id)
    assert co["provider_status_blank"] >= 1
    assert co["account_ref_blank"] >= 1
    assert co["reconstruct_ok"] == 1  # totals still reconstruct from the event


def test_paymob_event_reconstructs_with_uncollected(db, company):
    """A Paymob settlement (refund line) reconstructs into the canonical header +
    lines — the provider-agnostic path, no legacy StripePayout involved."""
    from accounting.settlement_imports import import_settlement_csv
    from platform_connectors.models import ProviderPayout, ProviderPayoutLine

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=(
            b"order_id,gross,fee,net,refund,payout_batch_id,payout_date\n"
            b"ORD-1,1000.00,30.00,970.00,0,PMB-Z,2026-04-25\n"
            b"ORD-2,500.00,15.00,285.00,200.00,PMB-Z,2026-04-25\n"
        ),
    )
    applied = build_summary(apply=True)
    header = ProviderPayout.objects.get(company=company, provider="paymob", payout_batch_id="PMB-Z")
    assert header.uncollected_amount == Decimal("200.00")  # batch-level uncollected from the refund
    assert ProviderPayoutLine.objects.filter(company=company, provider="paymob", payout_batch_id="PMB-Z").count() == 2
    co = next(c for c in applied["companies"] if c["company_id"] == company.id)
    assert co["reconstruct_ok"] == 1
    assert co["reconstruct_mismatch"] == []


def test_call_command_runs_and_prints_cleanly(db, company, stripe_event_no_canonical):
    """The management entrypoint must not crash on its printed report (handle()
    returns None — call_command writes any truthy return to stdout)."""
    call_command("payments_canonical_backfill")  # report-only
    call_command("payments_canonical_backfill", "--apply")  # rebuild
    from platform_connectors.models import ProviderPayout

    assert ProviderPayout.objects.filter(company=company, payout_batch_id="po_1").count() == 1
