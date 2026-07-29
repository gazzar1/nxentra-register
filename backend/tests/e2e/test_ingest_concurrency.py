# tests/e2e/test_ingest_concurrency.py
"""A3-PR2 final concurrency pass (fresh-review P2): the REAL two-connection
race on PostgreSQL.

Two overlapping same-company/same-key `journal_entry.posted` ingest requests:
request B passes the pre-validation idempotency lookup while no event exists,
request A then commits the event, the referenced account is deactivated, and
B's canonical validation fails — B's post-failure recheck must find A's
committed event (cross-connection visibility) and return it idempotently
instead of 422.

Synchronization uses threading.Event barriers — no timing sleeps. The
validator is wrapped ONLY to coordinate the interleaving; every
company-scoped database lookup and the genuine validation run for real.

This file lives under tests/e2e/ so CI runs it on the PostgreSQL service
(the SQLite job ignores tests/e2e/); pytest-django's transaction=True makes
A's commit visible to B's independent connection.
"""

import threading
from datetime import date
from uuid import uuid4

import pytest
from django.db import connections
from django.utils import timezone
from rest_framework.test import APIClient

from accounting.models import Account
from events.models import BusinessEvent
from events.types import EventTypes

pytestmark = [pytest.mark.django_db(transaction=True)]


def _payload(idempotency_key, cash_account, revenue_account):
    return {
        "event_type": EventTypes.JOURNAL_ENTRY_POSTED,
        "aggregate_type": "JournalEntry",
        "aggregate_id": str(uuid4()),
        "idempotency_key": idempotency_key,
        "data": {
            "entry_public_id": str(uuid4()),
            "entry_number": "EXT-RACE-1",
            "date": date.today().isoformat(),
            "memo": "concurrent external journal",
            "kind": "NORMAL",
            "currency": "USD",
            "exchange_rate": "1.0",
            "posted_at": timezone.now().isoformat(),
            "posted_by_id": 0,
            "posted_by_email": "ext@example.com",
            "total_debit": "50.00",
            "total_credit": "50.00",
            "lines": [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "50.00", "credit": "0"},
                {"line_no": 2, "account_public_id": str(revenue_account.public_id), "debit": "0", "credit": "50.00"},
            ],
        },
    }


def test_concurrent_same_key_retry_returns_stored_event(company, cash_account, revenue_account, monkeypatch):
    from events.api_keys import ExternalAPIKey

    _key_obj, raw_key = ExternalAPIKey.create_key(
        company=company,
        name="A3 Race",
        source_system="a3_race",
        allowed_event_types=[EventTypes.JOURNAL_ENTRY_POSTED],
    )
    idempotency_key = f"a3.race:{uuid4()}"
    payload = _payload(idempotency_key, cash_account, revenue_account)

    import accounting.journal_invariant as ji

    real_validate = ji.require_valid_posted_journal
    b_inside_validation = threading.Event()
    a_committed = threading.Event()
    call_no = {"n": 0}
    call_lock = threading.Lock()

    def coordinated(company_arg, data):
        with call_lock:
            call_no["n"] += 1
            mine = call_no["n"]
        if mine == 1:
            # Request B: it already passed the pre-lookup (no event existed).
            # Hold it inside validation until A has committed and the account
            # has been deactivated, then run the REAL validator.
            b_inside_validation.set()
            assert a_committed.wait(timeout=30), "request A never committed"
        return real_validate(company_arg, data)

    monkeypatch.setattr(ji, "require_valid_posted_journal", coordinated)

    b_result = {}

    def run_b():
        try:
            response = APIClient().post(
                "/api/events/ingest/", payload, format="json", HTTP_AUTHORIZATION=f"Api-Key {raw_key}"
            )
            b_result["status"] = response.status_code
            b_result["data"] = response.data
        except Exception as exc:  # surfaced by the main thread's asserts
            b_result["error"] = repr(exc)
        finally:
            for conn in connections.all():
                conn.close()

    thread_b = threading.Thread(target=run_b)
    thread_b.start()
    try:
        assert b_inside_validation.wait(timeout=30), "request B never reached validation"

        # Request A: identical payload, same key — commits while B is held.
        response_a = APIClient().post(
            "/api/events/ingest/", payload, format="json", HTTP_AUTHORIZATION=f"Api-Key {raw_key}"
        )
        assert response_a.status_code == 201, response_a.data

        # Deactivate the account so B's (real) validation genuinely fails
        # with JE_ACCOUNT_INACTIVE — the exact mid-flight state change the
        # finding describes.
        Account.objects.filter(pk=cash_account.pk).update(status=Account.Status.LOCKED)
        a_committed.set()
    finally:
        a_committed.set()  # never leave B hanging
        thread_b.join(timeout=60)

    assert not thread_b.is_alive(), "request B did not finish"
    assert "error" not in b_result, b_result.get("error")
    assert b_result["status"] == 201, b_result.get("data")
    assert b_result["data"]["event_id"] == response_a.data["event_id"]

    # transaction=True re-establishes connections mid-test, so the autouse
    # RLS-bypass GUC may be gone from this connection — re-enter it
    # explicitly for the bookkeeping read (matters on non-superuser roles;
    # CI's service user is a superuser and unaffected).
    from accounts.rls import rls_bypass

    with rls_bypass():
        events = BusinessEvent.objects.filter(company=company, idempotency_key=idempotency_key)
        assert events.count() == 1, "exactly one BusinessEvent for the key"
