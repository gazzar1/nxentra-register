# inventory/apps.py
from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventory"
    verbose_name = "Inventory"

    def ready(self):
        # Import projections to register them
        try:
            from projections import inventory_balance  # noqa: F401
        except ImportError:
            pass
