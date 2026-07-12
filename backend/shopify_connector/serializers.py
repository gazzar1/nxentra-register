# shopify_connector/serializers.py
from rest_framework import serializers

from .models import ShopifyOrder, ShopifyRefund, ShopifyStore


class ShopifyStoreSerializer(serializers.ModelSerializer):
    connected = serializers.SerializerMethodField()
    default_cod_settlement_provider_id = serializers.IntegerField(read_only=True)
    default_cod_settlement_provider_code = serializers.CharField(
        source="default_cod_settlement_provider.normalized_code",
        read_only=True,
        default=None,
    )
    default_cod_settlement_provider_name = serializers.CharField(
        source="default_cod_settlement_provider.display_name",
        read_only=True,
        default=None,
    )

    class Meta:
        model = ShopifyStore
        fields = [
            "id",
            "public_id",
            "shop_domain",
            "status",
            "scopes",
            "last_sync_at",
            "error_message",
            "uninstalled_at",
            "needs_reauth",
            "connected",
            "default_cod_settlement_provider_id",
            "default_cod_settlement_provider_code",
            "default_cod_settlement_provider_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_connected(self, obj):
        return obj.status == ShopifyStore.Status.ACTIVE


class ShopifyOrderSerializer(serializers.ModelSerializer):
    journal_entry_pk = serializers.SerializerMethodField()
    journal_entry_number = serializers.SerializerMethodField()
    total_refunded = serializers.SerializerMethodField()

    class Meta:
        model = ShopifyOrder
        fields = [
            "id",
            "public_id",
            "shopify_order_id",
            "shopify_order_number",
            "shopify_order_name",
            "total_price",
            "subtotal_price",
            "total_tax",
            "total_discounts",
            "total_refunded",
            "currency",
            "financial_status",
            "gateway",
            "order_date",
            "status",
            "journal_entry_id",
            "journal_entry_pk",
            "journal_entry_number",
            "error_message",
            "created_at",
        ]
        read_only_fields = fields

    def get_total_refunded(self, obj):
        # F11: from the queryset annotation; falls back to a direct sum when the
        # serializer is used on an un-annotated instance.
        annotated = getattr(obj, "total_refunded", None)
        if annotated is not None:
            return str(annotated)
        from django.db.models import Sum

        total = obj.refunds.aggregate(t=Sum("amount"))["t"]
        return str(total or "0.00")

    def get_journal_entry_pk(self, obj):
        if not obj.journal_entry_id:
            return None
        from accounting.models import JournalEntry

        je = (
            JournalEntry.objects.filter(
                company=obj.company,
                public_id=obj.journal_entry_id,
            )
            .only("id")
            .first()
        )
        return je.id if je else None

    def get_journal_entry_number(self, obj):
        if not obj.journal_entry_id:
            return None
        from accounting.models import JournalEntry

        je = (
            JournalEntry.objects.filter(
                company=obj.company,
                public_id=obj.journal_entry_id,
            )
            .only("entry_number")
            .first()
        )
        return je.entry_number if je else None


class ShopifyRefundSerializer(serializers.ModelSerializer):
    order_name = serializers.CharField(
        source="order.shopify_order_name",
        read_only=True,
    )

    class Meta:
        model = ShopifyRefund
        fields = [
            "id",
            "public_id",
            "shopify_refund_id",
            "order_name",
            "amount",
            "currency",
            "reason",
            "status",
            "journal_entry_id",
            "error_message",
            "created_at",
        ]
        read_only_fields = fields
