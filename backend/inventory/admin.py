# inventory/admin.py
from django.contrib import admin
from .models import Warehouse, StockLedgerEntry


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "company", "is_active", "is_default"]
    list_filter = ["company", "is_active", "is_default"]
    search_fields = ["code", "name"]
    readonly_fields = ["public_id", "created_at", "updated_at"]


@admin.register(StockLedgerEntry)
class StockLedgerEntryAdmin(admin.ModelAdmin):
    list_display = [
        "sequence",
        "source_type",
        "item",
        "warehouse",
        "qty_delta",
        "unit_cost",
        "value_delta",
        "posted_at",
    ]
    list_filter = ["company", "source_type", "warehouse"]
    search_fields = ["item__code", "item__name"]
    readonly_fields = [
        "public_id",
        "sequence",
        "source_type",
        "source_id",
        "source_line_id",
        "item",
        "warehouse",
        "qty_delta",
        "unit_cost",
        "value_delta",
        "costing_method_snapshot",
        "qty_balance_after",
        "value_balance_after",
        "avg_cost_after",
        "posted_at",
        "posted_by",
        "journal_entry",
        "created_at",
    ]
    ordering = ["-sequence"]
