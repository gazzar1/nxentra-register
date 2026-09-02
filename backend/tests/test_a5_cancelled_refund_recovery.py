# tests/test_a5_cancelled_refund_recovery.py
"""
A5 cancelled-order refund recovery — a financially cancelled Shopify order
must produce a truthful financial outcome or a loud structured failure,
never a silent skip inside a status-"ok" leg.

Before the fix (the round-8 PR #138 review finding):
- _sync_refunds skipped EVERY candidate with cancelled_at before booking
  anything. Its own selector guarantees candidates are REFUNDED or
  PARTIALLY_REFUNDED — captured money — so a refund issued during the
  cancellation of an old order (exactly what the updated_at catch-up
  exists to recover) left no parent order, no refund, no event, no
  journal, no error, while the leg reported status "ok" with
  fetch_failures 0.
- _pick_order_handler routed every cancelled order to
  process_order_cancelled, which returns a benign not_captured skip for a
  first-seen order — the A-leg silently dropped first-seen cancelled
  orders whose money Shopify says was captured (PAID / PARTIALLY_PAID /
  REFUNDED / PARTIALLY_REFUNDED).

After the fix:
- cancellation alone is never a no-financial-effect predicate. Captured
  statuses book the parent through canonical process_order_paid, refunds
  book through canonical process_refund via the complete PR #139
  pagination, and cancellation provenance is stamped through canonical
  process_order_cancelled (posted-order branch: raw_payload.cancelled_at)
  — the exact durable state the webhook sequence orders/paid ->
  orders/cancelled -> refunds/create produces.
- never-captured cancellations (authorized / voided / pending / expired)
  stay no-effect but are explicitly counted (cancelled_no_effect_skipped);
  financially relevant cancelled candidates are visible in the leg
  results (cancelled_financial_candidates / _processed /
  cancelled_processing_errors) and can never disappear into a bare
  "scanned".
- a candidate the A4 EGP-only admission dispositions out of the pilot's
  scope stops right there (pilot_scope_skipped): no provenance stamp and
  no fulfillment / refund backfill runs against the row the pilot refused
  to create, so the documented disposition never degrades into recurring
  "Order not found" processing errors.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.db import connection as db_connection

from accounting.models import JournalEntry
from events.models import BusinessEvent
from shopify_connector import commands
from shopify_connector.graphql_client import ShopifyAdminClient
from shopify_connector.models import ShopifyOrder, ShopifyRefund, ShopifyStore

# The full company + chart-of-accounts + Shopify-mapping scaffolding, so
# journal-level assertions run against REAL projection postings instead of
# comparing 0 == 0.
from tests.test_system_je_validation import shopify_company  # noqa: F401

pytestmark = pytest.mark.django_db

SHOP_DOMAIN = "a5-cancelled.myshopify.com"
ORDER_ID = 9200001
REFUND_ID = 888001
CANCELLED_AT = "2026-04-28T12:00:00Z"
# The shopify_company scaffolding provisions exactly ONE open FiscalPeriod —
# the current month at run time — so journal-materializing tests must date
# their documents inside it.
RECENT_CREATED_AT = f"{date.today().isoformat()}T08:30:00Z"
RECENT_REFUND_AT = f"{date.today().isoformat()}T10:00:00Z"


@pytest.fixture
def shopify_store(db, shopify_company):  # noqa: F811
    # This suite books EGP orders — align the functional currency so the
    # projection posts natively instead of entering the FX-rate path.
    shopify_company.default_currency = "EGP"
    shopify_company.functional_currency = "EGP"
    shopify_company.save(update_fields=["default_currency", "functional_currency"])
    return ShopifyStore.objects.create(
        company=shopify_company,
        shop_domain=SHOP_DOMAIN,
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )


def _order_payload(
    order_id=ORDER_ID,
    financial_status="refunded",
    cancelled_at=CANCELLED_AT,
    created_at="2025-10-03T08:30:00Z",
    currency="EGP",
):
    """Cancelled parent payload; created_at deliberately months before any
    catch-up window (the B-candidate any-age reach)."""
    return {
        "id": order_id,
        "order_number": 3001,
        "name": "#3001",
        "created_at": created_at,
        "cancelled_at": cancelled_at,
        "total_price": "500.00",
        "subtotal_price": "500.00",
        "total_tax": "0.00",
        "total_discounts": "0.00",
        "currency": currency,
        "financial_status": financial_status,
        "gateway": "shopify_payments",
        "customer": None,
        "line_items": [],
        "shipping_lines": [],
        "transactions": [],
    }


def _refund_payload(refund_id=REFUND_ID, order_id=ORDER_ID, amount="50.00"):
    # Refund date kept near "today" so the journal-materializing tests post
    # into an auto-provisionable open period instead of quarantining.
    return {
        "id": refund_id,
        "order_id": order_id,
        "created_at": RECENT_REFUND_AT,
        "note": "cancelled order",
        "transactions": [{"kind": "refund", "status": "success", "amount": amount}],
        "refund_line_items": [],
    }


class _FakeClient:
    def __init__(self, orders=None, refunded_orders=None, refunds_by_order=None, refund_fetch_error=None):
        self._orders = orders or []
        self._refunded_orders = refunded_orders or []
        self._refunds = refunds_by_order or {}
        self._refund_fetch_error = refund_fetch_error

    def iter_orders(self, created_at_min, created_at_max):
        yield from self._orders

    def iter_refunded_orders(self, updated_at_min, updated_at_max):
        yield from self._refunded_orders

    def get_order_fulfillments(self, order_id):
        return []

    def get_order_refunds(self, order_id):
        if self._refund_fetch_error is not None:
            raise self._refund_fetch_error
        return self._refunds.get(order_id, [])


def _order_row(store, order_id=ORDER_ID):
    return ShopifyOrder.objects.filter(company=store.company, shopify_order_id=order_id).first()


def _paid_events(store, order_id=ORDER_ID):
    return BusinessEvent.objects.filter(company=store.company, idempotency_key=f"shopify.order.paid:{order_id}").count()


def _refund_events(store, refund_id=REFUND_ID):
    return BusinessEvent.objects.filter(
        company=store.company, idempotency_key=f"shopify.refund.created:{refund_id}"
    ).count()


def _events(store):
    """Count the Shopify INGRESS events only — with real commits the
    projection cascade adds sales/journal events to the same company
    stream; a re-booked parent under a variant idempotency key would
    still appear here."""
    return BusinessEvent.objects.filter(
        company=store.company, event_type__in=("shopify.order_paid", "shopify.refund_created")
    ).count()


def _post_projections(store):
    """Drain any pending shopify events and assert NO projection failure was
    recorded. The journal-materializing tests run under
    django_db(transaction=True) so each leg-internal transaction.atomic
    COMMITS for real and the emitter's on_commit projection dispatch fires
    right at that boundary — exactly the Celery-worker sequencing, where the
    order posts fully before its refund is processed. (Processing both
    events later inside one wrapping test transaction instead hits the A23
    same-transaction visibility window and terminal-skips the refund.)"""
    from projections.models import ProjectionFailureLog
    from shopify_connector.projections import ShopifyAccountingHandler

    processed = ShopifyAccountingHandler().process_pending(store.company)
    failures = list(ProjectionFailureLog.objects.filter(company=store.company).values_list("category", "message"))
    if failures:
        from projections.models import ProjectionAppliedEvent
        from sales.models import SalesInvoice

        invoices = list(
            SalesInvoice.objects.filter(company=store.company).values("source", "source_document_id", "status")
        )
        events = list(BusinessEvent.objects.filter(company=store.company).values_list("id", "event_type"))
        applied = list(
            ProjectionAppliedEvent.objects.filter(company=store.company).values_list("projection_name", "event_id")
        )
        raise AssertionError(
            f"projection failures (processed={processed}): {failures}; "
            f"invoices={invoices}; events={events}; applied={applied}"
        )
    return processed


def _journals(store):
    return JournalEntry.objects.filter(company=store.company).count()


# ---------------------------------------------------------------------------
# B-leg: cancelled refunded candidates recover parent + complete refunds
# ---------------------------------------------------------------------------


def test_sync_refunds_books_cancelled_refunded_old_order(shopify_store, monkeypatch):
    """E1: old cancelled + REFUNDED order, no local parent — the catch-up
    must book the parent, process the complete refund history, record
    cancellation provenance, and return a truthful success."""
    from shopify_connector import tasks

    fake = _FakeClient(
        refunded_orders=[_order_payload()],
        refunds_by_order={ORDER_ID: [_refund_payload()]},
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    result = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    assert result["status"] == "ok", result
    assert result["refunds_created"] == 1
    assert result["errors"] == 0
    assert result["cancelled_financial_candidates"] == 1
    assert result["cancelled_financial_processed"] == 1
    assert result["cancelled_processing_errors"] == 0
    assert result["pilot_scope_skipped"] == 0, "an in-scope EGP candidate must never read as a pilot skip"

    order = _order_row(shopify_store)
    assert order is not None, "a cancelled REFUNDED candidate must book its parent invoice"
    assert (order.raw_payload or {}).get("cancelled_at") == CANCELLED_AT, (
        "cancellation provenance must be durable on the parent order"
    )
    assert ShopifyRefund.objects.filter(company=shopify_store.company, shopify_refund_id=REFUND_ID).exists()
    assert _paid_events(shopify_store) == 1
    assert _refund_events(shopify_store) == 1


def test_sync_refunds_books_cancelled_partially_refunded(shopify_store, monkeypatch):
    """E2: the same recovery for a PARTIALLY_REFUNDED cancelled order —
    part of the captured amount was returned; both the invoice and the
    partial refund must reach the books."""
    from shopify_connector import tasks

    fake = _FakeClient(
        refunded_orders=[_order_payload(financial_status="partially_refunded")],
        refunds_by_order={ORDER_ID: [_refund_payload(amount="120.00")]},
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    result = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    assert result["status"] == "ok", result
    assert result["cancelled_financial_candidates"] == 1
    assert result["cancelled_financial_processed"] == 1
    refund = ShopifyRefund.objects.get(company=shopify_store.company, shopify_refund_id=REFUND_ID)
    assert refund.amount == Decimal("120.00")
    assert _order_row(shopify_store) is not None
    assert (_order_row(shopify_store).raw_payload or {}).get("cancelled_at") == CANCELLED_AT


@pytest.mark.django_db(transaction=True)
def test_posted_parent_cancelled_with_missed_refund_webhook(shopify_store, monkeypatch):
    """E3: an order booked long ago, later cancelled-with-refund whose
    refunds/create webhook was dropped — the catch-up must book the refund
    and stamp provenance without duplicating the parent journal."""
    from shopify_connector import tasks

    assert commands.process_order_paid(
        shopify_store, _order_payload(financial_status="paid", cancelled_at=None, created_at=RECENT_CREATED_AT)
    ).success
    _post_projections(shopify_store)
    journals_before = _journals(shopify_store)
    events_before = _events(shopify_store)
    assert journals_before >= 1, "sanity: the posted parent must have a real journal"

    fake = _FakeClient(
        refunded_orders=[_order_payload()],
        refunds_by_order={ORDER_ID: [_refund_payload()]},
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    result = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")
    _post_projections(shopify_store)

    assert result["status"] == "ok", result
    assert result["refunds_created"] == 1
    assert result["cancelled_financial_processed"] == 1
    assert ShopifyOrder.objects.filter(company=shopify_store.company).count() == 1
    assert _paid_events(shopify_store) == 1, "the already-posted parent must not re-book"
    assert _refund_events(shopify_store) == 1
    assert _events(shopify_store) == events_before + 1, (
        "the catch-up must add EXACTLY the refund event — no re-booked parent under any variant key"
    )
    assert (_order_row(shopify_store).raw_payload or {}).get("cancelled_at") == CANCELLED_AT, (
        "the missed cancellation must be stamped through the canonical writer"
    )
    assert _journals(shopify_store) == journals_before + 1, (
        "exactly the refund journal is added — the parent journal must not duplicate"
    )


@pytest.mark.django_db(transaction=True)
def test_cancelled_refunded_in_both_a_and_b_posts_once(shopify_store, monkeypatch):
    """E4: a recent cancelled+refunded order appears in BOTH the created_at
    order leg and the updated_at refund leg — one parent, one refund, one
    event/journal per financial fact."""
    from shopify_connector import tasks

    payload = _order_payload(created_at=RECENT_CREATED_AT)
    fake = _FakeClient(
        orders=[payload],
        refunded_orders=[payload],
        refunds_by_order={ORDER_ID: [_refund_payload()]},
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    a_result = tasks._sync_orders(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")
    _post_projections(shopify_store)
    journals_after_a = _journals(shopify_store)
    b_result = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")
    _post_projections(shopify_store)

    assert a_result["status"] == "ok", a_result
    assert a_result["cancelled_financial_candidates"] == 1
    assert a_result["cancelled_financial_processed"] == 1
    assert a_result["refunds_backfilled"] == 1
    assert b_result["status"] == "ok", b_result
    assert b_result["cancelled_financial_candidates"] == 1
    assert b_result["cancelled_financial_processed"] == 1
    assert b_result["refunds_created"] == 0, "the B leg must not re-book the A-leg refund"

    assert ShopifyOrder.objects.filter(company=shopify_store.company).count() == 1
    assert ShopifyRefund.objects.filter(company=shopify_store.company).count() == 1
    assert _paid_events(shopify_store) == 1
    assert _refund_events(shopify_store) == 1
    assert _events(shopify_store) == 2, (
        "across BOTH legs the company must hold exactly the paid event and the "
        "refund event — a re-book under a variant idempotency key would show here"
    )
    assert journals_after_a >= 2, "sanity: the A leg must have posted real invoice + refund journals"
    assert _journals(shopify_store) == journals_after_a, "the overlapping A/B candidate must not duplicate any journal"


@pytest.mark.django_db(transaction=True)
def test_exact_retry_no_duplicates_and_honest_counters(shopify_store, monkeypatch):
    """E5: an exact retry re-fetches, books nothing new, and keeps the
    cancelled counters honest."""
    from shopify_connector import tasks

    fake = _FakeClient(
        refunded_orders=[_order_payload(created_at=RECENT_CREATED_AT)],
        refunds_by_order={ORDER_ID: [_refund_payload()]},
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    first = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")
    assert first["status"] == "ok"
    _post_projections(shopify_store)

    orders_before = ShopifyOrder.objects.count()
    refunds_before = ShopifyRefund.objects.count()
    events_before = BusinessEvent.objects.count()
    journals_before = _journals(shopify_store)
    assert journals_before >= 2, "sanity: real invoice + refund journals must exist before the retry"

    second = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")
    _post_projections(shopify_store)

    assert second["status"] == "ok", second
    assert second["refunds_created"] == 0
    assert second["cancelled_financial_candidates"] == 1
    assert second["cancelled_financial_processed"] == 1
    assert second["cancelled_processing_errors"] == 0
    assert ShopifyOrder.objects.count() == orders_before
    assert ShopifyRefund.objects.count() == refunds_before
    assert BusinessEvent.objects.count() == events_before, "retry must not duplicate any event"
    assert _journals(shopify_store) == journals_before, "retry must not duplicate any journal"


def test_cancelled_fetch_failure_keeps_parent_and_fails_loudly(shopify_store, monkeypatch):
    """E6: a complete-refund fetch failure on a cancelled candidate — the
    booked parent stays committed, no partial refund list is processed,
    the leg is a structured error, and the retry completes idempotently."""
    from shopify_connector import tasks

    broken = _FakeClient(
        refunded_orders=[_order_payload()],
        refund_fetch_error=RuntimeError("network down"),
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: broken)

    first = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    assert first["status"] == "error", first
    assert first["fetch_failures"] == 1
    assert first["refunds_created"] == 0
    assert first["errors"] == 1, "exactly the fetch failure — no double count"
    assert first["cancelled_financial_candidates"] == 1
    assert first["cancelled_financial_processed"] == 0
    assert first["cancelled_processing_errors"] == 1
    assert _order_row(shopify_store) is not None, (
        "a parent booked before the refund-fetch failure stays committed (PR #139 contract)"
    )
    assert ShopifyRefund.objects.filter(company=shopify_store.company).count() == 0, (
        "no partial refund list may be processed"
    )

    healthy = _FakeClient(
        refunded_orders=[_order_payload()],
        refunds_by_order={ORDER_ID: [_refund_payload()]},
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: healthy)

    retry = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    assert retry["status"] == "ok", retry
    assert retry["refunds_created"] == 1
    assert retry["cancelled_financial_processed"] == 1
    assert ShopifyOrder.objects.filter(company=shopify_store.company).count() == 1
    assert _paid_events(shopify_store) == 1, "retry must not duplicate the parent"
    assert _refund_events(shopify_store) == 1


# ---------------------------------------------------------------------------
# A-leg: first-seen cancelled orders with captured money
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("financial_status", ["paid", "partially_paid"])
def test_sync_orders_cancelled_paid_books_parent_with_provenance(shopify_store, monkeypatch, financial_status):
    """E7: a first-seen cancelled order whose financial_status is PAID or
    PARTIALLY_PAID — money Shopify says was captured and kept. Webhook
    parity (orders/paid then orders/cancelled) keeps the revenue booked
    until a refund reverses it; the sync path must produce that same
    durable state, not a silent not_captured skip."""
    from shopify_connector import tasks

    fake = _FakeClient(orders=[_order_payload(financial_status=financial_status, created_at="2026-04-27T09:00:00Z")])
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    result = tasks._sync_orders(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    assert result["status"] == "ok", result
    assert result["created"] == 1
    assert result["errors"] == 0
    assert result["cancelled_financial_candidates"] == 1
    assert result["cancelled_financial_processed"] == 1
    assert result["cancelled_no_effect_skipped"] == 0
    assert result["pilot_scope_skipped"] == 0
    order = _order_row(shopify_store)
    assert order is not None, "captured money on a cancelled order must not disappear"
    assert (order.raw_payload or {}).get("cancelled_at") == CANCELLED_AT
    assert _paid_events(shopify_store) == 1
    assert ShopifyRefund.objects.count() == 0


def test_sync_orders_cancelled_refunded_books_parent_and_refunds(shopify_store, monkeypatch):
    """A first-seen cancelled + REFUNDED order in the created_at window
    books the parent AND backfills its refunds in the same A-leg pass."""
    from shopify_connector import tasks

    fake = _FakeClient(
        orders=[_order_payload(created_at="2026-04-27T09:00:00Z")],
        refunds_by_order={ORDER_ID: [_refund_payload()]},
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    result = tasks._sync_orders(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    assert result["status"] == "ok", result
    assert result["cancelled_financial_candidates"] == 1
    assert result["cancelled_financial_processed"] == 1
    assert result["refunds_backfilled"] == 1
    assert _paid_events(shopify_store) == 1
    assert _refund_events(shopify_store) == 1
    assert (_order_row(shopify_store).raw_payload or {}).get("cancelled_at") == CANCELLED_AT


@pytest.mark.parametrize("financial_status", ["voided", "authorized", "pending", "expired", ""])
def test_sync_orders_cancelled_uncaptured_is_explicit_no_effect(shopify_store, monkeypatch, financial_status):
    """E8: a cancelled order whose money was never captured books nothing —
    zero financial journal — and is reported as an explicit intentional
    no-effect disposition, not a false error and not a generic skip."""
    from shopify_connector import tasks

    fake = _FakeClient(orders=[_order_payload(financial_status=financial_status, created_at="2026-04-27T09:00:00Z")])
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)
    journals_before = JournalEntry.objects.filter(company=shopify_store.company).count()

    result = tasks._sync_orders(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    assert result["status"] == "ok", result
    assert result["errors"] == 0
    assert result["cancelled_no_effect_skipped"] == 1
    assert result["cancelled_financial_candidates"] == 0
    # The not_captured answer is the benign idempotent skip, never the A4
    # pilot-scope disposition — the two buckets must not bleed into each other.
    assert result["skipped"] == 1
    assert result["pilot_scope_skipped"] == 0
    assert _order_row(shopify_store) is None, "never-captured cancellation must not invent revenue"
    assert BusinessEvent.objects.filter(company=shopify_store.company).count() == 0
    assert JournalEntry.objects.filter(company=shopify_store.company).count() == journals_before


# ---------------------------------------------------------------------------
# Webhook parity — the correction duplicates nothing the webhooks did
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_webhook_sequence_then_catchup_duplicates_nothing(shopify_store, monkeypatch):
    """E9: the full webhook sequence (paid -> cancelled -> refunds/create)
    already produced the truthful state; the catch-up encountering the
    same order as a B candidate must change nothing."""
    from shopify_connector import tasks

    assert commands.process_order_paid(
        shopify_store, _order_payload(financial_status="paid", cancelled_at=None, created_at=RECENT_CREATED_AT)
    ).success
    assert commands.process_order_cancelled(shopify_store, {"id": ORDER_ID, "cancelled_at": CANCELLED_AT}).success
    assert commands.process_refund(shopify_store, _refund_payload()).success
    _post_projections(shopify_store)

    orders_before = ShopifyOrder.objects.count()
    refunds_before = ShopifyRefund.objects.count()
    events_before = BusinessEvent.objects.count()
    journals_before = _journals(shopify_store)
    assert journals_before >= 2, "sanity: the webhook sequence must have posted real journals"

    fake = _FakeClient(
        refunded_orders=[_order_payload()],
        refunds_by_order={ORDER_ID: [_refund_payload()]},
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    result = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")
    _post_projections(shopify_store)

    assert result["status"] == "ok", result
    assert result["refunds_created"] == 0
    assert result["cancelled_financial_processed"] == 1
    assert ShopifyOrder.objects.count() == orders_before
    assert ShopifyRefund.objects.count() == refunds_before
    assert BusinessEvent.objects.count() == events_before
    assert _journals(shopify_store) == journals_before


# ---------------------------------------------------------------------------
# Lock / network posture — PR #139 refund reads stay outside transactions
# ---------------------------------------------------------------------------


class _DepthPinClient(ShopifyAdminClient):
    """Real client whose execute records the savepoint depth per network
    read and serves the scripted 2026-04 refund shapes."""

    def __init__(self, refunded_orders, refund_id=REFUND_ID, orders=None):
        super().__init__(SHOP_DOMAIN, "test-token")
        self._refunded_orders = refunded_orders
        self._refund_id = refund_id
        self._orders = orders or []
        self.execute_depths = []

    def iter_orders(self, created_at_min, created_at_max):
        yield from self._orders

    def iter_refunded_orders(self, updated_at_min, updated_at_max):
        yield from self._refunded_orders

    def get_order_fulfillments(self, order_id):
        return []

    def execute(self, query, variables=None, allow_partial=False):
        self.execute_depths.append(len(db_connection.savepoint_ids))
        if "OrderRefundSummaries" in query:
            return {
                "order": {
                    "refunds": [
                        {
                            "id": f"gid://shopify/Refund/{self._refund_id}",
                            "legacyResourceId": str(self._refund_id),
                            "createdAt": "2026-04-28T12:00:00Z",
                            "note": "",
                        }
                    ]
                }
            }
        assert "RefundDetail" in query, f"unexpected query: {query[:80]}"
        return {
            "refund": {
                "transactions": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"kind": "REFUND", "status": "SUCCESS", "amountSet": {"shopMoney": {"amount": "50.00"}}}],
                },
                "refundLineItems": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [],
                },
            }
        }


def test_cancelled_path_network_reads_outside_transactional_scope(shopify_store, monkeypatch):
    """E10: through the corrected cancelled path (parent booking +
    provenance stamp + refund backfill), every Shopify network read must
    happen OUTSIDE any transaction scope — no read under the Company
    admission lock or the provenance atomic."""
    from shopify_connector import tasks

    client = _DepthPinClient([_order_payload()])
    monkeypatch.setattr(commands, "_admin_client", lambda store: client)

    baseline_depth = len(db_connection.savepoint_ids)
    result = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    assert result["status"] == "ok", result
    assert result["refunds_created"] == 1
    assert client.execute_depths, "the pinned client must have served refund pages"
    assert all(d == baseline_depth for d in client.execute_depths), (
        "cancelled-candidate refund reads must never run inside an atomic "
        "block — a fetch inside one would hold locks across network I/O"
    )


def test_sync_orders_cancelled_path_reads_outside_transactional_scope(shopify_store, monkeypatch):
    """E10 (A-leg twin): the A-leg cancelled path adds a provenance-stamp
    atomic right next to the fulfillment/refund backfills — every Shopify
    network read must still happen OUTSIDE any transaction scope."""
    from shopify_connector import tasks

    client = _DepthPinClient([], orders=[_order_payload(created_at="2026-04-27T09:00:00Z")])
    monkeypatch.setattr(commands, "_admin_client", lambda store: client)

    baseline_depth = len(db_connection.savepoint_ids)
    result = tasks._sync_orders(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    assert result["status"] == "ok", result
    assert result["refunds_backfilled"] == 1
    assert result["cancelled_financial_processed"] == 1
    assert client.execute_depths, "the pinned client must have served refund pages"
    assert all(d == baseline_depth for d in client.execute_depths), (
        "A-leg cancelled-order refund reads must never run inside an atomic block"
    )


def test_cancelled_path_uses_complete_pagination(shopify_store, monkeypatch):
    """E12: high-cardinality — the corrected cancelled path drains the real
    PR #139 pagination (uncapped refund list, per-refund connections), so
    a cancelled order with many refunds books every one of them."""
    from shopify_connector import tasks

    refund_ids = [888100 + i for i in range(11)]
    paged_gid = f"gid://shopify/Refund/{refund_ids[0]}"

    class _ManyRefundsClient(_DepthPinClient):
        def execute(self, query, variables=None, allow_partial=False):
            variables = variables or {}
            self.execute_depths.append(len(db_connection.savepoint_ids))
            if "OrderRefundSummaries" in query:
                return {
                    "order": {
                        "refunds": [
                            {
                                "id": f"gid://shopify/Refund/{r}",
                                "legacyResourceId": str(r),
                                "createdAt": "2026-04-28T12:00:00Z",
                                "note": "",
                            }
                            for r in refund_ids
                        ]
                    }
                }
            if "RefundTransactionsPage" in query:
                # The first refund's transactions span TWO pages — an
                # implementation ignoring pageInfo/endCursor books 10.00
                # instead of 25.00.
                assert variables["id"] == paged_gid and variables.get("cursor") == "t1"
                return {
                    "refund": {
                        "transactions": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {"kind": "REFUND", "status": "SUCCESS", "amountSet": {"shopMoney": {"amount": "15.00"}}}
                            ],
                        }
                    }
                }
            assert "RefundDetail" in query
            first_page = variables["id"] == paged_gid
            return {
                "refund": {
                    "transactions": {
                        "pageInfo": {
                            "hasNextPage": first_page,
                            "endCursor": "t1" if first_page else None,
                        },
                        "nodes": [
                            {"kind": "REFUND", "status": "SUCCESS", "amountSet": {"shopMoney": {"amount": "10.00"}}}
                        ],
                    },
                    "refundLineItems": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []},
                }
            }

    client = _ManyRefundsClient([_order_payload()])
    monkeypatch.setattr(commands, "_admin_client", lambda store: client)

    result = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    assert result["status"] == "ok", result
    assert result["refunds_created"] == 11
    assert result["cancelled_financial_processed"] == 1
    assert ShopifyRefund.objects.filter(company=shopify_store.company).count() == 11
    paged = ShopifyRefund.objects.get(company=shopify_store.company, shopify_refund_id=refund_ids[0])
    assert paged.amount == Decimal("25.00"), (
        "the cursor-drained second transactions page must reach the refund aggregate"
    )


# ---------------------------------------------------------------------------
# Pilot parity — no new pilot semantics; EGP admission unchanged. A candidate
# the A4 EGP-only admission dispositions out of scope (structured
# skipped_pilot_scope: no row, no event, no retry) is counted in its own
# pilot_scope_skipped bucket and processing STOPS there — the review-round
# finding was that both legs fell through to the fulfillment / refund
# backfills against the row the pilot deliberately refused to create, so a
# refunded out-of-scope order raised "Order not found" per refund on EVERY
# poll, turning a documented disposition into a recurring processing error.
# ---------------------------------------------------------------------------


class _RecordingClient(_FakeClient):
    """Fake client that records every per-order read so a test can prove a
    dispositioned order triggered NO follow-up network reads."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fulfillment_reads = []
        self.refund_reads = []

    def get_order_fulfillments(self, order_id):
        self.fulfillment_reads.append(order_id)
        return super().get_order_fulfillments(order_id)

    def get_order_refunds(self, order_id):
        self.refund_reads.append(order_id)
        return super().get_order_refunds(order_id)


def _activate_pilot(company):
    from accounts.models import Company

    company.pilot_profile = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.fiscal_year_start_month = 1
    company.save()


def _assert_nothing_booked(company):
    assert ShopifyOrder.objects.filter(company=company).count() == 0, (
        "the EGP-only pilot admission must keep refusing non-EGP orders"
    )
    assert ShopifyRefund.objects.filter(company=company).count() == 0
    assert BusinessEvent.objects.filter(company=company).count() == 0


def test_pilot_non_egp_cancelled_candidate_is_dispositioned_not_errored(shopify_store, monkeypatch):
    """E11 (B leg): under the active pilot a non-EGP cancelled REFUNDED
    candidate WITH refunds in its history books nothing, triggers no refund
    read, and is reported in pilot_scope_skipped — never as processed and
    never as a recurring "Order not found" processing error."""
    from shopify_connector import tasks

    company = shopify_store.company
    _activate_pilot(company)
    fake = _RecordingClient(
        refunded_orders=[_order_payload(currency="USD")],
        refunds_by_order={ORDER_ID: [_refund_payload()]},
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    result = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    # Behavioral claims first, so a regression that drops the counter still
    # fails on the disposition itself rather than on a missing key.
    assert result["status"] == "ok", result
    assert result["scanned"] == 1
    assert result["errors"] == 0, "a deliberate out-of-scope disposition is not a processing error"
    assert fake.refund_reads == [], "no refund backfill may run against the row the pilot refused to create"
    assert result["refunds_created"] == 0
    assert result["cancelled_financial_candidates"] == 1
    assert result["cancelled_financial_processed"] == 0, "dispositioned out of scope is not 'processed'"
    assert result["cancelled_processing_errors"] == 0
    assert result["pilot_scope_skipped"] == 1
    _assert_nothing_booked(company)


def test_pilot_non_egp_disposition_is_stable_across_polls(shopify_store, monkeypatch):
    """E11b: the disposition is idempotent and QUIET on retry — the second
    poll reports the identical counters with zero errors (before the fix
    every poll re-raised the per-refund "Order not found" errors)."""
    from shopify_connector import tasks

    company = shopify_store.company
    _activate_pilot(company)
    fake = _RecordingClient(
        refunded_orders=[_order_payload(currency="USD")],
        refunds_by_order={ORDER_ID: [_refund_payload()]},
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    first = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")
    second = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    assert first == second, (first, second)
    assert second["errors"] == 0
    assert second["pilot_scope_skipped"] == 1
    assert fake.refund_reads == []
    _assert_nothing_booked(company)


@pytest.mark.parametrize(
    ("financial_status", "cancelled_at"),
    [("refunded", CANCELLED_AT), ("refunded", None), ("pending", None)],
    ids=["refunded-cancelled", "refunded-open", "pending-open"],
)
def test_sync_orders_pilot_non_egp_order_is_dispositioned(shopify_store, monkeypatch, financial_status, cancelled_at):
    """E11c (A leg): a non-EGP order — REFUNDED cancelled or open (the paid
    writer's path is shared), or PENDING (the pending writer's own
    structured skip) — is dispositioned out of scope with NO fulfillment
    read, NO refund read, no provenance stamp, and lands in
    pilot_scope_skipped: not created (nothing was — the old code reported
    every one of these as created), not skipped (the benign idempotent
    bucket), not an error (nothing failed)."""
    from shopify_connector import tasks

    company = shopify_store.company
    _activate_pilot(company)
    fake = _RecordingClient(
        orders=[
            _order_payload(
                financial_status=financial_status,
                currency="USD",
                cancelled_at=cancelled_at,
                created_at="2026-04-27T09:00:00Z",
            )
        ],
        refunds_by_order={ORDER_ID: [_refund_payload()]},
    )
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    result = tasks._sync_orders(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    # Behavioral claims first, so a regression that drops the counter still
    # fails on the disposition itself rather than on a missing key.
    assert result["status"] == "ok", result
    assert result["fetched"] == 1
    assert result["errors"] == 0
    assert result["created"] == 0, "a pilot-scope skip creates nothing and must not be reported as created"
    assert result["skipped"] == 0
    assert fake.fulfillment_reads == [], "no COGS backfill may run for a row the pilot refused to create"
    assert fake.refund_reads == [], "no refund backfill may run for a row the pilot refused to create"
    assert result["refunds_backfilled"] == 0
    assert result["cogs_fulfillments"] == 0
    expected_candidates = 1 if cancelled_at else 0
    assert result["cancelled_financial_candidates"] == expected_candidates
    assert result["cancelled_financial_processed"] == 0
    assert result["cancelled_processing_errors"] == 0
    assert result["cancelled_no_effect_skipped"] == 0
    assert result["pilot_scope_skipped"] == 1
    _assert_nothing_booked(company)


def test_pilot_scope_skip_is_distinct_from_already_booked_skip(shopify_store, monkeypatch):
    """E11d: the detector keys on the structured status, not on "skipped" —
    an already-booked EGP order re-seen by the A leg still takes the
    idempotent 'skipped' path (and still backfills) with pilot_scope_skipped
    at zero, so the new bucket can never absorb the existing semantics."""
    from shopify_connector import tasks

    company = shopify_store.company
    _activate_pilot(company)
    payload = _order_payload(financial_status="paid", cancelled_at=None, created_at="2026-04-27T09:00:00Z")
    assert commands.process_order_paid(shopify_store, payload).success
    fake = _RecordingClient(orders=[payload])
    monkeypatch.setattr(commands, "_admin_client", lambda store: fake)

    result = tasks._sync_orders(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")

    assert result["status"] == "ok", result
    assert result["skipped"] == 1
    assert result["created"] == 0
    assert result["pilot_scope_skipped"] == 0
    assert result["errors"] == 0
    assert fake.fulfillment_reads == [ORDER_ID], "an already-booked EGP order keeps its COGS backfill"
    assert ShopifyOrder.objects.filter(company=company).count() == 1
