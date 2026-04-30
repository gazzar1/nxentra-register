"""
Backfill PaymentGateway rows for existing Shopify stores.

For each ACTIVE ShopifyStore, run _ensure_shopify_sales_setup. The helper
is idempotent — it short-circuits if both default_customer and
default_posting_profile are set on the store, so re-running is safe.

The new bootstrap step inside that helper creates the PaymentGateway +
per-gateway PostingProfile rows. Existing stores were connected before A2
landed, so they need this one-shot backfill.

Run on the droplet:
    python manage.py backfill_payment_gateways
    python manage.py backfill_payment_gateways --dry-run
"""

from django.core.management.base import BaseCommand

from accounting.payment_gateway import PaymentGateway
from shopify_connector.commands import (
    _bootstrap_shopify_payment_gateways,
    _ensure_shopify_sales_setup,
)
from shopify_connector.models import ShopifyStore


class Command(BaseCommand):
    help = "Backfill PaymentGateway rows for existing ACTIVE Shopify stores."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be created, but make no writes.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        stores = ShopifyStore.objects.filter(status=ShopifyStore.Status.ACTIVE).select_related(
            "company",
            "default_posting_profile",
            "default_posting_profile__control_account",
        )

        if not stores.exists():
            self.stdout.write(self.style.WARNING("No ACTIVE Shopify stores found."))
            return

        for store in stores:
            company = store.company
            existing = PaymentGateway.objects.filter(
                company=company,
                external_system="shopify",
            ).count()
            self.stdout.write(f"  {company.name} ({store.shop_domain}) — {existing} existing row(s)")

            if dry_run:
                continue

            if not store.default_posting_profile_id:
                # Setup never ran; let the full helper create the customer + profile + gateways.
                _ensure_shopify_sales_setup(store)
            else:
                # Profile already exists. Run only the gateway bootstrap step.
                clearing = store.default_posting_profile.control_account
                _bootstrap_shopify_payment_gateways(
                    company=company,
                    clearing_account=clearing,
                    fallback_profile=store.default_posting_profile,
                )

            after = PaymentGateway.objects.filter(
                company=company,
                external_system="shopify",
            ).count()
            self.stdout.write(self.style.SUCCESS(f"    -> now {after} row(s) ({after - existing} added)"))

        if dry_run:
            self.stdout.write(self.style.NOTICE("Dry-run — no writes made."))
        else:
            self.stdout.write(self.style.SUCCESS("Backfill complete."))
