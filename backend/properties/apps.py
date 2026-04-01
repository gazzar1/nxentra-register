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
        from accounts.module_registry import ModuleCategory, module_registry

        module_registry.register(
            "properties",
            label="Properties",
            icon="Home",
            category=ModuleCategory.VERTICAL,
            order=60,
            nav_items=[
                {"label": "Dashboard", "href": "/properties/dashboard", "icon": "LayoutGrid", "translation_key": "nav.propDashboard"},
                {"label": "Properties", "href": "/properties/properties", "icon": "Building2", "translation_key": "nav.propertiesList"},
                {"label": "Units", "href": "/properties/units", "icon": "DoorOpen", "translation_key": "nav.units"},
                {"label": "Lessees", "href": "/properties/lessees", "icon": "UserSquare2", "translation_key": "nav.lessees"},
                {"label": "Leases", "href": "/properties/leases", "icon": "FileSignature", "translation_key": "nav.leases"},
                {"label": "Collections", "href": "/properties/payments", "icon": "Banknote", "translation_key": "nav.collections"},
                {"label": "Expenses", "href": "/properties/expenses", "icon": "Receipt", "translation_key": "nav.propExpenses"},
                {"label": "Alerts", "href": "/properties/alerts", "icon": "AlertTriangle", "translation_key": "nav.propAlerts"},
                {"label": "Reports", "href": "/properties/reports", "icon": "PieChart", "translation_key": "nav.propReports"},
                {"label": "Settings", "href": "/properties/settings", "icon": "Settings", "translation_key": "nav.propSettings"},
            ],
        )
