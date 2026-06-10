# shopify_connector/management/commands/shopify_graphql_ping.py
"""
Live-validate every ShopifyAdminClient GraphQL query against a connected store.

Run this right after deploying the GraphQL migration (and after any Shopify
API version bump) to prove the schema fields we query still exist — schema
drift surfaces here as a [FAIL] with Shopify's own error message instead of
as a broken sync in front of a merchant or App Store reviewer.

Usage:
    python manage.py shopify_graphql_ping --shop-domain nxentra-sync-6.myshopify.com
    python manage.py shopify_graphql_ping --company-slug shopify-r
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.rls import rls_bypass
from shopify_connector.models import ShopifyStore


class Command(BaseCommand):
    help = "Run every ShopifyAdminClient query once against a live store and report [OK]/[FAIL] per query."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--shop-domain", type=str)
        group.add_argument("--company-slug", type=str)

    def handle(self, *args, **options):
        from shopify_connector.commands import _admin_client

        with rls_bypass():
            stores = ShopifyStore.objects.filter(status=ShopifyStore.Status.ACTIVE).select_related("company")
            if options.get("shop_domain"):
                stores = stores.filter(shop_domain=options["shop_domain"])
            else:
                stores = stores.filter(company__slug=options["company_slug"])
            store = stores.first()

        if not store:
            raise CommandError("No ACTIVE ShopifyStore matches that filter.")

        client = _admin_client(store)
        if not client:
            raise CommandError(f"{store.shop_domain}: token expired or revoked — reconnect first.")

        now = timezone.now()
        week_ago = (now - timedelta(days=7)).isoformat()

        failures = 0
        failures += self._check("shop currency", lambda: client.get_shop_currency())
        failures += self._check("locations", lambda: len(client.list_locations()))
        failures += self._check("products page 1", lambda: self._first_page(client))
        failures += self._check(
            "orders last 7d",
            lambda: sum(1 for _ in client.iter_orders(week_ago, now.isoformat())),
        )
        failures += self._check("payouts", lambda: self._payouts(client))

        if failures:
            raise CommandError(f"{failures} GraphQL check(s) failed for {store.shop_domain}.")
        self.stdout.write(self.style.SUCCESS(f"All GraphQL queries OK against {store.shop_domain}."))

    @staticmethod
    def _first_page(client):
        products, cost_map = next(client.iter_product_pages(), ([], {}))
        return f"{len(products)} products, {len(cost_map)} costs"

    @staticmethod
    def _payouts(client):
        payouts = client.list_payouts(status="paid", limit=5)
        if payouts is None:
            return "no Shopify Payments account (expected on dev stores)"
        return f"{len(payouts)} paid payouts"

    def _check(self, label, fn) -> int:
        try:
            result = fn()
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"[FAIL] {label}: {exc}"))
            return 1
        self.stdout.write(self.style.SUCCESS(f"[OK]   {label}: {result}"))
        return 0
