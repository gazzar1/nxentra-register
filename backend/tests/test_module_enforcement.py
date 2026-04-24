# tests/test_module_enforcement.py
"""
Tests for module-level access enforcement.

Covers:
- Disabled module API returns 403
- Enabled module API returns 200
- Core module endpoints always accessible
- Data preserved after disable (no deletion)
- Re-enabling restores access
"""

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from accounts.models import Company, CompanyMembership, CompanyModule

User = get_user_model()


@pytest.fixture
def module_company(db):
    """Company for module enforcement tests."""
    uid = uuid4().hex[:8]
    return Company.objects.create(
        public_id=uuid4(),
        name="Module Test Co",
        slug=f"module-test-{uid}",
        default_currency="USD",
        fiscal_year_start_month=1,
        is_active=True,
    )


@pytest.fixture
def module_user(db, module_company):
    """User with owner membership for module tests."""
    user = User.objects.create_user(
        public_id=uuid4(),
        email=f"moduser-{uuid4().hex[:6]}@test.com",
        password="testpass123",
        name="Module User",
    )
    user.active_company = module_company
    user.save()
    CompanyMembership.objects.create(
        public_id=uuid4(),
        company=module_company,
        user=user,
        role=CompanyMembership.Role.OWNER,
        is_active=True,
    )
    return user


@pytest.fixture
def module_client(module_user):
    """Authenticated API client for module tests."""
    client = APIClient()
    client.force_authenticate(user=module_user)
    return client


@pytest.mark.django_db
class TestModuleEnforcementBackend:
    """Backend module enforcement tests."""

    def test_disabled_module_returns_403(self, module_client, module_company):
        """Accessing a disabled module's API should return 403."""
        # Clinic module is not enabled (no CompanyModule record)
        response = module_client.get("/api/clinic/patients/")
        assert response.status_code == 403
        assert "not enabled" in response.data["detail"].lower()

    def test_enabled_module_returns_200(self, module_client, module_company):
        """Accessing an enabled module's API should succeed."""
        CompanyModule.objects.create(
            company=module_company,
            module_key="clinic",
            is_enabled=True,
        )
        response = module_client.get("/api/clinic/patients/")
        assert response.status_code == 200

    def test_core_module_always_accessible(self, module_client, module_company):
        """Core modules (accounting) should always be accessible."""
        # No CompanyModule record needed for core modules
        response = module_client.get("/api/accounting/journal-entries/")
        assert response.status_code == 200

    def test_disable_preserves_data(self, module_client, module_company):
        """Disabling a module should not delete its data."""
        from clinic.models import Patient
        from projections.write_barrier import command_writes_allowed

        # Enable clinic and create a patient
        CompanyModule.objects.create(
            company=module_company,
            module_key="clinic",
            is_enabled=True,
        )
        with command_writes_allowed():
            patient = Patient.objects.create(
                company=module_company,
                name="Test Patient",
                phone="1234567890",
            )

        # Disable the module
        CompanyModule.objects.filter(company=module_company, module_key="clinic").update(is_enabled=False)

        # API should be blocked
        response = module_client.get("/api/clinic/patients/")
        assert response.status_code == 403

        # But data still exists in the database
        assert Patient.objects.filter(pk=patient.pk).exists()

    def test_reenable_restores_access(self, module_client, module_company):
        """Re-enabling a module should restore API access."""
        cm = CompanyModule.objects.create(
            company=module_company,
            module_key="clinic",
            is_enabled=False,
        )

        # Disabled → 403
        response = module_client.get("/api/clinic/patients/")
        assert response.status_code == 403

        # Re-enable
        cm.is_enabled = True
        cm.save()

        # Now accessible
        response = module_client.get("/api/clinic/patients/")
        assert response.status_code == 200

    def test_warehouses_always_accessible_as_core_setup(self, module_client, module_company):
        """Warehouses are core setup — accessible even without inventory module."""
        response = module_client.get("/api/inventory/warehouses/")
        assert response.status_code == 200

    def test_disabled_inventory_blocks_stock_balances(self, module_client, module_company):
        """Inventory module enforcement blocks non-setup endpoints."""
        response = module_client.get("/api/inventory/balances/")
        assert response.status_code == 403

    def test_enabled_inventory_allows_stock_balances(self, module_client, module_company):
        """Enabled inventory module allows stock balance access."""
        CompanyModule.objects.create(
            company=module_company,
            module_key="inventory",
            is_enabled=True,
        )
        response = module_client.get("/api/inventory/balances/")
        assert response.status_code == 200

    def test_items_always_accessible_as_core_setup(self, module_client, module_company):
        """Items, tax codes, posting profiles are core setup — always accessible."""
        response = module_client.get("/api/sales/items/")
        assert response.status_code == 200

    def test_disabled_sales_blocks_invoices(self, module_client, module_company):
        """Sales module enforcement blocks invoice endpoints."""
        response = module_client.get("/api/sales/invoices/")
        assert response.status_code == 403

    def test_disabled_properties_returns_403(self, module_client, module_company):
        """Properties module enforcement works."""
        response = module_client.get("/api/properties/properties/")
        assert response.status_code == 403

    def test_enabled_properties_returns_200(self, module_client, module_company):
        """Enabled properties module is accessible."""
        CompanyModule.objects.create(
            company=module_company,
            module_key="properties",
            is_enabled=True,
        )
        response = module_client.get("/api/properties/properties/")
        assert response.status_code == 200
