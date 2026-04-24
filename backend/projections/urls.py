# projections/urls.py
"""
URL configuration for projection/reports API.

All these endpoints read from projected data (materialized views),
not from computing on-the-fly.

Endpoints:
- /reports/trial-balance/ - Trial balance
- /reports/balance-sheet/ - Balance sheet
- /reports/income-statement/ - Income statement (P&L)
- /reports/cash-flow-statement/ - Cash flow statement
- /reports/subledger-tieout/ - AR/AP subledger tie-out reconciliation
- /reports/ar-aging/ - Accounts Receivable aging report
- /reports/ap-aging/ - Accounts Payable aging report
- /reports/account-balances/ - All account balances
- /reports/account-balances/<code>/ - Single account balance
- /reports/customer-balances/ - All customer balances (AR subledger)
- /reports/customer-balances/<code>/ - Single customer balance
- /reports/vendor-balances/ - All vendor balances (AP subledger)
- /reports/vendor-balances/<code>/ - Single vendor balance
- /reports/projection-status/ - Projection health monitoring
- /reports/dashboard-charts/ - Dashboard chart data

Admin Projection Management (staff/superuser only):
- /reports/admin/projections/ - List all projections with status
- /reports/admin/projections/<name>/ - Get detailed projection status
- /reports/admin/projections/<name>/rebuild/ - Trigger rebuild
- /reports/admin/projections/<name>/pause/ - Pause/unpause projection
- /reports/admin/projections/<name>/clear-error/ - Clear error state
- /reports/admin/projections/<name>/process/ - Process pending events
"""

from django.urls import path

from .views import (
    AccountBalanceDetailView,
    AccountBalanceListView,
    # Account inquiry
    AccountInquiryView,
    AdminProjectionClearErrorView,
    AdminProjectionDetailView,
    # Admin projection management
    AdminProjectionListView,
    AdminProjectionPauseView,
    AdminProjectionProcessView,
    AdminProjectionRebuildView,
    APAgingReportView,
    # Aging reports
    ARAgingReportView,
    BalanceSheetView,
    CashFlowStatementView,
    # Currency revaluation
    CurrencyRevaluationView,
    CustomerBalanceDetailView,
    # Customer/Vendor balance views
    CustomerBalanceListView,
    # Customer/Vendor statement views
    CustomerStatementView,
    DashboardChartsView,
    DashboardWidgetsView,
    DimensionAnalysisView,
    DimensionCrossTabView,
    DimensionDrilldownView,
    DimensionPLComparisonView,
    FiscalPeriodCloseView,
    FiscalPeriodCurrentView,
    FiscalPeriodDatesView,
    FiscalPeriodListView,
    FiscalPeriodOpenView,
    FiscalPeriodRangeView,
    FiscalPeriodsConfigureView,
    # Fiscal year management
    FiscalYearCloseReadinessView,
    FiscalYearCloseView,
    FiscalYearClosingEntriesView,
    FiscalYearReopenView,
    IncomeStatementView,
    # Item profitability
    ItemProfitabilityView,
    # System Health & Month-End Close
    MonthEndCloseView,
    ProjectionStatusView,
    # Reconciliation
    ReconciliationCheckView,
    SubledgerTieOutView,
    SystemHealthView,
    # Tax summary
    TaxSummaryReportView,
    # Trial balance by currency
    TrialBalanceByCurrencyView,
    TrialBalanceView,
    VendorBalanceDetailView,
    VendorBalanceListView,
    VendorStatementView,
)

app_name = "projections"

urlpatterns = [
    # Financial Reports
    path(
        "trial-balance/",
        TrialBalanceView.as_view(),
        name="trial-balance",
    ),
    path(
        "balance-sheet/",
        BalanceSheetView.as_view(),
        name="balance-sheet",
    ),
    path(
        "income-statement/",
        IncomeStatementView.as_view(),
        name="income-statement",
    ),
    path(
        "dimension-analysis/",
        DimensionAnalysisView.as_view(),
        name="dimension-analysis",
    ),
    path(
        "dimension-drilldown/",
        DimensionDrilldownView.as_view(),
        name="dimension-drilldown",
    ),
    path(
        "dimension-crosstab/",
        DimensionCrossTabView.as_view(),
        name="dimension-crosstab",
    ),
    path(
        "dimension-pl-comparison/",
        DimensionPLComparisonView.as_view(),
        name="dimension-pl-comparison",
    ),
    path(
        "cash-flow-statement/",
        CashFlowStatementView.as_view(),
        name="cash-flow-statement",
    ),
    path(
        "subledger-tieout/",
        SubledgerTieOutView.as_view(),
        name="subledger-tieout",
    ),
    # Aging Reports
    path(
        "ar-aging/",
        ARAgingReportView.as_view(),
        name="ar-aging",
    ),
    path(
        "ap-aging/",
        APAgingReportView.as_view(),
        name="ap-aging",
    ),
    # Tax Summary
    path(
        "tax-summary/",
        TaxSummaryReportView.as_view(),
        name="tax-summary",
    ),
    # Account Balances
    path(
        "account-balances/",
        AccountBalanceListView.as_view(),
        name="account-balance-list",
    ),
    path(
        "account-balances/<str:code>/",
        AccountBalanceDetailView.as_view(),
        name="account-balance-detail",
    ),
    # Customer Balances (AR Subledger)
    path(
        "customer-balances/",
        CustomerBalanceListView.as_view(),
        name="customer-balance-list",
    ),
    path(
        "customer-balances/<str:code>/",
        CustomerBalanceDetailView.as_view(),
        name="customer-balance-detail",
    ),
    # Vendor Balances (AP Subledger)
    path(
        "vendor-balances/",
        VendorBalanceListView.as_view(),
        name="vendor-balance-list",
    ),
    path(
        "vendor-balances/<str:code>/",
        VendorBalanceDetailView.as_view(),
        name="vendor-balance-detail",
    ),
    # Customer/Vendor Statements
    path(
        "customer-statement/<str:code>/",
        CustomerStatementView.as_view(),
        name="customer-statement",
    ),
    path(
        "vendor-statement/<str:code>/",
        VendorStatementView.as_view(),
        name="vendor-statement",
    ),
    # Account Inquiry
    path(
        "account-inquiry/",
        AccountInquiryView.as_view(),
        name="account-inquiry",
    ),
    # Fiscal Periods
    path(
        "periods/",
        FiscalPeriodListView.as_view(),
        name="period-list",
    ),
    path(
        "periods/<int:fiscal_year>/<int:period>/close/",
        FiscalPeriodCloseView.as_view(),
        name="period-close",
    ),
    path(
        "periods/<int:fiscal_year>/<int:period>/open/",
        FiscalPeriodOpenView.as_view(),
        name="period-open",
    ),
    path(
        "periods/configure/",
        FiscalPeriodsConfigureView.as_view(),
        name="periods-configure",
    ),
    path(
        "periods/range/",
        FiscalPeriodRangeView.as_view(),
        name="periods-range",
    ),
    path(
        "periods/current/",
        FiscalPeriodCurrentView.as_view(),
        name="periods-current",
    ),
    path(
        "periods/<int:fiscal_year>/<int:period>/dates/",
        FiscalPeriodDatesView.as_view(),
        name="period-dates",
    ),
    # Fiscal Year Management
    path(
        "fiscal-years/<int:year>/close-readiness/",
        FiscalYearCloseReadinessView.as_view(),
        name="fiscal-year-close-readiness",
    ),
    path(
        "fiscal-years/<int:year>/close/",
        FiscalYearCloseView.as_view(),
        name="fiscal-year-close",
    ),
    path(
        "fiscal-years/<int:year>/reopen/",
        FiscalYearReopenView.as_view(),
        name="fiscal-year-reopen",
    ),
    path(
        "fiscal-years/<int:year>/closing-entries/",
        FiscalYearClosingEntriesView.as_view(),
        name="fiscal-year-closing-entries",
    ),
    # Reconciliation
    path(
        "reconciliation/",
        ReconciliationCheckView.as_view(),
        name="reconciliation-check",
    ),
    # Currency Revaluation
    path(
        "currency-revaluation/",
        CurrencyRevaluationView.as_view(),
        name="currency-revaluation",
    ),
    # Trial Balance by Currency
    path(
        "trial-balance-by-currency/",
        TrialBalanceByCurrencyView.as_view(),
        name="trial-balance-by-currency",
    ),
    # Item Profitability
    path(
        "item-profitability/",
        ItemProfitabilityView.as_view(),
        name="item-profitability",
    ),
    # System Status
    path(
        "projection-status/",
        ProjectionStatusView.as_view(),
        name="projection-status",
    ),
    path(
        "system-health/",
        SystemHealthView.as_view(),
        name="system-health",
    ),
    path(
        "month-end-close/",
        MonthEndCloseView.as_view(),
        name="month-end-close",
    ),
    # Dashboard
    path(
        "dashboard-charts/",
        DashboardChartsView.as_view(),
        name="dashboard-charts",
    ),
    path(
        "dashboard-widgets/",
        DashboardWidgetsView.as_view(),
        name="dashboard-widgets",
    ),
    # Admin Projection Management
    path(
        "admin/projections/",
        AdminProjectionListView.as_view(),
        name="admin-projection-list",
    ),
    path(
        "admin/projections/<str:name>/",
        AdminProjectionDetailView.as_view(),
        name="admin-projection-detail",
    ),
    path(
        "admin/projections/<str:name>/rebuild/",
        AdminProjectionRebuildView.as_view(),
        name="admin-projection-rebuild",
    ),
    path(
        "admin/projections/<str:name>/pause/",
        AdminProjectionPauseView.as_view(),
        name="admin-projection-pause",
    ),
    path(
        "admin/projections/<str:name>/clear-error/",
        AdminProjectionClearErrorView.as_view(),
        name="admin-projection-clear-error",
    ),
    path(
        "admin/projections/<str:name>/process/",
        AdminProjectionProcessView.as_view(),
        name="admin-projection-process",
    ),
]
