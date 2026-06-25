# tests/test_s1_codex_fixes.py
"""S1 Codex-review hardening — the 4 follow-up fixes.

1. migration 0039 preserves a populated stale mapping when the canonical row is blank;
2. the pull cursors on arrival_date (not creation time);
3. the pull skips in_progress / un-itemized payouts so understated fees are never emitted;
4. the connect probe exercises the pull-path scopes (Payouts + Balance Transactions).
"""

import importlib
from datetime import UTC, datetime

import pytest

# ── 1. migration: preserve populated mapping on a blank-canonical collision ──


def test_migration_preserves_populated_mapping_on_blank_canonical(db, company):
    from django.apps import apps as global_apps
    from django.db import connection

    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account
    from projections.write_barrier import projection_writes_allowed

    with projection_writes_allowed():
        acct = Account.objects.projection().create(
            company=company,
            code="11510",
            name="Stripe Clearing",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
    # Stale row holds the merchant's real account; the canonical row is a blank placeholder.
    ModuleAccountMapping.objects.create(
        company=company, module="stripe_connector", role="STRIPE_CLEARING", account=acct
    )
    ModuleAccountMapping.objects.create(company=company, module="platform_stripe", role="STRIPE_CLEARING", account=None)

    mig = importlib.import_module("accounting.migrations.0039_unify_stripe_module_account_mapping_key")
    schema_editor = type("_SE", (), {"connection": connection})()
    mig.unify_key(global_apps, schema_editor)

    # The canonical row inherited the real account; the stale row is gone.
    assert ModuleAccountMapping.get_account(company, "platform_stripe", "STRIPE_CLEARING") == acct
    assert not ModuleAccountMapping.objects.filter(
        company=company, module="stripe_connector", role="STRIPE_CLEARING"
    ).exists()


# ── shared fixtures for the sync fixes ────────────────────────────────


class _CapturingClient:
    def __init__(self, payouts, txns_by_payout):
        self._payouts = payouts
        self._txns = txns_by_payout
        self.list_payouts_kwargs = None

    def list_payouts(self, **kw):
        self.list_payouts_kwargs = kw
        return self._payouts

    def list_balance_transactions(self, payout_id, **kw):
        return self._txns.get(payout_id, [])


@pytest.fixture
def stripe_account(db, company, owner_membership):
    from stripe_connector.models import StripeAccount
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)
    return StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        status=StripeAccount.Status.ACTIVE,
        credential_ref="rk_test_dummy",
    )


def _arrival(y, m, d):
    return int(datetime(y, m, d, tzinfo=UTC).timestamp())


def _settlement_events(company):
    from events.models import BusinessEvent
    from events.types import EventTypes

    return BusinessEvent.objects.filter(company=company, event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED)


# ── 2. cursor on arrival_date, not created ────────────────────────────


def test_sync_cursors_on_arrival_date(db, company, stripe_account, monkeypatch):
    from stripe_connector import sync as sync_mod

    fake = _CapturingClient([], {})
    monkeypatch.setattr(sync_mod, "_stripe_client", lambda acct: fake)
    sync_mod.sync_payouts(stripe_account)
    assert "arrival_date_gte" in fake.list_payouts_kwargs
    assert "created_gte" not in fake.list_payouts_kwargs


def test_sync_cursor_extends_back_to_old_last_sync(db, company, stripe_account, monkeypatch):
    # last_sync_at older than the rescan window → the cursor must reach back to
    # it (catch up after an outage longer than the window), not just now-lookback.
    from datetime import timedelta

    from django.utils import timezone

    from stripe_connector import sync as sync_mod

    old = timezone.now() - timedelta(days=20)
    stripe_account.last_sync_at = old
    stripe_account.save(update_fields=["last_sync_at"])

    fake = _CapturingClient([], {})
    monkeypatch.setattr(sync_mod, "_stripe_client", lambda acct: fake)
    sync_mod.sync_payouts(stripe_account, lookback_hours=168)  # 7-day rescan window

    cutoff = fake.list_payouts_kwargs["arrival_date_gte"]
    # Reaches back to ~last_sync_at (20d ago), well before the 7-day window.
    assert cutoff <= int(old.timestamp())
    assert cutoff < int((timezone.now() - timedelta(days=10)).timestamp())


# ── 3. skip in_progress / un-itemized payouts ─────────────────────────


def test_sync_skips_in_progress_payout(db, company, stripe_account, monkeypatch):
    from stripe_connector import sync as sync_mod
    from stripe_connector.models import StripePayout

    payout = {
        "id": "po_ip",
        "amount": 5000,
        "currency": "usd",
        "arrival_date": _arrival(2026, 6, 24),
        "status": "paid",
        "reconciliation_status": "in_progress",
    }
    fake = _CapturingClient([payout], {"po_ip": [{"id": "t", "type": "charge", "amount": 5300, "fee": 300}]})
    monkeypatch.setattr(sync_mod, "_stripe_client", lambda acct: fake)

    result = sync_mod.sync_payouts(stripe_account)
    assert result["skipped"] == 1 and result["created"] == 0
    assert not StripePayout.objects.filter(company=company, stripe_payout_id="po_ip").exists()
    assert _settlement_events(company).count() == 0


def test_sync_skips_payout_with_no_itemized_transactions(db, company, stripe_account, monkeypatch):
    from stripe_connector import sync as sync_mod

    payout = {
        "id": "po_empty",
        "amount": 5000,
        "currency": "usd",
        "arrival_date": _arrival(2026, 6, 24),
        "status": "paid",
        "reconciliation_status": "not_applicable",
    }
    # Only the payout transaction itself — deriving here would produce fees=0.
    fake = _CapturingClient([payout], {"po_empty": [{"id": "tp", "type": "payout", "amount": -5000, "fee": 0}]})
    monkeypatch.setattr(sync_mod, "_stripe_client", lambda acct: fake)

    result = sync_mod.sync_payouts(stripe_account)
    assert result["skipped"] == 1 and result["created"] == 0
    assert _settlement_events(company).count() == 0


def test_sync_processes_completed_payout(db, company, stripe_account, monkeypatch):
    from stripe_connector import sync as sync_mod

    payout = {
        "id": "po_ok",
        "amount": 9410,
        "currency": "usd",
        "arrival_date": _arrival(2026, 6, 24),
        "status": "paid",
        "reconciliation_status": "completed",
    }
    fake = _CapturingClient(
        [payout], {"po_ok": [{"id": "t1", "type": "charge", "amount": 10000, "fee": 590, "source": "ch_1"}]}
    )
    monkeypatch.setattr(sync_mod, "_stripe_client", lambda acct: fake)

    result = sync_mod.sync_payouts(stripe_account)
    assert result["created"] == 1
    assert _settlement_events(company).count() == 1


# ── 4. connect probes the pull-path scopes ────────────────────────────


def test_connect_rejects_key_missing_pull_scopes(db, company, monkeypatch):
    from stripe_connector.api_client import StripeAccessDenied
    from stripe_connector.commands import connect_stripe_account
    from stripe_connector.models import StripeAccount

    # Account read succeeds, but the pull-scope probe is denied (no Payouts/Balance read).
    monkeypatch.setattr(
        "stripe_connector.api_client.StripeApiClient.retrieve_account",
        lambda self: {"id": "acct_x", "livemode": False},
    )

    def _denied(self):
        raise StripeAccessDenied("payouts read not granted")

    monkeypatch.setattr("stripe_connector.api_client.StripeApiClient.probe", _denied)

    result = connect_stripe_account(company, "rk_live_partialscope")
    assert not result.success
    assert not StripeAccount.objects.filter(company=company).exists()
