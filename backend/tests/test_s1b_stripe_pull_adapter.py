# tests/test_s1b_stripe_pull_adapter.py
"""S1 PR-B — Stripe read-only pull adapter.

Covers the fee-derivation logic (the headline fix: payout.paid reports fees=0,
so fees are DERIVED from Balance Transactions), the platform_stripe seed +
SettlementProvider, and the end-to-end pull: mocked client → derived fees →
PAYMENT_SETTLEMENT_RECEIVED → PaymentSettlementProjection posts a settlement JE
WITH a real fee leg. Also pins the webhook payout.paid demotion (pull is the
sole settlement emitter) and idempotency.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from stripe_connector.normalize import derive_payout_breakdown

# ─────────────────────────────────────────────────────────────────────
# fee derivation (pure)
# ─────────────────────────────────────────────────────────────────────


def test_derive_grounds_net_in_payout_and_derives_fees():
    payout = {"id": "po_1", "amount": 9410, "currency": "usd", "arrival_date": 1_750_000_000, "status": "paid"}
    txns = [
        {"id": "txn_1", "type": "charge", "amount": 10000, "fee": 590, "source": "ch_1"},
        {"id": "txn_p", "type": "payout", "amount": -9410, "fee": 0, "source": "po_1"},  # excluded
    ]
    b = derive_payout_breakdown(payout, txns)
    assert b["net"] == Decimal("94.10")  # grounded in payout.amount
    assert b["fees"] == Decimal("5.90")  # summed from balance txn fees (NOT 0)
    assert b["gross"] == Decimal("100.00")  # net + fees → balance guard holds
    assert b["currency"] == "USD"
    assert len(b["line_items"]) == 1  # the payout txn is excluded
    assert b["line_items"][0]["fee"] == "5.90"


def test_derive_handles_refund_in_payout():
    payout = {"id": "po_2", "amount": 4700, "currency": "usd", "arrival_date": 1_750_000_000}
    txns = [
        {"id": "t1", "type": "charge", "amount": 10000, "fee": 600, "source": "ch_1"},
        {"id": "t2", "type": "refund", "amount": -5000, "fee": 0, "source": "re_1"},
        {"id": "tp", "type": "payout", "amount": -4700, "fee": 0},
    ]
    b = derive_payout_breakdown(payout, txns)
    assert b["net"] == Decimal("47.00")
    assert b["fees"] == Decimal("6.00")
    assert b["gross"] == Decimal("53.00")  # net + fees; balance guard: 47 + 6 == 53
    assert len(b["line_items"]) == 2


def test_derive_balance_guard_invariant_holds():
    # net + fees + uncollected(0) == gross must hold for ANY input (the projection
    # silently refuses the JE otherwise).
    payout = {"id": "po", "amount": 12345, "currency": "eur", "arrival_date": 1_750_000_000}
    txns = [{"id": "t", "type": "charge", "amount": 13000, "fee": 655, "source": "ch"}]
    b = derive_payout_breakdown(payout, txns)
    assert b["net"] + b["fees"] == b["gross"]


# ─────────────────────────────────────────────────────────────────────
# seed
# ─────────────────────────────────────────────────────────────────────


def test_setup_stripe_platform_seeds_mappings_provider_and_distinct_accounts(db, company):
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account
    from accounting.settlement_provider import SettlementProvider
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)

    mapping = ModuleAccountMapping.get_mapping(company, "platform_stripe")
    # The settlement-drain roles the projection requires.
    for role in ("EXPECTED_BANK_DEPOSIT", "SALES_RETURNS", "PAYMENT_PROCESSING_FEES", "STRIPE_CLEARING"):
        assert mapping.get(role) is not None, role
    # Per-provider isolation: Stripe clearing + EBD are distinct codes, NOT Shopify's.
    assert Account.objects.get(company=company, code="11510").id == mapping["STRIPE_CLEARING"].id
    assert Account.objects.get(company=company, code="11610").id == mapping["EXPECTED_BANK_DEPOSIT"].id
    # Gateway fees land on a DEDICATED account (53100), never a generic
    # admin-expense code (53000 "Office & General") — fees are a first-class
    # reconciliation metric and must be reportable on their own line.
    assert Account.objects.get(company=company, code="53100").id == mapping["PAYMENT_PROCESSING_FEES"].id

    provider = SettlementProvider.objects.get(company=company, external_system="stripe", normalized_code="stripe")
    assert provider.provider_type == SettlementProvider.ProviderType.GATEWAY
    assert provider.is_active and not provider.needs_review
    assert provider.posting_profile.control_account_id == mapping["STRIPE_CLEARING"].id
    assert provider.dimension_value_id is not None


# ─────────────────────────────────────────────────────────────────────
# webhook demotion
# ─────────────────────────────────────────────────────────────────────


def test_payout_paid_demoted_pull_is_sole_settlement_emitter():
    from stripe_connector.connector import STRIPE_TOPIC_MAP, StripeConnector

    assert "payout.paid" not in STRIPE_TOPIC_MAP
    assert StripeConnector().map_topic_to_canonical("payout.paid") is None


# ─────────────────────────────────────────────────────────────────────
# end-to-end pull → derived fees → settlement JE with a real fee leg
# ─────────────────────────────────────────────────────────────────────


class _FakeClient:
    def __init__(self, payouts, txns_by_payout):
        self._payouts = payouts
        self._txns = txns_by_payout

    def list_payouts(self, **kw):
        return self._payouts

    def list_balance_transactions(self, payout_id, **kw):
        return self._txns.get(payout_id, [])


@pytest.fixture
def stripe_account(db, company, owner_membership):
    # owner_membership → the projection resolves a system actor via the company's
    # active OWNER when it posts the settlement JE.
    from stripe_connector.models import StripeAccount
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)
    return StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        status=StripeAccount.Status.ACTIVE,
        credential_ref="rk_test_dummy",
    )


def test_pull_emits_settlement_and_projection_posts_real_fee_leg(db, company, stripe_account, monkeypatch):
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account, JournalEntry, JournalLine
    from accounting.payment_settlement_projection import PaymentSettlementProjection
    from platform_connectors.models import ProviderRawObject
    from stripe_connector import sync as sync_mod
    from stripe_connector.models import StripePayout

    arrival = int(datetime(2026, 6, 20, tzinfo=UTC).timestamp())
    payout = {"id": "po_1", "amount": 9410, "currency": "usd", "arrival_date": arrival, "status": "paid"}
    txns = [
        {"id": "txn_1", "type": "charge", "amount": 10000, "fee": 590, "source": "ch_1"},
        {"id": "txn_p", "type": "payout", "amount": -9410, "fee": 0, "source": "po_1"},
    ]
    monkeypatch.setattr(sync_mod, "_stripe_client", lambda acct: _FakeClient([payout], {"po_1": txns}))

    result = sync_mod.sync_payouts(stripe_account)
    assert result["status"] == "ok" and result["created"] == 1

    # read-model carries REAL derived fees (not the old fees=0)
    sp = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    assert (sp.gross_amount, sp.fees, sp.net_amount) == (Decimal("100.00"), Decimal("5.90"), Decimal("94.10"))

    # raw provenance cache written for payout + balance txn
    assert ProviderRawObject.objects.filter(company=company, object_type="payout", external_id="po_1").exists()
    assert ProviderRawObject.objects.filter(
        company=company, object_type="balance_transaction", external_id="txn_1"
    ).exists()

    # cursor advanced
    stripe_account.refresh_from_db()
    assert stripe_account.last_sync_at is not None

    # the projection posts the settlement JE WITH a real fee leg
    PaymentSettlementProjection().process_pending(company)
    je = JournalEntry.objects.get(company=company, source_module="payment_settlement")
    ebd = Account.objects.get(company=company, code="11610")
    clearing = Account.objects.get(company=company, code="11510")
    fees_acct = ModuleAccountMapping.get_account(company, "platform_stripe", "PAYMENT_PROCESSING_FEES")
    lines = {jl.account_id: (jl.debit, jl.credit) for jl in JournalLine.objects.filter(entry=je)}
    assert lines[ebd.id] == (Decimal("94.10"), Decimal("0.00"))  # DR Expected Bank Deposit (net)
    assert lines[fees_acct.id] == (Decimal("5.90"), Decimal("0.00"))  # DR fees — the bug fix
    assert lines[clearing.id] == (Decimal("0.00"), Decimal("100.00"))  # CR Stripe clearing (gross)


def test_pull_is_idempotent(db, company, stripe_account, monkeypatch):
    from stripe_connector import sync as sync_mod
    from stripe_connector.models import StripePayout

    arrival = int(datetime(2026, 6, 20, tzinfo=UTC).timestamp())
    payout = {"id": "po_1", "amount": 9410, "currency": "usd", "arrival_date": arrival, "status": "paid"}
    txns = [{"id": "txn_1", "type": "charge", "amount": 10000, "fee": 590, "source": "ch_1"}]
    monkeypatch.setattr(sync_mod, "_stripe_client", lambda acct: _FakeClient([payout], {"po_1": txns}))

    sync_mod.sync_payouts(stripe_account)
    sync_mod.sync_payouts(stripe_account)  # re-run

    assert StripePayout.objects.filter(company=company, stripe_payout_id="po_1").count() == 1
    from events.models import BusinessEvent
    from events.types import EventTypes

    settlement_events = BusinessEvent.objects.filter(company=company, event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED)
    assert settlement_events.count() == 1  # idempotency_key dedup


def test_pull_unavailable_without_credential(db, company, monkeypatch):
    from stripe_connector import sync as sync_mod
    from stripe_connector.models import StripeAccount
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)
    account = StripeAccount.objects.create(
        company=company, stripe_account_id="acct_nocred", status=StripeAccount.Status.ACTIVE, credential_ref=""
    )
    result = sync_mod.sync_payouts(account)
    assert result["status"] == "unavailable"
