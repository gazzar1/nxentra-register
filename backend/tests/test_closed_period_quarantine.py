# tests/test_closed_period_quarantine.py
"""
C (2026-06-19): a Shopify order whose date lands in a CLOSED fiscal period
must NOT stall the projection.

Background: A126 lifted the 60-day import cap, so historical orders can now
land on dates in closed periods. The order-invoice path posts via the command
layer, which REJECTS a closed period → _handle_order_paid raised
ProjectionCommandFailedError → process_pending (stop_on_error=True) hit `break`.
Because history is imported oldest-first, the oldest closed-period order would
head-of-line-stall the ENTIRE shopify_accounting projection — freezing new
orders too.

The fix is a framework primitive, ProjectionTerminalSkip: when retrying can't
help until an operator acts (closed period), the framework records an
operator-visible ProjectionFailureLog (A80) AND advances past the event
instead of stalling. _handle_order_paid pre-checks the period and raises it.

Coverage:
- Framework: ProjectionTerminalSkip advances + surfaces; a plain Exception
  still stalls (the control that proves the bug this fixes).
- Shopify e2e: a closed-period order is quarantined while a newer open-period
  order still posts (no head-of-line stall).
"""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from events.models import BusinessEvent, CompanyEventCounter
from projections.base import BaseProjection
from projections.exceptions import ProjectionTerminalSkip
from projections.models import ProjectionAppliedEvent, ProjectionFailureLog

# shopify_company fixture (company + chart-of-accounts + active store + open period).
from tests.test_system_je_validation import shopify_company  # noqa: F401

# ---------------------------------------------------------------------------
# Framework-level: terminal-skip advances; plain raise stalls (the control)
# ---------------------------------------------------------------------------


class _ToyProjection(BaseProjection):
    """Records the events it successfully handles; raises per event.data.mode."""

    def __init__(self):
        self.processed: list[str] = []

    @property
    def name(self) -> str:
        return "test_toy_quarantine_projection"

    @property
    def consumes(self):
        return ["test.toy_event"]

    def handle(self, event):
        mode = (event.data or {}).get("mode")
        if mode == "skip":
            raise ProjectionTerminalSkip("terminal — closed period", fix_hint="reopen the period")
        if mode == "boom":
            raise ValueError("plain unhandled error")
        self.processed.append(str(event.id))


def _make_toy_event(company, *, mode="ok"):
    counter, _ = CompanyEventCounter.objects.get_or_create(company=company)
    counter.last_sequence += 1
    counter.save()
    return BusinessEvent.objects.create(
        company=company,
        event_type="test.toy_event",
        aggregate_type="TestAggregate",
        aggregate_id=str(uuid4()),
        company_sequence=counter.last_sequence,
        idempotency_key=f"test.toy_event:{uuid4()}",
        data={"mode": mode},
    )


def test_terminal_skip_advances_and_does_not_stall(db, company):
    proj = _ToyProjection()
    e_skip = _make_toy_event(company, mode="skip")  # earlier sequence
    e_ok = _make_toy_event(company, mode="ok")  # later sequence

    proj.process_pending(company)

    # The later event processed despite the earlier terminal-skip → NO stall.
    assert str(e_ok.id) in proj.processed
    assert str(e_skip.id) not in proj.processed
    # The skipped event is surfaced (A80) AND advanced (consumed).
    assert ProjectionFailureLog.objects.filter(company=company, event=e_skip).exists()
    log = ProjectionFailureLog.objects.get(company=company, event=e_skip)
    assert log.category == ProjectionFailureLog.Category.MISSING_CONFIG
    assert log.fix_hint
    assert ProjectionAppliedEvent.objects.filter(company=company, projection_name=proj.name, event=e_skip).exists()


def test_plain_exception_still_stalls_behind_first_failure(db, company):
    """Control: a plain unhandled error keeps the old stop_on_error stall —
    the later event is NOT reached. This is exactly the freeze that
    ProjectionTerminalSkip exists to avoid for closed-period orders."""
    proj = _ToyProjection()
    e_boom = _make_toy_event(company, mode="boom")  # earlier
    e_ok = _make_toy_event(company, mode="ok")  # later

    proj.process_pending(company)

    assert str(e_ok.id) not in proj.processed, "plain raise should head-of-line stall the later event"
    assert not ProjectionAppliedEvent.objects.filter(company=company, projection_name=proj.name, event=e_boom).exists()


# ---------------------------------------------------------------------------
# Shopify e2e: closed-period order quarantined, newer order still posts
# ---------------------------------------------------------------------------


def _make_dated_order_event(company, shopify_order_id, *, transaction_date, store_public_id, amount="100.00"):
    from django.utils import timezone

    counter, _ = CompanyEventCounter.objects.get_or_create(company=company)
    counter.last_sequence += 1
    counter.save()
    return BusinessEvent.objects.create(
        company=company,
        event_type="shopify.order_paid",
        aggregate_type="ShopifyOrder",
        aggregate_id=str(uuid4()),
        company_sequence=counter.last_sequence,
        idempotency_key=f"shopify.order.paid:{shopify_order_id}",
        data={
            "amount": amount,
            "currency": "USD",
            "transaction_date": transaction_date.isoformat(),
            "store_public_id": str(store_public_id),
            "shopify_order_id": str(shopify_order_id),
            "order_number": str(shopify_order_id),
            "order_name": f"#{shopify_order_id}",
            "subtotal": amount,
            "total_tax": "0",
            "total_shipping": "0",
            "gateway": "shopify_payments",
            "line_items": [],
        },
        metadata={"source": "shopify_webhook"},
        occurred_at=timezone.now(),
    )


@pytest.mark.django_db
def test_closed_period_order_quarantined_newer_order_still_posts(shopify_company):  # noqa: F811
    """The A126 freeze scenario: an old order in a CLOSED period is processed
    first (lower sequence). It must be quarantined (failure log, no invoice)
    and the projection must keep going so a newer open-period order still
    posts. Pre-C, the closed-period order raised and stalled the whole stream."""
    import calendar

    from projections.models import FiscalPeriod
    from projections.write_barrier import projection_writes_allowed
    from sales.models import SalesInvoice
    from shopify_connector.models import ShopifyStore
    from shopify_connector.projections import ShopifyAccountingHandler

    store = ShopifyStore.objects.get(company=shopify_company)

    # A CLOSED period ~5 months back, plus an order dated inside it.
    old = date.today() - timedelta(days=150)
    with projection_writes_allowed():
        FiscalPeriod.objects.create(
            company=shopify_company,
            fiscal_year=old.year,
            period=old.month,
            period_type=FiscalPeriod.PeriodType.NORMAL,
            start_date=old.replace(day=1),
            end_date=old.replace(day=calendar.monthrange(old.year, old.month)[1]),
            status=FiscalPeriod.Status.CLOSED,
        )

    closed_event = _make_dated_order_event(
        shopify_company, 220001, transaction_date=old, store_public_id=store.public_id
    )
    open_event = _make_dated_order_event(
        shopify_company, 220002, transaction_date=date.today(), store_public_id=store.public_id
    )

    processed = ShopifyAccountingHandler().process_pending(shopify_company)

    # The closed-period order is quarantined: no invoice, a visible failure log,
    # and it's advanced (consumed) so it can't re-stall.
    assert not SalesInvoice.objects.filter(
        company=shopify_company, source="shopify", source_document_id="220001"
    ).exists()
    log = ProjectionFailureLog.objects.get(company=shopify_company, event=closed_event)
    assert "closed" in log.message.lower()
    assert ProjectionAppliedEvent.objects.filter(
        company=shopify_company, projection_name="shopify_accounting", event=closed_event
    ).exists()

    # The newer open-period order posted — proving NO head-of-line stall.
    newer = SalesInvoice.objects.get(company=shopify_company, source="shopify", source_document_id="220002")
    assert newer.status == SalesInvoice.Status.POSTED
    assert processed >= 1
