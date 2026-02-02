"""
Tenant Directory - Maps companies to their database configuration.

This module provides the TenantDirectory model which lives in the System DB
and determines where each tenant's data is stored.

Design Principles:
- No secrets stored in database (only db_alias references)
- Backward compatible (missing entry = shared mode)
- Supports migration status tracking
"""
import uuid

from django.db import models


class TenantDirectory(models.Model):
    """
    Maps a Company (tenant) to its database configuration.

    This table lives in the SYSTEM database ("default") and is
    used by the router to determine where tenant data lives.

    db_alias maps to environment variables:
    - "default" -> shared database, uses RLS
    - "tenant_acme" -> DATABASE_URL_TENANT_ACME env var

    Security: No DSNs or passwords stored - only alias names.
    """

    class IsolationMode(models.TextChoices):
        SHARED = "SHARED", "Shared Database (RLS)"
        DEDICATED_DB = "DEDICATED_DB", "Dedicated Database"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        MIGRATING = "MIGRATING", "Migrating (Write Freeze)"
        READ_ONLY = "READ_ONLY", "Read Only"
        SUSPENDED = "SUSPENDED", "Suspended"

    id = models.BigAutoField(primary_key=True)

    company = models.OneToOneField(
        "accounts.Company",
        on_delete=models.PROTECT,
        related_name="tenant_config",
        help_text="The company this tenant configuration applies to.",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        help_text="Public identifier for API exposure.",
    )

    mode = models.CharField(
        max_length=20,
        choices=IsolationMode.choices,
        default=IsolationMode.SHARED,
        help_text="Isolation mode: SHARED uses RLS, DEDICATED_DB uses separate database.",
    )

    db_alias = models.CharField(
        max_length=100,
        default="default",
        help_text="Database alias. Maps to DATABASE_URL_TENANT_{alias} env var for dedicated DBs.",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        help_text="Current status. MIGRATING enables write freeze.",
    )

    # Migration tracking
    migrated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When migration to dedicated DB completed.",
    )

    migration_event_sequence = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Last company_sequence exported during migration.",
    )

    migration_export_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="SHA-256 hash of exported event stream for verification.",
    )

    migration_import_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="SHA-256 hash of imported event stream for verification.",
    )

    migration_import_count = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Number of events imported during migration.",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Notes for operators
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Operator notes about this tenant configuration.",
    )

    class Meta:
        db_table = "tenant_directory"
        verbose_name = "Tenant Directory Entry"
        verbose_name_plural = "Tenant Directory"
        indexes = [
            models.Index(fields=["db_alias"], name="tenant_dir_db_alias_idx"),
            models.Index(fields=["status"], name="tenant_dir_status_idx"),
            models.Index(fields=["mode"], name="tenant_dir_mode_idx"),
        ]

    def __str__(self):
        return f"{self.company.slug} -> {self.db_alias} ({self.mode})"

    @property
    def is_shared(self) -> bool:
        """Check if tenant is in shared mode (RLS applies)."""
        return self.mode == self.IsolationMode.SHARED

    @property
    def is_dedicated(self) -> bool:
        """Check if tenant has a dedicated database."""
        return self.mode == self.IsolationMode.DEDICATED_DB

    @property
    def is_writable(self) -> bool:
        """Check if tenant allows writes (not migrating or suspended)."""
        return self.status == self.Status.ACTIVE

    @classmethod
    def get_for_company(cls, company_id: int) -> "TenantDirectory | None":
        """
        Get TenantDirectory entry for a company.

        Returns None if not found (means shared mode with default settings).
        """
        try:
            return cls.objects.select_related("company").get(company_id=company_id)
        except cls.DoesNotExist:
            return None

    @classmethod
    def get_db_alias_for_company(cls, company_id: int) -> str:
        """
        Get the database alias for a company.

        Returns 'default' if not found (backward compatible - shared tenant).
        """
        entry = cls.get_for_company(company_id)
        if entry is None:
            return "default"
        if entry.status in (cls.Status.ACTIVE, cls.Status.READ_ONLY):
            return entry.db_alias
        # During migration or suspension, route to source (default)
        return "default"

    @classmethod
    def get_tenant_info(cls, company_id: int) -> dict:
        """
        Get full tenant configuration info for middleware.

        Returns dict with:
        - db_alias: str
        - is_shared: bool
        - status: str
        - is_writable: bool
        """
        entry = cls.get_for_company(company_id)
        if entry is None:
            # No entry = shared tenant (backward compatible)
            return {
                "db_alias": "default",
                "is_shared": True,
                "status": cls.Status.ACTIVE,
                "is_writable": True,
            }
        return {
            "db_alias": entry.db_alias if entry.is_writable else "default",
            "is_shared": entry.is_shared,
            "status": entry.status,
            "is_writable": entry.is_writable,
        }


class MigrationLog(models.Model):
    """
    Audit log for tenant migrations.

    Records all migration attempts with verification hashes.
    """

    class Result(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        ROLLED_BACK = "ROLLED_BACK", "Rolled Back"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"

    id = models.BigAutoField(primary_key=True)

    tenant = models.ForeignKey(
        TenantDirectory,
        on_delete=models.CASCADE,
        related_name="migration_logs",
    )

    # Migration direction
    from_mode = models.CharField(max_length=20, choices=TenantDirectory.IsolationMode.choices)
    to_mode = models.CharField(max_length=20, choices=TenantDirectory.IsolationMode.choices)
    from_db_alias = models.CharField(max_length=100)
    to_db_alias = models.CharField(max_length=100)

    # Timing
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Verification
    export_event_count = models.BigIntegerField(default=0)
    import_event_count = models.BigIntegerField(default=0)
    export_hash = models.CharField(max_length=64, blank=True, default="")
    import_hash = models.CharField(max_length=64, blank=True, default="")
    hashes_match = models.BooleanField(default=False)

    # Result
    result = models.CharField(
        max_length=20,
        choices=Result.choices,
        default=Result.IN_PROGRESS,
    )
    error_message = models.TextField(blank=True, default="")

    # Operator
    initiated_by = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Username or system identifier that initiated migration.",
    )

    class Meta:
        db_table = "tenant_migration_log"
        verbose_name = "Migration Log"
        verbose_name_plural = "Migration Logs"
        ordering = ["-started_at"]

    def __str__(self):
        return f"Migration {self.id}: {self.tenant.company.slug} {self.from_mode} -> {self.to_mode} ({self.result})"
