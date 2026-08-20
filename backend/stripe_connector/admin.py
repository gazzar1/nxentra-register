# stripe_connector/admin.py
from django.contrib import admin

from .models import StripeAccount, StripeCharge, StripePayout, StripePayoutTransaction, StripeRefund


class ReadOnlyConnectorAdmin(admin.ModelAdmin):
    """A4: Stripe rows are written only by the gated connector commands/sync
    (Capability.STRIPE — blocked under the pilot). Admin-side writes would be a
    second, ungated writer whose residue can only be caught after the fact by
    preflight (stripe_connected), so the admin surface is read-only."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StripeAccount)
class StripeAccountAdmin(ReadOnlyConnectorAdmin):
    list_display = ("stripe_account_id", "company", "display_name", "status", "livemode", "created_at")
    list_filter = ("status", "livemode")
    search_fields = ("stripe_account_id", "display_name")
    # Never surface the encrypted secrets in the admin change form — they would
    # render DECRYPTED (EncryptedTextField.from_db_value) and be editable in
    # plaintext. They are write-only via the API; keep them out of admin too.
    exclude = ("webhook_secret", "credential_ref")


@admin.register(StripeCharge)
class StripeChargeAdmin(ReadOnlyConnectorAdmin):
    list_display = ("stripe_charge_id", "amount", "currency", "customer_email", "charge_date", "status")
    list_filter = ("status", "currency")
    search_fields = ("stripe_charge_id", "customer_email")


@admin.register(StripeRefund)
class StripeRefundAdmin(ReadOnlyConnectorAdmin):
    list_display = ("stripe_refund_id", "amount", "currency", "reason", "status")
    list_filter = ("status",)


@admin.register(StripePayout)
class StripePayoutAdmin(ReadOnlyConnectorAdmin):
    list_display = ("stripe_payout_id", "net_amount", "currency", "stripe_status", "payout_date")
    list_filter = ("stripe_status",)


@admin.register(StripePayoutTransaction)
class StripePayoutTransactionAdmin(ReadOnlyConnectorAdmin):
    list_display = ("stripe_balance_txn_id", "transaction_type", "amount", "fee", "net", "verified")
    list_filter = ("transaction_type", "verified")
