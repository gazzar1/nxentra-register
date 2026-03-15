# shopify_connector/event_types.py
"""
Shopify event data classes.

Each event type has a corresponding dataclass that defines its payload schema.
REGISTERED_EVENTS at the bottom is discovered by ProjectionsConfig.ready().
"""

from dataclasses import dataclass, field
from typing import List, Optional

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
    DR Shopify Clearing / CR Sales Revenue
    (+ tax and shipping lines if applicable)
    """
    store_public_id: str = ""
    shopify_order_id: str = ""
    order_number: str = ""
    order_name: str = ""
    subtotal: str = "0"
    total_tax: str = "0"
    total_shipping: str = "0"
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
    DR Sales Revenue / CR Shopify Clearing
    """
    store_public_id: str = ""
    shopify_refund_id: str = ""
    shopify_order_id: str = ""
    order_number: str = ""
    reason: str = ""


@dataclass
class ShopifyPayoutSettledData(FinancialEventData):
    """
    Triggers payout settlement journal entry:
    DR Cash/Bank        (net_amount)
    DR Processing Fees  (fees)
    CR Shopify Clearing (gross_amount)
    """
    store_public_id: str = ""
    shopify_payout_id: str = ""
    gross_amount: str = "0"
    fees: str = "0"
    net_amount: str = "0"
    shopify_status: str = ""
    payout_date: str = ""


@dataclass
class ShopifyOrderFulfilledData(FinancialEventData):
    """
    Triggers COGS journal entry per matched inventory item:
    DR Cost of Goods Sold   (qty × avg_cost per item)
    CR Inventory            (qty × avg_cost per item)

    Also triggers inventory deduction via INVENTORY_STOCK_ISSUED.
    """
    store_public_id: str = ""
    shopify_fulfillment_id: str = ""
    shopify_order_id: str = ""
    order_name: str = ""
    fulfillment_date: str = ""
    total_cogs: str = "0"
    # Each entry: {sku, item_public_id, item_code, warehouse_public_id,
    #              qty, unit_cost, cogs_value, cogs_account_id, inventory_account_id}
    cogs_lines: list = field(default_factory=list)
    # Unmatched SKUs (no corresponding Item found)
    unmatched_skus: list = field(default_factory=list)


@dataclass
class ShopifyDisputeCreatedData(FinancialEventData):
    """
    Triggers chargeback journal entry:
    DR Chargeback Loss / Receivable  (amount)
    DR Processing Fees               (chargeback fee)
    CR Shopify Clearing              (amount + fee)
    """
    store_public_id: str = ""
    shopify_dispute_id: str = ""
    shopify_order_id: str = ""
    order_name: str = ""
    dispute_amount: str = "0"
    chargeback_fee: str = "0"
    reason: str = ""
    dispute_status: str = ""


# =============================================================================
# REGISTERED_EVENTS — discovered by ProjectionsConfig.ready()
# =============================================================================

REGISTERED_EVENTS: dict[str, type[BaseEventData]] = {
    EventTypes.SHOPIFY_STORE_CONNECTED: ShopifyStoreConnectedData,
    EventTypes.SHOPIFY_STORE_DISCONNECTED: ShopifyStoreDisconnectedData,
    EventTypes.SHOPIFY_ORDER_PAID: ShopifyOrderPaidData,
    EventTypes.SHOPIFY_REFUND_CREATED: ShopifyRefundCreatedData,
    EventTypes.SHOPIFY_PAYOUT_SETTLED: ShopifyPayoutSettledData,
    EventTypes.SHOPIFY_ORDER_FULFILLED: ShopifyOrderFulfilledData,
    EventTypes.SHOPIFY_DISPUTE_CREATED: ShopifyDisputeCreatedData,
}
