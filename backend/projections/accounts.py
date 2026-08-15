# projections/accounts.py
"""
Accounts/Auth projections (read models).
"""

import logging

from django.contrib.auth import get_user_model

from accounts.models import (
    Company,
    CompanyMembership,
    CompanyMembershipPermission,
    NxPermission,
)
from accounts.permissions import grant_role_defaults
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection, projection_registry

logger = logging.getLogger(__name__)
User = get_user_model()


# A4 (control-plane integrity): a read-model projection is the APPLY half of an
# admission-gated command and must own EXACTLY the fields its producing command
# emits — never a generic setattr over event-supplied names (which would let a
# forged/replayed event write pilot_profile, is_superuser, password, ...). These
# frozen sets are pinned EQUAL to the command whitelists (accounts.commands
# update_company / update_company_settings / update_user) by
# tests/test_architecture_rules.py::test_company_user_projection_field_ownership.
# pilot_profile is DELIBERATELY absent from every set: it is activation-owned
# (activate_pilot_profile is the sole production writer). Privilege/identity fields
# (is_superuser, is_staff, password, active_company, user.is_active) are absent too.
COMPANY_UPDATED_APPLY_FIELDS = frozenset({"name", "name_ar", "slug", "is_active"})
COMPANY_SETTINGS_CHANGED_APPLY_FIELDS = frozenset(
    {
        "name",
        "name_ar",
        "default_currency",
        "fiscal_year_start_month",
        "thousand_separator",
        "decimal_separator",
        "decimal_places",
        "date_format",
        "enable_arabic_fields",
    }
)
USER_UPDATED_APPLY_FIELDS = frozenset({"name", "name_ar", "email"})


def _apply_owned_changes(instance, changes, allowed, *, event_id, label, extra_update_fields=()):
    """Apply ONLY the owned fields named in ``changes`` and persist with a NARROW
    ``update_fields`` — so a concurrent write to an unrelated column (e.g.
    ``activate_pilot_profile`` writing ``pilot_profile``) is never clobbered by a
    broad ``save()``. An event naming any field outside ``allowed`` is a VISIBLE
    projection failure: the raise rolls back the whole ``handle()`` and its
    ``ProjectionAppliedEvent`` marker (they share one transaction in
    ``process_pending``), so the event is NOT marked applied. Never a silent
    ``setattr`` over caller-supplied names."""
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(
            f"{label} event {event_id} names unsupported field(s) {sorted(unknown)}; "
            f"the projection applies only {sorted(allowed)} (pilot_profile and other "
            "non-owned fields are never applied from an event)."
        )
    update_fields = []
    for field, change in changes.items():
        setattr(instance, field, change.get("new"))
        update_fields.append(field)
    if update_fields:
        instance.save(update_fields=[*update_fields, *extra_update_fields])
    return len(update_fields)


class CompanyProjection(BaseProjection):
    @property
    def name(self) -> str:
        return "company_read_model"

    @property
    def consumes(self):
        return [
            EventTypes.COMPANY_CREATED,
            EventTypes.COMPANY_UPDATED,
            EventTypes.COMPANY_SETTINGS_CHANGED,
            EventTypes.COMPANY_LOGO_UPLOADED,
            EventTypes.COMPANY_LOGO_DELETED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        data = event.get_data()

        if event.event_type == EventTypes.COMPANY_CREATED:
            Company.objects.update_or_create(
                public_id=data["company_public_id"],
                defaults={
                    "name": data["name"],
                    "name_ar": data.get("name_ar", ""),
                    "slug": data.get("slug", ""),
                    "default_currency": data.get("default_currency", "USD"),
                    "functional_currency": data.get("functional_currency", data.get("default_currency", "USD")),
                    "fiscal_year_start_month": data.get("fiscal_year_start_month", 1),
                    "is_active": data.get("is_active", True),
                },
            )
            return

        if event.event_type == EventTypes.COMPANY_UPDATED:
            company = Company.objects.filter(public_id=data["company_public_id"]).first()
            if not company:
                logger.warning("Company not found for update: %s", data["company_public_id"])
                return

            _apply_owned_changes(
                company,
                data.get("changes", {}),
                COMPANY_UPDATED_APPLY_FIELDS,
                event_id=event.id,
                label="COMPANY_UPDATED",
                extra_update_fields=("updated_at",),
            )
            return

        if event.event_type == EventTypes.COMPANY_SETTINGS_CHANGED:
            company = Company.objects.filter(public_id=data["company_public_id"]).first()
            if not company:
                logger.warning("Company not found for settings change: %s", data["company_public_id"])
                return

            _apply_owned_changes(
                company,
                data.get("changes", {}),
                COMPANY_SETTINGS_CHANGED_APPLY_FIELDS,
                event_id=event.id,
                label="COMPANY_SETTINGS_CHANGED",
                extra_update_fields=("updated_at",),
            )
            return

        if event.event_type == EventTypes.COMPANY_LOGO_UPLOADED:
            company = Company.objects.filter(public_id=data["company_public_id"]).first()
            if not company:
                logger.warning("Company not found for logo upload: %s", data["company_public_id"])
                return

            # Update the logo field with the new path
            company.logo = data.get("logo_path")
            company.save(update_fields=["logo"])
            return

        if event.event_type == EventTypes.COMPANY_LOGO_DELETED:
            company = Company.objects.filter(public_id=data["company_public_id"]).first()
            if not company:
                logger.warning("Company not found for logo delete: %s", data["company_public_id"])
                return

            # Clear the logo field
            company.logo = None
            company.save(update_fields=["logo"])
            return

        logger.warning("Unhandled event type for CompanyProjection: %s", event.event_type)


class UserProjection(BaseProjection):
    @property
    def name(self) -> str:
        return "user_read_model"

    @property
    def consumes(self):
        return [
            EventTypes.USER_CREATED,
            EventTypes.USER_UPDATED,
            EventTypes.USER_PASSWORD_CHANGED,
            EventTypes.USER_COMPANY_SWITCHED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        data = event.get_data()

        if event.event_type == EventTypes.USER_CREATED:
            User.objects.update_or_create(
                public_id=data["user_public_id"],
                defaults={
                    "email": data["email"],
                    "name": data.get("name", ""),
                    "phone": data.get("phone", ""),
                    "is_active": True,
                },
            )
            return

        if event.event_type == EventTypes.USER_UPDATED:
            user = User.objects.filter(public_id=data["user_public_id"]).first()
            if not user:
                logger.warning("User not found for update: %s", data["user_public_id"])
                return
            _apply_owned_changes(
                user,
                data.get("changes", {}),
                USER_UPDATED_APPLY_FIELDS,
                event_id=event.id,
                label="USER_UPDATED",
            )
            return

        if event.event_type == EventTypes.USER_PASSWORD_CHANGED:
            user = User.objects.filter(public_id=data["user_public_id"]).first()
            if not user:
                logger.warning("User not found for password change: %s", data["user_public_id"])
            return

        if event.event_type == EventTypes.USER_COMPANY_SWITCHED:
            user = User.objects.filter(public_id=data["user_public_id"]).first()
            if not user:
                logger.warning("User not found for company switch: %s", data["user_public_id"])
                return
            company = Company.objects.filter(public_id=data.get("to_company_public_id")).first()
            user.active_company = company
            user.save(update_fields=["active_company"])
            return

        logger.warning("Unhandled event type for UserProjection: %s", event.event_type)


class MembershipProjection(BaseProjection):
    @property
    def name(self) -> str:
        return "membership_read_model"

    @property
    def consumes(self):
        return [
            EventTypes.MEMBERSHIP_CREATED,
            EventTypes.MEMBERSHIP_REACTIVATED,
            EventTypes.MEMBERSHIP_ROLE_CHANGED,
            EventTypes.MEMBERSHIP_DEACTIVATED,
            EventTypes.MEMBERSHIP_PERMISSIONS_UPDATED,
            EventTypes.PERMISSION_GRANTED,
            EventTypes.PERMISSION_REVOKED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        data = event.get_data()

        if event.event_type == EventTypes.MEMBERSHIP_CREATED:
            company = Company.objects.filter(public_id=data["company_public_id"]).first()
            user = User.objects.filter(public_id=data["user_public_id"]).first()
            if not company or not user:
                logger.warning("Missing company/user for membership create.")
                return
            membership, _ = CompanyMembership.objects.update_or_create(
                public_id=data["membership_public_id"],
                defaults={
                    "company": company,
                    "user": user,
                    "role": data["role"],
                    "is_active": data.get("is_active", True),
                },
            )
            grant_role_defaults(membership=membership, granted_by=user, overwrite=False)
            return

        if event.event_type == EventTypes.MEMBERSHIP_REACTIVATED:
            membership = CompanyMembership.objects.filter(public_id=data["membership_public_id"]).first()
            if not membership:
                logger.warning("Membership not found for reactivation.")
                return
            membership.is_active = True
            membership.role = data.get("role", membership.role)
            membership.save(update_fields=["is_active", "role"])
            grant_role_defaults(membership=membership, granted_by=membership.user, overwrite=False)
            return

        if event.event_type == EventTypes.MEMBERSHIP_ROLE_CHANGED:
            membership = CompanyMembership.objects.filter(public_id=data["membership_public_id"]).first()
            if not membership:
                logger.warning("Membership not found for role change.")
                return
            membership.role = data.get("new_role", membership.role)
            membership.save(update_fields=["role"])
            grant_role_defaults(membership=membership, granted_by=membership.user, overwrite=True)
            return

        if event.event_type == EventTypes.MEMBERSHIP_DEACTIVATED:
            membership = CompanyMembership.objects.filter(public_id=data["membership_public_id"]).first()
            if not membership:
                logger.warning("Membership not found for deactivation.")
                return
            membership.is_active = False
            membership.save(update_fields=["is_active"])
            if membership.user.active_company_id == membership.company_id:
                membership.user.active_company = None
                membership.user.save(update_fields=["active_company"])
            return

        if event.event_type == EventTypes.PERMISSION_GRANTED:
            membership = CompanyMembership.objects.filter(public_id=data["membership_public_id"]).first()
            if not membership:
                return
            granted_by = None
            granted_by_public_id = data.get("granted_by_public_id")
            if granted_by_public_id:
                granted_by = User.objects.filter(public_id=granted_by_public_id).first()
            for code in data.get("permission_codes", []):
                permission = NxPermission.objects.filter(code=code).first()
                if not permission:
                    continue
                CompanyMembershipPermission.objects.get_or_create(
                    membership=membership,
                    company=membership.company,
                    permission=permission,
                    defaults={"granted_by": granted_by},
                )
            return

        if event.event_type == EventTypes.PERMISSION_REVOKED:
            membership = CompanyMembership.objects.filter(public_id=data["membership_public_id"]).first()
            if not membership:
                return
            CompanyMembershipPermission.objects.filter(
                membership=membership,
                company=membership.company,
                permission__code__in=data.get("permission_codes", []),
            ).delete()
            return

        if event.event_type == EventTypes.MEMBERSHIP_PERMISSIONS_UPDATED:
            membership = CompanyMembership.objects.filter(public_id=data["membership_public_id"]).first()
            if not membership:
                return
            CompanyMembershipPermission.objects.filter(
                membership=membership,
                company=membership.company,
            ).delete()
            perms = NxPermission.objects.filter(code__in=data.get("new_permissions", []))
            for perm in perms:
                CompanyMembershipPermission.objects.create(
                    membership=membership,
                    company=membership.company,
                    permission=perm,
                    granted_by=None,
                )
            return

        logger.warning("Unhandled event type for MembershipProjection: %s", event.event_type)


projection_registry.register(CompanyProjection())
projection_registry.register(UserProjection())
projection_registry.register(MembershipProjection())
