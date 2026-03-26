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

    def ready(self):
        from accounts.module_registry import module_registry, ModuleCategory

        module_registry.register(
            "inventory",
            label="Inventory",
            icon="Warehouse",
            category=ModuleCategory.HORIZONTAL,
            order=50,
            nav_items=[
                {"label": "Warehouses", "href": "/inventory/warehouses", "icon": "Warehouse", "translation_key": "nav.warehouses"},
                {"label": "Items", "href": "/accounting/items", "icon": "Package", "translation_key": "nav.items"},
                {"label": "Stock Balances", "href": "/inventory/balances", "icon": "PackageOpen", "translation_key": "nav.inventoryBalances"},
                {"label": "Stock Ledger", "href": "/inventory/ledger", "icon": "ScrollText", "translation_key": "nav.stockLedger"},
                {"label": "Adjustment", "href": "/inventory/adjustments/new", "icon": "Scale", "translation_key": "nav.inventoryAdjustment"},
                {"label": "Opening Balance", "href": "/inventory/opening-balance", "icon": "PackagePlus", "translation_key": "nav.openingBalance"},
            ],
        )
