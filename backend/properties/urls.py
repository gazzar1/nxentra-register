# properties/urls.py
"""
URL configuration for property management API.
"""

from django.urls import path

from .views import (
    PropertyListCreateView,
    PropertyDetailView,
    UnitListCreateView,
    UnitDetailView,
    LesseeListCreateView,
    LesseeDetailView,
    LeaseListCreateView,
    LeaseDetailView,
    LeaseActivateView,
    LeaseTerminateView,
    LeaseRenewView,
    LeaseScheduleView,
    WaiveScheduleLineView,
    PaymentListCreateView,
    PaymentDetailView,
    PaymentAllocateView,
    PaymentAllocationsListView,
    PaymentVoidView,
    DepositListCreateView,
    ExpenseListCreateView,
    ExpenseDetailView,
    PropertyAccountMappingView,
)
from .reports import (
    RentRollView,
    OverdueBalancesView,
    LeaseExpiryReportView,
    OccupancySummaryView,
    MonthlyNetIncomeView,
    RentCollectionsView,
    ExpenseBreakdownView,
    DepositLiabilityView,
    PropertyDashboardView,
    PropertyAlertsView,
)

app_name = "properties"

urlpatterns = [
    # Properties
    path(
        "properties/",
        PropertyListCreateView.as_view(),
        name="property-list-create",
    ),
    path(
        "properties/<int:pk>/",
        PropertyDetailView.as_view(),
        name="property-detail",
    ),

    # Units
    path(
        "units/",
        UnitListCreateView.as_view(),
        name="unit-list-create",
    ),
    path(
        "units/<int:pk>/",
        UnitDetailView.as_view(),
        name="unit-detail",
    ),

    # Lessees
    path(
        "lessees/",
        LesseeListCreateView.as_view(),
        name="lessee-list-create",
    ),
    path(
        "lessees/<int:pk>/",
        LesseeDetailView.as_view(),
        name="lessee-detail",
    ),

    # Leases
    path(
        "leases/",
        LeaseListCreateView.as_view(),
        name="lease-list-create",
    ),
    path(
        "leases/<int:pk>/",
        LeaseDetailView.as_view(),
        name="lease-detail",
    ),
    path(
        "leases/<int:pk>/activate/",
        LeaseActivateView.as_view(),
        name="lease-activate",
    ),
    path(
        "leases/<int:pk>/terminate/",
        LeaseTerminateView.as_view(),
        name="lease-terminate",
    ),
    path(
        "leases/<int:pk>/renew/",
        LeaseRenewView.as_view(),
        name="lease-renew",
    ),
    path(
        "leases/<int:pk>/schedule/",
        LeaseScheduleView.as_view(),
        name="lease-schedule",
    ),

    # Schedule Line Actions
    path(
        "schedule-lines/<int:pk>/waive/",
        WaiveScheduleLineView.as_view(),
        name="schedule-line-waive",
    ),

    # Payments
    path(
        "payments/",
        PaymentListCreateView.as_view(),
        name="payment-list-create",
    ),
    path(
        "payments/<int:pk>/",
        PaymentDetailView.as_view(),
        name="payment-detail",
    ),
    path(
        "payments/<int:pk>/allocate/",
        PaymentAllocateView.as_view(),
        name="payment-allocate",
    ),
    path(
        "payments/<int:pk>/allocations/",
        PaymentAllocationsListView.as_view(),
        name="payment-allocations",
    ),
    path(
        "payments/<int:pk>/void/",
        PaymentVoidView.as_view(),
        name="payment-void",
    ),

    # Deposits
    path(
        "deposits/",
        DepositListCreateView.as_view(),
        name="deposit-list-create",
    ),

    # Expenses
    path(
        "expenses/",
        ExpenseListCreateView.as_view(),
        name="expense-list-create",
    ),
    path(
        "expenses/<int:pk>/",
        ExpenseDetailView.as_view(),
        name="expense-detail",
    ),

    # Account Mapping
    path(
        "account-mapping/",
        PropertyAccountMappingView.as_view(),
        name="account-mapping",
    ),

    # Reports
    path(
        "reports/rent-roll/",
        RentRollView.as_view(),
        name="report-rent-roll",
    ),
    path(
        "reports/overdue/",
        OverdueBalancesView.as_view(),
        name="report-overdue",
    ),
    path(
        "reports/expiry/",
        LeaseExpiryReportView.as_view(),
        name="report-expiry",
    ),
    path(
        "reports/occupancy/",
        OccupancySummaryView.as_view(),
        name="report-occupancy",
    ),
    path(
        "reports/income/",
        MonthlyNetIncomeView.as_view(),
        name="report-income",
    ),
    path(
        "reports/collections/",
        RentCollectionsView.as_view(),
        name="report-collections",
    ),
    path(
        "reports/expenses/",
        ExpenseBreakdownView.as_view(),
        name="report-expenses",
    ),
    path(
        "reports/deposits/",
        DepositLiabilityView.as_view(),
        name="report-deposits",
    ),

    # Dashboard
    path(
        "dashboard/",
        PropertyDashboardView.as_view(),
        name="dashboard",
    ),

    # Alerts
    path(
        "alerts/",
        PropertyAlertsView.as_view(),
        name="alerts",
    ),
]
