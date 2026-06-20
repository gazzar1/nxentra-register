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
        # A129b/P6: guard against orphaning a clearance JE by deleting a matched
        # bank statement outside the sanctioned unmatch-then-delete flow.
        from django.db.models.signals import pre_delete

        from accounting.models import BankStatement
        from accounting.signals import guard_bank_statement_delete

        pre_delete.connect(
            guard_bank_statement_delete,
            sender=BankStatement,
            dispatch_uid="accounting.guard_bank_statement_delete",
        )
