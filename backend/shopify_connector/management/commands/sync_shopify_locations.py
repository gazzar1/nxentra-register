# shopify_connector/management/commands/sync_shopify_locations.py
"""
Sync Shopify locations into Nxentra warehouses.

For each active ShopifyStore, fetches all locations from the Shopify API
and creates/updates platform-managed Warehouse records.

Usage:
    python manage.py sync_shopify_locations
    python manage.py sync_shopify_locations --company-slug abb
"""

from datetime import UTC, datetime

import requests
from django.core.management.base import BaseCommand

from accounts.rls import rls_bypass
from inventory.models import Warehouse
from projections.write_barrier import command_writes_allowed
from shopify_connector.models import ShopifyStore


class Command(BaseCommand):
    help = "Sync Shopify locations into platform-managed warehouses"

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-slug",
            help="Only sync for this company (default: all active stores)",
        )

    def handle(self, *args, **options):
        slug = options.get("company_slug")

        with rls_bypass(), command_writes_allowed():
            stores = ShopifyStore.objects.filter(status="ACTIVE")
            if slug:
                stores = stores.filter(company__slug=slug)

            total_created = 0
            total_updated = 0

            for store in stores.select_related("company"):
                created, updated = self._sync_store_locations(store)
                total_created += created
                total_updated += updated

            self.stdout.write(self.style.SUCCESS(f"Done. Created {total_created}, updated {total_updated} warehouses."))

    def _sync_store_locations(self, store):
        headers = {
            "X-Shopify-Access-Token": store.access_token,
            "Content-Type": "application/json",
        }

        try:
            resp = requests.get(
                f"https://{store.shop_domain}/admin/api/2025-01/locations.json",
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"  {store.company.slug}: failed to fetch locations: {exc}"))
            return 0, 0

        locations = resp.json().get("locations", [])
        created = 0
        updated = 0

        for loc in locations:
            loc_id = str(loc["id"])
            loc_name = loc.get("name", f"Shopify Location {loc_id}")
            loc_active = loc.get("active", True)
            address_parts = [
                loc.get("address1", ""),
                loc.get("address2", ""),
                loc.get("city", ""),
                loc.get("province", ""),
                loc.get("country_name", ""),
            ]
            address = ", ".join(p for p in address_parts if p)

            # Generate a short code from the location name
            code = f"SHOP-{loc_id[-6:]}"

            warehouse, was_created = Warehouse.objects.update_or_create(
                company=store.company,
                platform="shopify",
                platform_location_id=loc_id,
                is_platform_managed=True,
                defaults={
                    "code": code,
                    "name": f"Shopify: {loc_name}",
                    "name_ar": "",
                    "address": address,
                    "is_active": loc_active,
                    "last_synced_at": datetime.now(UTC),
                },
            )

            if was_created:
                created += 1
                self.stdout.write(f"  {store.company.slug}: created '{loc_name}' ({code})")
            else:
                # Update name and address if changed
                save_fields = []
                if warehouse.name != f"Shopify: {loc_name}":
                    warehouse.name = f"Shopify: {loc_name}"
                    save_fields.append("name")
                if warehouse.address != address:
                    warehouse.address = address
                    save_fields.append("address")
                if warehouse.is_active != loc_active:
                    warehouse.is_active = loc_active
                    save_fields.append("is_active")
                warehouse.last_synced_at = datetime.now(UTC)
                save_fields.append("last_synced_at")
                if save_fields:
                    warehouse.save(update_fields=save_fields)
                updated += 1

            # If this is the first/only location and no default exists, make it default
            if was_created:
                has_default = Warehouse.objects.filter(
                    company=store.company,
                    is_default=True,
                ).exists()
                if not has_default:
                    warehouse.is_default = True
                    warehouse.save(update_fields=["is_default"])
                    self.stdout.write(f"  {store.company.slug}: set '{loc_name}' as default")

        self.stdout.write(f"  {store.company.slug}: {len(locations)} locations ({created} created, {updated} updated)")
        return created, updated
