# accounting/urls.py
"""
URL configuration for accounting API.

Endpoints:
- /accounts/ - Chart of Accounts CRUD
- /journal-entries/ - Journal Entry CRUD with workflow actions
- /dimensions/ - Analysis Dimensions CRUD
- /dimensions/<id>/values/ - Dimension Values CRUD
- /customers/ - Customer (AR subledger) CRUD
- /vendors/ - Vendor (AP subledger) CRUD
- /statistical-entries/ - Statistical Entry CRUD with post action

Admin Endpoints (super-admin only):
- /admin/seed-status/ - Check seed status (missing/existing accounts)
- /admin/seed-accounts/ - Seed missing required accounts
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
    # Customer/Vendor views
    CustomerListCreateView,
    CustomerDetailView,
    VendorListCreateView,
    VendorDetailView,
    # Statistical entry views
    StatisticalEntryListCreateView,
    StatisticalEntryDetailView,
    StatisticalEntryPostView,
    # Admin views
    SeedStatusView,
    SeedAccountsView,
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

    # ==========================================================================
    # Customers (AR Subledger)
    # ==========================================================================
    path(
        "customers/",
        CustomerListCreateView.as_view(),
        name="customer-list-create",
    ),
    path(
        "customers/<str:code>/",
        CustomerDetailView.as_view(),
        name="customer-detail",
    ),

    # ==========================================================================
    # Vendors (AP Subledger)
    # ==========================================================================
    path(
        "vendors/",
        VendorListCreateView.as_view(),
        name="vendor-list-create",
    ),
    path(
        "vendors/<str:code>/",
        VendorDetailView.as_view(),
        name="vendor-detail",
    ),

    # ==========================================================================
    # Statistical Entries
    # ==========================================================================
    path(
        "statistical-entries/",
        StatisticalEntryListCreateView.as_view(),
        name="statistical-entry-list-create",
    ),
    path(
        "statistical-entries/<int:pk>/",
        StatisticalEntryDetailView.as_view(),
        name="statistical-entry-detail",
    ),
    path(
        "statistical-entries/<int:pk>/post/",
        StatisticalEntryPostView.as_view(),
        name="statistical-entry-post",
    ),

    # ==========================================================================
    # Admin: Chart of Accounts Seeding (super-admin only)
    # ==========================================================================
    path(
        "admin/seed-status/",
        SeedStatusView.as_view(),
        name="admin-seed-status",
    ),
    path(
        "admin/seed-accounts/",
        SeedAccountsView.as_view(),
        name="admin-seed-accounts",
    ),
]