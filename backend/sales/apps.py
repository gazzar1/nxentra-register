# sales/apps.py
from django.apps import AppConfig


class SalesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sales"
    verbose_name = "Sales & Invoicing"

    def ready(self):
        from accounts.module_registry import module_registry, ModuleCategory

        module_registry.register(
            "sales",
            label="Sales",
            icon="ShoppingCart",
            category=ModuleCategory.HORIZONTAL,
            order=30,
            nav_items=[
                {"label": "Invoices", "href": "/accounting/sales-invoices", "icon": "Receipt", "translation_key": "nav.salesInvoices"},
                {"label": "Receipts", "href": "/accounting/receipts", "icon": "CreditCard", "translation_key": "nav.customerReceipts"},
            ],
        )
