# edim/admin.py
from django.contrib import admin
from edim.models import (
    SourceSystem,
    MappingProfile,
    IdentityCrosswalk,
    IngestionBatch,
    StagedRecord,
)


@admin.register(SourceSystem)
class SourceSystemAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "system_type", "trust_level", "is_active", "company"]
    list_filter = ["system_type", "trust_level", "is_active"]
    search_fields = ["code", "name"]


@admin.register(MappingProfile)
class MappingProfileAdmin(admin.ModelAdmin):
    list_display = ["name", "source_system", "document_type", "status", "version", "posting_policy"]
    list_filter = ["document_type", "status", "posting_policy"]
    search_fields = ["name"]


@admin.register(IdentityCrosswalk)
class IdentityCrosswalkAdmin(admin.ModelAdmin):
    list_display = ["source_system", "object_type", "external_id", "nxentra_id", "status"]
    list_filter = ["object_type", "status"]
    search_fields = ["external_id", "nxentra_id"]


@admin.register(IngestionBatch)
class IngestionBatchAdmin(admin.ModelAdmin):
    list_display = ["public_id", "source_system", "ingestion_type", "status", "total_records", "created_at"]
    list_filter = ["status", "ingestion_type"]
    search_fields = ["original_filename"]


@admin.register(StagedRecord)
class StagedRecordAdmin(admin.ModelAdmin):
    list_display = ["batch", "row_number", "is_valid", "created_at"]
    list_filter = ["is_valid"]
