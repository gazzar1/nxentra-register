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
