"""
Django Database Router for multi-tenant isolation.

Routes database operations based on model classification:
- SYSTEM_MODELS -> always to "default" database
- TENANT_MODELS -> to tenant-specific database from context

This router enables the Database-per-Tenant architecture where
premium tenants can have their own isolated database while
shared tenants use RLS on the default database.

Usage:
    # In settings.py
    DATABASE_ROUTERS = ['tenant.router.TenantDatabaseRouter']

Design Principles:
- System models (User, Company, etc.) ALWAYS go to default
- Tenant models route based on current tenant context
- Migrations run on all applicable databases
- Backward compatible: no context = default database
"""
from typing import Optional, Type

from django.conf import settings
from django.db.models import Model

from tenant.context import get_current_db_alias, is_shared_tenant


# =============================================================================
# Model Classification
# =============================================================================

# System apps that ALWAYS live in the default database
# These apps contain system-wide data, not tenant-specific data
SYSTEM_APPS = frozenset({
    "auth",
    "contenttypes",
    "sessions",
    "admin",
    "token_blacklist",  # SimpleJWT token blacklist
    "tenant",  # TenantDirectory lives in system DB
})

# System models within apps that otherwise have tenant models
# These are explicitly classified as system models
SYSTEM_MODELS = frozenset({
    "accounts.User",
    "accounts.Company",
    "accounts.CompanyMembership",
    "accounts.CompanyMembershipPermission",
    "accounts.NxPermission",
    "accounts.EmailVerificationToken",
})

# Tenant apps that route based on context
# All models in these apps are tenant-specific
TENANT_APPS = frozenset({
    "events",
    "accounting",
    "projections",
    "edim",
})


# =============================================================================
# Router Implementation
# =============================================================================

class TenantDatabaseRouter:
    """
    Routes database operations based on tenant context.

    System models (User, Company, TenantDirectory, etc.) always go to 'default'.
    Tenant models route to the database alias in tenant context.

    Thread-safe via contextvars (see tenant.context module).
    """

    def _get_model_label(self, model: Type[Model]) -> str:
        """Get the app_label.ModelName for a model."""
        return f"{model._meta.app_label}.{model._meta.object_name}"

    def _is_system_model(self, model: Type[Model]) -> bool:
        """
        Check if model is a system model (always routes to default).

        A model is a system model if:
        - Its app_label is in SYSTEM_APPS, OR
        - Its full label (app.Model) is in SYSTEM_MODELS
        """
        # Handle SimpleLazyObject and other types without _meta
        # This can happen when Django admin uses lazy user objects
        if not hasattr(model, '_meta'):
            return True  # Treat as system model (safe fallback to default)

        app_label = model._meta.app_label
        model_label = self._get_model_label(model)
        return app_label in SYSTEM_APPS or model_label in SYSTEM_MODELS

    def _is_tenant_model(self, model: Type[Model]) -> bool:
        """
        Check if model is a tenant model (routes based on context).

        A model is a tenant model if:
        - Its app_label is in TENANT_APPS, AND
        - Its full label is NOT in SYSTEM_MODELS
        """
        # Handle types without _meta (e.g., SimpleLazyObject)
        if not hasattr(model, '_meta'):
            return False  # Not a tenant model if no _meta

        app_label = model._meta.app_label
        model_label = self._get_model_label(model)
        return app_label in TENANT_APPS and model_label not in SYSTEM_MODELS

    def db_for_read(self, model: Type[Model], **hints) -> Optional[str]:
        """
        Route reads to appropriate database.

        - System models -> 'default'
        - Tenant models -> current tenant's database alias
        - Unknown models -> 'default' (safe fallback)
        """
        if self._is_system_model(model):
            return "default"

        if self._is_tenant_model(model):
            return get_current_db_alias()

        # Default fallback for any unclassified models
        return "default"

    def db_for_write(self, model: Type[Model], **hints) -> Optional[str]:
        """
        Route writes to appropriate database.

        Same logic as db_for_read, but with additional safety checks
        to prevent accidental writes without proper context.
        """
        if self._is_system_model(model):
            return "default"

        if self._is_tenant_model(model):
            db_alias = get_current_db_alias()

            # Safety check: warn if writing tenant data without explicit context
            # This can happen during migrations or management commands
            # We allow it but log for debugging
            if db_alias == "default" and not is_shared_tenant():
                # This is a dedicated tenant but context says default
                # This shouldn't happen in normal operation
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(
                    "Writing tenant model %s to default without shared mode. "
                    "This may indicate missing tenant context.",
                    self._get_model_label(model),
                )

            return db_alias

        return "default"

    def allow_relation(self, obj1: Model, obj2: Model, **hints) -> Optional[bool]:
        """
        Allow relations between models.

        We allow all relations because:
        - System models can relate to system models
        - Tenant models can relate to tenant models
        - Cross-tier relations (e.g., JournalEntry -> User) are allowed
          but use db_constraint=False to avoid FK integrity issues

        The cross-database FK handling is managed at the model level,
        not the router level.
        """
        # Same database tier: always allow
        obj1_system = self._is_system_model(type(obj1))
        obj2_system = self._is_system_model(type(obj2))

        if obj1_system == obj2_system:
            return True

        # Cross-tier: allow (FK must use db_constraint=False)
        return True

    def allow_migrate(
        self,
        db: str,
        app_label: str,
        model_name: Optional[str] = None,
        **hints,
    ) -> Optional[bool]:
        """
        Control which migrations run on which database.

        - System apps only migrate on 'default'
        - Tenant apps migrate on ALL databases (default + tenant DBs)

        This ensures tenant tables exist in both the shared database
        and any dedicated tenant databases.
        """
        # System apps: only on default
        if app_label in SYSTEM_APPS:
            return db == "default"

        # accounts app: need special handling
        if app_label == "accounts":
            # System models in accounts only on default
            if model_name:
                full_label = f"accounts.{model_name}"
                if full_label in SYSTEM_MODELS:
                    return db == "default"
            # If no model_name specified, allow on default only (safe default)
            return db == "default"

        # Tenant apps: migrate on ALL databases
        if app_label in TENANT_APPS:
            return True

        # Default: only migrate on default
        return db == "default"


def get_tenant_databases() -> list[str]:
    """
    Get list of all configured tenant database aliases.

    Useful for running migrations on all tenant databases:
        for db in get_tenant_databases():
            call_command('migrate', database=db)
    """
    return [
        alias
        for alias in settings.DATABASES.keys()
        if alias.startswith("tenant_")
    ]


def get_all_databases() -> list[str]:
    """
    Get list of all database aliases (default + tenants).

    Useful for health checks or administrative operations.
    """
    return list(settings.DATABASES.keys())
