# edim/event_types.py
"""
EDIM-specific event data classes.

These define the canonical schema for EDIM events.
They follow the BaseEventData pattern from events/types.py.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from events.types import BaseEventData


# =============================================================================
# Source System Events
# =============================================================================

@dataclass
class EdimSourceSystemCreatedData(BaseEventData):
    """Data for edim_source_system.created event."""
    source_system_public_id: str
    code: str
    name: str
    system_type: str
    trust_level: str
    description: str = ""


@dataclass
class EdimSourceSystemUpdatedData(BaseEventData):
    """Data for edim_source_system.updated event."""
    source_system_public_id: str
    changes: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class EdimSourceSystemDeactivatedData(BaseEventData):
    """Data for edim_source_system.deactivated event."""
    source_system_public_id: str
    code: str
    name: str


# =============================================================================
# Ingestion Batch Events
# =============================================================================

@dataclass
class EdimBatchStagedData(BaseEventData):
    """Data for edim_batch.staged event."""
    batch_public_id: str
    source_system_public_id: str
    source_system_code: str
    ingestion_type: str
    original_filename: str
    file_checksum: str
    total_records: int
    staged_by_id: int
    staged_by_email: str


@dataclass
class EdimBatchMappedData(BaseEventData):
    """Data for edim_batch.mapped event."""
    batch_public_id: str
    mapping_profile_public_id: str
    mapping_profile_version: int
    total_records: int
    mapped_records: int
    error_count: int


@dataclass
class EdimBatchValidatedData(BaseEventData):
    """Data for edim_batch.validated event."""
    batch_public_id: str
    total_records: int
    validated_records: int
    error_count: int
    validation_summary: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EdimBatchPreviewedData(BaseEventData):
    """Data for edim_batch.previewed event."""
    batch_public_id: str
    previewed_by_id: int
    previewed_by_email: str
    preview_summary: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EdimBatchCommittedData(BaseEventData):
    """Data for edim_batch.committed event."""
    batch_public_id: str
    committed_by_id: int
    committed_by_email: str
    journal_entry_public_ids: List[str] = field(default_factory=list)
    total_entries_created: int = 0
    total_debit: str = "0"
    total_credit: str = "0"
    posting_policy: str = ""


@dataclass
class EdimBatchRejectedData(BaseEventData):
    """Data for edim_batch.rejected event."""
    batch_public_id: str
    rejected_by_id: int
    rejected_by_email: str
    rejection_reason: str = ""


# =============================================================================
# Mapping Profile Events
# =============================================================================

@dataclass
class EdimMappingProfileCreatedData(BaseEventData):
    """Data for edim_mapping_profile.created event."""
    profile_public_id: str
    source_system_public_id: str
    name: str
    document_type: str
    version: int
    posting_policy: str
    field_mappings: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class EdimMappingProfileUpdatedData(BaseEventData):
    """Data for edim_mapping_profile.updated event."""
    profile_public_id: str
    changes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    new_version: int = 0


@dataclass
class EdimMappingProfileActivatedData(BaseEventData):
    """Data for edim_mapping_profile.activated event."""
    profile_public_id: str
    version: int
    previous_active_version: Optional[int] = None


@dataclass
class EdimMappingProfileDeprecatedData(BaseEventData):
    """Data for edim_mapping_profile.deprecated event."""
    profile_public_id: str
    version: int


# =============================================================================
# Identity Crosswalk Events
# =============================================================================

@dataclass
class EdimCrosswalkCreatedData(BaseEventData):
    """Data for edim_crosswalk.created event."""
    crosswalk_public_id: str
    source_system_public_id: str
    object_type: str
    external_id: str
    external_label: str = ""
    nxentra_id: str = ""
    nxentra_label: str = ""
    status: str = "PROPOSED"


@dataclass
class EdimCrosswalkVerifiedData(BaseEventData):
    """Data for edim_crosswalk.verified event."""
    crosswalk_public_id: str
    verified_by_id: int
    verified_by_email: str


@dataclass
class EdimCrosswalkRejectedData(BaseEventData):
    """Data for edim_crosswalk.rejected event."""
    crosswalk_public_id: str
    rejected_by_id: int
    rejected_by_email: str
    reason: str = ""


@dataclass
class EdimCrosswalkUpdatedData(BaseEventData):
    """Data for edim_crosswalk.updated event."""
    crosswalk_public_id: str
    changes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
