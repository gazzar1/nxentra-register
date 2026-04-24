# backups/admin.py
from django.contrib import admin

from .models import BackupRecord


@admin.register(BackupRecord)
class BackupRecordAdmin(admin.ModelAdmin):
    list_display = (
        "public_id",
        "company",
        "backup_type",
        "status",
        "event_count",
        "file_size_bytes",
        "duration_seconds",
        "created_at",
    )
    list_filter = ("status", "backup_type")
    search_fields = ("company__name", "company__slug")
    readonly_fields = ("public_id", "file_checksum", "created_at")
    raw_id_fields = ("company", "created_by", "restored_from")
