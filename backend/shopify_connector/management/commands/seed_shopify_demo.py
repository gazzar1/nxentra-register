# shopify_connector/management/commands/seed_shopify_demo.py
"""
Seed a Shopify-first demo environment.

Creates a complete merchant story with realistic e-commerce data, accounting
entries, timing mismatches, refunds, and a dispute — designed to demonstrate
the Shopify reconciliation wedge in under 5 minutes.

Scenario seeded:
- ~30 orders over the last 45 days (various products, tax, fees)
- 4-5 weekly payouts (settled, with transaction-level detail)
- 1 full refund (customer request)
- 1 partial refund (wrong item)
- 1 dispute/chargeback (under review)
- 3-5 recent orders NOT yet paid out (timing mismatch — clearing balance)
- All events emitted → projections create journal entries automatically
- Operating expenses seeded for realistic P&L

Usage:
    python manage.py seed_shopify_demo --company-slug demo-co
    python manage.py seed_shopify_demo --company-slug demo-co --flush
"""

import random
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounting.mappings import ModuleAccountMapping
from accounting.models import Account
from accounts.models import Company, CompanyMembership
from accounts.rls import rls_bypass
from events.emitter import emit_event_no_actor
from events.types import EventTypes
from projections.models import FiscalPeriod, FiscalPeriodConfig, FiscalYear
from projections.write_barrier import command_writes_allowed, projection_writes_allowed
from shopify_connector.event_types import (
    ShopifyDisputeCreatedData,
    ShopifyOrderPaidData,
    ShopifyPayoutSettledData,
    ShopifyRefundCreatedData,
)
from shopify_connector.models import (
    ShopifyDispute,
    ShopifyOrder,
    ShopifyPayout,
    ShopifyPayoutTransaction,
    ShopifyRefund,
    ShopifyStore,
)

# ---------------------------------------------------------------------------
# Demo data constants
# ---------------------------------------------------------------------------

STORE_DOMAIN = "nxentra-demo.myshopify.com"

ORDER_ITEMS = [
    ("Wireless Earbuds Pro", Decimal("79.99")),
    ("Organic Cotton T-Shirt", Decimal("34.99")),
    ("Bamboo Water Bottle", Decimal("24.99")),
    ("LED Desk Lamp", Decimal("49.99")),
    ("Leather Wallet", Decimal("59.99")),
    ("Yoga Mat Premium", Decimal("39.99")),
    ("Stainless Steel Tumbler", Decimal("29.99")),
    ("Phone Case Ultra", Decimal("19.99")),
    ("Bluetooth Speaker Mini", Decimal("44.99")),
    ("Scented Candle Set", Decimal("27.99")),
    ("Running Shoes Lite", Decimal("89.99")),
    ("Ceramic Coffee Mug", Decimal("14.99")),
    ("Portable Charger 10K", Decimal("35.99")),
    ("Sunglasses Classic", Decimal("54.99")),
    ("Backpack Urban", Decimal("69.99")),
]

# Shopify account roles → GL accounts
SHOPIFY_ACCOUNTS = [
    ("SALES_REVENUE", "4100", "Sales Revenue", "إيرادات المبيعات", "REVENUE", "SALES"),
    ("SHOPIFY_CLEARING", "1150", "Shopify Clearing", "حساب تسوية شوبيفاي", "ASSET", "LIQUIDITY"),
    ("PAYMENT_PROCESSING_FEES", "5200", "Payment Processing Fees", "رسوم معالجة الدفع", "EXPENSE", "OPERATING_EXPENSE"),
    ("SALES_TAX_PAYABLE", "2200", "Sales Tax Payable", "ضريبة المبيعات المستحقة", "LIABILITY", "TAX_PAYABLE"),
    ("SHIPPING_REVENUE", "4200", "Shipping Revenue", "إيرادات الشحن", "REVENUE", "SALES"),
    ("SALES_DISCOUNTS", "4110", "Sales Discounts", "خصومات المبيعات", "REVENUE", "CONTRA_REVENUE"),
    ("CASH_BANK", "1100", "Cash and Bank", "النقدية والبنك", "ASSET", "LIQUIDITY"),
    ("CHARGEBACK_EXPENSE", "5210", "Chargeback Expense", "مصاريف رد المبالغ", "EXPENSE", "OTHER_EXPENSE"),
]

# General operating accounts for realistic P&L
OPERATING_ACCOUNTS = [
    ("bank", "1120", "Bank Account", "الحساب البنكي", "ASSET"),
    ("ar", "1200", "Accounts Receivable", "المدينون", "ASSET"),
    ("ap", "2100", "Accounts Payable", "الدائنون", "LIABILITY"),
    ("retained", "3200", "Retained Earnings", "الأرباح المحتجزة", "EQUITY"),
    ("rent", "6100", "Rent Expense", "مصروف الإيجار", "EXPENSE"),
    ("salaries", "6200", "Salaries & Wages", "الرواتب والأجور", "EXPENSE"),
    ("utilities", "6300", "Utilities", "المرافق", "EXPENSE"),
    ("marketing", "6400", "Marketing & Advertising", "التسويق والإعلان", "EXPENSE"),
    ("shipping_expense", "5300", "Shipping & Logistics", "الشحن واللوجستيات", "EXPENSE"),
]

OPERATING_EXPENSES = [
    ("rent", Decimal("3500"), "Monthly office rent"),
    ("salaries", Decimal("8500"), "Monthly payroll"),
    ("utilities", Decimal("420"), "Internet & utilities"),
    ("marketing", Decimal("1800"), "Google Ads & social media"),
    ("shipping_expense", Decimal("950"), "DHL / Aramex monthly"),
]


class Command(BaseCommand):
    help = "Seed a Shopify-first demo with events, journal entries, and reconciliation data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-slug",
            required=True,
            help="Company slug to seed data for",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing Shopify demo data before seeding",
        )

    def handle(self, *args, **options):
        slug = options["company_slug"]

        with rls_bypass():
            try:
                company = Company.objects.get(slug=slug)
            except Company.DoesNotExist:
                raise CommandError(f"Company with slug '{slug}' not found.")

            membership = (
                CompanyMembership.objects.filter(company=company, role="OWNER", is_active=True)
                .select_related("user")
                .first()
            )
            if not membership:
                raise CommandError("No active OWNER found for this company.")

            user = membership.user
            from accounts.authz import ActorContext

            actor = ActorContext(
                user=user,
                company=company,
                membership=membership,
                perms=frozenset(),
            )

            self.stdout.write(f"Seeding Shopify demo for: {company.name} ({slug})")

            with command_writes_allowed(), projection_writes_allowed():
                if options["flush"]:
                    self._flush(company)
                self._ensure_fiscal_periods(company)
                accounts = self._ensure_accounts(company)
                store = self._create_store(company)
                orders = self._create_orders(company, store)
                refunds = self._create_refunds(company, orders)
                settled_payouts = self._create_payouts(company, store, orders, refunds)
                unsettled_orders = self._create_unsettled_orders(company, store, len(orders))
                dispute = self._create_dispute(company, store, orders)
                self._emit_events(company, store, orders, refunds, settled_payouts, unsettled_orders, dispute)
                self._create_operating_expenses(actor, company, accounts)
                self._create_bank_statements(company, accounts, settled_payouts)

            self.stdout.write(
                self.style.SUCCESS(
                    f"\nDone! Shopify demo seeded for {company.name}.\n"
                    f"  Orders: {len(orders) + len(unsettled_orders)} "
                    f"({len(unsettled_orders)} awaiting payout)\n"
                    f"  Refunds: {len(refunds)}\n"
                    f"  Payouts: {len(settled_payouts)}\n"
                    f"  Disputes: {'1' if dispute else '0'}\n"
                    f"  Visit /accounting/bank-reconciliation/commerce to see "
                    f"the three-column reconciliation."
                )
            )

    def _flush(self, company):
        """Delete existing Shopify demo data."""
        from accounting.models import BankStatement, BankStatementLine
        from events.models import BusinessEvent

        # Delete bank statement lines and statements (for reconciliation demo)
        bsl_count, _ = BankStatementLine.objects.filter(company=company).delete()
        bs_count, _ = BankStatement.objects.filter(company=company).delete()
        if bsl_count or bs_count:
            self.stdout.write(f"  Deleted {bs_count} BankStatements, {bsl_count} BankStatementLines")

        # Delete Shopify models (dependency order)
        for model in [
            ShopifyPayoutTransaction,
            ShopifyPayout,
            ShopifyDispute,
            ShopifyRefund,
            ShopifyOrder,
            ShopifyStore,
        ]:
            count, _ = model.objects.filter(company=company).delete()
            self.stdout.write(f"  Deleted {count} {model.__name__}")

        # Delete Shopify events (JEs created by projection are left as orphans;
        # they'll be recreated via idempotent event emission on next seed)
        event_count, _ = BusinessEvent.objects.filter(company=company, event_type__startswith="shopify.").delete()
        self.stdout.write(f"  Deleted {event_count} Shopify events")

    # -----------------------------------------------------------------------
    # Fiscal periods
    # -----------------------------------------------------------------------

    def _ensure_fiscal_periods(self, company):
        """Create fiscal periods for the current year if they don't exist."""
        year = date.today().year
        if FiscalPeriod.objects.filter(company=company, fiscal_year=year).exists():
            return

        for period_num in range(1, 13):
            start = date(year, period_num, 1)
            _, last_day = monthrange(year, period_num)
            end = date(year, period_num, last_day)
            FiscalPeriod.objects.create(
                company=company,
                fiscal_year=year,
                period=period_num,
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=start,
                end_date=end,
                status=FiscalPeriod.Status.OPEN,
            )

        FiscalPeriodConfig.objects.get_or_create(
            company=company,
            fiscal_year=year,
            defaults={
                "period_count": 12,
                "open_from_period": 1,
                "open_to_period": 12,
            },
        )
        FiscalYear.objects.get_or_create(
            company=company,
            fiscal_year=year,
            defaults={"status": FiscalYear.Status.OPEN},
        )
        self.stdout.write(f"  Created fiscal periods for {year}")

    # -----------------------------------------------------------------------
    # Accounts
    # -----------------------------------------------------------------------

    def _ensure_accounts(self, company):
        """Create Shopify + operating GL accounts and module mappings."""
        accounts = {}

        for role, code, name, name_ar, acct_type, acct_role in SHOPIFY_ACCOUNTS:
            account, created = Account.objects.get_or_create(
                company=company,
                code=code,
                defaults={
                    "name": name,
                    "name_ar": name_ar,
                    "account_type": acct_type,
                    "role": acct_role,
                    "ledger_domain": "FINANCIAL",
                    "status": "ACTIVE",
                    "normal_balance": "DEBIT" if acct_type in ("ASSET", "EXPENSE") else "CREDIT",
                },
            )
            accounts[role] = account
            ModuleAccountMapping.objects.update_or_create(
                company=company,
                module="shopify_connector",
                role=role,
                defaults={"account": account},
            )

        for key, code, name, name_ar, acct_type in OPERATING_ACCOUNTS:
            account, _ = Account.objects.get_or_create(
                company=company,
                code=code,
                defaults={
                    "name": name,
                    "name_ar": name_ar,
                    "account_type": acct_type,
                    "ledger_domain": "FINANCIAL",
                    "status": "ACTIVE",
                    "normal_balance": "DEBIT" if acct_type in ("ASSET", "EXPENSE") else "CREDIT",
                },
            )
            accounts[key] = account

        self.stdout.write(f"  Ensured {len(accounts)} accounts + module mappings")
        return accounts

    # -----------------------------------------------------------------------
    # Shopify store
    # -----------------------------------------------------------------------

    def _create_store(self, company):
        store, created = ShopifyStore.objects.get_or_create(
            company=company,
            shop_domain=STORE_DOMAIN,
            defaults={
                "access_token": "demo-token-not-real",
                "status": "ACTIVE",
                "webhooks_registered": True,
                "scopes": "read_orders,read_payouts",
            },
        )
        if created:
            self.stdout.write(f"  Created store: {STORE_DOMAIN}")
        return store

    # -----------------------------------------------------------------------
    # Orders (settled — these have been paid out)
    # -----------------------------------------------------------------------

    def _create_orders(self, company, store):
        """Create ~30 orders spread over the last 7-45 days (all settled)."""
        today = date.today()
        orders = []
        base_id = 5000000000
        base_num = 1001

        for i in range(30):
            days_ago = random.randint(7, 45)
            order_date = today - timedelta(days=days_ago)
            product_name, base_price = random.choice(ORDER_ITEMS)
            qty = random.randint(1, 3)
            subtotal = base_price * qty
            tax = (subtotal * Decimal("0.08")).quantize(Decimal("0.01"))
            total = subtotal + tax

            order, created = ShopifyOrder.objects.get_or_create(
                company=company,
                shopify_order_id=base_id + i,
                defaults={
                    "store": store,
                    "shopify_order_number": str(base_num + i),
                    "shopify_order_name": f"#{base_num + i}",
                    "total_price": total,
                    "subtotal_price": subtotal,
                    "total_tax": tax,
                    "total_discounts": Decimal("0"),
                    "currency": "USD",
                    "financial_status": "paid",
                    "gateway": "shopify_payments",
                    "shopify_created_at": datetime.combine(order_date, datetime.min.time(), tzinfo=UTC),
                    "order_date": order_date,
                    "status": "PROCESSED",
                },
            )
            if created:
                orders.append(order)

        self.stdout.write(f"  Created {len(orders)} settled orders")
        return orders

    # -----------------------------------------------------------------------
    # Unsettled orders (timing mismatch — the merchant's real pain)
    # -----------------------------------------------------------------------

    def _create_unsettled_orders(self, company, store, offset):
        """Create 3-5 very recent orders that have NOT been paid out yet.

        These will show up in the clearing account as unresolved balance —
        the merchant can see money is owed but not yet deposited.
        """
        today = date.today()
        unsettled = []
        base_id = 5000000000 + offset
        base_num = 1001 + offset

        for i in range(random.randint(3, 5)):
            days_ago = random.randint(0, 2)
            order_date = today - timedelta(days=days_ago)
            product_name, base_price = random.choice(ORDER_ITEMS)
            qty = random.randint(1, 2)
            subtotal = base_price * qty
            tax = (subtotal * Decimal("0.08")).quantize(Decimal("0.01"))
            total = subtotal + tax

            order, created = ShopifyOrder.objects.get_or_create(
                company=company,
                shopify_order_id=base_id + i,
                defaults={
                    "store": store,
                    "shopify_order_number": str(base_num + i),
                    "shopify_order_name": f"#{base_num + i}",
                    "total_price": total,
                    "subtotal_price": subtotal,
                    "total_tax": tax,
                    "total_discounts": Decimal("0"),
                    "currency": "USD",
                    "financial_status": "paid",
                    "gateway": "shopify_payments",
                    "shopify_created_at": datetime.combine(order_date, datetime.min.time(), tzinfo=UTC),
                    "order_date": order_date,
                    "status": "RECEIVED",  # Not yet processed into payout
                },
            )
            if created:
                unsettled.append(order)

        self.stdout.write(f"  Created {len(unsettled)} unsettled orders (timing mismatch)")
        return unsettled

    # -----------------------------------------------------------------------
    # Refunds
    # -----------------------------------------------------------------------

    def _create_refunds(self, company, orders):
        """Create a full refund and a partial refund."""
        refunds = []
        if len(orders) < 10:
            return refunds

        # Full refund on order #5
        full_refund_order = orders[4]
        refund, created = ShopifyRefund.objects.get_or_create(
            company=company,
            shopify_refund_id=8000000001,
            defaults={
                "order": full_refund_order,
                "amount": full_refund_order.total_price,
                "currency": "USD",
                "reason": "Customer request — wrong size",
                "shopify_created_at": datetime.combine(
                    full_refund_order.order_date + timedelta(days=2),
                    datetime.min.time(),
                    tzinfo=UTC,
                ),
                "status": "RECEIVED",
            },
        )
        if created:
            refunds.append(refund)

        # Partial refund on order #12
        partial_refund_order = orders[11]
        partial_amount = (partial_refund_order.total_price * Decimal("0.40")).quantize(Decimal("0.01"))
        refund2, created = ShopifyRefund.objects.get_or_create(
            company=company,
            shopify_refund_id=8000000002,
            defaults={
                "order": partial_refund_order,
                "amount": partial_amount,
                "currency": "USD",
                "reason": "Partial refund — damaged item in shipment",
                "shopify_created_at": datetime.combine(
                    partial_refund_order.order_date + timedelta(days=4),
                    datetime.min.time(),
                    tzinfo=UTC,
                ),
                "status": "RECEIVED",
            },
        )
        if created:
            refunds.append(refund2)

        self.stdout.write(f"  Created {len(refunds)} refunds (1 full, 1 partial)")
        return refunds

    # -----------------------------------------------------------------------
    # Payouts
    # -----------------------------------------------------------------------

    def _create_payouts(self, company, store, orders, refunds):
        """Group settled orders into weekly Friday payouts."""
        orders_by_date = sorted(orders, key=lambda o: o.order_date)
        refund_order_ids = {r.order_id for r in refunds}

        # Bucket by payout Friday
        buckets = {}
        for order in orders_by_date:
            payout_date = order.order_date + timedelta(days=3)
            days_until_friday = (4 - payout_date.weekday()) % 7
            payout_friday = payout_date + timedelta(days=days_until_friday)
            buckets.setdefault(payout_friday.isoformat(), []).append(order)

        payouts = []
        payout_id_base = 9000000000
        txn_id_base = 7000000000

        for idx, (payout_date_str, bucket_orders) in enumerate(sorted(buckets.items())):
            payout_date_val = date.fromisoformat(payout_date_str)
            shopify_payout_id = payout_id_base + idx

            gross = sum(o.total_price for o in bucket_orders)
            fees = sum(
                (o.total_price * Decimal("0.029") + Decimal("0.30")).quantize(Decimal("0.01")) for o in bucket_orders
            )

            # Subtract refunds in this bucket
            refund_total = Decimal("0")
            bucket_refunds = [r for r in refunds if r.order_id in {o.id for o in bucket_orders}]
            for r in bucket_refunds:
                refund_total += r.amount
            gross -= refund_total

            net = gross - fees

            payout, created = ShopifyPayout.objects.get_or_create(
                company=company,
                shopify_payout_id=shopify_payout_id,
                defaults={
                    "store": store,
                    "gross_amount": gross,
                    "fees": fees,
                    "net_amount": net,
                    "currency": "USD",
                    "charges_gross": gross + refund_total,
                    "charges_fee": fees,
                    "refunds_gross": refund_total,
                    "shopify_status": "paid",
                    "payout_date": payout_date_val,
                    "status": "RECEIVED",
                },
            )

            if not created:
                payouts.append(payout)
                continue

            payouts.append(payout)

            # Charge transactions
            for ti, order in enumerate(bucket_orders):
                fee = (order.total_price * Decimal("0.029") + Decimal("0.30")).quantize(Decimal("0.01"))
                ShopifyPayoutTransaction.objects.get_or_create(
                    company=company,
                    shopify_transaction_id=txn_id_base + idx * 100 + ti,
                    defaults={
                        "payout": payout,
                        "transaction_type": "charge",
                        "amount": order.total_price,
                        "fee": fee,
                        "net": order.total_price - fee,
                        "currency": "USD",
                        "source_order_id": order.shopify_order_id,
                        "source_type": "order",
                        "verified": True,
                        "local_order": order,
                        "processed_at": order.shopify_created_at,
                    },
                )

            # Refund transactions
            for ri, refund in enumerate(bucket_refunds):
                ShopifyPayoutTransaction.objects.get_or_create(
                    company=company,
                    shopify_transaction_id=txn_id_base + idx * 100 + len(bucket_orders) + ri,
                    defaults={
                        "payout": payout,
                        "transaction_type": "refund",
                        "amount": -refund.amount,
                        "fee": Decimal("0"),
                        "net": -refund.amount,
                        "currency": "USD",
                        "source_order_id": refund.order.shopify_order_id,
                        "source_type": "refund",
                        "verified": True,
                        "processed_at": refund.shopify_created_at,
                    },
                )

        self.stdout.write(f"  Created {len(payouts)} payouts with transactions")
        return payouts

    # -----------------------------------------------------------------------
    # Dispute
    # -----------------------------------------------------------------------

    def _create_dispute(self, company, store, orders):
        """Create a chargeback on one of the older orders."""
        if len(orders) < 20:
            return None

        disputed_order = orders[18]
        dispute, created = ShopifyDispute.objects.get_or_create(
            company=company,
            shopify_dispute_id=6000000001,
            defaults={
                "store": store,
                "order": disputed_order,
                "shopify_order_id": disputed_order.shopify_order_id,
                "amount": disputed_order.total_price,
                "currency": "USD",
                "fee": Decimal("15.00"),
                "reason": "fraudulent",
                "shopify_dispute_status": "needs_response",
                "evidence_due_by": datetime.now(UTC) + timedelta(days=7),
                "status": "RECEIVED",
            },
        )
        if created:
            self.stdout.write(
                f"  Created dispute on order {disputed_order.shopify_order_name} "
                f"(${disputed_order.total_price} + $15 fee)"
            )
        return dispute if created else None

    # -----------------------------------------------------------------------
    # Event emission → projections create journal entries
    # -----------------------------------------------------------------------

    def _emit_events(self, company, store, orders, refunds, payouts, unsettled_orders, dispute):
        """Emit Shopify business events. Projections auto-run in DEBUG mode."""
        self.stdout.write("  Emitting events (projections will create journal entries)...")

        event_count = 0

        # ORDER_PAID for all orders (settled + unsettled)
        for order in orders + unsettled_orders:
            emit_event_no_actor(
                company=company,
                event_type=EventTypes.SHOPIFY_ORDER_PAID,
                aggregate_type="ShopifyOrder",
                aggregate_id=str(order.public_id),
                idempotency_key=f"shopify.order.paid:{order.shopify_order_id}",
                metadata={"source": "demo_seed", "shop_domain": store.shop_domain},
                data=ShopifyOrderPaidData(
                    amount=str(order.total_price),
                    currency=order.currency,
                    transaction_date=str(order.order_date),
                    document_ref=order.shopify_order_name,
                    store_public_id=str(store.public_id),
                    shopify_order_id=str(order.shopify_order_id),
                    order_number=order.shopify_order_number,
                    order_name=order.shopify_order_name,
                    subtotal=str(order.subtotal_price),
                    total_tax=str(order.total_tax),
                    total_shipping="0",
                    total_discounts=str(order.total_discounts),
                    financial_status=order.financial_status,
                    gateway=order.gateway,
                    line_items=[],
                    customer_email="",
                    customer_name="",
                ),
            )
            event_count += 1

        # REFUND_CREATED
        for refund in refunds:
            emit_event_no_actor(
                company=company,
                event_type=EventTypes.SHOPIFY_REFUND_CREATED,
                aggregate_type="ShopifyRefund",
                aggregate_id=str(refund.public_id),
                idempotency_key=f"shopify.refund.created:{refund.shopify_refund_id}",
                metadata={"source": "demo_seed", "shop_domain": store.shop_domain},
                data=ShopifyRefundCreatedData(
                    amount=str(refund.amount),
                    currency=refund.currency,
                    transaction_date=str(
                        refund.shopify_created_at.date()
                        if hasattr(refund.shopify_created_at, "date")
                        else refund.shopify_created_at
                    ),
                    document_ref=refund.order.shopify_order_name,
                    store_public_id=str(store.public_id),
                    shopify_refund_id=str(refund.shopify_refund_id),
                    shopify_order_id=str(refund.order.shopify_order_id),
                    order_number=refund.order.shopify_order_number,
                    reason=refund.reason,
                ),
            )
            event_count += 1

        # PAYOUT_SETTLED (only for settled payouts — unsettled orders have no payout)
        for payout in payouts:
            emit_event_no_actor(
                company=company,
                event_type=EventTypes.SHOPIFY_PAYOUT_SETTLED,
                aggregate_type="ShopifyPayout",
                aggregate_id=str(payout.public_id),
                idempotency_key=f"shopify.payout.settled:{payout.shopify_payout_id}",
                metadata={"source": "demo_seed", "shop_domain": store.shop_domain},
                data=ShopifyPayoutSettledData(
                    amount=str(payout.gross_amount),
                    currency=payout.currency,
                    transaction_date=str(payout.payout_date),
                    document_ref=f"Payout {payout.shopify_payout_id}",
                    store_public_id=str(store.public_id),
                    shopify_payout_id=str(payout.shopify_payout_id),
                    gross_amount=str(payout.gross_amount),
                    fees=str(payout.fees),
                    net_amount=str(payout.net_amount),
                    shopify_status=payout.shopify_status,
                    payout_date=str(payout.payout_date),
                ),
            )
            event_count += 1

        # DISPUTE_CREATED
        if dispute:
            emit_event_no_actor(
                company=company,
                event_type=EventTypes.SHOPIFY_DISPUTE_CREATED,
                aggregate_type="ShopifyDispute",
                aggregate_id=str(dispute.public_id),
                idempotency_key=f"shopify.dispute.created:{dispute.shopify_dispute_id}",
                metadata={"source": "demo_seed", "shop_domain": store.shop_domain},
                data=ShopifyDisputeCreatedData(
                    amount=str(dispute.amount),
                    currency=dispute.currency,
                    transaction_date=str(date.today()),
                    document_ref=dispute.order.shopify_order_name if dispute.order else "",
                    store_public_id=str(store.public_id),
                    shopify_dispute_id=str(dispute.shopify_dispute_id),
                    shopify_order_id=str(dispute.shopify_order_id or ""),
                    order_name=dispute.order.shopify_order_name if dispute.order else "",
                    dispute_amount=str(dispute.amount),
                    chargeback_fee=str(dispute.fee),
                    reason=dispute.reason,
                    dispute_status=dispute.shopify_dispute_status,
                ),
            )
            event_count += 1

        # If not running sync projections, run them explicitly
        if not getattr(settings, "PROJECTIONS_SYNC", False):
            self.stdout.write("  Running projections...")
            from projections.base import ProjectionRegistry

            registry = ProjectionRegistry()
            for projection in registry.all():
                projection.process_pending(company=company, limit=10000)

        self.stdout.write(f"  Emitted {event_count} events")

    # -----------------------------------------------------------------------
    # Operating expenses (makes P&L realistic)
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Bank statements (enables three-column reconciliation)
    # -----------------------------------------------------------------------

    def _create_bank_statements(self, company, accounts, payouts):
        """Create bank statement with lines matched to payout journal entries.

        The three-column commerce reconciliation view matches bank lines to
        payout JEs via: matched_journal_line__entry__memo == "Shopify payout: {id}"
        This method creates a bank statement with deposit lines for each payout,
        then finds the corresponding journal lines and matches them.
        """
        from accounting.models import BankStatement, BankStatementLine, JournalEntry, JournalLine

        if not payouts:
            return

        bank_account = accounts.get("CASH_BANK") or accounts.get("bank")
        if not bank_account:
            self.stdout.write("  No bank account found — skipping bank statements.")
            return

        # Check if bank statement already exists for this period
        sorted_payouts = sorted(payouts, key=lambda p: p.payout_date)
        period_start = sorted_payouts[0].payout_date - timedelta(days=7)
        period_end = sorted_payouts[-1].payout_date + timedelta(days=1)

        existing = BankStatement.objects.filter(
            company=company,
            account=bank_account,
            period_start=period_start,
            period_end=period_end,
        ).first()
        if existing:
            self.stdout.write("  Bank statement already exists — skipping.")
            return

        # Calculate running balance from payouts
        opening = Decimal("12500.00")  # Realistic starting balance
        total_deposits = sum(p.net_amount for p in sorted_payouts)
        closing = opening + total_deposits

        statement = BankStatement.objects.create(
            company=company,
            account=bank_account,
            statement_date=period_end,
            period_start=period_start,
            period_end=period_end,
            opening_balance=opening,
            closing_balance=closing,
            currency="USD",
            source="CSV",
            status="IN_PROGRESS",
            reference="Shopify demo bank statement",
            notes="Auto-generated by seed_shopify_demo for three-column reconciliation demo.",
        )

        matched_count = 0
        unmatched_count = 0

        for payout in sorted_payouts:
            # Find the journal entry created by the Shopify payout projection
            memo = f"Shopify payout: {payout.shopify_payout_id}"
            payout_je = JournalEntry.objects.filter(
                company=company,
                memo=memo,
                status="POSTED",
            ).first()

            # Find the Cash/Bank debit line (the bank deposit side)
            matched_jl = None
            if payout_je:
                matched_jl = JournalLine.objects.filter(
                    entry=payout_je,
                    account=bank_account,
                    debit__gt=0,
                ).first()

            bank_line = BankStatementLine.objects.create(
                statement=statement,
                company=company,
                line_date=payout.payout_date,
                description=f"SHOPIFY PAYOUT *{str(payout.shopify_payout_id)[-4:]}",
                reference=f"SPY-{payout.shopify_payout_id}",
                amount=payout.net_amount,  # Positive = deposit
                transaction_type="DEPOSIT",
                match_status="AUTO_MATCHED" if matched_jl else "UNMATCHED",
                matched_journal_line=matched_jl,
                match_confidence=Decimal("100.00") if matched_jl else None,
            )

            if matched_jl:
                matched_count += 1
            else:
                unmatched_count += 1

        # Add a few non-Shopify transactions for realism
        misc_transactions = [
            (period_start + timedelta(days=3), "STRIPE PAYOUT", Decimal("847.50"), "DEPOSIT"),
            (period_start + timedelta(days=10), "RENT PAYMENT - OFFICE", Decimal("-3500.00"), "WITHDRAWAL"),
            (period_start + timedelta(days=15), "PAYROLL BATCH 04/2026", Decimal("-8500.00"), "WITHDRAWAL"),
            (period_start + timedelta(days=20), "GOOGLE ADS", Decimal("-450.00"), "WITHDRAWAL"),
        ]
        for txn_date, desc, amount, txn_type in misc_transactions:
            if period_start <= txn_date <= period_end:
                BankStatementLine.objects.create(
                    statement=statement,
                    company=company,
                    line_date=txn_date,
                    description=desc,
                    reference="",
                    amount=amount,
                    transaction_type=txn_type,
                    match_status="UNMATCHED",
                )

        self.stdout.write(
            f"  Created bank statement with {matched_count + unmatched_count} payout lines "
            f"({matched_count} matched, {unmatched_count} unmatched) + "
            f"{len([t for t in misc_transactions if period_start <= t[0] <= period_end])} misc transactions"
        )

    def _create_operating_expenses(self, actor, company, accounts):
        """Create monthly operating expenses so the P&L isn't Shopify-only."""
        from accounting.commands import (
            create_journal_entry,
            post_journal_entry,
            save_journal_entry_complete,
        )
        from accounting.models import JournalEntry

        # Skip if JEs already exist (beyond Shopify-generated ones)
        non_shopify_jes = (
            JournalEntry.objects.filter(company=company).exclude(source_module="shopify_connector").count()
        )
        if non_shopify_jes > 5:
            self.stdout.write("  Operating expenses already exist — skipping.")
            return

        today = date.today()
        created_count = 0

        bank = accounts.get("bank") or accounts.get("CASH_BANK")
        if not bank:
            self.stdout.write("  No bank account found — skipping operating expenses.")
            return

        # Seed 3 months of operating expenses
        for months_ago in range(3, 0, -1):
            month_start = (today.replace(day=1) - timedelta(days=months_ago * 30)).replace(day=1)

            for acct_key, amount, memo in OPERATING_EXPENSES:
                expense_account = accounts.get(acct_key)
                if not expense_account:
                    continue

                entry_date = month_start + timedelta(days=random.randint(1, 25))
                # Vary amounts slightly
                varied = (amount * Decimal(str(random.uniform(0.9, 1.1)))).quantize(Decimal("0.01"))

                result = create_journal_entry(
                    actor=actor,
                    date=entry_date,
                    memo=f"{memo} — {entry_date.strftime('%B %Y')}",
                    lines=[
                        {"account_id": expense_account.id, "debit": str(varied), "credit": "0", "description": memo},
                        {
                            "account_id": bank.id,
                            "debit": "0",
                            "credit": str(varied),
                            "description": f"Bank payment — {memo}",
                        },
                    ],
                    kind="NORMAL",
                )
                if result.success:
                    entry = result.data
                    save = save_journal_entry_complete(actor, entry.id)
                    if save.success:
                        entry.refresh_from_db()
                        post_journal_entry(actor, entry.id)
                        created_count += 1

        self.stdout.write(f"  Created {created_count} operating expense entries")
