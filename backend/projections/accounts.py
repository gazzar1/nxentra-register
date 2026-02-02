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
        data = event.data
        
        if event.event_type == EventTypes.COMPANY_CREATED:
            Company.objects.update_or_create(
                public_id=data["company_public_id"],
                defaults={
                    "name": data["name"],
                    "name_ar": data.get("name_ar", ""),
                    "slug": data.get("slug", ""),
                    "default_currency": data.get("default_currency", "USD"),
                    "fiscal_year_start_month": data.get("fiscal_year_start_month", 1),
                    "is_active": data.get("is_active", True),
                },
            )
            return

        # ⬇️ ADD THIS HANDLER ⬇️
        if event.event_type == EventTypes.COMPANY_UPDATED:
            company = Company.objects.filter(
                public_id=data["company_public_id"]
            ).first()
            if not company:
                logger.warning("Company not found for update: %s", data["company_public_id"])
                return
            
            for field, change in data.get("changes", {}).items():
                if hasattr(company, field):
                    setattr(company, field, change.get("new"))
            company.save()
            return

        if event.event_type == EventTypes.COMPANY_SETTINGS_CHANGED:
            company = Company.objects.filter(
                public_id=data["company_public_id"]
            ).first()
            if not company:
                logger.warning("Company not found for settings change: %s", data["company_public_id"])
                return

            for setting, change in data.get("changes", {}).items():
                if hasattr(company, setting):
                    setattr(company, setting, change.get("new"))
            company.save()
            return

        if event.event_type == EventTypes.COMPANY_LOGO_UPLOADED:
            company = Company.objects.filter(
                public_id=data["company_public_id"]
            ).first()
            if not company:
                logger.warning("Company not found for logo upload: %s", data["company_public_id"])
                return

            # Update the logo field with the new path
            company.logo = data.get("logo_path")
            company.save(update_fields=["logo"])
            return

        if event.event_type == EventTypes.COMPANY_LOGO_DELETED:
            company = Company.objects.filter(
                public_id=data["company_public_id"]
            ).first()
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
        data = event.data

        if event.event_type == EventTypes.USER_CREATED:
            User.objects.update_or_create(
                public_id=data["user_public_id"],
                defaults={
                    "email": data["email"],
                    "name": data.get("name", ""),
                    "is_active": True,
                },
            )
            return

        if event.event_type == EventTypes.USER_UPDATED:
            user = User.objects.filter(public_id=data["user_public_id"]).first()
            if not user:
                logger.warning("User not found for update: %s", data["user_public_id"])
                return
            for field, change in data.get("changes", {}).items():
                setattr(user, field, change.get("new"))
            user.save()
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
            company = Company.objects.filter(
                public_id=data.get("to_company_public_id")
            ).first()
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
        data = event.data

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
            membership = CompanyMembership.objects.filter(
                public_id=data["membership_public_id"]
            ).first()
            if not membership:
                logger.warning("Membership not found for reactivation.")
                return
            membership.is_active = True
            membership.role = data.get("role", membership.role)
            membership.save(update_fields=["is_active", "role"])
            grant_role_defaults(membership=membership, granted_by=membership.user, overwrite=False)
            return

        if event.event_type == EventTypes.MEMBERSHIP_ROLE_CHANGED:
            membership = CompanyMembership.objects.filter(
                public_id=data["membership_public_id"]
            ).first()
            if not membership:
                logger.warning("Membership not found for role change.")
                return
            membership.role = data.get("new_role", membership.role)
            membership.save(update_fields=["role"])
            grant_role_defaults(membership=membership, granted_by=membership.user, overwrite=True)
            return

        if event.event_type == EventTypes.MEMBERSHIP_DEACTIVATED:
            membership = CompanyMembership.objects.filter(
                public_id=data["membership_public_id"]
            ).first()
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
            membership = CompanyMembership.objects.filter(
                public_id=data["membership_public_id"]
            ).first()
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
            membership = CompanyMembership.objects.filter(
                public_id=data["membership_public_id"]
            ).first()
            if not membership:
                return
            CompanyMembershipPermission.objects.filter(
                membership=membership,
                company=membership.company,
                permission__code__in=data.get("permission_codes", []),
            ).delete()
            return

        if event.event_type == EventTypes.MEMBERSHIP_PERMISSIONS_UPDATED:
            membership = CompanyMembership.objects.filter(
                public_id=data["membership_public_id"]
            ).first()
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
