# shopify_connector/management/commands/resync_shopify_orders.py
"""
Management command for manual Shopify order re-sync.

Catches up missed orders by polling the Shopify Orders API for a given
date range. Existing orders are skipped (idempotent).

Every covered Shopify command takes a fresh ``Company`` admission lock (PR #119),
whose ``Company`` query is hidden by production RLS unless the tenant's RLS
session context is set. A management command has no request/middleware, so — like
the scheduled Celery tasks — this command routes its per-store work through the
single private tenant/RLS execution path ``tasks._execute_scheduled_store_sync``
(two-plane tenant context + non-writable skip). Cross-tenant store discovery runs
under a short ``rls_bypass()`` and keeps store IDENTITIES only; the authoritative
``ShopifyStore`` is refetched inside the tenant context.

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
            "--company",
            type=str,
            default="",
            help="Company slug to sync (default: all active companies)",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Number of days to look back (default: 7). Ignored if --from is set.",
        )
        parser.add_argument(
            "--from",
            dest="from_date",
            type=str,
            default="",
            help="Start date (ISO format, e.g. 2026-03-01)",
        )
        parser.add_argument(
            "--to",
            dest="to_date",
            type=str,
            default="",
            help="End date (ISO format, e.g. 2026-03-31). Default: now.",
        )
        parser.add_argument(
            "--include-payouts",
            action="store_true",
            help="Also sync payouts (default: orders only)",
        )
        parser.add_argument(
            "--include-products",
            action="store_true",
            help="Also sync products (default: orders only)",
        )

    def handle(self, *args, **options):
        from shopify_connector.models import ShopifyStore
        from shopify_connector.tasks import _execute_scheduled_store_sync

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

        include_payouts = options["include_payouts"]
        include_products = options["include_products"]

        self.stdout.write(f"Re-syncing orders from {created_at_min} to {created_at_max}")

        # Cross-tenant discovery: a short bypass on the system/default DB. Keep
        # IDENTITIES only (id / company_id / domain / name) — never the
        # discovery-loaded Store as authoritative; the per-tenant runner refetches
        # the ShopifyStore on the control plane inside its RLS context.
        with rls_bypass():
            stores_qs = ShopifyStore.objects.filter(status=ShopifyStore.Status.ACTIVE)
            if options["company"]:
                stores_qs = stores_qs.filter(company__slug=options["company"])
            discovered = list(stores_qs.values_list("id", "company_id", "shop_domain", "company__name"))

        if not discovered:
            self.stdout.write(self.style.WARNING("No active Shopify stores found."))
            return

        self.stdout.write(f"Found {len(discovered)} active store(s)")

        for store_id, company_id, shop_domain, company_name in discovered:
            self.stdout.write(f"\n--- {shop_domain} ({company_name}) ---")
            # Per-tenant execution: tenant routing + RLS session context (bypass OFF
            # for shared tenants in production), non-writable tenants skipped,
            # refetched store, command run, guaranteed cleanup.
            result = _execute_scheduled_store_sync(
                store_id,
                company_id,
                lambda store: self._resync_one(
                    store, created_at_min, created_at_max, include_payouts, include_products
                ),
            )
            self._report(result)

        self.stdout.write(self.style.SUCCESS("\nRe-sync complete."))

    def _resync_one(self, store, created_at_min, created_at_max, include_payouts, include_products):
        """Per-tenant re-sync body — runs INSIDE ``_shopify_tenant_execution`` (via
        ``_execute_scheduled_store_sync``) so each covered command's fresh Company
        admission lock is visible under production RLS.

        Mirrors ``tasks._sync_store``: it re-asserts the tenant RLS session before
        every covered command, because ``events.emitter`` clears the connection's
        RLS session after each emit — without the re-assert the NEXT admission lock
        would be hidden by RLS (``Company.DoesNotExist``). Kept faithful to the
        original manual scope: orders, plus payouts / products only when requested.
        """
        from shopify_connector.commands import sync_payouts, sync_products
        from shopify_connector.tasks import _reassert_shopify_rls, _sync_orders

        out = {}

        _reassert_shopify_rls()
        out["orders"] = _sync_orders(store, created_at_min, created_at_max)

        if include_payouts:
            _reassert_shopify_rls()
            payout_result = sync_payouts(store)
            if payout_result.success:
                out["payouts"] = payout_result.data or {"status": "ok"}
            else:
                out["payouts"] = {"status": "error", "error": payout_result.error}

        if include_products:
            _reassert_shopify_rls()
            product_result = sync_products(store)
            if product_result.success:
                out["products"] = product_result.data or {"status": "ok"}
            else:
                out["products"] = {"status": "error", "error": product_result.error}

        return out

    def _report(self, result):
        # Non-writable (migrating / read-only / suspended) tenants are skipped by
        # the shared execution path — report that honestly rather than silently.
        if result.get("status") == "skipped":
            self.stdout.write(self.style.WARNING(f"  Skipped: {result.get('reason', 'tenant not writable')}"))
            return

        orders = result.get("orders", {})
        self.stdout.write(
            f"  Orders: fetched={orders.get('fetched', 0)}, "
            f"created={orders.get('created', 0)}, "
            f"skipped={orders.get('skipped', 0)}, "
            f"errors={orders.get('errors', 0)}"
        )
        if orders.get("error"):
            self.stdout.write(self.style.ERROR(f"  Error: {orders['error']}"))

        if "payouts" in result:
            payouts = result["payouts"]
            if payouts.get("status") == "error":
                self.stdout.write(self.style.ERROR(f"  Payout error: {payouts.get('error')}"))
            else:
                self.stdout.write(
                    f"  Payouts: created={payouts.get('created', 0)}, skipped={payouts.get('skipped', 0)}"
                )

        if "products" in result:
            products = result["products"]
            if products.get("status") == "error":
                self.stdout.write(self.style.ERROR(f"  Product error: {products.get('error')}"))
            else:
                self.stdout.write(
                    f"  Products: created={products.get('created', 0)}, "
                    f"linked={products.get('linked', 0)}, "
                    f"updated={products.get('updated', 0)}"
                )
