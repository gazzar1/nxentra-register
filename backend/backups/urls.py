# backups/urls.py
from django.urls import path

from .views import (
    BackupDetailView,
    BackupDownloadView,
    BackupExportView,
    BackupListView,
    BackupRestoreView,
)

urlpatterns = [
    path("", BackupListView.as_view(), name="backup-list"),
    path("export/", BackupExportView.as_view(), name="backup-export"),
    path("restore/", BackupRestoreView.as_view(), name="backup-restore"),
    path("<uuid:public_id>/", BackupDetailView.as_view(), name="backup-detail"),
    path("<uuid:public_id>/download/", BackupDownloadView.as_view(), name="backup-download"),
]
