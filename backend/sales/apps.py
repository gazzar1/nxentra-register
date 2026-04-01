# sales/apps.py
from django.apps import AppConfig


class SalesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sales"
    verbose_name = "Sales & Invoicing"

    def ready(self):
        from accounts.module_registry import ModuleCategory, module_registry

        module_registry.register(
            "sales",
            label="Sales",
            icon="ShoppingCart",
            category=ModuleCategory.HORIZONTAL,
            order=30,
            nav_items=[
                {"label": "Tax Codes", "href": "/accounting/tax-codes", "icon": "Percent", "translation_key": "nav.taxCodes"},
                {"label": "Posting Profiles", "href": "/accounting/posting-profiles", "icon": "CreditCard", "translation_key": "nav.postingProfiles"},
                {"label": "Invoices", "href": "/accounting/sales-invoices", "icon": "Receipt", "translation_key": "nav.salesInvoices"},
                {"label": "Credit Notes", "href": "/accounting/credit-notes", "icon": "ReceiptText", "translation_key": "nav.creditNotes"},
                {"label": "Receipts", "href": "/accounting/receipts", "icon": "CreditCard", "translation_key": "nav.customerReceipts"},
            ],
        )
