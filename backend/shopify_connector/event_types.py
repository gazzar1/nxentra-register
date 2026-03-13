# shopify_connector/event_types.py
"""
Shopify event data classes.

Each event type has a corresponding dataclass that defines its payload schema.
REGISTERED_EVENTS at the bottom is discovered by ProjectionsConfig.ready().
"""

from dataclasses import dataclass, field
from typing import List

from events.types import BaseEventData, FinancialEventData, EventTypes


# =============================================================================
# Connection events
# =============================================================================

@dataclass
class ShopifyStoreConnectedData(BaseEventData):
    store_public_id: str = ""
    shop_domain: str = ""
    company_public_id: str = ""
    connected_by_email: str = ""


@dataclass
class ShopifyStoreDisconnectedData(BaseEventData):
    store_public_id: str = ""
    shop_domain: str = ""
    company_public_id: str = ""
    reason: str = ""


# =============================================================================
# Financial events (extend FinancialEventData)
# =============================================================================

@dataclass
class ShopifyOrderPaidData(FinancialEventData):
    """
    Triggers journal entry:
    DR Accounts Receivable / CR Sales Revenue
    (+ tax and discount lines if applicable)
    """
    store_public_id: str = ""
    shopify_order_id: str = ""
    order_number: str = ""
    order_name: str = ""
    subtotal: str = "0"
    total_tax: str = "0"
    total_discounts: str = "0"
    financial_status: str = ""
    gateway: str = ""
    line_items: list = field(default_factory=list)
    customer_email: str = ""
    customer_name: str = ""


@dataclass
class ShopifyRefundCreatedData(FinancialEventData):
    """
    Triggers reversal journal entry:
    DR Sales Revenue / CR Accounts Receivable
    """
    store_public_id: str = ""
    shopify_refund_id: str = ""
    shopify_order_id: str = ""
    order_number: str = ""
    reason: str = ""


# =============================================================================
# REGISTERED_EVENTS — discovered by ProjectionsConfig.ready()
# =============================================================================

REGISTERED_EVENTS: dict[str, type[BaseEventData]] = {
    EventTypes.SHOPIFY_STORE_CONNECTED: ShopifyStoreConnectedData,
    EventTypes.SHOPIFY_STORE_DISCONNECTED: ShopifyStoreDisconnectedData,
    EventTypes.SHOPIFY_ORDER_PAID: ShopifyOrderPaidData,
    EventTypes.SHOPIFY_REFUND_CREATED: ShopifyRefundCreatedData,
}
