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
class ParsedProviderTransaction:
    """One money movement inside a payment provider — a Stripe BalanceTransaction,
    a PayPal transaction, etc. — the grain *below* a payout. Populated by an
    adapter's pull/normalize step (ADR-0002). The canonical form of the brief's
    `ProviderTransaction`; a sole-writer projection materializes the durable
    read-model from these.
    """

    external_id: str = ""  # provider id for this txn (e.g. txn_…, bt_…)
    txn_type: str = ""  # charge / refund / fee / dispute / adjustment / reserve / payout / transfer
    gross_amount: Decimal = Decimal("0")
    fee_amount: Decimal = Decimal("0")
    net_amount: Decimal = Decimal("0")
    currency: str = "USD"
    status: str = ""
    occurred_at: str = ""  # when the money moved (created)
    available_at: str = ""  # when funds become available / land in a payout
    payout_external_id: str = ""  # the payout this txn settled in (po_…), if any
    source_id: str = ""  # the charge / payment_intent this txn relates to
    source_order_reference: str = ""  # merchant/platform order ref, if resolvable


@dataclass
class ParsedPayoutLine:
    """One transaction's contribution to a provider payout — the breakdown that
    answers 'what made up this payout?' (the brief's `ProviderPayoutLine` and the
    Stage-2 per-batch detail). Feeds `PaymentSettlementReceivedData.line_items[]`.
    """

    payout_external_id: str = ""
    transaction_external_id: str = ""
    line_type: str = ""  # charge / refund / fee / dispute / adjustment
    gross_amount: Decimal = Decimal("0")
    fee_amount: Decimal = Decimal("0")
    net_amount: Decimal = Decimal("0")
    currency: str = "USD"


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a payment/commerce connector can do, so the engine and UI adapt per
    provider (Stripe pulls rich balance transactions; a CSV gateway only imports
    a file). Declared by each connector's `capabilities` property — a typed,
    dict-like descriptor, NOT a table (ADR-0002).
    """

    # Outbound, accounting-grade reads
    pull_payouts: bool = False
    pull_transactions: bool = False  # balance-transaction-level pull
    payout_line_breakdown: bool = False  # can attribute a payout to its txns
    webhooks: bool = False
    # Money lifecycle modeled
    refunds: bool = True
    disputes: bool = False
    dispute_resolution: bool = False  # won / lost / funds-withdrawn
    reserves: bool = False
    adjustments: bool = False
    multi_currency: bool = False  # realized-FX possible
    # Money shape / connection
    fee_in_payout: str = "none"  # "given" (in payout) | "derived" (from txns) | "none"
    auth: str = ""  # "restricted_read_key" | "oauth" | "offline_token" | "csv"
    csv_import: bool = False  # settlement-CSV import path


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
