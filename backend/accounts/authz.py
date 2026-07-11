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

from django.core.exceptions import PermissionDenied
from rest_framework.exceptions import NotAuthenticated

from accounts.models import Company, CompanyMembership

# A85 chunk 6 (2026-05-26): permissions that even the OWNER role must hold
# EXPLICITLY — superuser bypass still wins (system-level access), but a
# normal OWNER does not auto-acquire these via role. These are powerful
# actions that need a paper trail per docs/finance_event_first_policy.md
# §8 and ENGINEERING_PROTOCOL.md §1.5 ("auditability beats convenience").
#
# Adding a code here is a security-impacting change: any view that already
# checks `actor.has(<code>)` will now reject OWNERs who don't have the
# explicit grant. Be deliberate.
SENSITIVE_PERMISSIONS: frozenset[str] = frozenset(
    {
        # A85 chunk 3c: explicit grant required to override the date-derived
        # fiscal period on JE creation / settlement import / auto-match.
        # Tested at: tests/test_a85_manual_je_override.py,
        # tests/test_a85_settlement_period_override.py,
        # tests/test_a85_auto_match_preview.py.
        "accounting.je.override_period",
        # A160: restore OVERWRITES the entire company's books from an
        # uploaded ZIP. Even an OWNER needs the explicit grant (it is in
        # OWNER ROLE_DEFAULTS, so grant_role_defaults materializes the row
        # for new memberships; existing ones need the deploy backfill).
        # Tested at: tests/test_backups_authorization.py.
        "backups.restore",
    }
)


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
    perms: frozenset[str]  # Explicit permission codes

    def has(self, code: str) -> bool:
        """
        Check if actor has a specific permission.

        Order of checks:
        1. Superuser: implicit allow (all permissions)
        2. OWNER: implicit allow — EXCEPT for SENSITIVE_PERMISSIONS, which
           always require an explicit grant even from an OWNER (A85 chunk 6,
           2026-05-26). See SENSITIVE_PERMISSIONS docstring.
        3. everyone else: only codes in perms or in the DB.
        """
        if not self.membership.is_active:
            return False

        # Superusers have all permissions
        if getattr(self.user, "is_superuser", False):
            return True

        if self.membership.role == CompanyMembership.Role.OWNER and code not in SENSITIVE_PERMISSIONS:
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


def system_actor_for_company(company) -> ActorContext:
    """
    Create an ActorContext for system-level operations (webhooks, integrations).

    Uses the company's OWNER membership so all existing permission checks
    pass without modification. No real user session is involved.

    Args:
        company: The Company instance

    Returns:
        ActorContext with OWNER permissions

    Raises:
        ValueError: If no active OWNER membership exists
    """
    from accounts.rls import rls_bypass

    with rls_bypass():
        membership = (
            CompanyMembership.objects.filter(
                company=company,
                role=CompanyMembership.Role.OWNER,
                is_active=True,
            )
            .select_related("user")
            .first()
        )

        if not membership:
            raise ValueError(f"No active OWNER found for company {company.slug}")

        perms = frozenset(membership.permissions.values_list("code", flat=True))

    return ActorContext(
        user=membership.user,
        company=company,
        membership=membership,
        perms=perms,
    )


def resolve_actor(request) -> ActorContext:
    """
    Extract ActorContext from the current request.

    This is called at the start of every view that needs authorization.
    It loads the user's membership and permissions FRESH from the database,
    ensuring that permission changes take effect immediately.

    Uses rls_bypass internally because authorization must work independently
    of the tenant RLS context (the user needs to be resolved before we know
    which tenant they belong to).

    Args:
        request: Django/DRF request object

    Returns:
        ActorContext with user, company, membership, permissions

    Raises:
        NotAuthenticated: If user is not authenticated
        PermissionDenied: If user has no active company or membership
    """
    from accounts.rls import rls_bypass

    user = getattr(request, "user", None)

    if not user or not user.is_authenticated:
        raise NotAuthenticated("Authentication required.")

    with rls_bypass():
        company = getattr(user, "active_company", None)

        if not company:
            raise PermissionDenied("No active company selected. Please select a company first.")

        # Fresh membership lookup EVERY request (not cached)
        try:
            membership = (
                CompanyMembership.objects.select_related("company")
                .prefetch_related("permissions")
                .get(
                    user=user,
                    company=company,
                    is_active=True,
                )
            )
        except CompanyMembership.DoesNotExist:
            raise PermissionDenied("You are not an active member of the selected company.")

        # Fresh permission lookup EVERY request
        perms = frozenset(membership.permissions.values_list("code", flat=True))

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
