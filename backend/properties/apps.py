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
