# accounts/apps.py
"""Accounts app configuration."""

from django.apps import AppConfig
from django.conf import settings
from django.db.backends.signals import connection_created


class AccountsConfig(AppConfig):
    """Configuration for the accounts app."""
    
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Accounts & Multi-tenancy"
    
    def ready(self):
        """Initialize app when Django starts."""
        from accounts import rls
        from accounts.module_registry import module_registry, ModuleCategory

        def _on_connection_created(sender, connection, **kwargs):
            if settings.RLS_BYPASS:
                rls.set_rls_bypass(True, conn=connection)

        connection_created.connect(
            _on_connection_created,
            dispatch_uid="accounts.rls_init",
        )

        # Register core platform navigation sections.
        # These are always enabled for every tenant.
        module_registry.register(
            "dashboard",
            label="Dashboard",
            icon="LayoutDashboard",
            category=ModuleCategory.CORE,
            order=0,
            nav_items=[
                {"label": "Dashboard", "href": "/dashboard", "icon": "LayoutDashboard", "translation_key": "nav.dashboard"},
            ],
        )
        module_registry.register(
            "setup",
            label="Setup",
            icon="Wrench",
            category=ModuleCategory.CORE,
            order=10,
            nav_items=[
                {"label": "Periods", "href": "/settings/periods", "icon": "Calendar", "translation_key": "nav.periods"},
                {"label": "Chart of Accounts", "href": "/accounting/chart-of-accounts", "icon": "FileText", "translation_key": "nav.chartOfAccounts"},
                {"label": "Dimensions", "href": "/settings/dimensions", "icon": "Layers", "translation_key": "nav.dimensions"},
                {"label": "Vendors (AP)", "href": "/accounting/vendors", "icon": "Truck", "translation_key": "nav.vendors"},
                {"label": "Customers (AR)", "href": "/accounting/customers", "icon": "UserCircle", "translation_key": "nav.customers"},
                {"label": "Warehouses", "href": "/inventory/warehouses", "icon": "Warehouse", "translation_key": "nav.warehouses"},
                {"label": "Items", "href": "/accounting/items", "icon": "Package", "translation_key": "nav.items"},
                {"label": "Tax Codes", "href": "/accounting/tax-codes", "icon": "Percent", "translation_key": "nav.taxCodes"},
                {"label": "Posting Profiles", "href": "/accounting/posting-profiles", "icon": "CreditCard", "translation_key": "nav.postingProfiles"},
                {"label": "Integrations", "href": "/settings/integrations", "icon": "Plug", "translation_key": "nav.integrations"},
            ],
        )
        module_registry.register(
            "accounting",
            label="Accounting",
            icon="BookOpen",
            category=ModuleCategory.CORE,
            order=20,
            nav_items=[
                {"label": "Journal Entries", "href": "/accounting/journal-entries", "icon": "FileText", "translation_key": "nav.journalEntries"},
                {"label": "Bank Reconciliation", "href": "/accounting/bank-reconciliation", "icon": "Building2"},
                {"label": "Scratchpad", "href": "/accounting/scratchpad", "icon": "ClipboardList", "translation_key": "nav.scratchpad"},
                {"label": "Import Data", "href": "/accounting/import", "icon": "Upload", "translation_key": "nav.import"},
            ],
        )
        module_registry.register(
            "reports",
            label="Reports",
            icon="BarChart3",
            category=ModuleCategory.CORE,
            order=80,
            nav_items=[
                {"label": "Trial Balance", "href": "/reports/trial-balance", "icon": "BarChart3", "translation_key": "nav.trialBalance"},
                {"label": "Balance Sheet", "href": "/reports/balance-sheet", "icon": "BarChart3", "translation_key": "nav.balanceSheet"},
                {"label": "Income Statement", "href": "/reports/income-statement", "icon": "BarChart3", "translation_key": "nav.incomeStatement"},
                {"label": "Cash Flow", "href": "/reports/cash-flow", "icon": "BarChart3", "translation_key": "nav.cashFlowStatement"},
                {"label": "Account Inquiry", "href": "/reports/account-inquiry", "icon": "FileText", "translation_key": "nav.accountInquiry"},
                {"label": "AR Aging", "href": "/reports/ar-aging", "icon": "Clock"},
                {"label": "AP Aging", "href": "/reports/ap-aging", "icon": "Clock"},
                {"label": "Customer Balances", "href": "/reports/customer-balances", "icon": "UserCircle", "translation_key": "nav.customerBalances"},
                {"label": "Vendor Balances", "href": "/reports/vendor-balances", "icon": "Truck", "translation_key": "nav.vendorBalances"},
                {"label": "Customer Statement", "href": "/reports/customer-statement", "icon": "UserCircle", "translation_key": "nav.customerStatement"},
                {"label": "Vendor Statement", "href": "/reports/vendor-statement", "icon": "Truck", "translation_key": "nav.vendorStatement"},
                {"label": "Dimension Analysis", "href": "/reports/dimension-analysis", "icon": "Layers"},
                {"label": "Dimension Cross-Tab", "href": "/reports/dimension-crosstab", "icon": "Layers"},
                {"label": "P&L Comparison", "href": "/reports/dimension-pl-comparison", "icon": "Layers"},
            ],
        )
        module_registry.register(
            "settings",
            label="Settings",
            icon="Settings",
            category=ModuleCategory.CORE,
            order=90,
            nav_items=[
                {"label": "Company Settings", "href": "/settings/company", "icon": "Building2", "translation_key": "nav.companySettings"},
                {"label": "Modules", "href": "/settings/modules", "icon": "LayoutGrid", "translation_key": "nav.modules"},
                {"label": "Users", "href": "/users", "icon": "Users", "translation_key": "nav.users"},
                {"label": "Account", "href": "/settings/account", "icon": "KeyRound", "translation_key": "nav.account"},
                {"label": "Event Audit", "href": "/settings/audit", "icon": "ShieldCheck", "translation_key": "nav.audit"},
                {"label": "Exchange Rates", "href": "/settings/exchange-rates", "icon": "ArrowLeftRight"},
            ],
        )
