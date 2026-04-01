# clinic/apps.py
from django.apps import AppConfig


class ClinicConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "clinic"
    verbose_name = "Clinic Lite"

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
        from accounts.module_registry import ModuleCategory, SidebarTab, module_registry

        module_registry.register(
            "clinic",
            label="Clinic",
            icon="Stethoscope",
            category=ModuleCategory.VERTICAL,
            order=70,
        )

        module_registry.register_sidebar(
            "work_clinic",
            label="Clinic",
            icon="Stethoscope",
            tab=SidebarTab.WORK,
            order=70,
            module_key="clinic",
            nav_items=[
                {"label": "Patients", "href": "/clinic/patients", "icon": "HeartPulse"},
                {"label": "Doctors", "href": "/clinic/doctors", "icon": "Stethoscope"},
                {"label": "Visits", "href": "/clinic/visits", "icon": "CalendarCheck"},
                {"label": "Invoices", "href": "/clinic/invoices", "icon": "ClipboardCheck"},
                {"label": "Payments", "href": "/clinic/payments", "icon": "Banknote"},
            ],
        )

        module_registry.register_sidebar(
            "setup_clinic",
            label="Clinic",
            icon="Stethoscope",
            tab=SidebarTab.SETUP,
            order=70,
            module_key="clinic",
            nav_items=[
                {"label": "Settings", "href": "/clinic/settings", "icon": "Settings"},
            ],
        )
