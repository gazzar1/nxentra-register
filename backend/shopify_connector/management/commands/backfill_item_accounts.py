# shopify_connector/management/commands/backfill_item_accounts.py
"""
Backfill COGS and Inventory accounts on Items that were auto-created
from Shopify without accounts.

Usage:
    python manage.py backfill_item_accounts --company-slug demo-co
    python manage.py backfill_item_accounts  # all companies
"""

from django.core.management.base import BaseCommand

from accounts.models import Company
from accounts.rls import rls_bypass
from projections.write_barrier import command_writes_allowed
from sales.models import Item
from shopify_connector.commands import (
    _ensure_inventory_accounts,
    _resolve_default_item_accounts,
)


class Command(BaseCommand):
    help = "Backfill COGS/Inventory accounts on Items missing them"

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-slug",
            help="Only backfill for this company (default: all)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be updated without making changes",
        )

    def handle(self, *args, **options):
        slug = options.get("company_slug")
        dry_run = options["dry_run"]

        with rls_bypass(), command_writes_allowed():
            if slug:
                companies = Company.objects.filter(slug=slug)
            else:
                companies = Company.objects.filter(is_active=True)

            total_updated = 0

            for company in companies:
                # Find INVENTORY items missing cogs or inventory account
                items = Item.objects.filter(
                    company=company,
                    item_type="INVENTORY",
                    is_active=True,
                ).filter(
                    # Missing at least one required account
                    cogs_account__isnull=True,
                ) | Item.objects.filter(
                    company=company,
                    item_type="INVENTORY",
                    is_active=True,
                    inventory_account__isnull=True,
                )
                items = items.distinct()

                if not items.exists():
                    self.stdout.write(f"  {company.slug}: no items need backfill")
                    continue

                _ensure_inventory_accounts(company)
                defaults = _resolve_default_item_accounts(company)

                inv_account = defaults.get("inventory")
                cogs_account = defaults.get("cogs")
                sales_account = defaults.get("sales")

                if not inv_account or not cogs_account:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  {company.slug}: could not resolve "
                            f"inventory ({inv_account}) or COGS ({cogs_account}) account"
                        )
                    )
                    continue

                count = 0
                for item in items:
                    changes = []
                    if not item.inventory_account:
                        item.inventory_account = inv_account
                        changes.append("inventory")
                    if not item.cogs_account:
                        item.cogs_account = cogs_account
                        changes.append("cogs")
                    if not item.sales_account and sales_account:
                        item.sales_account = sales_account
                        changes.append("sales")

                    if changes:
                        if dry_run:
                            self.stdout.write(f"  [DRY RUN] {company.slug}/{item.code}: would set {', '.join(changes)}")
                        else:
                            item.save(
                                update_fields=[
                                    f
                                    for f in [
                                        "inventory_account_id" if "inventory" in changes else None,
                                        "cogs_account_id" if "cogs" in changes else None,
                                        "sales_account_id" if "sales" in changes else None,
                                    ]
                                    if f
                                ],
                                _projection_write=True,
                            )
                            self.stdout.write(f"  {company.slug}/{item.code}: set {', '.join(changes)}")
                        count += 1

                total_updated += count
                self.stdout.write(f"  {company.slug}: {'would update' if dry_run else 'updated'} {count} items")

            prefix = "[DRY RUN] " if dry_run else ""
            self.stdout.write(self.style.SUCCESS(f"\n{prefix}Total: {total_updated} items backfilled"))
