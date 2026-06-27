# backend/tests/test_s2d_provider_payout_header.py
"""ADR-0002 Phase 2 PR-C1 — canonical ProviderPayout HEADER read-model.

PaymentsProjection now materializes a provider-agnostic payout HEADER (alongside
the lines from PR-A/B) from the settlement event, enriched with provider-neutral
header fields (provider_status / provider_account_reference / provider_account_name).
This is the additive "expand" step: the canonical model becomes complete enough to
LATER replace the legacy StripePayout header — reads are NOT switched here.

PARITY is the contract: the projection-built ProviderPayout must match the legacy
StripePayout header that _upsert_read_models still direct-writes (dual-write). And
the header must be REPLAYABLE (event → rebuild → header restored).
"""

from datetime import UTC, date, datetime
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
def synced(db, company, monkeypatch):
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


def test_canonical_header_matches_legacy_stripe_payout(db, company, synced):
    from platform_connectors.models import ProviderPayout
    from platform_connectors.projections import PaymentsProjection
    from stripe_connector.models import StripePayout

    PaymentsProjection().process_pending(company)

    legacy = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    header = ProviderPayout.objects.get(company=company, provider="stripe", payout_batch_id="po_1")

    # Financial parity with the legacy header (what the recon/bank-match reads use).
    assert header.gross_amount == legacy.gross_amount == Decimal("150.00")
    assert header.fees == legacy.fees == Decimal("8.85")
    assert header.net_amount == legacy.net_amount == Decimal("141.15")
    assert header.currency == legacy.currency == "USD"
    assert header.payout_date == legacy.payout_date == date(2026, 6, 20)
    # provider_status maps from the provider's own status (legacy stripe_status).
    assert header.provider_status == legacy.stripe_status == "paid"


def test_header_carries_provider_neutral_account_reference(db, company, synced):
    from platform_connectors.models import ProviderPayout
    from platform_connectors.projections import PaymentsProjection

    PaymentsProjection().process_pending(company)
    header = ProviderPayout.objects.get(company=company, provider="stripe", payout_batch_id="po_1")
    # Provider-neutral names (NOT stripe_*) so the canonical event/model stays
    # provider-agnostic; the Stripe adapter maps acct id + display name into them.
    assert header.provider_account_reference == "acct_test"
    assert header.provider_account_name == "Acme Stripe"


def test_header_is_replayable_and_idempotent(db, company, synced):
    from platform_connectors.models import ProviderPayout
    from platform_connectors.projections import PaymentsProjection

    PaymentsProjection().process_pending(company)
    assert ProviderPayout.objects.filter(company=company, payout_batch_id="po_1").count() == 1

    # Rebuild from the (immutable) event — deterministic id → same single header,
    # fully restored, never duplicated.
    PaymentsProjection().rebuild(company)
    headers = ProviderPayout.objects.filter(company=company, payout_batch_id="po_1")
    assert headers.count() == 1
    assert headers.first().provider_status == "paid"
    assert headers.first().net_amount == Decimal("141.15")
