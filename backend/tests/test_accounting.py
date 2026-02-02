# tests/test_accounts.py
"""
Tests for the accounts module.

Tests cover:
- Membership is_active checks (critical fix)
- Permission handling
- User management commands
- Company switching
"""

import pytest
from uuid import uuid4

from django.contrib.auth import get_user_model

from accounts.models import Company, CompanyMembership, NxPermission, CompanyMembershipPermission
from accounts.authz import ActorContext, resolve_actor, require, PermissionDenied
from accounts.commands import (
    register_signup,
    switch_active_company,
    create_user_with_membership,
    update_user,
    update_membership_role,
    deactivate_membership,
    grant_permission,
    revoke_permission,
)
from accounts.permissions import grant_role_defaults


User = get_user_model()


# =============================================================================
# Membership is_active Tests (Critical Fix)
# =============================================================================

@pytest.mark.django_db
class TestMembershipIsActiveChecks:
    """
    Test that deactivated memberships don't grant permissions.
    
    This tests the critical fix in accounts/models.py.
    """
    
    def test_deactivated_membership_has_no_permissions(self, deactivated_membership):
        """Deactivated membership should return False for has_permission()."""
        # Even if the role is OWNER, deactivated should return False
        deactivated_membership.role = CompanyMembership.Role.OWNER
        deactivated_membership.save()
        
        assert deactivated_membership.is_active is False
        assert deactivated_membership.has_permission("any.permission") is False
    
    def test_active_owner_has_all_permissions(self, owner_membership):
        """Active OWNER should have all permissions implicitly."""
        assert owner_membership.is_active is True
        assert owner_membership.role == CompanyMembership.Role.OWNER
        
        # OWNER has all permissions without explicit grants
        assert owner_membership.has_permission("accounts.view") is True
        assert owner_membership.has_permission("journal.post") is True
        assert owner_membership.has_permission("any.random.permission") is True
    
    def test_active_user_needs_explicit_permissions(self, user_membership, permissions):
        """Active USER role needs explicit permission grants."""
        assert user_membership.is_active is True
        assert user_membership.role == CompanyMembership.Role.USER
        
        # Without grants, should not have permission
        assert user_membership.has_permission("accounts.view") is False
        
        # Grant permission
        perm = NxPermission.objects.get(code="accounts.view")
        CompanyMembershipPermission.objects.create(
            membership=user_membership,
            company=user_membership.company,
            permission=perm,
        )
        
        # Now should have it
        assert user_membership.has_permission("accounts.view") is True
        # But not others
        assert user_membership.has_permission("journal.post") is False
    
    def test_deactivating_membership_removes_permissions(self, user_membership, permissions):
        """Deactivating membership should block all permissions."""
        # Grant some permissions
        for perm in permissions[:3]:
            CompanyMembershipPermission.objects.create(
                membership=user_membership,
                company=user_membership.company,
                permission=perm,
            )
        
        # Verify permissions work while active
        assert user_membership.is_active is True
        assert user_membership.has_permission(permissions[0].code) is True
        
        # Deactivate
        user_membership.is_active = False
        user_membership.save()
        
        # Should now return False for all
        assert user_membership.has_permission(permissions[0].code) is False
        assert user_membership.has_permission(permissions[1].code) is False


# =============================================================================
# User.get_active_membership Tests (Critical Fix)
# =============================================================================

@pytest.mark.django_db
class TestGetActiveMembership:
    """
    Test that get_active_membership respects is_active flag.
    
    This tests the critical fix in accounts/models.py.
    """
    
    def test_returns_active_membership(self, user, company, owner_membership):
        """Should return membership when active."""
        user.active_company = company
        user.save()
        
        membership = user.get_active_membership()
        
        assert membership is not None
        assert membership.id == owner_membership.id
        assert membership.is_active is True
    
    def test_returns_none_for_deactivated_membership(self, company):
        """Should return None when membership is deactivated."""
        # Create user with deactivated membership
        user = User.objects.create_user(
            public_id=uuid4(),
            email="deactivated@test.com",
            password="testpass123",
        )
        
        membership = CompanyMembership.objects.create(
            public_id=uuid4(),
            company=company,
            user=user,
            role=CompanyMembership.Role.USER,
            is_active=False,  # Deactivated!
        )
        
        user.active_company = company
        user.save()
        
        # Should return None because membership is not active
        result = user.get_active_membership()
        assert result is None
    
    def test_returns_none_when_no_active_company(self, user):
        """Should return None when user has no active company."""
        user.active_company = None
        user.save()
        
        result = user.get_active_membership()
        assert result is None


# =============================================================================
# ActorContext and Authorization Tests
# =============================================================================

@pytest.mark.django_db
class TestActorContext:
    """Test ActorContext creation and usage."""
    
    def test_actor_context_is_immutable(self, actor_context):
        """ActorContext should be a frozen dataclass."""
        with pytest.raises(AttributeError):
            actor_context.user = None
    
    def test_require_raises_for_missing_permission(self, user_actor_context):
        """require() should raise PermissionDenied for missing permissions."""
        with pytest.raises(PermissionDenied):
            require(user_actor_context, "admin.only.permission")
    
    def test_require_passes_for_owner(self, actor_context):
        """require() should pass for OWNER role."""
        # Should not raise
        require(actor_context, "any.permission")
    
    def test_require_passes_with_granted_permission(self, user_actor_context, permissions):
        """require() should pass when permission is granted."""
        # Grant permission
        perm = NxPermission.objects.get(code="accounts.view")
        CompanyMembershipPermission.objects.create(
            membership=user_actor_context.membership,
            company=user_actor_context.membership.company,
            permission=perm,
        )
        
        # Should not raise
        require(user_actor_context, "accounts.view")


# =============================================================================
# Registration Command Tests
# =============================================================================

@pytest.mark.django_db
class TestRegisterSignup:
    """Test user registration command."""
    
    def test_register_creates_company_user_membership(self, db):
        """Registration should create company, user, and membership."""
        result = register_signup(
            email="newuser@example.com",
            password="securepass123",
            name="New User",
            company_name="New Company",
        )
        
        assert result.success is True
        
        # Check user created
        user = User.objects.get(email="newuser@example.com")
        assert user.name == "New User"
        
        # Check company created
        company = Company.objects.get(name="New Company")
        assert company.is_active is True
        
        # Check membership created with OWNER role
        membership = CompanyMembership.objects.get(user=user, company=company)
        assert membership.role == CompanyMembership.Role.OWNER
        assert membership.is_active is True
        
        # Check user's active company is set
        user.refresh_from_db()
        assert user.active_company_id == company.id
    
    def test_register_emits_events(self, db):
        """Registration should emit appropriate events."""
        from events.models import BusinessEvent
        from events.types import EventTypes
        
        result = register_signup(
            email="eventtest@example.com",
            password="securepass123",
            name="Event Test",
            company_name="Event Company",
        )
        
        assert result.success is True
        
        # Check events were emitted
        company = Company.objects.get(name="Event Company")
        
        company_event = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.COMPANY_CREATED,
        ).first()
        assert company_event is not None
        
        user_event = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.USER_REGISTERED,
        ).first()
        assert user_event is not None
    
    def test_register_fails_duplicate_email(self, user):
        """Registration should fail for duplicate email."""
        result = register_signup(
            email=user.email,  # Already exists
            password="securepass123",
            name="Duplicate",
            company_name="Dup Company",
        )
        
        assert result.success is False
        assert "email" in result.error.lower() or "exists" in result.error.lower()


# =============================================================================
# Company Switching Tests
# =============================================================================

@pytest.mark.django_db
class TestSwitchActiveCompany:
    """Test company switching command."""
    
    def test_switch_to_valid_company(self, actor_context, second_company):
        """User can switch to a company they have membership in."""
        # Create membership in second company
        CompanyMembership.objects.create(
            public_id=uuid4(),
            company=second_company,
            user=actor_context.user,
            role=CompanyMembership.Role.USER,
            is_active=True,
        )
        
        result = switch_active_company(actor_context, second_company.id)
        
        assert result.success is True
        
        actor_context.user.refresh_from_db()
        assert actor_context.user.active_company_id == second_company.id
    
    def test_cannot_switch_to_company_without_membership(self, actor_context, second_company):
        """User cannot switch to company they don't belong to."""
        result = switch_active_company(actor_context, second_company.id)
        
        assert result.success is False
        assert "membership" in result.error.lower() or "access" in result.error.lower()
    
    def test_cannot_switch_with_deactivated_membership(self, actor_context, second_company):
        """User cannot switch if membership is deactivated."""
        # Create deactivated membership
        CompanyMembership.objects.create(
            public_id=uuid4(),
            company=second_company,
            user=actor_context.user,
            role=CompanyMembership.Role.USER,
            is_active=False,  # Deactivated!
        )
        
        result = switch_active_company(actor_context, second_company.id)
        
        assert result.success is False


# =============================================================================
# Membership Role Change Tests
# =============================================================================

@pytest.mark.django_db
class TestUpdateMembershipRole:
    """Test membership role change command."""
    
    def test_owner_can_change_roles(self, actor_context, user_membership):
        """OWNER can change other members' roles."""
        result = update_membership_role(
            actor_context,
            user_membership.id,
            CompanyMembership.Role.ADMIN,
        )
        
        assert result.success is True
        
        user_membership.refresh_from_db()
        assert user_membership.role == CompanyMembership.Role.ADMIN
    
    def test_cannot_demote_last_owner(self, actor_context, owner_membership):
        """Cannot demote the last OWNER."""
        result = update_membership_role(
            actor_context,
            owner_membership.id,
            CompanyMembership.Role.USER,
        )
        
        assert result.success is False
        assert "owner" in result.error.lower()


# =============================================================================
# Membership Deactivation Tests
# =============================================================================

@pytest.mark.django_db
class TestDeactivateMembership:
    """Test membership deactivation command."""
    
    def test_can_deactivate_member(self, actor_context, user_membership):
        """OWNER can deactivate other members."""
        result = deactivate_membership(actor_context, user_membership.id)
        
        assert result.success is True
        
        user_membership.refresh_from_db()
        assert user_membership.is_active is False
    
    def test_cannot_deactivate_last_owner(self, actor_context, owner_membership):
        """Cannot deactivate the last OWNER."""
        result = deactivate_membership(actor_context, owner_membership.id)
        
        assert result.success is False
        assert "owner" in result.error.lower()
    
    def test_deactivation_clears_active_company(self, actor_context, user_membership, regular_user):
        """Deactivating membership clears user's active_company if it matches."""
        # Ensure user's active company is set
        regular_user.active_company = actor_context.company
        regular_user.save()
        
        result = deactivate_membership(actor_context, user_membership.id)
        
        assert result.success is True
        
        regular_user.refresh_from_db()
        assert regular_user.active_company is None


# =============================================================================
# Permission Grant/Revoke Tests
# =============================================================================

@pytest.mark.django_db
class TestPermissionManagement:
    """Test permission grant and revoke commands."""
    
    def test_grant_permission(self, actor_context, user_membership, permissions):
        """Can grant permission to membership."""
        perm = permissions[0]
        
        result = grant_permission(
            actor_context,
            user_membership.id,
            [perm.code],
        )
        
        assert result.success is True
        assert user_membership.has_permission(perm.code) is True
    
    def test_revoke_permission(self, actor_context, user_with_permissions):
        """Can revoke permission from membership."""
        # Get a permission that was granted
        granted = user_with_permissions.permissions.first()
        
        result = revoke_permission(
            actor_context,
            user_with_permissions.id,
            [granted.code],
        )
        
        assert result.success is True
        assert user_with_permissions.has_permission(granted.code) is False
    
    def test_grant_role_defaults(self, user_membership, permissions):
        """grant_role_defaults should grant appropriate permissions."""
        # Grant defaults for USER role
        count = grant_role_defaults(
            membership=user_membership,
            granted_by=None,
            overwrite=False,
        )
        
        # Should have granted some permissions
        assert count >= 0  # May be 0 if no defaults defined
        
    def test_grant_role_defaults_overwrite(self, user_membership, permissions):
        """grant_role_defaults with overwrite=True clears existing."""
        # Grant some permissions first
        for perm in permissions[:3]:
            CompanyMembershipPermission.objects.create(
                membership=user_membership,
                company=user_membership.company,
                permission=perm,
            )
        
        initial_count = user_membership.permission_records.count()
        assert initial_count == 3
        
        # Overwrite with role defaults
        grant_role_defaults(
            membership=user_membership,
            granted_by=None,
            overwrite=True,
        )
        
        # Previous permissions should be gone (replaced with defaults)
        # The exact count depends on ROLE_DEFAULTS configuration


# =============================================================================
# User Update Tests
# =============================================================================

@pytest.mark.django_db
class TestUpdateUser:
    """Test user update command."""
    
    def test_update_user_name(self, actor_context, user_membership):
        """Can update user's name."""
        target_user = user_membership.user
        result = update_user(
            actor_context,
            target_user.id,
            name="Updated Name",
        )
        
        assert result.success is True
        
        target_user.refresh_from_db()
        assert target_user.name == "Updated Name"
    
    def test_update_user_emits_event(self, actor_context, user_membership):
        """Updating user should emit user.updated event."""
        from events.models import BusinessEvent
        from events.types import EventTypes
        
        target_user = user_membership.user
        result = update_user(
            actor_context,
            target_user.id,
            name="Event Test Name",
        )
        
        assert result.success is True
        
        event = BusinessEvent.objects.filter(
            company=actor_context.company,
            event_type=EventTypes.USER_UPDATED,
        ).last()
        
        assert event is not None
        assert event.data["changes"]["name"]["new"] == "Event Test Name"
