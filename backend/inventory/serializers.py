# inventory/serializers.py
"""
Serializers for inventory API endpoints.
"""

from rest_framework import serializers

from projections.models import InventoryBalance

from .models import StockLedgerEntry, Warehouse


class WarehouseSerializer(serializers.ModelSerializer):
    """Serializer for Warehouse model."""

    class Meta:
        model = Warehouse
        fields = [
            "id",
            "public_id",
            "code",
            "name",
            "name_ar",
            "address",
            "is_active",
            "is_default",
            "platform",
            "platform_location_id",
            "is_platform_managed",
            "last_synced_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "platform",
            "platform_location_id",
            "is_platform_managed",
            "last_synced_at",
            "created_at",
            "updated_at",
        ]


class WarehouseCreateSerializer(serializers.Serializer):
    """Serializer for creating a warehouse."""

    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=255)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    address = serializers.CharField(required=False, allow_blank=True, default="")
    is_default = serializers.BooleanField(required=False, default=False)


class WarehouseUpdateSerializer(serializers.Serializer):
    """Serializer for updating a warehouse."""

    name = serializers.CharField(max_length=255, required=False)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)
    is_default = serializers.BooleanField(required=False)


class InventoryBalanceSerializer(serializers.ModelSerializer):
    """Serializer for InventoryBalance projection."""

    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    item_public_id = serializers.UUIDField(source="item.public_id", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    warehouse_public_id = serializers.UUIDField(source="warehouse.public_id", read_only=True)

    class Meta:
        model = InventoryBalance
        fields = [
            "id",
            "item_public_id",
            "item_code",
            "item_name",
            "warehouse_public_id",
            "warehouse_code",
            "warehouse_name",
            "qty_on_hand",
            "avg_cost",
            "stock_value",
            "entry_count",
            "last_entry_date",
            "created_at",
            "updated_at",
        ]


class StockLedgerEntrySerializer(serializers.ModelSerializer):
    """Serializer for StockLedgerEntry model."""

    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    item_public_id = serializers.UUIDField(source="item.public_id", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    warehouse_public_id = serializers.UUIDField(source="warehouse.public_id", read_only=True)
    posted_by_email = serializers.CharField(source="posted_by.email", read_only=True)
    journal_entry_public_id = serializers.UUIDField(source="journal_entry.public_id", read_only=True, allow_null=True)

    class Meta:
        model = StockLedgerEntry
        fields = [
            "id",
            "public_id",
            "sequence",
            "source_type",
            "source_id",
            "source_line_id",
            "item_public_id",
            "item_code",
            "item_name",
            "warehouse_public_id",
            "warehouse_code",
            "warehouse_name",
            "qty_delta",
            "unit_cost",
            "value_delta",
            "costing_method_snapshot",
            "qty_balance_after",
            "value_balance_after",
            "avg_cost_after",
            "posted_at",
            "posted_by_email",
            "journal_entry_public_id",
            "created_at",
        ]


class StockAvailabilitySerializer(serializers.Serializer):
    """Serializer for stock availability check result."""

    item_public_id = serializers.UUIDField()
    item_code = serializers.CharField()
    warehouse_public_id = serializers.UUIDField()
    warehouse_code = serializers.CharField()
    qty_on_hand = serializers.DecimalField(max_digits=18, decimal_places=4)
    qty_requested = serializers.DecimalField(max_digits=18, decimal_places=4)
    is_available = serializers.BooleanField()


class AdjustmentLineSerializer(serializers.Serializer):
    """Serializer for a single adjustment line."""

    item_id = serializers.IntegerField()
    warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    qty_delta = serializers.DecimalField(max_digits=18, decimal_places=4)
    unit_cost = serializers.DecimalField(max_digits=18, decimal_places=6, required=False, allow_null=True)


class InventoryAdjustmentSerializer(serializers.Serializer):
    """Serializer for creating an inventory adjustment."""

    adjustment_date = serializers.DateField()
    reason = serializers.CharField(max_length=255)
    adjustment_account_id = serializers.IntegerField()
    lines = AdjustmentLineSerializer(many=True)

    def validate_lines(self, value):
        if not value:
            raise serializers.ValidationError("At least one line is required.")
        return value


class OpeningBalanceLineSerializer(serializers.Serializer):
    """Serializer for a single opening balance line."""

    item_id = serializers.IntegerField()
    warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    qty = serializers.DecimalField(max_digits=18, decimal_places=4)
    unit_cost = serializers.DecimalField(max_digits=18, decimal_places=6)


class InventoryOpeningBalanceSerializer(serializers.Serializer):
    """Serializer for recording inventory opening balances."""

    as_of_date = serializers.DateField()
    opening_balance_equity_account_id = serializers.IntegerField()
    lines = OpeningBalanceLineSerializer(many=True)

    def validate_lines(self, value):
        if not value:
            raise serializers.ValidationError("At least one line is required.")
        return value
