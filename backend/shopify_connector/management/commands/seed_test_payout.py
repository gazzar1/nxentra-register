# shopify_connector/management/commands/seed_test_payout.py
"""
Seed a single Shopify payout for order #1002 to test bank reconciliation.

Creates a ShopifyPayout linked to the existing store and order,
so it appears in Banking > Reconciliation as an unmatched payout.

Usage:
    python manage.py seed_test_payout
    python manage.py seed_test_payout --company-id 1
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Company
from shopify_connector.models import (
    ShopifyOrder,
    ShopifyPayout,
    ShopifyPayoutTransaction,
    ShopifyStore,
)


class Command(BaseCommand):
    help = "Seed a test Shopify payout for order #1002 (bank reconciliation testing)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id", type=int, default=None,
            help="Company ID (defaults to first company)",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        # Resolve company
        if options["company_id"]:
            company = Company.objects.get(id=options["company_id"])
        else:
            company = Company.objects.first()

        self.stdout.write(f"Company: {company.name}")

        # Find the real store
        store = ShopifyStore.objects.filter(company=company).first()
        if not store:
            self.stderr.write(self.style.ERROR("No ShopifyStore found. Connect Shopify first."))
            return
        self.stdout.write(f"Store: {store.shop_domain}")

        # Find order #1002
        order = ShopifyOrder.objects.filter(
            company=company,
            shopify_order_name="#1002",
        ).first()
        if not order:
            self.stderr.write(self.style.ERROR("Order #1002 not found."))
            return

        self.stdout.write(f"Order: {order.shopify_order_name} — {order.currency} {order.total_price}")

        # Shopify Payments fee: ~2.9% + 0
        gross = order.total_price  # EGP 232.00
        fee = (gross * Decimal("0.029")).quantize(Decimal("0.01"))  # EGP 6.73
        net = gross - fee  # EGP 225.27

        self.stdout.write(f"  Gross: {gross}")
        self.stdout.write(f"  Fee:   {fee}")
        self.stdout.write(f"  Net:   {net}")

        # Create payout (use a fake but realistic Shopify payout ID)
        payout, created = ShopifyPayout.objects.get_or_create(
            company=company,
            shopify_payout_id=1122334455,
            defaults={
                "store": store,
                "gross_amount": gross,
                "fees": fee,
                "net_amount": net,
                "currency": order.currency,
                "charges_gross": gross,
                "charges_fee": fee,
                "refunds_gross": Decimal("0.00"),
                "shopify_status": "paid",
                "payout_date": date(2026, 3, 20),
                "status": "PROCESSED",
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"  Created ShopifyPayout (id={payout.id})"))
        else:
            self.stdout.write(f"  Payout already exists (id={payout.id})")

        # Create payout transaction linking to the order
        txn, txn_created = ShopifyPayoutTransaction.objects.get_or_create(
            company=company,
            shopify_transaction_id=5566778899,
            defaults={
                "payout": payout,
                "transaction_type": "charge",
                "amount": gross,
                "fee": fee,
                "net": net,
                "currency": order.currency,
                "source_order_id": order.shopify_order_id,
                "source_type": "order",
                "verified": True,
                "local_order": order,
                "processed_at": order.shopify_created_at,
            },
        )

        if txn_created:
            self.stdout.write(self.style.SUCCESS(f"  Created PayoutTransaction (id={txn.id})"))
        else:
            self.stdout.write(f"  Transaction already exists (id={txn.id})")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Done! Now:"))
        self.stdout.write("  1. Import this CSV row into your CIB bank account:")
        self.stdout.write("     Date: 2026-03-20")
        self.stdout.write("     Description: SHOPIFY *1122334455 PAYOUT")
        self.stdout.write(f"     Amount: {net} (credit/deposit)")
        self.stdout.write("  2. Go to Banking > Reconciliation")
        self.stdout.write("  3. Click Auto-Match or Find Match on the bank deposit")
