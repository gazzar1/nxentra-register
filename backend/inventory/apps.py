# inventory/apps.py
from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventory"
    verbose_name = "Inventory"

    # Declarative vertical-module manifest.
    # ProjectionsConfig.ready() auto-discovers and registers these.
    projections = [
        "projections.inventory_balance.InventoryBalanceProjection",
    ]
