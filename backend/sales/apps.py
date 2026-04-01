# sales/apps.py
from django.apps import AppConfig


class SalesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sales"
    verbose_name = "Sales & Invoicing"

    def ready(self):
        from accounts.module_registry import ModuleCategory, SidebarTab, module_registry

        # Module registration (for enablement)
        module_registry.register(
            "sales",
            label="Sales",
            icon="ShoppingCart",
            category=ModuleCategory.HORIZONTAL,
            order=30,
        )

        # WORK tab — Sales operations
        module_registry.register_sidebar(
            "work_sales",
            label="Sales",
            icon="Receipt",
            tab=SidebarTab.WORK,
            order=20,
            module_key="sales",
            nav_items=[
                {"label": "Invoices", "href": "/accounting/sales-invoices", "icon": "Receipt"},
                {"label": "Credit Notes", "href": "/accounting/credit-notes", "icon": "ReceiptText"},
                {"label": "Customer Receipts", "href": "/accounting/receipts", "icon": "CreditCard"},
            ],
        )
