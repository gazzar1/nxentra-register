# backend/tests/test_s2a_payments_projection.py
"""ADR-0002 Phase 2 PR-A — PaymentsProjection + ProviderPayoutLine.

A NEW, independent consumer of PAYMENT_SETTLEMENT_RECEIVED materializes one
ProviderPayoutLine per element of the event's line_items[] (the per-payout
breakdown). It is the sole writer of that read-model and runs alongside the
existing PaymentSettlementProjection (which keeps posting the drain JE).

Dual-write phase: stripe_connector.sync._upsert_read_models still direct-writes
the legacy StripePayout/StripePayoutTransaction caches. The CHARACTERIZATION
assertion is that the projection's lines reconcile, line-for-line, with those
legacy rows — so PR-C can later flip the projection to sole source of truth and
delete the direct writes with confidence.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest


class _FakeClient:
    """Minimal stand-in for StripeApiClient — only the two methods sync_payouts calls."""

    def __init__(self, payouts, txns_by_payout):
        self._payouts = payouts
        self._txns = txns_by_payout

    def list_payouts(self, arrival_date_gte=None, status=None):
        return self._payouts

    def list_balance_transactions(self, payout_id):
        return self._txns.get(payout_id, [])


@pytest.fixture
def stripe_account(db, company):
    from stripe_connector.models import StripeAccount
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)
    return StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        status=StripeAccount.Status.ACTIVE,
        credential_ref="rk_test_dummy",
    )


def _run_sync(stripe_account, monkeypatch):
    """Pull one payout with two constituent charges (net 141.15 + fees 8.85 =
    gross 150.00) so gross/net/fee are internally consistent and replayable."""
    from stripe_connector import sync as sync_mod

    arrival = int(datetime(2026, 6, 20, tzinfo=UTC).timestamp())
    payout = {"id": "po_1", "amount": 14115, "currency": "usd", "arrival_date": arrival, "status": "paid"}
    txns = [
        {"id": "txn_1", "type": "charge", "amount": 10000, "fee": 590, "source": "ch_1"},
        {"id": "txn_2", "type": "charge", "amount": 5000, "fee": 295, "source": "ch_2"},
        {"id": "txn_p", "type": "payout", "amount": -14115, "fee": 0, "source": "po_1"},
    ]
    monkeypatch.setattr(sync_mod, "_stripe_client", lambda acct: _FakeClient([payout], {"po_1": txns}))
    return sync_mod.sync_payouts(stripe_account)


def test_payments_projection_materializes_lines_matching_legacy(db, company, stripe_account, monkeypatch):
    from platform_connectors.models import ProviderPayoutLine
    from platform_connectors.projections import PaymentsProjection
    from stripe_connector.models import StripePayoutTransaction

    result = _run_sync(stripe_account, monkeypatch)
    assert result["status"] == "ok" and result["created"] == 1

    # The new projection is a SECOND consumer of the settlement event.
    processed = PaymentsProjection().process_pending(company)
    assert processed >= 1

    lines = ProviderPayoutLine.objects.filter(company=company, provider="stripe", payout_batch_id="po_1")
    # One line per constituent balance-txn (the type=="payout" txn is excluded).
    assert lines.count() == 2
    assert {line.currency for line in lines} == {"USD"}
    # line_index is dense + ordered.
    assert sorted(line.line_index for line in lines) == [0, 1]

    # Characterization: the projection's lines reconcile, by source, with the
    # legacy direct-written StripePayoutTransaction rows for the same payout.
    projected = {(line.source_id, line.gross_amount, line.fee, line.net_amount) for line in lines}
    legacy = {
        (txn.source_id, txn.amount, txn.fee, txn.net)
        for txn in StripePayoutTransaction.objects.filter(company=company, payout__stripe_payout_id="po_1")
    }
    assert projected == legacy
    assert projected == {
        ("ch_1", Decimal("100.00"), Decimal("5.90"), Decimal("94.10")),
        ("ch_2", Decimal("50.00"), Decimal("2.95"), Decimal("47.05")),
    }


def test_payments_projection_is_idempotent_on_rebuild(db, company, stripe_account, monkeypatch):
    from platform_connectors.models import ProviderPayoutLine
    from platform_connectors.projections import PaymentsProjection

    _run_sync(stripe_account, monkeypatch)
    PaymentsProjection().process_pending(company)
    assert ProviderPayoutLine.objects.filter(company=company, payout_batch_id="po_1").count() == 2

    # Rebuild clears + replays from the (immutable) event — deterministic ids
    # mean the same two rows reappear, never duplicates.
    PaymentsProjection().rebuild(company)
    assert ProviderPayoutLine.objects.filter(company=company, payout_batch_id="po_1").count() == 2


def test_payments_projection_independent_of_settlement_je(db, company, stripe_account, owner_membership, monkeypatch):
    """Both consumers process the same event independently: the JE projection
    posts the drain entry AND the payments projection materializes the lines."""
    from accounting.models import JournalEntry
    from accounting.payment_settlement_projection import PaymentSettlementProjection
    from platform_connectors.models import ProviderPayoutLine
    from platform_connectors.projections import PaymentsProjection

    _run_sync(stripe_account, monkeypatch)

    PaymentSettlementProjection().process_pending(company)
    PaymentsProjection().process_pending(company)

    assert JournalEntry.objects.filter(company=company, source_module="payment_settlement").exists()
    assert ProviderPayoutLine.objects.filter(company=company, payout_batch_id="po_1").count() == 2
