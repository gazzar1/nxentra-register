# bank_connector/admin.py
from django.contrib import admin

from .models import BankAccount, BankStatement, BankTransaction, ReconciliationException


class ReadOnlyLegacyBankAdmin(admin.ModelAdmin):
    """A4: the legacy bank module is blocked under the pilot
    (Capability.LEGACY_BANKING) and its rows are preflight residue
    (legacy_bank_data). Admin-side writes would be a second, ungated writer of
    exactly that residue, so the legacy financial models are read-only here."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BankAccount)
class BankAccountAdmin(ReadOnlyLegacyBankAdmin):
    list_display = ("account_name", "bank_name", "currency", "status", "company")
    list_filter = ("status", "bank_name")
    search_fields = ("account_name", "bank_name")


@admin.register(BankStatement)
class BankStatementAdmin(ReadOnlyLegacyBankAdmin):
    list_display = (
        "filename",
        "bank_account",
        "period_start",
        "period_end",
        "transaction_count",
        "status",
        "created_at",
    )
    list_filter = ("status",)


@admin.register(BankTransaction)
class BankTransactionAdmin(ReadOnlyLegacyBankAdmin):
    list_display = (
        "transaction_date",
        "description",
        "amount",
        "transaction_type",
        "status",
        "bank_account",
    )
    list_filter = ("status", "transaction_type")
    search_fields = ("description", "reference")


@admin.register(ReconciliationException)
class ReconciliationExceptionAdmin(admin.ModelAdmin):
    # Deliberately NOT read-only: the exception queue is live operational
    # workflow state (assignment/resolution), not canonical financial data —
    # no JE or ledger row derives from it.
    list_display = (
        "title",
        "exception_type",
        "severity",
        "status",
        "platform",
        "amount",
        "exception_date",
        "company",
    )
    list_filter = ("status", "severity", "exception_type", "platform")
    search_fields = ("title", "description", "reference_label")
    readonly_fields = ("public_id", "created_at", "updated_at")
    raw_id_fields = ("company", "assigned_to", "resolved_by")
