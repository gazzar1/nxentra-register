# shopify_connector/serializers.py
from rest_framework import serializers

from .models import ShopifyOrder, ShopifyRefund, ShopifyStore


class ShopifyStoreSerializer(serializers.ModelSerializer):
    connected = serializers.SerializerMethodField()

    class Meta:
        model = ShopifyStore
        fields = [
            "id",
            "public_id",
            "shop_domain",
            "status",
            "webhooks_registered",
            "scopes",
            "last_sync_at",
            "error_message",
            "connected",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_connected(self, obj):
        return obj.status == ShopifyStore.Status.ACTIVE


class ShopifyOrderSerializer(serializers.ModelSerializer):
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
            "currency",
            "financial_status",
            "gateway",
            "order_date",
            "status",
            "journal_entry_id",
            "error_message",
            "created_at",
        ]
        read_only_fields = fields


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
