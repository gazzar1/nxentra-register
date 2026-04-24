# shopify_connector/management/commands/sync_shopify_products.py
"""
Sync Shopify products to Nxentra Items.

Pulls all products from a connected Shopify store and creates/links
Items in the inventory system. Each Shopify variant with a SKU becomes
an Item (matched by SKU = Item.code).

Usage:
    python manage.py sync_shopify_products
    python manage.py sync_shopify_products --company-id 1
    python manage.py sync_shopify_products --inventory-account-id 5 --cogs-account-id 6
    python manage.py sync_shopify_products --dry-run
"""

from django.core.management.base import BaseCommand

from accounts.models import Company
from shopify_connector.commands import sync_products
from shopify_connector.models import ShopifyStore


class Command(BaseCommand):
    help = "Sync Shopify products to Nxentra Items"

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id",
            type=int,
            default=None,
            help="Company ID (defaults to first company)",
        )
        parser.add_argument(
            "--inventory-account-id",
            type=int,
            default=None,
            help="Default inventory asset account ID for auto-created Items",
        )
        parser.add_argument(
            "--cogs-account-id",
            type=int,
            default=None,
            help="Default COGS expense account ID for auto-created Items",
        )

    def handle(self, *args, **options):
        # Resolve company
        if options["company_id"]:
            company = Company.objects.get(id=options["company_id"])
        else:
            company = Company.objects.first()
            if not company:
                self.stderr.write(self.style.ERROR("No companies found."))
                return

        self.stdout.write(f"Company: {company.name}")

        # Find active store
        store = ShopifyStore.objects.filter(
            company=company,
            status=ShopifyStore.Status.ACTIVE,
        ).first()

        if not store:
            self.stderr.write(self.style.ERROR("No active Shopify store. Connect a store first."))
            return

        self.stdout.write(f"Store: {store.shop_domain}")

        # Run sync
        result = sync_products(
            store,
            inventory_account_id=options["inventory_account_id"],
            cogs_account_id=options["cogs_account_id"],
        )

        if not result.success:
            self.stderr.write(self.style.ERROR(f"Sync failed: {result.error}"))
            return

        data = result.data
        self.stdout.write(
            self.style.SUCCESS(
                f"Done! Created: {data['created']}, "
                f"Linked: {data['linked']}, "
                f"Updated: {data['updated']}, "
                f"Skipped (no SKU): {data['skipped']}"
            )
        )

        if data.get("errors"):
            for err in data["errors"]:
                self.stderr.write(f"  Error: {err}")
