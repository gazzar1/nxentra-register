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
    BankAutoMatchPreviewView,
    BankAutoMatchView,
    BankExcludeLineView,
    BankLineMatchCandidatesView,
    BankManualMatchView,
    BankReconcileView,
    BankResolveDifferenceView,
    BankStatementCSVHeadersView,
    BankStatementCSVImportView,
    BankStatementDetailView,
    BankStatementImportPreviewView,
    BankStatementListCreateView,
    BankUnmatchPreviewView,
    BankUnmatchView,
    BankUnreconciledLinesView,
    CommerceReconciliationView,
)
from .period_override_audit_views import PeriodOverrideAuditListView
from .reconciliation_views import (
    ReconciliationDrilldownView,
    ReconciliationOrdersView,
    ReconciliationPayoutLinesView,
    ReconciliationSummaryView,
    ReconciliationTraceView,
)
from .settlement_import_views import SettlementCSVImportView, SettlementCSVPreviewView
from .settlement_provider_views import (
    SettlementProviderDetailView,
    SettlementProviderListView,
)
from .views import (
    AccountAnalysisDefaultDeleteView,
    AccountAnalysisDefaultView,
    AccountDetailView,
    AccountDrilldownView,
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
    # A137: read-only GL account drilldown. Registered before the bare
    # <code>/ detail route for readability; Django's path() disambiguates on
    # the trailing "drilldown/" segment regardless of order. Named
    # "account-drilldown" to avoid confusion with the Reports "account-inquiry"
    # line-search report (projections app, different namespace).
    path(
        "accounts/<str:code>/drilldown/",
        AccountDrilldownView.as_view(),
        name="account-drilldown",
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
        "bank-statements/parse-csv-headers/",
        BankStatementCSVHeadersView.as_view(),
        name="bank-statement-parse-csv-headers",
    ),
    path(
        "bank-statements/parse-csv/",
        BankStatementCSVImportView.as_view(),
        name="bank-statement-parse-csv",
    ),
    # A85 chunk 2: dry-run preview before committing the bank statement.
    # Takes (account_id, lines) from parse-csv and returns dedup analysis.
    path(
        "bank-statements/import-preview/",
        BankStatementImportPreviewView.as_view(),
        name="bank-statement-import-preview",
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
    # A85 chunk 2b: dry-run preview of what JEs (if any) would be
    # reversed if the operator confirms the unmatch.
    path(
        "bank-statements/unmatch/preview/",
        BankUnmatchPreviewView.as_view(),
        name="bank-statement-unmatch-preview",
    ),
    path(
        "bank-statements/exclude/",
        BankExcludeLineView.as_view(),
        name="bank-statement-exclude",
    ),
    # A16: difference reason picker for MATCHED_WITH_DIFFERENCE bank lines.
    path(
        "bank-statements/lines/<int:pk>/difference/",
        BankResolveDifferenceView.as_view(),
        name="bank-statement-line-resolve-difference",
    ),
    path(
        "bank-statements/<int:pk>/",
        BankStatementDetailView.as_view(),
        name="bank-statement-detail",
    ),
    # A85 chunk 2c: dry-run preview must precede the execute route so
    # `<id>/auto-match/preview/` doesn't get swallowed by a regex match
    # against the execute path. Django's path() actually disambiguates
    # these cleanly, but we keep the order conventional for readability.
    path(
        "bank-statements/<int:pk>/auto-match/preview/",
        BankAutoMatchPreviewView.as_view(),
        name="bank-statement-auto-match-preview",
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
    path(
        "bank-statements/lines/<int:pk>/candidates/",
        BankLineMatchCandidatesView.as_view(),
        name="bank-line-match-candidates",
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
    # Settlement Provider Routing (provider -> posting profile -> clearing account)
    # ==========================================================================
    path(
        "settlement-providers/",
        SettlementProviderListView.as_view(),
        name="settlement-provider-list",
    ),
    path(
        "settlement-providers/<int:pk>/",
        SettlementProviderDetailView.as_view(),
        name="settlement-provider-detail",
    ),
    # ==========================================================================
    # Reconciliation Control Center (A13)
    # ==========================================================================
    path(
        "reconciliation/summary/",
        ReconciliationSummaryView.as_view(),
        name="reconciliation-summary",
    ),
    path(
        "reconciliation/drilldown/",
        ReconciliationDrilldownView.as_view(),
        name="reconciliation-drilldown",
    ),
    path(
        "reconciliation/orders/",
        ReconciliationOrdersView.as_view(),
        name="reconciliation-orders",
    ),
    path(
        "reconciliation/trace/",
        ReconciliationTraceView.as_view(),
        name="reconciliation-trace",
    ),
    path(
        "reconciliation/payout-lines/",
        ReconciliationPayoutLinesView.as_view(),
        name="reconciliation-payout-lines",
    ),
    # ==========================================================================
    # Settlement CSV Import (A14) + dry-run preview (A85)
    # ==========================================================================
    path(
        "settlements/import/preview/",
        SettlementCSVPreviewView.as_view(),
        name="settlement-csv-import-preview",
    ),
    path(
        "settlements/import/",
        SettlementCSVImportView.as_view(),
        name="settlement-csv-import",
    ),
    # A85 chunk 3: Period-override audit log (read-only).
    path(
        "period-overrides/",
        PeriodOverrideAuditListView.as_view(),
        name="period-override-audit-list",
    ),
]
