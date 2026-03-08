# clinic/apps.py
from django.apps import AppConfig


class ClinicConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "clinic"
    verbose_name = "Clinic Lite"

    # Declarative vertical-module manifest.
    projections = [
        "clinic.projections.ClinicAccountingProjection",
    ]

    event_types_module = "clinic.event_types"

    account_roles = [
        "ACCOUNTS_RECEIVABLE",
        "CONSULTATION_REVENUE",
        "CASH_BANK",
    ]
