# bank_connector/apps.py
from django.apps import AppConfig


class BankConnectorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bank_connector"
    verbose_name = "Bank Connector"

    def ready(self):
        from accounts.module_registry import module_registry, ModuleCategory

        module_registry.register(
            "bank_connector",
            label="Banking",
            icon="Landmark",
            category=ModuleCategory.VERTICAL,
            order=78,
            nav_items=[
                {"label": "Reconciliation", "href": "/banking/reconciliation", "icon": "Zap"},
                {"label": "Exceptions", "href": "/banking/exceptions", "icon": "AlertTriangle"},
                {"label": "Accounts", "href": "/banking/accounts", "icon": "Building2"},
                {"label": "Transactions", "href": "/banking/transactions", "icon": "ArrowLeftRight"},
                {"label": "Import", "href": "/banking/import", "icon": "Upload"},
            ],
        )
