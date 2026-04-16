# sales/admin.py
from django.contrib import admin

from .models import Item, PostingProfile, SalesInvoice, SalesInvoiceLine, TaxCode


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "item_type", "company", "is_active"]
    list_filter = ["item_type", "is_active", "company"]
    search_fields = ["code", "name"]
    ordering = ["company", "code"]


@admin.register(TaxCode)
class TaxCodeAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "rate", "direction", "company", "is_active"]
    list_filter = ["direction", "is_active", "company"]
    search_fields = ["code", "name"]
    ordering = ["company", "code"]


@admin.register(PostingProfile)
class PostingProfileAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "profile_type", "control_account", "company", "is_default"]
    list_filter = ["profile_type", "is_default", "company"]
    search_fields = ["code", "name"]
    ordering = ["company", "code"]


class SalesInvoiceLineInline(admin.TabularInline):
    model = SalesInvoiceLine
    extra = 0
    readonly_fields = ["gross_amount", "net_amount", "tax_amount", "line_total"]


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(admin.ModelAdmin):
    list_display = ["invoice_number", "invoice_date", "customer", "total_amount", "status", "company"]
    list_filter = ["status", "company"]
    search_fields = ["invoice_number", "customer__name"]
    ordering = ["-invoice_date", "-created_at"]
    inlines = [SalesInvoiceLineInline]
    readonly_fields = ["subtotal", "total_discount", "total_tax", "total_amount", "posted_at", "posted_by"]
