# tests/test_i14_same_pass_order_refund.py
"""
Same-pass order + refund regression (G1 rehearsal §I14 STOP, 2026-09-22).

The defect
----------
A Shopify order and a refund against it were both PENDING when one
`shopify_accounting` pass ran (the initial sync emits them in one task; the
projection pass follows). The order handler posts its invoice through the
sales command path; `create_journal_entry` drains ALL projections
synchronously from inside that call while the invoice is still DRAFT. The
drain re-entered `shopify_accounting`, reached the pending refund, missed the
POSTED-invoice lookup and — because the order's applied marker is written
before its handler runs — took the "order applied but produced no invoice"
branch: the refund was CONSUMED with a MISSING_CONFIG failure row, no credit
note, no self-heal (markers short-circuit, re-sync dedups, rebuild is
pilot-blocked). Nested passes also posted deepest-first, so journal numbers
ran opposite to ids (JE-000001 on the LAST ingested order).

The fix
-------
`BaseProjection.process_pending` refuses to re-enter a pass for the same
(projection, company) that is already in flight on this context; the outer
pass owns stream order, so the order posts fully before its refund is
attempted.

Why plain `django_db` (NOT transaction=True)
--------------------------------------------
Under the wrapping test transaction `transaction.on_commit` never fires, so
the emitter's per-event projection dispatch does not run at ingest and every
ingress event sits pending until the explicit pass below — exactly the
worker's same-pass shape. With `transaction=True` each order would be
projected at ingest-commit and the bug would never reproduce. Keep it plain.

RED on the unpatched tree (main e1b5948: 5 failed / 1 passed): every same-pass
shape fails with zero credit notes and the "produced no invoice" terminal skips;
only the separate-pass control passes. GREEN with the guard.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.models import JournalEntry
from events.models import BusinessEvent, EventBookmark
from projections.models import FiscalPeriod, ProjectionAppliedEvent, ProjectionFailureLog
from sales.models import SalesCreditNote, SalesInvoice
from shopify_connector import commands
from shopify_connector.models import ShopifyOrder, ShopifyRefund, ShopifyStore
from shopify_connector.projections import PROJECTION_NAME, ShopifyAccountingHandler

# The full company + chart-of-accounts + Shopify-mapping scaffolding (one open
# fiscal period = the current month).
from tests.test_system_je_validation import shopify_company  # noqa: F401

pytestmark = pytest.mark.django_db  # deliberately NOT transaction=True — see the module docstring

SHOP_DOMAIN = "i14-same-pass.myshopify.com"
TODAY = date.today()
CREATED_AT = f"{TODAY.isoformat()}T08:30:00Z"
CANCELLED_AT = f"{TODAY.isoformat()}T09:00:00Z"
REFUND_AT = f"{TODAY.isoformat()}T09:10:00Z"
# The rehearsal amounts: #1001 150, #1002 400, #1003 300 (full refund), #1004 650 (cancelled + full refund).
AMOUNTS = {1: "150.00", 2: "400.00", 3: "300.00", 4: "650.00"}
BASE_ORDER_ID = 91400000
BASE_REFUND_ID = 99400000
REHEARSAL_SHAPE = ("O1", "O2", "O3", "R3", "O4c", "R4")


@pytest.fixture
def store(db, shopify_company):  # noqa: F811
    # EGP orders on an EGP-functional company: the projection posts natively.
    shopify_company.default_currency = "EGP"
    shopify_company.functional_currency = "EGP"
    shopify_company.save(update_fields=["default_currency", "functional_currency"])
    return ShopifyStore.objects.create(
        company=shopify_company,
        shop_domain=SHOP_DOMAIN,
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )


@pytest.fixture(autouse=True)
def _fast_invoice_lookup(monkeypatch):
    import shopify_connector.projections as proj_module

    monkeypatch.setattr(proj_module, "_INVOICE_LOOKUP_DELAY_SECONDS", 0.001)


@pytest.fixture(autouse=True)
def _capture_projection_logs(caplog):
    """The project's logging config does not propagate `projections.base` to
    the root logger, so caplog would be blind to the terminal-skip WARNING.
    Attach caplog's handler to that logger directly for the test's duration."""
    target = logging.getLogger("projections.base")
    target.addHandler(caplog.handler)
    try:
        yield
    finally:
        target.removeHandler(caplog.handler)


def _order_payload(n: int, *, cancelled_at: str | None = None, created_at: str = CREATED_AT) -> dict:
    amount = AMOUNTS[n]
    return {
        "id": BASE_ORDER_ID + n,
        "order_number": 1000 + n,
        "name": f"#{1000 + n}",
        "created_at": created_at,
        "cancelled_at": cancelled_at,
        "total_price": amount,
        "subtotal_price": amount,
        "total_tax": "0.00",
        "total_discounts": "0.00",
        "currency": "EGP",
        "financial_status": "paid",
        "gateway": "bogus",
        "customer": None,
        "line_items": [],
        "shipping_lines": [],
        "transactions": [],
    }


def _refund_payload(n: int) -> dict:
    return {
        "id": BASE_REFUND_ID + n,
        "order_id": BASE_ORDER_ID + n,
        "created_at": REFUND_AT,
        "note": "synthetic full refund",
        "transactions": [{"kind": "refund", "status": "success", "amount": AMOUNTS[n]}],
        "refund_line_items": [],
    }


def _ingest(store: ShopifyStore, shape: tuple[str, ...], *, created_at: dict[int, str] | None = None) -> None:
    """Drive the canonical commands in the given order WITHOUT any projection pass.

    Tokens: ``O<n>`` = paid order n; ``O<n>c`` = paid order n then its cancellation;
    ``R<n>`` = full refund of order n. ``created_at`` overrides an order's date.
    """
    for tok in shape:
        n = int(tok[1])
        if tok[0] == "O":
            payload = _order_payload(n, created_at=(created_at or {}).get(n, CREATED_AT))
            result = commands.process_order_paid(store, payload)
            assert result.success, result.error
            if tok.endswith("c"):
                cancelled = _order_payload(n, cancelled_at=CANCELLED_AT, created_at=payload["created_at"])
                result = commands.process_order_cancelled(store, cancelled)
                assert result.success, result.error
        elif tok[0] == "R":
            result = commands.process_refund(store, _refund_payload(n))
            assert result.success, result.error
        else:  # pragma: no cover - test-authoring guard
            raise AssertionError(f"unknown shape token {tok!r}")
    # The same-pass precondition: nothing has been projected at ingest.
    assert not ProjectionAppliedEvent.objects.filter(company=store.company, projection_name=PROJECTION_NAME).exists()


def _pass(store: ShopifyStore) -> int:
    return ShopifyAccountingHandler().process_pending(store.company)


def _ingress_events(store: ShopifyStore):
    return BusinessEvent.objects.filter(
        company=store.company, event_type__in=("shopify.order_paid", "shopify.refund_created")
    ).order_by("company_sequence")


def _entry_number(company, journal_entry_public_id) -> str:
    return JournalEntry.objects.get(company=company, public_id=journal_entry_public_id).entry_number


def _assert_everything_posted_in_ingest_order(store: ShopifyStore, orders: list[int], refunds: list[int]) -> None:
    company = store.company

    invoices = list(SalesInvoice.objects.filter(company=company, source="shopify").order_by("id"))
    assert len(invoices) == len(orders)
    assert all(inv.status == SalesInvoice.Status.POSTED for inv in invoices), [inv.status for inv in invoices]

    credit_notes = list(SalesCreditNote.objects.filter(company=company, source="shopify").order_by("id"))
    assert len(credit_notes) == len(refunds)
    assert all(cn.status == SalesCreditNote.Status.POSTED for cn in credit_notes), [cn.status for cn in credit_notes]

    assert ProjectionFailureLog.objects.filter(company=company).count() == 0
    n_events = _ingress_events(store).count()
    assert n_events == len(orders) + len(refunds)
    assert ProjectionAppliedEvent.objects.filter(company=company, projection_name=PROJECTION_NAME).count() == n_events

    for n in refunds:
        refund = ShopifyRefund.objects.get(company=company, shopify_refund_id=BASE_REFUND_ID + n)
        assert refund.status == ShopifyRefund.Status.PROCESSED
        assert refund.journal_entry_id is not None, f"refund {n} has no journal"

    # Numbering follows posting order, which must now be ingest order: entry
    # numbers strictly ascend with ids (no more LIFO unwinding).
    posted = list(
        JournalEntry.objects.filter(company=company, status=JournalEntry.Status.POSTED)
        .order_by("id")
        .values_list("entry_number", flat=True)
    )
    assert posted == sorted(posted) and len(set(posted)) == len(posted), posted
    posted_at = list(
        SalesInvoice.objects.filter(company=company, source="shopify")
        .order_by("id")
        .values_list("posted_at", flat=True)
    )
    assert posted_at == sorted(posted_at), posted_at

    # Each order's journal precedes its own refund's, and order journals ascend
    # in ingest order.
    order_numbers = []
    for n in orders:
        order = ShopifyOrder.objects.get(company=company, shopify_order_id=BASE_ORDER_ID + n)
        assert order.journal_entry_id is not None
        order_numbers.append(_entry_number(company, order.journal_entry_id))
        if n in refunds:
            refund = ShopifyRefund.objects.get(company=company, shopify_refund_id=BASE_REFUND_ID + n)
            assert order_numbers[-1] < _entry_number(company, refund.journal_entry_id)
    assert order_numbers == sorted(order_numbers), order_numbers

    bookmark = EventBookmark.objects.get(consumer_name=PROJECTION_NAME, company=company)
    assert bookmark.last_event_id == _ingress_events(store).last().id
    assert bookmark.error_count == 0


def _no_terminal_skip_logged(caplog) -> None:
    offenders = [r.getMessage() for r in caplog.records if "terminally skipped" in r.getMessage()]
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "shape, orders, refunds",
    [
        pytest.param(REHEARSAL_SHAPE, [1, 2, 3, 4], [3, 4], id="rehearsal-O-O-O-R-Oc-R"),
        pytest.param(("O1", "R1", "O2", "R2"), [1, 2], [1, 2], id="interleaved-O-R-O-R"),
        pytest.param(("O1", "R1"), [1], [1], id="minimal-O-R"),
    ],
)
def test_same_pass_order_and_refund_both_post(store, caplog, shape, orders, refunds):
    """One pass over an order and its refund must post the invoice AND the
    credit note, record no failure, and number journals in ingest order."""
    _ingest(store, shape)

    with caplog.at_level(logging.WARNING, logger="projections.base"):
        processed = _pass(store)

    assert processed == len(orders) + len(refunds)
    _assert_everything_posted_in_ingest_order(store, orders, refunds)
    _no_terminal_skip_logged(caplog)

    # A second pass is a no-op: nothing pending, nothing duplicated.
    journals = JournalEntry.objects.filter(company=store.company).count()
    assert _pass(store) == 0
    assert JournalEntry.objects.filter(company=store.company).count() == journals
    assert ProjectionFailureLog.objects.filter(company=store.company).count() == 0


def test_rehearsal_shape_amounts_reach_the_books(store):
    """Belt and braces on the rehearsal amounts: 1500 sold, 950 refunded."""
    _ingest(store, REHEARSAL_SHAPE)
    _pass(store)
    company = store.company
    invoiced = sum(
        (inv.total_amount for inv in SalesInvoice.objects.filter(company=company, source="shopify")), Decimal("0")
    )
    credited = sum(
        (cn.total_amount for cn in SalesCreditNote.objects.filter(company=company, source="shopify")), Decimal("0")
    )
    assert invoiced == Decimal("1500.00")
    assert credited == Decimal("950.00")


# ---------------------------------------------------------------------------
# Controls: the guard must not change what was already correct
# ---------------------------------------------------------------------------


def test_separate_passes_still_post_like_the_webhook_cadence(store, caplog):
    """Order projected in one pass, refund ingested and projected in a later
    pass — today's live cadence — behaves exactly as before."""
    _ingest(store, ("O1",))
    assert _pass(store) == 1
    assert SalesInvoice.objects.filter(company=store.company, source="shopify", status="POSTED").count() == 1

    _ingest_refund_only(store, 1)
    with caplog.at_level(logging.WARNING, logger="projections.base"):
        assert _pass(store) == 1
    _assert_everything_posted_in_ingest_order(store, [1], [1])
    _no_terminal_skip_logged(caplog)


def _ingest_refund_only(store: ShopifyStore, n: int) -> None:
    result = commands.process_refund(store, _refund_payload(n))
    assert result.success, result.error


def test_closed_period_order_still_quarantines_its_refund_in_the_same_pass(store, caplog):
    """Negative control: the terminal branch must survive for the genuine
    dead-end. An order dated in a CLOSED period terminal-skips; its refund,
    evaluated in the same pass AFTER the order's consume, still lands as the
    round-3 'produced no invoice' quarantine. An open-period pair in the same
    pass posts normally."""
    from projections.write_barrier import projection_writes_allowed

    last_month_end = TODAY.replace(day=1) - timedelta(days=1)
    with projection_writes_allowed():
        FiscalPeriod.objects.create(
            company=store.company,
            fiscal_year=last_month_end.year,
            period=last_month_end.month,
            period_type=FiscalPeriod.PeriodType.NORMAL,
            start_date=last_month_end.replace(day=1),
            end_date=last_month_end,
            status=FiscalPeriod.Status.CLOSED,
        )
    closed_created_at = f"{last_month_end.isoformat()}T08:30:00Z"

    _ingest(store, ("O1", "R1", "O2", "R2"), created_at={1: closed_created_at})
    with caplog.at_level(logging.WARNING, logger="projections.base"):
        processed = _pass(store)

    # All four events are consumed (two posted, two terminally quarantined).
    assert processed == 4
    failures = list(ProjectionFailureLog.objects.filter(company=store.company).order_by("id"))
    assert [f.event_type for f in failures] == ["shopify.order_paid", "shopify.refund_created"]
    assert "cannot post" in failures[0].message
    assert "was processed but produced no invoice" in failures[1].message
    assert all(f.category == ProjectionFailureLog.Category.MISSING_CONFIG for f in failures)

    # The open-period pair posted in the same pass.
    assert SalesCreditNote.objects.filter(company=store.company, source="shopify", status="POSTED").count() == 1
    refund_2 = ShopifyRefund.objects.get(company=store.company, shopify_refund_id=BASE_REFUND_ID + 2)
    assert refund_2.status == ShopifyRefund.Status.PROCESSED and refund_2.journal_entry_id is not None
    refund_1 = ShopifyRefund.objects.get(company=store.company, shopify_refund_id=BASE_REFUND_ID + 1)
    assert refund_1.journal_entry_id is None
    # One WARNING per quarantined event, captured exactly once (`projections`
    # does not propagate; the fixture attaches caplog's single handler to
    # projections.base directly).
    skipped = [r.getMessage() for r in caplog.records if "terminally skipped" in r.getMessage()]
    assert len(skipped) == 2 and len(set(skipped)) == 2, skipped
