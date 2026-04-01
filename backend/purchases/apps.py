# purchases/apps.py
from django.apps import AppConfig


class PurchasesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "purchases"
    verbose_name = "Purchases & Bills"

    def ready(self):
        from accounts.module_registry import ModuleCategory, module_registry

        module_registry.register(
            "purchases",
            label="Purchases",
            icon="Truck",
            category=ModuleCategory.HORIZONTAL,
            order=40,
            nav_items=[
                {"label": "Purchase Orders", "href": "/accounting/purchase-orders", "icon": "ClipboardList", "translation_key": "nav.purchaseOrders"},
                {"label": "Goods Receipts", "href": "/accounting/goods-receipts", "icon": "PackageCheck", "translation_key": "nav.goodsReceipts"},
                {"label": "Bills", "href": "/accounting/purchase-bills", "icon": "Receipt", "translation_key": "nav.purchaseBills"},
                {"label": "Payments", "href": "/accounting/payments", "icon": "CreditCard", "translation_key": "nav.vendorPayments"},
            ],
        )
