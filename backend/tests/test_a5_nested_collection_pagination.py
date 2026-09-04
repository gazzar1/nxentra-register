# tests/test_a5_nested_collection_pagination.py
"""
A5 nested-collection pagination — complete order line-item evidence and
complete product variant catalogs, or a loud failure.

Before the fix, the GraphQL order queries selected
`lineItems(first: 50)` with no pagination and no pageInfo — an order with
more than 50 line items yielded exactly the first 50 with no warning,
counter, or error anywhere (the stored `line_items` evidence and
NON_STOCK item auto-provisioning silently truncated) — and
`iter_product_pages` selected `variants(first: 60)` with pageInfo used
only to LOG a private warning while dropping every variant past the
first page.

After the fix (pinned to the official Admin GraphQL 2026-04 schema,
where `Order.lineItems` is `LineItemConnection!` and `Product.variants`
is `ProductVariantConnection!` — both true cursor connections):
- both order queries and the products query request
  `pageInfo { hasNextPage endCursor }` on the nested connection;
- a parent whose nested first page reports hasNextPage is drained to
  exhaustion with per-parent overflow queries (OrderLineItemsPage /
  ProductVariantsPage), which never use allow_partial;
- ShopifyGraphQLIncomplete is raised on an invalid shape, a repeated
  cursor, hasNextPage without endCursor, or the parent vanishing
  mid-fetch — a truncated nested list is never returned as complete;
- at or below the page size, behavior and query count are unchanged.

Money-truth (unchanged by this fix): the Shopify invoice is built from
order-level totals, never by summing line items — what this fix
completes is the order's line-item EVIDENCE and the variant CATALOG.
"""

import pytest

from shopify_connector.graphql_client import (
    ShopifyAdminClient,
    ShopifyGraphQLError,
    ShopifyGraphQLIncomplete,
)

SHOP_DOMAIN = "a5-nested-pagination.myshopify.com"
ORDER_ID = 9300001
PRODUCT_ID = 9400001


def _page(nodes, has_next=False, end_cursor=None):
    return {"pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor}, "nodes": nodes}


def _li_node(i):
    return {
        "sku": f"SKU-{i}",
        "title": f"Item {i}",
        "quantity": 1,
        "originalUnitPriceSet": {"shopMoney": {"amount": "10.00"}},
        "variant": {"legacyResourceId": str(100000 + i)},
        "product": {"legacyResourceId": str(200000 + i)},
    }


def _order_node(order_id=ORDER_ID, line_items_conn=None):
    return {
        "legacyResourceId": str(order_id),
        "name": "#3001",
        "createdAt": "2026-08-30T10:00:00Z",
        "cancelledAt": None,
        "test": False,
        "displayFinancialStatus": "PAID",
        "currencyCode": "EGP",
        "paymentGatewayNames": ["manual"],
        "totalPriceSet": {"shopMoney": {"amount": "500.00"}},
        "subtotalPriceSet": {"shopMoney": {"amount": "500.00"}},
        "totalTaxSet": {"shopMoney": {"amount": "0.00"}},
        "totalDiscountsSet": {"shopMoney": {"amount": "0.00"}},
        "totalShippingPriceSet": {"shopMoney": {"amount": "0"}},
        "customer": None,
        "lineItems": line_items_conn if line_items_conn is not None else _page([_li_node(1)]),
    }


def _variant_node(i):
    return {
        "legacyResourceId": str(300000 + i),
        "sku": f"VSKU-{i}",
        "title": f"Variant {i}",
        "price": "25.00",
        "inventoryItem": {"legacyResourceId": str(400000 + i), "unitCost": {"amount": "7.00"}},
    }


def _product_node(product_id=PRODUCT_ID, variants_conn=None):
    return {
        "legacyResourceId": str(product_id),
        "title": "Widget",
        "productType": "Widgets",
        "featuredMedia": None,
        "variants": variants_conn if variants_conn is not None else _page([_variant_node(1)]),
    }


class _ScriptedClient(ShopifyAdminClient):
    """Real client with `execute` served from scripted responses.

    script keys:
      "Orders"              -> list of orders-connection pages (served in order)
      "Products"            -> list of products-connection pages (served in order)
      "OrderLineItemsPage"  -> {(gid, cursor): lineItems-page-or-Exception}
      "ProductVariantsPage" -> {(gid, cursor): variants-page-or-Exception}
    An overflow-page value may also be the sentinel string "VANISH"
    (parent comes back null) or an Exception instance (raised on serve).
    calls records (query_name, key, allow_partial); queries records full
    query text so tests can pin the exact field shapes sent to Shopify.
    """

    def __init__(self, script):
        super().__init__(SHOP_DOMAIN, "test-token")
        self._script = script
        self._orders_served = 0
        self._products_served = 0
        self.calls = []
        self.queries = []

    def execute(self, query, variables=None, allow_partial=False):
        variables = variables or {}
        self.queries.append(query)

        def serve(value):
            if isinstance(value, Exception):
                raise value
            return value

        if "OrderLineItemsPage" in query:
            key = (variables["id"], variables.get("cursor"))
            self.calls.append(("OrderLineItemsPage", key, allow_partial))
            value = serve(self._script["OrderLineItemsPage"][key])
            if value == "VANISH":
                return {"order": None}
            return {"order": {"lineItems": value}}
        if "ProductVariantsPage" in query:
            key = (variables["id"], variables.get("cursor"))
            self.calls.append(("ProductVariantsPage", key, allow_partial))
            value = serve(self._script["ProductVariantsPage"][key])
            if value == "VANISH":
                return {"product": None}
            return {"product": {"variants": value}}
        if "query Products" in query:
            self.calls.append(("Products", self._products_served, allow_partial))
            page = self._script["Products"][self._products_served]
            self._products_served += 1
            return {"products": serve(page)}
        assert "query Orders" in query, f"unexpected query: {query[:80]}"
        self.calls.append(("Orders", self._orders_served, allow_partial))
        page = self._script["Orders"][self._orders_served]
        self._orders_served += 1
        return {"orders": serve(page)}


def _orders_script(order_nodes, line_pages=None):
    return {
        "Orders": [_page(order_nodes)],
        "Products": [],
        "OrderLineItemsPage": line_pages or {},
        "ProductVariantsPage": {},
    }


def _products_script(product_nodes, variant_pages=None):
    return {
        "Orders": [],
        "Products": [_page(product_nodes)],
        "OrderLineItemsPage": {},
        "ProductVariantsPage": variant_pages or {},
    }


# ---------------------------------------------------------------------------
# Query-shape pins — the exact 2026-04 field forms
# ---------------------------------------------------------------------------


def test_order_queries_request_line_items_page_info():
    """Both order queries select lineItems as a connection WITH pageInfo
    (the pre-fix queries omitted pageInfo, making overflow undetectable),
    and the outer search keeps allow_partial=True for the documented
    customer-field tolerance."""
    for iterate in (
        lambda c: list(c.iter_orders("2026-08-25T00:00:00Z", "2026-09-01T00:00:00Z")),
        lambda c: list(c.iter_refunded_orders("2026-08-25T00:00:00Z", "2026-09-01T00:00:00Z")),
    ):
        client = _ScriptedClient(_orders_script([_order_node()]))
        iterate(client)
        outer_query = client.queries[0]
        assert "lineItems(first:" in outer_query
        assert outer_query.count("pageInfo { hasNextPage endCursor }") == 2, (
            "both the orders connection AND the nested lineItems connection must request pageInfo"
        )
        assert client.calls[0][2] is True, "the outer order search keeps allow_partial=True"


def test_overflow_page_queries_select_the_same_fields():
    """The overflow page queries must select the SAME node fields as the
    outer queries' nested selections — a field dropped from a page query
    would silently degrade overflow items (e.g. line items past 50 losing
    their variant_id), and the scripted client cannot catch that because
    it serves canned nodes regardless of the query text."""
    from shopify_connector.graphql_client import (
        _ORDER_LINE_ITEMS_PAGE_QUERY,
        _PRODUCT_VARIANTS_PAGE_QUERY,
        LINE_ITEMS_PER_ORDER,
        VARIANTS_PER_PRODUCT,
    )

    for required in (
        "order(id: $id)",
        f"lineItems(first: {LINE_ITEMS_PER_ORDER}, after: $cursor)",
        "pageInfo { hasNextPage endCursor }",
        "sku",
        "title",
        "quantity",
        "originalUnitPriceSet { shopMoney { amount } }",
        "variant { legacyResourceId }",
        "product { legacyResourceId }",
    ):
        assert required in _ORDER_LINE_ITEMS_PAGE_QUERY, f"line-items page query must select {required!r}"

    for required in (
        "product(id: $id)",
        f"variants(first: {VARIANTS_PER_PRODUCT}, after: $cursor)",
        "pageInfo { hasNextPage endCursor }",
        "legacyResourceId",
        "sku",
        "title",
        "price",
        "inventoryItem { legacyResourceId unitCost { amount } }",
    ):
        assert required in _PRODUCT_VARIANTS_PAGE_QUERY, f"variants page query must select {required!r}"


def test_products_query_requests_variants_end_cursor():
    """The products query's nested variants pageInfo must carry endCursor
    (the pre-fix query requested hasNextPage only — enough to log, not
    enough to paginate)."""
    client = _ScriptedClient(_products_script([_product_node()]))
    list(client.iter_product_pages())
    outer_query = client.queries[0]
    assert "variants(first:" in outer_query
    assert outer_query.count("pageInfo { hasNextPage endCursor }") == 2


# ---------------------------------------------------------------------------
# Overflow drains to exhaustion — 51 line items, 61 variants
# ---------------------------------------------------------------------------


def test_51_line_order_round_trips_all_51():
    gid = f"gid://shopify/Order/{ORDER_ID}"
    first_page = _page([_li_node(i) for i in range(1, 51)], has_next=True, end_cursor="licur1")
    script = _orders_script(
        [_order_node(line_items_conn=first_page)],
        line_pages={(gid, "licur1"): _page([_li_node(51)])},
    )
    client = _ScriptedClient(script)

    orders = list(client.iter_orders("2026-08-25T00:00:00Z", "2026-09-01T00:00:00Z"))

    assert len(orders) == 1
    line_items = orders[0]["line_items"]
    assert len(line_items) == 51, "all 51 line items must round-trip, not a truncated prefix"
    assert line_items[50] == {
        "sku": "SKU-51",
        "title": "Item 51",
        "quantity": 1,
        "price": "10.00",
        "variant_id": 100051,
        "product_id": 200051,
    }
    page_query = next(q for q in client.queries if "OrderLineItemsPage" in q)
    assert "after: $cursor" in page_query
    page_calls = [c for c in client.calls if c[0] == "OrderLineItemsPage"]
    assert page_calls == [("OrderLineItemsPage", (gid, "licur1"), False)], (
        "exactly one overflow page, fetched WITHOUT allow_partial"
    )
    # Order-level money fields come from the order node, untouched by the drain.
    assert orders[0]["total_price"] == "500.00"
    assert orders[0]["subtotal_price"] == "500.00"


def test_refunded_orders_drain_line_items_the_same_way():
    """The B-leg parent read (iter_refunded_orders) uses the same drain —
    a months-old refunded parent's line-item evidence is complete too."""
    gid = f"gid://shopify/Order/{ORDER_ID}"
    first_page = _page([_li_node(i) for i in range(1, 51)], has_next=True, end_cursor="licurB")
    script = _orders_script(
        [_order_node(line_items_conn=first_page)],
        line_pages={(gid, "licurB"): _page([_li_node(51)])},
    )
    client = _ScriptedClient(script)

    orders = list(client.iter_refunded_orders("2026-08-25T00:00:00Z", "2026-09-01T00:00:00Z"))
    assert len(orders[0]["line_items"]) == 51


def test_61_variant_product_round_trips_all_61():
    gid = f"gid://shopify/Product/{PRODUCT_ID}"
    first_page = _page([_variant_node(i) for i in range(1, 61)], has_next=True, end_cursor="vcur1")
    script = _products_script(
        [_product_node(variants_conn=first_page)],
        variant_pages={(gid, "vcur1"): _page([_variant_node(61)])},
    )
    client = _ScriptedClient(script)

    pages = list(client.iter_product_pages())

    assert len(pages) == 1
    products, cost_map = pages[0]
    assert len(products[0]["variants"]) == 61, "all 61 variants must round-trip, not a truncated prefix"
    assert products[0]["variants"][60] == {
        "id": 300061,
        "sku": "VSKU-61",
        "title": "Variant 61",
        "price": "25.00",
        "inventory_item_id": 400061,
    }
    assert cost_map[400061] == "7.00", "the drained page's unit costs must enter the cost map"
    page_query = next(q for q in client.queries if "ProductVariantsPage" in q)
    assert "after: $cursor" in page_query
    page_calls = [c for c in client.calls if c[0] == "ProductVariantsPage"]
    assert page_calls == [("ProductVariantsPage", (gid, "vcur1"), False)], (
        "exactly one overflow page, fetched WITHOUT allow_partial"
    )


def test_multi_page_drain_follows_cursors():
    """Three line-item pages (50 + 50 + 1) drain in cursor order."""
    gid = f"gid://shopify/Order/{ORDER_ID}"
    first_page = _page([_li_node(i) for i in range(1, 51)], has_next=True, end_cursor="c1")
    script = _orders_script(
        [_order_node(line_items_conn=first_page)],
        line_pages={
            (gid, "c1"): _page([_li_node(i) for i in range(51, 101)], has_next=True, end_cursor="c2"),
            (gid, "c2"): _page([_li_node(101)]),
        },
    )
    client = _ScriptedClient(script)

    orders = list(client.iter_orders("2026-08-25T00:00:00Z", "2026-09-01T00:00:00Z"))
    assert len(orders[0]["line_items"]) == 101
    assert [c[1] for c in client.calls if c[0] == "OrderLineItemsPage"] == [(gid, "c1"), (gid, "c2")]


def test_two_overflowing_orders_drain_independently():
    """Two overflowing parents in ONE outer page drain independently —
    deliberately using the SAME cursor string on both, so any
    seen-cursor state leaking between parents would false-positive as
    'cursor repeated'."""
    id_a, id_b = 9300010, 9300011
    gid_a = f"gid://shopify/Order/{id_a}"
    gid_b = f"gid://shopify/Order/{id_b}"
    node_a = _order_node(id_a, _page([_li_node(i) for i in range(1, 51)], has_next=True, end_cursor="sharedcur"))
    node_b = _order_node(id_b, _page([_li_node(i) for i in range(1, 51)], has_next=True, end_cursor="sharedcur"))
    script = _orders_script(
        [node_a, node_b],
        line_pages={
            (gid_a, "sharedcur"): _page([_li_node(51)]),
            (gid_b, "sharedcur"): _page([_li_node(51), _li_node(52)]),
        },
    )
    client = _ScriptedClient(script)

    orders = list(client.iter_orders("2026-08-25T00:00:00Z", "2026-09-01T00:00:00Z"))

    assert [len(o["line_items"]) for o in orders] == [51, 52]
    assert [c[1] for c in client.calls if c[0] == "OrderLineItemsPage"] == [
        (gid_a, "sharedcur"),
        (gid_b, "sharedcur"),
    ]


# ---------------------------------------------------------------------------
# Anomalies fail loudly — never a silently truncated list
# ---------------------------------------------------------------------------


def test_line_items_page_failure_fails_the_read():
    gid = f"gid://shopify/Order/{ORDER_ID}"
    first_page = _page([_li_node(1)], has_next=True, end_cursor="licur1")
    script = _orders_script(
        [_order_node(line_items_conn=first_page)],
        line_pages={(gid, "licur1"): ShopifyGraphQLError("boom on line-items page 2")},
    )
    client = _ScriptedClient(script)
    with pytest.raises(ShopifyGraphQLError):
        list(client.iter_orders("2026-08-25T00:00:00Z", "2026-09-01T00:00:00Z"))


def test_line_items_repeated_cursor_fails_loudly():
    gid = f"gid://shopify/Order/{ORDER_ID}"
    first_page = _page([_li_node(1)], has_next=True, end_cursor="same")
    script = _orders_script(
        [_order_node(line_items_conn=first_page)],
        line_pages={(gid, "same"): _page([_li_node(2)], has_next=True, end_cursor="same")},
    )
    client = _ScriptedClient(script)
    with pytest.raises(ShopifyGraphQLIncomplete, match="cursor repeated"):
        list(client.iter_orders("2026-08-25T00:00:00Z", "2026-09-01T00:00:00Z"))


def test_line_items_has_next_without_end_cursor_fails_loudly():
    first_page = _page([_li_node(1)], has_next=True, end_cursor=None)
    script = _orders_script([_order_node(line_items_conn=first_page)])
    client = _ScriptedClient(script)
    with pytest.raises(ShopifyGraphQLIncomplete, match="hasNextPage without endCursor"):
        list(client.iter_orders("2026-08-25T00:00:00Z", "2026-09-01T00:00:00Z"))


def test_order_vanishing_mid_line_items_fetch_fails_loudly():
    gid = f"gid://shopify/Order/{ORDER_ID}"
    first_page = _page([_li_node(1)], has_next=True, end_cursor="licur1")
    script = _orders_script(
        [_order_node(line_items_conn=first_page)],
        line_pages={(gid, "licur1"): "VANISH"},
    )
    client = _ScriptedClient(script)
    with pytest.raises(ShopifyGraphQLIncomplete, match="vanished mid-fetch"):
        list(client.iter_orders("2026-08-25T00:00:00Z", "2026-09-01T00:00:00Z"))


def test_absent_line_items_connection_fails_loudly():
    """A null/absent lineItems connection on an order node is an invalid
    shape, not an empty order: pre-fix code silently yielded the order
    with zero line-item evidence."""
    node = _order_node()
    node["lineItems"] = None
    client = _ScriptedClient(_orders_script([node]))
    with pytest.raises(ShopifyGraphQLIncomplete, match="Invalid lineItems shape"):
        list(client.iter_orders("2026-08-25T00:00:00Z", "2026-09-01T00:00:00Z"))


def test_variants_repeated_cursor_fails_loudly():
    gid = f"gid://shopify/Product/{PRODUCT_ID}"
    first_page = _page([_variant_node(1)], has_next=True, end_cursor="same")
    script = _products_script(
        [_product_node(variants_conn=first_page)],
        variant_pages={(gid, "same"): _page([_variant_node(2)], has_next=True, end_cursor="same")},
    )
    client = _ScriptedClient(script)
    with pytest.raises(ShopifyGraphQLIncomplete, match="cursor repeated"):
        list(client.iter_product_pages())


def test_variants_has_next_without_end_cursor_fails_loudly():
    first_page = _page([_variant_node(1)], has_next=True, end_cursor=None)
    script = _products_script([_product_node(variants_conn=first_page)])
    client = _ScriptedClient(script)
    with pytest.raises(ShopifyGraphQLIncomplete, match="hasNextPage without endCursor"):
        list(client.iter_product_pages())


def test_product_vanishing_mid_variants_fetch_fails_loudly():
    gid = f"gid://shopify/Product/{PRODUCT_ID}"
    first_page = _page([_variant_node(1)], has_next=True, end_cursor="vcur1")
    script = _products_script(
        [_product_node(variants_conn=first_page)],
        variant_pages={(gid, "vcur1"): "VANISH"},
    )
    client = _ScriptedClient(script)
    with pytest.raises(ShopifyGraphQLIncomplete, match="vanished mid-fetch"):
        list(client.iter_product_pages())


def test_variants_page_failure_fails_the_read():
    gid = f"gid://shopify/Product/{PRODUCT_ID}"
    first_page = _page([_variant_node(1)], has_next=True, end_cursor="vcur1")
    script = _products_script(
        [_product_node(variants_conn=first_page)],
        variant_pages={(gid, "vcur1"): ShopifyGraphQLError("boom on variants page 2")},
    )
    client = _ScriptedClient(script)
    with pytest.raises(ShopifyGraphQLError):
        list(client.iter_product_pages())


def test_no_truncation_warning_remains(caplog):
    """The pre-fix 'extra variants not synced' warning is gone — overflow
    is drained, not logged away."""
    gid = f"gid://shopify/Product/{PRODUCT_ID}"
    first_page = _page([_variant_node(i) for i in range(1, 61)], has_next=True, end_cursor="vcur1")
    script = _products_script(
        [_product_node(variants_conn=first_page)],
        variant_pages={(gid, "vcur1"): _page([_variant_node(61)])},
    )
    client = _ScriptedClient(script)
    with caplog.at_level("WARNING"):
        pages = list(client.iter_product_pages())
    assert len(pages[0][0][0]["variants"]) == 61
    assert "extra variants not synced" not in caplog.text


# ---------------------------------------------------------------------------
# At or below the page size: behavior and query count unchanged
# ---------------------------------------------------------------------------


def test_order_at_cap_uses_single_query_and_same_shape():
    node = _order_node(line_items_conn=_page([_li_node(i) for i in range(1, 3)]))
    client = _ScriptedClient(_orders_script([node]))

    orders = list(client.iter_orders("2026-08-25T00:00:00Z", "2026-09-01T00:00:00Z"))

    assert [c[0] for c in client.calls] == ["Orders"], "no overflow query below the page size"
    assert orders == [
        {
            "id": ORDER_ID,
            "name": "#3001",
            "order_number": "3001",
            "created_at": "2026-08-30T10:00:00Z",
            "cancelled_at": None,
            "test": False,
            "currency": "EGP",
            "financial_status": "paid",
            "payment_gateway_names": ["manual"],
            "total_price": "500.00",
            "subtotal_price": "500.00",
            "total_tax": "0.00",
            "total_discounts": "0.00",
            "shipping_lines": [],
            "customer": None,
            "line_items": [
                {
                    "sku": "SKU-1",
                    "title": "Item 1",
                    "quantity": 1,
                    "price": "10.00",
                    "variant_id": 100001,
                    "product_id": 200001,
                },
                {
                    "sku": "SKU-2",
                    "title": "Item 2",
                    "quantity": 1,
                    "price": "10.00",
                    "variant_id": 100002,
                    "product_id": 200002,
                },
            ],
        }
    ]


def test_product_below_cap_uses_single_query_and_same_shape():
    client = _ScriptedClient(_products_script([_product_node()]))

    pages = list(client.iter_product_pages())

    assert [c[0] for c in client.calls] == ["Products"], "no overflow query below the page size"
    products, cost_map = pages[0]
    assert products == [
        {
            "id": PRODUCT_ID,
            "title": "Widget",
            "product_type": "Widgets",
            "images": [],
            "variants": [
                {
                    "id": 300001,
                    "sku": "VSKU-1",
                    "title": "Variant 1",
                    "price": "25.00",
                    "inventory_item_id": 400001,
                }
            ],
        }
    ]
    assert cost_map == {400001: "7.00"}
