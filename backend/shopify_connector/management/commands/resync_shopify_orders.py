# shopify_connector/management/commands/resync_shopify_orders.py
"""
Management command for manual Shopify order re-sync.

Catches up missed orders by polling the Shopify Orders API for a given
date range. Existing orders are skipped (idempotent).

Usage:
    # Re-sync last 7 days (default) for all active stores
    python manage.py resync_shopify_orders

    # Re-sync last 30 days for a specific company
    python manage.py resync_shopify_orders --company my-company --days 30

    # Re-sync a specific date range
    python manage.py resync_shopify_orders --from 2026-03-01 --to 2026-03-31
"""

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone as tz

from accounts.rls import rls_bypass


class Command(BaseCommand):
    help = "Re-sync missed Shopify orders by polling the Orders API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--company", type=str, default="",
            help="Company slug to sync (default: all active companies)",
        )
        parser.add_argument(
            "--days", type=int, default=7,
            help="Number of days to look back (default: 7). Ignored if --from is set.",
        )
        parser.add_argument(
            "--from", dest="from_date", type=str, default="",
            help="Start date (ISO format, e.g. 2026-03-01)",
        )
        parser.add_argument(
            "--to", dest="to_date", type=str, default="",
            help="End date (ISO format, e.g. 2026-03-31). Default: now.",
        )
        parser.add_argument(
            "--include-payouts", action="store_true",
            help="Also sync payouts (default: orders only)",
        )
        parser.add_argument(
            "--include-products", action="store_true",
            help="Also sync products (default: orders only)",
        )

    def handle(self, *args, **options):
        from shopify_connector.models import ShopifyStore
        from shopify_connector.tasks import _sync_orders

        now = tz.now()

        # Determine date range
        if options["from_date"]:
            created_at_min = datetime.fromisoformat(options["from_date"]).isoformat()
        else:
            created_at_min = (now - timedelta(days=options["days"])).isoformat()

        if options["to_date"]:
            created_at_max = datetime.fromisoformat(options["to_date"]).isoformat()
        else:
            created_at_max = now.isoformat()

        self.stdout.write(f"Re-syncing orders from {created_at_min} to {created_at_max}")

        # Find stores
        with rls_bypass():
            stores_qs = ShopifyStore.objects.filter(
                status=ShopifyStore.Status.ACTIVE
            ).select_related("company")

            if options["company"]:
                stores_qs = stores_qs.filter(company__slug=options["company"])

            stores = list(stores_qs)

        if not stores:
            self.stdout.write(self.style.WARNING("No active Shopify stores found."))
            return

        self.stdout.write(f"Found {len(stores)} active store(s)")

        for store in stores:
            self.stdout.write(f"\n--- {store.shop_domain} ({store.company.name}) ---")

            # Sync orders
            result = _sync_orders(store, created_at_min, created_at_max)
            self.stdout.write(
                f"  Orders: fetched={result.get('fetched', 0)}, "
                f"created={result.get('created', 0)}, "
                f"skipped={result.get('skipped', 0)}, "
                f"errors={result.get('errors', 0)}"
            )

            if result.get("error"):
                self.stdout.write(self.style.ERROR(f"  Error: {result['error']}"))

            # Optionally sync payouts
            if options["include_payouts"]:
                from shopify_connector.commands import sync_payouts
                payout_result = sync_payouts(store)
                if payout_result.success:
                    data = payout_result.data or {}
                    self.stdout.write(
                        f"  Payouts: created={data.get('created', 0)}, "
                        f"skipped={data.get('skipped', 0)}"
                    )
                else:
                    self.stdout.write(self.style.ERROR(f"  Payout error: {payout_result.error}"))

            # Optionally sync products
            if options["include_products"]:
                from shopify_connector.commands import sync_products
                product_result = sync_products(store)
                if product_result.success:
                    data = product_result.data or {}
                    self.stdout.write(
                        f"  Products: created={data.get('created', 0)}, "
                        f"linked={data.get('linked', 0)}, "
                        f"updated={data.get('updated', 0)}"
                    )
                else:
                    self.stdout.write(self.style.ERROR(f"  Product error: {product_result.error}"))

        self.stdout.write(self.style.SUCCESS("\nRe-sync complete."))
