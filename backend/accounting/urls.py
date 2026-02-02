# accounting/urls.py
"""
URL configuration for accounting API.

Endpoints:
- /accounts/ - Chart of Accounts CRUD
- /journal-entries/ - Journal Entry CRUD with workflow actions
- /dimensions/ - Analysis Dimensions CRUD
- /dimensions/<id>/values/ - Dimension Values CRUD
"""

from django.urls import path

from .views import (
    # Account views
    AccountListCreateView,
    AccountDetailView,
    AccountAnalysisDefaultView,
    AccountAnalysisDefaultDeleteView,
    AccountExportView,
    # Journal entry views
    JournalEntryListCreateView,
    JournalEntryDetailView,
    JournalSaveCompleteView,
    JournalPostView,
    JournalReverseView,
    JournalEntryExportView,
    # Analysis dimension views
    AnalysisDimensionListCreateView,
    AnalysisDimensionDetailView,
    DimensionValueListCreateView,
    DimensionValueDetailView,
)

app_name = "accounting"

urlpatterns = [
    # ==========================================================================
    # Accounts (Chart of Accounts)
    # ==========================================================================
    path(
        "accounts/",
        AccountListCreateView.as_view(),
        name="account-list-create",
    ),
    path(
        "accounts/export/",
        AccountExportView.as_view(),
        name="account-export",
    ),
    path(
        "accounts/<str:code>/",
        AccountDetailView.as_view(),
        name="account-detail",
    ),
    
    # Account Analysis Defaults
    path(
        "accounts/<str:code>/analysis-defaults/",
        AccountAnalysisDefaultView.as_view(),
        name="account-analysis-defaults",
    ),
    path(
        "accounts/<str:code>/analysis-defaults/<int:dim_pk>/",
        AccountAnalysisDefaultDeleteView.as_view(),
        name="account-analysis-default-delete",
    ),

    # ==========================================================================
    # Journal Entries
    # ==========================================================================
    path(
        "journal-entries/",
        JournalEntryListCreateView.as_view(),
        name="journal-entry-list-create",
    ),
    path(
        "journal-entries/export/",
        JournalEntryExportView.as_view(),
        name="journal-entry-export",
    ),
    path(
        "journal-entries/<int:pk>/",
        JournalEntryDetailView.as_view(),
        name="journal-entry-detail",
    ),
    
    # Journal Entry Workflow Actions
    path(
        "journal-entries/<int:pk>/complete/",
        JournalSaveCompleteView.as_view(),
        name="journal-entry-complete",
    ),
    path(
        "journal-entries/<int:pk>/post/",
        JournalPostView.as_view(),
        name="journal-entry-post",
    ),
    path(
        "journal-entries/<int:pk>/reverse/",
        JournalReverseView.as_view(),
        name="journal-entry-reverse",
    ),

    # ==========================================================================
    # Analysis Dimensions
    # ==========================================================================
    path(
        "dimensions/",
        AnalysisDimensionListCreateView.as_view(),
        name="dimension-list-create",
    ),
    path(
        "dimensions/<int:pk>/",
        AnalysisDimensionDetailView.as_view(),
        name="dimension-detail",
    ),
    
    # Dimension Values
    path(
        "dimensions/<int:dim_pk>/values/",
        DimensionValueListCreateView.as_view(),
        name="dimension-value-list-create",
    ),
    path(
        "dimensions/<int:dim_pk>/values/<int:pk>/",
        DimensionValueDetailView.as_view(),
        name="dimension-value-detail",
    ),
]