# shopify_connector/apps.py
from django.apps import AppConfig


class ShopifyConnectorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shopify_connector"
    verbose_name = "Shopify Connector"

    # Projection that converts Shopify events → journal entries
    projections = [
        "shopify_connector.projections.ShopifyAccountingProjection",
    ]

    event_types_module = "shopify_connector.event_types"

    # GL account roles this module requires
    account_roles = [
        "SALES_REVENUE",
        "ACCOUNTS_RECEIVABLE",
        "PAYMENT_PROCESSING_FEES",
        "SALES_TAX_PAYABLE",
        "SHIPPING_REVENUE",
        "SALES_DISCOUNTS",
        "CASH_BANK",
    ]

    def ready(self):
        from accounts.module_registry import module_registry, ModuleCategory

        module_registry.register(
            "shopify_connector",
            label="Shopify",
            icon="ShoppingCart",
            category=ModuleCategory.VERTICAL,
            order=75,
            nav_items=[
                {"label": "Dashboard", "href": "/shopify", "icon": "LayoutDashboard"},
                {"label": "Orders", "href": "/shopify/orders", "icon": "ShoppingBag"},
                {"label": "Settings", "href": "/shopify/settings", "icon": "Settings"},
            ],
        )
