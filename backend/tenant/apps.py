import logging
import os

from django.apps import AppConfig


logger = logging.getLogger(__name__)


class TenantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tenant"
    verbose_name = "Tenant Isolation"

    def ready(self):
        """
        Run startup health checks for tenant isolation.

        This validates that every Company has a corresponding TenantDirectory entry,
        which is required for database routing to work correctly.

        Behavior controlled by TENANT_HEALTH_CHECK env var:
        - "error" (default for production): Raises RuntimeError on inconsistency
        - "warn": Logs warning but allows startup
        - "skip": Skips check entirely (for tests/migrations)
        """
        # Skip during migrations or test setup
        import sys
        if 'migrate' in sys.argv or 'makemigrations' in sys.argv:
            return
        if 'test' in sys.argv or os.environ.get('DJANGO_TEST_MODE'):
            return

        # Get health check mode from environment
        health_check_mode = os.environ.get('TENANT_HEALTH_CHECK', 'error')
        if health_check_mode == 'skip':
            logger.debug("Tenant health check skipped (TENANT_HEALTH_CHECK=skip)")
            return

        # Defer import to avoid AppRegistryNotReady
        from django.db import connection
        from django.db.utils import OperationalError, ProgrammingError

        try:
            # Check if tables exist (may not during initial migration)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name IN ('accounts_company', 'tenant_tenantdirectory')"
                )
                tables = cursor.fetchall()
                if len(tables) < 2:
                    # Tables don't exist yet (initial setup)
                    logger.debug("Tenant health check skipped (tables not yet created)")
                    return
        except (OperationalError, ProgrammingError):
            # Database not ready
            logger.debug("Tenant health check skipped (database not ready)")
            return

        # Run the health check
        self._check_tenant_directory_consistency(health_check_mode)

    def _check_tenant_directory_consistency(self, mode: str):
        """
        Verify TenantDirectory consistency:
        1. Every Company has a TenantDirectory entry (no missing)
        2. No duplicate TenantDirectory entries for the same company
        3. DEDICATED_DB mode → db_alias exists in settings.DATABASES
        4. SHARED mode → db_alias == "default"

        Args:
            mode: "error" to raise, "warn" to log warning
        """
        from django.conf import settings
        from django.db.models import Count

        from accounts.models import Company
        from accounts.rls import rls_bypass
        from tenant.models import TenantDirectory

        errors = []

        try:
            with rls_bypass():
                # Get all company IDs
                company_ids = set(Company.objects.values_list('id', flat=True))

                # Get all TenantDirectory entries
                tenant_entries = list(TenantDirectory.objects.all())
                tenant_company_ids = set(td.company_id for td in tenant_entries)

                # 1. Check for missing TenantDirectory entries
                missing = company_ids - tenant_company_ids
                if missing:
                    missing_companies = list(
                        Company.objects.filter(id__in=missing).values_list('slug', flat=True)
                    )
                    errors.append(
                        f"Missing TenantDirectory: {len(missing)} companies without entries: "
                        f"{missing_companies[:10]}{'...' if len(missing) > 10 else ''}"
                    )

                # 2. Check for duplicate TenantDirectory entries
                duplicates = (
                    TenantDirectory.objects
                    .values('company_id')
                    .annotate(count=Count('id'))
                    .filter(count__gt=1)
                )
                if duplicates.exists():
                    dup_company_ids = [d['company_id'] for d in duplicates]
                    dup_slugs = list(
                        Company.objects.filter(id__in=dup_company_ids).values_list('slug', flat=True)
                    )
                    errors.append(
                        f"Duplicate TenantDirectory: {len(dup_company_ids)} companies have "
                        f"multiple entries: {dup_slugs[:10]}{'...' if len(dup_slugs) > 10 else ''}"
                    )

                # 3 & 4. Validate mode and db_alias consistency
                available_databases = set(settings.DATABASES.keys())

                for td in tenant_entries:
                    company_slug = td.company.slug if td.company else f"company_id={td.company_id}"

                    if td.mode == TenantDirectory.IsolationMode.DEDICATED_DB:
                        # DEDICATED_DB must have db_alias in settings.DATABASES
                        if td.db_alias not in available_databases:
                            errors.append(
                                f"Invalid db_alias: {company_slug} has mode=DEDICATED_DB "
                                f"but db_alias='{td.db_alias}' not in DATABASES "
                                f"(available: {sorted(available_databases)})"
                            )

                    elif td.mode == TenantDirectory.IsolationMode.SHARED:
                        # SHARED must use "default" database
                        if td.db_alias != "default":
                            errors.append(
                                f"Invalid db_alias: {company_slug} has mode=SHARED "
                                f"but db_alias='{td.db_alias}' (should be 'default')"
                            )

                # Report results
                if errors:
                    message = (
                        f"TENANT HEALTH CHECK FAILED ({len(errors)} issues):\n"
                        + "\n".join(f"  - {e}" for e in errors)
                        + "\nRun: python manage.py seed_tenant_directory"
                    )

                    if mode == 'error':
                        raise RuntimeError(message)
                    else:
                        logger.warning(message)
                else:
                    logger.info(
                        f"Tenant health check passed: {len(company_ids)} companies, "
                        f"all have valid TenantDirectory entries"
                    )

        except RuntimeError:
            raise
        except Exception as e:
            # Other errors (e.g., table doesn't exist during migration)
            logger.debug(f"Tenant health check skipped: {e}")
