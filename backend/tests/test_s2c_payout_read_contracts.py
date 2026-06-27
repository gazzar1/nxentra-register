# backend/tests/test_s2c_payout_read_contracts.py
"""ADR-0002 Phase 2 PR-C0 — characterization tests for the Stripe payout READ
contracts that a future canonical cutover (PR-C1..C4) must preserve.

These pin the CURRENT (legacy `StripePayout`/`StripePayoutTransaction`-backed)
behavior of the two highest-stakes read seams:
  * `reconcile_payout()` — the reconciliation dataclass the Stripe recon views return;
  * `bank_connector.matching._get_stripe_payouts()` — the payout HEADER the bank
    reconciliation/match engine reads (and whose `journal_entry_id` it writes back).

They are GREEN today. They are a safety net: when PR-C switches these reads to the
canonical `ProviderPayout`/`ProviderPayoutLine` model, any divergence in the output
(amounts, counts, status, settlement id, journal_entry_id) trips here — forcing
real parity before the legacy direct writes can be removed. Do not "fix" these by
weakening assertions; fix the canonical model to match.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest


class _FakeClient:
    def __init__(self, payouts, txns_by_payout):
        self._payouts = payouts
        self._txns = txns_by_payout

    def list_payouts(self, arrival_date_gte=None, status=None):
        return self._payouts

    def list_balance_transactions(self, payout_id):
        return self._txns.get(payout_id, [])


@pytest.fixture
def synced_payout(db, company, monkeypatch):
    """Pull one payout (net 141.15 + fees 8.85 = gross 150.00) with two charges,
    so the legacy StripePayout header + two StripePayoutTransaction rows exist."""
    from stripe_connector import sync as sync_mod
    from stripe_connector.models import StripeAccount
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
    return account


def test_reconcile_payout_contract(db, company, synced_payout):
    """Pins reconcile_payout()'s financial contract: header amounts, line count,
    and zero gross/fee/net variance (lines sum to the header)."""
    from stripe_connector.models import StripePayout
    from stripe_connector.reconciliation import reconcile_payout

    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    recon = reconcile_payout(company, payout)

    assert recon.stripe_payout_id == "po_1"
    assert (recon.gross_amount, recon.fees, recon.net_amount) == (
        Decimal("150.00"),
        Decimal("8.85"),
        Decimal("141.15"),
    )
    assert recon.currency == "USD"
    assert recon.total_transactions == 2
    # Lines reconcile to the header — the variance the recon UI surfaces.
    assert (recon.gross_variance, recon.fee_variance, recon.net_variance) == (
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
    )
    # No local StripeCharge rows (those arrive via webhooks) → nothing auto-matches.
    assert recon.matched_transactions == 0
    assert recon.unmatched_transactions == 2
    assert recon.status in {"unverified", "discrepancy", "verified", "no_transactions"}


def test_bank_match_stripe_payout_header_contract(db, company, synced_payout):
    """Pins the payout HEADER the bank reconciliation engine reads. The canonical
    cutover MUST keep answering stripe_status + journal_entry_id (neither is in the
    settlement event today — see the PR-C cutover map)."""
    from bank_connector.matching import _get_stripe_payouts

    rows = _get_stripe_payouts(company)
    assert len(rows) == 1
    row = rows[0]
    assert row["platform"] == "stripe"
    assert row["payout_id"] == "po_1"
    assert (row["gross_amount"], row["fees"], row["net_amount"]) == (
        Decimal("150.00"),
        Decimal("8.85"),
        Decimal("141.15"),
    )
    assert row["currency"] == "USD"
    # stripe_status comes from the Stripe payout's own status; it is NOT carried on
    # PAYMENT_SETTLEMENT_RECEIVED today — the canonical header must source it (PR-C1
    # enriches the emit). Pinned here so the gap can't be silently shipped.
    assert row["status"] == "paid"
    # journal_entry_id is written back by the bank-match JE flow; None until matched.
    assert row["journal_entry_id"] is None
