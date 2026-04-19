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
from projections.write_barrier import command_writes_allowed, projection_writes_allowed

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
            company=company,
            public_id=store_public_id,
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
            is_header=False,
            status=Account.Status.ACTIVE,
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
            entry=entry,
            company=company,
            public_id=uuid.uuid4(),
            line_no=next_line_no,
            account=rounding_account,
            description="FX rounding adjustment",
            debit=Decimal("0"),
            credit=diff,
            currency=currency,
            exchange_rate=fx_rate,
        )
    else:
        # Credits exceed debits — add debit rounding line
        rounding_line = JournalLine(
            entry=entry,
            company=company,
            public_id=uuid.uuid4(),
            line_no=next_line_no,
            account=rounding_account,
            description="FX rounding adjustment",
            debit=abs(diff),
            credit=Decimal("0"),
            currency=currency,
            exchange_rate=fx_rate,
        )

    lines.append(rounding_line)
    logger.info(
        "FX rounding line added: %s %s to account %s",
        "CR" if diff > 0 else "DR",
        abs(diff),
        rounding_account.code,
    )


def _ensure_dimension_and_value(company, dim_code, dim_name, dim_name_ar, val_code, val_name, applies_to=None):
    """
    Ensure an AnalysisDimension and AnalysisDimensionValue exist.
    Creates them if missing. Idempotent.

    applies_to: list of account types this dimension is relevant for during
    manual JE entry (e.g. ["REVENUE", "EXPENSE"]). Empty = all types.
    Does not restrict auto-tagging from projections.
    """
    from accounting.models import AnalysisDimension, AnalysisDimensionValue

    dim, _ = AnalysisDimension.objects.projection().get_or_create(
        company=company,
        code=dim_code,
        defaults={
            "name": dim_name,
            "name_ar": dim_name_ar,
            "dimension_kind": AnalysisDimension.DimensionKind.ANALYTIC,
            "is_required_on_posting": False,
            "is_active": True,
            "applies_to_account_types": applies_to or [],
        },
    )

    AnalysisDimensionValue.objects.projection().get_or_create(
        dimension=dim,
        code=val_code,
        company=company,
        defaults={
            "name": val_name,
            "is_active": True,
        },
    )


# Account type sets for dimension scoping
_REVENUE_COGS = ["REVENUE", "EXPENSE"]
_REVENUE_COGS_INV = ["REVENUE", "EXPENSE", "ASSET"]
_REVENUE_ONLY = ["REVENUE"]
_FEES_CLEARING = ["EXPENSE", "ASSET"]


class ShopifyAccountingHandler(BaseProjection):
    """
    Shopify financial event handler (process manager / saga).

    Despite inheriting BaseProjection for infrastructure integration (event
    subscription, bookmark tracking, idempotent processing), this class is
    NOT a pure read-model projector. It creates journal entries and emits
    JOURNAL_ENTRY_POSTED events — making it an event-to-JE process manager.

    All JE creation validates via validate_system_journal_postable() before
    posting. If validation fails (closed period, inactive account), entries
    are created as INCOMPLETE with admin notification.

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
                "No ModuleAccountMapping for shopify_connector module, company %s — skipping %s",
                company,
                event.event_type,
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
                    event.event_type,
                    event.id,
                    exc,
                )
                from accounts.models import Notification

                Notification.notify_company_admins(
                    company=company,
                    title="Missing exchange rate — Shopify entry skipped",
                    message=str(exc),
                    level=Notification.Level.ERROR,
                    source_module="shopify_connector",
                )
                raise

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
                company=company,
                shopify_order_id=shopify_order_id,
            ).first()
            if order_record and order_record.raw_payload:
                raw = order_record.raw_payload

        # 1. CHANNEL — always Shopify (relevant on Revenue + COGS)
        _ensure_dimension_and_value(
            company, "CHANNEL", "Sales Channel", "قناة البيع", "SHOPIFY", "Shopify", _REVENUE_COGS
        )
        context["CHANNEL"] = "SHOPIFY"

        # 2. PRODUCT — SKU from first line item (Revenue + COGS + Inventory)
        line_items = data.get("line_items", []) or raw.get("line_items", [])
        if line_items:
            first_item = line_items[0]
            sku = first_item.get("sku", "")
            title = first_item.get("title", "")
            if sku:
                _ensure_dimension_and_value(
                    company, "PRODUCT", "Product", "المنتج", sku, title or sku, _REVENUE_COGS_INV
                )
                context["PRODUCT"] = sku

            # 3. CATEGORY — product_type (Revenue + COGS)
            product_type = first_item.get("product_type", "")
            if product_type:
                val_code = product_type.upper().replace(" ", "_")[:20]
                _ensure_dimension_and_value(
                    company, "CATEGORY", "Product Category", "فئة المنتج", val_code, product_type, _REVENUE_COGS
                )
                context["CATEGORY"] = val_code

            # 4. VENDOR — brand/supplier (Revenue + COGS + Inventory)
            vendor = first_item.get("vendor", "")
            if vendor:
                val_code = vendor.upper().replace(" ", "_")[:20]
                _ensure_dimension_and_value(
                    company, "VENDOR", "Vendor / Brand", "المورد / العلامة", val_code, vendor, _REVENUE_COGS_INV
                )
                context["VENDOR"] = val_code

        # 5. REGION — shipping country (Revenue + COGS)
        shipping = raw.get("shipping_address") or {}
        country = shipping.get("country", "") or shipping.get("country_code", "")
        if country:
            val_code = country.upper()[:20]
            country_name = shipping.get("country", country)
            _ensure_dimension_and_value(
                company, "REGION", "Region / Country", "المنطقة / الدولة", val_code, country_name, _REVENUE_COGS
            )
            context["REGION"] = val_code

        # 6. CITY — shipping city (Revenue + COGS)
        city = shipping.get("city", "")
        if city:
            val_code = city.upper().replace(" ", "_")[:20]
            _ensure_dimension_and_value(company, "CITY", "City", "المدينة", val_code, city, _REVENUE_COGS)
            context["CITY"] = val_code

        # 7. SOURCE — order source (Revenue only)
        source_name = raw.get("source_name", "")
        if source_name:
            val_code = source_name.upper()[:20]
            _ensure_dimension_and_value(
                company, "SOURCE", "Order Source", "مصدر الطلب", val_code, source_name, _REVENUE_ONLY
            )
            context["SOURCE"] = val_code

        # 8. PAY_METHOD — payment gateway (Fees + Clearing)
        gateway = data.get("gateway", "") or raw.get("gateway", "")
        if gateway:
            val_code = gateway.upper().replace(" ", "_")[:20]
            _ensure_dimension_and_value(
                company, "PAY_METHOD", "Payment Method", "طريقة الدفع", val_code, gateway, _FEES_CLEARING
            )
            context["PAY_METHOD"] = val_code

        # 9. PROMOTION — discount codes (Revenue only)
        discount_codes = raw.get("discount_codes", [])
        if discount_codes:
            code = discount_codes[0].get("code", "")
            if code:
                val_code = code.upper()[:20]
                _ensure_dimension_and_value(
                    company, "PROMOTION", "Promotion / Discount", "العرض / الخصم", val_code, code, _REVENUE_ONLY
                )
                context["PROMOTION"] = val_code

        # 10. CAMPAIGN — order tags (Revenue + COGS)
        tags_str = raw.get("tags", "")
        if tags_str:
            first_tag = tags_str.split(",")[0].strip()
            if first_tag:
                val_code = first_tag.upper().replace(" ", "_")[:20]
                _ensure_dimension_and_value(
                    company, "CAMPAIGN", "Campaign / Tag", "الحملة", val_code, first_tag, _REVENUE_COGS
                )
                context["CAMPAIGN"] = val_code

        # 11. REFERRER — referring site (Revenue only)
        referring_site = raw.get("referring_site", "")
        if referring_site:
            from urllib.parse import urlparse

            try:
                domain = urlparse(referring_site).netloc or referring_site
            except Exception:
                domain = referring_site
            val_code = domain.upper().replace(".", "_").replace("WWW_", "")[:20]
            _ensure_dimension_and_value(company, "REFERRER", "Referrer", "المُحيل", val_code, domain, _REVENUE_ONLY)
            context["REFERRER"] = val_code

        # 12. CUST_SEGMENT — customer tags (Revenue + COGS)
        customer = raw.get("customer", {})
        customer_tags = customer.get("tags", "")
        if customer_tags:
            first_tag = customer_tags.split(",")[0].strip()
            if first_tag:
                val_code = first_tag.upper().replace(" ", "_")[:20]
                _ensure_dimension_and_value(
                    company, "CUST_SEGMENT", "Customer Segment", "شريحة العملاء", val_code, first_tag, _REVENUE_COGS
                )
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

    def _validate_entry(self, company, entry_date, lines):
        """
        Run shared validation before creating a posted JE.

        Returns (force_incomplete: bool, errors: list[str]).
        If force_incomplete is True, the caller should create the entry
        as INCOMPLETE and notify admins instead of posting.
        """
        from accounting.validation import validate_system_journal_postable

        validation_lines = [{"account": l.account, "debit": l.debit, "credit": l.credit} for l in lines]
        result = validate_system_journal_postable(
            company=company,
            entry_date=entry_date,
            lines=validation_lines,
            source_module="shopify_connector",
            allow_missing_counterparty=True,
            on_closed_period="incomplete",
        )
        force_incomplete = not result.ok or bool(result.errors)
        return force_incomplete, result.errors

    def _mark_incomplete(self, entry, errors, memo):
        """Mark entry as INCOMPLETE and notify admins with actionable guidance."""
        entry.status = JournalEntry.Status.INCOMPLETE
        entry.posted_at = None
        entry.save(update_fields=["status", "posted_at"])

        # Build actionable resolution message
        error_str = "; ".join(errors)
        resolution = self._resolve_action(errors)

        from accounts.models import Notification

        Notification.notify_company_admins(
            company=entry.company,
            title=f"Shopify entry needs review: {memo}",
            message=(f"Journal entry '{memo}' was saved as INCOMPLETE. Reason: {error_str}. Action: {resolution}"),
            level=Notification.Level.ERROR,
            link=f"/accounting/journal-entries/{entry.id}",
            source_module="shopify_connector",
        )

    @staticmethod
    def _resolve_action(errors):
        """Map validation errors to actionable resolution steps."""
        for error in errors:
            e = error.lower()
            if "period" in e and "closed" in e:
                return "Go to Settings > Periods and reopen the period, then post this entry manually from Journal Entries."
            if "fiscal year" in e and "closed" in e:
                return (
                    "The fiscal year is closed. Go to Settings > Periods to reopen the year if adjustments are needed."
                )
            if "inactive" in e:
                return "One or more mapped accounts are inactive. Go to Settings > Shopify > Account Mapping and update to active accounts."
            if "header" in e:
                return "A mapped account is a header (non-postable). Go to Settings > Shopify > Account Mapping and select a postable child account."
            if "unbalanced" in e:
                return "The entry is unbalanced — this usually means a missing account mapping. Go to Settings > Shopify > Account Mapping and verify all roles are mapped."
        return "Review the entry in Journal Entries and post manually after resolving the issue."

    def _handle_order_paid(self, event, data, mapping, dimension_context=None):
        """
        Create a SalesInvoice from a paid Shopify order.

        Routes through the Sales module instead of creating JEs directly.
        The SalesInvoice → post_sales_invoice flow handles:
        - JE creation (DR Clearing / CR Revenue / CR Tax / CR Shipping)
        - FX conversion and rounding
        - Period resolution and validation
        - Event emission (SALES_INVOICE_POSTED, not JOURNAL_ENTRY_POSTED)

        COGS is skipped here (skip_cogs=True) and handled separately
        at fulfillment time via StockLedgerEntry (Phase 6).
        """
        from sales.commands import create_and_post_invoice_for_platform
        from sales.models import TaxCode
        from shopify_connector.models import ShopifyOrder, ShopifyStore

        revenue_account = mapping.get(ROLE_SALES_REVENUE)
        if not revenue_account:
            logger.warning(
                "Shopify SALES_REVENUE mapping missing for company %s — skipping order %s",
                event.company,
                data.get("order_number"),
            )
            return

        shipping_account = mapping.get(ROLE_SHIPPING_REVENUE) or revenue_account
        tax_account = mapping.get(ROLE_SALES_TAX_PAYABLE)

        total_price = Decimal(str(data.get("amount", "0")))
        subtotal = Decimal(str(data.get("subtotal", "0")))
        total_tax = Decimal(str(data.get("total_tax", "0")))
        total_shipping = Decimal(str(data.get("total_shipping", "0")))
        order_name = data.get("order_name", data.get("order_number", ""))
        shopify_order_id = str(data.get("shopify_order_id", ""))
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()
        currency = data.get("currency") or getattr(event.company, "default_currency", "USD")

        if total_price <= 0:
            logger.warning("Skipping Shopify order %s — non-positive amount %s", order_name, total_price)
            return

        # Get the store's Customer and PostingProfile (created during connect)
        store = (
            ShopifyStore.objects.filter(
                company=event.company,
                status="ACTIVE",
            )
            .select_related("default_customer", "default_posting_profile")
            .first()
        )

        if not store or not store.default_customer_id or not store.default_posting_profile_id:
            logger.warning(
                "Shopify store missing Customer/PostingProfile for company %s — "
                "run _ensure_shopify_sales_setup() or reconnect the store",
                event.company,
            )
            return

        # Resolve or create OUTPUT TaxCode for this tax rate
        tax_code = None
        if total_tax > 0 and tax_account:
            revenue_amount = subtotal if subtotal > 0 else total_price - total_tax
            if revenue_amount > 0:
                tax_rate = (total_tax / revenue_amount).quantize(Decimal("0.0001"))
            else:
                tax_rate = Decimal("0")

            if tax_rate > 0:
                tax_pct = int(tax_rate * 100)
                tax_code_str = f"VAT{tax_pct}"
                with command_writes_allowed(), projection_writes_allowed():
                    tax_code, _ = TaxCode.objects.get_or_create(
                        company=event.company,
                        code=tax_code_str,
                        defaults={
                            "name": f"VAT {tax_pct}%",
                            "name_ar": f"ضريبة {tax_pct}%",
                            "rate": tax_rate,
                            "direction": TaxCode.TaxDirection.OUTPUT,
                            "tax_account": tax_account,
                            "is_active": True,
                        },
                    )

        # Build invoice lines
        invoice_lines = []

        # Line 1: Revenue (subtotal after discounts)
        revenue_amount = subtotal if subtotal > 0 else total_price - total_tax - total_shipping
        if revenue_amount > 0:
            line = {
                "account_id": revenue_account.id,
                "description": f"Shopify order {order_name}",
                "quantity": "1",
                "unit_price": str(revenue_amount),
                "discount_amount": "0",
            }
            if tax_code:
                line["tax_code_id"] = tax_code.id
            invoice_lines.append(line)

        # Line 2: Shipping revenue (if any)
        if total_shipping > 0:
            invoice_lines.append(
                {
                    "account_id": shipping_account.id,
                    "description": f"Shipping: {order_name}",
                    "quantity": "1",
                    "unit_price": str(total_shipping),
                    "discount_amount": "0",
                }
            )

        if not invoice_lines:
            logger.warning("No invoice lines for Shopify order %s — skipping", order_name)
            return

        # Create and post the SalesInvoice (skip COGS — handled at fulfillment)
        result = create_and_post_invoice_for_platform(
            company=event.company,
            customer_id=store.default_customer_id,
            posting_profile_id=store.default_posting_profile_id,
            lines=invoice_lines,
            invoice_date=entry_date,
            source="shopify",
            source_document_id=shopify_order_id,
            reference=order_name,
            notes=f"Shopify order: {order_name}",
            currency=currency,
            skip_cogs=True,
        )

        if not result.success:
            logger.error(
                "Failed to create SalesInvoice for Shopify order %s: %s",
                order_name,
                result.error,
            )
            return

        invoice = result.data.get("invoice")
        journal_entry = result.data.get("journal_entry")

        # Update local order record
        je_public_id = journal_entry.public_id if journal_entry else None
        ShopifyOrder.objects.filter(
            company=event.company,
            shopify_order_id=data.get("shopify_order_id"),
        ).update(
            status=ShopifyOrder.Status.PROCESSED,
            journal_entry_id=je_public_id,
        )

        logger.info(
            "Created SalesInvoice %s + JE %s for Shopify order %s (event %s)",
            invoice.invoice_number if invoice else "?",
            je_public_id,
            order_name,
            event.id,
        )

    def _handle_refund_created(self, event, data, mapping, dimension_context=None):
        """
        Create a CreditNote from a Shopify refund.

        Routes through the Sales module. The CreditNote → post_credit_note
        flow handles JE creation (DR Revenue / CR Clearing).

        If items were restocked, the restock COGS reversal is handled
        separately (kept as direct JE until Phase 6 moves it to StockLedger).
        """
        from sales.commands import create_and_post_credit_note_for_platform
        from sales.models import SalesInvoice
        from shopify_connector.models import ShopifyRefund

        revenue_account = mapping.get(ROLE_SALES_REVENUE)
        if not revenue_account:
            logger.warning("Shopify SALES_REVENUE mapping missing for refund — skipping")
            return

        amount = Decimal(str(data.get("amount", "0")))
        order_number = data.get("order_number", "")
        shopify_order_id = str(data.get("shopify_order_id", ""))
        refund_id = str(data.get("shopify_refund_id", ""))
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()
        currency = data.get("currency") or getattr(event.company, "default_currency", "USD")

        if amount <= 0:
            return

        # Find the original SalesInvoice for this Shopify order
        original_invoice = SalesInvoice.objects.filter(
            company=event.company,
            source="shopify",
            source_document_id=shopify_order_id,
            status=SalesInvoice.Status.POSTED,
        ).first()

        if not original_invoice:
            logger.warning(
                "Cannot create CreditNote for Shopify refund %s — original invoice not found "
                "for order %s. The order may predate the module-routing refactor.",
                refund_id,
                shopify_order_id,
            )
            return

        # Build credit note lines — single revenue reversal line
        cn_lines = [
            {
                "account_id": revenue_account.id,
                "description": f"Shopify refund: Order {order_number}",
                "quantity": "1",
                "unit_price": str(amount),
                "discount_amount": "0",
            }
        ]

        result = create_and_post_credit_note_for_platform(
            company=event.company,
            invoice_id=original_invoice.id,
            lines=cn_lines,
            credit_note_date=entry_date,
            source="shopify",
            source_document_id=refund_id,
            reason="RETURN",
            reason_notes=data.get("reason", ""),
            reference=f"Order {order_number}",
        )

        if not result.success:
            logger.error(
                "Failed to create CreditNote for Shopify refund %s: %s",
                refund_id,
                result.error,
            )
            return

        credit_note = result.data.get("credit_note")
        journal_entry = result.data.get("journal_entry")

        # Update ShopifyRefund record
        refund_record = ShopifyRefund.objects.filter(
            company=event.company,
            shopify_refund_id=data.get("shopify_refund_id"),
        ).first()

        if refund_record:
            je_public_id = journal_entry.public_id if journal_entry else None
            refund_record.status = ShopifyRefund.Status.PROCESSED
            refund_record.journal_entry_id = je_public_id
            refund_record.save(update_fields=["status", "journal_entry_id"])

            # Handle inventory restock (kept as direct JE for now)
            fx_rate, is_foreign = _resolve_exchange_rate(event.company, currency, entry_date)
            self._handle_refund_restock(
                event,
                refund_record,
                mapping,
                entry_date,
                currency,
                fx_rate,
                is_foreign,
                dimension_context,
            )

        logger.info(
            "Created CreditNote %s + JE for Shopify refund %s on order %s",
            credit_note.credit_note_number if credit_note else "?",
            refund_id,
            order_number,
        )

    def _handle_refund_restock(
        self, event, refund_record, mapping, entry_date, currency, fx_rate, is_foreign, dimension_context
    ):
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
            restock_lines.append(
                {
                    "sku": sku,
                    "title": line_item.get("title", sku),
                    "quantity": quantity,
                    "unit_cost": unit_cost,
                    "total_cost": total_cost,
                    "inventory_account": item.inventory_account,
                    "cogs_account": item.cogs_account,
                }
            )

        if not restock_lines:
            return

        # Create restock JE
        order_number = refund_record.order.shopify_order_name if refund_record.order else ""
        memo = f"Shopify restock: Order {order_number} (Refund {refund_record.shopify_refund_id})"

        if JournalEntry.objects.filter(
            company=event.company,
            memo=memo,
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

        lines = []
        line_no = 0
        for rl in restock_lines:
            converted = _convert_amount(rl["total_cost"], fx_rate) if is_foreign else rl["total_cost"]

            # DR Inventory (return to stock)
            line_no += 1
            lines.append(
                JournalLine(
                    entry=entry,
                    company=event.company,
                    public_id=uuid.uuid4(),
                    line_no=line_no,
                    account=rl["inventory_account"],
                    description=f"Restock: {rl['title']} x{rl['quantity']}",
                    debit=converted,
                    credit=Decimal("0"),
                    currency=currency,
                    exchange_rate=fx_rate,
                )
            )

            # CR COGS (reverse cost)
            line_no += 1
            lines.append(
                JournalLine(
                    entry=entry,
                    company=event.company,
                    public_id=uuid.uuid4(),
                    line_no=line_no,
                    account=rl["cogs_account"],
                    description=f"COGS reversal: {rl['title']} x{rl['quantity']}",
                    debit=Decimal("0"),
                    credit=converted,
                    currency=currency,
                    exchange_rate=fx_rate,
                )
            )

        # Shared validation: period, account postability
        force_incomplete, validation_errors = self._validate_entry(event.company, entry_date, lines)

        # Balance validation
        total_debit = sum(l.debit for l in lines)
        total_credit = sum(l.credit for l in lines)
        if total_debit != total_credit:
            force_incomplete = True
            validation_errors.append(f"Unbalanced: debit={total_debit} credit={total_credit}")

        if force_incomplete:
            entry.status = JournalEntry.Status.INCOMPLETE
            entry.posted_at = None
            entry.save(update_fields=["status", "posted_at"])

        JournalLine.objects.projection().bulk_create(lines)
        self._attach_dimensions(event.company, lines, dimension_context)

        if force_incomplete:
            self._mark_incomplete(entry, validation_errors, memo)
            return

        # Assign proper entry number
        seq = _next_company_sequence(event.company, "journal_entry_number")
        entry_number = f"JE-{event.company_id}-{seq:06d}"
        entry.entry_number = entry_number
        entry.save(update_fields=["entry_number"])

        total = sum(
            _convert_amount(rl["total_cost"], fx_rate) if is_foreign else rl["total_cost"] for rl in restock_lines
        )

        lines_data = []
        for line in lines:
            lines_data.append(
                {
                    "line_public_id": str(line.public_id),
                    "line_no": line.line_no,
                    "account_public_id": str(line.account.public_id),
                    "account_code": line.account.code,
                    "description": line.description,
                    "debit": str(line.debit),
                    "credit": str(line.credit),
                    "currency": currency,
                    "exchange_rate": str(fx_rate),
                }
            )

        emit_event_no_actor(
            company=event.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry.public_id),
            idempotency_key=f"shopify.restock.je:{refund_record.shopify_refund_id}",
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
            entry.public_id,
            order_number,
            len(restock_lines),
        )

    def _handle_payout_settled(self, event, data, mapping, dimension_context=None):
        """
        Create a PlatformSettlement for a Shopify payout.

        Routes through platform_connectors.commands which creates the
        settlement record + JE via accounting commands.
        """
        from platform_connectors.commands import create_and_post_settlement
        from platform_connectors.models import PlatformSettlement
        from shopify_connector.models import ShopifyPayout

        gross_amount = Decimal(str(data.get("gross_amount", "0")))
        fees = Decimal(str(data.get("fees", "0")))
        net_amount = Decimal(str(data.get("net_amount", "0")))
        payout_id = str(data.get("shopify_payout_id", ""))
        entry_date = _parse_date(data.get("payout_date") or data.get("transaction_date")) or event.created_at.date()
        currency = data.get("currency") or getattr(event.company, "default_currency", "USD")

        if gross_amount == 0:
            logger.warning("Skipping Shopify payout %s — zero gross amount", payout_id)
            return

        result = create_and_post_settlement(
            company=event.company,
            platform="shopify",
            platform_document_id=payout_id,
            settlement_type=PlatformSettlement.SettlementType.PAYOUT,
            gross_amount=abs(gross_amount),
            fees=abs(fees),
            net_amount=abs(net_amount),
            currency=currency,
            settlement_date=entry_date,
            reference=f"Payout {payout_id}",
        )

        if not result.success:
            logger.error("Failed to create settlement for payout %s: %s", payout_id, result.error)
            return

        journal_entry = result.data.get("journal_entry")
        je_public_id = journal_entry.public_id if journal_entry else None

        ShopifyPayout.objects.filter(
            company=event.company,
            shopify_payout_id=data.get("shopify_payout_id"),
        ).update(
            status=ShopifyPayout.Status.PROCESSED,
            journal_entry_id=je_public_id,
        )

        logger.info("Created payout settlement %s → JE %s", payout_id, je_public_id)

    def _handle_order_fulfilled(self, event, data, mapping, dimension_context=None):
        """
        COGS for fulfillment is now handled by the command layer
        (process_fulfillment → _create_cogs_for_fulfillment in commands.py).

        This projection handler is intentionally a no-op. The event is still
        emitted for audit trail purposes, but COGS JE + StockLedgerEntry
        creation happens in the command, not here.
        """
        logger.debug(
            "Fulfillment %s COGS handled by command layer — projection no-op",
            data.get("shopify_fulfillment_id", ""),
        )

    def _handle_dispute_created(self, event, data, mapping, dimension_context=None):
        """
        Create a PlatformSettlement for a Shopify dispute/chargeback.
        """
        from platform_connectors.commands import create_and_post_settlement
        from platform_connectors.models import PlatformSettlement
        from shopify_connector.models import ShopifyDispute

        dispute_amount = Decimal(str(data.get("dispute_amount", "0")))
        chargeback_fee = Decimal(str(data.get("chargeback_fee", "0")))
        dispute_id = str(data.get("shopify_dispute_id", ""))
        order_name = data.get("order_name", "")
        currency = data.get("currency") or getattr(event.company, "default_currency", "USD")
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()

        if dispute_amount <= 0:
            return

        result = create_and_post_settlement(
            company=event.company,
            platform="shopify",
            platform_document_id=dispute_id,
            settlement_type=PlatformSettlement.SettlementType.DISPUTE,
            gross_amount=dispute_amount,
            fees=chargeback_fee,
            net_amount=dispute_amount + chargeback_fee,
            currency=currency,
            settlement_date=entry_date,
            reference=f"Dispute {dispute_id} ({order_name})",
        )

        if not result.success:
            logger.error("Failed to create settlement for dispute %s: %s", dispute_id, result.error)
            return

        journal_entry = result.data.get("journal_entry")
        je_public_id = journal_entry.public_id if journal_entry else None

        ShopifyDispute.objects.filter(
            company=event.company,
            shopify_dispute_id=data.get("shopify_dispute_id"),
        ).update(
            status=ShopifyDispute.Status.PROCESSED,
            journal_entry_id=je_public_id,
        )

        logger.info("Created dispute settlement %s → JE %s", dispute_id, je_public_id)

    def _handle_dispute_won(self, event, data, mapping, dimension_context=None):
        """
        Create a PlatformSettlement reversal when a dispute is won.
        """
        from platform_connectors.commands import create_and_post_settlement
        from platform_connectors.models import PlatformSettlement

        dispute_amount = Decimal(str(data.get("dispute_amount", "0")))
        chargeback_fee = Decimal(str(data.get("chargeback_fee", "0")))
        dispute_id = str(data.get("shopify_dispute_id", ""))
        order_name = data.get("order_name", "")
        currency = data.get("currency") or getattr(event.company, "default_currency", "USD")
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()

        if dispute_amount <= 0:
            return

        result = create_and_post_settlement(
            company=event.company,
            platform="shopify",
            platform_document_id=f"{dispute_id}-won",
            settlement_type=PlatformSettlement.SettlementType.DISPUTE_WON,
            gross_amount=dispute_amount,
            fees=chargeback_fee,
            net_amount=dispute_amount + chargeback_fee,
            currency=currency,
            settlement_date=entry_date,
            reference=f"Dispute won {dispute_id} ({order_name})",
        )

        if not result.success:
            logger.error("Failed to create dispute-won settlement for %s: %s", dispute_id, result.error)
            return

        logger.info("Created dispute-won settlement %s", dispute_id)

    def _clear_projected_data(self, company) -> None:
        """Clear Shopify-generated journal entries for rebuild."""
        JournalEntry.objects.filter(
            company=company,
            source_module="shopify_connector",
        ).delete()
