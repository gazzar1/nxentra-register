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

    def ready(self):
        from accounts.module_registry import ModuleCategory, module_registry

        module_registry.register(
            "clinic",
            label="Clinic",
            icon="Stethoscope",
            category=ModuleCategory.VERTICAL,
            order=70,
            nav_items=[
                {"label": "Patients", "href": "/clinic/patients", "icon": "HeartPulse", "translation_key": "nav.patients"},
                {"label": "Doctors", "href": "/clinic/doctors", "icon": "Stethoscope", "translation_key": "nav.doctors"},
                {"label": "Visits", "href": "/clinic/visits", "icon": "CalendarCheck", "translation_key": "nav.visits"},
                {"label": "Invoices", "href": "/clinic/invoices", "icon": "ClipboardCheck", "translation_key": "nav.clinicInvoices"},
                {"label": "Payments", "href": "/clinic/payments", "icon": "Banknote", "translation_key": "nav.clinicPayments"},
                {"label": "Settings", "href": "/clinic/settings", "icon": "Settings", "translation_key": "nav.clinicSettings"},
            ],
        )
