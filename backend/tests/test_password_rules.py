"""
Password rules: 8+ characters, one uppercase, one number, one special.

Owner decision 2026-07-15: explicit checklist rules enforced on every
password-setting path. Old password hashes are untouched (rules run only
when a password is set), so no existing account is locked out.
"""

import pytest

from accounts.authz import ActorContext
from accounts.commands import admin_reset_password, register_signup, set_user_password
from accounts.models import CompanyMembership, User
from accounts.passwords import password_rule_errors


class TestPasswordRuleErrors:
    """Unit tests for the canonical helper."""

    def test_valid_password_has_no_errors(self):
        assert password_rule_errors("Securepass123!") == []

    def test_short_password(self):
        errors = password_rule_errors("Ab1!")
        assert any("8 characters" in e for e in errors)

    def test_missing_uppercase(self):
        errors = password_rule_errors("lowercase1!")
        assert any("uppercase" in e for e in errors)

    def test_missing_number(self):
        errors = password_rule_errors("Lowercase!!")
        assert any("number" in e for e in errors)

    def test_missing_special_character(self):
        errors = password_rule_errors("Lowercase11")
        assert any("special character" in e for e in errors)

    def test_empty_password_fails_every_rule(self):
        assert len(password_rule_errors("")) == 4
        assert len(password_rule_errors(None)) == 4

    def test_all_rules_reported_together(self):
        # "abc" is short, has no uppercase, no number, no special
        assert len(password_rule_errors("abc")) == 4


@pytest.mark.django_db
class TestRegisterSignupPasswordRules:
    def _register(self, password):
        return register_signup(
            email="rules@test.example",
            password=password,
            company_name="Rules Co",
            name="Rules Tester",
            tos_accepted=True,
        )

    def test_rejects_missing_uppercase(self):
        result = self._register("lowercase1!")
        assert not result.success
        assert "uppercase" in result.error

    def test_rejects_missing_number(self):
        result = self._register("Lowercase!!")
        assert not result.success
        assert "number" in result.error

    def test_rejects_missing_special_character(self):
        result = self._register("Lowercase11")
        assert not result.success
        assert "special character" in result.error

    def test_rejects_short_password(self):
        result = self._register("Ab1!")
        assert not result.success
        assert "8 characters" in result.error

    def test_accepts_rule_passing_password(self):
        result = self._register("Securepass123!")
        assert result.success


@pytest.mark.django_db
class TestPasswordChangePaths:
    """set_user_password and admin_reset_password enforce the same rules.

    Before this change, both paths had NO validation at all — a user could
    change their password to a single character.
    """

    def _make_actor(self):
        result = register_signup(
            email="changer@test.example",
            password="Securepass123!",
            company_name="Change Co",
            tos_accepted=True,
        )
        assert result.success
        user = result.data["user"]
        membership = CompanyMembership.objects.get(user=user)
        perms = frozenset(membership.permissions.values_list("code", flat=True))
        return ActorContext(user=user, company=membership.company, membership=membership, perms=perms)

    def test_set_user_password_rejects_weak(self):
        actor = self._make_actor()
        result = set_user_password(actor, actor.user.id, "a")
        assert not result.success
        assert "8 characters" in result.error

    def test_set_user_password_accepts_strong(self):
        actor = self._make_actor()
        result = set_user_password(actor, actor.user.id, "NewSecure456?")
        assert result.success
        actor.user.refresh_from_db()
        assert actor.user.check_password("NewSecure456?")

    def test_admin_reset_password_rejects_weak(self):
        actor = self._make_actor()
        admin = User.objects.create_superuser(email="admin@test.example", password="Adminpass123!")
        result = admin_reset_password(admin, actor.user.id, "weakpass")
        assert not result.success

    def test_admin_reset_password_accepts_strong(self):
        actor = self._make_actor()
        admin = User.objects.create_superuser(email="admin2@test.example", password="Adminpass123!")
        result = admin_reset_password(admin, actor.user.id, "ResetSecure789#")
        assert result.success


@pytest.mark.django_db
class TestOldPasswordsUnaffected:
    def test_existing_weak_password_still_authenticates(self):
        """Rules run only when a password is SET — old hashes keep working."""
        user = User.objects.create_user(email="legacy@test.example", password="weak")
        assert user.check_password("weak")
