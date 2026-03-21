# sales/serializers.py
"""
Serializers for sales API.

Note: These serializers are used for:
1. Input validation
2. Output formatting

The actual business logic happens in commands.py.
"""

from decimal import Decimal
from rest_framework import serializers
from .models import Item, TaxCode, PostingProfile, SalesInvoice, SalesInvoiceLine


# =============================================================================
# Item Serializers
# =============================================================================

class ItemSerializer(serializers.ModelSerializer):
    """Serializer for Item model."""
    sales_account_code = serializers.CharField(source="sales_account.code", read_only=True, default=None)
    purchase_account_code = serializers.CharField(source="purchase_account.code", read_only=True, default=None)
    default_tax_code_code = serializers.CharField(source="default_tax_code.code", read_only=True, default=None)
    inventory_account_code = serializers.CharField(source="inventory_account.code", read_only=True, default=None)
    cogs_account_code = serializers.CharField(source="cogs_account.code", read_only=True, default=None)

    class Meta:
        model = Item
        fields = [
            "id", "public_id", "code", "name", "name_ar",
            "description", "description_ar", "item_type",
            "sales_account", "sales_account_code",
            "purchase_account", "purchase_account_code",
            "default_unit_price", "default_cost",
            "default_tax_code", "default_tax_code_code",
            # Inventory-specific fields
            "inventory_account", "inventory_account_code",
            "cogs_account", "cogs_account_code",
            "costing_method", "uom",
            "average_cost", "last_cost",
            "is_active", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "public_id",
            "sales_account_code", "purchase_account_code", "default_tax_code_code",
            "inventory_account_code", "cogs_account_code",
            "average_cost", "last_cost",
            "created_at", "updated_at",
        ]


class ItemCreateSerializer(serializers.Serializer):
    """Serializer for creating items via command."""
    code = serializers.CharField(max_length=50)
    name = serializers.CharField(max_length=255)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    description_ar = serializers.CharField(required=False, allow_blank=True, default="")
    item_type = serializers.ChoiceField(
        choices=Item.ItemType.choices,
        required=False,
        default=Item.ItemType.INVENTORY,
    )
    sales_account_id = serializers.IntegerField(required=False, allow_null=True)
    purchase_account_id = serializers.IntegerField(required=False, allow_null=True)
    default_unit_price = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, default=Decimal("0")
    )
    default_cost = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, default=Decimal("0")
    )
    default_tax_code_id = serializers.IntegerField(required=False, allow_null=True)
    # Inventory-specific fields
    inventory_account_id = serializers.IntegerField(required=False, allow_null=True)
    cogs_account_id = serializers.IntegerField(required=False, allow_null=True)
    costing_method = serializers.ChoiceField(
        choices=Item.CostingMethod.choices,
        required=False,
        default=Item.CostingMethod.WEIGHTED_AVERAGE,
    )
    uom = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")


class ItemUpdateSerializer(serializers.Serializer):
    """Serializer for updating items via command."""
    code = serializers.CharField(max_length=50, required=False)
    name = serializers.CharField(max_length=255, required=False)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    description_ar = serializers.CharField(required=False, allow_blank=True)
    item_type = serializers.ChoiceField(choices=Item.ItemType.choices, required=False)
    sales_account_id = serializers.IntegerField(required=False, allow_null=True)
    purchase_account_id = serializers.IntegerField(required=False, allow_null=True)
    default_unit_price = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    default_cost = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    default_tax_code_id = serializers.IntegerField(required=False, allow_null=True)
    # Inventory-specific fields
    inventory_account_id = serializers.IntegerField(required=False, allow_null=True)
    cogs_account_id = serializers.IntegerField(required=False, allow_null=True)
    costing_method = serializers.ChoiceField(choices=Item.CostingMethod.choices, required=False)
    uom = serializers.CharField(max_length=20, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)


# =============================================================================
# Tax Code Serializers
# =============================================================================

class TaxCodeSerializer(serializers.ModelSerializer):
    """Serializer for TaxCode model."""
    tax_account_code = serializers.CharField(source="tax_account.code", read_only=True)
    rate_percentage = serializers.DecimalField(
        max_digits=7, decimal_places=2, read_only=True,
        help_text="Tax rate as percentage (e.g., 15 for 15%)"
    )

    class Meta:
        model = TaxCode
        fields = [
            "id", "public_id", "code", "name", "name_ar", "description",
            "rate", "rate_percentage", "direction",
            "tax_account", "tax_account_code",
            "is_active", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "public_id", "tax_account_code", "rate_percentage",
            "created_at", "updated_at",
        ]


class TaxCodeCreateSerializer(serializers.Serializer):
    """Serializer for creating tax codes via command."""
    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=100)
    name_ar = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    rate = serializers.DecimalField(max_digits=5, decimal_places=4)
    direction = serializers.ChoiceField(choices=TaxCode.TaxDirection.choices)
    tax_account_id = serializers.IntegerField()


class TaxCodeUpdateSerializer(serializers.Serializer):
    """Serializer for updating tax codes via command."""
    code = serializers.CharField(max_length=20, required=False)
    name = serializers.CharField(max_length=100, required=False)
    name_ar = serializers.CharField(max_length=100, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    rate = serializers.DecimalField(max_digits=5, decimal_places=4, required=False)
    direction = serializers.ChoiceField(choices=TaxCode.TaxDirection.choices, required=False)
    tax_account_id = serializers.IntegerField(required=False)
    is_active = serializers.BooleanField(required=False)


# =============================================================================
# Posting Profile Serializers
# =============================================================================

class PostingProfileSerializer(serializers.ModelSerializer):
    """Serializer for PostingProfile model."""
    control_account_code = serializers.CharField(source="control_account.code", read_only=True)

    class Meta:
        model = PostingProfile
        fields = [
            "id", "public_id", "code", "name", "name_ar", "description",
            "profile_type", "control_account", "control_account_code",
            "is_default", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "public_id", "control_account_code",
            "created_at", "updated_at",
        ]


class PostingProfileCreateSerializer(serializers.Serializer):
    """Serializer for creating posting profiles via command."""
    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=100)
    name_ar = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    profile_type = serializers.ChoiceField(choices=PostingProfile.ProfileType.choices)
    control_account_id = serializers.IntegerField()
    is_default = serializers.BooleanField(required=False, default=False)


class PostingProfileUpdateSerializer(serializers.Serializer):
    """Serializer for updating posting profiles via command."""
    code = serializers.CharField(max_length=20, required=False)
    name = serializers.CharField(max_length=100, required=False)
    name_ar = serializers.CharField(max_length=100, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    profile_type = serializers.ChoiceField(choices=PostingProfile.ProfileType.choices, required=False)
    control_account_id = serializers.IntegerField(required=False)
    is_default = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)


# =============================================================================
# Sales Invoice Serializers
# =============================================================================

class SalesInvoiceLineSerializer(serializers.ModelSerializer):
    """Serializer for SalesInvoiceLine model."""
    item_code = serializers.CharField(source="item.code", read_only=True, default=None)
    account_code = serializers.CharField(source="account.code", read_only=True)
    tax_code_code = serializers.CharField(source="tax_code.code", read_only=True, default=None)

    class Meta:
        model = SalesInvoiceLine
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


class SalesInvoiceSerializer(serializers.ModelSerializer):
    """Serializer for SalesInvoice model."""
    lines = SalesInvoiceLineSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    customer_code = serializers.CharField(source="customer.code", read_only=True)
    customer_email = serializers.CharField(source="customer.email", read_only=True, default="")
    posting_profile_code = serializers.CharField(source="posting_profile.code", read_only=True)
    posted_by_email = serializers.CharField(source="posted_by.email", read_only=True, default=None)
    posted_journal_entry_number = serializers.CharField(
        source="posted_journal_entry.entry_number", read_only=True, default=None
    )

    class Meta:
        model = SalesInvoice
        fields = [
            "id", "public_id", "invoice_number", "invoice_date", "due_date",
            "customer", "customer_name", "customer_code", "customer_email",
            "posting_profile", "posting_profile_code",
            "subtotal", "total_discount", "total_tax", "total_amount",
            "status", "posted_at", "posted_by", "posted_by_email",
            "posted_journal_entry", "posted_journal_entry_number",
            "notes", "reference",
            "created_at", "created_by", "updated_at",
            "lines",
        ]
        read_only_fields = [
            "id", "public_id",
            "customer_name", "customer_code", "customer_email", "posting_profile_code",
            "subtotal", "total_discount", "total_tax", "total_amount",
            "status", "posted_at", "posted_by", "posted_by_email",
            "posted_journal_entry", "posted_journal_entry_number",
            "created_at", "created_by", "updated_at",
            "lines",
        ]


class SalesInvoiceLineInputSerializer(serializers.Serializer):
    """Serializer for input line data when creating/updating invoices."""
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


class SalesInvoiceCreateSerializer(serializers.Serializer):
    """Serializer for creating sales invoices via command."""
    invoice_number = serializers.CharField(max_length=50)
    invoice_date = serializers.DateField()
    due_date = serializers.DateField(required=False, allow_null=True)
    customer_id = serializers.IntegerField()
    posting_profile_id = serializers.IntegerField()
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    lines = SalesInvoiceLineInputSerializer(many=True)

    def validate_lines(self, value):
        if not value:
            raise serializers.ValidationError("At least one line is required.")
        return value


class SalesInvoiceUpdateSerializer(serializers.Serializer):
    """Serializer for updating sales invoices via command."""
    invoice_number = serializers.CharField(max_length=50, required=False)
    invoice_date = serializers.DateField(required=False)
    due_date = serializers.DateField(required=False, allow_null=True)
    customer_id = serializers.IntegerField(required=False)
    posting_profile_id = serializers.IntegerField(required=False)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    lines = SalesInvoiceLineInputSerializer(many=True, required=False)

    def validate_lines(self, value):
        if value is not None and len(value) == 0:
            raise serializers.ValidationError("At least one line is required.")
        return value


class SalesInvoiceListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing invoices."""
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    customer_code = serializers.CharField(source="customer.code", read_only=True)

    class Meta:
        model = SalesInvoice
        fields = [
            "id", "public_id", "invoice_number", "invoice_date", "due_date",
            "customer", "customer_name", "customer_code",
            "total_amount", "status",
            "created_at",
        ]
