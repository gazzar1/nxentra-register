# edim/urls.py
"""URL configuration for EDIM (External Data Ingestion & Mapping) API."""

from django.urls import path
from edim.views import (
    # Source System views
    SourceSystemListCreateView,
    SourceSystemDetailView,
    # Mapping Profile views
    MappingProfileListCreateView,
    MappingProfileDetailView,
    MappingProfileActivateView,
    MappingProfileDeprecateView,
    # Crosswalk views
    CrosswalkListCreateView,
    CrosswalkDetailView,
    CrosswalkVerifyView,
    CrosswalkRejectView,
    # Batch views
    BatchListView,
    BatchUploadView,
    BatchDetailView,
    BatchRecordsView,
    BatchMapView,
    BatchValidateView,
    BatchPreviewView,
    BatchCommitView,
    BatchRejectView,
)

app_name = "edim"

urlpatterns = [
    # Source Systems
    path("source-systems/", SourceSystemListCreateView.as_view(), name="source-system-list"),
    path("source-systems/<int:pk>/", SourceSystemDetailView.as_view(), name="source-system-detail"),

    # Mapping Profiles
    path("mapping-profiles/", MappingProfileListCreateView.as_view(), name="mapping-profile-list"),
    path("mapping-profiles/<int:pk>/", MappingProfileDetailView.as_view(), name="mapping-profile-detail"),
    path("mapping-profiles/<int:pk>/activate/", MappingProfileActivateView.as_view(), name="mapping-profile-activate"),
    path("mapping-profiles/<int:pk>/deprecate/", MappingProfileDeprecateView.as_view(), name="mapping-profile-deprecate"),

    # Identity Crosswalks
    path("crosswalks/", CrosswalkListCreateView.as_view(), name="crosswalk-list"),
    path("crosswalks/<int:pk>/", CrosswalkDetailView.as_view(), name="crosswalk-detail"),
    path("crosswalks/<int:pk>/verify/", CrosswalkVerifyView.as_view(), name="crosswalk-verify"),
    path("crosswalks/<int:pk>/reject/", CrosswalkRejectView.as_view(), name="crosswalk-reject"),

    # Ingestion Batches
    path("batches/", BatchListView.as_view(), name="batch-list"),
    path("batches/upload/", BatchUploadView.as_view(), name="batch-upload"),
    path("batches/<int:pk>/", BatchDetailView.as_view(), name="batch-detail"),
    path("batches/<int:pk>/records/", BatchRecordsView.as_view(), name="batch-records"),
    path("batches/<int:pk>/map/", BatchMapView.as_view(), name="batch-map"),
    path("batches/<int:pk>/validate/", BatchValidateView.as_view(), name="batch-validate"),
    path("batches/<int:pk>/preview/", BatchPreviewView.as_view(), name="batch-preview"),
    path("batches/<int:pk>/commit/", BatchCommitView.as_view(), name="batch-commit"),
    path("batches/<int:pk>/reject/", BatchRejectView.as_view(), name="batch-reject"),
]
