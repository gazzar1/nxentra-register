# backups/views.py
"""
API views for company backup & restore.

Endpoints:
- POST /api/backups/export/     — Create a new backup
- GET  /api/backups/            — List backup history
- GET  /api/backups/<public_id>/ — Backup detail
- GET  /api/backups/<public_id>/download/ — Download backup file
- POST /api/backups/restore/    — Restore from backup upload
- DELETE /api/backups/<public_id>/ — Delete a backup
"""
import logging

from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authz import resolve_actor

from .models import BackupRecord

logger = logging.getLogger(__name__)


class BackupListView(APIView):
    """List backup history for the current company."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        backups = BackupRecord.objects.filter(company=actor.company)

        data = []
        for b in backups[:50]:
            data.append(_serialize_backup(b))

        return Response({"results": data})


class BackupExportView(APIView):
    """Create a new backup export."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from backups.exporter import export_company

        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        # Check for concurrent backup
        active = BackupRecord.objects.filter(
            company=actor.company,
            status__in=[BackupRecord.Status.PENDING, BackupRecord.Status.IN_PROGRESS],
        ).exists()
        if active:
            return Response(
                {"detail": "A backup is already in progress."},
                status=status.HTTP_409_CONFLICT,
            )

        # Create record
        record = BackupRecord.objects.create(
            company=actor.company,
            backup_type=BackupRecord.BackupType.MANUAL,
            status=BackupRecord.Status.IN_PROGRESS,
            started_at=timezone.now(),
            created_by=request.user,
        )

        try:
            zip_bytes, metadata = export_company(actor.company)

            # Save file
            from django.core.files.base import ContentFile
            filename = f"backup_{actor.company.slug}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.zip"
            record.file.save(filename, ContentFile(zip_bytes), save=False)

            record.status = BackupRecord.Status.COMPLETED
            record.completed_at = timezone.now()
            record.file_size_bytes = metadata["file_size_bytes"]
            record.file_checksum = metadata["file_checksum"]
            record.event_count = metadata["event_count"]
            record.model_counts = metadata["model_counts"]
            record.duration_seconds = metadata["duration_seconds"]
            record.save()

            logger.info(
                "Backup completed for %s: %d records, %d bytes",
                actor.company.slug,
                metadata["total_records"],
                metadata["file_size_bytes"],
            )

            return Response(_serialize_backup(record), status=status.HTTP_201_CREATED)

        except Exception as e:
            record.status = BackupRecord.Status.FAILED
            record.error_message = str(e)[:2000]
            record.completed_at = timezone.now()
            record.save()
            logger.exception("Backup failed for %s", actor.company.slug)
            return Response(
                {"detail": f"Backup failed: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BackupDetailView(APIView):
    """Get backup detail or delete a backup."""

    permission_classes = [IsAuthenticated]

    def get(self, request, public_id):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            record = BackupRecord.objects.get(
                public_id=public_id, company=actor.company
            )
        except BackupRecord.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        return Response(_serialize_backup(record))

    def delete(self, request, public_id):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            record = BackupRecord.objects.get(
                public_id=public_id, company=actor.company
            )
        except BackupRecord.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        # Delete file from storage
        if record.file:
            record.file.delete(save=False)

        record.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class BackupDownloadView(APIView):
    """Download a backup file."""

    permission_classes = [IsAuthenticated]

    def get(self, request, public_id):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            record = BackupRecord.objects.get(
                public_id=public_id, company=actor.company
            )
        except BackupRecord.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        if not record.file:
            return Response({"detail": "No backup file available."}, status=404)

        response = FileResponse(
            record.file.open("rb"),
            content_type="application/zip",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{record.file.name.split("/")[-1]}"'
        )
        return response


class BackupRestoreView(APIView):
    """Restore company data from a backup ZIP upload."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        from backups.importer import RestoreError, restore_company

        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        upload = request.FILES.get("file")
        if not upload:
            return Response(
                {"detail": "No file uploaded. Send a ZIP file as 'file'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate file size (max 500MB)
        if upload.size > 500 * 1024 * 1024:
            return Response(
                {"detail": "File too large. Maximum size is 500MB."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create restore record
        record = BackupRecord.objects.create(
            company=actor.company,
            backup_type=BackupRecord.BackupType.RESTORE,
            status=BackupRecord.Status.IN_PROGRESS,
            started_at=timezone.now(),
            created_by=request.user,
        )

        try:
            result = restore_company(actor.company, upload.read())

            record.status = BackupRecord.Status.COMPLETED
            record.completed_at = timezone.now()
            record.model_counts = result.get("imported", {})
            record.duration_seconds = result.get("duration_seconds", 0)
            record.save()

            logger.info("Restore completed for %s", actor.company.slug)

            return Response(
                {
                    "status": "success",
                    "detail": "Company data restored successfully.",
                    "stats": result,
                    "backup": _serialize_backup(record),
                },
                status=status.HTTP_200_OK,
            )

        except RestoreError as e:
            record.status = BackupRecord.Status.FAILED
            record.error_message = str(e)
            record.completed_at = timezone.now()
            record.save()
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            record.status = BackupRecord.Status.FAILED
            record.error_message = str(e)[:2000]
            record.completed_at = timezone.now()
            record.save()
            logger.exception("Restore failed for %s", actor.company.slug)
            return Response(
                {"detail": f"Restore failed: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


def _serialize_backup(b):
    return {
        "id": str(b.public_id),
        "backup_type": b.backup_type,
        "status": b.status,
        "file_size_bytes": b.file_size_bytes,
        "file_checksum": b.file_checksum,
        "event_count": b.event_count,
        "model_counts": b.model_counts or {},
        "started_at": b.started_at.isoformat() if b.started_at else None,
        "completed_at": b.completed_at.isoformat() if b.completed_at else None,
        "duration_seconds": b.duration_seconds,
        "error_message": b.error_message if b.status == BackupRecord.Status.FAILED else "",
        "created_by": b.created_by.email if b.created_by else None,
        "created_at": b.created_at.isoformat(),
        "has_file": bool(b.file),
    }
