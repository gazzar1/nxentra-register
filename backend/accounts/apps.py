# accounts/apps.py
"""Accounts app configuration."""

from django.apps import AppConfig
from django.conf import settings
from django.db.backends.signals import connection_created


class AccountsConfig(AppConfig):
    """Configuration for the accounts app."""
    
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Accounts & Multi-tenancy"
    
    def ready(self):
        """Initialize app when Django starts."""
        from accounts import rls

        def _on_connection_created(sender, connection, **kwargs):
            if settings.RLS_BYPASS:
                rls.set_rls_bypass(True, conn=connection)

        connection_created.connect(
            _on_connection_created,
            dispatch_uid="accounts.rls_init",
        )
