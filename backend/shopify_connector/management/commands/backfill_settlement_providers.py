"""
Backfill SettlementProvider rows for existing Shopify stores.

For each ACTIVE ShopifyStore, run the bootstrap which is idempotent:
- Creates the SETTLEMENT_PROVIDER AnalysisDimension + values per provider
- Creates per-provider PostingProfile + SettlementProvider rows (paymob,
  paypal, shopify_payments, manual, bank_transfer, bosta, unknown).
  Deactivates the deprecated cash_on_delivery row from A2.
- Populates SettlementProvider.dimension_value FK on existing rows.

Optional `--cod-provider <code>` flag sets each store's
default_cod_settlement_provider FK to the SettlementProvider with the
matching normalized_code (e.g. `--cod-provider bosta`). Validates the
provider exists for each company before assignment. Skipped on dry-run.

Run on the droplet:
    python manage.py backfill_settlement_providers
    python manage.py backfill_settlement_providers --dry-run
    python manage.py backfill_settlement_providers --cod-provider bosta
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
                "Normalized SettlementProvider code to use as each store's "
                "default COD courier (e.g. 'bosta', 'aramex'). Sets "
                "ShopifyStore.default_cod_settlement_provider FK if a "
                "matching SettlementProvider row exists for the company."
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
                # Look up the SettlementProvider row for this company and
                # set ShopifyStore.default_cod_settlement_provider. Skipped
                # on dry-run.
                from projections.write_barrier import command_writes_allowed

                target = SettlementProvider.objects.filter(
                    company=company,
                    external_system="shopify",
                    normalized_code=cod_provider_code,
                ).first()
                if not target:
                    self.stdout.write(
                        self.style.WARNING(
                            f"    warn: --cod-provider {cod_provider_code} does NOT exist as a "
                            f"SettlementProvider for {company.name}. Skipping FK assignment "
                            "for this store."
                        )
                    )
                else:
                    if store.default_cod_settlement_provider_id == target.id:
                        self.stdout.write(f"    cod provider already set to {target.display_name}")
                    else:
                        with command_writes_allowed():
                            store.default_cod_settlement_provider = target
                            store.save(
                                update_fields=[
                                    "default_cod_settlement_provider",
                                    "updated_at",
                                ]
                            )
                        self.stdout.write(
                            self.style.SUCCESS(f"    -> default_cod_settlement_provider = {target.display_name}")
                        )

        if dry_run:
            self.stdout.write(self.style.NOTICE("Dry-run — no writes made."))
        else:
            self.stdout.write(self.style.SUCCESS("Backfill complete."))
