# tests/test_a50_historical_import_scope_clamp.py
"""
Regression: the wizard's "Import all historical orders" option used to send
created_at_min=2015-01-01 to Shopify. The `read_orders` scope only grants
access to the last 60 days; anything older returns 403. Sentry surfaced this
during 2026-05-15 App Store reviewer-store setup (event 5d5177e81c9941499b36ad943d312a35).

The fix clamps both `all` and `from_date` modes to a 59-day floor so the
default scope set can never trigger the 403.

A126 (2026-06-19): the clamp is now scope-gated — a store granted only
`read_orders` (no `read_all_orders`) MUST still be clamped. These tests use a
store with no granted scopes, so they keep asserting the clamp. The import now
fans out into monthly chunks, so the helper captures every enqueued task.
(read_all_orders cap-lift behaviour is covered by test_a126_*.)
"""

from datetime import date, datetime, timedelta

import pytest
from django.utils import timezone as tz

from accounts.commands import _enqueue_shopify_historical_import
from shopify_connector.models import ShopifyStore


@pytest.fixture
def active_store(db, company):
    # No `scopes` → read_all_orders NOT granted → clamp applies (A126 gate).
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="a50-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )


def _capture_calls(monkeypatch):
    """Collect every sync_shopify_store_orders.delay(...) call (A126 chunks)."""
    calls: list[dict] = []

    def _fake_delay(*, store_id, created_at_min, created_at_max):
        calls.append({"store_id": store_id, "created_at_min": created_at_min, "created_at_max": created_at_max})

    monkeypatch.setattr("shopify_connector.tasks.sync_shopify_store_orders.delay", _fake_delay)
    return calls


def _earliest_min(calls):
    return min(datetime.fromisoformat(c["created_at_min"]) for c in calls)


def test_import_all_clamps_to_60_day_window(active_store, monkeypatch):
    calls = _capture_calls(monkeypatch)

    _enqueue_shopify_historical_import(active_store.company, "all", None)

    assert calls, "expected at least one chunk task"
    earliest = _earliest_min(calls)
    age = tz.now().replace(tzinfo=None) - earliest
    assert age <= timedelta(days=60), (
        f"earliest created_at_min must be within Shopify's 60-day read_orders window; "
        f"got {earliest.isoformat()} (age={age.days}d)"
    )
    assert all("2015" not in c["created_at_min"] for c in calls), (
        "Regression: 2015-01-01 hardcode is back. read_orders scope rejects anything older than 60 days with 403."
    )


def test_import_from_date_clamps_when_requested_date_is_older_than_60_days(active_store, monkeypatch):
    calls = _capture_calls(monkeypatch)

    ancient = date(2020, 1, 1)
    _enqueue_shopify_historical_import(active_store.company, "from_date", ancient)

    assert calls
    age = tz.now().replace(tzinfo=None) - _earliest_min(calls)
    assert age <= timedelta(days=60)


def test_import_from_date_passes_through_recent_date(active_store, monkeypatch):
    calls = _capture_calls(monkeypatch)

    recent = (tz.now() - timedelta(days=14)).date()
    _enqueue_shopify_historical_import(active_store.company, "from_date", recent)

    assert calls
    # The earliest chunk begins at the requested (within-window) date unchanged.
    assert _earliest_min(calls).date() == recent, (
        f"Dates within the 60-day window should pass through unchanged; requested={recent}, "
        f"got={_earliest_min(calls).date()}"
    )
