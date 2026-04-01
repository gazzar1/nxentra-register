# purchases/serializers.py
"""
Serializers for purchases API.
"""

from decimal import Decimal

from rest_framework import serializers

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseBill,
    PurchaseBillLine,
    PurchaseOrder,
    PurchaseOrderLine,
)

# =============================================================================
# Purchase Bill Serializers
# =============================================================================

class PurchaseBillLineSerializer(serializers.ModelSerializer):
    """Serializer for PurchaseBillLine model."""
    item_code = serializers.CharField(source="item.code", read_only=True, default=None)
    account_code = serializers.CharField(source="account.code", read_only=True)
    tax_code_code = serializers.CharField(source="tax_code.code", read_only=True, default=None)

    class Meta:
        model = PurchaseBillLine
        fields = [
            "id", "public_id", "line_number",
            "item", "item_code", "description", "description_ar",
            "quantity", "unit_price", "discount_amount",
            "tax_code", "tax_code_code", "tax_rate",
            "gross_amount", "net_amount", "tax_amount", "line_total",
            "account", "account_code",
        ]
        read_only_fields = [
            "id", "public_id", "item_code", "account_code", "tax_code_code",
            "gross_amount", "net_amount", "tax_amount", "line_total",
        ]


class PurchaseBillSerializer(serializers.ModelSerializer):
    """Serializer for PurchaseBill model."""
    lines = PurchaseBillLineSerializer(many=True, read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    vendor_code = serializers.CharField(source="vendor.code", read_only=True)
    posting_profile_code = serializers.CharField(source="posting_profile.code", read_only=True)
    posted_by_email = serializers.CharField(source="posted_by.email", read_only=True, default=None)
    posted_journal_entry_number = serializers.CharField(
        source="posted_journal_entry.entry_number", read_only=True, default=None
    )

    class Meta:
        model = PurchaseBill
        fields = [
            "id", "public_id", "bill_number", "bill_date", "due_date",
            "vendor", "vendor_name", "vendor_code",
            "posting_profile", "posting_profile_code",
            "currency", "exchange_rate",
            "subtotal", "total_discount", "total_tax", "total_amount",
            "status", "posted_at", "posted_by", "posted_by_email",
            "posted_journal_entry", "posted_journal_entry_number",
            "notes", "reference",
            "created_at", "created_by", "updated_at",
            "lines",
        ]
        read_only_fields = [
            "id", "public_id",
            "vendor_name", "vendor_code", "posting_profile_code",
            "currency", "exchange_rate",
            "subtotal", "total_discount", "total_tax", "total_amount",
            "status", "posted_at", "posted_by", "posted_by_email",
            "posted_journal_entry", "posted_journal_entry_number",
            "created_at", "created_by", "updated_at",
            "lines",
        ]


class PurchaseBillLineInputSerializer(serializers.Serializer):
    """Serializer for input line data when creating/updating bills."""
    account_id = serializers.IntegerField()
    description = serializers.CharField(max_length=500)
    description_ar = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4, default=Decimal("1"))
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=2)
    discount_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default=Decimal("0"))
    tax_code_id = serializers.IntegerField(required=False, allow_null=True)
    item_id = serializers.IntegerField(required=False, allow_null=True)
    dimension_value_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )


class PurchaseBillCreateSerializer(serializers.Serializer):
    """Serializer for creating purchase bills via command."""
    bill_number = serializers.CharField(max_length=50, required=False, default="")
    bill_date = serializers.DateField()
    due_date = serializers.DateField(required=False, allow_null=True)
    vendor_id = serializers.IntegerField()
    posting_profile_id = serializers.IntegerField()
    currency = serializers.CharField(max_length=3, required=False, allow_blank=True, default="")
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=6, required=False, default=Decimal("1"))
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    lines = PurchaseBillLineInputSerializer(many=True)

    def validate_lines(self, value):
        if not value:
            raise serializers.ValidationError("At least one line is required.")
        return value


class PurchaseBillListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing bills."""
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    vendor_code = serializers.CharField(source="vendor.code", read_only=True)
    vendor_bill_reference = serializers.CharField(source="reference", read_only=True)

    class Meta:
        model = PurchaseBill
        fields = [
            "id", "public_id", "bill_number", "bill_date", "due_date",
            "vendor", "vendor_name", "vendor_code", "vendor_bill_reference",
            "currency", "exchange_rate",
            "total_amount", "status",
            "created_at",
        ]


# =============================================================================
# Purchase Order Serializers
# =============================================================================

class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)

    class Meta:
        model = PurchaseOrderLine
        fields = [
            "id", "public_id", "line_number",
            "item", "description", "description_ar",
            "quantity", "unit_price", "discount_amount",
            "tax_code", "tax_rate",
            "gross_amount", "net_amount", "tax_amount", "line_total",
            "account", "account_code", "account_name",
            "qty_received", "qty_billed",
        ]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    vendor_code = serializers.CharField(source="vendor.code", read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id", "public_id", "order_number", "order_date", "expected_delivery_date",
            "vendor", "vendor_name", "vendor_code",
            "posting_profile", "currency", "exchange_rate",
            "subtotal", "total_discount", "total_tax", "total_amount",
            "status", "approved_at", "approved_by",
            "notes", "reference", "shipping_address",
            "created_at", "created_by", "updated_at",
            "lines",
        ]


class PurchaseOrderListSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    vendor_code = serializers.CharField(source="vendor.code", read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id", "public_id", "order_number", "order_date", "expected_delivery_date",
            "vendor", "vendor_name", "vendor_code",
            "currency", "total_amount", "status",
            "created_at",
        ]


class PurchaseOrderCreateSerializer(serializers.Serializer):
    vendor_id = serializers.IntegerField()
    posting_profile_id = serializers.IntegerField()
    order_date = serializers.DateField(required=False)
    expected_delivery_date = serializers.DateField(required=False)
    reference = serializers.CharField(required=False, default="", allow_blank=True)
    notes = serializers.CharField(required=False, default="", allow_blank=True)
    shipping_address = serializers.CharField(required=False, default="", allow_blank=True)
    currency = serializers.CharField(required=False, default="", allow_blank=True)
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=6, required=False)
    lines = serializers.ListField(child=serializers.DictField(), min_length=1)


# =============================================================================
# Goods Receipt Serializers
# =============================================================================

class GoodsReceiptLineSerializer(serializers.ModelSerializer):
    po_line_number = serializers.IntegerField(source="po_line.line_number", read_only=True)

    class Meta:
        model = GoodsReceiptLine
        fields = [
            "id", "public_id", "line_number",
            "po_line", "po_line_number",
            "item", "description",
            "qty_received", "unit_cost",
        ]


class GoodsReceiptSerializer(serializers.ModelSerializer):
    lines = GoodsReceiptLineSerializer(many=True, read_only=True)
    order_number = serializers.CharField(source="purchase_order.order_number", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = GoodsReceipt
        fields = [
            "id", "public_id", "receipt_number", "receipt_date",
            "purchase_order", "order_number",
            "vendor", "vendor_name",
            "warehouse", "warehouse_name",
            "status", "posted_at", "posted_by",
            "notes", "created_at", "created_by",
            "lines",
        ]


class GoodsReceiptListSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="purchase_order.order_number", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = GoodsReceipt
        fields = [
            "id", "public_id", "receipt_number", "receipt_date",
            "purchase_order", "order_number",
            "vendor", "vendor_name",
            "warehouse", "warehouse_name",
            "status", "created_at",
        ]


class GoodsReceiptCreateSerializer(serializers.Serializer):
    purchase_order_id = serializers.IntegerField()
    warehouse_id = serializers.IntegerField()
    receipt_date = serializers.DateField(required=False)
    notes = serializers.CharField(required=False, default="", allow_blank=True)
    lines = serializers.ListField(child=serializers.DictField(), min_length=1)


class CreateBillFromPOSerializer(serializers.Serializer):
    bill_date = serializers.DateField(required=False)
    due_date = serializers.DateField(required=False)
    vendor_bill_number = serializers.CharField(required=False, default="", allow_blank=True)
    notes = serializers.CharField(required=False, default="", allow_blank=True)
