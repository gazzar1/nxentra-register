# edim/models.py
"""
EDIM write models.

These are primary state owned by EDIM commands.
They use command_writes_allowed() guards to enforce that all
mutations go through the command layer.

Models:
- SourceSystem: External data origin
- MappingProfile: Versioned mapping rules
- IdentityCrosswalk: External ID <-> Nxentra ID resolution
- IngestionBatch: One import operation lifecycle
- StagedRecord: Immutable raw evidence from external source
"""

import hashlib
import json
import uuid

from django.conf import settings
from django.db import models

from accounts.models import Company
from projections.write_barrier import write_context_allowed


class SourceSystem(models.Model):
    """
    External system that produces data for ingestion.
    Examples: POS_Square, Payroll_ADP, Bank_Chase.
    """

    class SystemType(models.TextChoices):
        POS = "POS", "Point of Sale"
        HR = "HR", "Human Resources"
        INVENTORY = "INVENTORY", "Inventory Management"
        PAYROLL = "PAYROLL", "Payroll"
        BANK = "BANK", "Bank Feed"
        ERP = "ERP", "External ERP"
        CUSTOM = "CUSTOM", "Custom"

    class TrustLevel(models.TextChoices):
        INFORMATIONAL = "INFORMATIONAL", "Informational (no auto-post)"
        OPERATIONAL = "OPERATIONAL", "Operational (auto-draft)"
        FINANCIAL = "FINANCIAL", "Financial (auto-post eligible)"

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="edim_source_systems"
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    system_type = models.CharField(max_length=20, choices=SystemType.choices)
    trust_level = models.CharField(
        max_length=20, choices=TrustLevel.choices, default=TrustLevel.INFORMATIONAL
    )
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    connection_info = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="uniq_edim_source_system_code",
            )
        ]
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def save(self, *args, **kwargs):
        if not write_context_allowed({"command", "migration", "bootstrap"}) and not getattr(
            settings, "TESTING", False
        ):
            raise RuntimeError(
                "SourceSystem is an EDIM write model. Use edim.commands to modify."
            )
        super().save(*args, **kwargs)


class MappingProfile(models.Model):
    """
    Versioned mapping rules for transforming external data to canonical form.
    Profiles are versioned: updating creates a new version.
    """

    class DocumentType(models.TextChoices):
        SALES = "SALES", "Sales"
        PAYROLL = "PAYROLL", "Payroll"
        INVENTORY_MOVE = "INVENTORY_MOVE", "Inventory Movement"
        JOURNAL = "JOURNAL", "Generic Journal"
        BANK_TRANSACTION = "BANK_TRANSACTION", "Bank Transaction"
        CUSTOM = "CUSTOM", "Custom"

    class ProfileStatus(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACTIVE = "ACTIVE", "Active"
        DEPRECATED = "DEPRECATED", "Deprecated"

    class PostingPolicy(models.TextChoices):
        AUTO_DRAFT = "AUTO_DRAFT", "Auto Draft (create as DRAFT)"
        AUTO_POST = "AUTO_POST", "Auto Post (create and post)"
        MANUAL_APPROVAL = "MANUAL_APPROVAL", "Manual Approval (preview required)"

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="edim_mapping_profiles"
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    source_system = models.ForeignKey(
        SourceSystem, on_delete=models.CASCADE, related_name="mapping_profiles"
    )

    name = models.CharField(max_length=255)
    document_type = models.CharField(max_length=30, choices=DocumentType.choices)
    status = models.CharField(
        max_length=20, choices=ProfileStatus.choices, default=ProfileStatus.DRAFT
    )
    version = models.PositiveIntegerField(default=1)

    # Mapping configuration (JSON schema)
    field_mappings = models.JSONField(
        default=list,
        help_text="List of field mapping rules: [{source_field, target_field, transform, format}]",
    )
    transform_rules = models.JSONField(
        default=list,
        help_text="Post-mapping transform rules",
    )
    defaults = models.JSONField(
        default=dict,
        help_text="Default values for unmapped fields",
    )
    validation_rules = models.JSONField(
        default=list,
        help_text="Additional validation rules",
    )

    posting_policy = models.CharField(
        max_length=20,
        choices=PostingPolicy.choices,
        default=PostingPolicy.MANUAL_APPROVAL,
    )

    default_debit_account_code = models.CharField(max_length=20, blank=True, default="")
    default_credit_account_code = models.CharField(max_length=20, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="edim_created_profiles",
        db_constraint=False,  # Cross-database FK (User in system DB, EDIM in tenant DB)
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "source_system", "document_type", "version"],
                name="uniq_edim_mapping_profile_version",
            )
        ]
        ordering = ["source_system", "document_type", "-version"]
        indexes = [
            models.Index(fields=["company", "source_system", "status"]),
        ]

    def __str__(self):
        return f"{self.name} v{self.version} ({self.status})"

    def save(self, *args, **kwargs):
        if not write_context_allowed({"command", "migration", "bootstrap"}) and not getattr(
            settings, "TESTING", False
        ):
            raise RuntimeError(
                "MappingProfile is an EDIM write model. Use edim.commands to modify."
            )
        super().save(*args, **kwargs)


class IdentityCrosswalk(models.Model):
    """
    Maps external system identifiers to Nxentra internal identifiers.
    Enables resolution of external account codes, customer IDs, etc.
    """

    class ObjectType(models.TextChoices):
        ACCOUNT = "ACCOUNT", "Account"
        CUSTOMER = "CUSTOMER", "Customer"
        ITEM = "ITEM", "Item"
        TAX_CODE = "TAX_CODE", "Tax Code"
        DIMENSION = "DIMENSION", "Analysis Dimension"
        DIMENSION_VALUE = "DIMENSION_VALUE", "Analysis Dimension Value"

    class CrosswalkStatus(models.TextChoices):
        VERIFIED = "VERIFIED", "Verified"
        PROPOSED = "PROPOSED", "Proposed"
        REJECTED = "REJECTED", "Rejected"

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="edim_crosswalks"
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    source_system = models.ForeignKey(
        SourceSystem, on_delete=models.CASCADE, related_name="crosswalks"
    )

    object_type = models.CharField(max_length=30, choices=ObjectType.choices)
    external_id = models.CharField(max_length=255, help_text="Identifier in the external system")
    external_label = models.CharField(
        max_length=500, blank=True, default="", help_text="Human-readable label from external system"
    )

    nxentra_id = models.CharField(
        max_length=255, blank=True, default="", help_text="public_id of the Nxentra entity"
    )
    nxentra_label = models.CharField(
        max_length=500, blank=True, default="", help_text="Human-readable label of Nxentra entity"
    )

    status = models.CharField(
        max_length=20, choices=CrosswalkStatus.choices, default=CrosswalkStatus.PROPOSED
    )

    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="edim_verified_crosswalks",
        db_constraint=False,  # Cross-database FK (User in system DB, EDIM in tenant DB)
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "source_system", "object_type", "external_id"],
                name="uniq_edim_crosswalk_entry",
            )
        ]
        ordering = ["source_system", "object_type", "external_id"]
        indexes = [
            models.Index(fields=["company", "source_system", "object_type"]),
            models.Index(fields=["company", "nxentra_id"]),
        ]

    def __str__(self):
        return f"{self.object_type}: {self.external_id} -> {self.nxentra_id}"

    def save(self, *args, **kwargs):
        if not write_context_allowed({"command", "migration", "bootstrap"}) and not getattr(
            settings, "TESTING", False
        ):
            raise RuntimeError(
                "IdentityCrosswalk is an EDIM write model. Use edim.commands to modify."
            )
        super().save(*args, **kwargs)


class IngestionBatch(models.Model):
    """
    A single ingestion batch. Tracks lifecycle from staging through commit.
    All-or-nothing: partial success is forbidden.
    """

    class IngestionType(models.TextChoices):
        FILE_CSV = "FILE_CSV", "CSV File"
        FILE_XLSX = "FILE_XLSX", "Excel File"
        FILE_JSON = "FILE_JSON", "JSON File"
        API = "API", "API"

    class Status(models.TextChoices):
        STAGED = "STAGED", "Staged"
        MAPPED = "MAPPED", "Mapped"
        VALIDATED = "VALIDATED", "Validated"
        PREVIEWED = "PREVIEWED", "Previewed"
        COMMITTED = "COMMITTED", "Committed"
        REJECTED = "REJECTED", "Rejected"

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="edim_batches"
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    source_system = models.ForeignKey(
        SourceSystem, on_delete=models.PROTECT, related_name="batches"
    )

    ingestion_type = models.CharField(max_length=20, choices=IngestionType.choices)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.STAGED
    )

    # File metadata
    original_filename = models.CharField(max_length=500, blank=True, default="")
    file = models.FileField(upload_to="edim/uploads/", blank=True, null=True)
    file_checksum = models.CharField(
        max_length=64, blank=True, default="", help_text="SHA-256 of uploaded file"
    )
    file_size_bytes = models.BigIntegerField(null=True, blank=True)

    # Mapping reference
    mapping_profile = models.ForeignKey(
        MappingProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="batches",
    )
    mapping_profile_version = models.PositiveIntegerField(
        null=True, blank=True, help_text="Snapshot of profile version used"
    )

    # Stats
    total_records = models.PositiveIntegerField(default=0)
    mapped_records = models.PositiveIntegerField(default=0)
    validated_records = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)

    # Audit
    staged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="edim_staged_batches",
        db_constraint=False,  # Cross-database FK (User in system DB, EDIM in tenant DB)
    )
    committed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="edim_committed_batches",
        db_constraint=False,  # Cross-database FK (User in system DB, EDIM in tenant DB)
    )
    committed_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="edim_rejected_batches",
        db_constraint=False,  # Cross-database FK (User in system DB, EDIM in tenant DB)
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")

    # Journal entries created by commit
    committed_entry_public_ids = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "file_checksum"],
                condition=models.Q(file_checksum__gt=""),
                name="uniq_edim_batch_checksum",
            )
        ]
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "source_system"]),
        ]

    def __str__(self):
        return f"Batch {self.public_id} ({self.status})"

    def save(self, *args, **kwargs):
        if not write_context_allowed({"command", "migration", "bootstrap"}) and not getattr(
            settings, "TESTING", False
        ):
            raise RuntimeError(
                "IngestionBatch is an EDIM write model. Use edim.commands to modify."
            )
        super().save(*args, **kwargs)


class StagedRecord(models.Model):
    """
    Immutable staged record. Raw data from external source.
    raw_payload and row_hash cannot change after creation.
    """

    batch = models.ForeignKey(
        IngestionBatch, on_delete=models.CASCADE, related_name="records"
    )
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="edim_staged_records"
    )

    row_number = models.PositiveIntegerField()
    raw_payload = models.JSONField(help_text="Raw data from the source, exactly as parsed")
    row_hash = models.CharField(max_length=64, help_text="SHA-256 of raw_payload")

    # Mapping results (populated during Map step)
    mapped_payload = models.JSONField(
        null=True, blank=True, help_text="Canonical payload after mapping rules applied"
    )
    mapping_errors = models.JSONField(
        default=list, blank=True, help_text="List of mapping error messages"
    )

    # Validation results (populated during Validate step)
    validation_errors = models.JSONField(
        default=list, blank=True, help_text="List of validation error messages"
    )
    is_valid = models.BooleanField(
        null=True, blank=True, help_text="True if record passed validation"
    )

    # Crosswalk resolution results
    resolved_accounts = models.JSONField(
        default=dict, blank=True, help_text="Map of external_id -> nxentra_id"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "row_number"],
                name="uniq_edim_staged_record_row",
            )
        ]
        ordering = ["batch", "row_number"]
        indexes = [
            models.Index(fields=["company", "batch"]),
        ]

    def __str__(self):
        return f"Record {self.row_number} in Batch {self.batch_id}"

    @staticmethod
    def compute_row_hash(payload: dict) -> str:
        """Compute SHA-256 hash of a payload dict."""
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(normalized).hexdigest()

    def save(self, *args, **kwargs):
        if not write_context_allowed({"command", "migration", "bootstrap"}) and not getattr(
            settings, "TESTING", False
        ):
            raise RuntimeError(
                "StagedRecord is an EDIM write model. Use edim.commands to modify."
            )
        # Enforce immutability of raw_payload and row_hash after creation
        if not self._state.adding and self.pk:
            original = StagedRecord.objects.filter(pk=self.pk).values(
                "row_hash"
            ).first()
            if original and self.row_hash != original["row_hash"]:
                raise ValueError("raw_payload/row_hash are immutable after creation.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Staged records are immutable and cannot be deleted.")
