# shopify_connector/management/commands/setup_shopify_module_routing.py
"""
Set up existing Shopify stores for the module-routing architecture.

For each active ShopifyStore that's missing default_customer or
default_posting_profile, this command creates them using the same
logic as _ensure_shopify_sales_setup (called on new store connect).

Also ensures platform-managed warehouses exist.

Usage:
    python manage.py setup_shopify_module_routing
    python manage.py setup_shopify_module_routing --company-slug abb
"""

from django.core.management.base import BaseCommand

from accounts.rls import rls_bypass
from projections.write_barrier import command_writes_allowed, projection_writes_allowed
from shopify_connector.commands import (
    _ensure_shopify_sales_setup,
    _ensure_shopify_warehouse,
)
from shopify_connector.models import ShopifyStore


class Command(BaseCommand):
    help = "Set up existing Shopify stores for module-routing (Customer, PostingProfile, warehouses)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-slug",
            help="Only set up stores for this company (default: all active stores)",
        )

    def handle(self, *args, **options):
        slug = options.get("company_slug")

        with rls_bypass(), command_writes_allowed(), projection_writes_allowed():
            stores = ShopifyStore.objects.filter(status="ACTIVE").select_related("company")
            if slug:
                stores = stores.filter(company__slug=slug)

            if not stores.exists():
                self.stdout.write("No active Shopify stores found.")
                return

            for store in stores:
                self.stdout.write(f"\n{store.company.slug} ({store.shop_domain}):")

                # Ensure warehouses
                _ensure_shopify_warehouse(store)

                # Ensure Customer + PostingProfile
                had_customer = store.default_customer_id is not None
                had_profile = store.default_posting_profile_id is not None

                _ensure_shopify_sales_setup(store)

                store.refresh_from_db()

                if store.default_customer_id and not had_customer:
                    self.stdout.write(f"  Created Customer: {store.default_customer}")
                elif store.default_customer_id:
                    self.stdout.write(f"  Customer already exists: {store.default_customer}")
                else:
                    self.stdout.write(self.style.WARNING("  Customer NOT created (missing clearing account?)"))

                if store.default_posting_profile_id and not had_profile:
                    self.stdout.write(f"  Created PostingProfile: {store.default_posting_profile}")
                elif store.default_posting_profile_id:
                    self.stdout.write(f"  PostingProfile already exists: {store.default_posting_profile}")
                else:
                    self.stdout.write(self.style.WARNING("  PostingProfile NOT created"))

        self.stdout.write(self.style.SUCCESS("\nDone."))
