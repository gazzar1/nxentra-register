# tests/test_a138_arabic_fields.py
"""
A138 — optional Arabic data-entry field visibility (Company.enable_arabic_fields).

Verifies the backend half of the feature:
- new companies default to Arabic fields OFF (English-first),
- the onboarding Arabic/bilingual choice turns them ON,
- a settings update can toggle them, and toggling OFF preserves existing
  Arabic data (name_ar) — this is a visibility flag, not a data wipe,
- the flag is tenant-isolated,
- the flag is exposed by the company serializers the frontend reads.
"""

from uuid import uuid4

import pytest

from accounts.authz import ActorContext
from accounts.commands import complete_onboarding, update_company_settings
from accounts.models import Company
from accounts.serializers import CompanyModelSerializer, CompanyOutputSerializer
from projections.write_barrier import command_writes_allowed


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


@pytest.mark.django_db
class TestEnableArabicFieldsDefault:
    def test_new_company_defaults_arabic_fields_off(self):
        """A freshly created company is English-first (model default False)."""
        c = Company.objects.create(
            public_id=uuid4(),
            name="Fresh Co",
            slug=f"fresh-{uuid4().hex[:8]}",
            default_currency="USD",
        )
        assert c.enable_arabic_fields is False


@pytest.mark.django_db
class TestOnboardingChoice:
    def test_onboarding_bilingual_enables_arabic_fields(self, actor, company):
        result = complete_onboarding(actor, company_name="Acme", enable_arabic_fields=True)
        assert result.success
        company.refresh_from_db()
        assert company.enable_arabic_fields is True

    def test_onboarding_english_only_keeps_arabic_fields_off(self, actor, company):
        result = complete_onboarding(actor, company_name="Acme", enable_arabic_fields=False)
        assert result.success
        company.refresh_from_db()
        assert company.enable_arabic_fields is False


@pytest.mark.django_db
class TestSettingsToggle:
    def test_settings_update_can_turn_arabic_fields_on_then_off(self, actor, company):
        assert company.enable_arabic_fields is False

        on = update_company_settings(actor, enable_arabic_fields=True)
        assert on.success
        company.refresh_from_db()
        assert company.enable_arabic_fields is True

        off = update_company_settings(actor, enable_arabic_fields=False)
        assert off.success
        company.refresh_from_db()
        assert company.enable_arabic_fields is False

    def test_toggling_off_preserves_existing_arabic_data(self, actor, company):
        """Turning the visibility flag OFF must NOT delete stored Arabic values."""
        company.name_ar = "شركة الاختبار"
        with command_writes_allowed():
            company.save(update_fields=["name_ar"])

        result = update_company_settings(actor, enable_arabic_fields=False)
        assert result.success
        company.refresh_from_db()
        assert company.enable_arabic_fields is False
        assert company.name_ar == "شركة الاختبار"  # preserved


@pytest.mark.django_db
class TestTenantIsolation:
    def test_flag_is_isolated_between_companies(self, actor, company):
        other = Company.objects.create(
            public_id=uuid4(),
            name="Other Co",
            slug=f"other-{uuid4().hex[:8]}",
            default_currency="EUR",
        )
        assert other.enable_arabic_fields is False

        result = update_company_settings(actor, enable_arabic_fields=True)
        assert result.success

        company.refresh_from_db()
        other.refresh_from_db()
        assert company.enable_arabic_fields is True
        assert other.enable_arabic_fields is False  # untouched


@pytest.mark.django_db
class TestSerializerExposure:
    def test_company_serializers_expose_enable_arabic_fields(self, company):
        company.enable_arabic_fields = True
        with command_writes_allowed():
            company.save(update_fields=["enable_arabic_fields"])

        out = CompanyOutputSerializer(company).data
        assert out["enable_arabic_fields"] is True

        model = CompanyModelSerializer(company).data
        assert model["enable_arabic_fields"] is True
