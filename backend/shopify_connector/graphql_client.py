# shopify_connector/graphql_client.py
"""
Single Shopify Admin API client for Nxentra Sync — GraphQL only.

Shopify made the REST Admin API legacy on 2024-10-01, and public apps
created on or after 2025-04-01 (Nxentra Sync is one) must use the GraphQL
Admin API exclusively — App Store review checks this. Every Admin API data
read in the connector goes through ShopifyAdminClient below; nothing else
in the codebase may call https://<shop>/admin/api/ directly.

The only Shopify HTTP calls allowed outside this module are the OAuth
token endpoints (https://<shop>/admin/oauth/access_token) in commands.py —
those have no GraphQL equivalent by design — and CDN image downloads.

Adapter convention: every helper returns dicts shaped like the legacy REST
payloads (snake_case keys, numeric ids from legacyResourceId) so the
downstream processing code — which must keep accepting REST-shaped webhook
payloads from Shopify anyway — works unchanged for both webhook and
GraphQL-sourced data.
"""

import logging
import time
from datetime import UTC, datetime

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Shopify Admin API version. Keep in sync with shopify.app.toml's
# [webhooks] api_version. Bumped 2026-06-01 from 2025-01 (past its 12-month
# support window) ahead of App Store resubmission. Override via Django
# settings.SHOPIFY_API_VERSION when testing against a different release.
SHOPIFY_API_VERSION = getattr(settings, "SHOPIFY_API_VERSION", "2026-04")

# Page sizes are chosen to keep each query's calculated cost comfortably
# under Shopify's 1000-point single-query ceiling (nested connections
# multiply: products × variants, orders × line_items). These are page
# sizes only, never completeness caps: a nested lineItems/variants
# connection reporting hasNextPage is drained per-parent to exhaustion
# (A5 nested-collection pagination — the pre-fix code silently truncated
# an order's line-item evidence at 50 and a product's variants at 60).
PRODUCTS_PAGE_SIZE = 12
VARIANTS_PER_PRODUCT = 60
ORDERS_PAGE_SIZE = 10
LINE_ITEMS_PER_ORDER = 50

# Fulfillment backfill (A125): pulled per-order in a dedicated query so the
# nested fulfillments × fulfillmentLineItems cost (≈ FULFILLMENTS_PER_ORDER ×
# (1 + FULFILLMENT_LINE_ITEMS) ≈ 510) never compounds with the orders-page cost
# and breaches Shopify's 1000-point single-query ceiling for large orders.
FULFILLMENTS_PER_ORDER = 10
FULFILLMENT_LINE_ITEMS = 50
# A159: refunds are pulled per-order (never nested in iter_orders) for the
# same 1000-point-ceiling reason as fulfillments. Order.refunds itself is a
# plain list field in 2026-04 (queried uncapped — its `first` argument
# TRUNCATES); the two per-refund connections below are cursor-paginated, so
# these are page sizes, never completeness caps.
REFUND_TRANSACTIONS = 10
REFUND_LINE_ITEMS = 50

_MAX_THROTTLE_RETRIES = 5

# Per-parent overflow pages for the nested connections above. Issued only
# when a parent's first (nested) page reports hasNextPage — the common
# ≤-page-size case costs no extra query.
_ORDER_LINE_ITEMS_PAGE_QUERY = f"""
query OrderLineItemsPage($id: ID!, $cursor: String) {{
  order(id: $id) {{
    lineItems(first: {LINE_ITEMS_PER_ORDER}, after: $cursor) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        sku
        title
        quantity
        originalUnitPriceSet {{ shopMoney {{ amount }} }}
        variant {{ legacyResourceId }}
        product {{ legacyResourceId }}
      }}
    }}
  }}
}}
"""

_PRODUCT_VARIANTS_PAGE_QUERY = f"""
query ProductVariantsPage($id: ID!, $cursor: String) {{
  product(id: $id) {{
    variants(first: {VARIANTS_PER_PRODUCT}, after: $cursor) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        legacyResourceId
        sku
        title
        price
        inventoryItem {{ legacyResourceId unitCost {{ amount }} }}
      }}
    }}
  }}
}}
"""


class ShopifyGraphQLError(requests.RequestException):
    """GraphQL-level error (HTTP 200 with an errors array)."""

    def __init__(self, message: str, errors: list | None = None):
        super().__init__(message)
        self.errors = errors or []


class ShopifyGraphQLDenied(ShopifyGraphQLError):
    """
    Access denied at the GraphQL layer (missing scope, protected customer
    data not approved, Shopify Payments not exposed). The REST equivalent
    was an HTTP 401/403/404 — _shopify_access_denied() in commands.py
    recognises this class the same way so callers keep their graceful
    "nothing to sync" paths.
    """

    access_denied = True


class ShopifyGraphQLIncomplete(ShopifyGraphQLError):
    """
    Complete data could not be assembled for a financially load-bearing
    read (a page failed, a connection page had an invalid shape, a cursor
    repeated, or hasNextPage was reported without an endCursor). Callers
    must treat this as a failed fetch — a partial payload is NEVER
    returned in its place, because a truncated refund list silently
    understates the refund aggregate.
    """


def _gid_tail(gid: str | None) -> int | None:
    """'gid://shopify/Order/123' -> 123. None-safe."""
    if not gid:
        return None
    try:
        return int(str(gid).rsplit("/", 1)[-1])
    except (ValueError, TypeError):
        return None


def _iso_for_search(value: str) -> str:
    """
    Normalize an ISO datetime to the second-precision UTC `...T19:37:58Z`
    form Shopify's search syntax documents. Django's isoformat() carries
    microseconds and a +00:00 offset, which the search parser is not
    documented to accept — a silently-ignored filter would make order
    backfill fetch nothing (or everything).
    """
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return value
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(node: dict | None) -> str:
    """MoneyV2 (or a {shopMoney} MoneyBag) -> amount string, '0' default."""
    if not node:
        return "0"
    if "shopMoney" in node:
        node = node.get("shopMoney") or {}
    return str(node.get("amount", "0"))


class ShopifyAdminClient:
    """
    Thin GraphQL Admin API client bound to one store + access token.

    Raises requests.HTTPError for transport-level failures (401/403/404/5xx
    — same semantics the REST calls had), ShopifyGraphQLDenied when Shopify
    answers 200 but refuses the data, and ShopifyGraphQLError for any other
    GraphQL error. THROTTLED responses are retried with backoff.
    """

    def __init__(self, shop_domain: str, access_token: str):
        self.shop_domain = shop_domain
        self.access_token = access_token

    @property
    def endpoint(self) -> str:
        return f"https://{self.shop_domain}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

    def execute(self, query: str, variables: dict | None = None, allow_partial: bool = False) -> dict:
        """
        POST one GraphQL query, return the `data` dict.

        allow_partial: when Shopify returns data alongside errors (e.g. a
        protected field it won't expose), log and return the partial data
        instead of raising — used for order reads where a denied customer
        field must not sink the whole sync.
        """
        attempt = 0
        while True:
            resp = requests.post(
                self.endpoint,
                json={"query": query, "variables": variables or {}},
                headers={
                    "X-Shopify-Access-Token": self.access_token,
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()
            errors = body.get("errors") or []
            data = body.get("data")

            if not errors:
                return data or {}

            codes = {(e.get("extensions") or {}).get("code", "") for e in errors}
            messages = "; ".join(str(e.get("message", "")) for e in errors)

            if "THROTTLED" in codes:
                attempt += 1
                if attempt > _MAX_THROTTLE_RETRIES:
                    raise ShopifyGraphQLError(f"Shopify throttled the query repeatedly: {messages}", errors)
                wait = self._throttle_wait(body)
                logger.info("Shopify GraphQL throttled for %s — retrying in %.1fs", self.shop_domain, wait)
                time.sleep(wait)
                continue

            if data and allow_partial and any(v is not None for v in data.values()):
                logger.warning(
                    "Shopify GraphQL partial response for %s (continuing): %s",
                    self.shop_domain,
                    messages,
                )
                return data

            lowered = messages.lower()
            if "ACCESS_DENIED" in codes or "access denied" in lowered or "not approved" in lowered:
                raise ShopifyGraphQLDenied(f"Shopify denied access: {messages}", errors)

            raise ShopifyGraphQLError(f"Shopify GraphQL error: {messages}", errors)

    @staticmethod
    def _throttle_wait(body: dict) -> float:
        try:
            cost = body["extensions"]["cost"]
            requested = float(cost["requestedQueryCost"])
            available = float(cost["throttleStatus"]["currentlyAvailable"])
            restore = float(cost["throttleStatus"]["restoreRate"]) or 50.0
            return max(1.0, (requested - available) / restore)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return 2.0

    def _drain_nested_connection(
        self,
        page_query: str,
        parent_gid: str,
        root_field: str,
        field: str,
        conn: object,
        context: str,
    ) -> list:
        """Drain one parent object's nested connection to exhaustion,
        starting from its already-fetched first page `conn`, and return
        the RAW node dicts (REST-shape mapping stays at the call site).

        Complete-or-loud (the A5 refund-read contract, applied to the
        nested lineItems/variants reads): raises ShopifyGraphQLIncomplete
        on an invalid first-page or overflow-page shape, a repeated
        cursor, hasNextPage without endCursor, or the parent vanishing
        mid-fetch; a failed overflow page propagates from execute() —
        these reads never pass allow_partial, so no partial nested page
        can masquerade as complete.
        """
        if not isinstance(conn, dict) or not isinstance(conn.get("nodes"), list):
            raise ShopifyGraphQLIncomplete(f"Invalid {field} shape for {context} on {self.shop_domain}")
        nodes = list(conn["nodes"])
        seen_cursors: set[str] = set()
        while True:
            page_info = conn.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return nodes
            end_cursor = page_info.get("endCursor")
            if not end_cursor:
                raise ShopifyGraphQLIncomplete(
                    f"{field} page for {context} on {self.shop_domain} reports hasNextPage without endCursor"
                )
            if end_cursor in seen_cursors:
                raise ShopifyGraphQLIncomplete(
                    f"{field} pagination cursor repeated for {context} on {self.shop_domain}"
                )
            seen_cursors.add(end_cursor)
            data = self.execute(page_query, {"id": parent_gid, "cursor": end_cursor})
            parent = data.get(root_field)
            if not isinstance(parent, dict):
                raise ShopifyGraphQLIncomplete(f"{context} on {self.shop_domain} vanished mid-fetch")
            conn = parent.get(field)
            if not isinstance(conn, dict) or not isinstance(conn.get("nodes"), list):
                raise ShopifyGraphQLIncomplete(f"Invalid {field} page shape for {context} on {self.shop_domain}")
            nodes.extend(conn["nodes"])

    # ------------------------------------------------------------------
    # Shop
    # ------------------------------------------------------------------

    def get_shop_currency(self) -> str:
        data = self.execute("query { shop { currencyCode } }")
        return (data.get("shop") or {}).get("currencyCode", "") or ""

    # ------------------------------------------------------------------
    # Locations  (REST shape: GET /locations.json)
    # ------------------------------------------------------------------

    def list_locations(self) -> list[dict]:
        query = """
        query Locations($cursor: String) {
          locations(first: 50, after: $cursor, includeInactive: true) {
            pageInfo { hasNextPage endCursor }
            nodes {
              legacyResourceId
              name
              isActive
              address { address1 address2 city province country }
            }
          }
        }
        """
        locations = []
        cursor = None
        while True:
            data = self.execute(query, {"cursor": cursor})
            conn = data.get("locations") or {}
            for node in conn.get("nodes") or []:
                addr = node.get("address") or {}
                locations.append(
                    {
                        "id": _gid_tail(f"x/{node.get('legacyResourceId')}"),
                        "name": node.get("name", ""),
                        "active": bool(node.get("isActive", True)),
                        "address1": addr.get("address1") or "",
                        "address2": addr.get("address2") or "",
                        "city": addr.get("city") or "",
                        "province": addr.get("province") or "",
                        "country_name": addr.get("country") or "",
                    }
                )
            page = conn.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return locations
            cursor = page.get("endCursor")

    # ------------------------------------------------------------------
    # Products  (REST shape: GET /products.json + /inventory_items.json)
    # ------------------------------------------------------------------

    def iter_product_pages(self):
        """
        Yield (products, cost_map) per page.

        products: REST-shaped dicts {id, title, product_type, images:[{src}],
        variants:[{id, sku, title, price, inventory_item_id}]}.
        cost_map: {inventory_item_id: cost string} — unitCost comes back in
        the same query, replacing the separate REST inventory_items.json call.

        A product's `variants` connection is drained to exhaustion:
        VARIANTS_PER_PRODUCT is a page size, not a completeness cap (the
        pre-fix code logged a warning and dropped variants past the first
        page). Raises ShopifyGraphQLIncomplete on any pagination anomaly —
        a truncated variant list is never yielded as complete.
        """
        query = f"""
        query Products($cursor: String) {{
          products(first: {PRODUCTS_PAGE_SIZE}, after: $cursor) {{
            pageInfo {{ hasNextPage endCursor }}
            nodes {{
              legacyResourceId
              title
              productType
              featuredMedia {{ preview {{ image {{ url }} }} }}
              variants(first: {VARIANTS_PER_PRODUCT}) {{
                pageInfo {{ hasNextPage endCursor }}
                nodes {{
                  legacyResourceId
                  sku
                  title
                  price
                  inventoryItem {{ legacyResourceId unitCost {{ amount }} }}
                }}
              }}
            }}
          }}
        }}
        """
        cursor = None
        while True:
            data = self.execute(query, {"cursor": cursor})
            conn = data.get("products") or {}
            products = []
            cost_map = {}
            for node in conn.get("nodes") or []:
                image_url = ""
                media = node.get("featuredMedia") or {}
                preview = media.get("preview") or {}
                image = preview.get("image") or {}
                if image.get("url"):
                    image_url = image["url"]

                variant_nodes = self._drain_nested_connection(
                    _PRODUCT_VARIANTS_PAGE_QUERY,
                    f"gid://shopify/Product/{node.get('legacyResourceId')}",
                    "product",
                    "variants",
                    node.get("variants"),
                    f"product {node.get('legacyResourceId')}",
                )

                variants = []
                for v in variant_nodes:
                    inv_item = v.get("inventoryItem") or {}
                    inv_item_id = inv_item.get("legacyResourceId")
                    inv_item_id = int(inv_item_id) if inv_item_id else None
                    unit_cost = inv_item.get("unitCost") or {}
                    if inv_item_id and unit_cost.get("amount"):
                        cost_map[inv_item_id] = str(unit_cost["amount"])
                    variants.append(
                        {
                            "id": int(v["legacyResourceId"]),
                            "sku": v.get("sku") or "",
                            "title": v.get("title") or "",
                            "price": str(v.get("price", "0")),
                            "inventory_item_id": inv_item_id,
                        }
                    )

                products.append(
                    {
                        "id": int(node["legacyResourceId"]),
                        "title": node.get("title", ""),
                        "product_type": node.get("productType") or "",
                        "images": [{"src": image_url}] if image_url else [],
                        "variants": variants,
                    }
                )

            yield products, cost_map

            page = conn.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return
            cursor = page.get("endCursor")

    def get_variant_unit_cost(self, variant_id) -> tuple[str, str]:
        """
        Return (cost, currency) for one variant from its inventory item.
        ('0', '') when the variant or cost is missing.
        """
        query = """
        query VariantCost($id: ID!) {
          productVariant(id: $id) {
            inventoryItem { unitCost { amount currencyCode } }
          }
        }
        """
        data = self.execute(query, {"id": f"gid://shopify/ProductVariant/{variant_id}"})
        variant = data.get("productVariant") or {}
        unit_cost = (variant.get("inventoryItem") or {}).get("unitCost") or {}
        return str(unit_cost.get("amount", "0") or "0"), unit_cost.get("currencyCode", "") or ""

    # ------------------------------------------------------------------
    # Orders  (REST shape: GET /orders.json)
    # ------------------------------------------------------------------

    def iter_orders(self, created_at_min: str, created_at_max: str):
        """
        Yield REST-shaped order dicts for the window, oldest first.

        Unlike the legacy REST orders.json (which silently drops dev-store
        test orders — the bug behind the reviewer's "0 / 0" re-sync toast),
        the GraphQL orders query returns test orders too.

        Each order's `lineItems` connection is drained to exhaustion:
        LINE_ITEMS_PER_ORDER is a page size, not a completeness cap (the
        pre-fix code silently truncated the line-item evidence at the
        first page). Raises ShopifyGraphQLIncomplete on any pagination
        anomaly — a truncated line-item list is never yielded.
        """
        query = f"""
        query Orders($cursor: String, $search: String) {{
          orders(first: {ORDERS_PAGE_SIZE}, after: $cursor, query: $search, sortKey: CREATED_AT) {{
            pageInfo {{ hasNextPage endCursor }}
            nodes {{
              legacyResourceId
              name
              createdAt
              cancelledAt
              test
              displayFinancialStatus
              currencyCode
              paymentGatewayNames
              totalPriceSet {{ shopMoney {{ amount }} }}
              subtotalPriceSet {{ shopMoney {{ amount }} }}
              totalTaxSet {{ shopMoney {{ amount }} }}
              totalDiscountsSet {{ shopMoney {{ amount }} }}
              totalShippingPriceSet {{ shopMoney {{ amount }} }}
              customer {{ email firstName lastName }}
              lineItems(first: {LINE_ITEMS_PER_ORDER}) {{
                pageInfo {{ hasNextPage endCursor }}
                nodes {{
                  sku
                  title
                  quantity
                  originalUnitPriceSet {{ shopMoney {{ amount }} }}
                  variant {{ legacyResourceId }}
                  product {{ legacyResourceId }}
                }}
              }}
            }}
          }}
        }}
        """
        search = (
            f"created_at:>='{_iso_for_search(created_at_min)}' AND created_at:<='{_iso_for_search(created_at_max)}'"
        )
        yield from self._iter_orders_search(query, search)

    def iter_refunded_orders(self, updated_at_min: str, updated_at_max: str):
        """A159: yield REST-shaped orders whose refund state changed in the
        window. Searches by updated_at (a refund bumps the order's
        updatedAt) + financial_status, so a refund issued today against an
        order created months before the lookback window is still caught —
        iter_orders' created_at filter can never see those. Line items
        drain to exhaustion exactly like iter_orders (a B candidate's
        booked parent carries complete line-item evidence)."""
        query = f"""
        query Orders($cursor: String, $search: String) {{
          orders(first: {ORDERS_PAGE_SIZE}, after: $cursor, query: $search, sortKey: UPDATED_AT) {{
            pageInfo {{ hasNextPage endCursor }}
            nodes {{
              legacyResourceId
              name
              createdAt
              cancelledAt
              test
              displayFinancialStatus
              currencyCode
              paymentGatewayNames
              totalPriceSet {{ shopMoney {{ amount }} }}
              subtotalPriceSet {{ shopMoney {{ amount }} }}
              totalTaxSet {{ shopMoney {{ amount }} }}
              totalDiscountsSet {{ shopMoney {{ amount }} }}
              totalShippingPriceSet {{ shopMoney {{ amount }} }}
              customer {{ email firstName lastName }}
              lineItems(first: {LINE_ITEMS_PER_ORDER}) {{
                pageInfo {{ hasNextPage endCursor }}
                nodes {{
                  sku
                  title
                  quantity
                  originalUnitPriceSet {{ shopMoney {{ amount }} }}
                  variant {{ legacyResourceId }}
                  product {{ legacyResourceId }}
                }}
              }}
            }}
          }}
        }}
        """
        search = (
            f"updated_at:>='{_iso_for_search(updated_at_min)}' "
            f"AND updated_at:<='{_iso_for_search(updated_at_max)}' "
            f"AND (financial_status:refunded OR financial_status:partially_refunded)"
        )
        yield from self._iter_orders_search(query, search)

    def _iter_orders_search(self, query: str, search: str):
        cursor = None
        while True:
            # allow_partial: customer fields can be individually denied on
            # stores where protected-customer-data approval hasn't propagated;
            # that must not sink the whole order sync. The lineItems
            # connection itself is NOT optional: its complete drain below is
            # the order's line-item evidence, so an absent/invalid lineItems
            # shape fails loudly, and the per-order overflow pages never use
            # allow_partial.
            data = self.execute(query, {"cursor": cursor, "search": search}, allow_partial=True)
            conn = data.get("orders") or {}
            for node in conn.get("nodes") or []:
                line_item_nodes = self._drain_nested_connection(
                    _ORDER_LINE_ITEMS_PAGE_QUERY,
                    f"gid://shopify/Order/{node.get('legacyResourceId')}",
                    "order",
                    "lineItems",
                    node.get("lineItems"),
                    f"order {node.get('legacyResourceId')}",
                )
                yield self._order_to_rest_shape(node, line_item_nodes)
            page = conn.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return
            cursor = page.get("endCursor")

    @staticmethod
    def _order_to_rest_shape(node: dict, line_item_nodes: list) -> dict:
        name = node.get("name") or ""
        customer = node.get("customer")
        line_items = []
        for li in line_item_nodes:
            line_items.append(
                {
                    "sku": li.get("sku") or "",
                    "title": li.get("title") or "",
                    "quantity": li.get("quantity", 1),
                    "price": _money(li.get("originalUnitPriceSet")),
                    "variant_id": _gid_tail(f"x/{(li.get('variant') or {}).get('legacyResourceId')}"),
                    "product_id": _gid_tail(f"x/{(li.get('product') or {}).get('legacyResourceId')}"),
                }
            )

        shipping = _money(node.get("totalShippingPriceSet"))
        financial_status = (node.get("displayFinancialStatus") or "").lower()

        return {
            "id": int(node["legacyResourceId"]),
            "name": name,
            "order_number": name.lstrip("#") or str(node["legacyResourceId"]),
            "created_at": node.get("createdAt") or "",
            "cancelled_at": node.get("cancelledAt"),
            "test": bool(node.get("test", False)),
            "currency": node.get("currencyCode", "USD"),
            "financial_status": financial_status,
            "payment_gateway_names": node.get("paymentGatewayNames") or [],
            "total_price": _money(node.get("totalPriceSet")),
            "subtotal_price": _money(node.get("subtotalPriceSet")),
            "total_tax": _money(node.get("totalTaxSet")),
            "total_discounts": _money(node.get("totalDiscountsSet")),
            "shipping_lines": [{"price": shipping}] if shipping not in ("0", "0.0", "0.00") else [],
            "customer": (
                {
                    "email": customer.get("email") or "",
                    "first_name": customer.get("firstName") or "",
                    "last_name": customer.get("lastName") or "",
                }
                if customer
                else None
            ),
            "line_items": line_items,
        }

    # ------------------------------------------------------------------
    # Fulfillments  (A125 COGS backfill — REST shape: fulfillments/create)
    # ------------------------------------------------------------------

    def get_order_fulfillments(self, order_id) -> list[dict]:
        """REST-shaped fulfillment dicts for a single order (A125 COGS backfill).

        Pulled per-order (not nested in iter_orders) so the
        fulfillments × fulfillmentLineItems cost stays well under Shopify's
        1000-point single-query ceiling regardless of order size. Each dict
        matches the fulfillments/create webhook payload process_fulfillment
        consumes: {id, order_id, created_at, location_id, status, line_items}.
        Returns [] when the order has no fulfillments.
        """
        gid = f"gid://shopify/Order/{order_id}"
        query = f"""
        query OrderFulfillments($id: ID!) {{
          order(id: $id) {{
            fulfillments(first: {FULFILLMENTS_PER_ORDER}) {{
              legacyResourceId
              createdAt
              status
              location {{ legacyResourceId }}
              fulfillmentLineItems(first: {FULFILLMENT_LINE_ITEMS}) {{
                nodes {{
                  quantity
                  lineItem {{ sku title }}
                }}
              }}
            }}
          }}
        }}
        """
        data = self.execute(query, {"id": gid}, allow_partial=True)
        order = data.get("order") or {}
        raw = order.get("fulfillments") or []
        # Tolerate either the list shape ([Fulfillment!]!) or a connection
        # shape ({nodes: [...]}) across Admin API versions.
        if isinstance(raw, dict):
            raw = raw.get("nodes") or []

        results = []
        for f in raw:
            fli_nodes = (f.get("fulfillmentLineItems") or {}).get("nodes") or []
            line_items = []
            for li in fli_nodes:
                item = li.get("lineItem") or {}
                line_items.append(
                    {
                        "sku": item.get("sku") or "",
                        "title": item.get("title") or "",
                        "quantity": li.get("quantity", 0),
                    }
                )
            location = f.get("location") or {}
            results.append(
                {
                    "id": int(f["legacyResourceId"]),
                    "order_id": int(order_id),
                    "created_at": f.get("createdAt") or "",
                    "status": (f.get("status") or "").lower(),
                    "location_id": location.get("legacyResourceId") or "",
                    "line_items": line_items,
                }
            )
        return results

    # ------------------------------------------------------------------
    # Refunds  (A159 refund backfill — REST shape: refunds/create)
    # ------------------------------------------------------------------

    def get_order_refunds(self, order_id) -> list[dict]:
        """COMPLETE REST-shaped refund dicts for a single order (A159
        backfill), or a loud failure — never a silently truncated list.

        Two-phase fetch (A5 refund-catch-up completeness):

        1. The order's complete refund summary list. `Order.refunds` is
           a plain LIST field in Admin GraphQL 2026-04 whose optional
           `first` argument truncates the array — the query omits `first`
           entirely, so the response is the complete list by the field's
           own contract.
        2. Per refund, the complete `transactions`
           (`OrderTransactionConnection!`) and `refundLineItems`
           (`RefundLineItemConnection!`) connections, cursor-paginated to
           exhaustion. REFUND_TRANSACTIONS / REFUND_LINE_ITEMS remain
           page sizes only.

        Pulled per-order (never nested in iter_orders) so each query's
        cost stays far under Shopify's 1000-point single-query ceiling.
        Raises ShopifyGraphQLIncomplete when any page fails, has an
        invalid shape, repeats a cursor, or reports hasNextPage without
        an endCursor — a partial refund is never returned as complete
        (the old first-page-only read silently omitted financially
        relevant refunds, transactions, and refund line items).

        GraphQL enums come back UPPERCASE (REFUND/SUCCESS/RETURN) and are
        lowercased here: process_refund compares kind == 'refund' and
        status == 'success', and the accounting projection compares
        restock_type in ('return', 'cancel'). Forgetting this yields a
        silent refund_amount=0.
        """
        summaries = self._fetch_order_refund_summaries(order_id)
        results = []
        for summary in summaries:
            transactions, refund_line_items = self._fetch_refund_connections(summary["gid"])
            results.append(
                {
                    "id": summary["id"],
                    "order_id": int(order_id),
                    "created_at": summary["created_at"],
                    "note": summary["note"],
                    "transactions": transactions,
                    "refund_line_items": refund_line_items,
                }
            )
        return results

    def _fetch_order_refund_summaries(self, order_id) -> list[dict]:
        """Phase 1: the COMPLETE list of the order's refunds (gid,
        legacy id, createdAt, note) — no nested connections, so the cost
        stays trivial regardless of refund count.

        `Order.refunds` is a plain LIST field in Admin GraphQL 2026-04
        (`[Refund!]!`); its optional `first` argument TRUNCATES the
        array. The query below therefore omits `first` entirely — the
        complete list by the field's own contract — and selects no
        pageInfo/nodes/after (those belong to connections; the similarly
        named `Order.transactions` list field is a known confusion
        source)."""
        order_gid = f"gid://shopify/Order/{order_id}"
        query = """
        query OrderRefundSummaries($id: ID!) {
          order(id: $id) {
            refunds { id legacyResourceId createdAt note }
          }
        }
        """

        data = self.execute(query, {"id": order_gid})
        order = data.get("order")
        if order is None:
            return []  # order absent/deleted — not a truncation
        raw = order.get("refunds")
        if not isinstance(raw, list):
            raise ShopifyGraphQLIncomplete(f"Invalid refund list shape for order {order_id} on {self.shop_domain}")

        summaries = []
        for node in raw:
            gid = (node or {}).get("id") or ""
            legacy = (node or {}).get("legacyResourceId")
            if not gid or legacy is None:
                raise ShopifyGraphQLIncomplete(
                    f"Refund node without id/legacyResourceId for order {order_id} on {self.shop_domain}"
                )
            summaries.append(
                {
                    "gid": gid,
                    "id": int(legacy),
                    "created_at": node.get("createdAt") or "",
                    "note": node.get("note") or "",
                }
            )
        return summaries

    def _fetch_refund_connections(self, refund_gid: str) -> tuple[list[dict], list[dict]]:
        """Phase 2: one refund's COMPLETE transactions and refundLineItems.
        Both are connections in Admin GraphQL 2026-04
        (`OrderTransactionConnection!` / `RefundLineItemConnection!`),
        cursor-paginated to exhaustion. The first page carries both
        connections; remaining pages are fetched per connection."""
        first_query = f"""
        query RefundDetail($id: ID!) {{
          refund(id: $id) {{
            transactions(first: {REFUND_TRANSACTIONS}) {{
              pageInfo {{ hasNextPage endCursor }}
              nodes {{ kind status amountSet {{ shopMoney {{ amount }} }} }}
            }}
            refundLineItems(first: {REFUND_LINE_ITEMS}) {{
              pageInfo {{ hasNextPage endCursor }}
              nodes {{
                quantity
                restockType
                subtotalSet {{ shopMoney {{ amount }} }}
                lineItem {{ sku title }}
              }}
            }}
          }}
        }}
        """
        transactions_page = f"""
        query RefundTransactionsPage($id: ID!, $cursor: String) {{
          refund(id: $id) {{
            transactions(first: {REFUND_TRANSACTIONS}, after: $cursor) {{
              pageInfo {{ hasNextPage endCursor }}
              nodes {{ kind status amountSet {{ shopMoney {{ amount }} }} }}
            }}
          }}
        }}
        """
        line_items_page = f"""
        query RefundLineItemsPage($id: ID!, $cursor: String) {{
          refund(id: $id) {{
            refundLineItems(first: {REFUND_LINE_ITEMS}, after: $cursor) {{
              pageInfo {{ hasNextPage endCursor }}
              nodes {{
                quantity
                restockType
                subtotalSet {{ shopMoney {{ amount }} }}
                lineItem {{ sku title }}
              }}
            }}
          }}
        }}
        """

        def _connection(data: dict, field: str) -> dict:
            refund = data.get("refund")
            if not isinstance(refund, dict):
                raise ShopifyGraphQLIncomplete(f"Refund {refund_gid} on {self.shop_domain} vanished mid-fetch")
            conn = refund.get(field)
            if not isinstance(conn, dict) or not isinstance(conn.get("nodes"), list):
                raise ShopifyGraphQLIncomplete(
                    f"Invalid {field} page shape for refund {refund_gid} on {self.shop_domain}"
                )
            return conn

        def _drain(page_query: str, field: str, conn: dict, shape) -> list:
            nodes = [shape(n) for n in conn["nodes"]]
            seen_cursors: set[str] = set()
            while True:
                page_info = conn.get("pageInfo") or {}
                if not page_info.get("hasNextPage"):
                    return nodes
                end_cursor = page_info.get("endCursor")
                if not end_cursor:
                    raise ShopifyGraphQLIncomplete(
                        f"{field} page for refund {refund_gid} on {self.shop_domain} reports hasNextPage without endCursor"
                    )
                if end_cursor in seen_cursors:
                    raise ShopifyGraphQLIncomplete(
                        f"{field} pagination cursor repeated for refund {refund_gid} on {self.shop_domain}"
                    )
                seen_cursors.add(end_cursor)
                data = self.execute(page_query, {"id": refund_gid, "cursor": end_cursor})
                conn = _connection(data, field)
                nodes.extend(shape(n) for n in conn["nodes"])

        def _txn(t: dict) -> dict:
            return {
                "kind": (t.get("kind") or "").lower(),
                "status": (t.get("status") or "").lower(),
                "amount": _money(t.get("amountSet")),
            }

        def _line(li: dict) -> dict:
            item = li.get("lineItem") or {}
            return {
                "quantity": li.get("quantity", 0),
                "restock_type": (li.get("restockType") or "").lower(),
                "subtotal": _money(li.get("subtotalSet")),
                "line_item": {"sku": item.get("sku") or "", "title": item.get("title") or ""},
            }

        data = self.execute(first_query, {"id": refund_gid})
        transactions = _drain(transactions_page, "transactions", _connection(data, "transactions"), _txn)
        refund_line_items = _drain(line_items_page, "refundLineItems", _connection(data, "refundLineItems"), _line)
        return transactions, refund_line_items

    # ------------------------------------------------------------------
    # Shopify Payments  (REST shape: /shopify_payments/payouts.json and
    # /shopify_payments/balance/transactions.json)
    # ------------------------------------------------------------------

    def list_payouts(self, status: str = "paid", limit: int | None = None) -> list[dict] | None:
        """
        ALL payouts, REST-shaped, cursor-paginated (A169: the old
        single-page `payouts(first: 50)` silently made anything older
        than the newest 50 unreachable — no error, no log; combined with
        the client-side status filter the effective window was even
        smaller). Returns None when the store has no Shopify Payments
        account exposed to us (Payments not enabled, or scope withheld) —
        callers treat that like the old REST 403.

        `limit` is an optional TOTAL cap for cheap health checks
        (shopify_graphql_ping passes 5); None paginates to exhaustion.
        """
        query = """
        query Payouts($cursor: String) {
          shopifyPaymentsAccount {
            payouts(first: 50, after: $cursor) {
              pageInfo { hasNextPage endCursor }
              nodes {
                legacyResourceId
                issuedAt
                status
                net { amount currencyCode }
                summary {
                  adjustmentsFee { amount }
                  adjustmentsGross { amount }
                  chargesFee { amount }
                  chargesGross { amount }
                  refundsFee { amount }
                  refundsFeeGross { amount }
                  reservedFundsFee { amount }
                  reservedFundsGross { amount }
                }
              }
            }
          }
        }
        """
        payouts: list[dict] = []
        cursor = None
        while True:
            data = self.execute(query, {"cursor": cursor})
            account = data.get("shopifyPaymentsAccount")
            if account is None:
                # First page in practice — preserve the "unavailable"
                # contract (A120) rather than returning a partial list.
                return None

            conn = account.get("payouts") or {}
            for node in conn.get("nodes") or []:
                node_status = (node.get("status") or "").lower()
                if status and node_status != status.lower():
                    continue
                net = node.get("net") or {}
                summary = node.get("summary") or {}
                payouts.append(
                    {
                        "id": int(node["legacyResourceId"]),
                        "date": node.get("issuedAt") or "",
                        "status": node_status,
                        "amount": str(net.get("amount", "0")),
                        "currency": net.get("currencyCode", "USD"),
                        "summary": {
                            "adjustments_fee_amount": _money(summary.get("adjustmentsFee")),
                            "adjustments_gross_amount": _money(summary.get("adjustmentsGross")),
                            "charges_fee_amount": _money(summary.get("chargesFee")),
                            "charges_gross_amount": _money(summary.get("chargesGross")),
                            "refunds_fee_amount": _money(summary.get("refundsFee")),
                            "refunds_gross_amount": _money(summary.get("refundsFeeGross")),
                            "reserved_funds_fee_amount": _money(summary.get("reservedFundsFee")),
                            "reserved_funds_gross_amount": _money(summary.get("reservedFundsGross")),
                        },
                    }
                )

            if limit is not None and len(payouts) >= limit:
                return payouts[:limit]
            page = conn.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return payouts
            cursor = page.get("endCursor")

    def list_payout_transactions(self, payout_id, limit: int | None = None) -> list[dict] | None:
        """
        Balance transactions belonging to one payout, REST-shaped.
        Returns None when no Shopify Payments account is exposed.

        A169: fetches ALL pages by default. The old `limit=250` stop
        condition silently truncated large payouts (~300 transactions
        max), so payout verification compared complete Shopify totals
        against incomplete sums — a guaranteed, permanent false "Net
        mismatch" on any big payout. A limit, if passed, is a hard cap
        the CALLER opted into.
        """
        query = """
        query PayoutTransactions($cursor: String, $search: String) {
          shopifyPaymentsAccount {
            balanceTransactions(first: 100, after: $cursor, query: $search) {
              pageInfo { hasNextPage endCursor }
              nodes {
                id
                sourceId
                sourceType
                sourceOrderTransactionId
                transactionDate
                amount { amount currencyCode }
                fee { amount }
                net { amount }
                associatedOrder { id }
              }
            }
          }
        }
        """
        search = f"payout_id:{payout_id}"
        transactions: list[dict] = []
        cursor = None
        while limit is None or len(transactions) < limit:
            data = self.execute(query, {"cursor": cursor, "search": search})
            account = data.get("shopifyPaymentsAccount")
            if account is None:
                return None
            conn = account.get("balanceTransactions") or {}
            for node in conn.get("nodes") or []:
                amount = node.get("amount") or {}
                associated_order = node.get("associatedOrder") or {}
                transactions.append(
                    {
                        "id": _gid_tail(node.get("id")),
                        "type": (node.get("sourceType") or "").lower(),
                        "amount": str(amount.get("amount", "0")),
                        "currency": amount.get("currencyCode", ""),
                        "fee": _money(node.get("fee")),
                        "net": _money(node.get("net")),
                        "source_order_id": _gid_tail(associated_order.get("id")),
                        "source_type": (node.get("sourceType") or "").lower(),
                        "processed_at": node.get("transactionDate") or "",
                    }
                )
            page = conn.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")
        return transactions
