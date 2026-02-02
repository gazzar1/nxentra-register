"""
Django Admin registration for Tenant models.
"""
from django.contrib import admin

from tenant.models import TenantDirectory, MigrationLog


@admin.register(TenantDirectory)
class TenantDirectoryAdmin(admin.ModelAdmin):
    """Admin interface for TenantDirectory."""

    list_display = [
        "company",
        "mode",
        "db_alias",
        "status",
        "migrated_at",
        "updated_at",
    ]
    list_filter = ["mode", "status"]
    search_fields = ["company__name", "company__slug", "db_alias"]
    readonly_fields = [
        "public_id",
        "created_at",
        "updated_at",
        "migrated_at",
        "migration_event_sequence",
        "migration_export_hash",
    ]
    raw_id_fields = ["company"]

    fieldsets = (
        (None, {
            "fields": ("company", "public_id"),
        }),
        ("Database Configuration", {
            "fields": ("mode", "db_alias", "status"),
        }),
        ("Migration Info", {
            "fields": (
                "migrated_at",
                "migration_event_sequence",
                "migration_export_hash",
            ),
            "classes": ("collapse",),
        }),
        ("Notes", {
            "fields": ("notes",),
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    def has_delete_permission(self, request, obj=None):
        """Prevent accidental deletion of tenant configs."""
        # Only allow deletion if status is SUSPENDED
        if obj and obj.status != TenantDirectory.Status.SUSPENDED:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(MigrationLog)
class MigrationLogAdmin(admin.ModelAdmin):
    """Admin interface for MigrationLog (read-only audit log)."""

    list_display = [
        "id",
        "tenant",
        "from_mode",
        "to_mode",
        "result",
        "started_at",
        "completed_at",
    ]
    list_filter = ["result", "from_mode", "to_mode"]
    search_fields = ["tenant__company__name", "tenant__company__slug"]
    readonly_fields = [
        "tenant",
        "from_mode",
        "to_mode",
        "from_db_alias",
        "to_db_alias",
        "started_at",
        "completed_at",
        "export_event_count",
        "import_event_count",
        "export_hash",
        "import_hash",
        "hashes_match",
        "result",
        "error_message",
        "initiated_by",
    ]

    fieldsets = (
        (None, {
            "fields": ("tenant", "initiated_by"),
        }),
        ("Migration Direction", {
            "fields": (
                "from_mode",
                "to_mode",
                "from_db_alias",
                "to_db_alias",
            ),
        }),
        ("Timing", {
            "fields": ("started_at", "completed_at"),
        }),
        ("Verification", {
            "fields": (
                "export_event_count",
                "import_event_count",
                "export_hash",
                "import_hash",
                "hashes_match",
            ),
        }),
        ("Result", {
            "fields": ("result", "error_message"),
        }),
    )

    def has_add_permission(self, request):
        """Migrations are created by management commands, not admin."""
        return False

    def has_change_permission(self, request, obj=None):
        """Migration logs are read-only audit records."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Migration logs should never be deleted."""
        return False
