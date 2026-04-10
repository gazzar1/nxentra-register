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
    ]

    def ready(self):
        from accounts.module_registry import ModuleCategory, SidebarTab, module_registry
        from platform_connectors.registry import connector_registry

        from .connector import ShopifyConnector

        connector_registry.register(ShopifyConnector())

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
                {"label": "Reconciliation", "href": "/shopify/reconciliation", "icon": "Scale"},
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
