# scratchpad/admin.py
from django.contrib import admin
from django.db.models import Count, Sum
from django.utils.html import format_html

from .models import AccountDimensionRule, ScratchpadRow, ScratchpadRowDimension, VoiceUsageEvent


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


@admin.register(VoiceUsageEvent)
class VoiceUsageEventAdmin(admin.ModelAdmin):
    """
    Admin view for voice usage tracking.

    This is an APPEND-ONLY table - no editing allowed.
    Use filters to analyze usage by company, user, date range.
    """

    list_display = [
        "id",
        "company",
        "user_email",
        "audio_duration_display",
        "transcript_chars",
        "transactions_parsed",
        "total_cost_display",
        "success_display",
        "created_at",
    ]
    list_filter = [
        "success",
        "company",
        "asr_model",
        "parse_model",
        ("created_at", admin.DateFieldListFilter),
    ]
    search_fields = ["user__email", "error_message"]
    readonly_fields = [
        "public_id",
        "company",
        "user",
        "scratchpad_row",
        "audio_seconds",
        "transcript_chars",
        "asr_model",
        "parse_model",
        "asr_input_tokens",
        "parse_input_tokens",
        "parse_output_tokens",
        "asr_cost_usd",
        "parse_cost_usd",
        "success",
        "error_message",
        "transactions_parsed",
        "created_at",
    ]
    ordering = ["-created_at"]
    date_hierarchy = "created_at"
    raw_id_fields = ["company", "user", "scratchpad_row"]

    # No editing/deleting - append-only table
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def user_email(self, obj):
        return obj.user.email if obj.user else "-"

    user_email.short_description = "User"
    user_email.admin_order_field = "user__email"

    def audio_duration_display(self, obj):
        if obj.audio_seconds:
            minutes = int(obj.audio_seconds // 60)
            seconds = int(obj.audio_seconds % 60)
            return f"{minutes}:{seconds:02d}"
        return "-"

    audio_duration_display.short_description = "Duration"
    audio_duration_display.admin_order_field = "audio_seconds"

    def total_cost_display(self, obj):
        total = obj.total_cost_usd
        if total:
            return f"${total:.4f}"
        return "-"

    total_cost_display.short_description = "Cost"

    def success_display(self, obj):
        if obj.success:
            return format_html('<span style="color: green;">&#10004;</span>')
        return format_html('<span style="color: red;">&#10008;</span>')

    success_display.short_description = "OK"
    success_display.admin_order_field = "success"

    def changelist_view(self, request, extra_context=None):
        """Add summary stats to the changelist view."""
        from decimal import Decimal

        # Get the filtered queryset
        response = super().changelist_view(request, extra_context)

        try:
            qs = response.context_data["cl"].queryset
        except (AttributeError, KeyError):
            return response

        # Calculate aggregates
        aggregates = qs.aggregate(
            total_requests=Count("id"),
            total_audio_seconds=Sum("audio_seconds"),
            total_asr_cost=Sum("asr_cost_usd"),
            total_parse_cost=Sum("parse_cost_usd"),
            total_transactions=Sum("transactions_parsed"),
        )

        total_cost = (aggregates["total_asr_cost"] or Decimal("0")) + (aggregates["total_parse_cost"] or Decimal("0"))
        audio_minutes = (aggregates["total_audio_seconds"] or Decimal("0")) / Decimal("60")

        response.context_data["summary"] = {
            "total_requests": aggregates["total_requests"],
            "total_audio_minutes": f"{audio_minutes:.1f}",
            "total_cost": f"${total_cost:.4f}",
            "total_transactions": aggregates["total_transactions"] or 0,
        }

        return response
