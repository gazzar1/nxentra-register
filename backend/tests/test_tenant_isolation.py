# tests/test_tenant_isolation.py
"""
Multi-Tenant Isolation Tests.

Verifies that tenant data is properly isolated:
1. Tenant context routing works correctly
2. Cross-tenant data leakage is impossible
3. System models are always on default database
4. TenantDirectory controls routing
5. Write freeze during migration status
"""
from uuid import uuid4

import pytest

from accounting.models import Account, JournalEntry
from accounts.models import Company, CompanyMembership, User
from events.models import BusinessEvent
from tenant.context import (
    clear_tenant_context,
    get_current_company_id,
    get_current_db_alias,
    is_dedicated_tenant,
    is_shared_tenant,
    set_tenant_context,
    system_db_context,
    tenant_context,
)
from tenant.models import TenantDirectory
from tenant.router import SYSTEM_APPS, TENANT_APPS, TenantDatabaseRouter

# ═════════════════════════════════════════════════════════════════════════════
# TENANT CONTEXT TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestTenantContext:
    """Test tenant context management (contextvars)."""

    def test_default_context_is_none(self):
        clear_tenant_context()
        assert get_current_db_alias() == "default"
        assert get_current_company_id() is None
        assert is_shared_tenant() is True
        assert is_dedicated_tenant() is False

    def test_set_shared_tenant(self):
        set_tenant_context(company_id=1, db_alias="default", is_shared=True)
        assert get_current_db_alias() == "default"
        assert get_current_company_id() == 1
        assert is_shared_tenant() is True
        assert is_dedicated_tenant() is False
        clear_tenant_context()

    def test_set_dedicated_tenant(self):
        set_tenant_context(company_id=2, db_alias="tenant_acme", is_shared=False)
        assert get_current_db_alias() == "tenant_acme"
        assert get_current_company_id() == 2
        assert is_shared_tenant() is False
        assert is_dedicated_tenant() is True
        clear_tenant_context()

    def test_context_manager_restores_previous(self):
        set_tenant_context(company_id=1, db_alias="default", is_shared=True)

        with tenant_context(company_id=99, db_alias="tenant_x", is_shared=False):
            assert get_current_company_id() == 99
            assert get_current_db_alias() == "tenant_x"

        # Restored to previous
        assert get_current_company_id() == 1
        assert get_current_db_alias() == "default"
        clear_tenant_context()

    def test_system_db_context_clears_tenant(self):
        set_tenant_context(company_id=5, db_alias="tenant_five", is_shared=False)

        with system_db_context():
            assert get_current_company_id() is None
            assert get_current_db_alias() == "default"

        # Restored
        assert get_current_company_id() == 5
        clear_tenant_context()

    def test_context_cleanup_on_exception(self):
        set_tenant_context(company_id=1, db_alias="default", is_shared=True)
        try:
            with tenant_context(company_id=99, db_alias="tenant_x", is_shared=False):
                raise ValueError("boom")
        except ValueError:
            pass
        # Must be restored despite exception
        assert get_current_company_id() == 1
        clear_tenant_context()


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE ROUTER TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestDatabaseRouter:
    """Test that the router classifies models correctly."""

    def setup_method(self):
        self.router = TenantDatabaseRouter()
        clear_tenant_context()

    def teardown_method(self):
        clear_tenant_context()

    # -- System models always route to default --

    def test_user_routes_to_default(self):
        assert self.router.db_for_read(User) == "default"
        assert self.router.db_for_write(User) == "default"

    def test_company_routes_to_default(self):
        assert self.router.db_for_read(Company) == "default"
        assert self.router.db_for_write(Company) == "default"

    def test_membership_routes_to_default(self):
        assert self.router.db_for_read(CompanyMembership) == "default"
        assert self.router.db_for_write(CompanyMembership) == "default"

    def test_tenant_directory_routes_to_default(self):
        assert self.router.db_for_read(TenantDirectory) == "default"
        assert self.router.db_for_write(TenantDirectory) == "default"

    # -- Tenant models route based on context --

    def test_account_routes_to_default_without_context(self):
        """Without tenant context, tenant models go to default."""
        assert self.router.db_for_read(Account) == "default"

    def test_account_routes_to_tenant_db_with_context(self):
        set_tenant_context(company_id=1, db_alias="tenant_acme", is_shared=False)
        assert self.router.db_for_read(Account) == "tenant_acme"
        assert self.router.db_for_write(Account) == "tenant_acme"

    def test_journal_entry_routes_to_tenant_db(self):
        set_tenant_context(company_id=1, db_alias="tenant_acme", is_shared=False)
        assert self.router.db_for_read(JournalEntry) == "tenant_acme"
        assert self.router.db_for_write(JournalEntry) == "tenant_acme"

    def test_event_routes_to_tenant_db(self):
        set_tenant_context(company_id=1, db_alias="tenant_acme", is_shared=False)
        assert self.router.db_for_read(BusinessEvent) == "tenant_acme"
        assert self.router.db_for_write(BusinessEvent) == "tenant_acme"

    def test_shared_tenant_routes_to_default(self):
        """Shared tenants route tenant models to default (with RLS)."""
        set_tenant_context(company_id=1, db_alias="default", is_shared=True)
        assert self.router.db_for_read(Account) == "default"
        assert self.router.db_for_write(Account) == "default"

    # -- Migration rules --

    def test_system_apps_migrate_only_on_default(self):
        for app in SYSTEM_APPS:
            assert self.router.allow_migrate("default", app) is True
            assert self.router.allow_migrate("tenant_acme", app) is False

    def test_tenant_apps_migrate_on_all_databases(self):
        for app in TENANT_APPS:
            assert self.router.allow_migrate("default", app) is True
            assert self.router.allow_migrate("tenant_acme", app) is True

    # -- Relations --

    def test_same_tier_relations_allowed(self):
        assert self.router.allow_relation(User(), Company()) is True
        assert self.router.allow_relation(Account(), JournalEntry()) is True


# ═════════════════════════════════════════════════════════════════════════════
# CROSS-TENANT DATA ISOLATION (requires DB)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestCrossTenantIsolation:
    """Verify that data is properly isolated between tenants."""

    def test_accounts_filtered_by_company(self, company, second_company, user):
        """Accounts created for company A must not appear in company B queries."""

        # Create account for company A
        Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="1000",
            name="Cash A",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )

        # Create account for company B
        Account.objects.create(
            public_id=uuid4(),
            company=second_company,
            code="1000",
            name="Cash B",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )

        # Query for company A
        a_accounts = Account.objects.filter(company=company)
        assert a_accounts.count() == 1
        assert a_accounts.first().name == "Cash A"

        # Query for company B
        b_accounts = Account.objects.filter(company=second_company)
        assert b_accounts.count() == 1
        assert b_accounts.first().name == "Cash B"

    def test_events_filtered_by_company(self, company, second_company, user):
        """Events for company A must not leak into company B queries."""
        from events.emitter import emit_event
        from events.types import EventTypes

        # Emit event for company A
        emit_event(
            company=company,
            event_type=EventTypes.COMPANY_CREATED,
            aggregate_type="Company",
            aggregate_id=str(company.public_id),
            data={
                "company_id": company.id,
                "company_public_id": str(company.public_id),
                "name": company.name,
                "slug": company.slug,
                "default_currency": company.default_currency,
            },
            caused_by_user=user,
            idempotency_key=f"isolation-test-a-{uuid4().hex[:8]}",
        )

        # Events for company A
        a_events = BusinessEvent.objects.filter(company=company)
        # Events for company B (should have none from our emit)
        b_events = BusinessEvent.objects.filter(
            company=second_company,
            event_type=EventTypes.COMPANY_CREATED,
        )

        assert a_events.filter(event_type=EventTypes.COMPANY_CREATED).exists()
        # company B might have its own COMPANY_CREATED from fixture, but NOT company A's
        for ev in b_events:
            data = ev.get_data()
            assert data.get("company_id") != company.id

    def test_journal_entries_isolated(self, company, second_company):
        """Journal entries for one company cannot be queried by another."""
        je_a = JournalEntry.objects.create(
            company=company,
            date="2026-01-01",
            memo="Company A entry",
        )
        je_b = JournalEntry.objects.create(
            company=second_company,
            date="2026-01-01",
            memo="Company B entry",
        )

        assert JournalEntry.objects.filter(company=company).count() == 1
        assert JournalEntry.objects.filter(company=second_company).count() == 1
        assert JournalEntry.objects.filter(company=company, memo="Company B entry").count() == 0


# ═════════════════════════════════════════════════════════════════════════════
# TENANT DIRECTORY TESTS
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestTenantDirectory:
    """Test TenantDirectory model and lookup methods."""

    def test_shared_tenant_returns_default(self, company):
        td = TenantDirectory.objects.create(
            company=company,
            mode=TenantDirectory.IsolationMode.SHARED,
            db_alias="default",
            status=TenantDirectory.Status.ACTIVE,
        )
        assert td.is_shared is True
        assert td.is_dedicated is False
        assert td.is_writable is True

        info = TenantDirectory.get_tenant_info(company.id)
        assert info["db_alias"] == "default"
        assert info["is_shared"] is True

    def test_dedicated_tenant_returns_alias(self, company):
        TenantDirectory.objects.create(
            company=company,
            mode=TenantDirectory.IsolationMode.DEDICATED_DB,
            db_alias="tenant_test",
            status=TenantDirectory.Status.ACTIVE,
        )
        info = TenantDirectory.get_tenant_info(company.id)
        assert info["db_alias"] == "tenant_test"
        assert info["is_shared"] is False

    def test_migrating_status_blocks_writes(self, company):
        TenantDirectory.objects.create(
            company=company,
            mode=TenantDirectory.IsolationMode.SHARED,
            db_alias="default",
            status=TenantDirectory.Status.MIGRATING,
        )
        info = TenantDirectory.get_tenant_info(company.id)
        assert info["is_writable"] is False

    def test_missing_entry_returns_shared_default(self):
        """Company without TenantDirectory entry defaults to shared."""
        info = TenantDirectory.get_tenant_info(999999)
        assert info["db_alias"] == "default"
        assert info["is_shared"] is True
        assert info["is_writable"] is True

    def test_get_db_alias_for_company(self, company):
        TenantDirectory.objects.create(
            company=company,
            mode=TenantDirectory.IsolationMode.DEDICATED_DB,
            db_alias="tenant_premium",
            status=TenantDirectory.Status.ACTIVE,
        )
        alias = TenantDirectory.get_db_alias_for_company(company.id)
        assert alias == "tenant_premium"

    def test_migrating_tenant_routes_to_default(self, company):
        """During migration, routing falls back to default."""
        TenantDirectory.objects.create(
            company=company,
            mode=TenantDirectory.IsolationMode.DEDICATED_DB,
            db_alias="tenant_migrating",
            status=TenantDirectory.Status.MIGRATING,
        )
        alias = TenantDirectory.get_db_alias_for_company(company.id)
        assert alias == "default"
