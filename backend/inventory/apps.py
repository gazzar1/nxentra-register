# inventory/apps.py
from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventory"
    verbose_name = "Inventory Management"

    projections = [
        "projections.inventory_balance.InventoryBalanceProjection",
    ]

    def ready(self):
        from accounts.module_registry import ModuleCategory, SidebarTab, module_registry

        module_registry.register(
            "inventory",
            label="Inventory",
            icon="Warehouse",
            category=ModuleCategory.HORIZONTAL,
            order=50,
        )

        module_registry.register_sidebar(
            "work_inventory",
            label="Inventory",
            icon="Boxes",
            tab=SidebarTab.WORK,
            order=40,
            module_key="inventory",
            nav_items=[
                {"label": "Stock Adjustments", "href": "/inventory/adjustments/new", "icon": "Scale"},
            ],
        )

        module_registry.register_sidebar(
            "review_inventory",
            label="Inventory",
            icon="Boxes",
            tab=SidebarTab.REVIEW,
            order=35,
            module_key="inventory",
            nav_items=[
                {"label": "Stock Balances", "href": "/inventory/balances", "icon": "PackageOpen"},
                {"label": "Stock Ledger", "href": "/inventory/ledger", "icon": "ScrollText"},
            ],
        )

        module_registry.register_sidebar(
            "setup_inventory",
            label="Inventory",
            icon="Warehouse",
            tab=SidebarTab.SETUP,
            order=50,
            module_key="inventory",
            nav_items=[
                {"label": "Warehouses", "href": "/inventory/warehouses", "icon": "Warehouse"},
            ],
        )
