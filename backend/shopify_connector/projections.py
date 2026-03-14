# shopify_connector/projections.py
"""
Shopify accounting projection.

Consumes Shopify financial events and creates corresponding journal entries
using ModuleAccountMapping for account resolution.

Account roles used by this module:
    SALES_REVENUE — Product/service revenue
    ACCOUNTS_RECEIVABLE — Customer receivable (cleared when Stripe payout lands)
    SALES_TAX_PAYABLE — Collected sales tax
    SALES_DISCOUNTS — Discount contra-revenue
    CASH_BANK — Cash/bank for direct payments
"""

import logging
import uuid
from decimal import Decimal
from datetime import datetime, date

from django.utils import timezone

from events.types import EventTypes, JournalEntryPostedData
from events.models import BusinessEvent
from events.emitter import emit_event_no_actor
from projections.base import BaseProjection
from projections.models import FiscalPeriod
from accounting.mappings import ModuleAccountMapping
from accounting.models import JournalEntry, JournalLine


logger = logging.getLogger(__name__)

MODULE_NAME = "shopify_connector"
PROJECTION_NAME = "shopify_accounting"

# Account roles
ROLE_SALES_REVENUE = "SALES_REVENUE"
ROLE_ACCOUNTS_RECEIVABLE = "ACCOUNTS_RECEIVABLE"
ROLE_SALES_TAX_PAYABLE = "SALES_TAX_PAYABLE"
ROLE_SALES_DISCOUNTS = "SALES_DISCOUNTS"
ROLE_SHIPPING_REVENUE = "SHIPPING_REVENUE"
ROLE_CASH_BANK = "CASH_BANK"


def _parse_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    return value


def _resolve_period(company, entry_date):
    fp = FiscalPeriod.objects.filter(
        company=company,
        start_date__lte=entry_date,
        end_date__gte=entry_date,
        period_type=FiscalPeriod.PeriodType.NORMAL,
    ).first()
    if fp:
        return fp.period
    return entry_date.month


class ShopifyAccountingProjection(BaseProjection):
    """
    Creates journal entries from Shopify financial events.

    Order paid:
        DR Accounts Receivable   (total_price)
        CR Sales Revenue         (subtotal)
        CR Sales Tax Payable     (total_tax)     — if > 0
        DR Sales Discounts       (total_discounts) — if > 0
        CR Sales Discounts Offset                   — netted into revenue

    Refund created:
        DR Sales Revenue         (amount)
        CR Accounts Receivable   (amount)
    """

    @property
    def name(self) -> str:
        return PROJECTION_NAME

    @property
    def consumes(self):
        return [
            EventTypes.SHOPIFY_ORDER_PAID,
            EventTypes.SHOPIFY_REFUND_CREATED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        metadata = event.metadata or {}
        if metadata.get("source_projection") == PROJECTION_NAME:
            return

        data = event.get_data()
        company = event.company

        mapping = ModuleAccountMapping.get_mapping(company, MODULE_NAME)
        if not mapping:
            logger.warning(
                "No ModuleAccountMapping for shopify_connector module, "
                "company %s — skipping %s",
                company, event.event_type,
            )
            return

        handler = {
            EventTypes.SHOPIFY_ORDER_PAID: self._handle_order_paid,
            EventTypes.SHOPIFY_REFUND_CREATED: self._handle_refund_created,
        }.get(event.event_type)

        if handler:
            handler(event, data, mapping)

    def _handle_order_paid(self, event, data, mapping):
        """
        Multi-line journal entry for a paid order.

        DR Accounts Receivable   total_price
        CR Sales Revenue         subtotal (net of discounts)
        CR Sales Tax Payable     total_tax (if > 0)

        If there are discounts, they reduce the subtotal that becomes revenue.
        Shopify's subtotal_price already excludes discounts, so:
          total_price = subtotal_price + total_tax
        """
        ar = mapping.get(ROLE_ACCOUNTS_RECEIVABLE)
        revenue = mapping.get(ROLE_SALES_REVENUE)
        if not ar or not revenue:
            logger.warning(
                "Shopify account mapping missing ACCOUNTS_RECEIVABLE or SALES_REVENUE "
                "for company %s — skipping order %s",
                event.company, data.get("order_number"),
            )
            return

        tax_account = mapping.get(ROLE_SALES_TAX_PAYABLE)
        shipping_account = mapping.get(ROLE_SHIPPING_REVENUE)

        total_price = Decimal(str(data.get("amount", "0")))
        subtotal = Decimal(str(data.get("subtotal", "0")))
        total_tax = Decimal(str(data.get("total_tax", "0")))
        total_shipping = Decimal(str(data.get("total_shipping", "0")))
        order_name = data.get("order_name", data.get("order_number", ""))
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()
        currency = data.get("currency") or getattr(event.company, "default_currency", "USD")
        memo = f"Shopify order: {order_name}"

        if total_price <= 0:
            logger.warning(
                "Skipping Shopify order %s — non-positive amount %s",
                order_name, total_price,
            )
            return

        # Idempotency
        if JournalEntry.objects.filter(
            company=event.company, memo=memo,
            status=JournalEntry.Status.POSTED,
        ).exists():
            logger.info("Journal entry already exists for '%s' — skipping", memo)
            return

        period = _resolve_period(event.company, entry_date)
        now = timezone.now()

        entry = JournalEntry.objects.projection().create(
            company=event.company,
            public_id=uuid.uuid4(),
            date=entry_date,
            period=period,
            memo=memo,
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            posted_at=now,
            currency=currency,
            exchange_rate=Decimal("1.0"),
            source_module="shopify_connector",
            source_document=str(data.get("shopify_order_id", "")),
        )

        lines = []
        line_no = 0

        # DR Accounts Receivable — total price
        line_no += 1
        lines.append(JournalLine(
            entry=entry, company=event.company,
            public_id=uuid.uuid4(), line_no=line_no,
            account=ar, description=memo,
            debit=total_price, credit=Decimal("0"),
            currency=currency, exchange_rate=Decimal("1.0"),
        ))

        # CR Sales Revenue — subtotal (after discounts)
        revenue_amount = subtotal if subtotal > 0 else total_price - total_tax
        line_no += 1
        lines.append(JournalLine(
            entry=entry, company=event.company,
            public_id=uuid.uuid4(), line_no=line_no,
            account=revenue, description=memo,
            debit=Decimal("0"), credit=revenue_amount,
            currency=currency, exchange_rate=Decimal("1.0"),
        ))

        # CR Sales Tax Payable — if applicable
        if total_tax > 0 and tax_account:
            line_no += 1
            lines.append(JournalLine(
                entry=entry, company=event.company,
                public_id=uuid.uuid4(), line_no=line_no,
                account=tax_account, description=f"Sales tax: {order_name}",
                debit=Decimal("0"), credit=total_tax,
                currency=currency, exchange_rate=Decimal("1.0"),
            ))

        # CR Shipping Revenue — if applicable
        if total_shipping > 0:
            ship_acct = shipping_account or revenue  # fall back to sales revenue
            line_no += 1
            lines.append(JournalLine(
                entry=entry, company=event.company,
                public_id=uuid.uuid4(), line_no=line_no,
                account=ship_acct, description=f"Shipping: {order_name}",
                debit=Decimal("0"), credit=total_shipping,
                currency=currency, exchange_rate=Decimal("1.0"),
            ))

        JournalLine.objects.projection().bulk_create(lines)

        # Emit JOURNAL_ENTRY_POSTED for balance projection
        lines_data = []
        for line in lines:
            lines_data.append({
                "line_public_id": str(line.public_id),
                "line_no": line.line_no,
                "account_public_id": str(line.account.public_id),
                "account_code": line.account.code,
                "description": line.description,
                "debit": str(line.debit),
                "credit": str(line.credit),
                "currency": currency,
                "exchange_rate": "1.0",
            })

        emit_event_no_actor(
            company=event.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry.public_id),
            idempotency_key=f"shopify.je.posted:{entry.public_id}",
            metadata={"source_projection": PROJECTION_NAME},
            data=JournalEntryPostedData(
                entry_public_id=str(entry.public_id),
                entry_number="",
                date=str(entry_date),
                memo=memo,
                kind="NORMAL",
                posted_at=str(now),
                posted_by_id=0,
                posted_by_email="system@shopify",
                total_debit=str(total_price),
                total_credit=str(total_price),
                lines=lines_data,
                period=period,
                currency=currency,
                exchange_rate="1.0",
            ),
            caused_by_event=event,
        )

        # Update local order record
        from shopify_connector.models import ShopifyOrder
        ShopifyOrder.objects.filter(
            company=event.company,
            shopify_order_id=data.get("shopify_order_id"),
        ).update(
            status=ShopifyOrder.Status.PROCESSED,
            journal_entry_id=entry.public_id,
        )

        logger.info(
            "Created journal entry %s for Shopify order %s (event %s)",
            entry.public_id, order_name, event.id,
        )

    def _handle_refund_created(self, event, data, mapping):
        """
        Reversal entry for a refund.
        DR Sales Revenue / CR Accounts Receivable
        """
        ar = mapping.get(ROLE_ACCOUNTS_RECEIVABLE)
        revenue = mapping.get(ROLE_SALES_REVENUE)
        if not ar or not revenue:
            logger.warning(
                "Shopify account mapping missing for refund — skipping"
            )
            return

        amount = Decimal(str(data.get("amount", "0")))
        order_number = data.get("order_number", "")
        refund_id = data.get("shopify_refund_id", "")
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()
        currency = data.get("currency") or getattr(event.company, "default_currency", "USD")
        memo = f"Shopify refund: Order {order_number} (Ref {refund_id})"

        if amount <= 0:
            return

        if JournalEntry.objects.filter(
            company=event.company, memo=memo,
            status=JournalEntry.Status.POSTED,
        ).exists():
            return

        period = _resolve_period(event.company, entry_date)
        now = timezone.now()

        entry = JournalEntry.objects.projection().create(
            company=event.company,
            public_id=uuid.uuid4(),
            date=entry_date,
            period=period,
            memo=memo,
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            posted_at=now,
            currency=currency,
            exchange_rate=Decimal("1.0"),
            source_module="shopify_connector",
            source_document=str(refund_id),
        )

        debit_line = JournalLine(
            entry=entry, company=event.company,
            public_id=uuid.uuid4(), line_no=1,
            account=revenue, description=memo,
            debit=amount, credit=Decimal("0"),
            currency=currency, exchange_rate=Decimal("1.0"),
        )
        credit_line = JournalLine(
            entry=entry, company=event.company,
            public_id=uuid.uuid4(), line_no=2,
            account=ar, description=memo,
            debit=Decimal("0"), credit=amount,
            currency=currency, exchange_rate=Decimal("1.0"),
        )
        JournalLine.objects.projection().bulk_create([debit_line, credit_line])

        lines_data = [
            {
                "line_public_id": str(debit_line.public_id),
                "line_no": 1,
                "account_public_id": str(revenue.public_id),
                "account_code": revenue.code,
                "description": memo,
                "debit": str(amount),
                "credit": "0",
                "currency": currency,
                "exchange_rate": "1.0",
            },
            {
                "line_public_id": str(credit_line.public_id),
                "line_no": 2,
                "account_public_id": str(ar.public_id),
                "account_code": ar.code,
                "description": memo,
                "debit": "0",
                "credit": str(amount),
                "currency": currency,
                "exchange_rate": "1.0",
            },
        ]

        emit_event_no_actor(
            company=event.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry.public_id),
            idempotency_key=f"shopify.refund.je.posted:{entry.public_id}",
            metadata={"source_projection": PROJECTION_NAME},
            data=JournalEntryPostedData(
                entry_public_id=str(entry.public_id),
                entry_number="",
                date=str(entry_date),
                memo=memo,
                kind="NORMAL",
                posted_at=str(now),
                posted_by_id=0,
                posted_by_email="system@shopify",
                total_debit=str(amount),
                total_credit=str(amount),
                lines=lines_data,
                period=period,
                currency=currency,
                exchange_rate="1.0",
            ),
            caused_by_event=event,
        )

        from shopify_connector.models import ShopifyRefund
        ShopifyRefund.objects.filter(
            company=event.company,
            shopify_refund_id=data.get("shopify_refund_id"),
        ).update(
            status=ShopifyRefund.Status.PROCESSED,
            journal_entry_id=entry.public_id,
        )

        logger.info(
            "Created refund journal entry %s for order %s",
            entry.public_id, order_number,
        )

    def _clear_projected_data(self, company) -> None:
        """Clear Shopify-generated journal entries for rebuild."""
        JournalEntry.objects.filter(
            company=company,
            source_module="shopify_connector",
        ).delete()
