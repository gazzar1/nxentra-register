# backups/apps.py
from django.apps import AppConfig


class BackupsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "backups"
    verbose_name = "Company Backup & Restore"

    def ready(self):
        from accounts.module_registry import module_registry, ModuleCategory

        module_registry.register(
            "backups",
            label="Backup & Restore",
            icon="Database",
            category=ModuleCategory.CORE,
            order=95,
            nav_items=[
                {
                    "label": "Backups",
                    "href": "/settings/backups",
                    "icon": "Database",
                    "translation_key": "nav.backups",
                },
            ],
        )
