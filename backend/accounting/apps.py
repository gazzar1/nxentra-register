# accounting/apps.py
"""Accounting app configuration."""

from django.apps import AppConfig


class AccountingConfig(AppConfig):
    """Configuration for the accounting app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "accounting"
    verbose_name = "Accounting"

    # A14: PaymentSettlementProjection consumes PAYMENT_SETTLEMENT_RECEIVED
    # events emitted by the manual-CSV importer (and, eventually, automated
    # Paymob/Bosta connectors).
    projections = [
        "accounting.payment_settlement_projection.PaymentSettlementProjection",
    ]

    def ready(self):
        """Initialize app when Django starts."""
        # Import signal handlers here if needed in the future
        pass
