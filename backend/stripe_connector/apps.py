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
        from accounts.module_registry import module_registry, ModuleCategory

        module_registry.register(
            "stripe_connector",
            label="Stripe",
            icon="CreditCard",
            category=ModuleCategory.VERTICAL,
            order=76,
            nav_items=[
                {"label": "Dashboard", "href": "/stripe", "icon": "LayoutDashboard"},
                {"label": "Charges", "href": "/stripe/charges", "icon": "Receipt"},
                {"label": "Payouts", "href": "/stripe/payouts", "icon": "Banknote"},
                {"label": "Payout Verification", "href": "/stripe/reconciliation", "icon": "Scale"},
                {"label": "Settings", "href": "/stripe/settings", "icon": "Settings"},
            ],
        )

        from platform_connectors.registry import connector_registry
        from stripe_connector.connector import StripeConnector
        connector_registry.register(StripeConnector())
