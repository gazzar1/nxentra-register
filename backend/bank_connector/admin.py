# bank_connector/admin.py
from django.contrib import admin
from .models import BankAccount, BankStatement, BankTransaction, ReconciliationException


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ("account_name", "bank_name", "currency", "status", "company")
    list_filter = ("status", "bank_name")
    search_fields = ("account_name", "bank_name")


@admin.register(BankStatement)
class BankStatementAdmin(admin.ModelAdmin):
    list_display = (
        "filename", "bank_account", "period_start", "period_end",
        "transaction_count", "status", "created_at",
    )
    list_filter = ("status",)


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "transaction_date", "description", "amount", "transaction_type",
        "status", "bank_account",
    )
    list_filter = ("status", "transaction_type")
    search_fields = ("description", "reference")


@admin.register(ReconciliationException)
class ReconciliationExceptionAdmin(admin.ModelAdmin):
    list_display = (
        "title", "exception_type", "severity", "status",
        "platform", "amount", "exception_date", "company",
    )
    list_filter = ("status", "severity", "exception_type", "platform")
    search_fields = ("title", "description", "reference_label")
    readonly_fields = ("public_id", "created_at", "updated_at")
    raw_id_fields = ("company", "assigned_to", "resolved_by")
