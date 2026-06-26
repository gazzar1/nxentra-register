# tests/test_s1_webhook_trigger_sync.py
"""S1 — the payout.paid webhook TRIGGERS the pull; it never emits/posts.

Hard boundary (ADR-0002): the webhook payout.paid must only enqueue the Stripe
pull/backfill for the connection — it must NOT emit PAYMENT_SETTLEMENT_RECEIVED,
must NOT post journal entries, and must NOT write settlement read-models. The
pull path remains the SOLE settlement emitter. Enqueue is debounced and the pull
is idempotent, so a duplicate webhook cannot cause a sync storm or double-post.
"""

import hashlib
import hmac
import json
import time

import pytest

from stripe_connector.connector import StripeConnector
from stripe_connector.models import StripeAccount, StripePayout


def _payout_paid_payload(payout_id="po_1"):
    return {"type": "payout.paid", "data": {"object": {"id": payout_id, "amount": 5000, "status": "paid"}}}


def _no_ledger_writes(company):
    """Assert nothing touched the ledger / settlement read-models."""
    from accounting.models import JournalEntry
    from events.models import BusinessEvent
    from events.types import EventTypes

    assert not BusinessEvent.objects.filter(company=company, event_type=EventTypes.PLATFORM_PAYOUT_SETTLED).exists()
    assert not BusinessEvent.objects.filter(company=company, event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED).exists()
    assert not StripePayout.objects.filter(company=company).exists()
    assert not JournalEntry.objects.filter(company=company, source_module="payment_settlement").exists()


@pytest.fixture
def active_account(db, company):
    return StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        status=StripeAccount.Status.ACTIVE,
        webhook_secret="whsec_test",
        credential_ref="rk_test_dummy",
    )


# ── connector hook: enqueue only, no ledger writes ────────────────────


def test_on_payout_paid_enqueues_sync_and_writes_nothing(db, company, active_account, monkeypatch):
    calls = []
    monkeypatch.setattr("stripe_connector.tasks.enqueue_account_sync", lambda aid: calls.append(aid))

    StripeConnector().on_unhandled_topic(company=company, topic="payout.paid", payload=_payout_paid_payload())

    assert calls == [active_account.id]  # sync triggered for the connection
    _no_ledger_writes(company)  # but nothing emitted/posted/stored


def test_non_payout_topic_does_not_enqueue(db, company, active_account, monkeypatch):
    calls = []
    monkeypatch.setattr("stripe_connector.tasks.enqueue_account_sync", lambda aid: calls.append(aid))

    StripeConnector().on_unhandled_topic(company=company, topic="charge.refunded", payload={})
    assert calls == []


def test_no_active_account_acknowledges_safely(db, company, monkeypatch):
    # Connection resolution fails (no active account) → no enqueue, no crash.
    StripeAccount.objects.create(
        company=company, stripe_account_id="acct_x", status=StripeAccount.Status.DISCONNECTED, webhook_secret="w"
    )
    calls = []
    monkeypatch.setattr("stripe_connector.tasks.enqueue_account_sync", lambda aid: calls.append(aid))

    # Must not raise.
    StripeConnector().on_unhandled_topic(company=company, topic="payout.paid", payload=_payout_paid_payload())
    assert calls == []


# ── debounce: a burst of webhooks → one enqueue ───────────────────────


def test_enqueue_is_debounced(db, monkeypatch):
    from django.core.cache import cache

    from stripe_connector import tasks

    cache.clear()
    delayed = []
    monkeypatch.setattr(tasks.sync_stripe_account, "delay", lambda aid: delayed.append(aid))

    assert tasks.enqueue_account_sync(4242) is True  # first enqueues
    assert tasks.enqueue_account_sync(4242) is False  # within window → debounced
    assert delayed == [4242]  # only one actual task queued


# ── view wiring: a signed payout.paid webhook triggers sync, posts nothing ──


def _sign(secret: str, body: bytes) -> str:
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_webhook_route_triggers_sync_without_posting(db, company, active_account, client, monkeypatch):
    calls = []
    monkeypatch.setattr("stripe_connector.tasks.enqueue_account_sync", lambda aid: calls.append(aid))

    body = json.dumps(_payout_paid_payload("po_route")).encode()
    resp = client.post(
        "/api/platforms/stripe/webhooks/",
        data=body,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=_sign("whsec_test", body),
    )

    assert resp.status_code == 200
    assert calls == [active_account.id]  # the pull was triggered
    _no_ledger_writes(company)  # and the demoted webhook posted nothing
