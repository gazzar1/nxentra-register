# backups/models.py
"""
BackupRecord tracks company backup/restore history.

Each record represents a single export or restore operation,
with metadata about what was included and the resulting file.
"""
import uuid

from django.conf import settings
from django.db import models


def backup_upload_path(instance, filename):
    return f"backups/{instance.company.slug}/{filename}"


class BackupRecord(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    class BackupType(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        RESTORE = "RESTORE", "Restore"

    company = models.ForeignKey(
        "accounts.Company",
        on_delete=models.CASCADE,
        related_name="backups",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    backup_type = models.CharField(
        max_length=20, choices=BackupType.choices, default=BackupType.MANUAL
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )

    # File
    file = models.FileField(upload_to=backup_upload_path, blank=True, null=True)
    file_size_bytes = models.BigIntegerField(null=True, blank=True)
    file_checksum = models.CharField(max_length=64, blank=True, default="")

    # Stats
    event_count = models.PositiveIntegerField(default=0)
    model_counts = models.JSONField(default=dict, blank=True)

    # Timing
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)

    # Error
    error_message = models.TextField(blank=True, default="")

    # Audit
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_backups",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Restore tracking
    restored_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "-created_at"]),
        ]

    def __str__(self):
        return f"Backup {self.public_id} ({self.status})"
