# shopify_connector/management/commands/seed_shopify_demo.py
"""
Seed realistic Shopify demo data for the reconciliation dashboard.

Creates a store, orders, payouts, payout transactions, and a refund so that
the reconciliation page is immediately usable.

Usage:
    python manage.py seed_shopify_demo              # uses first company
    python manage.py seed_shopify_demo --company-id 1
    python manage.py seed_shopify_demo --flush       # delete existing demo data first
"""

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from accounting.mappings import ModuleAccountMapping
from accounting.models import Account
from accounts.models import Company
from projections.write_barrier import projection_writes_allowed
from shopify_connector.models import (
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

# Realistic Shopify order names / products
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

# Shopify account roles → GL accounts to create for demo
DEMO_ACCOUNTS = [
    ("SALES_REVENUE", "4100", "Sales Revenue", "REVENUE", "SALES"),
    ("SHOPIFY_CLEARING", "1150", "Shopify Clearing", "ASSET", "LIQUIDITY"),
    ("PAYMENT_PROCESSING_FEES", "5200", "Payment Processing Fees", "EXPENSE", "OPERATING_EXPENSE"),
    ("SALES_TAX_PAYABLE", "2200", "Sales Tax Payable", "LIABILITY", "TAX_PAYABLE"),
    ("SHIPPING_REVENUE", "4200", "Shipping Revenue", "REVENUE", "SALES"),
    ("SALES_DISCOUNTS", "4110", "Sales Discounts", "REVENUE", "CONTRA_REVENUE"),
    ("CASH_BANK", "1100", "Cash and Bank", "ASSET", "LIQUIDITY"),
    ("CHARGEBACK_EXPENSE", "5210", "Chargeback Expense", "EXPENSE", "OTHER_EXPENSE"),
]


class Command(BaseCommand):
    help = "Seed realistic Shopify demo data for the reconciliation dashboard"

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id", type=int, default=None,
            help="Company ID to seed data for (defaults to first company)",
        )
        parser.add_argument(
            "--flush", action="store_true",
            help="Delete existing demo data before seeding",
        )
        parser.add_argument(
            "--flush-only", action="store_true",
            help="Delete existing Shopify demo data before seeding",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        with projection_writes_allowed():
            self._handle(options)

    def _handle(self, options):
        # Resolve company
        if options["company_id"]:
            company = Company.objects.get(id=options["company_id"])
        else:
            company = Company.objects.first()
            if not company:
                self.stderr.write(self.style.ERROR("No companies found. Create a company first."))
                return

        self.stdout.write(f"Seeding Shopify demo data for: {company.name}")

        if options["flush"] or options["flush_only"]:
            self._flush(company)
            if options["flush_only"]:
                self.stdout.write(self.style.SUCCESS("Flush complete."))
                return

        # Ensure GL accounts and mappings exist
        self._ensure_accounts(company)

        # Create store
        store = self._create_store(company)

        # Generate orders spanning the last 45 days
        orders = self._create_orders(company, store)

        # Create a refund on one of the orders
        refund = self._create_refund(company, orders)

        # Create payouts (weekly batches)
        self._create_payouts(company, store, orders, refund)

        self.stdout.write(self.style.SUCCESS(
            f"Done! Created {len(orders)} orders, 1 refund, and payouts. "
            f"Visit /shopify/reconciliation to see the dashboard."
        ))

    def _flush(self, company):
        """Delete existing Shopify demo data for the company."""
        count, _ = ShopifyPayoutTransaction.objects.filter(company=company).delete()
        self.stdout.write(f"  Deleted {count} payout transactions")
        count, _ = ShopifyPayout.objects.filter(company=company).delete()
        self.stdout.write(f"  Deleted {count} payouts")
        count, _ = ShopifyRefund.objects.filter(company=company).delete()
        self.stdout.write(f"  Deleted {count} refunds")
        count, _ = ShopifyOrder.objects.filter(company=company).delete()
        self.stdout.write(f"  Deleted {count} orders")
        count, _ = ShopifyStore.objects.filter(company=company).delete()
        self.stdout.write(f"  Deleted {count} stores")

    def _ensure_accounts(self, company):
        """Create GL accounts and module mappings if they don't exist."""
        for role, code, name, acct_type, acct_role in DEMO_ACCOUNTS:
            # Find or create the GL account
            account, created = Account.objects.get_or_create(
                company=company,
                code=code,
                defaults={
                    "name": name,
                    "account_type": acct_type,
                    "role": acct_role,
                    "ledger_domain": "FINANCIAL",
                    "status": "ACTIVE",
                },
            )
            if created:
                self.stdout.write(f"  Created account {code} - {name}")

            # Ensure the module mapping exists
            ModuleAccountMapping.objects.update_or_create(
                company=company,
                module="shopify_connector",
                role=role,
                defaults={"account": account},
            )

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

    def _create_orders(self, company, store):
        """Create ~30 orders spread over the last 45 days."""
        today = date.today()
        orders = []
        base_order_id = 5000000000
        base_order_num = 1001

        for i in range(30):
            days_ago = random.randint(1, 45)
            order_date = today - timedelta(days=days_ago)
            product_name, base_price = random.choice(ORDER_ITEMS)
            qty = random.randint(1, 3)
            subtotal = base_price * qty
            tax = (subtotal * Decimal("0.08")).quantize(Decimal("0.01"))
            total = subtotal + tax

            shopify_order_id = base_order_id + i
            order_number = str(base_order_num + i)

            order, created = ShopifyOrder.objects.get_or_create(
                company=company,
                shopify_order_id=shopify_order_id,
                defaults={
                    "store": store,
                    "shopify_order_number": order_number,
                    "shopify_order_name": f"#{order_number}",
                    "total_price": total,
                    "subtotal_price": subtotal,
                    "total_tax": tax,
                    "total_discounts": Decimal("0.00"),
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

        self.stdout.write(f"  Created {len(orders)} orders")
        return orders

    def _create_refund(self, company, orders):
        """Create a refund on the 5th order."""
        if len(orders) < 5:
            return None

        refund_order = orders[4]
        refund_amount = refund_order.total_price

        refund, created = ShopifyRefund.objects.get_or_create(
            company=company,
            shopify_refund_id=8000000001,
            defaults={
                "order": refund_order,
                "amount": refund_amount,
                "currency": "USD",
                "reason": "Customer request - wrong size",
                "shopify_created_at": datetime.combine(
                    refund_order.order_date + timedelta(days=2),
                    datetime.min.time(), tzinfo=UTC,
                ),
                "status": "PROCESSED",
            },
        )
        if created:
            self.stdout.write(f"  Created refund for order #{refund_order.shopify_order_name}: {refund_amount}")
        return refund

    def _create_payouts(self, company, store, orders, refund):
        """
        Group orders into weekly payouts, add transactions, and leave
        one payout partially matched to demonstrate discrepancy states.
        """
        today = date.today()
        # Sort orders by date
        orders_by_date = sorted(orders, key=lambda o: o.order_date)

        # Group into weekly buckets
        buckets = {}
        for order in orders_by_date:
            # Payout happens ~3 days after order
            payout_date = order.order_date + timedelta(days=3)
            # Round to the nearest Friday (Shopify pays out on Fridays)
            days_until_friday = (4 - payout_date.weekday()) % 7
            payout_friday = payout_date + timedelta(days=days_until_friday)
            bucket_key = payout_friday.isoformat()
            buckets.setdefault(bucket_key, []).append(order)

        payout_id_base = 9000000000
        txn_id_base = 7000000000
        payout_count = 0

        for idx, (payout_date_str, bucket_orders) in enumerate(sorted(buckets.items())):
            payout_date_val = date.fromisoformat(payout_date_str)
            shopify_payout_id = payout_id_base + idx

            # Calculate payout totals
            gross = sum(o.total_price for o in bucket_orders)
            # ~2.9% + $0.30 per transaction (Shopify Payments rate)
            fees = sum(
                (o.total_price * Decimal("0.029") + Decimal("0.30")).quantize(Decimal("0.01"))
                for o in bucket_orders
            )

            # If there's a refund in this bucket, subtract from gross
            refund_amount = Decimal("0.00")
            refund_in_bucket = False
            if refund and refund.order in bucket_orders:
                refund_amount = refund.amount
                refund_in_bucket = True
                gross -= refund_amount

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
                    "charges_gross": gross + refund_amount,
                    "charges_fee": fees,
                    "refunds_gross": refund_amount,
                    "shopify_status": "paid",
                    "payout_date": payout_date_val,
                    "status": "PROCESSED",
                },
            )

            if not created:
                continue

            payout_count += 1
            txn_idx = 0

            # Create charge transactions for each order
            for order in bucket_orders:
                fee = (order.total_price * Decimal("0.029") + Decimal("0.30")).quantize(Decimal("0.01"))
                net_txn = order.total_price - fee

                # For the last payout, leave some transactions unverified
                # to show partial/discrepancy state
                is_last = idx == len(buckets) - 1
                verified = not is_last or txn_idx < len(bucket_orders) // 2

                ShopifyPayoutTransaction.objects.get_or_create(
                    company=company,
                    shopify_transaction_id=txn_id_base + idx * 100 + txn_idx,
                    defaults={
                        "payout": payout,
                        "transaction_type": "charge",
                        "amount": order.total_price,
                        "fee": fee,
                        "net": net_txn,
                        "currency": "USD",
                        "source_order_id": order.shopify_order_id,
                        "source_type": "order",
                        "verified": verified,
                        "local_order": order if verified else None,
                        "processed_at": order.shopify_created_at,
                    },
                )
                txn_idx += 1

            # Add refund transaction if applicable
            if refund_in_bucket:
                ShopifyPayoutTransaction.objects.get_or_create(
                    company=company,
                    shopify_transaction_id=txn_id_base + idx * 100 + txn_idx,
                    defaults={
                        "payout": payout,
                        "transaction_type": "refund",
                        "amount": -refund_amount,
                        "fee": Decimal("0.00"),
                        "net": -refund_amount,
                        "currency": "USD",
                        "source_order_id": refund.order.shopify_order_id,
                        "source_type": "refund",
                        "verified": True,
                        "processed_at": refund.shopify_created_at,
                    },
                )

        self.stdout.write(f"  Created {payout_count} payouts with transactions")
