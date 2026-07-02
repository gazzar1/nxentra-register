# backend/tests/test_s1e_stripe_manual_payout.py
"""A merchant's MANUAL payout (dashboard "Pay out" / POST /v1/payouts) must
never break the pull sync.

Stripe refuses to itemize manual payouts — ``GET /v1/balance_transactions
?payout=…`` returns 400 "Balance transaction history can only be filtered on
automatic transfers, not manual" — so their fees are underivable. Surfaced
live 2026-07-02: the sandbox's first manual payout crashed sync_payouts, and
because the payout stays inside the 7-day arrival rescan window, every
subsequent sync for the account would have crashed for a week.

Policy pinned here:
  * manual payouts (``automatic: false``) SKIP without an API call — never
    emit a settlement with understated fees;
  * a per-payout balance-txn fetch failure skips THAT payout only and the
    window continues (per-payout isolation);
  * StripeAccessDenied keeps its account-level "unavailable" semantics.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest


def _arrival(day: int) -> int:
    return int(datetime(2026, 6, day, tzinfo=UTC).timestamp())


def _auto_payout(pid: str, day: int = 20) -> dict:
    return {"id": pid, "amount": 14115, "currency": "usd", "arrival_date": _arrival(day), "status": "paid"}


def _manual_payout(pid: str, day: int = 21) -> dict:
    return {
        "id": pid,
        "amount": 9680,
        "currency": "usd",
        "arrival_date": _arrival(day),
        "status": "paid",
        "automatic": False,
    }


def _charge_txns(prefix: str) -> list[dict]:
    return [
        {"id": f"txn_{prefix}_1", "type": "charge", "amount": 10000, "fee": 590, "source": f"ch_{prefix}_1"},
        {"id": f"txn_{prefix}_2", "type": "charge", "amount": 5000, "fee": 295, "source": f"ch_{prefix}_2"},
        {"id": f"txn_{prefix}_p", "type": "payout", "amount": -14115, "fee": 0, "source": prefix},
    ]


class _FakeClient:
    """list_balance_transactions records calls and can raise per payout id."""

    def __init__(self, payouts, txns_by_payout, bt_errors=None):
        self._payouts = payouts
        self._txns = txns_by_payout
        self._bt_errors = bt_errors or {}
        self.bt_calls = []

    def list_payouts(self, arrival_date_gte=None, status=None):
        return self._payouts

    def list_balance_transactions(self, payout_id):
        self.bt_calls.append(payout_id)
        if payout_id in self._bt_errors:
            raise self._bt_errors[payout_id]
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


def _run_sync(account, client, monkeypatch):
    from stripe_connector import sync as sync_mod

    monkeypatch.setattr(sync_mod, "_stripe_client", lambda acct: client)
    return sync_mod.sync_payouts(account)


def test_manual_payout_skipped_without_bt_call(db, company, stripe_account, monkeypatch):
    """automatic=False → skip BEFORE hitting the balance-transactions API; no
    event, no read-models, and the sync still completes (last_sync_at set)."""
    from events.models import BusinessEvent
    from events.types import EventTypes
    from stripe_connector.models import StripePayout

    client = _FakeClient([_manual_payout("po_manual")], {})
    result = _run_sync(stripe_account, client, monkeypatch)

    assert result == {"status": "ok", "created": 0, "skipped": 1}
    assert client.bt_calls == []  # never asked Stripe to itemize it
    assert StripePayout.objects.filter(company=company).count() == 0
    assert not BusinessEvent.objects.filter(company=company, event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED).exists()
    stripe_account.refresh_from_db()
    assert stripe_account.last_sync_at is not None


def test_manual_payout_does_not_block_automatic_payout(db, company, stripe_account, monkeypatch):
    """THE regression: a manual payout in the window must not abort the sync —
    the automatic payout behind it still ingests with real fees."""
    from stripe_connector.models import StripePayout

    client = _FakeClient(
        [_manual_payout("po_manual"), _auto_payout("po_auto")],
        {"po_auto": _charge_txns("po_auto")},
    )
    result = _run_sync(stripe_account, client, monkeypatch)

    assert result == {"status": "ok", "created": 1, "skipped": 1}
    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_auto")
    assert (payout.gross_amount, payout.fees, payout.net_amount) == (
        Decimal("150.00"),
        Decimal("8.85"),
        Decimal("141.15"),
    )
    assert not StripePayout.objects.filter(company=company, stripe_payout_id="po_manual").exists()


def test_bt_fetch_error_skips_payout_and_continues(db, company, stripe_account, monkeypatch):
    """A per-payout StripeApiError (e.g. the manual-payout 400 arriving without
    the `automatic` flag, or a transient failure) skips that payout only."""
    from stripe_connector.api_client import StripeApiError
    from stripe_connector.models import StripePayout

    broken = _auto_payout("po_broken", day=19)
    client = _FakeClient(
        [broken, _auto_payout("po_ok")],
        {"po_ok": _charge_txns("po_ok")},
        bt_errors={
            "po_broken": StripeApiError(
                "Balance transaction history can only be filtered on automatic transfers, not manual."
            )
        },
    )
    result = _run_sync(stripe_account, client, monkeypatch)

    assert result == {"status": "ok", "created": 1, "skipped": 1}
    assert StripePayout.objects.filter(company=company, stripe_payout_id="po_ok").exists()
    assert not StripePayout.objects.filter(company=company, stripe_payout_id="po_broken").exists()


def test_access_denied_on_bt_fetch_keeps_account_level_semantics(db, company, stripe_account, monkeypatch):
    """StripeAccessDenied is an account/key problem, not a payout problem —
    the pre-existing 'unavailable' early-return must survive the new catch."""
    from stripe_connector.api_client import StripeAccessDenied

    client = _FakeClient(
        [_auto_payout("po_1")],
        {},
        bt_errors={"po_1": StripeAccessDenied("key lacks balance read scope")},
    )
    result = _run_sync(stripe_account, client, monkeypatch)

    assert result["status"] == "unavailable"
    stripe_account.refresh_from_db()
    assert "access denied" in stripe_account.error_message.lower()
