# stripe_connector/admin.py
from django.contrib import admin

from .models import StripeAccount, StripeCharge, StripePayout, StripePayoutTransaction, StripeRefund


@admin.register(StripeAccount)
class StripeAccountAdmin(admin.ModelAdmin):
    list_display = ("stripe_account_id", "company", "display_name", "status", "livemode", "created_at")
    list_filter = ("status", "livemode")
    search_fields = ("stripe_account_id", "display_name")


@admin.register(StripeCharge)
class StripeChargeAdmin(admin.ModelAdmin):
    list_display = ("stripe_charge_id", "amount", "currency", "customer_email", "charge_date", "status")
    list_filter = ("status", "currency")
    search_fields = ("stripe_charge_id", "customer_email")


@admin.register(StripeRefund)
class StripeRefundAdmin(admin.ModelAdmin):
    list_display = ("stripe_refund_id", "amount", "currency", "reason", "status")
    list_filter = ("status",)


@admin.register(StripePayout)
class StripePayoutAdmin(admin.ModelAdmin):
    list_display = ("stripe_payout_id", "net_amount", "currency", "stripe_status", "payout_date")
    list_filter = ("stripe_status",)


@admin.register(StripePayoutTransaction)
class StripePayoutTransactionAdmin(admin.ModelAdmin):
    list_display = ("stripe_balance_txn_id", "transaction_type", "amount", "fee", "net", "verified")
    list_filter = ("transaction_type", "verified")
