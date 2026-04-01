# shopify_connector/connector.py
"""
Shopify connector adapter — implements BasePlatformConnector.

This wires Shopify's webhook format into the platform abstraction layer.
Note: Shopify continues to use its OWN event types (shopify.order_paid etc.)
and its own ShopifyAccountingProjection for backward compatibility.

This adapter is registered to demonstrate the pattern and to make Shopify
available through the generic /api/platforms/shopify/webhooks/ endpoint
as an alternative to the existing /api/shopify/webhooks/ endpoint.

For new platforms (Stripe, WooCommerce), the generic endpoint + PLATFORM_*
events would be the primary path.
"""

import logging
from decimal import Decimal

from django.http import HttpRequest

from platform_connectors.base import BasePlatformConnector
from platform_connectors.canonical import (
    ParsedDispute,
    ParsedFulfillment,
    ParsedOrder,
    ParsedOrderLine,
    ParsedPayout,
    ParsedRefund,
)

logger = logging.getLogger(__name__)

# Topic mapping: Shopify topic → canonical topic category
SHOPIFY_TOPIC_MAP = {
    "orders/paid": "order_paid",
    "refunds/create": "refund_created",
    "fulfillments/create": "fulfillment_created",
    "disputes/create": "dispute_created",
    "disputes/update": "dispute_created",
}


class ShopifyConnector(BasePlatformConnector):
    """
    Shopify implementation of the platform connector interface.

    Uses Shopify-specific HMAC verification and header-based topic
    extraction.
    """

    @property
    def platform_slug(self) -> str:
        return "shopify"

    @property
    def platform_name(self) -> str:
        return "Shopify"

    @property
    def account_roles(self) -> list[str]:
        return [
            "SALES_REVENUE",
            "SHOPIFY_CLEARING",
            "PAYMENT_PROCESSING_FEES",
            "SALES_TAX_PAYABLE",
            "SHIPPING_REVENUE",
            "SALES_DISCOUNTS",
            "CASH_BANK",
            "CHARGEBACK_EXPENSE",
        ]

    @property
    def webhook_topics(self) -> list[str]:
        return [
            "orders/paid",
            "refunds/create",
            "fulfillments/create",
            "disputes/create",
            "disputes/update",
            "app/uninstalled",
        ]

    def verify_webhook(self, request: HttpRequest) -> bool:
        from shopify_connector.commands import verify_webhook_hmac

        hmac_header = request.META.get("HTTP_X_SHOPIFY_HMAC_SHA256", "")
        if not hmac_header:
            return False
        return verify_webhook_hmac(request.body, hmac_header)

    def parse_webhook_topic(self, request: HttpRequest) -> str:
        return request.META.get("HTTP_X_SHOPIFY_TOPIC", "")

    def map_topic_to_canonical(self, topic: str) -> str | None:
        """Map a Shopify webhook topic to a canonical topic category."""
        return SHOPIFY_TOPIC_MAP.get(topic)

    def resolve_company_from_webhook(self, request: HttpRequest):
        """Resolve company via shop_domain header → ShopifyStore → company."""
        shop_domain = request.META.get("HTTP_X_SHOPIFY_SHOP_DOMAIN", "")
        if not shop_domain:
            return None

        from shopify_connector.models import ShopifyStore
        try:
            store = ShopifyStore.objects.select_related("company").get(
                shop_domain=shop_domain,
                status=ShopifyStore.Status.ACTIVE,
            )
            return store.company
        except ShopifyStore.DoesNotExist:
            logger.warning("No active Shopify store for domain: %s", shop_domain)
            return None

    def get_module_key(self) -> str:
        """Use existing shopify_connector module key."""
        return "shopify_connector"

    def parse_order(self, payload: dict) -> ParsedOrder:
        line_items = []
        for item in payload.get("line_items", []):
            line_items.append(ParsedOrderLine(
                sku=item.get("sku", ""),
                title=item.get("title", ""),
                quantity=item.get("quantity", 0),
                unit_price=Decimal(str(item.get("price", "0"))),
                total=Decimal(str(item.get("price", "0"))) * item.get("quantity", 1),
                tax=sum(
                    Decimal(str(t.get("price", "0")))
                    for t in item.get("tax_lines", [])
                ),
                platform_line_id=str(item.get("id", "")),
            ))

        return ParsedOrder(
            platform_order_id=str(payload.get("id", "")),
            order_number=str(payload.get("order_number", "")),
            order_name=payload.get("name", ""),
            total_price=Decimal(str(payload.get("total_price", "0"))),
            subtotal=Decimal(str(payload.get("subtotal_price", "0"))),
            total_tax=Decimal(str(payload.get("total_tax", "0"))),
            total_shipping=sum(
                Decimal(str(line.get("price", "0")))
                for line in payload.get("shipping_lines", [])
            ),
            total_discounts=Decimal(str(payload.get("total_discounts", "0"))),
            currency=payload.get("currency", "USD"),
            financial_status=payload.get("financial_status", ""),
            gateway=payload.get("gateway", ""),
            customer_email=payload.get("email", ""),
            customer_name=_customer_name(payload),
            order_date=payload.get("created_at", ""),
            line_items=line_items,
            payment_transactions=payload.get("transactions", []),
        )

    def parse_refund(self, payload: dict) -> ParsedRefund:
        # Shopify refund webhook wraps in "refund" key
        refund = payload.get("refund", payload)
        transactions = refund.get("transactions", [])
        amount = sum(
            Decimal(str(t.get("amount", "0")))
            for t in transactions
            if t.get("kind") == "refund"
        )
        if amount == 0:
            amount = Decimal(str(refund.get("total_duties_set", {}).get("shop_money", {}).get("amount", "0")))

        return ParsedRefund(
            platform_refund_id=str(refund.get("id", "")),
            platform_order_id=str(refund.get("order_id", "")),
            order_number=str(payload.get("order_number", refund.get("order_id", ""))),
            amount=amount,
            currency=refund.get("currency", "USD"),
            reason=refund.get("note", ""),
            refund_date=refund.get("created_at", ""),
        )

    def parse_payout(self, payload: dict) -> ParsedPayout:
        return ParsedPayout(
            platform_payout_id=str(payload.get("id", "")),
            gross_amount=Decimal(str(payload.get("amount", "0"))),
            fees=Decimal(str(payload.get("fee", "0"))),
            net_amount=Decimal(str(payload.get("net", payload.get("amount", "0")))),
            currency=payload.get("currency", "USD"),
            status=payload.get("status", ""),
            payout_date=payload.get("date", ""),
        )

    def parse_dispute(self, payload: dict) -> ParsedDispute | None:
        return ParsedDispute(
            platform_dispute_id=str(payload.get("id", "")),
            platform_order_id=str(payload.get("order_id", "")),
            order_name="",
            dispute_amount=Decimal(str(payload.get("amount", "0"))),
            chargeback_fee=Decimal(str(payload.get("fee", "0"))),
            currency=payload.get("currency", "USD"),
            reason=payload.get("reason", ""),
            status=payload.get("status", ""),
            evidence_due_by=payload.get("evidence_due_by"),
        )

    def parse_fulfillment(self, payload: dict) -> ParsedFulfillment | None:
        return ParsedFulfillment(
            platform_fulfillment_id=str(payload.get("id", "")),
            platform_order_id=str(payload.get("order_id", "")),
            order_name=payload.get("name", ""),
            fulfillment_date=payload.get("created_at", ""),
        )


def _customer_name(payload: dict) -> str:
    customer = payload.get("customer", {})
    if not customer:
        return ""
    first = customer.get("first_name", "")
    last = customer.get("last_name", "")
    return f"{first} {last}".strip()
