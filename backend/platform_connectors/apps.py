# platform_connectors/apps.py
"""
Django app configuration for platform_connectors.

This app provides the shared infrastructure for all commerce platform
integrations: base connector class, registry, canonical types, and
the shared JE builder.

No database tables — all models are abstract.
"""

from django.apps import AppConfig


class PlatformConnectorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "platform_connectors"
    verbose_name = "Platform Connectors"

    # Projection that creates JEs from PLATFORM_* events
    projections = [
        "platform_connectors.projections.PlatformAccountingProjection",
    ]
