# accounts/authz.py
"""
Authorization utilities for Nxentra.

Provides:
- ActorContext: Immutable context for the current request
- resolve_actor: Extract actor context from request
- require: Check permissions and raise if not granted

CRITICAL: Permissions are checked:
1. First by role (OWNER: implicit allow)
2. ADMIN/USER/VIEWER: explicit permissions only (defaults + manual)

This ensures role-based defaults work correctly.
"""

from dataclasses import dataclass
from typing import FrozenSet
from django.core.exceptions import PermissionDenied
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated

from accounts.models import CompanyMembership, Company


@dataclass(frozen=True)
class ActorContext:
    """
    Immutable context for the current actor (user + company).
    
    This is passed to commands and policies to provide context
    about who is performing an action and in which company.
    
    Attributes:
        user: The authenticated user
        company: The active company (tenant)
        membership: The user's membership in the company
        perms: Set of explicit permission codes the user has
    """
    user: object  # User model
    company: Company
    membership: CompanyMembership
    perms: FrozenSet[str]  # Explicit permission codes

    def has(self, code: str) -> bool:
        """
        Check if actor has a specific permission.
        
        Order of checks:
        1. OWNER: implicit allow
        2. everyone else: only code in permse
        """
        if not self.membership.is_active:
            return False

        if self.membership.role == CompanyMembership.Role.OWNER:
            return True
        if code in self.perms:
            return True
        # Fallback to a fresh lookup in case permissions changed after context creation.
        return self.membership.permissions.filter(code=code).exists()

    # Alias for compatibility with commands that use has_permission
    def has_permission(self, code: str) -> bool:
        """Alias for has() - for compatibility."""
        return self.has(code)

    @property
    def is_authenticated(self) -> bool:
        """Mirror Django's user.is_authenticated for compatibility."""
        return bool(getattr(self.user, "is_authenticated", False))

    @property
    def is_owner(self) -> bool:
        """Check if user is the company owner."""
        return self.membership.role == CompanyMembership.Role.OWNER

    @property
    def is_admin(self) -> bool:
        """Check if user is an admin (owner or admin role)."""
        return self.membership.role in [
            CompanyMembership.Role.OWNER,
            CompanyMembership.Role.ADMIN,
        ]

    @property
    def role(self) -> str:
        """Get the user's role in this company."""
        return self.membership.role


def resolve_actor(request) -> ActorContext:
    """
    Extract ActorContext from the current request.
    
    This is called at the start of every view that needs authorization.
    It loads the user's membership and permissions FRESH from the database,
    ensuring that permission changes take effect immediately.
    
    Args:
        request: Django/DRF request object
    
    Returns:
        ActorContext with user, company, membership, permissions
    
    Raises:
        NotAuthenticated: If user is not authenticated
        PermissionDenied: If user has no active company or membership
    """
    user = getattr(request, "user", None)
    
    if not user or not user.is_authenticated:
        raise NotAuthenticated("Authentication required.")
    
    company = getattr(user, "active_company", None)
    
    if not company:
        raise PermissionDenied("No active company selected. Please select a company first.")
    
    # Fresh membership lookup EVERY request (not cached)
    try:
        membership = CompanyMembership.objects.select_related(
            "company"
        ).prefetch_related(
            "permissions"
        ).get(
            user=user,
            company=company,
            is_active=True,
        )
    except CompanyMembership.DoesNotExist:
        raise PermissionDenied("You are not an active member of the selected company.")
    
    # Fresh permission lookup EVERY request
    perms = frozenset(
        membership.permissions.values_list("code", flat=True)
    )
    
    return ActorContext(
        user=user,
        company=company,
        membership=membership,
        perms=perms,
    )


def require(actor: ActorContext, code: str) -> None:
    """
    Require that the actor has a specific permission.
    
    Raises PermissionDenied if the permission is not granted.
    
    Args:
        actor: The ActorContext
        code: Permission code to check (e.g., "journal.post")
    
    Raises:
        PermissionDenied: If permission is not granted
    
    Example:
        require(actor, "journal.post")
        # If we get here, permission is granted
    """
    if not actor.has(code):
        raise PermissionDenied(f"Permission denied: {code}")


def require_any(actor: ActorContext, *codes: str) -> None:
    """
    Require that the actor has AT LEAST ONE of the specified permissions.
    
    Args:
        actor: The ActorContext
        *codes: One or more permission codes to check
    
    Raises:
        PermissionDenied: If none of the permissions are granted
    
    Example:
        require_any(actor, "journal.view", "journal.edit_draft")
    """
    for code in codes:
        if actor.has(code):
            return
    
    raise PermissionDenied(f"Permission denied: requires one of {', '.join(codes)}")


def check_permission(actor: ActorContext, code: str) -> bool:
    """
    Check if actor has a permission without raising.
    
    Args:
        actor: The ActorContext
        code: Permission code to check
    
    Returns:
        True if permission is granted, False otherwise
    """
    return actor.has(code)


def resolve_actor_optional(request):
    """
    Try to resolve ActorContext, return None if not possible.
    
    Useful for views that work with or without authentication.
    """
    try:
        return resolve_actor(request)
    except (NotAuthenticated, PermissionDenied):
        return None
