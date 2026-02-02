# edim/commands.py
"""
Command layer for EDIM (External Data Ingestion & Mapping) operations.

Commands are the single point where business operations happen.
Views call commands; commands enforce rules and emit events.

Pattern:
1. Validate permissions (require)
2. Apply business policies
3. Perform the operation (model changes)
4. Emit event (emit_event)
5. Return CommandResult

ALL state changes MUST go through commands to ensure events are emitted.
"""

from django.db import transaction
from django.conf import settings
from django.utils import timezone
import hashlib
import json
import uuid

from accounts.authz import ActorContext, require
from events.emitter import emit_event
from projections.write_barrier import command_writes_allowed
from events.types import EventTypes

from edim.models import (
    SourceSystem,
    MappingProfile,
    IdentityCrosswalk,
    IngestionBatch,
    StagedRecord,
)
from edim.event_types import (
    EdimSourceSystemCreatedData,
    EdimSourceSystemUpdatedData,
    EdimSourceSystemDeactivatedData,
    EdimMappingProfileCreatedData,
    EdimMappingProfileUpdatedData,
    EdimMappingProfileActivatedData,
    EdimMappingProfileDeprecatedData,
    EdimCrosswalkCreatedData,
    EdimCrosswalkVerifiedData,
    EdimCrosswalkRejectedData,
    EdimCrosswalkUpdatedData,
    EdimBatchStagedData,
    EdimBatchMappedData,
    EdimBatchValidatedData,
    EdimBatchPreviewedData,
    EdimBatchCommittedData,
    EdimBatchRejectedData,
)


class CommandResult:
    """
    Wrapper for command results with success/failure info.

    Usage:
        result = create_source_system(actor, code="shopify", ...)
        if result.success:
            source_system = result.data
            event = result.event
        else:
            error_message = result.error
    """

    def __init__(self, success: bool, data=None, error: str = None, event=None):
        self.success = success
        self.data = data
        self.error = error
        self.event = event

    @classmethod
    def ok(cls, data=None, event=None):
        return cls(success=True, data=data, event=event)

    @classmethod
    def fail(cls, error: str):
        return cls(success=False, error=error)


def _changes_hash(changes: dict) -> str:
    payload = json.dumps(changes, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def _idempotency_hash(prefix: str, payload: dict) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(normalized).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _process_projections(company, exclude: set[str] | None = None) -> None:
    if not settings.PROJECTIONS_SYNC:
        return

    from projections.base import projection_registry

    excluded = exclude or set()
    for projection in projection_registry.all():
        if projection.name in excluded:
            continue
        projection.process_pending(company, limit=1000)


# =============================================================================
# Source System Commands
# =============================================================================

@transaction.atomic
def create_source_system(
    actor: ActorContext,
    code: str,
    name: str,
    system_type: str,
    trust_level: str = SourceSystem.TrustLevel.INFORMATIONAL,
    description: str = "",
) -> CommandResult:
    """
    Create a new source system for external data ingestion.

    Args:
        actor: The actor context (user + company)
        code: Unique source system code (e.g., "shopify", "square")
        name: Display name
        system_type: Type of system (POS, HR, INVENTORY, etc.)
        trust_level: Trust level (INFORMATIONAL, OPERATIONAL, FINANCIAL)
        description: Optional description

    Returns:
        CommandResult with the created SourceSystem or error
    """
    require(actor, "edim.manage_sources")

    # Validate system_type
    valid_types = [choice[0] for choice in SourceSystem.SystemType.choices]
    if system_type not in valid_types:
        return CommandResult.fail(f"Invalid system_type '{system_type}'. Must be one of: {valid_types}")

    # Validate trust_level
    valid_trust_levels = [choice[0] for choice in SourceSystem.TrustLevel.choices]
    if trust_level not in valid_trust_levels:
        return CommandResult.fail(f"Invalid trust_level '{trust_level}'. Must be one of: {valid_trust_levels}")

    # Check for duplicate code
    if SourceSystem.objects.filter(company=actor.company, code=code).exists():
        return CommandResult.fail(f"Source system code '{code}' already exists.")

    source_system_public_id = uuid.uuid4()

    idempotency_key = _idempotency_hash("edim_source_system.created", {
        "company_public_id": str(actor.company.public_id),
        "code": code,
        "name": name,
        "system_type": system_type,
        "trust_level": trust_level,
        "description": description,
    })

    # Create the source system within write context
    with command_writes_allowed():
        source_system = SourceSystem.objects.create(
            company=actor.company,
            public_id=source_system_public_id,
            code=code,
            name=name,
            system_type=system_type,
            trust_level=trust_level,
            description=description,
        )

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_SOURCE_SYSTEM_CREATED,
        aggregate_type="EdimSourceSystem",
        aggregate_id=str(source_system_public_id),
        idempotency_key=idempotency_key,
        data=EdimSourceSystemCreatedData(
            source_system_public_id=str(source_system_public_id),
            code=code,
            name=name,
            system_type=system_type,
            trust_level=trust_level,
            description=description,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(source_system, event=event)


@transaction.atomic
def update_source_system(
    actor: ActorContext,
    source_system_id: int,
    **updates,
) -> CommandResult:
    """
    Update an existing source system.

    Args:
        actor: The actor context
        source_system_id: ID of source system to update
        **updates: Field updates (name, description, trust_level)

    Returns:
        CommandResult with updated SourceSystem or error
    """
    require(actor, "edim.manage_sources")

    try:
        source_system = SourceSystem.objects.select_for_update().get(
            pk=source_system_id, company=actor.company
        )
    except SourceSystem.DoesNotExist:
        return CommandResult.fail("Source system not found.")

    # Track changes
    changes = {}
    allowed_fields = {"name", "description", "trust_level", "system_type", "connection_info"}

    for field, value in updates.items():
        if field in allowed_fields:
            old_value = getattr(source_system, field)
            if old_value != value:
                changes[field] = {"old": old_value, "new": value}

    if not changes:
        return CommandResult.ok(source_system)

    # Apply changes within write context
    with command_writes_allowed():
        for field, change in changes.items():
            setattr(source_system, field, change["new"])
        source_system.save()

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_SOURCE_SYSTEM_UPDATED,
        aggregate_type="EdimSourceSystem",
        aggregate_id=str(source_system.public_id),
        idempotency_key=f"edim_source_system.updated:{source_system.public_id}:{_changes_hash(changes)}",
        data=EdimSourceSystemUpdatedData(
            source_system_public_id=str(source_system.public_id),
            changes=changes,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(source_system, event=event)


@transaction.atomic
def deactivate_source_system(
    actor: ActorContext,
    source_system_id: int,
) -> CommandResult:
    """
    Deactivate a source system.

    Args:
        actor: The actor context
        source_system_id: ID of source system to deactivate

    Returns:
        CommandResult with deactivated SourceSystem or error
    """
    require(actor, "edim.manage_sources")

    try:
        source_system = SourceSystem.objects.select_for_update().get(
            pk=source_system_id, company=actor.company
        )
    except SourceSystem.DoesNotExist:
        return CommandResult.fail("Source system not found.")

    if not source_system.is_active:
        return CommandResult.fail("Source system is already deactivated.")

    # Deactivate within write context
    with command_writes_allowed():
        source_system.is_active = False
        source_system.save(update_fields=["is_active", "updated_at"])

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_SOURCE_SYSTEM_DEACTIVATED,
        aggregate_type="EdimSourceSystem",
        aggregate_id=str(source_system.public_id),
        idempotency_key=f"edim_source_system.deactivated:{source_system.public_id}",
        data=EdimSourceSystemDeactivatedData(
            source_system_public_id=str(source_system.public_id),
            code=source_system.code,
            name=source_system.name,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(source_system, event=event)


# =============================================================================
# Mapping Profile Commands
# =============================================================================

@transaction.atomic
def create_mapping_profile(
    actor: ActorContext,
    source_system_id: int,
    name: str,
    document_type: str,
    field_mappings: list = None,
    transform_rules: list = None,
    defaults: dict = None,
    validation_rules: list = None,
    posting_policy: str = MappingProfile.PostingPolicy.MANUAL_APPROVAL,
    default_debit_account_code: str = "",
    default_credit_account_code: str = "",
) -> CommandResult:
    """
    Create a new mapping profile for a source system.

    Args:
        actor: The actor context
        source_system_id: ID of the source system
        name: Profile name
        document_type: Type of document (SALES, PAYROLL, etc.)
        field_mappings: List of field mapping rules
        transform_rules: Post-mapping transform rules
        defaults: Default values for unmapped fields
        validation_rules: Additional validation rules
        posting_policy: How to handle journal entry creation
        default_debit_account_code: Default debit account
        default_credit_account_code: Default credit account

    Returns:
        CommandResult with created MappingProfile or error
    """
    require(actor, "edim.manage_mappings")

    try:
        source_system = SourceSystem.objects.get(
            pk=source_system_id, company=actor.company
        )
    except SourceSystem.DoesNotExist:
        return CommandResult.fail("Source system not found.")

    # Validate document_type
    valid_doc_types = [choice[0] for choice in MappingProfile.DocumentType.choices]
    if document_type not in valid_doc_types:
        return CommandResult.fail(f"Invalid document_type '{document_type}'. Must be one of: {valid_doc_types}")

    # Validate posting_policy
    valid_policies = [choice[0] for choice in MappingProfile.PostingPolicy.choices]
    if posting_policy not in valid_policies:
        return CommandResult.fail(f"Invalid posting_policy '{posting_policy}'. Must be one of: {valid_policies}")

    profile_public_id = uuid.uuid4()

    # Determine version (first version for this source_system + document_type combo)
    existing_versions = MappingProfile.objects.filter(
        company=actor.company,
        source_system=source_system,
        document_type=document_type,
    ).values_list("version", flat=True)
    version = max(existing_versions, default=0) + 1

    idempotency_key = _idempotency_hash("edim_mapping_profile.created", {
        "company_public_id": str(actor.company.public_id),
        "source_system_public_id": str(source_system.public_id),
        "name": name,
        "document_type": document_type,
        "version": version,
    })

    # Create profile within write context
    with command_writes_allowed():
        profile = MappingProfile.objects.create(
            company=actor.company,
            public_id=profile_public_id,
            source_system=source_system,
            name=name,
            document_type=document_type,
            version=version,
            field_mappings=field_mappings or [],
            transform_rules=transform_rules or [],
            defaults=defaults or {},
            validation_rules=validation_rules or [],
            posting_policy=posting_policy,
            default_debit_account_code=default_debit_account_code,
            default_credit_account_code=default_credit_account_code,
            created_by=actor.user,
        )

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_MAPPING_PROFILE_CREATED,
        aggregate_type="EdimMappingProfile",
        aggregate_id=str(profile_public_id),
        idempotency_key=idempotency_key,
        data=EdimMappingProfileCreatedData(
            profile_public_id=str(profile_public_id),
            source_system_public_id=str(source_system.public_id),
            name=name,
            document_type=document_type,
            version=version,
            posting_policy=posting_policy,
            field_mappings=field_mappings or [],
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(profile, event=event)


@transaction.atomic
def update_mapping_profile(
    actor: ActorContext,
    profile_id: int,
    **updates,
) -> CommandResult:
    """
    Update a mapping profile. Creates a new version if field_mappings change.

    Args:
        actor: The actor context
        profile_id: ID of profile to update
        **updates: Field updates

    Returns:
        CommandResult with updated MappingProfile or error
    """
    require(actor, "edim.manage_mappings")

    try:
        profile = MappingProfile.objects.select_for_update().get(
            pk=profile_id, company=actor.company
        )
    except MappingProfile.DoesNotExist:
        return CommandResult.fail("Mapping profile not found.")

    if profile.status == MappingProfile.ProfileStatus.DEPRECATED:
        return CommandResult.fail("Cannot update a deprecated profile.")

    # Track changes
    changes = {}
    allowed_fields = {
        "name", "field_mappings", "transform_rules", "defaults",
        "validation_rules", "posting_policy",
        "default_debit_account_code", "default_credit_account_code"
    }

    for field, value in updates.items():
        if field in allowed_fields:
            old_value = getattr(profile, field)
            if old_value != value:
                changes[field] = {"old": old_value, "new": value}

    if not changes:
        return CommandResult.ok(profile)

    # If structural changes, increment version
    new_version = profile.version
    if "field_mappings" in changes or "transform_rules" in changes:
        new_version = profile.version + 1
        changes["version"] = {"old": profile.version, "new": new_version}

    # Apply changes within write context
    with command_writes_allowed():
        for field, change in changes.items():
            if field != "version":
                setattr(profile, field, change["new"])
        profile.version = new_version
        profile.save()

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_MAPPING_PROFILE_UPDATED,
        aggregate_type="EdimMappingProfile",
        aggregate_id=str(profile.public_id),
        idempotency_key=f"edim_mapping_profile.updated:{profile.public_id}:{_changes_hash(changes)}",
        data=EdimMappingProfileUpdatedData(
            profile_public_id=str(profile.public_id),
            changes=changes,
            new_version=new_version,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(profile, event=event)


@transaction.atomic
def activate_mapping_profile(
    actor: ActorContext,
    profile_id: int,
) -> CommandResult:
    """
    Activate a mapping profile, deprecating any previous active version.

    Args:
        actor: The actor context
        profile_id: ID of profile to activate

    Returns:
        CommandResult with activated MappingProfile or error
    """
    require(actor, "edim.manage_mappings")

    try:
        profile = MappingProfile.objects.select_for_update().get(
            pk=profile_id, company=actor.company
        )
    except MappingProfile.DoesNotExist:
        return CommandResult.fail("Mapping profile not found.")

    if profile.status == MappingProfile.ProfileStatus.ACTIVE:
        return CommandResult.fail("Profile is already active.")

    if profile.status == MappingProfile.ProfileStatus.DEPRECATED:
        return CommandResult.fail("Cannot activate a deprecated profile.")

    # Find and deprecate any currently active profile for this source_system + document_type
    previous_active_version = None
    with command_writes_allowed():
        active_profiles = MappingProfile.objects.filter(
            company=actor.company,
            source_system=profile.source_system,
            document_type=profile.document_type,
            status=MappingProfile.ProfileStatus.ACTIVE,
        ).exclude(pk=profile_id)

        for active_profile in active_profiles:
            previous_active_version = active_profile.version
            active_profile.status = MappingProfile.ProfileStatus.DEPRECATED
            active_profile.save(update_fields=["status", "updated_at"])

        # Activate the target profile
        profile.status = MappingProfile.ProfileStatus.ACTIVE
        profile.save(update_fields=["status", "updated_at"])

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_MAPPING_PROFILE_ACTIVATED,
        aggregate_type="EdimMappingProfile",
        aggregate_id=str(profile.public_id),
        idempotency_key=f"edim_mapping_profile.activated:{profile.public_id}",
        data=EdimMappingProfileActivatedData(
            profile_public_id=str(profile.public_id),
            version=profile.version,
            previous_active_version=previous_active_version,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(profile, event=event)


@transaction.atomic
def deprecate_mapping_profile(
    actor: ActorContext,
    profile_id: int,
) -> CommandResult:
    """
    Deprecate a mapping profile.

    Args:
        actor: The actor context
        profile_id: ID of profile to deprecate

    Returns:
        CommandResult with deprecated MappingProfile or error
    """
    require(actor, "edim.manage_mappings")

    try:
        profile = MappingProfile.objects.select_for_update().get(
            pk=profile_id, company=actor.company
        )
    except MappingProfile.DoesNotExist:
        return CommandResult.fail("Mapping profile not found.")

    if profile.status == MappingProfile.ProfileStatus.DEPRECATED:
        return CommandResult.fail("Profile is already deprecated.")

    # Deprecate within write context
    with command_writes_allowed():
        profile.status = MappingProfile.ProfileStatus.DEPRECATED
        profile.save(update_fields=["status", "updated_at"])

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_MAPPING_PROFILE_DEPRECATED,
        aggregate_type="EdimMappingProfile",
        aggregate_id=str(profile.public_id),
        idempotency_key=f"edim_mapping_profile.deprecated:{profile.public_id}",
        data=EdimMappingProfileDeprecatedData(
            profile_public_id=str(profile.public_id),
            version=profile.version,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(profile, event=event)


# =============================================================================
# Identity Crosswalk Commands
# =============================================================================

@transaction.atomic
def create_crosswalk(
    actor: ActorContext,
    source_system_id: int,
    object_type: str,
    external_id: str,
    external_label: str = "",
    nxentra_id: str = "",
    nxentra_label: str = "",
    status: str = IdentityCrosswalk.CrosswalkStatus.PROPOSED,
) -> CommandResult:
    """
    Create a new identity crosswalk entry.

    Args:
        actor: The actor context
        source_system_id: ID of the source system
        object_type: Type of object (ACCOUNT, CUSTOMER, etc.)
        external_id: ID in the external system
        external_label: Human-readable label from external system
        nxentra_id: public_id of the Nxentra entity
        nxentra_label: Human-readable label of Nxentra entity
        status: Crosswalk status (VERIFIED, PROPOSED, REJECTED)

    Returns:
        CommandResult with created IdentityCrosswalk or error
    """
    require(actor, "edim.manage_crosswalks")

    try:
        source_system = SourceSystem.objects.get(
            pk=source_system_id, company=actor.company
        )
    except SourceSystem.DoesNotExist:
        return CommandResult.fail("Source system not found.")

    # Validate object_type
    valid_object_types = [choice[0] for choice in IdentityCrosswalk.ObjectType.choices]
    if object_type not in valid_object_types:
        return CommandResult.fail(f"Invalid object_type '{object_type}'. Must be one of: {valid_object_types}")

    # Validate status
    valid_statuses = [choice[0] for choice in IdentityCrosswalk.CrosswalkStatus.choices]
    if status not in valid_statuses:
        return CommandResult.fail(f"Invalid status '{status}'. Must be one of: {valid_statuses}")

    # Check for duplicate
    if IdentityCrosswalk.objects.filter(
        company=actor.company,
        source_system=source_system,
        object_type=object_type,
        external_id=external_id,
    ).exists():
        return CommandResult.fail(
            f"Crosswalk entry already exists for {object_type}:{external_id} in this source system."
        )

    crosswalk_public_id = uuid.uuid4()

    idempotency_key = _idempotency_hash("edim_crosswalk.created", {
        "company_public_id": str(actor.company.public_id),
        "source_system_public_id": str(source_system.public_id),
        "object_type": object_type,
        "external_id": external_id,
    })

    # Create crosswalk within write context
    with command_writes_allowed():
        crosswalk = IdentityCrosswalk.objects.create(
            company=actor.company,
            public_id=crosswalk_public_id,
            source_system=source_system,
            object_type=object_type,
            external_id=external_id,
            external_label=external_label,
            nxentra_id=nxentra_id,
            nxentra_label=nxentra_label,
            status=status,
        )

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_CROSSWALK_CREATED,
        aggregate_type="EdimCrosswalk",
        aggregate_id=str(crosswalk_public_id),
        idempotency_key=idempotency_key,
        data=EdimCrosswalkCreatedData(
            crosswalk_public_id=str(crosswalk_public_id),
            source_system_public_id=str(source_system.public_id),
            object_type=object_type,
            external_id=external_id,
            external_label=external_label,
            nxentra_id=nxentra_id,
            nxentra_label=nxentra_label,
            status=status,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(crosswalk, event=event)


@transaction.atomic
def update_crosswalk(
    actor: ActorContext,
    crosswalk_id: int,
    **updates,
) -> CommandResult:
    """
    Update an identity crosswalk entry.

    Args:
        actor: The actor context
        crosswalk_id: ID of crosswalk to update
        **updates: Field updates (nxentra_id, nxentra_label, external_label)

    Returns:
        CommandResult with updated IdentityCrosswalk or error
    """
    require(actor, "edim.manage_crosswalks")

    try:
        crosswalk = IdentityCrosswalk.objects.select_for_update().get(
            pk=crosswalk_id, company=actor.company
        )
    except IdentityCrosswalk.DoesNotExist:
        return CommandResult.fail("Crosswalk entry not found.")

    # Track changes
    changes = {}
    allowed_fields = {"nxentra_id", "nxentra_label", "external_label"}

    for field, value in updates.items():
        if field in allowed_fields:
            old_value = getattr(crosswalk, field)
            if old_value != value:
                changes[field] = {"old": old_value, "new": value}

    if not changes:
        return CommandResult.ok(crosswalk)

    # Apply changes within write context
    with command_writes_allowed():
        for field, change in changes.items():
            setattr(crosswalk, field, change["new"])
        crosswalk.save()

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_CROSSWALK_UPDATED,
        aggregate_type="EdimCrosswalk",
        aggregate_id=str(crosswalk.public_id),
        idempotency_key=f"edim_crosswalk.updated:{crosswalk.public_id}:{_changes_hash(changes)}",
        data=EdimCrosswalkUpdatedData(
            crosswalk_public_id=str(crosswalk.public_id),
            changes=changes,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(crosswalk, event=event)


@transaction.atomic
def verify_crosswalk(
    actor: ActorContext,
    crosswalk_id: int,
) -> CommandResult:
    """
    Verify a crosswalk entry.

    Args:
        actor: The actor context
        crosswalk_id: ID of crosswalk to verify

    Returns:
        CommandResult with verified IdentityCrosswalk or error
    """
    require(actor, "edim.manage_crosswalks")

    try:
        crosswalk = IdentityCrosswalk.objects.select_for_update().get(
            pk=crosswalk_id, company=actor.company
        )
    except IdentityCrosswalk.DoesNotExist:
        return CommandResult.fail("Crosswalk entry not found.")

    if crosswalk.status == IdentityCrosswalk.CrosswalkStatus.VERIFIED:
        return CommandResult.fail("Crosswalk is already verified.")

    if not crosswalk.nxentra_id:
        return CommandResult.fail("Cannot verify crosswalk without a mapped Nxentra ID.")

    verified_at = timezone.now()

    # Verify within write context
    with command_writes_allowed():
        crosswalk.status = IdentityCrosswalk.CrosswalkStatus.VERIFIED
        crosswalk.verified_by = actor.user
        crosswalk.verified_at = verified_at
        crosswalk.save(update_fields=["status", "verified_by", "verified_at", "updated_at"])

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_CROSSWALK_VERIFIED,
        aggregate_type="EdimCrosswalk",
        aggregate_id=str(crosswalk.public_id),
        idempotency_key=f"edim_crosswalk.verified:{crosswalk.public_id}",
        data=EdimCrosswalkVerifiedData(
            crosswalk_public_id=str(crosswalk.public_id),
            verified_by_id=actor.user.id,
            verified_by_email=actor.user.email,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(crosswalk, event=event)


@transaction.atomic
def reject_crosswalk(
    actor: ActorContext,
    crosswalk_id: int,
    reason: str = "",
) -> CommandResult:
    """
    Reject a crosswalk entry.

    Args:
        actor: The actor context
        crosswalk_id: ID of crosswalk to reject
        reason: Reason for rejection

    Returns:
        CommandResult with rejected IdentityCrosswalk or error
    """
    require(actor, "edim.manage_crosswalks")

    try:
        crosswalk = IdentityCrosswalk.objects.select_for_update().get(
            pk=crosswalk_id, company=actor.company
        )
    except IdentityCrosswalk.DoesNotExist:
        return CommandResult.fail("Crosswalk entry not found.")

    if crosswalk.status == IdentityCrosswalk.CrosswalkStatus.REJECTED:
        return CommandResult.fail("Crosswalk is already rejected.")

    # Reject within write context
    with command_writes_allowed():
        crosswalk.status = IdentityCrosswalk.CrosswalkStatus.REJECTED
        crosswalk.save(update_fields=["status", "updated_at"])

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_CROSSWALK_REJECTED,
        aggregate_type="EdimCrosswalk",
        aggregate_id=str(crosswalk.public_id),
        idempotency_key=f"edim_crosswalk.rejected:{crosswalk.public_id}",
        data=EdimCrosswalkRejectedData(
            crosswalk_public_id=str(crosswalk.public_id),
            rejected_by_id=actor.user.id,
            rejected_by_email=actor.user.email,
            reason=reason,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(crosswalk, event=event)


# =============================================================================
# Ingestion Batch Commands (Stage, Map, Validate, Preview, Commit, Reject)
# =============================================================================

@transaction.atomic
def stage_batch(
    actor: ActorContext,
    source_system_id: int,
    file,
    filename: str,
    mapping_profile_id: int = None,
) -> CommandResult:
    """
    Stage a new ingestion batch by uploading a file.

    Args:
        actor: The actor context
        source_system_id: ID of the source system
        file: The uploaded file object
        filename: Original filename
        mapping_profile_id: Optional mapping profile to use

    Returns:
        CommandResult with created IngestionBatch or error
    """
    require(actor, "edim.stage_data")

    try:
        source_system = SourceSystem.objects.get(
            pk=source_system_id, company=actor.company
        )
    except SourceSystem.DoesNotExist:
        return CommandResult.fail("Source system not found.")

    if not source_system.is_active:
        return CommandResult.fail("Source system is not active.")

    # Determine ingestion type from filename extension
    extension = filename.lower().split(".")[-1] if "." in filename else ""
    ingestion_type_map = {
        "csv": IngestionBatch.IngestionType.FILE_CSV,
        "xlsx": IngestionBatch.IngestionType.FILE_XLSX,
        "xls": IngestionBatch.IngestionType.FILE_XLSX,
        "json": IngestionBatch.IngestionType.FILE_JSON,
    }
    ingestion_type = ingestion_type_map.get(extension)
    if not ingestion_type:
        return CommandResult.fail(f"Unsupported file type: {extension}. Supported: csv, xlsx, xls, json")

    # Get mapping profile if specified
    mapping_profile = None
    if mapping_profile_id:
        try:
            mapping_profile = MappingProfile.objects.get(
                pk=mapping_profile_id, company=actor.company
            )
        except MappingProfile.DoesNotExist:
            return CommandResult.fail("Mapping profile not found.")

    # Parse file and compute checksum
    from edim.parsers import detect_and_parse

    try:
        file_content = file.read()
        file.seek(0)
        file_checksum = hashlib.sha256(file_content).hexdigest()
        file_size = len(file_content)

        # Check for duplicate file
        existing_batch = IngestionBatch.objects.filter(
            company=actor.company,
            file_checksum=file_checksum,
        ).first()
        if existing_batch:
            # Return existing batch (idempotent)
            return CommandResult.ok(existing_batch)

        # Parse the file
        file.seek(0)
        _, records = detect_and_parse(file, filename)
    except Exception as e:
        return CommandResult.fail(f"Failed to parse file: {str(e)}")

    batch_public_id = uuid.uuid4()

    idempotency_key = _idempotency_hash("edim_batch.staged", {
        "company_public_id": str(actor.company.public_id),
        "file_checksum": file_checksum,
    })

    # Create batch and staged records within write context
    with command_writes_allowed():
        batch = IngestionBatch.objects.create(
            company=actor.company,
            public_id=batch_public_id,
            source_system=source_system,
            ingestion_type=ingestion_type,
            status=IngestionBatch.Status.STAGED,
            original_filename=filename,
            file=file,
            file_checksum=file_checksum,
            file_size_bytes=file_size,
            mapping_profile=mapping_profile,
            mapping_profile_version=mapping_profile.version if mapping_profile else None,
            total_records=len(records),
            staged_by=actor.user,
        )

        # Create staged records
        for row_number, raw_payload in enumerate(records, start=1):
            row_hash = StagedRecord.compute_row_hash(raw_payload)
            StagedRecord.objects.create(
                batch=batch,
                company=actor.company,
                row_number=row_number,
                raw_payload=raw_payload,
                row_hash=row_hash,
            )

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_BATCH_STAGED,
        aggregate_type="EdimBatch",
        aggregate_id=str(batch_public_id),
        idempotency_key=idempotency_key,
        data=EdimBatchStagedData(
            batch_public_id=str(batch_public_id),
            source_system_public_id=str(source_system.public_id),
            source_system_code=source_system.code,
            ingestion_type=ingestion_type,
            original_filename=filename,
            file_checksum=file_checksum,
            total_records=len(records),
            staged_by_id=actor.user.id,
            staged_by_email=actor.user.email,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(batch, event=event)


@transaction.atomic
def map_batch(
    actor: ActorContext,
    batch_id: int,
    mapping_profile_id: int = None,
) -> CommandResult:
    """
    Apply mapping profile to all staged records in a batch.

    Args:
        actor: The actor context
        batch_id: ID of the batch to map
        mapping_profile_id: Optional mapping profile (overrides batch's profile)

    Returns:
        CommandResult with mapped IngestionBatch or error
    """
    require(actor, "edim.stage_data")

    try:
        batch = IngestionBatch.objects.select_for_update().get(
            pk=batch_id, company=actor.company
        )
    except IngestionBatch.DoesNotExist:
        return CommandResult.fail("Batch not found.")

    if batch.status != IngestionBatch.Status.STAGED:
        return CommandResult.fail(f"Batch must be in STAGED status to map. Current: {batch.status}")

    # Get mapping profile
    if mapping_profile_id:
        try:
            mapping_profile = MappingProfile.objects.get(
                pk=mapping_profile_id, company=actor.company
            )
        except MappingProfile.DoesNotExist:
            return CommandResult.fail("Mapping profile not found.")
    elif batch.mapping_profile:
        mapping_profile = batch.mapping_profile
    else:
        return CommandResult.fail("No mapping profile specified.")

    # Apply mapping to all records
    from edim.mappers import apply_mapping

    mapped_count = 0
    error_count = 0

    with command_writes_allowed():
        records = batch.records.all()
        for record in records:
            mapped_payload, errors = apply_mapping(record.raw_payload, mapping_profile)
            record.mapped_payload = mapped_payload
            record.mapping_errors = errors
            record.save(update_fields=["mapped_payload", "mapping_errors"])

            if errors:
                error_count += 1
            else:
                mapped_count += 1

        # Update batch
        batch.status = IngestionBatch.Status.MAPPED
        batch.mapping_profile = mapping_profile
        batch.mapping_profile_version = mapping_profile.version
        batch.mapped_records = mapped_count
        batch.error_count = error_count
        batch.save()

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_BATCH_MAPPED,
        aggregate_type="EdimBatch",
        aggregate_id=str(batch.public_id),
        idempotency_key=f"edim_batch.mapped:{batch.public_id}:{mapping_profile.version}",
        data=EdimBatchMappedData(
            batch_public_id=str(batch.public_id),
            mapping_profile_public_id=str(mapping_profile.public_id),
            mapping_profile_version=mapping_profile.version,
            total_records=batch.total_records,
            mapped_records=mapped_count,
            error_count=error_count,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(batch, event=event)


@transaction.atomic
def validate_batch(
    actor: ActorContext,
    batch_id: int,
) -> CommandResult:
    """
    Validate all mapped records in a batch.

    Args:
        actor: The actor context
        batch_id: ID of the batch to validate

    Returns:
        CommandResult with validated IngestionBatch or error
    """
    require(actor, "edim.stage_data")

    try:
        batch = IngestionBatch.objects.select_for_update().get(
            pk=batch_id, company=actor.company
        )
    except IngestionBatch.DoesNotExist:
        return CommandResult.fail("Batch not found.")

    if batch.status != IngestionBatch.Status.MAPPED:
        return CommandResult.fail(f"Batch must be in MAPPED status to validate. Current: {batch.status}")

    # Get crosswalks for this source system
    crosswalks = IdentityCrosswalk.objects.filter(
        company=actor.company,
        source_system=batch.source_system,
        status=IdentityCrosswalk.CrosswalkStatus.VERIFIED,
    )

    # Validate all records
    from edim.validators import validate_record

    validated_count = 0
    error_count = 0
    validation_summary = {"errors_by_type": {}}

    with command_writes_allowed():
        records = batch.records.all()
        for record in records:
            if not record.mapped_payload:
                record.validation_errors = ["No mapped payload"]
                record.is_valid = False
                record.save(update_fields=["validation_errors", "is_valid"])
                error_count += 1
                continue

            is_valid, errors, resolved_accounts = validate_record(
                record.mapped_payload, actor.company, crosswalks
            )
            record.validation_errors = errors
            record.is_valid = is_valid
            record.resolved_accounts = resolved_accounts
            record.save(update_fields=["validation_errors", "is_valid", "resolved_accounts"])

            if is_valid:
                validated_count += 1
            else:
                error_count += 1
                for error in errors:
                    error_type = error.split(":")[0] if ":" in error else "other"
                    validation_summary["errors_by_type"][error_type] = (
                        validation_summary["errors_by_type"].get(error_type, 0) + 1
                    )

        # Update batch status
        if error_count == 0:
            batch.status = IngestionBatch.Status.VALIDATED
        # If there are errors, stay in MAPPED so user can fix issues
        batch.validated_records = validated_count
        batch.error_count = error_count
        batch.save()

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_BATCH_VALIDATED,
        aggregate_type="EdimBatch",
        aggregate_id=str(batch.public_id),
        idempotency_key=f"edim_batch.validated:{batch.public_id}:{timezone.now().isoformat()}",
        data=EdimBatchValidatedData(
            batch_public_id=str(batch.public_id),
            total_records=batch.total_records,
            validated_records=validated_count,
            error_count=error_count,
            validation_summary=validation_summary,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(batch, event=event)


@transaction.atomic
def preview_batch(
    actor: ActorContext,
    batch_id: int,
) -> CommandResult:
    """
    Preview a validated batch, showing proposed journal entries.

    Args:
        actor: The actor context
        batch_id: ID of the batch to preview

    Returns:
        CommandResult with batch and preview data or error
    """
    require(actor, "edim.review_batches")

    try:
        batch = IngestionBatch.objects.select_for_update().get(
            pk=batch_id, company=actor.company
        )
    except IngestionBatch.DoesNotExist:
        return CommandResult.fail("Batch not found.")

    if batch.status != IngestionBatch.Status.VALIDATED:
        return CommandResult.fail(f"Batch must be in VALIDATED status to preview. Current: {batch.status}")

    # Build preview data from validated records
    preview_summary = {
        "total_entries": 0,
        "total_debit": "0",
        "total_credit": "0",
        "proposed_entries": [],
    }

    # Group records into proposed journal entries (simplified - group by date)
    from decimal import Decimal
    from collections import defaultdict

    entries_by_date = defaultdict(list)
    records = batch.records.filter(is_valid=True)

    for record in records:
        payload = record.mapped_payload
        entry_date = payload.get("date", "unknown")
        entries_by_date[entry_date].append(record)

    total_debit = Decimal("0")
    total_credit = Decimal("0")

    for entry_date, entry_records in entries_by_date.items():
        entry_lines = []
        for record in entry_records:
            payload = record.mapped_payload
            debit = Decimal(str(payload.get("debit", "0")))
            credit = Decimal(str(payload.get("credit", "0")))
            total_debit += debit
            total_credit += credit
            entry_lines.append({
                "account_code": payload.get("account_code", ""),
                "description": payload.get("description", ""),
                "debit": str(debit),
                "credit": str(credit),
            })

        preview_summary["proposed_entries"].append({
            "date": entry_date,
            "memo": f"Imported from {batch.original_filename}",
            "lines": entry_lines,
        })

    preview_summary["total_entries"] = len(preview_summary["proposed_entries"])
    preview_summary["total_debit"] = str(total_debit)
    preview_summary["total_credit"] = str(total_credit)

    # Update batch status
    with command_writes_allowed():
        batch.status = IngestionBatch.Status.PREVIEWED
        batch.save(update_fields=["status", "updated_at"])

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_BATCH_PREVIEWED,
        aggregate_type="EdimBatch",
        aggregate_id=str(batch.public_id),
        idempotency_key=f"edim_batch.previewed:{batch.public_id}",
        data=EdimBatchPreviewedData(
            batch_public_id=str(batch.public_id),
            previewed_by_id=actor.user.id,
            previewed_by_email=actor.user.email,
            preview_summary=preview_summary,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok({"batch": batch, "preview": preview_summary}, event=event)


@transaction.atomic
def commit_batch(
    actor: ActorContext,
    batch_id: int,
) -> CommandResult:
    """
    Commit a batch, creating journal entries via accounting commands.

    Args:
        actor: The actor context
        batch_id: ID of the batch to commit

    Returns:
        CommandResult with committed IngestionBatch or error
    """
    require(actor, "edim.commit_batches")

    try:
        batch = IngestionBatch.objects.select_for_update().get(
            pk=batch_id, company=actor.company
        )
    except IngestionBatch.DoesNotExist:
        return CommandResult.fail("Batch not found.")

    # Allow commit from PREVIEWED status, or VALIDATED if posting_policy is not MANUAL_APPROVAL
    if batch.status == IngestionBatch.Status.PREVIEWED:
        pass  # OK
    elif batch.status == IngestionBatch.Status.VALIDATED:
        if batch.mapping_profile and batch.mapping_profile.posting_policy == MappingProfile.PostingPolicy.MANUAL_APPROVAL:
            return CommandResult.fail("Batch requires preview before commit (MANUAL_APPROVAL policy).")
    else:
        return CommandResult.fail(f"Batch must be in VALIDATED or PREVIEWED status to commit. Current: {batch.status}")

    # Import accounting commands
    from accounting.commands import create_journal_entry, save_journal_entry_complete, post_journal_entry

    # Build and create journal entries
    from decimal import Decimal
    from collections import defaultdict
    from datetime import date as date_cls

    entries_by_date = defaultdict(list)
    records = batch.records.filter(is_valid=True)

    for record in records:
        payload = record.mapped_payload
        entry_date = payload.get("date", "unknown")
        entries_by_date[entry_date].append(record)

    committed_entry_public_ids = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")

    for entry_date, entry_records in entries_by_date.items():
        # Parse date
        try:
            if isinstance(entry_date, str):
                parsed_date = date_cls.fromisoformat(entry_date)
            else:
                parsed_date = entry_date
        except ValueError:
            raise ValueError(f"Invalid date format: {entry_date}")

        # Build lines
        lines = []
        from accounting.models import Account

        for record in entry_records:
            payload = record.mapped_payload
            account_code = payload.get("account_code", "")

            # Look up account by code
            account = Account.objects.filter(
                company=actor.company,
                code=account_code,
            ).first()

            if not account:
                # Try resolved_accounts
                resolved_id = record.resolved_accounts.get(account_code)
                if resolved_id:
                    account = Account.objects.filter(
                        company=actor.company,
                        public_id=resolved_id,
                    ).first()

            if not account:
                raise ValueError(f"Account not found: {account_code}")

            debit = Decimal(str(payload.get("debit", "0")))
            credit = Decimal(str(payload.get("credit", "0")))
            total_debit += debit
            total_credit += credit

            lines.append({
                "account_id": account.id,
                "description": payload.get("description", ""),
                "debit": debit,
                "credit": credit,
            })

        # Create journal entry
        result = create_journal_entry(
            actor=actor,
            date=parsed_date,
            memo=f"Imported from {batch.original_filename}",
            lines=lines,
        )
        if not result.success:
            raise ValueError(f"Failed to create journal entry: {result.error}")

        entry = result.data

        # Save as complete (DRAFT)
        result = save_journal_entry_complete(
            actor=actor,
            entry_id=entry.id,
            lines=lines,
        )
        if not result.success:
            raise ValueError(f"Failed to save journal entry: {result.error}")

        # Post if policy allows
        posting_policy = batch.mapping_profile.posting_policy if batch.mapping_profile else MappingProfile.PostingPolicy.MANUAL_APPROVAL
        trust_level = batch.source_system.trust_level

        if posting_policy == MappingProfile.PostingPolicy.AUTO_POST and trust_level == SourceSystem.TrustLevel.FINANCIAL:
            result = post_journal_entry(actor=actor, entry_id=entry.id)
            if not result.success:
                raise ValueError(f"Failed to post journal entry: {result.error}")

        committed_entry_public_ids.append(str(entry.public_id))

    # Update batch
    committed_at = timezone.now()
    with command_writes_allowed():
        batch.status = IngestionBatch.Status.COMMITTED
        batch.committed_by = actor.user
        batch.committed_at = committed_at
        batch.committed_entry_public_ids = committed_entry_public_ids
        batch.save()

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_BATCH_COMMITTED,
        aggregate_type="EdimBatch",
        aggregate_id=str(batch.public_id),
        idempotency_key=f"edim_batch.committed:{batch.public_id}",
        data=EdimBatchCommittedData(
            batch_public_id=str(batch.public_id),
            committed_by_id=actor.user.id,
            committed_by_email=actor.user.email,
            journal_entry_public_ids=committed_entry_public_ids,
            total_entries_created=len(committed_entry_public_ids),
            total_debit=str(total_debit),
            total_credit=str(total_credit),
            posting_policy=posting_policy if batch.mapping_profile else "",
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(batch, event=event)


@transaction.atomic
def reject_batch(
    actor: ActorContext,
    batch_id: int,
    reason: str = "",
) -> CommandResult:
    """
    Reject an ingestion batch.

    Args:
        actor: The actor context
        batch_id: ID of the batch to reject
        reason: Reason for rejection

    Returns:
        CommandResult with rejected IngestionBatch or error
    """
    require(actor, "edim.review_batches")

    try:
        batch = IngestionBatch.objects.select_for_update().get(
            pk=batch_id, company=actor.company
        )
    except IngestionBatch.DoesNotExist:
        return CommandResult.fail("Batch not found.")

    if batch.status in (IngestionBatch.Status.COMMITTED, IngestionBatch.Status.REJECTED):
        return CommandResult.fail(f"Cannot reject batch in {batch.status} status.")

    rejected_at = timezone.now()

    # Reject within write context
    with command_writes_allowed():
        batch.status = IngestionBatch.Status.REJECTED
        batch.rejected_by = actor.user
        batch.rejected_at = rejected_at
        batch.rejection_reason = reason
        batch.save()

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.EDIM_BATCH_REJECTED,
        aggregate_type="EdimBatch",
        aggregate_id=str(batch.public_id),
        idempotency_key=f"edim_batch.rejected:{batch.public_id}",
        data=EdimBatchRejectedData(
            batch_public_id=str(batch.public_id),
            rejected_by_id=actor.user.id,
            rejected_by_email=actor.user.email,
            rejection_reason=reason,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok(batch, event=event)
