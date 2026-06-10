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
# multiply: products × variants, orders × line_items).
PRODUCTS_PAGE_SIZE = 12
VARIANTS_PER_PRODUCT = 60
ORDERS_PAGE_SIZE = 10
LINE_ITEMS_PER_ORDER = 50

_MAX_THROTTLE_RETRIES = 5


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
                pageInfo {{ hasNextPage }}
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

                variants_conn = node.get("variants") or {}
                if (variants_conn.get("pageInfo") or {}).get("hasNextPage"):
                    logger.warning(
                        "Product %s on %s has more than %d variants — extra variants not synced",
                        node.get("legacyResourceId"),
                        self.shop_domain,
                        VARIANTS_PER_PRODUCT,
                    )

                variants = []
                for v in variants_conn.get("nodes") or []:
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
        cursor = None
        while True:
            # allow_partial: customer fields can be individually denied on
            # stores where protected-customer-data approval hasn't propagated;
            # that must not sink the whole order sync.
            data = self.execute(query, {"cursor": cursor, "search": search}, allow_partial=True)
            conn = data.get("orders") or {}
            for node in conn.get("nodes") or []:
                yield self._order_to_rest_shape(node)
            page = conn.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return
            cursor = page.get("endCursor")

    @staticmethod
    def _order_to_rest_shape(node: dict) -> dict:
        name = node.get("name") or ""
        customer = node.get("customer")
        line_items = []
        for li in ((node.get("lineItems") or {}).get("nodes")) or []:
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
    # Shopify Payments  (REST shape: /shopify_payments/payouts.json and
    # /shopify_payments/balance/transactions.json)
    # ------------------------------------------------------------------

    def list_payouts(self, status: str = "paid", limit: int = 50) -> list[dict] | None:
        """
        Most-recent payouts, REST-shaped. Returns None when the store has
        no Shopify Payments account exposed to us (Payments not enabled, or
        scope withheld) — callers treat that like the old REST 403.
        """
        query = f"""
        query Payouts {{
          shopifyPaymentsAccount {{
            payouts(first: {int(limit)}) {{
              nodes {{
                legacyResourceId
                issuedAt
                status
                net {{ amount currencyCode }}
                summary {{
                  adjustmentsFee {{ amount }}
                  adjustmentsGross {{ amount }}
                  chargesFee {{ amount }}
                  chargesGross {{ amount }}
                  refundsFee {{ amount }}
                  refundsFeeGross {{ amount }}
                  reservedFundsFee {{ amount }}
                  reservedFundsGross {{ amount }}
                }}
              }}
            }}
          }}
        }}
        """
        data = self.execute(query)
        account = data.get("shopifyPaymentsAccount")
        if account is None:
            return None

        payouts = []
        for node in (account.get("payouts") or {}).get("nodes") or []:
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
        return payouts

    def list_payout_transactions(self, payout_id, limit: int = 250) -> list[dict] | None:
        """
        Balance transactions belonging to one payout, REST-shaped.
        Returns None when no Shopify Payments account is exposed.
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
        while len(transactions) < limit:
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
