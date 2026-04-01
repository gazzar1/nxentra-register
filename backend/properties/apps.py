# properties/apps.py
from django.apps import AppConfig


class PropertiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "properties"
    verbose_name = "Property Management"

    # Declarative vertical-module manifest.
    # ProjectionsConfig.ready() auto-discovers and registers these.
    projections = [
        "projections.property.PropertyAccountingProjection",
    ]

    event_types_module = "properties.event_types"

    account_roles = [
        "RENTAL_INCOME",
        "OTHER_INCOME",
        "ACCOUNTS_RECEIVABLE",
        "CASH_BANK",
        "UNAPPLIED_CASH",
        "SECURITY_DEPOSIT",
        "ACCOUNTS_PAYABLE",
        "PROPERTY_EXPENSE",
    ]

    def ready(self):
        from accounts.module_registry import ModuleCategory, SidebarTab, module_registry

        module_registry.register(
            "properties",
            label="Properties",
            icon="Home",
            category=ModuleCategory.VERTICAL,
            order=60,
        )

        module_registry.register_sidebar(
            "work_properties",
            label="Properties",
            icon="Home",
            tab=SidebarTab.WORK,
            order=60,
            module_key="properties",
            nav_items=[
                {"label": "Dashboard", "href": "/properties/dashboard", "icon": "LayoutGrid"},
                {"label": "Properties", "href": "/properties/properties", "icon": "Building2"},
                {"label": "Units", "href": "/properties/units", "icon": "DoorOpen"},
                {"label": "Lessees", "href": "/properties/lessees", "icon": "UserSquare2"},
                {"label": "Leases", "href": "/properties/leases", "icon": "FileSignature"},
                {"label": "Collections", "href": "/properties/payments", "icon": "Banknote"},
                {"label": "Expenses", "href": "/properties/expenses", "icon": "Receipt"},
            ],
        )

        module_registry.register_sidebar(
            "review_properties",
            label="Properties",
            icon="Home",
            tab=SidebarTab.REVIEW,
            order=50,
            module_key="properties",
            nav_items=[
                {"label": "Alerts", "href": "/properties/alerts", "icon": "AlertTriangle"},
                {"label": "Reports", "href": "/properties/reports", "icon": "PieChart"},
            ],
        )

        module_registry.register_sidebar(
            "setup_properties",
            label="Properties",
            icon="Home",
            tab=SidebarTab.SETUP,
            order=60,
            module_key="properties",
            nav_items=[
                {"label": "Settings", "href": "/properties/settings", "icon": "Settings"},
            ],
        )
