# stripe_connector/apps.py
from django.apps import AppConfig


class StripeConnectorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "stripe_connector"
    verbose_name = "Stripe Connector"

    account_roles = [
        "SALES_REVENUE",
        "STRIPE_CLEARING",
        "PAYMENT_PROCESSING_FEES",
        "SALES_TAX_PAYABLE",
        "CASH_BANK",
        "CHARGEBACK_EXPENSE",
    ]

    def ready(self):
        from accounts.module_registry import ModuleCategory, SidebarTab, module_registry
        from platform_connectors.registry import connector_registry

        from .connector import StripeConnector

        connector_registry.register(StripeConnector())

        module_registry.register(
            "stripe_connector",
            label="Stripe",
            icon="CreditCard",
            category=ModuleCategory.VERTICAL,
            order=76,
        )

        module_registry.register_sidebar(
            "work_stripe",
            label="Stripe",
            icon="CreditCard",
            tab=SidebarTab.WORK,
            order=76,
            module_key="stripe_connector",
            nav_items=[
                {"label": "Dashboard", "href": "/stripe", "icon": "LayoutDashboard"},
                {"label": "Charges", "href": "/stripe/charges", "icon": "Receipt"},
                {"label": "Payouts", "href": "/stripe/payouts", "icon": "Banknote"},
                {"label": "Payout Verification", "href": "/stripe/reconciliation", "icon": "Scale"},
            ],
        )

        module_registry.register_sidebar(
            "setup_stripe",
            label="Stripe",
            icon="CreditCard",
            tab=SidebarTab.SETUP,
            order=36,
            module_key="stripe_connector",
            nav_items=[
                {"label": "Settings", "href": "/stripe/settings", "icon": "Settings"},
            ],
        )
