# platform_connectors/projections.py
"""
Platform-agnostic accounting projection.

Consumes PLATFORM_* events and creates journal entries using the shared
JE builder. This single projection handles all platform connectors that
emit generic platform.* events.

Shopify keeps its own ShopifyAccountingHandler for backward compatibility.
New platforms (Stripe, WooCommerce, etc.) emit PLATFORM_* events and are
handled here.
"""

import logging
from datetime import date, datetime
from decimal import Decimal

from accounting.mappings import ModuleAccountMapping
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection

from .je_builder import JELine, JERequest, build_journal_entry

logger = logging.getLogger(__name__)

PROJECTION_NAME = "platform_accounting"

# Standard account roles expected across all platforms
ROLE_CLEARING = "PLATFORM_CLEARING"
ROLE_SALES_REVENUE = "SALES_REVENUE"
ROLE_SALES_TAX = "SALES_TAX_PAYABLE"
ROLE_SHIPPING_REVENUE = "SHIPPING_REVENUE"
ROLE_CASH_BANK = "CASH_BANK"
ROLE_PROCESSING_FEES = "PAYMENT_PROCESSING_FEES"
ROLE_CHARGEBACK_EXPENSE = "CHARGEBACK_EXPENSE"


def _parse_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value).date()
    return None


class PlatformAccountingProjection(BaseProjection):
    """
    Creates journal entries from generic platform commerce events.

    Order paid:
        DR Platform Clearing    (total_price)
        CR Sales Revenue        (subtotal)
        CR Sales Tax            (total_tax)      — if > 0
        CR Shipping Revenue     (total_shipping)  — if > 0

    Refund created:
        DR Sales Revenue        (amount)
        CR Platform Clearing    (amount)

    Payout settled:
        DR Cash/Bank            (net_amount)
        DR Processing Fees      (fees)
        CR Platform Clearing    (gross_amount)

    Dispute created:
        DR Chargeback Expense   (dispute_amount)
        DR Processing Fees      (chargeback_fee)
        CR Platform Clearing    (total)
    """

    @property
    def name(self) -> str:
        return PROJECTION_NAME

    @property
    def consumes(self):
        return [
            EventTypes.PLATFORM_ORDER_PAID,
            EventTypes.PLATFORM_REFUND_CREATED,
            EventTypes.PLATFORM_PAYOUT_SETTLED,
            EventTypes.PLATFORM_DISPUTE_CREATED,
            EventTypes.PLATFORM_FULFILLMENT_CREATED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        metadata = event.metadata or {}
        if metadata.get("source_projection") == PROJECTION_NAME:
            return

        data = event.get_data()
        company = event.company
        platform_slug = data.get("platform_slug", "unknown")
        module_key = f"platform_{platform_slug}"

        mapping = ModuleAccountMapping.get_mapping(company, module_key)
        if not mapping:
            logger.warning(
                "No ModuleAccountMapping for %s, company %s — skipping %s",
                module_key,
                company,
                event.event_type,
            )
            return

        # Resolve dimension context for JE line tagging
        from platform_connectors.dimensions import resolve_platform_dimensions

        dimension_context = resolve_platform_dimensions(company, platform_slug)

        handler = {
            EventTypes.PLATFORM_ORDER_PAID: self._handle_order_paid,
            EventTypes.PLATFORM_REFUND_CREATED: self._handle_refund_created,
            EventTypes.PLATFORM_PAYOUT_SETTLED: self._handle_payout_settled,
            EventTypes.PLATFORM_DISPUTE_CREATED: self._handle_dispute_created,
        }.get(event.event_type)

        if handler:
            handler(event, data, mapping, platform_slug, dimension_context)

    def _handle_order_paid(self, event, data, mapping, platform_slug, dimension_context=None):
        clearing = mapping.get(ROLE_CLEARING)
        revenue = mapping.get(ROLE_SALES_REVENUE)
        if not clearing or not revenue:
            logger.warning(
                "Account mapping missing CLEARING or SALES_REVENUE for %s — skipping",
                platform_slug,
            )
            return

        total_price = Decimal(str(data.get("amount", "0")))
        subtotal = Decimal(str(data.get("subtotal", "0")))
        total_tax = Decimal(str(data.get("total_tax", "0")))
        total_shipping = Decimal(str(data.get("total_shipping", "0")))
        order_name = data.get("order_name", data.get("order_number", ""))
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()
        currency = data.get("currency") or "USD"
        memo = f"{platform_slug.title()} order: {order_name}"

        if total_price <= 0:
            return

        lines = [
            JELine(account=clearing, description=memo, debit=total_price),
        ]

        revenue_amount = subtotal if subtotal > 0 else total_price - total_tax
        lines.append(JELine(account=revenue, description=memo, credit=revenue_amount))

        tax_account = mapping.get(ROLE_SALES_TAX)
        if total_tax > 0 and tax_account:
            lines.append(
                JELine(
                    account=tax_account,
                    description=f"Sales tax: {order_name}",
                    credit=total_tax,
                )
            )

        shipping_account = mapping.get(ROLE_SHIPPING_REVENUE)
        if total_shipping > 0:
            ship_acct = shipping_account or revenue
            lines.append(
                JELine(
                    account=ship_acct,
                    description=f"Shipping: {order_name}",
                    credit=total_shipping,
                )
            )

        entry = build_journal_entry(
            JERequest(
                company=event.company,
                entry_date=entry_date,
                memo=memo,
                source_module=f"platform_{platform_slug}",
                source_document=data.get("platform_order_id", ""),
                currency=currency,
                lines=lines,
                caused_by_event=event,
                projection_name=PROJECTION_NAME,
                posted_by_email=f"system@{platform_slug}",
                dimension_context=dimension_context or {},
            )
        )

        if entry:
            logger.info(
                "Created JE %s for %s order %s",
                entry.public_id,
                platform_slug,
                order_name,
            )

    def _handle_refund_created(self, event, data, mapping, platform_slug, dimension_context=None):
        clearing = mapping.get(ROLE_CLEARING)
        revenue = mapping.get(ROLE_SALES_REVENUE)
        if not clearing or not revenue:
            return

        amount = Decimal(str(data.get("amount", "0")))
        order_number = data.get("order_number", "")
        refund_id = data.get("platform_refund_id", "")
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()
        currency = data.get("currency") or "USD"
        memo = f"{platform_slug.title()} refund: Order {order_number} (Ref {refund_id})"

        if amount <= 0:
            return

        entry = build_journal_entry(
            JERequest(
                company=event.company,
                entry_date=entry_date,
                memo=memo,
                source_module=f"platform_{platform_slug}",
                source_document=str(refund_id),
                currency=currency,
                lines=[
                    JELine(account=revenue, description=memo, debit=amount),
                    JELine(account=clearing, description=memo, credit=amount),
                ],
                caused_by_event=event,
                projection_name=PROJECTION_NAME,
                posted_by_email=f"system@{platform_slug}",
                dimension_context=dimension_context or {},
            )
        )

        if entry:
            logger.info(
                "Created refund JE %s for %s order %s",
                entry.public_id,
                platform_slug,
                order_number,
            )

    def _handle_payout_settled(self, event, data, mapping, platform_slug, dimension_context=None):
        clearing = mapping.get(ROLE_CLEARING)
        bank = mapping.get(ROLE_CASH_BANK)
        if not clearing or not bank:
            logger.warning(
                "Account mapping missing CLEARING or CASH_BANK for %s — skipping payout",
                platform_slug,
            )
            return

        fees_account = mapping.get(ROLE_PROCESSING_FEES)

        gross_amount = Decimal(str(data.get("gross_amount", "0")))
        fees = Decimal(str(data.get("fees", "0")))
        net_amount = Decimal(str(data.get("net_amount", "0")))
        payout_id = data.get("platform_payout_id", "")
        entry_date = _parse_date(data.get("payout_date") or data.get("transaction_date")) or event.created_at.date()
        currency = data.get("currency") or "USD"
        memo = f"{platform_slug.title()} payout: {payout_id}"

        if gross_amount == 0:
            return

        is_negative = gross_amount < 0
        abs_gross = abs(gross_amount)
        abs_net = abs(net_amount)

        lines = []
        if is_negative:
            # Negative payout: refunds > charges in this period
            lines.append(JELine(account=clearing, description=memo, debit=abs_gross))
            if fees > 0 and fees_account:
                lines.append(
                    JELine(
                        account=fees_account,
                        description=f"Processing fees: {payout_id}",
                        debit=fees,
                    )
                )
            lines.append(JELine(account=bank, description=memo, credit=abs_net))
        else:
            # Normal positive payout
            lines.append(JELine(account=bank, description=memo, debit=abs_net))
            if fees > 0 and fees_account:
                lines.append(
                    JELine(
                        account=fees_account,
                        description=f"Processing fees: {payout_id}",
                        debit=fees,
                    )
                )
            lines.append(JELine(account=clearing, description=memo, credit=abs_gross))

        entry = build_journal_entry(
            JERequest(
                company=event.company,
                entry_date=entry_date,
                memo=memo,
                source_module=f"platform_{platform_slug}",
                source_document=str(payout_id),
                currency=currency,
                lines=lines,
                caused_by_event=event,
                projection_name=PROJECTION_NAME,
                posted_by_email=f"system@{platform_slug}",
                dimension_context=dimension_context or {},
            )
        )

        if entry:
            logger.info(
                "Created payout JE %s for %s payout %s",
                entry.public_id,
                platform_slug,
                payout_id,
            )

    def _handle_dispute_created(self, event, data, mapping, platform_slug, dimension_context=None):
        clearing = mapping.get(ROLE_CLEARING)
        chargeback = mapping.get(ROLE_CHARGEBACK_EXPENSE)
        if not clearing or not chargeback:
            logger.warning(
                "Account mapping missing CLEARING or CHARGEBACK_EXPENSE for %s",
                platform_slug,
            )
            return

        fees_account = mapping.get(ROLE_PROCESSING_FEES)

        dispute_amount = Decimal(str(data.get("dispute_amount", "0")))
        chargeback_fee = Decimal(str(data.get("chargeback_fee", "0")))
        dispute_id = data.get("platform_dispute_id", "")
        order_name = data.get("order_name", "")
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()
        currency = data.get("currency") or "USD"
        memo = f"{platform_slug.title()} chargeback: {order_name} (Dispute {dispute_id})"
        total = dispute_amount + chargeback_fee

        if total <= 0:
            return

        lines = [
            JELine(account=chargeback, description=memo, debit=dispute_amount),
        ]
        if chargeback_fee > 0 and fees_account:
            lines.append(
                JELine(
                    account=fees_account,
                    description=f"Chargeback fee: {dispute_id}",
                    debit=chargeback_fee,
                )
            )
        lines.append(JELine(account=clearing, description=memo, credit=total))

        entry = build_journal_entry(
            JERequest(
                company=event.company,
                entry_date=entry_date,
                memo=memo,
                source_module=f"platform_{platform_slug}",
                source_document=str(dispute_id),
                currency=currency,
                lines=lines,
                caused_by_event=event,
                projection_name=PROJECTION_NAME,
                posted_by_email=f"system@{platform_slug}",
                dimension_context=dimension_context or {},
            )
        )

        if entry:
            logger.info(
                "Created chargeback JE %s for %s dispute %s",
                entry.public_id,
                platform_slug,
                dispute_id,
            )
