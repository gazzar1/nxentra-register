# projections/admin.py
"""Django admin for projection models."""

from django.contrib import admin
from django.utils.html import format_html

from .models import AccountBalance, PeriodAccountBalance, ProjectionAppliedEvent


@admin.register(AccountBalance)
class AccountBalanceAdmin(admin.ModelAdmin):
    list_display = [
        "account_code", "account_name", "balance", 
        "debit_total", "credit_total", "entry_count", "company",
    ]
    list_filter = ["company", "account__account_type"]
    search_fields = ["account__code", "account__name"]
    list_select_related = ["company", "account"]
    ordering = ["company", "account__code"]
    readonly_fields = [
        "company", "account", "balance", "debit_total", "credit_total",
        "entry_count", "last_entry_date", "last_event", "created_at", "updated_at",
    ]
    
    def account_code(self, obj):
        return obj.account.code
    account_code.short_description = "Code"
    account_code.admin_order_field = "account__code"
    
    def account_name(self, obj):
        return obj.account.name
    account_name.short_description = "Name"

    def has_add_permission(self, request):
        return False  # Managed by projection
    
    def has_change_permission(self, request, obj=None):
        return False  # Managed by projection
    
    def has_delete_permission(self, request, obj=None):
        return False  # Managed by projection


@admin.register(PeriodAccountBalance)
class PeriodAccountBalanceAdmin(admin.ModelAdmin):
    list_display = [
        "account", "fiscal_year", "period", 
        "opening_balance", "closing_balance", "is_closed", "company",
    ]
    list_filter = ["company", "fiscal_year", "period", "is_closed"]
    list_select_related = ["company", "account"]
    ordering = ["company", "fiscal_year", "period", "account__code"]
    readonly_fields = [
        "company", "account", "fiscal_year", "period",
        "opening_balance", "period_debit", "period_credit", "closing_balance",
        "is_closed", "created_at", "updated_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ProjectionAppliedEvent)
class ProjectionAppliedEventAdmin(admin.ModelAdmin):
    list_display = ["projection_name", "event_id_short", "applied_at", "company"]
    list_filter = ["company", "projection_name"]
    list_select_related = ["company", "event"]
    ordering = ["-applied_at"]
    readonly_fields = ["company", "projection_name", "event", "applied_at"]
    
    def event_id_short(self, obj):
        return str(obj.event_id)[:8] + "..."
    event_id_short.short_description = "Event"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False