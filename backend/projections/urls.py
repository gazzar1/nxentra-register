# projections/urls.py
"""
URL configuration for projection/reports API.

All these endpoints read from projected data (materialized views),
not from computing on-the-fly.

Endpoints:
- /reports/trial-balance/ - Trial balance
- /reports/balance-sheet/ - Balance sheet
- /reports/income-statement/ - Income statement (P&L)
- /reports/account-balances/ - All account balances
- /reports/account-balances/<code>/ - Single account balance
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