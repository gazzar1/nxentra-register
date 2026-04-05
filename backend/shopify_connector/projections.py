# shopify_connector/projections.py
"""
Shopify accounting projection.

Consumes Shopify financial events and creates corresponding journal entries
using ModuleAccountMapping for account resolution.

Account roles used by this module:
    SALES_REVENUE — Product/service revenue
    SHOPIFY_CLEARING — Platform clearing (holds funds until payout)
    SALES_TAX_PAYABLE — Collected sales tax
    SALES_DISCOUNTS — Discount contra-revenue
    CASH_BANK — Cash/bank for direct payments
    PAYMENT_PROCESSING_FEES — Payment processing fees (Shopify Payments)
"""

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone

from accounting.commands import _next_company_sequence
from accounting.mappings import ModuleAccountMapping
from accounting.models import ExchangeRate, JournalEntry, JournalLine
from events.emitter import emit_event_no_actor
from events.models import BusinessEvent
from events.types import EventTypes, JournalEntryPostedData
from projections.base import BaseProjection
from projections.models import FiscalPeriod

logger = logging.getLogger(__name__)

MODULE_NAME = "shopify_connector"
PROJECTION_NAME = "shopify_accounting"

# Account roles
ROLE_SALES_REVENUE = "SALES_REVENUE"
ROLE_SHOPIFY_CLEARING = "SHOPIFY_CLEARING"
ROLE_SALES_TAX_PAYABLE = "SALES_TAX_PAYABLE"
ROLE_SALES_DISCOUNTS = "SALES_DISCOUNTS"
ROLE_SHIPPING_REVENUE = "SHIPPING_REVENUE"
ROLE_CASH_BANK = "CASH_BANK"
ROLE_PROCESSING_FEES = "PAYMENT_PROCESSING_FEES"
ROLE_CHARGEBACK_EXPENSE = "CHARGEBACK_EXPENSE"


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


def _resolve_store_domain(company, store_public_id):
    """Resolve shop_domain from a store_public_id."""
    if not store_public_id:
        return None
    from shopify_connector.models import ShopifyStore
    try:
        return ShopifyStore.objects.values_list("shop_domain", flat=True).get(
            company=company, public_id=store_public_id,
        )
    except ShopifyStore.DoesNotExist:
        return None


class MissingExchangeRate(Exception):
    """Raised when a required exchange rate is not configured."""
    pass


def _resolve_exchange_rate(company, currency, entry_date):
    """
    Resolve exchange rate for converting currency to functional currency.

    Returns (exchange_rate, is_foreign) tuple.
    If currency == functional currency, returns (Decimal("1.0"), False).
    Raises MissingExchangeRate if no rate is found for a foreign currency.
    """
    functional = company.functional_currency or company.default_currency or "USD"
    if currency == functional:
        return Decimal("1.0"), False

    rate = ExchangeRate.get_rate(company, currency, functional, entry_date)
    if rate:
        return rate, True

    raise MissingExchangeRate(
        f"No exchange rate found for {currency}→{functional} on {entry_date}. "
        f"Add the rate via Settings → Exchange Rates before processing."
    )


def _convert_amount(amount, exchange_rate):
    """Convert a foreign amount to functional currency."""
    return (amount * exchange_rate).quantize(Decimal("0.01"))


def _fix_fx_rounding(lines, entry, company, currency, fx_rate):
    """
    Fix penny rounding imbalance caused by independent per-line FX conversion.

    When multiple foreign-currency lines are each rounded to 2 decimal places,
    the sum of credits may differ from the sum of debits by a small amount
    (typically 0.01).

    Following SAP/Oracle/NetSuite convention, adds a visible rounding line
    to a dedicated FX Rounding account rather than silently adjusting
    existing lines. Only applies for trivial imbalances (≤ 0.05).
    """
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account

    total_debit = sum(l.debit for l in lines)
    total_credit = sum(l.credit for l in lines)
    diff = total_debit - total_credit

    if diff == Decimal("0"):
        return  # Already balanced

    if abs(diff) > Decimal("0.05"):
        return  # Too large to be a rounding error — don't touch

    # Find the FX rounding account (prefer core mapping, fallback to role)
    rounding_account = ModuleAccountMapping.get_account(company, "core", "FX_ROUNDING")
    if not rounding_account:
        rounding_account = Account.objects.filter(
            company=company,
            role=Account.AccountRole.FX_ROUNDING,
            is_postable=True,
        ).first()

    if not rounding_account:
        # Fallback: adjust the largest line (pre-seed behavior)
        if diff > 0:
            credit_lines = [l for l in lines if l.credit > 0]
            target = max(credit_lines, key=lambda l: l.credit) if credit_lines else max(lines, key=lambda l: l.debit)
            if target.credit > 0:
                target.credit += diff
            else:
                target.debit -= diff
        else:
            debit_lines = [l for l in lines if l.debit > 0]
            target = max(debit_lines, key=lambda l: l.debit) if debit_lines else max(lines, key=lambda l: l.credit)
            if target.debit > 0:
                target.debit -= diff
            else:
                target.credit += diff
        logger.debug("FX rounding adjustment (no rounding account): %s on line %s", diff, target.line_no)
        return

    # Add a dedicated rounding line
    next_line_no = max(l.line_no for l in lines) + 1
    if diff > 0:
        # Debits exceed credits — add credit rounding line
        rounding_line = JournalLine(
            entry=entry, company=company,
            public_id=uuid.uuid4(), line_no=next_line_no,
            account=rounding_account,
            description="FX rounding adjustment",
            debit=Decimal("0"), credit=diff,
            currency=currency, exchange_rate=fx_rate,
        )
    else:
        # Credits exceed debits — add debit rounding line
        rounding_line = JournalLine(
            entry=entry, company=company,
            public_id=uuid.uuid4(), line_no=next_line_no,
            account=rounding_account,
            description="FX rounding adjustment",
            debit=abs(diff), credit=Decimal("0"),
            currency=currency, exchange_rate=fx_rate,
        )

    lines.append(rounding_line)
    logger.info(
        "FX rounding line added: %s %s to account %s",
        "CR" if diff > 0 else "DR", abs(diff), rounding_account.code,
    )


def _ensure_dimension_and_value(company, dim_code, dim_name, dim_name_ar, val_code, val_name):
    """
    Ensure an AnalysisDimension and AnalysisDimensionValue exist.
    Creates them if missing. Idempotent.
    """
    from accounting.models import AnalysisDimension, AnalysisDimensionValue

    dim, _ = AnalysisDimension.objects.projection().get_or_create(
        company=company, code=dim_code,
        defaults={
            "name": dim_name,
            "name_ar": dim_name_ar,
            "dimension_kind": AnalysisDimension.DimensionKind.ANALYTIC,
            "is_required_on_posting": False,
            "is_active": True,
        },
    )

    AnalysisDimensionValue.objects.projection().get_or_create(
        dimension=dim, code=val_code, company=company,
        defaults={
            "name": val_name,
            "is_active": True,
        },
    )


class ShopifyAccountingProjection(BaseProjection):
    """
    Creates journal entries from Shopify financial events.

    Order paid:
        DR Shopify Clearing      (total_price)
        CR Sales Revenue         (subtotal)
        CR Sales Tax Payable     (total_tax)     — if > 0
        CR Shipping Revenue      (total_shipping) — if > 0

    Refund created:
        DR Sales Revenue         (amount)
        CR Shopify Clearing      (amount)

    Payout settled:
        DR Cash/Bank             (net_amount)
        DR Processing Fees       (fees)
        CR Shopify Clearing      (gross_amount)

    Order fulfilled (COGS):
        DR Cost of Goods Sold    (qty × avg_cost per item)
        CR Inventory             (qty × avg_cost per item)
    """

    @property
    def name(self) -> str:
        return PROJECTION_NAME

    @property
    def consumes(self):
        return [
            EventTypes.SHOPIFY_ORDER_PAID,
            EventTypes.SHOPIFY_REFUND_CREATED,
            EventTypes.SHOPIFY_PAYOUT_SETTLED,
            EventTypes.SHOPIFY_ORDER_FULFILLED,
            EventTypes.SHOPIFY_DISPUTE_CREATED,
            EventTypes.SHOPIFY_DISPUTE_WON,
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

        # Resolve dimension context for tagging JE lines
        dimension_context = self._resolve_dimensions(company, data)

        handler = {
            EventTypes.SHOPIFY_ORDER_PAID: self._handle_order_paid,
            EventTypes.SHOPIFY_REFUND_CREATED: self._handle_refund_created,
            EventTypes.SHOPIFY_PAYOUT_SETTLED: self._handle_payout_settled,
            EventTypes.SHOPIFY_ORDER_FULFILLED: self._handle_order_fulfilled,
            EventTypes.SHOPIFY_DISPUTE_CREATED: self._handle_dispute_created,
            EventTypes.SHOPIFY_DISPUTE_WON: self._handle_dispute_won,
        }.get(event.event_type)

        if handler:
            try:
                handler(event, data, mapping, dimension_context)
            except MissingExchangeRate as exc:
                logger.error(
                    "Missing exchange rate for %s event %s: %s",
                    event.event_type, event.id, exc,
                )
                from accounts.models import Notification
                Notification.notify_company_admins(
                    company=company,
                    title="Missing exchange rate — Shopify entry skipped",
                    message=str(exc),
                    level=Notification.Level.ERROR,
                    source_module="shopify_connector",
                )

    def _resolve_dimensions(self, company, data):
        """
        Resolve dimension context for a Shopify event.

        Auto-creates dimensions and values from all available Shopify data:
        - CHANNEL: "Shopify" (sales channel)
        - PRODUCT: SKU from line items
        - CATEGORY: product_type from line items
        - VENDOR: vendor/brand from line items
        - REGION: shipping country
        - CITY: shipping city
        - SOURCE: order source (web, pos, mobile)
        - PAY_METHOD: payment gateway
        - PROMOTION: discount codes used
        - CAMPAIGN: order tags (e.g. "ramadan-sale")
        - REFERRER: referring site
        - CUST_SEGMENT: customer tags

        Returns dict like {"CHANNEL": "SHOPIFY", "PRODUCT": "TSH-001", ...}
        """
        context = {}

        # Look up raw payload for enriched data
        raw = {}
        shopify_order_id = data.get("shopify_order_id")
        if shopify_order_id:
            from shopify_connector.models import ShopifyOrder
            order_record = ShopifyOrder.objects.filter(
                company=company, shopify_order_id=shopify_order_id,
            ).first()
            if order_record and order_record.raw_payload:
                raw = order_record.raw_payload

        # 1. CHANNEL — always Shopify
        _ensure_dimension_and_value(company, "CHANNEL", "Sales Channel", "قناة البيع", "SHOPIFY", "Shopify")
        context["CHANNEL"] = "SHOPIFY"

        # 2. PRODUCT — SKU from first line item
        line_items = data.get("line_items", []) or raw.get("line_items", [])
        if line_items:
            first_item = line_items[0]
            sku = first_item.get("sku", "")
            title = first_item.get("title", "")
            if sku:
                _ensure_dimension_and_value(company, "PRODUCT", "Product", "المنتج", sku, title or sku)
                context["PRODUCT"] = sku

            # 3. CATEGORY — product_type
            product_type = first_item.get("product_type", "")
            if product_type:
                val_code = product_type.upper().replace(" ", "_")[:20]
                _ensure_dimension_and_value(company, "CATEGORY", "Product Category", "فئة المنتج", val_code, product_type)
                context["CATEGORY"] = val_code

            # 4. VENDOR — brand/supplier
            vendor = first_item.get("vendor", "")
            if vendor:
                val_code = vendor.upper().replace(" ", "_")[:20]
                _ensure_dimension_and_value(company, "VENDOR", "Vendor / Brand", "المورد / العلامة", val_code, vendor)
                context["VENDOR"] = val_code

        # 5. REGION — shipping country
        shipping = raw.get("shipping_address") or {}
        country = shipping.get("country", "") or shipping.get("country_code", "")
        if country:
            val_code = country.upper()[:20]
            country_name = shipping.get("country", country)
            _ensure_dimension_and_value(company, "REGION", "Region / Country", "المنطقة / الدولة", val_code, country_name)
            context["REGION"] = val_code

        # 6. CITY — shipping city
        city = shipping.get("city", "")
        if city:
            val_code = city.upper().replace(" ", "_")[:20]
            _ensure_dimension_and_value(company, "CITY", "City", "المدينة", val_code, city)
            context["CITY"] = val_code

        # 7. SOURCE — order source (web, pos, mobile, api)
        source_name = raw.get("source_name", "")
        if source_name:
            val_code = source_name.upper()[:20]
            _ensure_dimension_and_value(company, "SOURCE", "Order Source", "مصدر الطلب", val_code, source_name)
            context["SOURCE"] = val_code

        # 8. PAY_METHOD — payment gateway
        gateway = data.get("gateway", "") or raw.get("gateway", "")
        if gateway:
            val_code = gateway.upper().replace(" ", "_")[:20]
            _ensure_dimension_and_value(company, "PAY_METHOD", "Payment Method", "طريقة الدفع", val_code, gateway)
            context["PAY_METHOD"] = val_code

        # 9. PROMOTION — discount codes
        discount_codes = raw.get("discount_codes", [])
        if discount_codes:
            code = discount_codes[0].get("code", "")
            if code:
                val_code = code.upper()[:20]
                _ensure_dimension_and_value(company, "PROMOTION", "Promotion / Discount", "العرض / الخصم", val_code, code)
                context["PROMOTION"] = val_code

        # 10. CAMPAIGN — order tags
        tags_str = raw.get("tags", "")
        if tags_str:
            first_tag = tags_str.split(",")[0].strip()
            if first_tag:
                val_code = first_tag.upper().replace(" ", "_")[:20]
                _ensure_dimension_and_value(company, "CAMPAIGN", "Campaign / Tag", "الحملة", val_code, first_tag)
                context["CAMPAIGN"] = val_code

        # 11. REFERRER — referring site
        referring_site = raw.get("referring_site", "")
        if referring_site:
            # Extract domain from URL
            from urllib.parse import urlparse
            try:
                domain = urlparse(referring_site).netloc or referring_site
            except Exception:
                domain = referring_site
            val_code = domain.upper().replace(".", "_").replace("WWW_", "")[:20]
            _ensure_dimension_and_value(company, "REFERRER", "Referrer", "المُحيل", val_code, domain)
            context["REFERRER"] = val_code

        # 12. CUST_SEGMENT — customer tags
        customer = raw.get("customer", {})
        customer_tags = customer.get("tags", "")
        if customer_tags:
            first_tag = customer_tags.split(",")[0].strip()
            if first_tag:
                val_code = first_tag.upper().replace(" ", "_")[:20]
                _ensure_dimension_and_value(company, "CUST_SEGMENT", "Customer Segment", "شريحة العملاء", val_code, first_tag)
                context["CUST_SEGMENT"] = val_code

        # Also include platform connector dimensions if available
        store_public_id = data.get("store_public_id")
        shop_domain = _resolve_store_domain(company, store_public_id)
        try:
            from platform_connectors.dimensions import resolve_platform_dimensions
            platform_dims = resolve_platform_dimensions(company, "shopify", shop_domain)
            context.update(platform_dims)
        except Exception:
            pass

        return context

    def _attach_dimensions(self, company, lines, dimension_context):
        """Attach dimension tags to journal lines."""
        if not dimension_context:
            return
        try:
            from platform_connectors.je_builder import _attach_dimensions
            _attach_dimensions(company, lines, dimension_context)
        except Exception:
            logger.debug("Could not attach dimensions — platform_connectors not available")

    def _handle_order_paid(self, event, data, mapping, dimension_context=None):
        """
        Multi-line journal entry for a paid order.

        DR Accounts Receivable   total_price
        CR Sales Revenue         subtotal (net of discounts)
        CR Sales Tax Payable     total_tax (if > 0)

        If there are discounts, they reduce the subtotal that becomes revenue.
        Shopify's subtotal_price already excludes discounts, so:
          total_price = subtotal_price + total_tax
        """
        ar = mapping.get(ROLE_SHOPIFY_CLEARING)
        revenue = mapping.get(ROLE_SALES_REVENUE)
        if not ar or not revenue:
            logger.warning(
                "Shopify account mapping missing SHOPIFY_CLEARING or SALES_REVENUE "
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

        # Multi-currency: resolve exchange rate
        fx_rate, is_foreign = _resolve_exchange_rate(event.company, currency, entry_date)

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
            exchange_rate=fx_rate,
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
            debit=_convert_amount(total_price, fx_rate) if is_foreign else total_price,
            credit=Decimal("0"),
            amount_currency=total_price if is_foreign else None,
            currency=currency, exchange_rate=fx_rate,
        ))

        # CR Sales Revenue — subtotal (after discounts)
        revenue_amount = subtotal if subtotal > 0 else total_price - total_tax
        line_no += 1
        lines.append(JournalLine(
            entry=entry, company=event.company,
            public_id=uuid.uuid4(), line_no=line_no,
            account=revenue, description=memo,
            debit=Decimal("0"),
            credit=_convert_amount(revenue_amount, fx_rate) if is_foreign else revenue_amount,
            amount_currency=-revenue_amount if is_foreign else None,
            currency=currency, exchange_rate=fx_rate,
        ))

        # CR Sales Tax Payable — if applicable
        if total_tax > 0 and tax_account:
            line_no += 1
            lines.append(JournalLine(
                entry=entry, company=event.company,
                public_id=uuid.uuid4(), line_no=line_no,
                account=tax_account, description=f"Sales tax: {order_name}",
                debit=Decimal("0"),
                credit=_convert_amount(total_tax, fx_rate) if is_foreign else total_tax,
                amount_currency=-total_tax if is_foreign else None,
                currency=currency, exchange_rate=fx_rate,
            ))

        # CR Shipping Revenue — if applicable
        if total_shipping > 0:
            ship_acct = shipping_account or revenue  # fall back to sales revenue
            line_no += 1
            lines.append(JournalLine(
                entry=entry, company=event.company,
                public_id=uuid.uuid4(), line_no=line_no,
                account=ship_acct, description=f"Shipping: {order_name}",
                debit=Decimal("0"),
                credit=_convert_amount(total_shipping, fx_rate) if is_foreign else total_shipping,
                amount_currency=-total_shipping if is_foreign else None,
                currency=currency, exchange_rate=fx_rate,
            ))

        # Fix FX rounding imbalance before saving
        if is_foreign:
            _fix_fx_rounding(lines, entry, event.company, currency, fx_rate)

        # Balance validation — save as INCOMPLETE if unbalanced
        total_debit = sum(l.debit for l in lines)
        total_credit = sum(l.credit for l in lines)

        JournalLine.objects.projection().bulk_create(lines)
        self._attach_dimensions(event.company, lines, dimension_context)

        if total_debit != total_credit:
            logger.error(
                "Unbalanced Shopify JE for order %s: debit=%s credit=%s — saved as INCOMPLETE",
                order_name, total_debit, total_credit,
            )
            entry.status = JournalEntry.Status.INCOMPLETE
            entry.posted_at = None
            entry.save(update_fields=["status", "posted_at"])

            # Notify company admins
            from accounts.models import Notification
            Notification.notify_company_admins(
                company=event.company,
                title=f"Unbalanced Shopify order: {order_name}",
                message=(
                    f"Journal entry for Shopify order {order_name} is unbalanced "
                    f"(Debit: {total_debit}, Credit: {total_credit}). "
                    f"Saved as INCOMPLETE — please review account mappings."
                ),
                level=Notification.Level.ERROR,
                link=f"/accounting/journal-entries/{entry.id}",
                source_module="shopify_connector",
            )
            return

        # Assign proper entry number (only for balanced/posted entries)
        seq = _next_company_sequence(event.company, "journal_entry_number")
        entry_number = f"JE-{event.company_id}-{seq:06d}"
        entry.entry_number = entry_number
        entry.save(update_fields=["entry_number"])

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
                "exchange_rate": str(fx_rate),
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
                entry_number=entry_number,
                date=str(entry_date),
                memo=memo,
                kind="NORMAL",
                posted_at=str(now),
                posted_by_id=0,
                posted_by_email="system@shopify",
                total_debit=str(total_debit),
                total_credit=str(total_credit),
                lines=lines_data,
                period=period,
                currency=currency,
                exchange_rate=str(fx_rate),
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

    def _handle_refund_created(self, event, data, mapping, dimension_context=None):
        """
        Reversal entry for a refund.
        DR Sales Revenue / CR Accounts Receivable
        """
        ar = mapping.get(ROLE_SHOPIFY_CLEARING)
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

        # Multi-currency: resolve exchange rate
        fx_rate, is_foreign = _resolve_exchange_rate(event.company, currency, entry_date)

        period = _resolve_period(event.company, entry_date)
        now = timezone.now()

        converted_amount = _convert_amount(amount, fx_rate) if is_foreign else amount

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
            exchange_rate=fx_rate,
            source_module="shopify_connector",
            source_document=str(refund_id),
        )

        # Assign proper entry number
        seq = _next_company_sequence(event.company, "journal_entry_number")
        entry_number = f"JE-{event.company_id}-{seq:06d}"
        entry.entry_number = entry_number
        entry.save(update_fields=["entry_number"])

        debit_line = JournalLine(
            entry=entry, company=event.company,
            public_id=uuid.uuid4(), line_no=1,
            account=revenue, description=memo,
            debit=converted_amount, credit=Decimal("0"),
            amount_currency=amount if is_foreign else None,
            currency=currency, exchange_rate=fx_rate,
        )
        credit_line = JournalLine(
            entry=entry, company=event.company,
            public_id=uuid.uuid4(), line_no=2,
            account=ar, description=memo,
            debit=Decimal("0"), credit=converted_amount,
            amount_currency=-amount if is_foreign else None,
            currency=currency, exchange_rate=fx_rate,
        )
        JournalLine.objects.projection().bulk_create([debit_line, credit_line])
        self._attach_dimensions(event.company, [debit_line, credit_line], dimension_context)

        lines_data = [
            {
                "line_public_id": str(debit_line.public_id),
                "line_no": 1,
                "account_public_id": str(revenue.public_id),
                "account_code": revenue.code,
                "description": memo,
                "debit": str(converted_amount),
                "credit": "0",
                "currency": currency,
                "exchange_rate": str(fx_rate),
            },
            {
                "line_public_id": str(credit_line.public_id),
                "line_no": 2,
                "account_public_id": str(ar.public_id),
                "account_code": ar.code,
                "description": memo,
                "debit": "0",
                "credit": str(converted_amount),
                "currency": currency,
                "exchange_rate": str(fx_rate),
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
                entry_number=entry_number,
                date=str(entry_date),
                memo=memo,
                kind="NORMAL",
                posted_at=str(now),
                posted_by_id=0,
                posted_by_email="system@shopify",
                total_debit=str(converted_amount),
                total_credit=str(converted_amount),
                lines=lines_data,
                period=period,
                currency=currency,
                exchange_rate=str(fx_rate),
            ),
            caused_by_event=event,
        )

        from shopify_connector.models import ShopifyRefund
        refund_record = ShopifyRefund.objects.filter(
            company=event.company,
            shopify_refund_id=data.get("shopify_refund_id"),
        ).first()

        if refund_record:
            refund_record.status = ShopifyRefund.Status.PROCESSED
            refund_record.journal_entry_id = entry.public_id
            refund_record.save(update_fields=["status", "journal_entry_id"])

            # Create inventory restock JE if items were restocked
            self._handle_refund_restock(
                event, refund_record, mapping, entry_date, currency,
                fx_rate, is_foreign, dimension_context,
            )

        logger.info(
            "Created refund journal entry %s for order %s",
            entry.public_id, order_number,
        )

    def _handle_refund_restock(self, event, refund_record, mapping, entry_date,
                               currency, fx_rate, is_foreign, dimension_context):
        """
        Reverse COGS for restocked items in a refund.

        For each restocked line item:
        DR Inventory    (qty × unit cost)
          CR COGS       (qty × unit cost)
        """
        raw = refund_record.raw_payload or {}
        refund_line_items = raw.get("refund_line_items", [])
        if not refund_line_items:
            return

        from sales.models import Item
        from shopify_connector.models import ShopifyProduct

        restock_lines = []
        for rli in refund_line_items:
            restock_type = rli.get("restock_type", "")
            if restock_type not in ("return", "cancel"):
                continue

            quantity = rli.get("quantity", 0)
            if quantity <= 0:
                continue

            line_item = rli.get("line_item", {})
            sku = line_item.get("sku", "")
            if not sku:
                continue

            # Find the Nxentra Item by SKU
            item = Item.objects.filter(company=event.company, code=sku).first()
            if not item or not item.cogs_account or not item.inventory_account:
                logger.debug("Skipping restock for SKU %s — no item or accounts", sku)
                continue

            unit_cost = item.default_cost or item.average_cost
            if not unit_cost or unit_cost <= 0:
                logger.debug("Skipping restock for SKU %s — no cost", sku)
                continue

            total_cost = unit_cost * Decimal(str(quantity))
            restock_lines.append({
                "sku": sku,
                "title": line_item.get("title", sku),
                "quantity": quantity,
                "unit_cost": unit_cost,
                "total_cost": total_cost,
                "inventory_account": item.inventory_account,
                "cogs_account": item.cogs_account,
            })

        if not restock_lines:
            return

        # Create restock JE
        order_number = refund_record.order.shopify_order_name if refund_record.order else ""
        memo = f"Shopify restock: Order {order_number} (Refund {refund_record.shopify_refund_id})"

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
            exchange_rate=fx_rate,
            source_module="shopify_connector",
            source_document=str(refund_record.shopify_refund_id),
        )

        seq = _next_company_sequence(event.company, "journal_entry_number")
        entry_number = f"JE-{event.company_id}-{seq:06d}"
        entry.entry_number = entry_number
        entry.save(update_fields=["entry_number"])

        lines = []
        line_no = 0
        for rl in restock_lines:
            converted = _convert_amount(rl["total_cost"], fx_rate) if is_foreign else rl["total_cost"]

            # DR Inventory (return to stock)
            line_no += 1
            lines.append(JournalLine(
                entry=entry, company=event.company,
                public_id=uuid.uuid4(), line_no=line_no,
                account=rl["inventory_account"],
                description=f"Restock: {rl['title']} x{rl['quantity']}",
                debit=converted, credit=Decimal("0"),
                currency=currency, exchange_rate=fx_rate,
            ))

            # CR COGS (reverse cost)
            line_no += 1
            lines.append(JournalLine(
                entry=entry, company=event.company,
                public_id=uuid.uuid4(), line_no=line_no,
                account=rl["cogs_account"],
                description=f"COGS reversal: {rl['title']} x{rl['quantity']}",
                debit=Decimal("0"), credit=converted,
                currency=currency, exchange_rate=fx_rate,
            ))

        JournalLine.objects.projection().bulk_create(lines)
        self._attach_dimensions(event.company, lines, dimension_context)

        total = sum(
            _convert_amount(rl["total_cost"], fx_rate) if is_foreign else rl["total_cost"]
            for rl in restock_lines
        )

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
                "exchange_rate": str(fx_rate),
            })

        emit_event_no_actor(
            company=event.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry.public_id),
            idempotency_key=f"shopify.restock.je.posted:{entry.public_id}",
            metadata={"source_projection": PROJECTION_NAME},
            data=JournalEntryPostedData(
                entry_public_id=str(entry.public_id),
                entry_number=entry_number,
                date=str(entry_date),
                memo=memo,
                kind="NORMAL",
                posted_at=str(now),
                posted_by_id=0,
                posted_by_email="system@shopify",
                total_debit=str(total),
                total_credit=str(total),
                lines=lines_data,
                period=period,
                currency=currency,
                exchange_rate=str(fx_rate),
            ),
            caused_by_event=event,
        )

        logger.info(
            "Created restock journal entry %s for refund on order %s (%d items)",
            entry.public_id, order_number, len(restock_lines),
        )

    def _handle_payout_settled(self, event, data, mapping, dimension_context=None):
        """
        Settlement entry when Shopify sends a payout to bank.

        DR Cash/Bank             net_amount
        DR Processing Fees       fees (if > 0)
        CR Shopify Clearing      gross_amount
        """
        clearing = mapping.get(ROLE_SHOPIFY_CLEARING)
        bank = mapping.get(ROLE_CASH_BANK)
        if not clearing or not bank:
            logger.warning(
                "Shopify account mapping missing SHOPIFY_CLEARING or CASH_BANK "
                "for company %s — skipping payout %s",
                event.company, data.get("shopify_payout_id"),
            )
            return

        fees_account = mapping.get(ROLE_PROCESSING_FEES)

        gross_amount = Decimal(str(data.get("gross_amount", "0")))
        fees = Decimal(str(data.get("fees", "0")))
        net_amount = Decimal(str(data.get("net_amount", "0")))
        payout_id = data.get("shopify_payout_id", "")
        entry_date = _parse_date(data.get("payout_date") or data.get("transaction_date")) or event.created_at.date()
        currency = data.get("currency") or getattr(event.company, "default_currency", "USD")
        memo = f"Shopify payout: {payout_id}"

        if gross_amount == 0:
            logger.warning(
                "Skipping Shopify payout %s — zero gross amount",
                payout_id,
            )
            return

        is_negative_payout = gross_amount < 0

        # Idempotency
        if JournalEntry.objects.filter(
            company=event.company, memo=memo,
            status=JournalEntry.Status.POSTED,
        ).exists():
            logger.info("Journal entry already exists for '%s' — skipping", memo)
            return

        # Multi-currency: resolve exchange rate
        fx_rate, is_foreign = _resolve_exchange_rate(event.company, currency, entry_date)

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
            exchange_rate=fx_rate,
            source_module="shopify_connector",
            source_document=str(payout_id),
        )

        lines = []
        line_no = 0
        abs_gross = abs(gross_amount)
        abs_net = abs(net_amount)

        def _make_line(account, description, debit_amt, credit_amt):
            """Helper to create a JournalLine with FX conversion."""
            return JournalLine(
                entry=entry, company=event.company,
                public_id=uuid.uuid4(), line_no=0,  # set below
                account=account, description=description,
                debit=_convert_amount(debit_amt, fx_rate) if is_foreign else debit_amt,
                credit=_convert_amount(credit_amt, fx_rate) if is_foreign else credit_amt,
                amount_currency=(debit_amt if debit_amt > 0 else -credit_amt) if is_foreign else None,
                currency=currency, exchange_rate=fx_rate,
            )

        if is_negative_payout:
            line_no += 1
            ln = _make_line(clearing, f"Negative payout reversal: {payout_id}", abs_gross, Decimal("0"))
            ln.line_no = line_no
            lines.append(ln)

            if fees > 0 and fees_account:
                line_no += 1
                ln = _make_line(fees_account, f"Processing fees: Payout {payout_id}", fees, Decimal("0"))
                ln.line_no = line_no
                lines.append(ln)
            elif fees > 0 and not fees_account:
                logger.warning(
                    "No PROCESSING_FEES account mapped — fees of %s absorbed into clearing for payout %s",
                    fees, payout_id,
                )
                converted_abs_net = _convert_amount(abs_net, fx_rate) if is_foreign else abs_net
                lines[0].debit = converted_abs_net
                if is_foreign:
                    lines[0].amount_currency = abs_net

            line_no += 1
            ln = _make_line(bank, f"Negative payout: {payout_id}", Decimal("0"), abs_net)
            ln.line_no = line_no
            lines.append(ln)
        else:
            line_no += 1
            ln = _make_line(bank, memo, net_amount, Decimal("0"))
            ln.line_no = line_no
            lines.append(ln)

            if fees > 0 and fees_account:
                line_no += 1
                ln = _make_line(fees_account, f"Processing fees: Payout {payout_id}", fees, Decimal("0"))
                ln.line_no = line_no
                lines.append(ln)
            elif fees > 0 and not fees_account:
                logger.warning(
                    "No PROCESSING_FEES account mapped — fees of %s included in bank deposit for payout %s",
                    fees, payout_id,
                )
                converted_gross = _convert_amount(gross_amount, fx_rate) if is_foreign else gross_amount
                lines[0].debit = converted_gross
                if is_foreign:
                    lines[0].amount_currency = gross_amount

            line_no += 1
            ln = _make_line(clearing, memo, Decimal("0"), gross_amount)
            ln.line_no = line_no
            lines.append(ln)

        # Fix FX rounding imbalance before saving
        if is_foreign:
            _fix_fx_rounding(lines, entry, event.company, currency, fx_rate)

        # Balance validation
        total_debit = sum(l.debit for l in lines)
        total_credit = sum(l.credit for l in lines)

        JournalLine.objects.projection().bulk_create(lines)
        self._attach_dimensions(event.company, lines, dimension_context)

        if total_debit != total_credit:
            logger.error(
                "Unbalanced Shopify payout JE %s: debit=%s credit=%s — saved as INCOMPLETE",
                payout_id, total_debit, total_credit,
            )
            entry.status = JournalEntry.Status.INCOMPLETE
            entry.posted_at = None
            entry.save(update_fields=["status", "posted_at"])

            from accounts.models import Notification
            Notification.notify_company_admins(
                company=event.company,
                title=f"Unbalanced Shopify payout: {payout_id}",
                message=(
                    f"Journal entry for Shopify payout {payout_id} is unbalanced "
                    f"(Debit: {total_debit}, Credit: {total_credit}). "
                    f"Saved as INCOMPLETE — please review account mappings."
                ),
                level=Notification.Level.ERROR,
                link=f"/accounting/journal-entries/{entry.id}",
                source_module="shopify_connector",
            )
            return

        # Assign entry number
        seq = _next_company_sequence(event.company, "journal_entry_number")
        entry_number = f"JE-{event.company_id}-{seq:06d}"
        entry.entry_number = entry_number
        entry.save(update_fields=["entry_number"])

        # Emit JOURNAL_ENTRY_POSTED
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
                "exchange_rate": str(fx_rate),
            })

        emit_event_no_actor(
            company=event.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry.public_id),
            idempotency_key=f"shopify.payout.je.posted:{entry.public_id}",
            metadata={"source_projection": PROJECTION_NAME},
            data=JournalEntryPostedData(
                entry_public_id=str(entry.public_id),
                entry_number=entry_number,
                date=str(entry_date),
                memo=memo,
                kind="NORMAL",
                posted_at=str(now),
                posted_by_id=0,
                posted_by_email="system@shopify",
                total_debit=str(total_debit),
                total_credit=str(total_credit),
                lines=lines_data,
                period=period,
                currency=currency,
                exchange_rate=str(fx_rate),
            ),
            caused_by_event=event,
        )

        # Update local payout record
        from shopify_connector.models import ShopifyPayout
        ShopifyPayout.objects.filter(
            company=event.company,
            shopify_payout_id=data.get("shopify_payout_id"),
        ).update(
            status=ShopifyPayout.Status.PROCESSED,
            journal_entry_id=entry.public_id,
        )

        logger.info(
            "Created payout journal entry %s for Shopify payout %s (event %s)",
            entry.public_id, payout_id, event.id,
        )

    def _handle_order_fulfilled(self, event, data, mapping, dimension_context=None):
        """
        COGS entry when a Shopify order is fulfilled.

        For each matched inventory item:
        DR Cost of Goods Sold    (qty × avg_cost)
        CR Inventory             (qty × avg_cost)

        Uses per-item COGS and inventory accounts from the Item model,
        not module-level account mapping.
        Also emits INVENTORY_STOCK_ISSUED for the inventory balance projection.
        """
        cogs_lines = data.get("cogs_lines", [])
        if not cogs_lines:
            logger.info(
                "Fulfillment %s has no matched COGS lines — skipping",
                data.get("shopify_fulfillment_id"),
            )
            return

        total_cogs = Decimal(str(data.get("total_cogs", "0")))
        fulfillment_id = data.get("shopify_fulfillment_id", "")
        order_name = data.get("order_name", "")
        entry_date = _parse_date(
            data.get("fulfillment_date") or data.get("transaction_date")
        ) or event.created_at.date()
        currency = data.get("currency") or getattr(event.company, "default_currency", "USD")
        memo = f"Shopify COGS: {order_name} (Fulfillment {fulfillment_id})"

        if total_cogs <= 0:
            logger.warning(
                "Skipping COGS for fulfillment %s — zero total COGS", fulfillment_id,
            )
            return

        # Idempotency
        if JournalEntry.objects.filter(
            company=event.company, memo=memo,
            status=JournalEntry.Status.POSTED,
        ).exists():
            logger.info("COGS journal entry already exists for '%s' — skipping", memo)
            return

        # Resolve accounts for each line
        from accounting.models import Account

        # Multi-currency: resolve exchange rate
        fx_rate, is_foreign = _resolve_exchange_rate(event.company, currency, entry_date)

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
            exchange_rate=fx_rate,
            source_module="shopify_connector",
            source_document=str(fulfillment_id),
        )

        lines = []
        line_no = 0
        stock_entries = []  # For INVENTORY_STOCK_ISSUED event

        for cl in cogs_lines:
            cogs_value = Decimal(str(cl.get("cogs_value", "0")))
            if cogs_value <= 0:
                continue

            cogs_account_id = cl.get("cogs_account_id")
            inventory_account_id = cl.get("inventory_account_id")

            try:
                cogs_account = Account.objects.get(
                    id=cogs_account_id, company=event.company,
                )
                inventory_account = Account.objects.get(
                    id=inventory_account_id, company=event.company,
                )
            except Account.DoesNotExist:
                logger.warning(
                    "COGS or Inventory account not found for item %s — skipping line",
                    cl.get("item_code"),
                )
                continue

            item_code = cl.get("item_code", "")
            qty = cl.get("qty", "0")
            converted_cogs = _convert_amount(cogs_value, fx_rate) if is_foreign else cogs_value

            # DR Cost of Goods Sold
            line_no += 1
            lines.append(JournalLine(
                entry=entry, company=event.company,
                public_id=uuid.uuid4(), line_no=line_no,
                account=cogs_account,
                description=f"COGS: {item_code} × {qty}",
                debit=converted_cogs, credit=Decimal("0"),
                amount_currency=cogs_value if is_foreign else None,
                currency=currency, exchange_rate=fx_rate,
            ))

            # CR Inventory
            line_no += 1
            lines.append(JournalLine(
                entry=entry, company=event.company,
                public_id=uuid.uuid4(), line_no=line_no,
                account=inventory_account,
                description=f"Inventory issued: {item_code} × {qty}",
                debit=Decimal("0"), credit=converted_cogs,
                amount_currency=-cogs_value if is_foreign else None,
                currency=currency, exchange_rate=fx_rate,
            ))

            # Build stock entry for INVENTORY_STOCK_ISSUED event
            if cl.get("item_public_id") and cl.get("warehouse_public_id"):
                stock_entries.append({
                    "item_public_id": cl["item_public_id"],
                    "warehouse_public_id": cl["warehouse_public_id"],
                    "qty_delta": str(-Decimal(str(qty))),
                    "unit_cost": str(cl.get("unit_cost", "0")),
                    "value_delta": str(-cogs_value),
                    "costing_method_snapshot": "WEIGHTED_AVERAGE",
                })

        if not lines:
            # No valid lines — clean up
            entry.delete()
            return

        # Fix FX rounding imbalance before saving
        if is_foreign:
            _fix_fx_rounding(lines, entry, event.company, currency, fx_rate)

        # Balance validation
        total_debit = sum(l.debit for l in lines)
        total_credit = sum(l.credit for l in lines)

        JournalLine.objects.projection().bulk_create(lines)
        self._attach_dimensions(event.company, lines, dimension_context)

        if total_debit != total_credit:
            logger.error(
                "Unbalanced COGS JE for fulfillment %s: debit=%s credit=%s — saved as INCOMPLETE",
                fulfillment_id, total_debit, total_credit,
            )
            entry.status = JournalEntry.Status.INCOMPLETE
            entry.posted_at = None
            entry.save(update_fields=["status", "posted_at"])

            from accounts.models import Notification
            Notification.notify_company_admins(
                company=event.company,
                title=f"Unbalanced COGS entry: {order_name}",
                message=(
                    f"COGS journal entry for fulfillment {fulfillment_id} is unbalanced "
                    f"(Debit: {total_debit}, Credit: {total_credit}). "
                    f"Saved as INCOMPLETE — please review item account configuration."
                ),
                level=Notification.Level.ERROR,
                link=f"/accounting/journal-entries/{entry.id}",
                source_module="shopify_connector",
            )
            return

        # Assign entry number
        seq = _next_company_sequence(event.company, "journal_entry_number")
        entry_number = f"JE-{event.company_id}-{seq:06d}"
        entry.entry_number = entry_number
        entry.save(update_fields=["entry_number"])

        # Emit JOURNAL_ENTRY_POSTED
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
                "exchange_rate": str(fx_rate),
            })

        emit_event_no_actor(
            company=event.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry.public_id),
            idempotency_key=f"shopify.cogs.je.posted:{entry.public_id}",
            metadata={"source_projection": PROJECTION_NAME},
            data=JournalEntryPostedData(
                entry_public_id=str(entry.public_id),
                entry_number=entry_number,
                date=str(entry_date),
                memo=memo,
                kind="NORMAL",
                posted_at=str(now),
                posted_by_id=0,
                posted_by_email="system@shopify",
                total_debit=str(total_debit),
                total_credit=str(total_credit),
                lines=lines_data,
                period=period,
                currency=currency,
                exchange_rate=str(fx_rate),
            ),
            caused_by_event=event,
        )

        # Emit INVENTORY_STOCK_ISSUED for inventory balance projection
        if stock_entries:
            emit_event_no_actor(
                company=event.company,
                event_type=EventTypes.INVENTORY_STOCK_ISSUED,
                aggregate_type="StockLedger",
                aggregate_id=str(event.company.public_id),
                idempotency_key=f"shopify.fulfillment.stock.issued:{fulfillment_id}",
                metadata={"source_projection": PROJECTION_NAME},
                data={
                    "source_type": "SHOPIFY_FULFILLMENT",
                    "source_id": str(fulfillment_id),
                    "company_public_id": str(event.company.public_id),
                    "entries": stock_entries,
                    "total_cogs": str(total_cogs),
                    "journal_entry_public_id": str(entry.public_id),
                },
                caused_by_event=event,
            )

        # Update local fulfillment record
        from shopify_connector.models import ShopifyFulfillment
        ShopifyFulfillment.objects.filter(
            company=event.company,
            shopify_fulfillment_id=data.get("shopify_fulfillment_id"),
        ).update(
            status=ShopifyFulfillment.Status.PROCESSED,
            journal_entry_id=entry.public_id,
        )

        logger.info(
            "Created COGS journal entry %s for fulfillment %s (%s lines, COGS %s %s)",
            entry.public_id, fulfillment_id, len(cogs_lines), currency, total_cogs,
        )

    def _handle_dispute_created(self, event, data, mapping, dimension_context=None):
        """
        Create a chargeback journal entry when a Shopify dispute is received.

        Journal entry:
        DR Chargeback Expense   (dispute amount)
        DR Processing Fees      (chargeback fee, if any)
        CR Shopify Clearing     (total = amount + fee)

        The clearing account is credited because the disputed funds are
        being pulled back by the payment processor.
        """
        clearing = mapping.get(ROLE_SHOPIFY_CLEARING)
        chargeback_account = mapping.get(ROLE_CHARGEBACK_EXPENSE)
        fees_account = mapping.get(ROLE_PROCESSING_FEES)

        if not clearing or not chargeback_account:
            logger.warning(
                "Shopify account mapping missing SHOPIFY_CLEARING or CHARGEBACK_EXPENSE "
                "for company %s — skipping dispute %s",
                event.company, data.get("shopify_dispute_id"),
            )
            return

        dispute_amount = Decimal(str(data.get("dispute_amount", "0")))
        chargeback_fee = Decimal(str(data.get("chargeback_fee", "0")))
        dispute_id = data.get("shopify_dispute_id", "")
        order_name = data.get("order_name", "")
        currency = data.get("currency") or getattr(event.company, "default_currency", "USD")
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()
        memo = f"Shopify chargeback: Dispute {dispute_id} ({order_name})"

        if dispute_amount <= 0:
            logger.warning(
                "Skipping dispute %s — non-positive amount %s",
                dispute_id, dispute_amount,
            )
            return

        # Idempotency
        if JournalEntry.objects.filter(
            company=event.company, memo=memo,
            status=JournalEntry.Status.POSTED,
        ).exists():
            logger.info("Journal entry already exists for '%s' — skipping", memo)
            return

        # Multi-currency: resolve exchange rate
        fx_rate, is_foreign = _resolve_exchange_rate(event.company, currency, entry_date)

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
            exchange_rate=fx_rate,
            source_module="shopify_connector",
            source_document=str(dispute_id),
        )

        lines = []
        line_no = 0

        # DR Chargeback Expense — disputed amount
        line_no += 1
        converted_dispute = _convert_amount(dispute_amount, fx_rate) if is_foreign else dispute_amount
        lines.append(JournalLine(
            entry=entry, company=event.company,
            public_id=uuid.uuid4(), line_no=line_no,
            account=chargeback_account,
            description=f"Chargeback: {order_name} (Dispute {dispute_id})",
            debit=converted_dispute, credit=Decimal("0"),
            amount_currency=dispute_amount if is_foreign else None,
            currency=currency, exchange_rate=fx_rate,
        ))

        # DR Processing Fees — chargeback fee
        if chargeback_fee > 0 and fees_account:
            line_no += 1
            converted_fee = _convert_amount(chargeback_fee, fx_rate) if is_foreign else chargeback_fee
            lines.append(JournalLine(
                entry=entry, company=event.company,
                public_id=uuid.uuid4(), line_no=line_no,
                account=fees_account,
                description=f"Chargeback fee: Dispute {dispute_id}",
                debit=converted_fee, credit=Decimal("0"),
                amount_currency=chargeback_fee if is_foreign else None,
                currency=currency, exchange_rate=fx_rate,
            ))

        # CR Shopify Clearing — total pulled back
        total_credit_foreign = dispute_amount + chargeback_fee
        total_credit = _convert_amount(total_credit_foreign, fx_rate) if is_foreign else total_credit_foreign
        line_no += 1
        lines.append(JournalLine(
            entry=entry, company=event.company,
            public_id=uuid.uuid4(), line_no=line_no,
            account=clearing,
            description=f"Chargeback clearing: Dispute {dispute_id}",
            debit=Decimal("0"), credit=total_credit,
            amount_currency=-total_credit_foreign if is_foreign else None,
            currency=currency, exchange_rate=fx_rate,
        ))

        # Fix FX rounding imbalance before saving
        if is_foreign:
            _fix_fx_rounding(lines, entry, event.company, currency, fx_rate)

        # Balance validation
        total_debit = sum(l.debit for l in lines)
        total_credit_check = sum(l.credit for l in lines)

        JournalLine.objects.projection().bulk_create(lines)
        self._attach_dimensions(event.company, lines, dimension_context)

        if total_debit != total_credit_check:
            logger.error(
                "Unbalanced chargeback JE %s: debit=%s credit=%s",
                dispute_id, total_debit, total_credit_check,
            )
            entry.status = JournalEntry.Status.INCOMPLETE
            entry.posted_at = None
            entry.save(update_fields=["status", "posted_at"])

            from accounts.models import Notification
            Notification.notify_company_admins(
                company=event.company,
                title=f"Unbalanced chargeback entry: Dispute {dispute_id}",
                message=(
                    f"Journal entry for chargeback dispute {dispute_id} is unbalanced "
                    f"(Debit: {total_debit}, Credit: {total_credit_check}). "
                    f"Saved as INCOMPLETE — please review account mappings."
                ),
                level=Notification.Level.ERROR,
                link=f"/accounting/journal-entries/{entry.id}",
                source_module="shopify_connector",
            )
            return

        # Assign entry number
        seq = _next_company_sequence(event.company, "journal_entry_number")
        entry_number = f"JE-{event.company_id}-{seq:06d}"
        entry.entry_number = entry_number
        entry.save(update_fields=["entry_number"])

        # Emit JOURNAL_ENTRY_POSTED
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
                "exchange_rate": str(fx_rate),
            })

        emit_event_no_actor(
            company=event.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry.public_id),
            idempotency_key=f"shopify.dispute.je.posted:{entry.public_id}",
            metadata={"source_projection": PROJECTION_NAME},
            data=JournalEntryPostedData(
                entry_public_id=str(entry.public_id),
                entry_number=entry_number,
                date=str(entry_date),
                memo=memo,
                kind="NORMAL",
                posted_at=str(now),
                posted_by_id=0,
                posted_by_email="system@shopify",
                total_debit=str(total_debit),
                total_credit=str(total_credit_check),
                lines=lines_data,
                period=period,
                currency=currency,
                exchange_rate=str(fx_rate),
            ),
            caused_by_event=event,
        )

        # Update local dispute record
        from shopify_connector.models import ShopifyDispute
        ShopifyDispute.objects.filter(
            company=event.company,
            shopify_dispute_id=data.get("shopify_dispute_id"),
        ).update(
            status=ShopifyDispute.Status.PROCESSED,
            journal_entry_id=entry.public_id,
        )

        logger.info(
            "Created chargeback journal entry %s for dispute %s (%s %s)",
            entry.public_id, dispute_id, currency, dispute_amount,
        )

    def _handle_dispute_won(self, event, data, mapping, dimension_context=None):
        """
        Reverse the chargeback journal entry when a dispute is won.

        Journal entry (mirrors the original chargeback entry):
        DR Shopify Clearing     (amount + fee)
        CR Chargeback Expense   (amount)
        CR Processing Fees      (chargeback fee)
        """
        clearing = mapping.get(ROLE_SHOPIFY_CLEARING)
        chargeback_account = mapping.get(ROLE_CHARGEBACK_EXPENSE)
        fees_account = mapping.get(ROLE_PROCESSING_FEES)

        if not clearing or not chargeback_account:
            logger.warning(
                "Shopify account mapping missing for dispute won reversal, "
                "company %s — skipping dispute %s",
                event.company, data.get("shopify_dispute_id"),
            )
            return

        dispute_amount = Decimal(str(data.get("dispute_amount", "0")))
        chargeback_fee = Decimal(str(data.get("chargeback_fee", "0")))
        dispute_id = data.get("shopify_dispute_id", "")
        order_name = data.get("order_name", "")
        currency = data.get("currency") or getattr(event.company, "default_currency", "USD")
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()
        memo = f"Shopify chargeback reversal (won): Dispute {dispute_id} ({order_name})"

        if dispute_amount <= 0:
            return

        # Idempotency
        if JournalEntry.objects.filter(
            company=event.company, memo=memo,
            status=JournalEntry.Status.POSTED,
        ).exists():
            logger.info("Reversal JE already exists for '%s' — skipping", memo)
            return

        fx_rate, is_foreign = _resolve_exchange_rate(event.company, currency, entry_date)
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
            exchange_rate=fx_rate,
            source_module="shopify_connector",
            source_document=str(dispute_id),
        )

        lines = []
        line_no = 0

        # DR Shopify Clearing — funds returned
        total_debit_foreign = dispute_amount + chargeback_fee
        total_debit_converted = _convert_amount(total_debit_foreign, fx_rate) if is_foreign else total_debit_foreign
        line_no += 1
        lines.append(JournalLine(
            entry=entry, company=event.company,
            public_id=uuid.uuid4(), line_no=line_no,
            account=clearing,
            description=f"Chargeback reversal (won): Dispute {dispute_id}",
            debit=total_debit_converted, credit=Decimal("0"),
            amount_currency=total_debit_foreign if is_foreign else None,
            currency=currency, exchange_rate=fx_rate,
        ))

        # CR Chargeback Expense — recover disputed amount
        converted_dispute = _convert_amount(dispute_amount, fx_rate) if is_foreign else dispute_amount
        line_no += 1
        lines.append(JournalLine(
            entry=entry, company=event.company,
            public_id=uuid.uuid4(), line_no=line_no,
            account=chargeback_account,
            description=f"Chargeback recovered (won): {order_name} (Dispute {dispute_id})",
            debit=Decimal("0"), credit=converted_dispute,
            amount_currency=-dispute_amount if is_foreign else None,
            currency=currency, exchange_rate=fx_rate,
        ))

        # CR Processing Fees — recover chargeback fee
        if chargeback_fee > 0 and fees_account:
            converted_fee = _convert_amount(chargeback_fee, fx_rate) if is_foreign else chargeback_fee
            line_no += 1
            lines.append(JournalLine(
                entry=entry, company=event.company,
                public_id=uuid.uuid4(), line_no=line_no,
                account=fees_account,
                description=f"Chargeback fee recovered: Dispute {dispute_id}",
                debit=Decimal("0"), credit=converted_fee,
                amount_currency=-chargeback_fee if is_foreign else None,
                currency=currency, exchange_rate=fx_rate,
            ))

        # Fix FX rounding
        if is_foreign:
            _fix_fx_rounding(lines, entry, event.company, currency, fx_rate)

        total_debit_check = sum(l.debit for l in lines)
        total_credit_check = sum(l.credit for l in lines)

        JournalLine.objects.projection().bulk_create(lines)
        self._attach_dimensions(event.company, lines, dimension_context)

        if total_debit_check != total_credit_check:
            logger.error(
                "Unbalanced dispute-won reversal JE %s: debit=%s credit=%s",
                dispute_id, total_debit_check, total_credit_check,
            )
            entry.status = JournalEntry.Status.INCOMPLETE
            entry.posted_at = None
            entry.save(update_fields=["status", "posted_at"])
            return

        seq = _next_company_sequence(event.company, "journal_entry_number")
        entry_number = f"JE-{event.company_id}-{seq:06d}"
        entry.entry_number = entry_number
        entry.save(update_fields=["entry_number"])

        # Emit JOURNAL_ENTRY_POSTED
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
                "exchange_rate": str(fx_rate),
            })

        emit_event_no_actor(
            company=event.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry.public_id),
            idempotency_key=f"shopify.dispute.won.je.posted:{entry.public_id}",
            metadata={"source_projection": PROJECTION_NAME},
            data=JournalEntryPostedData(
                entry_public_id=str(entry.public_id),
                entry_number=entry_number,
                date=str(entry_date),
                memo=memo,
                kind="NORMAL",
                posted_at=str(now),
                posted_by_id=0,
                posted_by_email="system@shopify",
                total_debit=str(total_debit_check),
                total_credit=str(total_credit_check),
                lines=lines_data,
                period=period,
                currency=currency,
                exchange_rate=str(fx_rate),
            ),
            caused_by_event=event,
        )

        logger.info(
            "Created dispute-won reversal JE %s for dispute %s (%s %s)",
            entry.public_id, dispute_id, currency, dispute_amount,
        )

    def _clear_projected_data(self, company) -> None:
        """Clear Shopify-generated journal entries for rebuild."""
        JournalEntry.objects.filter(
            company=company,
            source_module="shopify_connector",
        ).delete()
