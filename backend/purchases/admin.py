# purchases/admin.py
from django.contrib import admin
from .models import PurchaseBill, PurchaseBillLine


class PurchaseBillLineInline(admin.TabularInline):
    model = PurchaseBillLine
    extra = 0
    readonly_fields = ["gross_amount", "net_amount", "tax_amount", "line_total"]


@admin.register(PurchaseBill)
class PurchaseBillAdmin(admin.ModelAdmin):
    list_display = ["bill_number", "bill_date", "vendor", "total_amount", "status", "company"]
    list_filter = ["status", "company"]
    search_fields = ["bill_number", "vendor__name"]
    ordering = ["-bill_date", "-created_at"]
    inlines = [PurchaseBillLineInline]
    readonly_fields = ["subtotal", "total_discount", "total_tax", "total_amount", "posted_at", "posted_by"]
