# shopify_connector/apps.py
from django.apps import AppConfig


class ShopifyConnectorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shopify_connector"
    verbose_name = "Shopify Connector"

    projections = [
        "shopify_connector.projections.ShopifyAccountingHandler",
    ]

    event_types_module = "shopify_connector.event_types"

    account_roles = [
        "SALES_REVENUE",
        "SHOPIFY_CLEARING",
        "PAYMENT_PROCESSING_FEES",
        "SALES_TAX_PAYABLE",
        "SHIPPING_REVENUE",
        "CASH_BANK",
        "COGS",
        "INVENTORY",
        "CHARGEBACK_EXPENSE",
        # A14: settlement-import support.
        "EXPECTED_BANK_DEPOSIT",
        "SALES_RETURNS",
    ]

    def ready(self):
        from accounts.module_registry import ModuleCategory, SidebarTab, module_registry
        from platform_connectors.registry import connector_registry

        from .connector import ShopifyConnector

        connector_registry.register(ShopifyConnector())

        # A5-PR3b (Codex round-10): the ADAPTER registers how to resolve which
        # order ids exist locally, so the core orphan-flag writer never imports
        # provider code (AGENTS.md dependency direction — adapter depends on
        # core, never the reverse).
        from accounting.import_rejects import register_known_order_lookup

        def _known_shopify_order_ids(company, digit_order_ids):
            from shopify_connector.models import ShopifyOrder

            return {
                str(oid)
                for oid in ShopifyOrder.objects.filter(
                    company=company, shopify_order_id__in=digit_order_ids
                ).values_list("shopify_order_id", flat=True)
            }

        register_known_order_lookup("shopify", _known_shopify_order_ids)

        # A5-PR4a: the ADAPTER registers its pilot-adjustment source
        # resolvers (shopify_order / shopify_refund / shopify_reject) into
        # the core registry — same dependency inversion as above; core
        # accounting never imports shopify_connector models.
        from shopify_connector.pilot_adjustment_sources import register_pilot_adjustment_sources

        register_pilot_adjustment_sources()

        # A5-PR2b: the ADAPTER registers how to count its open rejected source
        # evidence, so the core health fold (/_health/alerts) never imports
        # provider code — the same dependency inversion as above.
        from ops.health import register_rejected_evidence_counter

        def _open_shopify_rejected_evidence() -> int:
            from shopify_connector.models import ShopifyRejectedEvidence

            return ShopifyRejectedEvidence.objects.filter(
                acknowledged=False,
                superseded_at__isnull=True,
            ).count()

        register_rejected_evidence_counter("shopify", _open_shopify_rejected_evidence)

        # A5-PR1a: adapter-registered SOURCE-HEALTH conditions — the supported
        # source becoming unable to deliver or recover data must reach the
        # /_health/alerts pinger (a revoked token or a dead scheduled sync
        # produces NO events, so lag/failure-log counters read all-clear while
        # ingestion is halted). Scoped to constrained-pilot companies
        # (ISOLATED_SHADOW_LEDGER_V1) and ACTIVE stores; aggregate counts only,
        # no tenant identity. Same dependency inversion as the counters above:
        # core ops never imports shopify_connector.
        from ops.health import register_source_health_counter

        def _pilot_shopify_reauth_required() -> int:
            from accounts.models import Company
            from shopify_connector.models import ShopifyStore

            return ShopifyStore.objects.filter(
                status=ShopifyStore.Status.ACTIVE,
                company__pilot_profile=Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1,
                needs_reauth=True,
            ).count()

        def _pilot_shopify_stale_sources() -> int:
            # Stale = the scheduled sync has not completed inside the
            # threshold. SHOPIFY_SOURCE_STALE_SECONDS — Django setting first,
            # env fallback; default 28800s (8h) = two consecutive missed runs
            # of the 4h sync cadence plus margin, so one late run never pages.
            # A never-synced newly connected store gets the same grace period
            # from created_at instead of being declared stale immediately.
            import os
            from datetime import timedelta

            from django.conf import settings
            from django.db.models import Q
            from django.utils import timezone

            from accounts.models import Company
            from shopify_connector.models import ShopifyStore

            threshold = getattr(settings, "SHOPIFY_SOURCE_STALE_SECONDS", None)
            if threshold is None:
                threshold = os.getenv("SHOPIFY_SOURCE_STALE_SECONDS", "28800")
            cutoff = timezone.now() - timedelta(seconds=int(threshold))
            return (
                ShopifyStore.objects.filter(
                    status=ShopifyStore.Status.ACTIVE,
                    company__pilot_profile=Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1,
                )
                .filter(Q(last_sync_at__lt=cutoff) | Q(last_sync_at__isnull=True, created_at__lt=cutoff))
                .count()
            )

        register_source_health_counter("shopify_reauth_required", _pilot_shopify_reauth_required)
        register_source_health_counter("shopify_stale_sources", _pilot_shopify_stale_sources)

        module_registry.register(
            "shopify_connector",
            label="Shopify",
            icon="ShoppingCart",
            category=ModuleCategory.VERTICAL,
            order=75,
        )

        module_registry.register_sidebar(
            "work_shopify",
            label="Shopify",
            icon="ShoppingCart",
            tab=SidebarTab.WORK,
            order=5,  # Above Finance (10) — primary nav for Shopify merchants
            module_key="shopify_connector",
            nav_items=[
                # "Reconciliation" used to live here (legacy three-column
                # payout-centric view). A13 supersedes it with the
                # provider-agnostic Finance -> Reconciliation Control
                # Center; the legacy page stays accessible at
                # /shopify/reconciliation but is no longer in the sidebar
                # to avoid confusion with the new master view.
                {"label": "Orders", "href": "/shopify/orders", "icon": "ShoppingBag"},
                {"label": "Payouts", "href": "/shopify/payouts", "icon": "Banknote"},
                {"label": "Dashboard", "href": "/shopify", "icon": "LayoutDashboard"},
            ],
        )

        module_registry.register_sidebar(
            "setup_shopify",
            label="Shopify",
            icon="ShoppingCart",
            tab=SidebarTab.SETUP,
            order=35,
            module_key="shopify_connector",
            nav_items=[
                {"label": "Settings", "href": "/shopify/settings", "icon": "Settings"},
            ],
        )
