# accounts/commands.py
"""
Command layer for accounts/authorization operations.

ALL security-critical mutations MUST go through these commands:
- Company switching
- User creation/updates
- Membership management
- Permission grants/revocations

This ensures:
1. Consistent validation
2. Audit trail via events
3. Single point of enforcement
"""

from django.db import transaction
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
import json
import hashlib
import uuid

from accounts.authz import ActorContext, require, PermissionDenied

from accounts.models import Company, CompanyMembership, NxPermission

from events.emitter import emit_event, emit_event_no_actor
from events.types import (
    EventTypes,
    CompanyCreatedData,
    CompanyUpdatedData,
    CompanySettingsChangedData,
    UserRegisteredData,
    PermissionGrantedData,
    PermissionRevokedData,
    MembershipCreatedData,
    MembershipReactivatedData,
    UserCreatedData,
    UserCompanySwitchedData,
    UserPasswordChangedData,
    UserUpdatedData,
    MembershipRoleChangedData,
    MembershipDeactivatedData,
    MembershipPermissionsUpdatedData,
)

User = get_user_model()


class CommandResult:
    def __init__(self, success: bool, data=None, error: str = None, event=None, events=None):
        self.success = success
        self.data = data
        self.error = error

        # Primary event (optional)
        self.event = event

        # Always a list
        if events is None:
            self.events = ([] if event is None else [event])
        else:
            self.events = list(events)

    @classmethod
    def ok(cls, data=None, event=None, events=None):
        return cls(success=True, data=data, event=event, events=events)

    @classmethod
    def fail(cls, error: str):
        return cls(success=False, error=error)


def _changes_hash(changes: dict) -> str:
    payload = str(sorted((k, v.get("new")) for k, v in changes.items())).encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def _idempotency_hash(prefix: str, payload: dict) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(normalized).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _process_projections(company) -> None:
    from projections.base import projection_registry
    for projection in projection_registry.all():
        projection.process_pending(company, limit=1000)


# =============================================================================
# Registration (Company + User + Membership atomic creation)
# =============================================================================

@transaction.atomic
def register_signup(
    email: str,
    password: str,
    company_name: str,
    name: str = "",
) -> CommandResult:
    """
    Register a new user with a new company.
    
    This is the ONLY way to create a company owner. It atomically:
    1. Creates the company with unique slug (retry on collision)
    2. Creates the user
    3. Creates the owner membership
    4. Sets the active company
    5. Emits the registration event
    
    Args:
        email: User's email (must be unique)
        password: User's password
        company_name: Name of the company to create
        name: User's display name (optional)
    
    Returns:
        CommandResult with user, company, membership
    """
    from django.utils.text import slugify
    
    # Validate email uniqueness
    email = email.lower().strip()
    if User.objects.filter(email=email).exists():
        return CommandResult.fail(f"User with email '{email}' already exists.")
    
    if not company_name or not company_name.strip():
        return CommandResult.fail("Company name is required.")
    
    if not password or len(password) < 8:
        return CommandResult.fail("Password must be at least 8 characters.")
    
    # Generate unique slug with retry on collision
    base_slug = slugify(company_name.strip())
    if not base_slug:
        base_slug = "company"
    
    slug = base_slug
    max_attempts = 10
    for attempt in range(max_attempts):
        if Company.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{attempt + 1}"
            if attempt == max_attempts - 1:
                return CommandResult.fail("Could not generate unique company slug. Please try a different name.")
            continue
        break

    company_public_id = uuid.uuid4()
    user_public_id = uuid.uuid4()
    membership_public_id = uuid.uuid4()
    password_hash = make_password(password)

    company = Company.objects.create(
        name=company_name.strip(),
        slug=slug,
        public_id=company_public_id,
    )

    event_company = emit_event_no_actor(
        company=company,
        user=None,
        event_type=EventTypes.COMPANY_CREATED,
        aggregate_type="Company",
        aggregate_id=str(company_public_id),
        idempotency_key=_idempotency_hash("company.created", {
            "company_public_id": str(company_public_id),
            "name": company_name.strip(),
            "slug": slug,
        }),
        data=CompanyCreatedData(
            company_public_id=str(company_public_id),
            name=company_name.strip(),
            slug=slug,
        ).to_dict(),
    )

    event_user = emit_event_no_actor(
        company=company,
        user=None,
        event_type=EventTypes.USER_CREATED,
        aggregate_type="User",
        aggregate_id=str(user_public_id),
        idempotency_key=_idempotency_hash("user.created", {
            "company_public_id": str(company_public_id),
            "user_public_id": str(user_public_id),
            "email": email,
            "name": name.strip() if name else "",
        }),
        data=UserCreatedData(
            user_public_id=str(user_public_id),
            email=email,
            name=name.strip() if name else "",
            created_by_user_public_id=None,
            password_hash=password_hash,
        ).to_dict(),
    )

    event_membership = emit_event_no_actor(
        company=company,
        user=None,
        event_type=EventTypes.MEMBERSHIP_CREATED,
        aggregate_type="CompanyMembership",
        aggregate_id=str(membership_public_id),
        idempotency_key=_idempotency_hash("membership.created", {
            "company_public_id": str(company_public_id),
            "user_public_id": str(user_public_id),
            "membership_public_id": str(membership_public_id),
            "role": CompanyMembership.Role.OWNER,
        }),
        data=MembershipCreatedData(
            membership_public_id=str(membership_public_id),
            company_public_id=str(company_public_id),
            user_public_id=str(user_public_id),
            role=CompanyMembership.Role.OWNER,
            is_active=True,
        ).to_dict(),
    )

    event_switch = emit_event_no_actor(
        company=company,
        user=None,
        event_type=EventTypes.USER_COMPANY_SWITCHED,
        aggregate_type="User",
        aggregate_id=str(user_public_id),
        idempotency_key=_idempotency_hash("user.company_switched", {
            "user_public_id": str(user_public_id),
            "to_company_public_id": str(company_public_id),
        }),
        data=UserCompanySwitchedData(
            user_public_id=str(user_public_id),
            email=email,
            from_company_public_id=None,
            to_company_public_id=str(company_public_id),
            to_company_name=company_name.strip(),
        ).to_dict(),
    )
    
    # Emit registration event
    # FIX: Added idempotency_key
    event = emit_event_no_actor(
        company=company,
        user=None,
        event_type=EventTypes.USER_REGISTERED,
        aggregate_type="User",
        aggregate_id=str(user_public_id),
        idempotency_key=_idempotency_hash("user.registered", {
            "company_public_id": str(company_public_id),
            "user_public_id": str(user_public_id),
            "email": email,
        }),
        data=UserRegisteredData(
            user_public_id=str(user_public_id),
            email=email,
            name=name.strip() if name else "",
            company_public_id=str(company_public_id),
            company_name=company_name.strip(),
            membership_public_id=str(membership_public_id),
            password_hash=password_hash,
        ).to_dict(),
        metadata={
            "is_owner": True,
            "registration_type": "signup",
        },
    )

    _process_projections(company)

    company = Company.objects.get(public_id=company_public_id)
    user = User.objects.get(public_id=user_public_id)
    membership = CompanyMembership.objects.get(public_id=membership_public_id)

    return CommandResult.ok({
        "user": user,
        "company": company,
        "membership": membership,
    }, event=event, events=[event_company, event_user, event_membership, event_switch, event])


# =============================================================================
# Company Switching
# =============================================================================

@transaction.atomic
def switch_active_company(user, target_company_id: int) -> CommandResult:
    """
    Switch user's active company.
    
    This is a SECURITY-CRITICAL operation that changes what the user can access.
    Must be audited.
    
    Args:
        user: The user switching companies (not ActorContext - they may not have one yet)
        target_company_id: ID of company to switch to
    
    Returns:
        CommandResult with company info and role
    """
    if isinstance(user, ActorContext):
        user = user.user

    if not user or not user.is_authenticated:
        return CommandResult.fail("Authentication required.")
    
    try:
        target_company = Company.objects.get(pk=target_company_id, is_active=True)
    except Company.DoesNotExist:
        return CommandResult.fail("Company not found or inactive.")
    
    # Verify active membership exists
    try:
        membership = CompanyMembership.objects.select_related("company").get(
            user=user, company=target_company, is_active=True
        )
    except CompanyMembership.DoesNotExist:
        return CommandResult.fail("You do not have an active membership for that company.")
    
    # Capture old company for audit
    old_company_public_id = user.active_company.public_id if user.active_company else None
    old_company_name = user.active_company.name if user.active_company else None
    
    # Update active company immediately
    user.active_company = target_company
    user.save(update_fields=["active_company"])

    # Emit audit event
    # FIX: Added idempotency_key with timestamp for uniqueness
    event = emit_event_no_actor(
        company=target_company,
        user=user,
        event_type=EventTypes.USER_COMPANY_SWITCHED,
        aggregate_type="User",
        aggregate_id=str(user.public_id),
        idempotency_key=_idempotency_hash("user.company_switched", {
            "user_public_id": str(user.public_id),
            "from_company_public_id": str(old_company_public_id) if old_company_public_id else None,
            "to_company_public_id": str(target_company.public_id),
        }),
        data=UserCompanySwitchedData(
            user_public_id=str(user.public_id),
            email=user.email,
            from_company_public_id=str(old_company_public_id) if old_company_public_id else None,
            to_company_public_id=str(target_company.public_id),
            to_company_name=target_company.name,
        ).to_dict(),
        metadata={
            "from_company_name": old_company_name,
        },
    )

    _process_projections(target_company)
    
    return CommandResult.ok({
        "company_id": target_company.id,
        "company_public_id": str(target_company.public_id),
        "company_name": str(target_company),
        "role": membership.role,
        "membership_id": membership.id,
        "membership_public_id": str(membership.public_id),
    }, event=event)


# =============================================================================
# User Management
# =============================================================================

@transaction.atomic
def create_user_with_membership(
    actor,  # ActorContext
    email: str,
    name: str,
    password: str,
    role: str = CompanyMembership.Role.USER,
) -> CommandResult:
    """
    Create a new user and add them to the actor's company.
    
    Args:
        actor: The actor context (must have company.manage_users permission)
        email: User's email (must be unique)
        name: User's display name
        password: Initial password
        role: Role in the company (OWNER, ADMIN, USER, VIEWER)
    
    Returns:
        CommandResult with user and membership
    """
    from accounts.authz import require
    require(actor, "company.manage_users")
    
    # Validate email uniqueness
    if User.objects.filter(email=email).exists():
        return CommandResult.fail(f"User with email '{email}' already exists.")
    
    # Validate role
    valid_roles = [r[0] for r in CompanyMembership.Role.choices]
    if role not in valid_roles:
        return CommandResult.fail(f"Invalid role. Must be one of: {valid_roles}")
    
    # Cannot create OWNER - there can only be one, set at company creation
    if role == CompanyMembership.Role.OWNER and not actor.is_owner:
        return CommandResult.fail("Only the company owner can assign OWNER role.")
    
    user_public_id = uuid.uuid4()
    membership_public_id = uuid.uuid4()
    password_hash = make_password(password)

    event_user = emit_event(
        actor=actor,
        event_type=EventTypes.USER_CREATED,
        aggregate_type="User",
        aggregate_id=str(user_public_id),
        idempotency_key=_idempotency_hash("user.created", {
            "company_public_id": str(actor.company.public_id),
            "user_public_id": str(user_public_id),
            "email": email,
            "name": name,
        }),
        data=UserCreatedData(
            user_public_id=str(user_public_id),
            email=email,
            name=name,
            created_by_user_public_id=str(actor.user.public_id),
            password_hash=password_hash,
        ).to_dict(),
        metadata={"source": "admin"},
    )

    event_membership = emit_event(
        actor=actor,
        event_type=EventTypes.MEMBERSHIP_CREATED,
        aggregate_type="CompanyMembership",
        aggregate_id=str(membership_public_id),
        idempotency_key=_idempotency_hash("membership.created", {
            "company_public_id": str(actor.company.public_id),
            "user_public_id": str(user_public_id),
            "membership_public_id": str(membership_public_id),
            "role": role,
        }),
        data=MembershipCreatedData(
            membership_public_id=str(membership_public_id),
            company_public_id=str(actor.company.public_id),
            user_public_id=str(user_public_id),
            role=role,
        ).to_dict(),
    )

    event_switch = emit_event(
        actor=actor,
        event_type=EventTypes.USER_COMPANY_SWITCHED,
        aggregate_type="User",
        aggregate_id=str(user_public_id),
        idempotency_key=_idempotency_hash("user.company_switched", {
            "user_public_id": str(user_public_id),
            "to_company_public_id": str(actor.company.public_id),
        }),
        data=UserCompanySwitchedData(
            user_public_id=str(user_public_id),
            email=email,
            from_company_public_id=None,
            to_company_public_id=str(actor.company.public_id),
            to_company_name=actor.company.name,
        ).to_dict(),
    )

    _process_projections(actor.company)
    user = User.objects.get(public_id=user_public_id)
    membership = CompanyMembership.objects.get(public_id=membership_public_id)

    return CommandResult.ok(
        {"user": user, "membership": membership},
        event=event_membership,
        events=[event_user, event_membership, event_switch],
    )


@transaction.atomic
def update_user(
    actor,  # ActorContext
    user_id: int,
    **updates,
) -> CommandResult:
    """
    Update a user's profile.
    
    Users can update their own profile.
    Admins can update any user in their company.
    
    Args:
        actor: The actor context
        user_id: ID of user to update
        **updates: Field updates (name, email)
    
    Returns:
        CommandResult with updated user
    """
    try:
        target_user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return CommandResult.fail("User not found.")
    
    # Permission check: self or admin
    is_self = actor.user.id == target_user.id
    if not is_self:
        from accounts.authz import require
        require(actor, "company.manage_users")
        
        # Verify target user is in actor's company unless actor is owner
        if not actor.is_owner:
            if not CompanyMembership.objects.filter(
                user=target_user, company=actor.company, is_active=True
            ).exists():
                return CommandResult.fail("User is not a member of your company.")
    
    # Track changes
    changes = {}
    allowed_fields = {"name", "name_ar", "email"}
    
    for field, value in updates.items():
        if field in allowed_fields:
            old_value = getattr(target_user, field)
            if old_value != value:
                changes[field] = {"old": old_value, "new": value}
                setattr(target_user, field, value)
    
    if not changes:
        return CommandResult.ok({"user": target_user})
    
    # Validate email uniqueness if changing
    if "email" in changes:
        if User.objects.filter(email=updates["email"]).exclude(pk=user_id).exists():
            return CommandResult.fail("Email already in use.")
    
    # Emit event
    # Create a deterministic hash of changes for idempotency
    changes_hash = hashlib.sha256(
        str(sorted((k, v['new']) for k, v in changes.items())).encode()
    ).hexdigest()[:12]
    
    event = emit_event(
        actor=actor,
        event_type=EventTypes.USER_UPDATED,
        aggregate_type="User",
        aggregate_id=str(target_user.public_id),
        idempotency_key=_idempotency_hash("user.updated", {
            "user_public_id": str(target_user.public_id),
            "changes_hash": changes_hash,
        }),
        data=UserUpdatedData(
            user_public_id=str(target_user.public_id),
            email=target_user.email,
            changes=changes,
        ).to_dict(),
    )

    _process_projections(actor.company)
    target_user = User.objects.get(public_id=target_user.public_id)
    return CommandResult.ok({"user": target_user}, event=event)


@transaction.atomic
def set_user_password(
    actor,  # ActorContext
    user_id: int,
    new_password: str,
) -> CommandResult:
    """
    Set a user's password.
    
    Users can change their own password.
    Admins can reset any user's password in their company.
    
    Args:
        actor: The actor context
        user_id: ID of user
        new_password: New password
    
    Returns:
        CommandResult
    """
    try:
        target_user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return CommandResult.fail("User not found.")
    
    # Permission check: self or admin
    is_self = actor.user.id == target_user.id
    if not is_self:
        from accounts.authz import require
        require(actor, "company.manage_users")
        
        # Verify target user is in actor's company
        if not CompanyMembership.objects.filter(
            user=target_user, company=actor.company, is_active=True
        ).exists():
            return CommandResult.fail("User is not a member of your company.")
    
    # Emit audit event (no password in event data!)
    # FIX: Use timestamp to make idempotency key unique per password change
    password_hash = make_password(new_password)
    event = emit_event(
        actor=actor,
        event_type=EventTypes.USER_PASSWORD_CHANGED,
        aggregate_type="User",
        aggregate_id=str(target_user.public_id),
        idempotency_key=_idempotency_hash("user.password_changed", {
            "user_public_id": str(target_user.public_id),
            "password_hash": password_hash,
        }),
        data=UserPasswordChangedData(
            user_public_id=str(target_user.public_id),
            email=target_user.email,
            changed_by_self=is_self,
            password_hash=password_hash,
        ).to_dict()
    )
    _process_projections(actor.company)
    return CommandResult.ok({"success": True}, event=event)


# =============================================================================
# Membership Management
# =============================================================================

@transaction.atomic
def add_user_to_company(
    actor,  # ActorContext
    user_id: int,
    role: str = CompanyMembership.Role.USER,
) -> CommandResult:
    """
    Add an existing user to the actor's company.
    
    Args:
        actor: The actor context
        user_id: ID of existing user
        role: Role in the company
    
    Returns:
        CommandResult with membership
    """
    from accounts.authz import require
    require(actor, "company.manage_users")
    
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return CommandResult.fail("User not found.")
    
    # Check if already a member
    existing = CompanyMembership.objects.filter(
        user=user, company=actor.company
    ).first()
    
    was_reactivated = False
    membership_public_id = uuid.uuid4()

    if existing:
        if existing.is_active:
            return CommandResult.fail("User is already a member of this company.")
        was_reactivated = True
        membership_public_id = existing.public_id

    # Emit event
    if was_reactivated:
        event_type = EventTypes.MEMBERSHIP_REACTIVATED
        idempotency_key = _idempotency_hash("membership.reactivated", {
            "company_public_id": str(actor.company.public_id),
            "user_public_id": str(user.public_id),
            "membership_public_id": str(membership_public_id),
            "role": role,
        })
        data = MembershipReactivatedData(
            membership_public_id=str(membership_public_id),
            company_public_id=str(actor.company.public_id),
            user_public_id=str(user.public_id),
            role=role,
            reactivated_by_user_public_id=str(actor.user.public_id),
        ).to_dict()
    else:
        event_type = EventTypes.MEMBERSHIP_CREATED
        idempotency_key = _idempotency_hash("membership.created", {
            "company_public_id": str(actor.company.public_id),
            "user_public_id": str(user.public_id),
            "membership_public_id": str(membership_public_id),
            "role": role,
        })
        data = MembershipCreatedData(
            membership_public_id=str(membership_public_id),
            company_public_id=str(actor.company.public_id),
            user_public_id=str(user.public_id),
            role=role,
            is_active=True,
        ).to_dict()

    event = emit_event(
        actor=actor,
        event_type=event_type,
        aggregate_type="CompanyMembership",
        aggregate_id=str(membership_public_id),
        idempotency_key=idempotency_key,
        data=data,
    )

    _process_projections(actor.company)
    membership = CompanyMembership.objects.get(public_id=membership_public_id)
    return CommandResult.ok(membership, event=event)


@transaction.atomic
def update_membership_role(
    actor,  # ActorContext
    membership_id: int,
    new_role: str,
) -> CommandResult:
    """
    Update a membership's role.
    
    Args:
        actor: The actor context
        membership_id: ID of membership
        new_role: New role
    
    Returns:
        CommandResult with updated membership
    """
    from accounts.authz import require
    require(actor, "company.manage_users")
    
    try:
        membership = CompanyMembership.objects.select_related("user", "company").get(
            pk=membership_id, company=actor.company
        )
    except CompanyMembership.DoesNotExist:
        return CommandResult.fail("Membership not found.")
    
    # Validate role
    valid_roles = [r[0] for r in CompanyMembership.Role.choices]
    if new_role not in valid_roles:
        return CommandResult.fail(f"Invalid role. Must be one of: {valid_roles}")
    
    # Cannot change OWNER role unless you're the owner
    if membership.role == CompanyMembership.Role.OWNER and not actor.is_owner:
        return CommandResult.fail("Cannot modify the owner's role.")
    
    if new_role == CompanyMembership.Role.OWNER and not actor.is_owner:
        return CommandResult.fail("Only the owner can assign OWNER role.")
    
    # Cannot demote yourself if you're the only owner
    if (membership.user_id == actor.user.id and 
        membership.role == CompanyMembership.Role.OWNER and 
        new_role != CompanyMembership.Role.OWNER):
        return CommandResult.fail("Cannot demote yourself from owner. Transfer ownership first.")
    
    # Capture old permissions BEFORE overwrite
    old_codes = set(membership.permissions.values_list("code", flat=True))
    
    old_role = membership.role
    # Predict permissions after role change based on defaults (read model)
    new_codes = set(
        NxPermission.objects.filter(default_for_roles__contains=[new_role]).values_list("code", flat=True)
    )
    granted = sorted(new_codes - old_codes)
    revoked = sorted(old_codes - new_codes)
    
    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.MEMBERSHIP_ROLE_CHANGED,
        aggregate_type="CompanyMembership",
        aggregate_id=str(membership.public_id),
        idempotency_key=_idempotency_hash("membership.role_changed", {
            "membership_public_id": str(membership.public_id),
            "new_role": new_role,
        }),
        data=MembershipRoleChangedData(
            membership_public_id=str(membership.public_id),
            user_public_id=str(membership.user.public_id),
            old_role=old_role,
            new_role=new_role,
            permissions_before=sorted(old_codes),
            permissions_after=sorted(new_codes),
            permissions_granted=granted,
            permissions_revoked=revoked,
            policy="role_change_resets_permissions_to_defaults",
        ).to_dict(),
    )
    
    _process_projections(actor.company)
    membership = CompanyMembership.objects.get(public_id=membership.public_id)
    return CommandResult.ok(membership, event=event)


@transaction.atomic
def deactivate_membership(
    actor,  # ActorContext
    membership_id: int,
) -> CommandResult:
    """
    Deactivate a membership (soft delete).
    
    Args:
        actor: The actor context
        membership_id: ID of membership
    
    Returns:
        CommandResult
    """
    from accounts.authz import require
    require(actor, "company.manage_users")
    
    try:
        membership = CompanyMembership.objects.select_related("user", "company").get(
            pk=membership_id, company=actor.company
        )
    except CompanyMembership.DoesNotExist:
        return CommandResult.fail("Membership not found.")
    
    # Cannot deactivate the owner
    if membership.role == CompanyMembership.Role.OWNER:
        return CommandResult.fail("Cannot deactivate the company owner.")
    
    # Cannot deactivate yourself
    if membership.user_id == actor.user.id:
        return CommandResult.fail("Cannot deactivate your own membership.")
    
    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.MEMBERSHIP_DEACTIVATED,
        aggregate_type="CompanyMembership",
        aggregate_id=str(membership.public_id),
        idempotency_key=_idempotency_hash("membership.deactivated", {
            "membership_public_id": str(membership.public_id),
        }),
        data=MembershipDeactivatedData(
            membership_public_id=str(membership.public_id),
            user_public_id=str(membership.user.public_id),
            user_email=membership.user.email,
            company_public_id=str(actor.company.public_id),
        ).to_dict(),
    )
    
    _process_projections(actor.company)
    return CommandResult.ok({"deactivated": True}, event=event)


# =============================================================================
# Permission Management
# =============================================================================

@transaction.atomic
def grant_permission(
    actor,  # ActorContext
    membership_id: int,
    permission_code,
) -> CommandResult:
    """
    Grant a permission to a membership.
    
    Args:
        actor: The actor context
        membership_id: ID of membership
        permission_code: Permission code to grant
    
    Returns:
        CommandResult
    """
    from accounts.authz import require
    require(actor, "company.manage_permissions")
    
    try:
        membership = CompanyMembership.objects.select_related("user").get(
            pk=membership_id, company=actor.company
        )
    except CompanyMembership.DoesNotExist:
        return CommandResult.fail("Membership not found.")
    
    if isinstance(permission_code, (list, tuple, set)):
        permission_codes = list(permission_code)
    else:
        permission_codes = [permission_code]

    permission_codes = [code for code in permission_codes if code]
    if not permission_codes:
        return CommandResult.fail("Permission code is required.")

    permissions = list(NxPermission.objects.filter(code__in=permission_codes))
    found_codes = {perm.code for perm in permissions}
    missing = set(permission_codes) - found_codes
    if missing:
        return CommandResult.fail(f"Permission(s) not found: {sorted(missing)}")
    
    # Check if already granted
    if membership.permissions.filter(code__in=permission_codes).exists():
        return CommandResult.fail("Permission already granted.")

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.PERMISSION_GRANTED,
        aggregate_type="CompanyMembership",
        aggregate_id=str(membership.public_id),
        idempotency_key=_idempotency_hash("permission.granted", {
            "membership_public_id": str(membership.public_id),
            "permission_codes": sorted(permission_codes),
        }),
        data=PermissionGrantedData(
            membership_public_id=str(membership.public_id),
            user_public_id=str(membership.user.public_id),
            user_email=membership.user.email,
            permission_codes=permission_codes,
            granted_by_public_id=str(actor.user.public_id),
            granted_by_email=actor.user.email,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok({"granted": permission_codes}, event=event)


@transaction.atomic
def revoke_permission(
    actor,  # ActorContext
    membership_id: int,
    permission_code,
) -> CommandResult:
    """
    Revoke a permission from a membership.
    
    Args:
        actor: The actor context
        membership_id: ID of membership
        permission_code: Permission code to revoke
    
    Returns:
        CommandResult
    """
    from accounts.authz import require
    require(actor, "company.manage_permissions")
    
    try:
        membership = CompanyMembership.objects.select_related("user").get(
            pk=membership_id, company=actor.company
        )
    except CompanyMembership.DoesNotExist:
        return CommandResult.fail("Membership not found.")
    
    if isinstance(permission_code, (list, tuple, set)):
        permission_codes = list(permission_code)
    else:
        permission_codes = [permission_code]

    permission_codes = [code for code in permission_codes if code]
    if not permission_codes:
        return CommandResult.fail("Permission code is required.")

    if not membership.permissions.filter(code__in=permission_codes).exists():
        return CommandResult.fail("Permission not granted to this membership.")

    # Emit event
    event = emit_event(
        actor=actor,
        event_type=EventTypes.PERMISSION_REVOKED,
        aggregate_type="CompanyMembership",
        aggregate_id=str(membership.public_id),
        idempotency_key=_idempotency_hash("permission.revoked", {
            "membership_public_id": str(membership.public_id),
            "permission_codes": sorted(permission_codes),
        }),
        data=PermissionRevokedData(
            membership_public_id=str(membership.public_id),
            user_public_id=str(membership.user.public_id),
            user_email=membership.user.email,
            permission_codes=permission_codes,
            revoked_by_public_id=str(actor.user.public_id),
            revoked_by_email=actor.user.email,
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok({"revoked": permission_codes}, event=event)


@transaction.atomic
def bulk_set_permissions(
    actor,  # ActorContext
    membership_id: int,
    permission_codes: list,
) -> CommandResult:
    """
    Set exact permissions for a membership (replaces existing).
    
    Args:
        actor: The actor context
        membership_id: ID of membership
        permission_codes: List of permission codes to set
    
    Returns:
        CommandResult
    """
    from accounts.authz import require
    require(actor, "company.manage_permissions")
    
    try:
        membership = CompanyMembership.objects.select_related("user").get(
            pk=membership_id, company=actor.company
        )
    except CompanyMembership.DoesNotExist:
        return CommandResult.fail("Membership not found.")
    
    # Validate all permission codes
    permissions = NxPermission.objects.filter(code__in=permission_codes)
    found_codes = set(permissions.values_list("code", flat=True))
    missing = set(permission_codes) - found_codes
    if missing:
        return CommandResult.fail(f"Unknown permissions: {missing}")
    
    # Get current permissions for comparison
    old_codes = set(membership.permissions.values_list("code", flat=True))
    new_codes = set(permission_codes)
    
    # Calculate changes for event
    granted = new_codes - old_codes
    revoked = old_codes - new_codes
    
    # Emit event
    payload = ",".join(sorted(permission_codes)).encode()
    digest = hashlib.sha256(payload).hexdigest()[:12]
    
    event = emit_event(
        actor=actor,
        event_type=EventTypes.MEMBERSHIP_PERMISSIONS_UPDATED,
        aggregate_type="CompanyMembership",
        aggregate_id=str(membership.public_id),
        idempotency_key=_idempotency_hash("membership.permissions_updated", {
            "membership_public_id": str(membership.public_id),
            "digest": digest,
        }),
        data=MembershipPermissionsUpdatedData(
            membership_public_id=str(membership.public_id),
            user_public_id=str(membership.user.public_id),
            user_email=membership.user.email,
            old_permissions=list(old_codes),
            new_permissions=list(new_codes),
            granted=list(granted),
            revoked=list(revoked),
        ).to_dict(),
    )

    _process_projections(actor.company)
    return CommandResult.ok({
        "permissions": list(new_codes),
        "granted": list(granted),
        "revoked": list(revoked),
    }, event=event)
    
@transaction.atomic
def update_company(
    actor: ActorContext,
    company_id: int,
    **updates,
) -> CommandResult:
    """
    Update company basic information.
    
    Allowed fields: name, name_ar, slug, is_active
    """
    # 1. Authorization
    require(actor, "company.update")
    
    # 2. Load company
    try:
        company = Company.objects.get(id=company_id)
    except Company.DoesNotExist:
        return CommandResult.fail("Company not found.")
    
    # 3. Validate actor belongs to this company
    if actor.company_id != company.id:
        return CommandResult.fail("Cannot update another company.")
    
    # 4. Build changes dict
    allowed_fields = {"name", "name_ar", "slug", "is_active"}
    changes = {}
    
    for field, new_value in updates.items():
        if field not in allowed_fields:
            continue
        old_value = getattr(company, field)
        if old_value != new_value:
            changes[field] = {"old": old_value, "new": new_value}
    
    if not changes:
        return CommandResult.success({"company": company, "message": "No changes"})
    
    # 5. Emit event
    emit_event(
        company=company,
        event_type=EventTypes.COMPANY_UPDATED,
        aggregate_type="Company",
        aggregate_id=str(company.public_id),
        data={
            "company_public_id": str(company.public_id),
            "changes": changes,
        },
        caused_by_user=actor.user,
        idempotency_key=_idempotency_hash("company.update", company.public_id, changes),
    )
    
    # 6. Process projections (projection will apply the changes)
    _process_projections(company)
    
    
    # 7. Reload and return
    company.refresh_from_db()
    return CommandResult.success({"company": company})


@transaction.atomic
def update_company_settings(
    actor: ActorContext,
    **settings,
) -> CommandResult:
    """
    Update company configuration settings.
    
    Allowed settings: default_currency, fiscal_year_start_month
    """
    # 1. Authorization
    require(actor, "company.settings.update")
    
    company = actor.company
    
    # 2. Build changes
    allowed_settings = {"default_currency", "fiscal_year_start_month"}
    changes = {}
    
    for setting, new_value in settings.items():
        if setting not in allowed_settings:
            continue
        old_value = getattr(company, setting)
        if old_value != new_value:
            changes[setting] = {"old": old_value, "new": new_value}
    
    if not changes:
        return CommandResult.success({"company": company, "message": "No changes"})
    
    # 3. Validate settings
    if "default_currency" in changes:
        new_currency = changes["default_currency"]["new"]
        if len(new_currency) != 3:
            return CommandResult.fail("Currency must be 3-letter ISO code.")
    
    if "fiscal_year_start_month" in changes:
        new_month = changes["fiscal_year_start_month"]["new"]
        if not (1 <= new_month <= 12):
            return CommandResult.fail("Fiscal year start month must be 1-12.")
    
    # 4. Emit event
    emit_event(
        company=company,
        event_type=EventTypes.COMPANY_SETTINGS_CHANGED,
        aggregate_type="Company",
        aggregate_id=str(company.public_id),
        data={
            "company_public_id": str(company.public_id),
            "changes": changes,
        },
        caused_by_user=actor.user,
        idempotency_key=_idempotency_hash("company.settings", company.public_id, changes),
    )
    
    # 5. Process projections
    _process_projections(company)
    
    # 6. Return
    company.refresh_from_db()
    return CommandResult.success({"company": company})
