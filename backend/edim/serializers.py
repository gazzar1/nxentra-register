# edim/serializers.py
"""
Serializers for EDIM (External Data Ingestion & Mapping) API.

Note: These serializers are used for:
1. Input validation
2. Output formatting

The actual business logic happens in commands.py.
"""

from rest_framework import serializers

from edim.models import (
    SourceSystem,
    MappingProfile,
    IdentityCrosswalk,
    IngestionBatch,
    StagedRecord,
)


# =============================================================================
# Source System Serializers
# =============================================================================

class SourceSystemSerializer(serializers.ModelSerializer):
    """Serializer for listing and retrieving source systems."""

    class Meta:
        model = SourceSystem
        fields = [
            "id", "public_id", "code", "name", "system_type", "trust_level",
            "description", "is_active", "connection_info",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "public_id", "created_at", "updated_at",
        ]


class SourceSystemCreateSerializer(serializers.Serializer):
    """Serializer for creating source systems via command."""
    code = serializers.CharField(max_length=50)
    name = serializers.CharField(max_length=255)
    system_type = serializers.ChoiceField(choices=SourceSystem.SystemType.choices)
    trust_level = serializers.ChoiceField(
        choices=SourceSystem.TrustLevel.choices,
        required=False,
        default=SourceSystem.TrustLevel.INFORMATIONAL,
    )
    description = serializers.CharField(required=False, allow_blank=True, default="")


class SourceSystemUpdateSerializer(serializers.Serializer):
    """Serializer for updating source systems via command."""
    name = serializers.CharField(max_length=255, required=False)
    system_type = serializers.ChoiceField(
        choices=SourceSystem.SystemType.choices, required=False
    )
    trust_level = serializers.ChoiceField(
        choices=SourceSystem.TrustLevel.choices, required=False
    )
    description = serializers.CharField(required=False, allow_blank=True)
    connection_info = serializers.JSONField(required=False)


# =============================================================================
# Mapping Profile Serializers
# =============================================================================

class FieldMappingSerializer(serializers.Serializer):
    """Serializer for individual field mapping rules."""
    source_field = serializers.CharField(max_length=100)
    target_field = serializers.CharField(max_length=100)
    transform = serializers.CharField(max_length=50, required=False, default="")
    format = serializers.CharField(max_length=100, required=False, default="")
    default = serializers.CharField(required=False, allow_null=True)


class TransformRuleSerializer(serializers.Serializer):
    """Serializer for post-mapping transform rules."""
    type = serializers.CharField(max_length=50)
    source = serializers.CharField(max_length=100, required=False)
    sources = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
    )
    target = serializers.CharField(max_length=100, required=False)
    field = serializers.CharField(max_length=100, required=False)
    debit_field = serializers.CharField(max_length=100, required=False)
    credit_field = serializers.CharField(max_length=100, required=False)
    separator = serializers.CharField(max_length=10, required=False)


class MappingProfileSerializer(serializers.ModelSerializer):
    """Serializer for listing and retrieving mapping profiles."""
    source_system_code = serializers.CharField(
        source="source_system.code", read_only=True
    )
    source_system_name = serializers.CharField(
        source="source_system.name", read_only=True
    )
    created_by_email = serializers.CharField(
        source="created_by.email", read_only=True, default=""
    )

    class Meta:
        model = MappingProfile
        fields = [
            "id", "public_id", "source_system", "source_system_code", "source_system_name",
            "name", "document_type", "status", "version",
            "field_mappings", "transform_rules", "defaults", "validation_rules",
            "posting_policy",
            "default_debit_account_code", "default_credit_account_code",
            "created_by", "created_by_email",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "public_id", "source_system_code", "source_system_name",
            "status", "version", "created_by", "created_by_email",
            "created_at", "updated_at",
        ]


class MappingProfileCreateSerializer(serializers.Serializer):
    """Serializer for creating mapping profiles via command."""
    source_system_id = serializers.IntegerField()
    name = serializers.CharField(max_length=255)
    document_type = serializers.ChoiceField(choices=MappingProfile.DocumentType.choices)
    field_mappings = FieldMappingSerializer(many=True, required=False, default=list)
    transform_rules = TransformRuleSerializer(many=True, required=False, default=list)
    defaults = serializers.JSONField(required=False, default=dict)
    validation_rules = serializers.ListField(required=False, default=list)
    posting_policy = serializers.ChoiceField(
        choices=MappingProfile.PostingPolicy.choices,
        required=False,
        default=MappingProfile.PostingPolicy.MANUAL_APPROVAL,
    )
    default_debit_account_code = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )
    default_credit_account_code = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )


class MappingProfileUpdateSerializer(serializers.Serializer):
    """Serializer for updating mapping profiles via command."""
    name = serializers.CharField(max_length=255, required=False)
    field_mappings = FieldMappingSerializer(many=True, required=False)
    transform_rules = TransformRuleSerializer(many=True, required=False)
    defaults = serializers.JSONField(required=False)
    validation_rules = serializers.ListField(required=False)
    posting_policy = serializers.ChoiceField(
        choices=MappingProfile.PostingPolicy.choices, required=False
    )
    default_debit_account_code = serializers.CharField(
        max_length=20, required=False, allow_blank=True
    )
    default_credit_account_code = serializers.CharField(
        max_length=20, required=False, allow_blank=True
    )


# =============================================================================
# Identity Crosswalk Serializers
# =============================================================================

class IdentityCrosswalkSerializer(serializers.ModelSerializer):
    """Serializer for listing and retrieving crosswalks."""
    source_system_code = serializers.CharField(
        source="source_system.code", read_only=True
    )
    source_system_name = serializers.CharField(
        source="source_system.name", read_only=True
    )
    verified_by_email = serializers.CharField(
        source="verified_by.email", read_only=True, default=""
    )

    class Meta:
        model = IdentityCrosswalk
        fields = [
            "id", "public_id", "source_system", "source_system_code", "source_system_name",
            "object_type", "external_id", "external_label",
            "nxentra_id", "nxentra_label", "status",
            "verified_by", "verified_by_email", "verified_at",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "public_id", "source_system_code", "source_system_name",
            "verified_by", "verified_by_email", "verified_at",
            "created_at", "updated_at",
        ]


class IdentityCrosswalkCreateSerializer(serializers.Serializer):
    """Serializer for creating crosswalks via command."""
    source_system_id = serializers.IntegerField()
    object_type = serializers.ChoiceField(choices=IdentityCrosswalk.ObjectType.choices)
    external_id = serializers.CharField(max_length=255)
    external_label = serializers.CharField(
        max_length=500, required=False, allow_blank=True, default=""
    )
    nxentra_id = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    nxentra_label = serializers.CharField(
        max_length=500, required=False, allow_blank=True, default=""
    )
    status = serializers.ChoiceField(
        choices=IdentityCrosswalk.CrosswalkStatus.choices,
        required=False,
        default=IdentityCrosswalk.CrosswalkStatus.PROPOSED,
    )


class IdentityCrosswalkUpdateSerializer(serializers.Serializer):
    """Serializer for updating crosswalks via command."""
    nxentra_id = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    nxentra_label = serializers.CharField(
        max_length=500, required=False, allow_blank=True
    )
    external_label = serializers.CharField(
        max_length=500, required=False, allow_blank=True
    )


class CrosswalkRejectSerializer(serializers.Serializer):
    """Serializer for rejecting a crosswalk."""
    reason = serializers.CharField(required=False, allow_blank=True, default="")


# =============================================================================
# Ingestion Batch Serializers
# =============================================================================

class StagedRecordSerializer(serializers.ModelSerializer):
    """Serializer for staged records."""

    class Meta:
        model = StagedRecord
        fields = [
            "id", "row_number", "raw_payload", "row_hash",
            "mapped_payload", "mapping_errors",
            "validation_errors", "is_valid", "resolved_accounts",
            "created_at",
        ]
        read_only_fields = fields


class IngestionBatchSerializer(serializers.ModelSerializer):
    """Serializer for listing and retrieving ingestion batches."""
    source_system_code = serializers.CharField(
        source="source_system.code", read_only=True
    )
    source_system_name = serializers.CharField(
        source="source_system.name", read_only=True
    )
    mapping_profile_name = serializers.CharField(
        source="mapping_profile.name", read_only=True, default=""
    )
    staged_by_email = serializers.CharField(
        source="staged_by.email", read_only=True, default=""
    )
    committed_by_email = serializers.CharField(
        source="committed_by.email", read_only=True, default=""
    )
    rejected_by_email = serializers.CharField(
        source="rejected_by.email", read_only=True, default=""
    )

    class Meta:
        model = IngestionBatch
        fields = [
            "id", "public_id", "source_system", "source_system_code", "source_system_name",
            "ingestion_type", "status",
            "original_filename", "file_checksum", "file_size_bytes",
            "mapping_profile", "mapping_profile_name", "mapping_profile_version",
            "total_records", "mapped_records", "validated_records", "error_count",
            "staged_by", "staged_by_email",
            "committed_by", "committed_by_email", "committed_at",
            "rejected_by", "rejected_by_email", "rejected_at", "rejection_reason",
            "committed_entry_public_ids",
            "created_at", "updated_at",
        ]
        read_only_fields = fields


class IngestionBatchDetailSerializer(IngestionBatchSerializer):
    """Serializer for batch detail with records."""
    records = StagedRecordSerializer(many=True, read_only=True)

    class Meta(IngestionBatchSerializer.Meta):
        fields = IngestionBatchSerializer.Meta.fields + ["records"]


class BatchUploadSerializer(serializers.Serializer):
    """Serializer for uploading a batch file."""
    source_system_id = serializers.IntegerField()
    file = serializers.FileField()
    mapping_profile_id = serializers.IntegerField(required=False, allow_null=True)


class BatchMapSerializer(serializers.Serializer):
    """Serializer for mapping a batch."""
    mapping_profile_id = serializers.IntegerField(required=False, allow_null=True)


class BatchRejectSerializer(serializers.Serializer):
    """Serializer for rejecting a batch."""
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class BatchPreviewSerializer(serializers.Serializer):
    """Serializer for batch preview response."""
    total_entries = serializers.IntegerField()
    total_debit = serializers.CharField()
    total_credit = serializers.CharField()
    proposed_entries = serializers.ListField()
