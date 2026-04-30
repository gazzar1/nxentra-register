"""
Backfill SettlementProvider rows for existing Shopify stores.

For each ACTIVE ShopifyStore, run _ensure_shopify_sales_setup. The helper
is idempotent — it short-circuits if both default_customer and
default_posting_profile are set on the store, so re-running is safe.

The bootstrap step inside that helper creates the SettlementProvider +
per-provider PostingProfile rows. Existing stores were connected before
A2.5 landed, so they need this one-shot backfill.

Optional `--cod-provider <code>` flag (currently inert; A12 wires
ShopifyStore.default_cod_settlement_provider into the schema and this
flag will set it for the named normalized_code, e.g.
`--cod-provider bosta`). The flag is parsed today so deploy commands
can be written ahead of A12 shipping.

Run on the droplet:
    python manage.py backfill_settlement_providers
    python manage.py backfill_settlement_providers --dry-run
    python manage.py backfill_settlement_providers --cod-provider bosta   # A12+
"""

from django.core.management.base import BaseCommand

from accounting.settlement_provider import SettlementProvider
from shopify_connector.commands import (
    _bootstrap_shopify_settlement_providers,
    _ensure_shopify_sales_setup,
)
from shopify_connector.models import ShopifyStore


class Command(BaseCommand):
    help = "Backfill SettlementProvider rows for existing ACTIVE Shopify stores."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be created, but make no writes.",
        )
        parser.add_argument(
            "--cod-provider",
            help=(
                "Normalized SettlementProvider code to use as the store's "
                "default COD provider (e.g. 'bosta', 'aramex'). Inert until "
                "A12 wires ShopifyStore.default_cod_settlement_provider; "
                "accepted now so deploy scripts can be written ahead."
            ),
            default=None,
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        cod_provider_code = options.get("cod_provider")
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
            existing = SettlementProvider.objects.filter(
                company=company,
                external_system="shopify",
            ).count()
            self.stdout.write(f"  {company.name} ({store.shop_domain}) — {existing} existing row(s)")

            if dry_run:
                continue

            if not store.default_posting_profile_id:
                # Setup never ran; let the full helper create the customer + profile + providers.
                _ensure_shopify_sales_setup(store)
            else:
                # Profile already exists. Run only the provider bootstrap step.
                clearing = store.default_posting_profile.control_account
                _bootstrap_shopify_settlement_providers(
                    company=company,
                    clearing_account=clearing,
                    fallback_profile=store.default_posting_profile,
                )

            after = SettlementProvider.objects.filter(
                company=company,
                external_system="shopify",
            ).count()
            self.stdout.write(self.style.SUCCESS(f"    -> now {after} row(s) ({after - existing} added)"))

            if cod_provider_code:
                # Forward-compat: --cod-provider is parsed but inert until A12
                # adds ShopifyStore.default_cod_settlement_provider FK. Verify
                # the named provider exists for this company so the deploy
                # operator catches typos before A12 lands.
                exists = SettlementProvider.objects.filter(
                    company=company,
                    external_system="shopify",
                    normalized_code=cod_provider_code,
                ).exists()
                if exists:
                    self.stdout.write(
                        f"    note: --cod-provider {cod_provider_code} verified for {company.name}; "
                        "will be applied when A12 ships the FK."
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f"    warn: --cod-provider {cod_provider_code} does NOT exist as a "
                            f"SettlementProvider for {company.name}. Bootstrap may need extending "
                            "before this can be assigned."
                        )
                    )

        if dry_run:
            self.stdout.write(self.style.NOTICE("Dry-run — no writes made."))
        else:
            self.stdout.write(self.style.SUCCESS("Backfill complete."))
