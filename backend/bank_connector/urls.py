# bank_connector/urls.py
from django.urls import path

from . import views

urlpatterns = [
    # Bank accounts
    path("accounts/", views.BankAccountListCreateView.as_view(), name="bank-accounts"),
    path("accounts/<int:pk>/", views.BankAccountDetailView.as_view(), name="bank-account-detail"),

    # CSV import
    path("import/preview/", views.BankStatementPreviewView.as_view(), name="bank-import-preview"),
    path("import/", views.BankStatementImportView.as_view(), name="bank-import"),

    # Statements
    path("statements/", views.BankStatementListView.as_view(), name="bank-statements"),

    # Transactions
    path("transactions/", views.BankTransactionListView.as_view(), name="bank-transactions"),
    path("transactions/<int:pk>/", views.BankTransactionUpdateView.as_view(), name="bank-transaction-detail"),

    # Summary
    path("summary/", views.BankSummaryView.as_view(), name="bank-summary"),

    # Reconciliation
    path("reconciliation/overview/", views.ReconciliationOverviewView.as_view(), name="recon-overview"),
    path("reconciliation/auto-match/", views.AutoMatchView.as_view(), name="recon-auto-match"),
    path("reconciliation/suggestions/<int:pk>/", views.MatchSuggestionsView.as_view(), name="recon-suggestions"),
    path("reconciliation/match/", views.ManualMatchView.as_view(), name="recon-manual-match"),
    path("reconciliation/explain/<str:platform>/<int:pk>/", views.PayoutExplainerView.as_view(), name="recon-explain"),
    path("reconciliation/unmatched-payouts/", views.UnmatchedPayoutsView.as_view(), name="recon-unmatched-payouts"),

    # Exception queue
    path("exceptions/", views.ExceptionListView.as_view(), name="exceptions-list"),
    path("exceptions/scan/", views.ExceptionScanView.as_view(), name="exceptions-scan"),
    path("exceptions/summary/", views.ExceptionSummaryView.as_view(), name="exceptions-summary"),
    path("exceptions/<int:pk>/", views.ExceptionDetailView.as_view(), name="exceptions-detail"),
]
