# backups/apps.py
from django.apps import AppConfig


class BackupsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "backups"
    verbose_name = "Backup & Restore"

    def ready(self):
        from accounts.module_registry import ModuleCategory, SidebarTab, module_registry

        module_registry.register(
            "backups",
            label="Backup & Restore",
            icon="Database",
            category=ModuleCategory.CORE,
            order=95,
        )

        module_registry.register_sidebar(
            "setup_backups",
            label="Backup & Restore",
            icon="Database",
            tab=SidebarTab.SETUP,
            order=95,
            nav_items=[
                {"label": "Backups", "href": "/settings/backups", "icon": "Database"},
            ],
        )
