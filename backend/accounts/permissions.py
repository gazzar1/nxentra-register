# accounts/permissions.py
from __future__ import annotations

from typing import Iterable, Optional
from django.db import transaction
from django.contrib.auth import get_user_model
from django.conf import settings

from accounts.models import NxPermission, CompanyMembership, CompanyMembershipPermission
from accounts.permission_defaults import ROLE_DEFAULTS, all_permission_codes
from projections.write_barrier import write_context_allowed

User = get_user_model()

def _perm_defaults(code: str) -> dict:
    return {
        "name": code,
        "name_ar": "",
        "module": code.split(".")[0],
        "description": "",
        "default_for_roles": [],
    }

def _require_write_context() -> None:
    if getattr(settings, "TESTING", False):
        return
    if not write_context_allowed({"projection", "command", "bootstrap", "migration", "admin_emergency"}):
        raise RuntimeError(
            "Permission defaults can only be written within an allowed write context."
        )



@transaction.atomic
def grant_role_defaults(
    membership: CompanyMembership,
    granted_by: Optional[User] = None,
    overwrite: bool = False,
) -> int:
    """
    Grant default permissions for the membership.role.

    - Idempotent by default: only grants missing codes.
    - If overwrite=True: first removes existing permissions then grants defaults.
    Returns number of permissions newly granted.
    """
    role = membership.role
    default_codes = ROLE_DEFAULTS.get(role, set())

    _require_write_context()

    if overwrite:
        CompanyMembershipPermission.objects.filter(
            membership=membership,
            company=membership.company,
        ).delete()

    # Ensure NxPermission rows exist for these codes
    existing = set(NxPermission.objects.filter(code__in=default_codes).values_list("code", flat=True))
    missing = [c for c in default_codes if c not in existing]
    if missing:
        NxPermission.objects.bulk_create(
            [
                NxPermission(
                    code=c, 
                    name=c,  # placeholder
                    name_ar="",
                    module=c.split(".")[0],
                    description="",
                    default_for_roles=[],
                ) 
                for c in missing
            ],
            ignore_conflicts=True,
        )

    perms = list(NxPermission.objects.filter(code__in=default_codes))

    # Find which are already granted
    already = set(
        CompanyMembershipPermission.objects.filter(
            membership=membership,
            company=membership.company,
            permission__in=perms,
        ).values_list("permission__code", flat=True)
    )

    to_grant = [p for p in perms if p.code not in already]
    if not to_grant:
        return 0

    CompanyMembershipPermission.objects.bulk_create(
        [
            CompanyMembershipPermission(
                membership=membership,
                company=membership.company,
                permission=p,
                granted_by=granted_by if (granted_by and granted_by.is_authenticated) else None,
            )
            for p in to_grant
        ],
        ignore_conflicts=True,
    )
    return len(to_grant)


@transaction.atomic
def grant_defaults_to_all_memberships(
    granted_by: Optional[User] = None,
    only_if_empty: bool = True,
) -> dict[str, int]:
    """
    Grant defaults across all memberships.
    - only_if_empty=True: only touches memberships with zero explicit permissions.
    """
    qs = CompanyMembership.objects.all().select_related("user")

    _require_write_context()

    updated = 0
    total_granted = 0

    for m in qs:
        if only_if_empty and CompanyMembershipPermission.objects.filter(membership=m).exists():
            continue
        g = grant_role_defaults(membership=m, granted_by=granted_by, overwrite=False)
        if g > 0:
            updated += 1
            total_granted += g

    return {"memberships_updated": updated, "permissions_granted": total_granted}
