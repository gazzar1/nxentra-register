# edim/apps.py
"""EDIM (External Data Ingestion & Mapping) app configuration."""

from django.apps import AppConfig


class EdimConfig(AppConfig):
    """Configuration for the EDIM app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "edim"
    verbose_name = "External Data Ingestion & Mapping"

    def ready(self):
        """Register EDIM projections when the app starts."""
        import edim.projections  # noqa: F401
