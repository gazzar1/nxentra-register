# projections/urls.py
"""
URL configuration for projection/reports API.

All these endpoints read from projected data (materialized views),
not from computing on-the-fly.

Endpoints:
- /reports/trial-balance/ - Trial balance
- /reports/balance-sheet/ - Balance sheet
- /reports/income-statement/ - Income statement (P&L)
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
    TrialBalanceView,
    AccountBalanceListView,
    AccountBalanceDetailView,
    ProjectionStatusView,
    BalanceSheetView,
    IncomeStatementView,
    FiscalPeriodListView,
    FiscalPeriodCloseView,
    FiscalPeriodOpenView,
    FiscalPeriodsConfigureView,
    FiscalPeriodRangeView,
    FiscalPeriodCurrentView,
    FiscalPeriodDatesView,
    DashboardChartsView,
    SubledgerTieOutView,
    # Customer/Vendor balance views
    CustomerBalanceListView,
    CustomerBalanceDetailView,
    VendorBalanceListView,
    VendorBalanceDetailView,
    # Aging reports
    ARAgingReportView,
    APAgingReportView,
    # Account inquiry
    AccountInquiryView,
    # Admin projection management
    AdminProjectionListView,
    AdminProjectionDetailView,
    AdminProjectionRebuildView,
    AdminProjectionPauseView,
    AdminProjectionClearErrorView,
    AdminProjectionProcessView,
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

    # System Status
    path(
        "projection-status/",
        ProjectionStatusView.as_view(),
        name="projection-status",
    ),

    # Dashboard
    path(
        "dashboard-charts/",
        DashboardChartsView.as_view(),
        name="dashboard-charts",
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