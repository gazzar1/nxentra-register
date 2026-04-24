# platform_connectors/management/commands/sync_platform_dimensions.py
"""
Sync platform and store dimensions for commerce connectors.

Creates CONTEXT dimensions (platform, store) if they don't exist, then
creates dimension values for each registered platform and connected store.

Usage:
    python manage.py sync_platform_dimensions --company "Acme Corp"
    python manage.py sync_platform_dimensions --all
    python manage.py sync_platform_dimensions --all --dry-run
"""

from django.core.management.base import BaseCommand

from accounts.models import Company
from accounts.rls import rls_bypass
from platform_connectors.dimensions import (
    ensure_store_dimension_value,
    sync_platform_dimensions,
)


class Command(BaseCommand):
    help = "Sync platform/store analysis dimensions for commerce connectors."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=str, help="Company name to process")
        parser.add_argument("--all", action="store_true", help="Process all companies")
        parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")

    def handle(self, *args, **options):
        with rls_bypass():
            if options["all"]:
                companies = Company.objects.filter(is_active=True)
            elif options["company"]:
                companies = Company.objects.filter(name__icontains=options["company"])
            else:
                self.stderr.write(self.style.ERROR("Specify --company <name> or --all"))
                return

            for company in companies:
                self._sync_company(company, dry_run=options["dry_run"])

    def _sync_company(self, company, dry_run=False):
        self.stdout.write(f"\n{company.name}:")

        if dry_run:
            self.stdout.write("  (dry-run mode — no changes)")
            self._preview(company)
            return

        dims = sync_platform_dimensions(company)
        self.stdout.write(self.style.SUCCESS(f"  Dimensions: {', '.join(dims.keys())}"))

        # Sync store values from connected Shopify stores
        self._sync_shopify_stores(company)

    def _sync_shopify_stores(self, company):
        try:
            from shopify_connector.models import ShopifyStore

            stores = ShopifyStore.objects.filter(company=company).exclude(
                status=ShopifyStore.Status.DISCONNECTED,
            )
            for store in stores:
                ensure_store_dimension_value(
                    company,
                    platform_slug="shopify",
                    store_code=store.shop_domain,
                    store_name=store.shop_domain,
                )
                self.stdout.write(f"  Synced store: shopify:{store.shop_domain}")
        except ImportError:
            pass

    def _preview(self, company):
        from platform_connectors.registry import connector_registry

        for connector in connector_registry.all():
            self.stdout.write(f"  Would ensure platform value: {connector.platform_slug}")

        try:
            from shopify_connector.models import ShopifyStore

            stores = ShopifyStore.objects.filter(company=company).exclude(
                status=ShopifyStore.Status.DISCONNECTED,
            )
            for store in stores:
                self.stdout.write(f"  Would ensure store value: shopify:{store.shop_domain}")
        except ImportError:
            pass
