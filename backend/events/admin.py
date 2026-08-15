# events/admin.py
"""
Django admin configuration for event store models.

Events are read-only in admin (they're immutable).
Bookmarks can be managed for debugging projections.
"""

import json

from django.contrib import admin
from django.utils.html import format_html

from .models import BusinessEvent, EventBookmark


@admin.register(BusinessEvent)
class BusinessEventAdmin(admin.ModelAdmin):
    """
    Admin interface for BusinessEvents.
    Read-only since events are immutable.
    """

    list_display = [
        "id_short",
        "event_type",
        "aggregate_display",
        "caused_by_user",
        "occurred_at",
        "company",
    ]
    list_filter = [
        "company",
        "event_type",
        "aggregate_type",
        "occurred_at",
        "external_source",
    ]
    search_fields = [
        "event_type",
        "aggregate_type",
        "caused_by_user__email",
        "external_id",
    ]
    date_hierarchy = "occurred_at"
    list_select_related = ["company", "caused_by_user"]
    ordering = ["-occurred_at"]

    readonly_fields = [
        "id",
        "company",
        "event_type",
        "aggregate_type",
        "aggregate_id",
        "sequence",
        "data_formatted",
        "metadata_formatted",
        "schema_version",
        "caused_by_user",
        "caused_by_event",
        "external_source",
        "external_id",
        "occurred_at",
    ]

    fieldsets = (
        (
            "Event Identity",
            {
                "fields": ("id", "event_type", "schema_version"),
            },
        ),
        (
            "Aggregate",
            {
                "fields": ("aggregate_type", "aggregate_id", "sequence"),
            },
        ),
        (
            "Payload",
            {
                "fields": ("data_formatted",),
            },
        ),
        (
            "Context",
            {
                "fields": ("company", "caused_by_user", "caused_by_event"),
            },
        ),
        (
            "Metadata",
            {
                "fields": ("metadata_formatted",),
                "classes": ("collapse",),
            },
        ),
        (
            "External Source",
            {
                "fields": ("external_source", "external_id"),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamp",
            {
                "fields": ("occurred_at",),
            },
        ),
    )

    def id_short(self, obj):
        """Display shortened UUID."""
        return str(obj.id)[:8] + "..."

    id_short.short_description = "ID"

    def aggregate_display(self, obj):
        """Display aggregate type and ID together."""
        return f"{obj.aggregate_type}#{obj.aggregate_id}"

    aggregate_display.short_description = "Aggregate"

    def data_formatted(self, obj):
        """Format JSON data for display."""
        return format_html(
            "<pre style='white-space: pre-wrap; max-width: 600px;'>{}</pre>",
            json.dumps(obj.data, indent=2, default=str),
        )

    data_formatted.short_description = "Data"

    def metadata_formatted(self, obj):
        """Format JSON metadata for display."""
        return format_html(
            "<pre style='white-space: pre-wrap; max-width: 600px;'>{}</pre>",
            json.dumps(obj.metadata, indent=2, default=str),
        )

    metadata_formatted.short_description = "Metadata"

    def has_add_permission(self, request):
        return False  # Events created through commands only

    def has_change_permission(self, request, obj=None):
        return False  # Events are immutable

    def has_delete_permission(self, request, obj=None):
        return False  # Events are immutable


@admin.register(EventBookmark)
class EventBookmarkAdmin(admin.ModelAdmin):
    """
    Admin interface for EventBookmarks — operator-VISIBLE, not operator-MUTABLE.

    A4: a bookmark is projection PROGRESS. Rewinding ``last_event`` (or pausing a
    consumer) re-applies or halts canonical history — a ``Capability.PROJECTION_REBUILD``
    bypass — so every field is read-only and add/change/delete (incl. the bulk-delete
    action) and every progress-mutating custom action are prohibited here. Projection
    progress is managed through the sanctioned command/runtime paths, not the admin.
    """

    list_display = [
        "consumer_name",
        "company",
        "last_event_short",
        "last_processed_at",
        "is_paused",
        "error_count",
    ]
    list_filter = ["company", "is_paused", "consumer_name"]
    search_fields = ["consumer_name"]
    list_select_related = ["company", "last_event"]
    ordering = ["consumer_name", "company"]

    readonly_fields = [
        "consumer_name",
        "company",
        "last_event",
        "last_processed_at",
        "is_paused",
        "error_count",
        "last_error",
        "created_at",
        "updated_at",
    ]

    # A4: no mutating custom actions; disabling delete permission also removes the
    # default bulk-delete action.
    actions = None

    def last_event_short(self, obj):
        """Display shortened last event ID."""
        if obj.last_event:
            return str(obj.last_event.id)[:8] + "..."
        return "-"

    last_event_short.short_description = "Last Event"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
