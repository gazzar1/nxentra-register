# scratchpad/urls.py
from django.urls import path

from . import views

app_name = "scratchpad"

urlpatterns = [
    # CRUD endpoints
    path("", views.ScratchpadListCreateView.as_view(), name="list-create"),
    path("<uuid:public_id>/", views.ScratchpadDetailView.as_view(), name="detail"),

    # Bulk operations
    path("bulk/", views.ScratchpadBulkView.as_view(), name="bulk"),

    # Validation
    path("validate/", views.ScratchpadValidateView.as_view(), name="validate"),

    # Commit
    path("commit/", views.ScratchpadCommitView.as_view(), name="commit"),

    # Import/Export
    path("import/", views.ScratchpadImportView.as_view(), name="import"),
    path("export/", views.ScratchpadExportView.as_view(), name="export"),

    # Dimension schema (for dynamic columns)
    path("dimensions/schema/", views.DimensionSchemaView.as_view(), name="dimension-schema"),

    # Account dimension rules
    path("dimension-rules/", views.AccountDimensionRuleListCreateView.as_view(), name="dimension-rules"),
    path("dimension-rules/<int:pk>/", views.AccountDimensionRuleDetailView.as_view(), name="dimension-rule-detail"),

    # Voice parsing (optional feature)
    path("parse-voice/", views.ScratchpadParseVoiceView.as_view(), name="parse-voice"),

    # Create rows from already-parsed data (avoids double API call)
    path("create-from-parsed/", views.ScratchpadCreateFromParsedView.as_view(), name="create-from-parsed"),

    # Voice usage statistics (admin only)
    path("voice-usage/", views.VoiceUsageView.as_view(), name="voice-usage"),
]
