# accounting/admin.py
"""
Django admin configuration for accounting models.

IMPORTANT: These are READ MODELS.
==============================
All accounting models are event-sourced read models. The admin interface
is for viewing only. All mutations MUST go through the command layer
(accounting/commands.py), which emits events.

The admin is intentionally locked down to prevent bypassing the event system.
"""

from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Account,
    JournalEntry,
    JournalLine,
    AnalysisDimension,
    AnalysisDimensionValue,
    JournalLineAnalysis,
    AccountAnalysisDefault,
)


class ReadOnlyModelAdmin(admin.ModelAdmin):
    """
    Base admin class for read-only models.

    All accounting models are read models (event-sourced projections).
    Direct admin edits bypass the event system and corrupt data integrity.

    To modify these models, use the command layer (accounting/commands.py).
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["show_save"] = False
        extra_context["show_save_and_continue"] = False
        extra_context["show_save_and_add_another"] = False
        extra_context["readonly_message"] = (
            "This is a read model. Use the API/command layer to make changes."
        )
        return super().changeform_view(request, object_id, form_url, extra_context)


class ReadOnlyInline(admin.TabularInline):
    """Base inline class for read-only models."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# =============================================================================
# Inline Admin Classes
# =============================================================================

class JournalLineInline(ReadOnlyInline):
    """Inline display of journal lines within journal entry (read-only)."""
    model = JournalLine
    extra = 0
    readonly_fields = ["line_no", "account", "description", "debit", "credit"]
    fields = ["line_no", "account", "description", "debit", "credit"]


class AnalysisDimensionValueInline(ReadOnlyInline):
    """Inline display of dimension values within dimension (read-only)."""
    model = AnalysisDimensionValue
    extra = 0
    readonly_fields = ["code", "name", "name_ar", "parent", "is_active"]
    fields = ["code", "name", "name_ar", "parent", "is_active"]


class AccountAnalysisDefaultInline(ReadOnlyInline):
    """Inline display of analysis defaults within account (read-only)."""
    model = AccountAnalysisDefault
    extra = 0
    readonly_fields = ["dimension", "default_value"]
    fields = ["dimension", "default_value"]


class JournalLineAnalysisInline(ReadOnlyInline):
    """Inline display of analysis tags on journal lines (read-only)."""
    model = JournalLineAnalysis
    extra = 0
    readonly_fields = ["dimension", "dimension_value"]
    fields = ["dimension", "dimension_value"]


# =============================================================================
# Account Admin
# =============================================================================

@admin.register(Account)
class AccountAdmin(ReadOnlyModelAdmin):
    """Admin interface for Chart of Accounts (read-only)."""

    list_display = [
        "code", "name", "account_type", "normal_balance",
        "status", "is_header", "parent", "company",
    ]
    list_filter = ["company", "account_type", "status", "is_header"]
    search_fields = ["code", "name", "name_ar", "description"]
    list_select_related = ["company", "parent"]
    ordering = ["company", "code"]

    fieldsets = (
        (None, {
            "fields": ("company", "code", "name", "name_ar"),
        }),
        ("Classification", {
            "fields": ("account_type", "normal_balance", "status", "is_header"),
        }),
        ("Hierarchy", {
            "fields": ("parent",),
        }),
        ("Description", {
            "fields": ("description", "description_ar"),
        }),
        ("Memo Account Settings", {
            "fields": ("unit_of_measure",),
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    readonly_fields = [
        "company", "code", "name", "name_ar", "account_type", "normal_balance",
        "status", "is_header", "parent", "description", "description_ar",
        "unit_of_measure", "created_at", "updated_at",
    ]
    inlines = [AccountAnalysisDefaultInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("company", "parent")


# =============================================================================
# Journal Entry Admin
# =============================================================================

@admin.register(JournalEntry)
class JournalEntryAdmin(ReadOnlyModelAdmin):
    """Admin interface for Journal Entries (read-only)."""

    list_display = [
        "id", "entry_number", "date", "memo_truncated", "kind",
        "status_colored", "total_debit", "total_credit", "company",
    ]
    list_filter = ["company", "status", "kind", "date"]
    search_fields = ["entry_number", "memo", "memo_ar"]
    date_hierarchy = "date"
    list_select_related = ["company", "posted_by", "created_by"]
    ordering = ["-date", "-id"]

    fieldsets = (
        (None, {
            "fields": ("company", "entry_number", "date", "period"),
        }),
        ("Content", {
            "fields": ("memo", "memo_ar", "kind"),
        }),
        ("Status & Workflow", {
            "fields": ("status", "posted_at", "posted_by", "reversed_at", "reversed_by"),
        }),
        ("Source", {
            "fields": ("source_module", "source_document", "reverses_entry"),
            "classes": ("collapse",),
        }),
        ("Audit", {
            "fields": ("created_at", "created_by", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    readonly_fields = [
        "company", "entry_number", "date", "period", "memo", "memo_ar", "kind",
        "status", "posted_at", "posted_by", "reversed_at", "reversed_by",
        "source_module", "source_document", "reverses_entry",
        "created_at", "created_by", "updated_at",
    ]
    inlines = [JournalLineInline]

    def memo_truncated(self, obj):
        """Truncate memo for list display."""
        if len(obj.memo) > 50:
            return f"{obj.memo[:50]}..."
        return obj.memo
    memo_truncated.short_description = "Memo"

    def status_colored(self, obj):
        """Show status with color coding."""
        colors = {
            JournalEntry.Status.INCOMPLETE: "#999",
            JournalEntry.Status.DRAFT: "#007bff",
            JournalEntry.Status.POSTED: "#28a745",
            JournalEntry.Status.REVERSED: "#dc3545",
        }
        color = colors.get(obj.status, "#000")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )
    status_colored.short_description = "Status"
    status_colored.admin_order_field = "status"


@admin.register(JournalLine)
class JournalLineAdmin(ReadOnlyModelAdmin):
    """Admin interface for Journal Lines (read-only)."""

    list_display = [
        "entry", "line_no", "account", "description_truncated",
        "debit", "credit", "entry_status",
    ]
    list_filter = ["entry__status", "entry__company", "account__account_type"]
    search_fields = ["description", "account__code", "account__name"]
    list_select_related = ["entry", "account"]
    ordering = ["entry", "line_no"]

    readonly_fields = ["entry", "line_no", "account", "description", "description_ar", "debit", "credit"]
    inlines = [JournalLineAnalysisInline]

    def description_truncated(self, obj):
        if len(obj.description) > 40:
            return f"{obj.description[:40]}..."
        return obj.description
    description_truncated.short_description = "Description"

    def entry_status(self, obj):
        return obj.entry.status
    entry_status.short_description = "Entry Status"
    entry_status.admin_order_field = "entry__status"


# =============================================================================
# Analysis Dimension Admin
# =============================================================================

@admin.register(AnalysisDimension)
class AnalysisDimensionAdmin(ReadOnlyModelAdmin):
    """Admin interface for Analysis Dimensions (read-only)."""

    list_display = [
        "code", "name", "is_required_on_posting", "is_active",
        "value_count", "display_order", "company",
    ]
    list_filter = ["company", "is_required_on_posting", "is_active"]
    search_fields = ["code", "name", "name_ar"]
    list_select_related = ["company"]
    ordering = ["company", "display_order", "code"]

    fieldsets = (
        (None, {
            "fields": ("company", "code", "name", "name_ar"),
        }),
        ("Description", {
            "fields": ("description", "description_ar"),
        }),
        ("Configuration", {
            "fields": ("is_required_on_posting", "applies_to_account_types", "display_order", "is_active"),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    readonly_fields = [
        "company", "code", "name", "name_ar", "description", "description_ar",
        "is_required_on_posting", "applies_to_account_types", "display_order",
        "is_active", "created_at", "updated_at",
    ]
    inlines = [AnalysisDimensionValueInline]

    def value_count(self, obj):
        return obj.values.count()
    value_count.short_description = "Values"


@admin.register(AnalysisDimensionValue)
class AnalysisDimensionValueAdmin(ReadOnlyModelAdmin):
    """Admin interface for Dimension Values (read-only)."""

    list_display = [
        "code", "name", "dimension", "parent", "is_active", "full_path",
    ]
    list_filter = ["dimension__company", "dimension", "is_active"]
    search_fields = ["code", "name", "name_ar"]
    list_select_related = ["dimension", "parent"]
    ordering = ["dimension", "code"]

    fieldsets = (
        (None, {
            "fields": ("dimension", "code", "name", "name_ar"),
        }),
        ("Hierarchy", {
            "fields": ("parent",),
        }),
        ("Description", {
            "fields": ("description", "description_ar"),
        }),
        ("Status", {
            "fields": ("is_active",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    readonly_fields = [
        "dimension", "code", "name", "name_ar", "parent",
        "description", "description_ar", "is_active",
        "created_at", "updated_at",
    ]


@admin.register(JournalLineAnalysis)
class JournalLineAnalysisAdmin(ReadOnlyModelAdmin):
    """Admin interface for Journal Line Analysis (read-only)."""

    list_display = [
        "journal_line", "dimension", "dimension_value",
    ]
    list_filter = ["dimension"]
    list_select_related = ["journal_line", "dimension", "dimension_value"]
    readonly_fields = ["journal_line", "dimension", "dimension_value"]


@admin.register(AccountAnalysisDefault)
class AccountAnalysisDefaultAdmin(ReadOnlyModelAdmin):
    """Admin interface for Account Analysis Defaults (read-only)."""

    list_display = [
        "account", "dimension", "default_value",
    ]
    list_filter = ["dimension", "account__company"]
    list_select_related = ["account", "dimension", "default_value"]
    readonly_fields = ["account", "dimension", "default_value"]