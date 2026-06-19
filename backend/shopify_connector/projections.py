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

import hashlib
import logging
import time
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

_DIMENSION_VALUE_CODE_MAX_LENGTH = 20
_DIMENSION_VALUE_NAME_MAX_LENGTH = 100


def _coerce_shopify_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _dimension_value_code(value):
    """
    Normalize a Shopify-derived value into an uppercase dimension-value code
    (spaces become underscores; hyphens and other punctuation are preserved)
    that fits AnalysisDimensionValue.code (max 20). When the normalized text
    exceeds the limit, truncate and append a short content hash so two distinct
    long values don't collapse onto the same code (which would mis-merge their
    analytics). Idempotent — re-applying to an already-coded value is a no-op,
    so the call-site code stays consistent with the code that
    _ensure_dimension_and_value re-derives and stores.
    """
    text = _coerce_shopify_text(value).upper().replace(" ", "_")
    if len(text) <= _DIMENSION_VALUE_CODE_MAX_LENGTH:
        return text
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8].upper()
    return f"{text[: _DIMENSION_VALUE_CODE_MAX_LENGTH - 9]}-{digest}"


def _dimension_value_name(value):
    return _coerce_shopify_text(value)[:_DIMENSION_VALUE_NAME_MAX_LENGTH]


def _parse_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    return value


# A23: bounded retry for the refund-handler projection race.
#
# When a Shopify order_paid event and its corresponding refund_created
# event land in the same projection pass (or close together), the refund
# handler's SalesInvoice POSTED lookup can fail to see the order_paid
# handler's commit due to transaction-isolation lag. Pre-A23, the refund
# handler silently early-returned and `ProjectionAppliedEvent` recorded
# the event as "processed" — permanent data loss with no merchant signal.
#
# This helper retries the lookup a few times with short sleeps so the
# common case (within-pass race) self-heals without any external
# intervention. If the invoice truly never arrives (the order predates
# the integration, or the webhook arrived without a corresponding paid
# event), retries exhaust and the caller can log a warning the same way
# it always did.
_INVOICE_LOOKUP_MAX_ATTEMPTS = 5
_INVOICE_LOOKUP_DELAY_SECONDS = 0.1


def _find_posted_shopify_invoice(company, shopify_order_id, *, max_attempts=None, delay=None):
    """Locate the POSTED SalesInvoice for a Shopify order, retrying on miss.

    Returns the SalesInvoice or None after `max_attempts` exhausts.
    """
    from sales.models import SalesInvoice

    attempts = max_attempts if max_attempts is not None else _INVOICE_LOOKUP_MAX_ATTEMPTS
    sleep_for = delay if delay is not None else _INVOICE_LOOKUP_DELAY_SECONDS

    for attempt in range(attempts):
        invoice = SalesInvoice.objects.filter(
            company=company,
            source="shopify",
            source_document_id=shopify_order_id,
            status=SalesInvoice.Status.POSTED,
        ).first()
        if invoice:
            return invoice
        if attempt < attempts - 1:
            time.sleep(sleep_for)
    return None


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

    val_code = _dimension_value_code(val_code)
    val_name = _dimension_value_name(val_name) or val_code
    if not val_code:
        return None

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

    return AnalysisDimensionValue.objects.projection().get_or_create(
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
            # A80: raise instead of silent return so the failure surfaces in
            # ProjectionFailureLog → /finance/exceptions. The event remains
            # unprocessed (transaction rolls back); once the operator wires
            # the mapping, the next process_pending pass self-heals.
            from projections.exceptions import ProjectionStateError

            raise ProjectionStateError(
                f"No ModuleAccountMapping for shopify_connector module on company {company.name}",
                fix_hint=(
                    "Complete the Shopify onboarding wizard, or run "
                    "`python manage.py setup_shopify_module_routing` to "
                    "wire up the chart-of-accounts mapping."
                ),
            )

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
            sku = _coerce_shopify_text(first_item.get("sku"))
            title = _coerce_shopify_text(first_item.get("title"))
            if sku:
                val_code = _dimension_value_code(sku)
                _ensure_dimension_and_value(
                    company, "PRODUCT", "Product", "المنتج", val_code, title or sku, _REVENUE_COGS_INV
                )
                context["PRODUCT"] = val_code

            # 3. CATEGORY — product_type (Revenue + COGS)
            product_type = _coerce_shopify_text(first_item.get("product_type"))
            if product_type:
                val_code = _dimension_value_code(product_type)
                _ensure_dimension_and_value(
                    company, "CATEGORY", "Product Category", "فئة المنتج", val_code, product_type, _REVENUE_COGS
                )
                context["CATEGORY"] = val_code

            # 4. VENDOR — brand/supplier (Revenue + COGS + Inventory)
            vendor = _coerce_shopify_text(first_item.get("vendor"))
            if vendor:
                val_code = _dimension_value_code(vendor)
                _ensure_dimension_and_value(
                    company, "VENDOR", "Vendor / Brand", "المورد / العلامة", val_code, vendor, _REVENUE_COGS_INV
                )
                context["VENDOR"] = val_code

        # 5. REGION — shipping country (Revenue + COGS)
        shipping = raw.get("shipping_address") or {}
        country = shipping.get("country", "") or shipping.get("country_code", "")
        if country:
            val_code = _dimension_value_code(country)
            country_name = shipping.get("country", country)
            _ensure_dimension_and_value(
                company, "REGION", "Region / Country", "المنطقة / الدولة", val_code, country_name, _REVENUE_COGS
            )
            context["REGION"] = val_code

        # 6. CITY — shipping city (Revenue + COGS)
        city = shipping.get("city", "")
        if city:
            val_code = _dimension_value_code(city)
            _ensure_dimension_and_value(company, "CITY", "City", "المدينة", val_code, city, _REVENUE_COGS)
            context["CITY"] = val_code

        # 7. SOURCE — order source (Revenue only)
        source_name = raw.get("source_name", "")
        if source_name:
            val_code = _dimension_value_code(source_name)
            _ensure_dimension_and_value(
                company, "SOURCE", "Order Source", "مصدر الطلب", val_code, source_name, _REVENUE_ONLY
            )
            context["SOURCE"] = val_code

        # 8. PAY_METHOD — payment gateway (Fees + Clearing)
        gateway = data.get("gateway", "") or raw.get("gateway", "")
        if gateway:
            val_code = _dimension_value_code(gateway)
            _ensure_dimension_and_value(
                company, "PAY_METHOD", "Payment Method", "طريقة الدفع", val_code, gateway, _FEES_CLEARING
            )
            context["PAY_METHOD"] = val_code

        # 9. PROMOTION — discount codes (Revenue only)
        discount_codes = raw.get("discount_codes", [])
        if discount_codes:
            code = discount_codes[0].get("code", "")
            if code:
                val_code = _dimension_value_code(code)
                _ensure_dimension_and_value(
                    company, "PROMOTION", "Promotion / Discount", "العرض / الخصم", val_code, code, _REVENUE_ONLY
                )
                context["PROMOTION"] = val_code

        # 10. CAMPAIGN — order tags (Revenue + COGS)
        tags_str = raw.get("tags", "")
        if tags_str:
            first_tag = tags_str.split(",")[0].strip()
            if first_tag:
                val_code = _dimension_value_code(first_tag)
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
            val_code = _dimension_value_code(domain.upper().replace(".", "_").replace("WWW_", ""))
            _ensure_dimension_and_value(company, "REFERRER", "Referrer", "المُحيل", val_code, domain, _REVENUE_ONLY)
            context["REFERRER"] = val_code

        # 12. CUST_SEGMENT — customer tags (Revenue + COGS)
        # Shopify sends "customer": null when the order has no customer attached;
        # dict.get default doesn't catch that, so coerce explicitly.
        customer = raw.get("customer") or {}
        customer_tags = customer.get("tags", "")
        if customer_tags:
            first_tag = customer_tags.split(",")[0].strip()
            if first_tag:
                val_code = _dimension_value_code(first_tag)
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

    def _resolve_store_for_event(self, event, data):
        """Resolve the ShopifyStore a financial event belongs to.

        A134: replaces the historical
        ``ShopifyStore.filter(status=ACTIVE).first()`` pattern (same bug
        family as A57). That bare ``.first()`` mis-attributed orders for
        multi-store merchants and, after a connect/disconnect churn, returned
        ``None`` and re-errored every projection beat (the b74379 reviewer
        residual on Shopify_R).

        The event's identifier is the source of truth for WHICH store the
        order belongs to. We resolve it **regardless of store status**:
        ``disconnect_store`` only flips status + blanks the access_token; the
        store's ``default_customer`` / ``default_posting_profile`` are
        preserved, so posting a still-queued order under its now-disconnected
        store is correct (and a reconnect reuses the same row). Re-homing the
        order to a *different* active store would mis-attribute the money —
        the A57 bug this exists to kill.

        1. ``store_public_id`` from the event payload — the exact store that
           emitted the order (carried on every Shopify financial event; see
           shopify_connector/event_types.py). Any status.
        2. ``shop_domain`` from event metadata — same store, domain-keyed
           (covers any payload predating ``store_public_id``). Any status.
        3. The company's SOLE active store — ONLY for legacy events carrying
           neither identifier (the single-store common case). Never guess
           among several active stores; that ambiguity is the A57 bug.

        Returns the ShopifyStore, or ``None`` only when an explicit identifier
        matches no store row (store hard-deleted), or a legacy event arrives
        while the active-store count is not exactly one. The caller bounds the
        resulting defer with an age cap.
        """
        from shopify_connector.models import ShopifyStore

        base = ShopifyStore.objects.filter(company=event.company).select_related(
            "default_customer",
            "default_posting_profile",
            "default_cod_settlement_provider",
        )

        store_public_id = (data or {}).get("store_public_id")
        shop_domain = (event.metadata or {}).get("shop_domain")

        # 1 + 2: an explicit identifier is authoritative. Honor it regardless
        # of status, and NEVER fall through to the sole-active fallback when
        # one is present — doing so would re-home the order to a different
        # store and mis-attribute the money (A57). A None here means the row
        # was hard-deleted, which the caller defers/surfaces.
        if store_public_id or shop_domain:
            store = None
            if store_public_id:
                store = base.filter(public_id=store_public_id).first()
            if store is None and shop_domain:
                store = base.filter(shop_domain=shop_domain).order_by("-created_at").first()
            return store

        # 3: no identifier at all (legacy event). Safe only when unambiguous.
        active = list(base.filter(status=ShopifyStore.Status.ACTIVE)[:2])
        if len(active) == 1:
            return active[0]
        return None

    def _ensure_store_setup(self, store):
        """A134: idempotent self-heal of a store missing its default
        Customer / PostingProfile (the OAuth-callback ordering gap A80
        documented — SHOPIFY_CLEARING didn't exist yet at connect time, so
        the store was left unconfigured).

        Runs the canonical ``_ensure_shopify_sales_setup`` once and refreshes
        the instance. The caller re-checks and raises ``ProjectionStateError``
        if it is STILL missing (loud-not-silent per A80 — never skip a
        financial event silently).

        Note: the heal is best-effort. Its Customer/PostingProfile/store writes
        execute inside process_pending's per-event ``transaction.atomic()``, so
        if this handler later raises, they roll back and re-run next beat — all
        writes are get_or_create-based, so this is idempotent, just repeated.
        ``_ensure_shopify_sales_setup`` opens its own
        ``command_writes_allowed() + projection_writes_allowed()``; the inner
        ``projection_writes_allowed()`` is REQUIRED (not redundant) because
        Customer/PostingProfile are projection-owned read models and the write
        barrier checks only the top of the context stack.
        """
        if store.default_customer_id and store.default_posting_profile_id:
            return store
        from shopify_connector.commands import _ensure_shopify_sales_setup

        _ensure_shopify_sales_setup(store)
        store.refresh_from_db()
        return store

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
        from shopify_connector.models import ShopifyOrder

        revenue_account = mapping.get(ROLE_SALES_REVENUE)
        if not revenue_account:
            # A80: raise instead of silent return.
            from projections.exceptions import ProjectionStateError

            raise ProjectionStateError(
                f"SALES_REVENUE not configured in shopify_connector mapping for "
                f"company {event.company.name} (order {data.get('order_number')})",
                fix_hint=(
                    "Setup → Account Mapping → Shopify Connector → add SALES_REVENUE "
                    "role pointing at the company's main Sales Revenue account."
                ),
            )

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

        # A134: resolve the exact store this order belongs to (by
        # store_public_id / shop_domain) instead of blindly taking the
        # company's first ACTIVE store. The bare .first() mis-attributed
        # orders for multi-store merchants and, after a disconnect, returned
        # None and re-errored every projection beat (the b74379 residual on
        # Shopify_R; same bug family as A57).
        store = self._resolve_store_for_event(event, data)
        if store is None:
            # The event's store can't be resolved — its identifier points at a
            # hard-deleted store row, or a legacy (no-identifier) event arrived
            # while the company has 0 or 2+ active stores. While the event is
            # fresh, defer (quiet INFO retry): a store may be (re)connecting.
            # Raising would re-hit Sentry every beat; the deferred event stays
            # unprocessed (no data loss) and posts once a store appears.
            #
            # Bounded like the refund handler (A41): past 24h, stop deferring
            # and surface loudly (A80) so a genuinely orphaned order becomes
            # operator-visible in /finance/exceptions instead of re-scanning
            # the company stream forever (head-of-line stall).
            from datetime import timedelta

            from django.utils import timezone as _tz

            event_age = _tz.now() - event.recorded_at
            label = order_name or data.get("order_number")
            if event_age < timedelta(hours=24):
                from projections.base import DeferEvent

                raise DeferEvent(
                    f"No Shopify store resolvable yet for order {label} on "
                    f"company {event.company.name} (store_public_id="
                    f"{data.get('store_public_id')!r}, aged "
                    f"{event_age.total_seconds():.0f}s) — deferring."
                )

            from projections.exceptions import ProjectionStateError

            raise ProjectionStateError(
                f"No Shopify store resolvable for order {label} on company "
                f"{event.company.name} after {event_age}; the originating "
                f"store (store_public_id={data.get('store_public_id')!r}) "
                f"appears deleted or ambiguous.",
                fix_hint=(
                    "Reconnect the store via Settings → Integrations → Shopify. "
                    "If the order is genuinely orphaned, resolve it from "
                    "/finance/exceptions."
                ),
            )

        # A134: idempotent self-heal for the OAuth-callback ordering gap
        # (SHOPIFY_CLEARING didn't exist yet at connect time so the store was
        # left without a default_customer / posting_profile). Run the
        # canonical setup once; only raise if it's STILL missing afterward —
        # the exact failure mode the comment in accounts/commands.py:3485 was
        # warning about. Loud-not-silent per A80: surfaces in
        # /finance/exceptions instead of a silent no-op.
        store = self._ensure_store_setup(store)
        if not store.default_customer_id or not store.default_posting_profile_id:
            from projections.exceptions import ProjectionStateError

            raise ProjectionStateError(
                f"Shopify store missing Customer/PostingProfile for company "
                f"{event.company.name} (order {data.get('order_number')})",
                fix_hint=(
                    "Run `python manage.py setup_shopify_module_routing --company-slug=<slug>` "
                    "or reconnect the store via Settings → Integrations → Shopify."
                ),
            )

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
            # A80: raise INVALID_DATA. Order has neither revenue nor shipping
            # amount — would create an empty invoice that disappears from the
            # merchant's books without explanation. Surface for operator review.
            from projections.exceptions import ProjectionInvalidDataError

            raise ProjectionInvalidDataError(
                f"Shopify order {order_name} has no postable lines "
                f"(subtotal={subtotal}, total_tax={total_tax}, total_shipping={total_shipping}, "
                f"total_price={total_price}). Cannot create an invoice."
            )

        # Resolve which SettlementProvider routes this order's clearing line.
        # The JE Debit AR Control is read from posting_profile.control_account
        # at JE-build time, so per-provider routing is fully expressed by which
        # profile we pick. The clearing line is also tagged with the provider's
        # AnalysisDimensionValue so the reconciliation engine can pivot on
        # (clearing_account, dimension_value) to surface per-provider balances.
        provider = self._resolve_settlement_provider(event, store, data.get("gateway") or "")
        control_line_analysis_tags = self._build_provider_tags(provider)

        posting_profile_id = store.default_posting_profile_id
        if provider and provider.is_active and provider.posting_profile_id:
            posting_profile_id = provider.posting_profile_id

        # Create and post the SalesInvoice (skip COGS — handled at fulfillment)
        result = create_and_post_invoice_for_platform(
            company=event.company,
            customer_id=store.default_customer_id,
            posting_profile_id=posting_profile_id,
            lines=invoice_lines,
            invoice_date=entry_date,
            source="shopify",
            source_document_id=shopify_order_id,
            reference=order_name,
            notes=f"Shopify order: {order_name}",
            currency=currency,
            skip_cogs=True,
            control_line_analysis_tags=control_line_analysis_tags,
        )

        if not result.success:
            # C (closed-period quarantine): a CLOSED fiscal period is terminal
            # — it won't self-heal until an operator reopens it. Raising the
            # usual ProjectionCommandFailedError under stop_on_error would
            # head-of-line-stall the WHOLE projection (A126 made this reachable
            # by importing historical dates; the oldest closed-period order
            # would freeze every later order). Raise ProjectionTerminalSkip
            # instead so the framework records the failure (A80) AND advances.
            # (A85 chunk 6 already left the invoice + INCOMPLETE JE persisted
            # via independent atomics — data isn't lost.)
            from accounting.validation import _check_period

            if _check_period(event.company, entry_date):
                from projections.exceptions import ProjectionTerminalSkip

                raise ProjectionTerminalSkip(
                    f"Shopify order {order_name} dated {entry_date} cannot post: {result.error}",
                    fix_hint=(
                        "Reopen the fiscal period to post this order's INCOMPLETE "
                        "journal entry, or exclude pre-close history from the import."
                    ),
                )

            # A80: any other downstream refusal is (potentially) transient — it
            # should surface AND retry, so keep raising ProjectionCommandFailedError.
            # The 2026-05-25 incident (A78's missing `not auto_created` bypass)
            # is the canonical case this guards.
            from projections.exceptions import ProjectionCommandFailedError

            raise ProjectionCommandFailedError(
                f"create_and_post_invoice_for_platform failed for Shopify order {order_name}: {result.error}",
                command_name="create_and_post_invoice_for_platform",
                original_error=result.error,
            )

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

    def _build_provider_tags(self, provider) -> list:
        """Shape a SettlementProvider's dimension_value as analysis_tags
        for the JE clearing line. Empty list if the provider has no
        dimension_value (shouldn't happen post-A12 but defensive)."""
        if not provider or not provider.dimension_value_id:
            return []
        return [
            {
                "dimension_public_id": str(provider.dimension_value.dimension.public_id),
                "value_public_id": str(provider.dimension_value.public_id),
            }
        ]

    def _build_shopify_payments_tags(self, company) -> list:
        """Build the analysis_tags for Shopify-Payments-relayed events
        (payouts, disputes). Shopify only relays payouts and disputes
        for its own payment processor — Paymob/PayPal/Bosta arrive via
        A14 manual CSV import — so the tag is always shopify_payments."""
        from accounting.settlement_provider import SettlementProvider

        provider = (
            SettlementProvider.objects.filter(
                company=company,
                external_system="shopify",
                normalized_code="shopify_payments",
                is_active=True,
            )
            .select_related("dimension_value", "dimension_value__dimension")
            .first()
        )
        return self._build_provider_tags(provider)

    def _resolve_settlement_provider(self, event, store, raw_gateway: str):
        """Resolve which SettlementProvider routes this order's clearing line.

        Three branches:

        1. **Cash on Delivery.** Shopify's webhook says `cash_on_delivery`
           but doesn't carry the courier identity (Bosta vs DHL vs Aramex).
           We route via `store.default_cod_settlement_provider`. If the
           merchant hasn't configured it yet, lazy-create a
           `pending_cod_setup` row with needs_review=True so the order
           still posts (via fallback profile) but is operator-visible.

        2. **Empty gateway.** Some early Shopify orders ship without a
           gateway at all (admin-paid drafts, etc.). Return None — the
           caller falls back to store.default_posting_profile and posts
           with no analysis tag.

        3. **Known prepaid gateway** (paymob, paypal, shopify_payments,
           manual, bank_transfer). Look up by normalized gateway code; on
           miss, lazy-create with needs_review=True.

        `raw_gateway` is passed in (rather than read from `data`) so
        non-order events — refund_created, payout_settled — can resolve
        a provider too: refund handler reads the original ShopifyOrder's
        gateway; payout/dispute handlers pass "shopify_payments" since
        Shopify only relays its own payouts.
        """
        from accounting.settlement_provider import SettlementProvider, normalize_gateway_code

        normalized = normalize_gateway_code(raw_gateway)

        if not normalized:
            return None

        # Branch 1: COD orders route via the store's configured courier.
        if normalized == "cash_on_delivery":
            if store.default_cod_settlement_provider_id:
                # Merchant has configured a courier — use it.
                return (
                    SettlementProvider.objects.select_related(
                        "posting_profile",
                        "dimension_value",
                        "dimension_value__dimension",
                    )
                    .filter(pk=store.default_cod_settlement_provider_id)
                    .first()
                )
            # Not configured — lazy-create a flagged row so the operator
            # sees it. Order still posts via fallback profile.
            return SettlementProvider.lookup_or_create_for_review(
                company=event.company,
                external_system="shopify",
                raw_gateway="pending_cod_setup",
                fallback_posting_profile=store.default_posting_profile,
            )

        # Branch 3: prepaid gateways — direct lookup, lazy-create on miss.
        provider = SettlementProvider.lookup(
            company=event.company,
            external_system="shopify",
            raw_gateway=raw_gateway,
        )
        if provider is None:
            provider = SettlementProvider.lookup_or_create_for_review(
                company=event.company,
                external_system="shopify",
                raw_gateway=raw_gateway,
                fallback_posting_profile=store.default_posting_profile,
            )
        return provider

    def _handle_refund_created(self, event, data, mapping, dimension_context=None):
        """
        Create a CreditNote from a Shopify refund.

        Routes through the Sales module. The CreditNote → post_credit_note
        flow handles JE creation (DR Revenue / CR Clearing).

        If items were restocked, the restock COGS reversal is handled
        separately (kept as direct JE until Phase 6 moves it to StockLedger).
        """
        from sales.commands import create_and_post_credit_note_for_platform
        from shopify_connector.models import ShopifyOrder, ShopifyRefund

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

        # A23: lookup with bounded retry — the order_paid handler's
        # SalesInvoice commit may not yet be visible to this transaction
        # if both events land in the same projection pass.
        original_invoice = _find_posted_shopify_invoice(event.company, shopify_order_id)

        if not original_invoice:
            # A41: the in-pass retry exhausted, but the order_paid event
            # may still be pending in the queue (Shopify webhook
            # re-ordering, or a worker restart split the batch). If the
            # event is fresh (< 24h), raise DeferEvent so process_pending
            # rewinds the bookmark and re-attempts on the next pass.
            # Older events are treated as truly orphan (the order really
            # doesn't exist) — log warning and accept, matching pre-A41
            # behavior.
            from datetime import timedelta

            from django.utils import timezone as _tz

            from projections.base import DeferEvent

            event_age = _tz.now() - event.recorded_at
            if event_age < timedelta(hours=24):
                raise DeferEvent(
                    f"Awaiting order_paid for Shopify order {shopify_order_id} "
                    f"(refund {refund_id} aged {event_age.total_seconds():.0f}s)"
                )

            logger.warning(
                "Cannot create CreditNote for Shopify refund %s — original invoice not found "
                "for order %s after %d retries and %s of waiting. Treating as orphan; the "
                "order may predate the module-routing refactor or the order_paid webhook "
                "may have been lost.",
                refund_id,
                shopify_order_id,
                _INVOICE_LOOKUP_MAX_ATTEMPTS,
                event_age,
            )
            return

        # A12 follow-up: tag the credit-note clearing line with the same
        # settlement provider the original order posted under, so the
        # refund drains the correct provider's clearing balance. For COD
        # orders this resolves through ShopifyStore.default_cod_settlement_provider —
        # if the merchant changed couriers between order and refund the
        # refund tags the *current* courier (acceptable; differences
        # show up via needs_review on lazy-create).
        original_order = (
            ShopifyOrder.objects.filter(
                company=event.company,
                shopify_order_id=data.get("shopify_order_id"),
            )
            .select_related("store", "store__default_posting_profile", "store__default_cod_settlement_provider")
            .first()
        )
        order_gateway = original_order.gateway if original_order else ""
        # A134: tag the refund via the ORIGINAL order's own store (its FK), not
        # a re-resolve from the refund event. The order already booked under
        # that store; re-resolving could land on a *different* active store for
        # a multi-store merchant and mis-tag the refund's clearing line.
        refund_store = original_order.store if original_order else None
        refund_provider = (
            self._resolve_settlement_provider(event, refund_store, order_gateway) if refund_store else None
        )
        refund_tags = self._build_provider_tags(refund_provider)

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
            control_line_analysis_tags=refund_tags,
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
        entry_number = f"JE-{seq:06d}"
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

        # A12 follow-up: tag the clearing JE line with shopify_payments.
        # Shopify only relays its OWN payouts (Shopify Payments-processed
        # money); Paymob/PayPal/Bosta payouts come via A14 manual import.
        # Without the tag, the credit to clearing wouldn't drain the
        # right provider's reconciliation balance.
        clearing_tags = self._build_shopify_payments_tags(event.company)

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
            clearing_line_analysis_tags=clearing_tags,
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
            clearing_line_analysis_tags=self._build_shopify_payments_tags(event.company),
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
            clearing_line_analysis_tags=self._build_shopify_payments_tags(event.company),
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
