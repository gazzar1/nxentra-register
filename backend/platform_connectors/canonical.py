# platform_connectors/canonical.py
"""
Canonical dataclasses for platform-agnostic commerce data.

These are intermediate structures between platform-specific webhook parsing
and event emission. Each platform connector's `parse_*` methods return these
types, which the generic webhook handler converts into BusinessEvents.
"""

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class ParsedOrderLine:
    """Single line item within a parsed order."""

    sku: str = ""
    title: str = ""
    quantity: int = 0
    unit_price: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    discount: Decimal = Decimal("0")
    platform_line_id: str = ""


@dataclass
class ParsedOrder:
    """
    Platform-agnostic representation of a commerce order.

    Populated by a connector's parse_order() method, consumed by the
    generic webhook handler to emit PLATFORM_ORDER_PAID events.
    """

    platform_order_id: str = ""
    order_number: str = ""
    order_name: str = ""
    total_price: Decimal = Decimal("0")
    subtotal: Decimal = Decimal("0")
    total_tax: Decimal = Decimal("0")
    total_shipping: Decimal = Decimal("0")
    total_discounts: Decimal = Decimal("0")
    currency: str = "USD"
    financial_status: str = ""
    gateway: str = ""
    customer_email: str = ""
    customer_name: str = ""
    order_date: str = ""
    line_items: list[ParsedOrderLine] = field(default_factory=list)
    # Raw transactions for payment verification (Layer 1)
    payment_transactions: list[dict] = field(default_factory=list)


@dataclass
class ParsedRefund:
    """
    Platform-agnostic representation of a refund.
    """

    platform_refund_id: str = ""
    platform_order_id: str = ""
    order_number: str = ""
    amount: Decimal = Decimal("0")
    currency: str = "USD"
    reason: str = ""
    refund_date: str = ""


@dataclass
class ParsedPayout:
    """
    Platform-agnostic representation of a payout/settlement.

    Covers both positive (normal) and negative (refund-heavy) payouts.
    """

    platform_payout_id: str = ""
    gross_amount: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    net_amount: Decimal = Decimal("0")
    currency: str = "USD"
    status: str = ""
    payout_date: str = ""


@dataclass
class ParsedDispute:
    """
    Platform-agnostic representation of a chargeback/dispute.
    """

    platform_dispute_id: str = ""
    platform_order_id: str = ""
    order_name: str = ""
    dispute_amount: Decimal = Decimal("0")
    chargeback_fee: Decimal = Decimal("0")
    currency: str = "USD"
    reason: str = ""
    status: str = ""
    evidence_due_by: str | None = None


@dataclass
class ParsedFulfillment:
    """
    Platform-agnostic representation of a fulfillment/shipment.

    Used to trigger COGS recognition and inventory deduction.
    """

    platform_fulfillment_id: str = ""
    platform_order_id: str = ""
    order_name: str = ""
    fulfillment_date: str = ""
    total_cogs: Decimal = Decimal("0")
    # Each entry: {sku, item_public_id, item_code, warehouse_public_id,
    #              qty, unit_cost, cogs_value, cogs_account_id, inventory_account_id}
    cogs_lines: list[dict] = field(default_factory=list)
    unmatched_skus: list[str] = field(default_factory=list)
