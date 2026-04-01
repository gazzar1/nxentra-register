# purchases/apps.py
from django.apps import AppConfig


class PurchasesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "purchases"
    verbose_name = "Purchases & Bills"

    def ready(self):
        from accounts.module_registry import ModuleCategory, SidebarTab, module_registry

        # Module registration (for enablement)
        module_registry.register(
            "purchases",
            label="Purchases",
            icon="Truck",
            category=ModuleCategory.HORIZONTAL,
            order=40,
        )

        # WORK tab — Purchases operations
        module_registry.register_sidebar(
            "work_purchases",
            label="Purchases",
            icon="ShoppingBag",
            tab=SidebarTab.WORK,
            order=30,
            module_key="purchases",
            nav_items=[
                {"label": "Purchase Orders", "href": "/accounting/purchase-orders", "icon": "ClipboardList"},
                {"label": "Goods Receipts", "href": "/accounting/goods-receipts", "icon": "PackageCheck"},
                {"label": "Vendor Bills", "href": "/accounting/purchase-bills", "icon": "Receipt"},
                {"label": "Vendor Payments", "href": "/accounting/payments", "icon": "CreditCard"},
            ],
        )
