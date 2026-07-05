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

    # Event dataclasses merged into EVENT_DATA_CLASSES by ProjectionsConfig.ready().
    event_types_module = "platform_connectors.event_types"

    # Projections discovered + registered by ProjectionsConfig.ready().
    projections = [
        # Creates JEs from PLATFORM_* events.
        "platform_connectors.projections.PlatformAccountingProjection",
        # ADR-0002 Phase 2: materializes ProviderPayoutLine from the per-payout
        # line breakdown of PAYMENT_SETTLEMENT_RECEIVED (2nd, independent consumer).
        "platform_connectors.projections.PaymentsProjection",
    ]
