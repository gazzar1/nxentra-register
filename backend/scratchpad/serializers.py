# scratchpad/serializers.py
"""
Serializers for scratchpad API.

Note: These serializers are used for:
1. Input validation
2. Output formatting

The actual business logic happens in commands.py.
"""

from decimal import Decimal
from rest_framework import serializers
from .models import ScratchpadRow, ScratchpadRowDimension, AccountDimensionRule
from accounting.models import Account, AnalysisDimension, AnalysisDimensionValue


# =============================================================================
# Dimension Serializers
# =============================================================================

class ScratchpadRowDimensionSerializer(serializers.ModelSerializer):
    """Serializer for dimension values on a scratchpad row."""
    dimension_id = serializers.IntegerField(source="dimension.id", read_only=True)
    dimension_code = serializers.CharField(source="dimension.code", read_only=True)
    dimension_name = serializers.CharField(source="dimension.name", read_only=True)
    dimension_value_id = serializers.IntegerField(
        source="dimension_value.id", read_only=True, default=None
    )
    dimension_value_code = serializers.CharField(
        source="dimension_value.code", read_only=True, default=None
    )
    dimension_value_name = serializers.CharField(
        source="dimension_value.name", read_only=True, default=None
    )

    class Meta:
        model = ScratchpadRowDimension
        fields = [
            "id",
            "dimension_id",
            "dimension_code",
            "dimension_name",
            "dimension_value_id",
            "dimension_value_code",
            "dimension_value_name",
            "raw_value",
        ]
        read_only_fields = [
            "id",
            "dimension_id",
            "dimension_code",
            "dimension_name",
            "dimension_value_id",
            "dimension_value_code",
            "dimension_value_name",
        ]


class ScratchpadRowDimensionInputSerializer(serializers.Serializer):
    """Input serializer for creating/updating dimension values."""
    dimension_id = serializers.IntegerField()
    dimension_value_id = serializers.IntegerField(required=False, allow_null=True)
    raw_value = serializers.CharField(required=False, allow_blank=True, default="")


# =============================================================================
# ScratchpadRow Serializers
# =============================================================================

class ScratchpadRowSerializer(serializers.ModelSerializer):
    """Serializer for ScratchpadRow - used for list/retrieve."""
    dimensions = ScratchpadRowDimensionSerializer(many=True, read_only=True)
    debit_account_id = serializers.IntegerField(
        source="debit_account.id", read_only=True, default=None
    )
    debit_account_code = serializers.CharField(
        source="debit_account.code", read_only=True, default=None
    )
    debit_account_name = serializers.CharField(
        source="debit_account.name", read_only=True, default=None
    )
    credit_account_id = serializers.IntegerField(
        source="credit_account.id", read_only=True, default=None
    )
    credit_account_code = serializers.CharField(
        source="credit_account.code", read_only=True, default=None
    )
    credit_account_name = serializers.CharField(
        source="credit_account.name", read_only=True, default=None
    )
    created_by_email = serializers.CharField(
        source="created_by.email", read_only=True, default=None
    )

    class Meta:
        model = ScratchpadRow
        fields = [
            "id",
            "public_id",
            "group_id",
            "group_order",
            "status",
            "source",
            "transaction_date",
            "description",
            "description_ar",
            "amount",
            "debit_account_id",
            "debit_account_code",
            "debit_account_name",
            "credit_account_id",
            "credit_account_code",
            "credit_account_name",
            "notes",
            "raw_input",
            "validation_errors",
            "import_batch_id",
            "import_row_number",
            "committed_at",
            "committed_by",
            "committed_event",
            "created_at",
            "created_by",
            "created_by_email",
            "updated_at",
            "dimensions",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "status",
            "validation_errors",
            "committed_at",
            "committed_by",
            "committed_event",
            "created_at",
            "created_by",
            "updated_at",
        ]


class ScratchpadRowCreateSerializer(serializers.Serializer):
    """Serializer for creating a new scratchpad row."""
    group_id = serializers.UUIDField(required=False, allow_null=True)
    group_order = serializers.IntegerField(required=False, default=0)
    source = serializers.ChoiceField(
        choices=ScratchpadRow.Source.choices,
        default=ScratchpadRow.Source.MANUAL,
    )
    transaction_date = serializers.DateField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    description_ar = serializers.CharField(required=False, allow_blank=True, default="")
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True
    )
    debit_account_id = serializers.IntegerField(required=False, allow_null=True)
    credit_account_id = serializers.IntegerField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    raw_input = serializers.CharField(required=False, allow_blank=True, default="")
    dimensions = ScratchpadRowDimensionInputSerializer(many=True, required=False, default=list)


class ScratchpadRowUpdateSerializer(serializers.Serializer):
    """Serializer for updating a scratchpad row."""
    group_id = serializers.UUIDField(required=False, allow_null=True)
    group_order = serializers.IntegerField(required=False)
    transaction_date = serializers.DateField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True)
    description_ar = serializers.CharField(required=False, allow_blank=True)
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True
    )
    debit_account_id = serializers.IntegerField(required=False, allow_null=True)
    credit_account_id = serializers.IntegerField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    dimensions = ScratchpadRowDimensionInputSerializer(many=True, required=False)


class ScratchpadBulkCreateSerializer(serializers.Serializer):
    """Serializer for bulk creating scratchpad rows."""
    rows = ScratchpadRowCreateSerializer(many=True)
    group_id = serializers.UUIDField(required=False, allow_null=True)


class ScratchpadBulkDeleteSerializer(serializers.Serializer):
    """Serializer for bulk deleting scratchpad rows."""
    row_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
    )


# =============================================================================
# Validation Serializers
# =============================================================================

class ScratchpadValidateSerializer(serializers.Serializer):
    """Serializer for validation request."""
    row_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
    )
    group_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
    )


class ValidationErrorSerializer(serializers.Serializer):
    """Serializer for a single validation error."""
    field = serializers.CharField()
    code = serializers.CharField()
    message = serializers.CharField()


class ValidationResultSerializer(serializers.Serializer):
    """Serializer for validation result per row."""
    row_id = serializers.UUIDField()
    status = serializers.CharField()
    errors = ValidationErrorSerializer(many=True)


class ScratchpadValidateResponseSerializer(serializers.Serializer):
    """Serializer for validation response."""
    valid_count = serializers.IntegerField()
    invalid_count = serializers.IntegerField()
    results = ValidationResultSerializer(many=True)


# =============================================================================
# Commit Serializers
# =============================================================================

class ScratchpadCommitSerializer(serializers.Serializer):
    """Serializer for commit request."""
    group_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
    )
    post_immediately = serializers.BooleanField(default=False)


class CommittedEntrySerializer(serializers.Serializer):
    """Serializer for a committed journal entry."""
    group_id = serializers.UUIDField()
    entry_id = serializers.IntegerField()
    entry_public_id = serializers.UUIDField()


class ScratchpadCommitResponseSerializer(serializers.Serializer):
    """Serializer for commit response."""
    batch_id = serializers.UUIDField()
    committed_groups = serializers.IntegerField()
    journal_entries = CommittedEntrySerializer(many=True)


# =============================================================================
# Import/Export Serializers
# =============================================================================

class ColumnMappingSerializer(serializers.Serializer):
    """Serializer for column mapping in imports."""
    date = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    amount = serializers.CharField(required=False, allow_blank=True)
    debit_account = serializers.CharField(required=False, allow_blank=True)
    credit_account = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    # Dynamic dimension mappings are handled separately


class ImportPreviewRowSerializer(serializers.Serializer):
    """Serializer for import preview row."""
    row_number = serializers.IntegerField()
    data = serializers.DictField()
    errors = serializers.ListField(child=serializers.CharField(), default=list)


class ImportPreviewResponseSerializer(serializers.Serializer):
    """Serializer for import preview response."""
    columns = serializers.ListField(child=serializers.CharField())
    sample_rows = ImportPreviewRowSerializer(many=True)
    total_rows = serializers.IntegerField()


class ImportResultSerializer(serializers.Serializer):
    """Serializer for import result."""
    import_batch_id = serializers.UUIDField()
    rows_created = serializers.IntegerField()
    rows_with_errors = serializers.IntegerField()
    errors = serializers.ListField(child=serializers.DictField(), default=list)


# =============================================================================
# Account Dimension Rule Serializers
# =============================================================================

class AccountDimensionRuleSerializer(serializers.ModelSerializer):
    """Serializer for AccountDimensionRule."""
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    dimension_code = serializers.CharField(source="dimension.code", read_only=True)
    dimension_name = serializers.CharField(source="dimension.name", read_only=True)
    default_value_code = serializers.CharField(
        source="default_value.code", read_only=True, default=None
    )

    class Meta:
        model = AccountDimensionRule
        fields = [
            "id",
            "account",
            "account_code",
            "account_name",
            "dimension",
            "dimension_code",
            "dimension_name",
            "rule_type",
            "default_value",
            "default_value_code",
        ]
        read_only_fields = [
            "id",
            "account_code",
            "account_name",
            "dimension_code",
            "dimension_name",
            "default_value_code",
        ]


class AccountDimensionRuleCreateSerializer(serializers.Serializer):
    """Serializer for creating AccountDimensionRule."""
    account_id = serializers.IntegerField()
    dimension_id = serializers.IntegerField()
    rule_type = serializers.ChoiceField(choices=AccountDimensionRule.RuleType.choices)
    default_value_id = serializers.IntegerField(required=False, allow_null=True)


# =============================================================================
# Dimension Schema Serializers
# =============================================================================

class DimensionValueSchema(serializers.Serializer):
    """Schema for a dimension value."""
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    name_ar = serializers.CharField()


class DimensionSchema(serializers.Serializer):
    """Schema for a dimension type."""
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    name_ar = serializers.CharField()
    is_required_on_posting = serializers.BooleanField()
    applies_to_account_types = serializers.ListField(child=serializers.CharField())
    display_order = serializers.IntegerField()
    values = DimensionValueSchema(many=True)


class DimensionSchemaResponseSerializer(serializers.Serializer):
    """Schema response for frontend."""
    dimensions = DimensionSchema(many=True)


# =============================================================================
# Voice Parsing Serializers
# =============================================================================

class VoiceParseRequestSerializer(serializers.Serializer):
    """Serializer for voice parsing request."""
    transcript = serializers.CharField(min_length=1)


class VoiceParseResponseSerializer(serializers.Serializer):
    """Serializer for voice parsing response."""
    parsed = serializers.DictField()
    confidence = serializers.FloatField()
    raw_input = serializers.CharField()
