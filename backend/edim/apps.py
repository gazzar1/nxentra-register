# edim/apps.py
"""EDIM (External Data Ingestion & Mapping) app configuration."""

from django.apps import AppConfig


class EdimConfig(AppConfig):
    """Configuration for the EDIM app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "edim"
    verbose_name = "External Data Ingestion & Mapping"

    # Declarative vertical-module manifest.
    # ProjectionsConfig.ready() auto-discovers and registers these.
    projections = [
        "edim.projections.EdimBatchAuditProjection",
        "edim.projections.EdimConfigAuditProjection",
    ]

    event_types_module = "edim.event_types"
