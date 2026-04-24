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
        from accounts.module_registry import ModuleCategory, SidebarTab, module_registry

        def _on_connection_created(sender, connection, **kwargs):
            if settings.RLS_BYPASS:
                rls.set_rls_bypass(True, conn=connection)

        connection_created.connect(
            _on_connection_created,
            dispatch_uid="accounts.rls_init",
        )

        # =====================================================================
        # Module registrations (for enablement tracking — keep existing)
        # =====================================================================
        module_registry.register(
            "dashboard", label="Dashboard", icon="LayoutDashboard", category=ModuleCategory.CORE, order=0
        )
        module_registry.register(
            "accounting", label="Accounting", icon="BookOpen", category=ModuleCategory.CORE, order=20
        )
        module_registry.register("reports", label="Reports", icon="BarChart3", category=ModuleCategory.CORE, order=80)
        module_registry.register("settings", label="Settings", icon="Settings", category=ModuleCategory.CORE, order=90)

        # =====================================================================
        # WORK tab — daily operations
        # =====================================================================
        module_registry.register_sidebar(
            "work_finance",
            label="Finance",
            icon="Wallet",
            tab=SidebarTab.WORK,
            order=10,
            nav_items=[
                {"label": "Journal Entries", "href": "/accounting/journal-entries", "icon": "FileText"},
                {"label": "Bank Reconciliation", "href": "/accounting/bank-reconciliation", "icon": "Building2"},
                {"label": "Scratchpad", "href": "/accounting/scratchpad", "icon": "ClipboardList"},
            ],
        )

        module_registry.register_sidebar(
            "work_records",
            label="Records",
            icon="Database",
            tab=SidebarTab.WORK,
            order=50,
            nav_items=[
                {"label": "Customers", "href": "/accounting/customers", "icon": "UserCircle"},
                {"label": "Vendors", "href": "/accounting/vendors", "icon": "Truck"},
                {"label": "Items", "href": "/accounting/items", "icon": "Package"},
            ],
        )

        # =====================================================================
        # REVIEW tab — reports, analysis, control
        # =====================================================================
        module_registry.register_sidebar(
            "review_control",
            label="Control",
            icon="ShieldCheck",
            tab=SidebarTab.REVIEW,
            order=10,
            nav_items=[
                {"label": "Month-End Close", "href": "/settings/month-end-close", "icon": "ClipboardCheck"},
                {"label": "System Health", "href": "/settings/system-health", "icon": "Activity"},
                {"label": "Reconciliation Status", "href": "/banking/reconciliation", "icon": "Scale"},
                {"label": "Audit Trail", "href": "/settings/audit", "icon": "ShieldCheck"},
                {"label": "FX Revaluation", "href": "/accounting/currency-revaluation", "icon": "ArrowLeftRight"},
                {"label": "Period Close", "href": "/settings/periods", "icon": "Calendar"},
            ],
        )

        module_registry.register_sidebar(
            "review_statements",
            label="Financial Statements",
            icon="BarChart3",
            tab=SidebarTab.REVIEW,
            order=20,
            nav_items=[
                {"label": "Trial Balance", "href": "/reports/trial-balance", "icon": "BarChart3"},
                {"label": "Income Statement", "href": "/reports/income-statement", "icon": "BarChart3"},
                {"label": "Balance Sheet", "href": "/reports/balance-sheet", "icon": "BarChart3"},
                {"label": "Cash Flow", "href": "/reports/cash-flow", "icon": "BarChart3"},
            ],
        )

        module_registry.register_sidebar(
            "review_receivables_payables",
            label="Receivables & Payables",
            icon="Users",
            tab=SidebarTab.REVIEW,
            order=30,
            nav_items=[
                {"label": "Customer Balances", "href": "/reports/customer-balances", "icon": "UserCircle"},
                {"label": "Customer Statements", "href": "/reports/customer-statement", "icon": "UserCircle"},
                {"label": "AR Aging", "href": "/reports/ar-aging", "icon": "Clock"},
                {"label": "Vendor Balances", "href": "/reports/vendor-balances", "icon": "Truck"},
                {"label": "Vendor Statements", "href": "/reports/vendor-statement", "icon": "Truck"},
                {"label": "AP Aging", "href": "/reports/ap-aging", "icon": "Clock"},
            ],
        )

        module_registry.register_sidebar(
            "review_analysis",
            label="Analysis",
            icon="Search",
            tab=SidebarTab.REVIEW,
            order=40,
            nav_items=[
                {"label": "Account Activity", "href": "/reports/account-inquiry", "icon": "FileText"},
                {"label": "Dimension Analysis", "href": "/reports/dimension-analysis", "icon": "Layers"},
                {"label": "Dimension Cross-Tab", "href": "/reports/dimension-crosstab", "icon": "Layers"},
                {"label": "P&L Comparison", "href": "/reports/dimension-pl-comparison", "icon": "Layers"},
                {"label": "Tax Summary", "href": "/reports/tax-summary", "icon": "Receipt"},
            ],
        )

        # =====================================================================
        # SETUP tab — configuration
        # =====================================================================
        module_registry.register_sidebar(
            "setup_organization",
            label="Organization",
            icon="Building2",
            tab=SidebarTab.SETUP,
            order=10,
            nav_items=[
                {"label": "Company Settings", "href": "/settings/company", "icon": "Building2"},
                {"label": "Users & Roles", "href": "/users", "icon": "Users"},
                {"label": "Modules", "href": "/settings/modules", "icon": "LayoutGrid"},
                {"label": "Plan & Billing", "href": "/settings/billing", "icon": "CreditCard"},
                {"label": "Account", "href": "/settings/account", "icon": "KeyRound"},
            ],
        )

        module_registry.register_sidebar(
            "setup_accounting",
            label="Accounting",
            icon="Calculator",
            tab=SidebarTab.SETUP,
            order=20,
            nav_items=[
                {"label": "Chart of Accounts", "href": "/accounting/chart-of-accounts", "icon": "FileText"},
                {"label": "Tax Codes", "href": "/accounting/tax-codes", "icon": "Percent"},
                {"label": "Posting Profiles", "href": "/accounting/posting-profiles", "icon": "CreditCard"},
                {"label": "Dimensions", "href": "/settings/dimensions", "icon": "Layers"},
                {"label": "Fiscal Periods", "href": "/settings/periods", "icon": "Calendar"},
                {"label": "Exchange Rates", "href": "/settings/exchange-rates", "icon": "ArrowLeftRight"},
                {"label": "Accounting Settings", "href": "/accounting/settings", "icon": "Settings"},
            ],
        )

        module_registry.register_sidebar(
            "setup_integrations",
            label="Integrations",
            icon="Plug",
            tab=SidebarTab.SETUP,
            order=40,
            nav_items=[
                {"label": "Integrations", "href": "/settings/integrations", "icon": "Plug"},
            ],
        )

        module_registry.register_sidebar(
            "setup_migration",
            label="Migration",
            icon="Upload",
            tab=SidebarTab.SETUP,
            order=90,
            nav_items=[
                {"label": "Opening Balances", "href": "/inventory/opening-balance", "icon": "PackagePlus"},
                {"label": "Import Data", "href": "/accounting/import", "icon": "Upload"},
            ],
        )
