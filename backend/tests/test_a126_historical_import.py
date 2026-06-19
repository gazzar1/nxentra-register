# tests/test_a126_historical_import.py
"""
A126: install the approved read_all_orders scope + lift the 60-day historical
import cap + chunk large imports by month.

The cap-lift is SCOPE-GATED on ShopifyStore.scopes: only a store that has
actually been granted read_all_orders (i.e. reconnected after `shopify app
deploy`) skips the 59-day clamp. A store still on read_orders alone stays
clamped so it can never hit Shopify's 403 (the A50 regression).

Coverage:
- A read_all_orders store: `all` reaches years back; `from_date` honours an
  old requested date (no clamp).
- A non-read_all_orders store stays clamped (the gate is real).
- _month_window_chunks splits a window into contiguous monthly tasks.
"""

import itertools
from datetime import date, datetime, timedelta

import pytest
from django.utils import timezone as tz

from accounts.commands import _enqueue_shopify_historical_import, _month_window_chunks
from shopify_connector.models import ShopifyStore


def _make_store(company, *, scopes):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="a126-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
        scopes=scopes,
    )


def _capture_calls(monkeypatch):
    calls: list[dict] = []

    def _fake_delay(*, store_id, created_at_min, created_at_max):
        calls.append({"store_id": store_id, "created_at_min": created_at_min, "created_at_max": created_at_max})

    monkeypatch.setattr("shopify_connector.tasks.sync_shopify_store_orders.delay", _fake_delay)
    return calls


def _earliest_min(calls):
    return min(datetime.fromisoformat(c["created_at_min"]) for c in calls)


# ---------------------------------------------------------------------------
# Scope-gated cap lift
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_read_all_orders_store_lifts_cap_for_import_all(company, monkeypatch):
    store = _make_store(company, scopes="read_orders,read_all_orders,read_products")
    calls = _capture_calls(monkeypatch)

    _enqueue_shopify_historical_import(store.company, "all", None)

    assert calls, "expected chunk tasks"
    age = tz.now().replace(tzinfo=None) - _earliest_min(calls)
    assert age > timedelta(days=60), "read_all_orders must lift the 60-day clamp for 'all' mode"
    # 5-year floor → far more than a dozen monthly chunks.
    assert len(calls) >= 12, f"expected many monthly chunks, got {len(calls)}"


@pytest.mark.django_db
def test_read_all_orders_store_honours_old_from_date(company, monkeypatch):
    store = _make_store(company, scopes="read_orders,read_all_orders")
    calls = _capture_calls(monkeypatch)

    requested = date(2024, 1, 1)  # well older than 60 days
    _enqueue_shopify_historical_import(store.company, "from_date", requested)

    assert calls
    assert _earliest_min(calls).date() == requested, (
        "from_date must pass through unclamped when read_all_orders granted"
    )
    age = tz.now().replace(tzinfo=None) - _earliest_min(calls)
    assert age > timedelta(days=60)


@pytest.mark.django_db
def test_store_without_read_all_orders_stays_clamped(company, monkeypatch):
    store = _make_store(company, scopes="read_orders,read_products")  # no read_all_orders
    calls = _capture_calls(monkeypatch)

    _enqueue_shopify_historical_import(store.company, "all", None)

    assert calls
    age = tz.now().replace(tzinfo=None) - _earliest_min(calls)
    assert age <= timedelta(days=60), "without read_all_orders the import must stay clamped (no 403)"


@pytest.mark.django_db
def test_import_skips_closed_period_months(company, monkeypatch):
    """A (closed-period skip): a month in a CLOSED period is not enqueued —
    those orders can't post and already live in the closing balances — while
    other months in range still import."""
    import calendar

    from projections.models import FiscalPeriod
    from projections.write_barrier import projection_writes_allowed

    store = _make_store(company, scopes="read_orders,read_all_orders")

    # Close the period for the month ~3 months back (well inside the 5yr range).
    # update_or_create: the `company` fixture may auto-create periods, so close
    # whichever one covers that month rather than colliding on the unique key.
    old = (date.today() - timedelta(days=90)).replace(day=1)
    closed_month = old.strftime("%Y-%m")
    with projection_writes_allowed():
        FiscalPeriod.objects.update_or_create(
            company=company,
            fiscal_year=old.year,
            period=old.month,
            defaults=dict(
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=old,
                end_date=old.replace(day=calendar.monthrange(old.year, old.month)[1]),
                status=FiscalPeriod.Status.CLOSED,
            ),
        )

    calls = _capture_calls(monkeypatch)
    _enqueue_shopify_historical_import(store.company, "all", None)

    assert calls, "other (open/undefined) months should still enqueue"
    assert all(not c["created_at_min"].startswith(closed_month) for c in calls), (
        f"the closed month {closed_month} must not be imported; enqueued mins: {[c['created_at_min'] for c in calls]}"
    )


# ---------------------------------------------------------------------------
# Month chunking
# ---------------------------------------------------------------------------


def test_month_window_chunks_cover_range_contiguously():
    start = "2026-01-15T00:00:00"
    end = "2026-04-10T00:00:00"

    chunks = _month_window_chunks(start, end)

    assert chunks[0][0] == start, "first chunk starts at the requested start"
    assert chunks[-1][1] == end, "last chunk ends at the requested end"
    # Contiguous: each chunk's max is the next chunk's min — no gaps, no overlap.
    for this_chunk, next_chunk in itertools.pairwise(chunks):
        assert this_chunk[1] == next_chunk[0]
    # Jan(partial) + Feb + Mar + Apr(partial) = 4 windows.
    assert len(chunks) == 4


def test_month_window_chunks_single_window_within_a_month():
    chunks = _month_window_chunks("2026-03-05T00:00:00", "2026-03-20T00:00:00")
    assert chunks == [("2026-03-05T00:00:00", "2026-03-20T00:00:00")]


def test_month_window_chunks_empty_when_start_not_before_end():
    assert _month_window_chunks("2026-04-10T00:00:00", "2026-04-10T00:00:00") == []
    assert _month_window_chunks("2026-05-01T00:00:00", "2026-04-01T00:00:00") == []
