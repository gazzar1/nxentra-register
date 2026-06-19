# tests/test_a135_relevance_aware_lag.py
"""
A135 (2026-06-19): projection lag is relevance-aware.

A bookmark sits at the last event its consumer *handled*. The old
get_projection_lag_metrics counted the WHOLE company stream after the
bookmark, so events of types a projection never consumes inflated its lag —
a fully-caught-up projection reported "N behind" (the b74379 phantom-lag
finding). The metric now counts only each registered projection's consumed
types; unknown consumers fall back to the coarse whole-stream count.
"""

from uuid import uuid4

import pytest

from events.metrics import get_projection_lag_metrics
from events.models import BusinessEvent, CompanyEventCounter, EventBookmark
from shopify_connector.projections import PROJECTION_NAME, ShopifyAccountingHandler


def _event(company, event_type):
    counter, _ = CompanyEventCounter.objects.get_or_create(company=company)
    counter.last_sequence += 1
    counter.save()
    return BusinessEvent.objects.create(
        company=company,
        event_type=event_type,
        aggregate_type="TestAggregate",
        aggregate_id=str(uuid4()),
        company_sequence=counter.last_sequence,
        idempotency_key=f"{event_type}:{uuid4()}",
        data={},
    )


def _metric_for(metrics, consumer_name, company):
    return next(m for m in metrics if m["consumer_name"] == consumer_name and m["company_id"] == str(company.public_id))


@pytest.mark.django_db
def test_lag_counts_only_consumed_event_types(company):
    """shopify_accounting consumes shopify.* events. A bookmark parked at a
    handled event, followed by a flood of UNHANDLED events, must read lag = 0
    (no phantom) — and real handled backlog must still be counted."""
    handled_type = "shopify.order_paid"  # in ShopifyAccountingHandler.consumes
    assert handled_type in ShopifyAccountingHandler().consumes

    # Bookmark parked at the last handled event (seq 1).
    marker = _event(company, handled_type)
    EventBookmark.objects.create(consumer_name=PROJECTION_NAME, company=company, last_event=marker)

    # ...then 5 UNHANDLED events stream in after it (the phantom-lag fuel).
    for _ in range(5):
        _event(company, "test.unhandled_event")

    metrics = get_projection_lag_metrics()
    m = _metric_for(metrics, PROJECTION_NAME, company)

    assert m["relevance_aware"] is True
    assert m["lag"] == 0, f"unhandled events must not count as lag; got {m['lag']}"

    # Now two REAL handled events arrive after the bookmark → genuine backlog.
    _event(company, handled_type)
    _event(company, handled_type)

    m = _metric_for(get_projection_lag_metrics(), PROJECTION_NAME, company)
    assert m["lag"] == 2, f"handled backlog must be counted; got {m['lag']}"


@pytest.mark.django_db
def test_unknown_consumer_falls_back_to_whole_stream(company):
    """A bookmark whose consumer_name isn't a registered projection can't know
    its handled types, so it keeps the coarse whole-stream count."""
    marker = _event(company, "shopify.order_paid")
    EventBookmark.objects.create(consumer_name="some_legacy_consumer", company=company, last_event=marker)

    for _ in range(3):
        _event(company, "test.unhandled_event")

    m = _metric_for(get_projection_lag_metrics(), "some_legacy_consumer", company)
    assert m["relevance_aware"] is False
    assert m["lag"] == 3, "unknown consumer counts every event type after the bookmark"
