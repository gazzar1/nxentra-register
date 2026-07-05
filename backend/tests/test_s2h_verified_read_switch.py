# backend/tests/test_s2h_verified_read_switch.py
"""ADR-0002 PR-D2 — PaymentsProjection consumes PROVIDER_PAYOUT_RECONCILED and
the verified-count reads flip canonical behind STRIPE_CANONICAL_VERIFIED_READS.

The A139 headline: verified match-state now lives on ProviderPayoutLine,
stamped from full-state snapshots, and survives wipe+replay. The read switch
is parity-provable (byte-identical counts when legacy and canonical agree) and
non-vacuous (a canonical-only mutation is visible ONLY flag-on).
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.test import override_settings
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
    """The s2c/s2f payout synced + one charge matched + snapshots processed:
    legacy has verified=1/2, canonical lines carry the same stamped state."""
    from platform_connectors.projections import PaymentsProjection
    from stripe_connector import sync as sync_mod
    from stripe_connector.models import StripeAccount, StripeCharge, StripePayout
    from stripe_connector.reconciliation import reconcile_payout
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
    legacy_payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    reconcile_payout(company, legacy_payout)  # persists ch_1 match + emits the snapshot
    PaymentsProjection().process_pending(company)
    return account


def _canonical_line(company, batch, index):
    from platform_connectors.models import ProviderPayoutLine

    return ProviderPayoutLine.objects.get(company=company, provider="stripe", payout_batch_id=batch, line_index=index)


# ── projection stamping ─────────────────────────────────────────────


def test_projection_stamps_line_and_header_state(db, company, stamped_payout):
    from platform_connectors.models import ProviderPayout

    line0 = _canonical_line(company, "po_1", 0)  # ch_1, matched
    assert line0.verified is True
    assert line0.match_kind == "charge"
    assert line0.matched_ref == "ch_1"
    assert line0.matched_ref_type == "charge"
    assert line0.provider_line_ref == "txn_1"
    assert line0.verified_at is not None

    line1 = _canonical_line(company, "po_1", 1)  # ch_2, unmatched
    assert line1.verified is False
    assert line1.match_kind == "none"
    assert line1.matched_ref == ""
    assert line1.verified_at is None  # never verified → no verified timestamp

    header = ProviderPayout.objects.get(company=company, provider="stripe", payout_batch_id="po_1")
    assert header.reconciliation_outcome == "discrepancy"  # 1 unmatched line
    assert (header.matched_line_count, header.unmatched_line_count, header.verified_line_count) == (1, 1, 1)
    assert (header.gross_variance, header.fee_variance, header.net_variance) == (
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
    )
    assert header.reconciliation_source == "auto_reconcile"
    assert header.last_reconciled_at is not None


def test_rebuild_reconstructs_verdicts_from_events(db, company, stamped_payout):
    """A139 headline: wipe + replay reproduces the stamped match state exactly —
    including clearing a stale verdict injected outside the event stream."""
    from platform_connectors.models import ProviderPayoutLine
    from platform_connectors.projections import PaymentsProjection

    def state():
        return list(
            ProviderPayoutLine.objects.filter(company=company, provider="stripe")
            .order_by("line_index")
            .values("line_index", "verified", "match_kind", "matched_ref", "matched_ref_type", "provider_line_ref")
        )

    before = state()
    assert before[0]["verified"] is True  # sanity: there is real state to lose

    # Stale injection (settings.TESTING bypasses the projection-only save guard).
    stale = _canonical_line(company, "po_1", 1)
    stale.verified = True
    stale.match_kind = "charge"
    stale.matched_ref = "ch_bogus"
    stale.save()
    assert state() != before

    proj = PaymentsProjection()
    proj.rebuild(company)
    while proj.process_pending(company, limit=1000):
        pass
    assert state() == before


def test_last_write_wins_across_snapshots(db, company, stamped_payout, monkeypatch):
    """A second snapshot (ch_2 arrives and matches) supersedes the first on
    replay — the FINAL event's stamp is final state."""
    from platform_connectors.models import ProviderPayout
    from platform_connectors.projections import PaymentsProjection
    from stripe_connector.models import StripeCharge, StripePayout
    from stripe_connector.reconciliation import reconcile_payout

    StripeCharge.objects.create(
        company=company,
        account=stamped_payout,
        stripe_charge_id="ch_2",
        amount=Decimal("50.00"),
        fee=Decimal("2.95"),
        net=Decimal("47.05"),
        currency="USD",
        charge_date=datetime(2026, 6, 19, tzinfo=UTC).date(),
        stripe_created_at=timezone.now(),
    )
    legacy_payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    reconcile_payout(company, legacy_payout)  # second snapshot: all matched → verified

    assert BusinessEvent.objects.filter(company=company, event_type=EventTypes.PROVIDER_PAYOUT_RECONCILED).count() == 2

    proj = PaymentsProjection()
    proj.rebuild(company)
    while proj.process_pending(company, limit=1000):
        pass

    assert _canonical_line(company, "po_1", 1).verified is True
    header = ProviderPayout.objects.get(company=company, provider="stripe", payout_batch_id="po_1")
    assert header.reconciliation_outcome == "verified"
    assert (header.matched_line_count, header.unmatched_line_count, header.verified_line_count) == (2, 0, 2)


def test_settlement_reapply_does_not_zero_verdicts(db, company, stamped_payout):
    """The settlement handler's update_or_create defaults must never grow the
    match-state fields — re-applying the settlement event leaves verdicts
    intact (the load-bearing gotcha pinned)."""
    from platform_connectors.projections import PaymentsProjection

    settlement = BusinessEvent.objects.get(company=company, idempotency_key="payment.settlement.received:stripe:po_1")
    PaymentsProjection()._handle_settlement(settlement)  # simulate a re-apply

    line0 = _canonical_line(company, "po_1", 0)
    assert line0.verified is True
    assert line0.match_kind == "charge"


# ── read switch (both-ways + non-vacuous) ───────────────────────────


def _get_json(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, resp.content
    return resp.json()


def test_verified_counts_identical_under_flag(db, company, stamped_payout, authenticated_client, owner_membership):
    """When legacy and canonical agree (the parity-gate condition), flipping
    STRIPE_CANONICAL_VERIFIED_READS is byte-invisible on both views."""
    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        legacy_join = _get_json(authenticated_client, "/api/stripe/payouts/")
        summary_legacy = _get_json(
            authenticated_client, "/api/stripe/reconciliation/?date_from=2026-06-01&date_to=2026-06-30"
        )
        with override_settings(STRIPE_CANONICAL_VERIFIED_READS=True):
            canonical = _get_json(authenticated_client, "/api/stripe/payouts/")
            summary_canonical = _get_json(
                authenticated_client, "/api/stripe/reconciliation/?date_from=2026-06-01&date_to=2026-06-30"
            )

    assert canonical == legacy_join
    assert summary_canonical == summary_legacy
    row = canonical["results"][0]
    assert (row["transactions_total"], row["transactions_verified"]) == (2, 1)
    assert row["reconciliation_status"] == "partial"
    assert row["journal_entry_id"] is None  # JE join stays legacy (C4)


def test_flag_on_reads_canonical_verified_not_legacy(
    db, company, stamped_payout, authenticated_client, owner_membership
):
    """Non-vacuous proof: a canonical-only verified mutation shows up ONLY
    under the verified flag."""
    line1 = _canonical_line(company, "po_1", 1)
    line1.verified = True
    line1.save()  # settings.TESTING bypasses the save guard

    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        legacy_row = _get_json(authenticated_client, "/api/stripe/payouts/")["results"][0]
        assert legacy_row["transactions_verified"] == 1  # legacy join unaffected
        with override_settings(STRIPE_CANONICAL_VERIFIED_READS=True):
            canonical_row = _get_json(authenticated_client, "/api/stripe/payouts/")["results"][0]
            assert canonical_row["transactions_verified"] == 2
            assert canonical_row["reconciliation_status"] == "verified"


def test_explainer_verified_flag_switch(db, company, stamped_payout):
    from bank_connector.matching import _explain_stripe_payout
    from stripe_connector.models import StripePayout

    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")

    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        legacy_join = _explain_stripe_payout(company, payout)
        with override_settings(STRIPE_CANONICAL_VERIFIED_READS=True):
            canonical = _explain_stripe_payout(company, payout)
    # Parity when the two sides agree.
    assert canonical == legacy_join

    # Non-vacuous: canonical-only mutation visible only flag-on.
    line1 = _canonical_line(company, "po_1", 1)
    line1.verified = True
    line1.save()
    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        assert sum(t["verified"] for t in _explain_stripe_payout(company, payout)["transactions"]) == 1
        with override_settings(STRIPE_CANONICAL_VERIFIED_READS=True):
            assert sum(t["verified"] for t in _explain_stripe_payout(company, payout)["transactions"]) == 2


# ── parity counters (the flip gate) ─────────────────────────────────


def test_backfill_reports_verified_parity(db, company, stamped_payout):
    from platform_connectors.management.commands.payments_canonical_backfill import build_summary

    summary = build_summary(apply=False, company_id=company.id)
    [rep] = summary["companies"]
    assert rep["reconciled_events"] == 1
    assert rep["verified_parity_ok"] == 1
    assert rep["verified_parity_mismatch"] == []
    assert rep["verified_parity_skipped_no_event"] == 0

    # A canonical-only drift is a MISMATCH — the gate must catch it.
    line1 = _canonical_line(company, "po_1", 1)
    line1.verified = True
    line1.save()
    summary = build_summary(apply=False, company_id=company.id)
    [rep] = summary["companies"]
    assert rep["verified_parity_ok"] == 0
    assert rep["verified_parity_mismatch"] == ["stripe:po_1: verified 2!=1"]


def test_backfill_skips_eventless_payouts_visibly(db, company, stamped_payout):
    """Event-less payouts (pre-PR-A / seeded demos) can never reach canonical
    parity — they are counted OUTSIDE the gate, not silently and not blocking."""
    from platform_connectors.management.commands.payments_canonical_backfill import build_summary
    from stripe_connector.models import StripePayout, StripePayoutTransaction

    legacy_only = StripePayout.objects.create(
        company=company,
        account=stamped_payout,
        stripe_payout_id="po_seeded",
        gross_amount=Decimal("10.00"),
        fees=Decimal("1.00"),
        net_amount=Decimal("9.00"),
        currency="USD",
        payout_date=datetime(2026, 5, 1, tzinfo=UTC).date(),
    )
    StripePayoutTransaction.objects.create(
        company=company,
        payout=legacy_only,
        stripe_balance_txn_id="txn_seeded",
        transaction_type="charge",
        amount=Decimal("10.00"),
        fee=Decimal("1.00"),
        net=Decimal("9.00"),
        currency="USD",
        source_id="ch_seeded",
        verified=True,
    )

    summary = build_summary(apply=False, company_id=company.id)
    [rep] = summary["companies"]
    assert rep["verified_parity_skipped_no_event"] == 1
    assert rep["verified_parity_ok"] == 1  # po_1 still passes; the gate is achievable
    assert rep["verified_parity_mismatch"] == []


# ── provider-agnostic (the event is not Stripe-shaped) ──────────────


def test_paymob_reconciled_snapshot_stamps_canonical_lines(db, company):
    from events.emitter import emit_event_no_actor
    from events.types import PaymentSettlementReceivedData
    from platform_connectors.event_types import ProviderPayoutReconciledData
    from platform_connectors.models import ProviderPayout
    from platform_connectors.projections import PaymentsProjection

    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
        aggregate_type="PaymentSettlement",
        aggregate_id="paymob:PMB-77",
        idempotency_key="payment.settlement.received:paymob:PMB-77",
        data=PaymentSettlementReceivedData(
            amount="1000.00",
            currency="EGP",
            transaction_date="2026-06-20",
            document_ref="PMB-77",
            provider_normalized_code="paymob",
            external_system="shopify",
            payout_batch_id="PMB-77",
            gross_amount="1000.00",
            fees="25.00",
            net_amount="975.00",
            uncollected_amount="0",
            payment_method="card",
            payout_date="2026-06-20",
            line_items=[
                {"order_id": "1001", "gross": "600.00", "fee": "15.00", "net": "585.00", "status": "settled"},
                {"order_id": "1002", "gross": "400.00", "fee": "10.00", "net": "390.00", "status": "settled"},
            ],
        ),
    )
    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PROVIDER_PAYOUT_RECONCILED,
        aggregate_type="ProviderPayout",
        aggregate_id="paymob:PMB-77",
        idempotency_key="provider_payout.reconciled:test-paymob-1",
        data=ProviderPayoutReconciledData(
            provider="paymob",
            payout_batch_id="PMB-77",
            reconciled_at=timezone.now().isoformat(),
            source="auto_reconcile",
            outcome="verified",
            matched_count=2,
            unmatched_count=0,
            total_count=2,
            verified_count=2,
            currency="EGP",
            line_verdicts=[
                {
                    "line_index": 0,
                    "verified": True,
                    "match_kind": "charge",
                    "matched_ref": "1001",
                    "matched_ref_type": "charge",
                    "provider_line_ref": "",
                },
                {
                    "line_index": 1,
                    "verified": True,
                    "match_kind": "charge",
                    "matched_ref": "1002",
                    "matched_ref_type": "charge",
                    "provider_line_ref": "",
                },
            ],
        ),
    )

    PaymentsProjection().process_pending(company)

    from platform_connectors.models import ProviderPayoutLine

    lines = ProviderPayoutLine.objects.filter(company=company, provider="paymob", payout_batch_id="PMB-77").order_by(
        "line_index"
    )
    assert [line.verified for line in lines] == [True, True]
    assert [line.matched_ref for line in lines] == ["1001", "1002"]
    header = ProviderPayout.objects.get(company=company, provider="paymob", payout_batch_id="PMB-77")
    assert header.reconciliation_outcome == "verified"
    assert header.verified_line_count == 2


def test_reconciled_for_missing_lines_is_warn_not_block(db, company):
    """A verdict for a payout the settlement handler never materialized must
    not stop the stream (no defer, no crash) — warn + no-op."""
    from events.emitter import emit_event_no_actor
    from platform_connectors.event_types import ProviderPayoutReconciledData
    from platform_connectors.projections import PaymentsProjection

    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PROVIDER_PAYOUT_RECONCILED,
        aggregate_type="ProviderPayout",
        aggregate_id="stripe:po_ghost",
        idempotency_key="provider_payout.reconciled:test-ghost-1",
        data=ProviderPayoutReconciledData(
            provider="stripe",
            payout_batch_id="po_ghost",
            reconciled_at=timezone.now().isoformat(),
            source="auto_reconcile",
            outcome="discrepancy",
            matched_count=0,
            unmatched_count=1,
            total_count=1,
            verified_count=0,
            currency="USD",
            line_verdicts=[
                {
                    "line_index": 0,
                    "verified": False,
                    "match_kind": "none",
                    "matched_ref": "",
                    "matched_ref_type": "",
                    "provider_line_ref": "txn_ghost",
                }
            ],
        ),
    )

    proj = PaymentsProjection()
    proj.process_pending(company)
    assert proj.get_lag(company) == 0  # consumed, not stuck
