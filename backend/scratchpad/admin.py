# scratchpad/admin.py
from django.contrib import admin
from .models import ScratchpadRow, ScratchpadRowDimension, AccountDimensionRule


class ScratchpadRowDimensionInline(admin.TabularInline):
    model = ScratchpadRowDimension
    extra = 0
    readonly_fields = ["dimension", "dimension_value", "raw_value"]
    can_delete = False


@admin.register(ScratchpadRow)
class ScratchpadRowAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "public_id",
        "company",
        "status",
        "source",
        "transaction_date",
        "description",
        "amount",
        "debit_account",
        "credit_account",
        "created_at",
    ]
    list_filter = ["status", "source", "company"]
    search_fields = ["description", "notes", "public_id"]
    readonly_fields = [
        "public_id",
        "group_id",
        "created_at",
        "updated_at",
        "committed_at",
        "committed_by",
        "committed_event",
    ]
    inlines = [ScratchpadRowDimensionInline]
    raw_id_fields = ["company", "debit_account", "credit_account", "created_by"]
    ordering = ["-created_at"]


@admin.register(ScratchpadRowDimension)
class ScratchpadRowDimensionAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "scratchpad_row",
        "dimension",
        "dimension_value",
        "raw_value",
    ]
    list_filter = ["dimension"]
    raw_id_fields = ["scratchpad_row", "company", "dimension", "dimension_value"]


@admin.register(AccountDimensionRule)
class AccountDimensionRuleAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "company",
        "account",
        "dimension",
        "rule_type",
        "default_value",
    ]
    list_filter = ["rule_type", "company", "dimension"]
    search_fields = ["account__code", "account__name", "dimension__code"]
    raw_id_fields = ["company", "account", "dimension", "default_value"]
