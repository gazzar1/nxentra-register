# bank_connector/apps.py
from django.apps import AppConfig


class BankConnectorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bank_connector"
    verbose_name = "Bank Connector"

    def ready(self):
        from accounts.module_registry import ModuleCategory, SidebarTab, module_registry

        module_registry.register(
            "bank_connector",
            label="Banking",
            icon="Landmark",
            category=ModuleCategory.VERTICAL,
            order=78,
        )

        module_registry.register_sidebar(
            "setup_banking",
            label="Banking",
            icon="Landmark",
            tab=SidebarTab.SETUP,
            order=37,
            module_key="bank_connector",
            nav_items=[
                {"label": "Accounts", "href": "/banking/accounts", "icon": "Building2"},
                {"label": "Import", "href": "/banking/import", "icon": "Upload"},
            ],
        )
