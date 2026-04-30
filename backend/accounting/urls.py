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

from .bank_views import (
    BankAutoMatchView,
    BankExcludeLineView,
    BankManualMatchView,
    BankReconcileView,
    BankStatementCSVImportView,
    BankStatementDetailView,
    BankStatementListCreateView,
    BankUnmatchView,
    BankUnreconciledLinesView,
    CommerceReconciliationView,
)
from .payment_gateway_views import (
    PaymentGatewayDetailView,
    PaymentGatewayListView,
)
from .views import (
    AccountAnalysisDefaultDeleteView,
    AccountAnalysisDefaultView,
    AccountDetailView,
    AccountExportView,
    # Account views
    AccountListCreateView,
    AnalysisDimensionDetailView,
    # Analysis dimension views
    AnalysisDimensionListCreateView,
    # Core account mapping
    CoreAccountMappingView,
    CustomerDetailView,
    # Customer/Vendor views
    CustomerListCreateView,
    # Cash application views
    CustomerReceiptCreateView,
    DimensionValueDetailView,
    DimensionValueListCreateView,
    ExchangeRateDetailView,
    # Exchange rate views
    ExchangeRateListCreateView,
    ExchangeRateLookupView,
    JournalEntryDetailView,
    JournalEntryExportView,
    # Journal entry views
    JournalEntryListCreateView,
    JournalPostView,
    JournalReverseView,
    JournalSaveCompleteView,
    SeedAccountsView,
    # Admin views
    SeedStatusView,
    StatisticalEntryDetailView,
    # Statistical entry views
    StatisticalEntryListCreateView,
    StatisticalEntryPostView,
    VendorDetailView,
    VendorListCreateView,
    VendorPaymentCreateView,
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
    # Cash Application (Customer Receipts / Vendor Payments)
    # ==========================================================================
    path(
        "customer-receipts/",
        CustomerReceiptCreateView.as_view(),
        name="customer-receipt-create",
    ),
    path(
        "vendor-payments/",
        VendorPaymentCreateView.as_view(),
        name="vendor-payment-create",
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
    # Exchange Rates
    # ==========================================================================
    path(
        "exchange-rates/",
        ExchangeRateListCreateView.as_view(),
        name="exchange-rate-list-create",
    ),
    path(
        "exchange-rates/lookup/",
        ExchangeRateLookupView.as_view(),
        name="exchange-rate-lookup",
    ),
    path(
        "exchange-rates/<int:pk>/",
        ExchangeRateDetailView.as_view(),
        name="exchange-rate-detail",
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
    # ==========================================================================
    # Bank Reconciliation
    # ==========================================================================
    path(
        "bank-statements/",
        BankStatementListCreateView.as_view(),
        name="bank-statement-list-create",
    ),
    path(
        "bank-statements/parse-csv/",
        BankStatementCSVImportView.as_view(),
        name="bank-statement-parse-csv",
    ),
    path(
        "bank-statements/match/",
        BankManualMatchView.as_view(),
        name="bank-statement-manual-match",
    ),
    path(
        "bank-statements/unmatch/",
        BankUnmatchView.as_view(),
        name="bank-statement-unmatch",
    ),
    path(
        "bank-statements/exclude/",
        BankExcludeLineView.as_view(),
        name="bank-statement-exclude",
    ),
    path(
        "bank-statements/<int:pk>/",
        BankStatementDetailView.as_view(),
        name="bank-statement-detail",
    ),
    path(
        "bank-statements/<int:pk>/auto-match/",
        BankAutoMatchView.as_view(),
        name="bank-statement-auto-match",
    ),
    path(
        "bank-statements/<int:pk>/reconcile/",
        BankReconcileView.as_view(),
        name="bank-statement-reconcile",
    ),
    path(
        "bank-reconciliation/unreconciled/",
        BankUnreconciledLinesView.as_view(),
        name="bank-reconciliation-unreconciled",
    ),
    # ==========================================================================
    # Commerce Reconciliation (Three-Column View)
    # ==========================================================================
    path(
        "commerce-reconciliation/",
        CommerceReconciliationView.as_view(),
        name="commerce-reconciliation",
    ),
    # ==========================================================================
    # Core Account Mapping (FX Gain/Loss/Rounding)
    # ==========================================================================
    path(
        "core-account-mapping/",
        CoreAccountMappingView.as_view(),
        name="core-account-mapping",
    ),
    # ==========================================================================
    # Payment Gateway Routing (gateway -> posting profile -> clearing account)
    # ==========================================================================
    path(
        "payment-gateways/",
        PaymentGatewayListView.as_view(),
        name="payment-gateway-list",
    ),
    path(
        "payment-gateways/<int:pk>/",
        PaymentGatewayDetailView.as_view(),
        name="payment-gateway-detail",
    ),
]
