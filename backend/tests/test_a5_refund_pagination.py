# tests/test_a5_refund_pagination.py
"""
A5 refund-catch-up completeness — complete pagination, loud truncation.

Before the fix, get_order_refunds fetched only the first GraphQL page:
first 10 refunds per order, first 10 transactions per refund, first 50
refund line items per refund — no pagination, no truncation detection. A
refund component past the first page silently vanished from the refund
aggregate, and a failed fetch read as "status ok, refunds_created 0".

After the fix:
- get_order_refunds paginates Order.refunds, Refund.transactions and
  Refund.refundLineItems to exhaustion (page-size constants are page
  sizes, not completeness caps) and raises ShopifyGraphQLIncomplete on a
  failed page, an invalid shape, a repeated cursor, or hasNextPage
  without endCursor — a partial payload is never returned as complete.
- _backfill_order_refunds reports {"booked", "fetch_failed",
  "process_errors"}; a fetch failure books nothing for that order.
- _sync_refunds returns a structured non-success (status "error",
  fetch_failures counted) when any candidate order's complete refund
  history could not be fetched.
"""

from decimal import Decimal

import pytest
from django.db import connection as db_connection

from accounting.models import JournalEntry
from events.models import BusinessEvent
from shopify_connector import commands
from shopify_connector.graphql_client import (
    ShopifyAdminClient,
    ShopifyGraphQLError,
    ShopifyGraphQLIncomplete,
)
from shopify_connector.models import ShopifyOrder, ShopifyRefund, ShopifyStore
from shopify_connector.payload_validation import refund_aggregate_amount

pytestmark = pytest.mark.django_db

SHOP_DOMAIN = "a5-pagination.myshopify.com"
ORDER_ID = 9100001


@pytest.fixture
def shopify_store(db, company):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain=SHOP_DOMAIN,
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )


def _order_payload(order_id=ORDER_ID, financial_status="refunded", created_at="2025-11-03T08:30:00Z"):
    """Parent order payload; created_at deliberately months before any
    catch-up window (the A159 historical-reach contract)."""
    return {
        "id": order_id,
        "order_number": 2001,
        "name": "#2001",
        "created_at": created_at,
        "total_price": "500.00",
        "subtotal_price": "500.00",
        "total_tax": "0.00",
        "total_discounts": "0.00",
        "currency": "EGP",
        "financial_status": financial_status,
        "customer": None,
        "line_items": [],
        "shipping_lines": [],
        "transactions": [],
    }


# ---------------------------------------------------------------------------
# Scripted GraphQL page server
# ---------------------------------------------------------------------------


def _page(nodes, has_next=False, end_cursor=None):
    return {"pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor}, "nodes": nodes}


def _id_node(refund_id):
    return {
        "id": f"gid://shopify/Refund/{refund_id}",
        "legacyResourceId": str(refund_id),
        "createdAt": "2026-04-29T10:00:00Z",
        "note": "",
    }


def _txn_node(amount="10.00", kind="REFUND", status="SUCCESS"):
    return {"kind": kind, "status": status, "amountSet": {"shopMoney": {"amount": amount}}}


def _line_node(subtotal="1.00"):
    return {
        "quantity": 1,
        "restockType": "RETURN",
        "subtotalSet": {"shopMoney": {"amount": subtotal}},
        "lineItem": {"sku": "SKU-1", "title": "Item"},
    }


class _ScriptedClient(ShopifyAdminClient):
    """Real client with `execute` served from scripted pages.

    script keys:
      "OrderRefundIds"        -> list of refunds-connection pages (in cursor order)
      "OrderRefundIdsList"    -> list-form response value for order.refunds
      "RefundDetail"          -> {gid: {"transactions": page, "refundLineItems": page}}
      "RefundTransactionsPage"-> {(gid, cursor): page-or-Exception}
      "RefundLineItemsPage"   -> {(gid, cursor): page-or-Exception}
    Any value may be an Exception instance — it is raised on serve.
    """

    def __init__(self, script, refunded_orders=None):
        super().__init__(SHOP_DOMAIN, "test-token")
        self._script = script
        self._refunded_orders = refunded_orders or []
        self._id_page_cursor_calls = 0
        self.calls = []
        self.execute_depths = []

    def iter_refunded_orders(self, updated_at_min, updated_at_max):
        yield from self._refunded_orders

    def execute(self, query, variables=None, allow_partial=False):
        variables = variables or {}
        self.execute_depths.append(len(db_connection.savepoint_ids))

        def serve(value):
            if isinstance(value, Exception):
                raise value
            return value

        if "OrderRefundIdsList" in query:
            self.calls.append(("OrderRefundIdsList", None))
            return {"order": {"refunds": serve(self._script["OrderRefundIdsList"])}}
        if "OrderRefundIds" in query:
            pages = self._script["OrderRefundIds"]
            idx = self._id_page_cursor_calls
            self._id_page_cursor_calls += 1
            self.calls.append(("OrderRefundIds", variables.get("cursor")))
            if idx >= len(pages):
                raise AssertionError("OrderRefundIds requested past the scripted pages")
            return {"order": {"refunds": serve(pages[idx])}}
        if "RefundTransactionsPage" in query:
            key = (variables["id"], variables.get("cursor"))
            self.calls.append(("RefundTransactionsPage", key))
            return {"refund": {"transactions": serve(self._script["RefundTransactionsPage"][key])}}
        if "RefundLineItemsPage" in query:
            key = (variables["id"], variables.get("cursor"))
            self.calls.append(("RefundLineItemsPage", key))
            return {"refund": {"refundLineItems": serve(self._script["RefundLineItemsPage"][key])}}
        assert "RefundDetail" in query, f"unexpected query: {query[:80]}"
        gid = variables["id"]
        self.calls.append(("RefundDetail", gid))
        detail = serve(self._script["RefundDetail"][gid])
        return {"refund": detail}


def _single_refund_script(refund_id=777101, txn_page=None, line_page=None):
    gid = f"gid://shopify/Refund/{refund_id}"
    return {
        "OrderRefundIds": [_page([_id_node(refund_id)])],
        "RefundDetail": {
            gid: {
                "transactions": txn_page or _page([_txn_node("50.00")]),
                "refundLineItems": line_page or _page([]),
            }
        },
        "RefundTransactionsPage": {},
        "RefundLineItemsPage": {},
    }


# ---------------------------------------------------------------------------
# D1 — 11 refunds: all returned and processed
# ---------------------------------------------------------------------------


def test_eleven_refunds_all_returned_and_processed(shopify_store):
    refund_ids = [777200 + i for i in range(11)]
    script = {
        "OrderRefundIds": [
            _page([_id_node(r) for r in refund_ids[:10]], has_next=True, end_cursor="idcur1"),
            _page([_id_node(refund_ids[10])]),
        ],
        "RefundDetail": {
            f"gid://shopify/Refund/{r}": {
                "transactions": _page([_txn_node("10.00")]),
                "refundLineItems": _page([]),
            }
            for r in refund_ids
        },
        "RefundTransactionsPage": {},
        "RefundLineItemsPage": {},
    }
    client = _ScriptedClient(script)

    out = client.get_order_refunds(ORDER_ID)
    assert [r["id"] for r in out] == refund_ids, "all 11 refunds must be returned, not the first 10"

    assert commands.process_order_paid(shopify_store, _order_payload()).success
    from shopify_connector import tasks

    client._id_page_cursor_calls = 0
    result = tasks._backfill_order_refunds(shopify_store, client, ORDER_ID)
    assert result == {"booked": 11, "fetch_failed": False, "process_errors": 0}
    assert ShopifyRefund.objects.filter(company=shopify_store.company).count() == 11


# ---------------------------------------------------------------------------
# D2 — 11 successful transactions: complete aggregate, not first-10
# ---------------------------------------------------------------------------


def test_eleven_transactions_complete_aggregate():
    gid = "gid://shopify/Refund/777301"
    script = _single_refund_script(
        777301,
        txn_page=_page([_txn_node("10.00") for _ in range(10)], has_next=True, end_cursor="tcur1"),
    )
    script["RefundTransactionsPage"][(gid, "tcur1")] = _page([_txn_node("10.00")])
    client = _ScriptedClient(script)

    out = client.get_order_refunds(ORDER_ID)
    assert len(out[0]["transactions"]) == 11
    assert refund_aggregate_amount(out[0]) == Decimal("110.00"), "aggregate must include page-2 transactions"


# ---------------------------------------------------------------------------
# D3 — zero successful transactions + 51 refund line items: complete fallback
# ---------------------------------------------------------------------------


def test_fifty_one_line_items_complete_fallback_aggregate():
    gid = "gid://shopify/Refund/777401"
    script = _single_refund_script(
        777401,
        txn_page=_page([_txn_node("50.00", status="FAILURE")]),
        line_page=_page([_line_node("1.00") for _ in range(50)], has_next=True, end_cursor="lcur1"),
    )
    script["RefundLineItemsPage"][(gid, "lcur1")] = _page([_line_node("1.00")])
    client = _ScriptedClient(script)

    out = client.get_order_refunds(ORDER_ID)
    assert len(out[0]["refund_line_items"]) == 51
    assert refund_aggregate_amount(out[0]) == Decimal("51.00"), "fallback aggregate must include page-2 line items"


# ---------------------------------------------------------------------------
# D4 + D5 — old parent order booked with full history; exact re-run idempotent
# ---------------------------------------------------------------------------


def test_old_parent_order_full_history_and_idempotent_retry(shopify_store, monkeypatch):
    from shopify_connector import tasks

    refund_ids = [777500 + i for i in range(11)]
    script = {
        "OrderRefundIds": [
            _page([_id_node(r) for r in refund_ids[:10]], has_next=True, end_cursor="idcur1"),
            _page([_id_node(refund_ids[10])]),
        ],
        "RefundDetail": {
            f"gid://shopify/Refund/{r}": {
                "transactions": _page([_txn_node("10.00")]),
                "refundLineItems": _page([]),
            }
            for r in refund_ids
        },
        "RefundTransactionsPage": {},
        "RefundLineItemsPage": {},
    }
    client = _ScriptedClient(script, refunded_orders=[_order_payload(created_at="2025-11-03T08:30:00Z")])
    monkeypatch.setattr(commands, "_admin_client", lambda store: client)

    first = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")
    assert first["status"] == "ok"
    assert first["fetch_failures"] == 0
    assert first["refunds_created"] == 11
    assert ShopifyOrder.objects.filter(company=shopify_store.company).count() == 1, (
        "the months-old parent order must be booked when its refund state changed in the window"
    )
    assert ShopifyRefund.objects.filter(company=shopify_store.company).count() == 11

    orders_before = ShopifyOrder.objects.count()
    refunds_before = ShopifyRefund.objects.count()
    events_before = BusinessEvent.objects.count()
    journals_before = JournalEntry.objects.count()

    # D5: reset the scripted id-page cursor and run the exact catch-up again.
    client._id_page_cursor_calls = 0
    calls_before_second_run = len(client.calls)
    second = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")
    assert len(client.calls) > calls_before_second_run, (
        "run 2 must actually re-fetch the refund history (a skipped fetch would make the no-duplicate assertions vacuous)"
    )
    assert second["status"] == "ok"
    assert second["refunds_created"] == 0

    assert ShopifyOrder.objects.count() == orders_before
    assert ShopifyRefund.objects.count() == refunds_before
    assert BusinessEvent.objects.count() == events_before, "a second catch-up must not duplicate any event"
    assert JournalEntry.objects.count() == journals_before, "a second catch-up must not duplicate any journal"


# ---------------------------------------------------------------------------
# D6 — failure on refund page 2: nothing partial is processed
# ---------------------------------------------------------------------------


def test_refund_page_two_failure_returns_no_partial(shopify_store):
    from shopify_connector import tasks

    script = {
        "OrderRefundIds": [
            _page([_id_node(777600 + i) for i in range(10)], has_next=True, end_cursor="idcur1"),
            ShopifyGraphQLError("boom on page 2"),
        ],
        "RefundDetail": {},
        "RefundTransactionsPage": {},
        "RefundLineItemsPage": {},
    }
    client = _ScriptedClient(script)

    with pytest.raises(ShopifyGraphQLError):
        client.get_order_refunds(ORDER_ID)

    assert commands.process_order_paid(shopify_store, _order_payload()).success
    client._id_page_cursor_calls = 0
    result = tasks._backfill_order_refunds(shopify_store, client, ORDER_ID)
    assert result["fetch_failed"] is True
    assert result["booked"] == 0
    assert ShopifyRefund.objects.filter(company=shopify_store.company).count() == 0, (
        "a partial refund list must never be processed as complete"
    )


# ---------------------------------------------------------------------------
# D7 — failure on a transactions page is visible in the leg result
# ---------------------------------------------------------------------------


def test_transaction_page_failure_makes_leg_nonsuccess(shopify_store, monkeypatch):
    from shopify_connector import tasks

    gid = "gid://shopify/Refund/777701"
    script = _single_refund_script(
        777701,
        txn_page=_page([_txn_node("10.00")], has_next=True, end_cursor="tcur1"),
    )
    script["RefundTransactionsPage"][(gid, "tcur1")] = ShopifyGraphQLError("transactions page 2 failed")
    client = _ScriptedClient(script, refunded_orders=[_order_payload()])
    monkeypatch.setattr(commands, "_admin_client", lambda store: client)

    result = tasks._sync_refunds(shopify_store, "2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z")
    assert result["status"] == "error", "an incomplete refund fetch must be a structured non-success"
    assert result["fetch_failures"] == 1
    assert ShopifyRefund.objects.filter(company=shopify_store.company).count() == 0


# ---------------------------------------------------------------------------
# D8 — a repeated cursor fails loudly instead of looping
# ---------------------------------------------------------------------------


def test_repeated_cursor_fails_loudly():
    gid = "gid://shopify/Refund/777801"
    script = _single_refund_script(
        777801,
        txn_page=_page([_txn_node("10.00")], has_next=True, end_cursor="same"),
    )
    script["RefundTransactionsPage"][(gid, "same")] = _page([_txn_node("10.00")], has_next=True, end_cursor="same")
    client = _ScriptedClient(script)

    with pytest.raises(ShopifyGraphQLIncomplete, match="cursor repeated"):
        client.get_order_refunds(ORDER_ID)


def test_order_vanishing_mid_pagination_fails_loudly():
    """An order that answered refunds page 1 but nulls on page 2 must not
    silently return the collected prefix as complete."""
    script = {
        "OrderRefundIds": [
            _page([_id_node(777950 + i) for i in range(10)], has_next=True, end_cursor="idcur1"),
            None,  # order: null on page 2
        ],
        "RefundDetail": {},
        "RefundTransactionsPage": {},
        "RefundLineItemsPage": {},
    }
    client = _ScriptedClient(script)

    def execute_with_null_order(query, variables=None, allow_partial=False):
        variables = variables or {}
        if "OrderRefundIds" in query and variables.get("cursor") == "idcur1":
            return {"order": None}
        return _ScriptedClient.execute(client, query, variables, allow_partial)

    client.execute = execute_with_null_order
    with pytest.raises(ShopifyGraphQLIncomplete, match="vanished mid-pagination"):
        client.get_order_refunds(ORDER_ID)


def test_has_next_without_end_cursor_fails_loudly():
    script = {
        "OrderRefundIds": [_page([_id_node(777901)], has_next=True, end_cursor=None)],
        "RefundDetail": {},
        "RefundTransactionsPage": {},
        "RefundLineItemsPage": {},
    }
    client = _ScriptedClient(script)
    with pytest.raises(ShopifyGraphQLIncomplete, match="hasNextPage without endCursor"):
        client.get_order_refunds(ORDER_ID)


# ---------------------------------------------------------------------------
# Schema-form tolerance — list-form fallback stays complete
# ---------------------------------------------------------------------------


def test_list_form_fallback_is_complete_and_cached():
    refund_ids = [778001 + i for i in range(12)]
    script = {
        "OrderRefundIds": [ShopifyGraphQLError("Field 'pageInfo' doesn't exist")],
        "OrderRefundIdsList": [_id_node(r) for r in refund_ids],
        "RefundDetail": {
            f"gid://shopify/Refund/{r}": {
                "transactions": _page([_txn_node("1.00")]),
                "refundLineItems": _page([]),
            }
            for r in refund_ids
        },
        "RefundTransactionsPage": {},
        "RefundLineItemsPage": {},
    }
    client = _ScriptedClient(script)

    out = client.get_order_refunds(ORDER_ID)
    assert [r["id"] for r in out] == refund_ids, "list-form fallback must return the complete uncapped list"
    assert client._refunds_list_form is True

    # Second order skips the failed connection attempt entirely.
    calls_before = len([c for c in client.calls if c[0] == "OrderRefundIds"])
    client.get_order_refunds(ORDER_ID)
    calls_after = len([c for c in client.calls if c[0] == "OrderRefundIds"])
    assert calls_after == calls_before, "the discovered list form must be cached on the client"


# ---------------------------------------------------------------------------
# D9 — network reads stay outside any lock-holding transaction scope
# ---------------------------------------------------------------------------


def test_network_reads_outside_transactional_scope(shopify_store):
    from shopify_connector import tasks

    client = _ScriptedClient(_single_refund_script(778101))
    assert commands.process_order_paid(shopify_store, _order_payload()).success

    baseline_depth = len(db_connection.savepoint_ids)
    client.execute_depths = []
    result = tasks._backfill_order_refunds(shopify_store, client, ORDER_ID)
    assert result["booked"] == 1
    assert client.execute_depths, "the scripted client must have served pages"
    assert all(d == baseline_depth for d in client.execute_depths), (
        "every Shopify network read must happen OUTSIDE the per-refund "
        "transaction scope (and outside any admission lock) — a fetch inside "
        "an atomic block would hold locks across network I/O"
    )


# ---------------------------------------------------------------------------
# D10 — normal one-page behavior unchanged (exact query count)
# ---------------------------------------------------------------------------


def test_single_page_order_uses_two_queries_and_same_shape(shopify_store):
    client = _ScriptedClient(_single_refund_script(778201, line_page=_page([_line_node("50.00")])))

    out = client.get_order_refunds(ORDER_ID)
    assert [c[0] for c in client.calls] == ["OrderRefundIds", "RefundDetail"]
    assert out == [
        {
            "id": 778201,
            "order_id": ORDER_ID,
            "created_at": "2026-04-29T10:00:00Z",
            "note": "",
            "transactions": [{"kind": "refund", "status": "success", "amount": "50.00"}],
            "refund_line_items": [
                {
                    "quantity": 1,
                    "restock_type": "return",
                    "subtotal": "50.00",
                    "line_item": {"sku": "SKU-1", "title": "Item"},
                }
            ],
        }
    ]
