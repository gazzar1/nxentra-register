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
]
