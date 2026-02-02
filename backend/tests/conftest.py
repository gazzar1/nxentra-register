# tests/conftest.py
"""
Pytest fixtures for Nxentra tests.

Updated to match actual Nxentra API signatures:
- ActorContext requires: user, company, membership, perms
- emit_event() takes actor as first arg
- emit_event_no_actor() takes company, user as separate args
"""

import pytest
from django.conf import settings
from decimal import Decimal
from datetime import date, datetime
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.utils import timezone

# Import your models
from accounts.models import Company, CompanyMembership, NxPermission, CompanyMembershipPermission
from accounts.authz import ActorContext
from accounting.models import Account, JournalEntry, JournalLine, AnalysisDimension
from events.models import BusinessEvent, EventBookmark
from events.types import EventTypes
from projections.models import AccountBalance


User = get_user_model()


@pytest.fixture(autouse=True, scope="session")
def _testing_settings(django_db_setup, django_db_blocker):
    """Ensure test-only settings are enabled for read-model guards and validation."""
    settings.TESTING = True
    settings.DISABLE_EVENT_VALIDATION = True
    settings.RLS_BYPASS = True
    with django_db_blocker.unblock():
        from accounts import rls
        from django.db import connection

        rls.set_rls_bypass(True, conn=connection)


@pytest.fixture(autouse=True)
def _rls_bypass(db):
    """Keep RLS bypass enabled for tests using the default connection."""
    from accounts import rls
    from django.db import connection

    rls.set_rls_bypass(True, conn=connection)


# =============================================================================
# Company & User Fixtures
# =============================================================================

@pytest.fixture
def company(db):
    """Create a test company."""
    return Company.objects.create(
        public_id=uuid4(),
        name="Test Company",
        name_ar="شركة اختبار",
        slug="test-company",
        default_currency="USD",
        fiscal_year_start_month=1,
        is_active=True,
    )


@pytest.fixture
def second_company(db):
    """Create a second test company for multi-tenant tests."""
    return Company.objects.create(
        public_id=uuid4(),
        name="Second Company",
        slug="second-company",
        default_currency="EUR",
        is_active=True,
    )


@pytest.fixture
def user(db, company):
    """Create a test user with owner membership."""
    user = User.objects.create_user(
        public_id=uuid4(),
        email="owner@test.com",
        password="testpass123",
        name="Test Owner",
    )
    user.active_company = company
    user.save()
    return user


@pytest.fixture
def admin_user(db, company):
    """Create an admin user."""
    user = User.objects.create_user(
        public_id=uuid4(),
        email="admin@test.com",
        password="testpass123",
        name="Test Admin",
    )
    user.active_company = company
    user.save()
    return user


@pytest.fixture
def regular_user(db, company):
    """Create a regular user."""
    user = User.objects.create_user(
        public_id=uuid4(),
        email="user@test.com",
        password="testpass123",
        name="Test User",
    )
    user.active_company = company
    user.save()
    return user


@pytest.fixture
def owner_membership(db, company, user):
    """Create owner membership."""
    return CompanyMembership.objects.create(
        public_id=uuid4(),
        company=company,
        user=user,
        role=CompanyMembership.Role.OWNER,
        is_active=True,
    )


@pytest.fixture
def admin_membership(db, company, admin_user):
    """Create admin membership."""
    return CompanyMembership.objects.create(
        public_id=uuid4(),
        company=company,
        user=admin_user,
        role=CompanyMembership.Role.ADMIN,
        is_active=True,
    )


@pytest.fixture
def user_membership(db, company, regular_user):
    """Create regular user membership."""
    return CompanyMembership.objects.create(
        public_id=uuid4(),
        company=company,
        user=regular_user,
        role=CompanyMembership.Role.USER,
        is_active=True,
    )


@pytest.fixture
def deactivated_membership(db, company):
    """Create a deactivated membership."""
    user = User.objects.create_user(
        public_id=uuid4(),
        email="inactive@test.com",
        password="testpass123",
        name="Inactive User",
    )
    return CompanyMembership.objects.create(
        public_id=uuid4(),
        company=company,
        user=user,
        role=CompanyMembership.Role.USER,
        is_active=False,  # Deactivated!
    )


# =============================================================================
# Actor Context Fixtures (Updated for your API)
# =============================================================================

@pytest.fixture
def actor_context(user, company, owner_membership):
    """Create an ActorContext for the owner user."""
    # Get permissions for this membership
    perms = frozenset(
        owner_membership.permissions.values_list("code", flat=True)
    )
    
    return ActorContext(
        user=user,
        company=company,
        membership=owner_membership,
        perms=perms,
    )


@pytest.fixture
def admin_actor_context(admin_user, company, admin_membership):
    """Create an ActorContext for the admin user."""
    perms = frozenset(
        admin_membership.permissions.values_list("code", flat=True)
    )
    
    return ActorContext(
        user=admin_user,
        company=company,
        membership=admin_membership,
        perms=perms,
    )


@pytest.fixture
def user_actor_context(regular_user, company, user_membership):
    """Create an ActorContext for a regular user."""
    perms = frozenset(
        user_membership.permissions.values_list("code", flat=True)
    )
    
    return ActorContext(
        user=regular_user,
        company=company,
        membership=user_membership,
        perms=perms,
    )


# =============================================================================
# Permission Fixtures
# =============================================================================

@pytest.fixture
def permissions(db):
    """Create standard permissions."""
    permission_codes = [
        "accounts.view",
        "accounts.create",
        "accounts.update",
        "accounts.delete",
        "journal.view",
        "journal.create",
        "journal.post",
        "journal.reverse",
        "reports.view",
        "company.update",
        "company.settings.update",
        "users.view",
        "users.create",
        "users.update",
    ]
    
    permissions = []
    for code in permission_codes:
        perm, _ = NxPermission.objects.get_or_create(
            code=code,
            defaults={
                "name": code,
                "module": code.split(".")[0],
            }
        )
        permissions.append(perm)
    
    return permissions


@pytest.fixture
def user_with_permissions(db, company, user_membership, permissions):
    """Give the user membership specific permissions."""
    for perm in permissions[:5]:  # Give first 5 permissions
        CompanyMembershipPermission.objects.create(
            membership=user_membership,
            company=user_membership.company,
            permission=perm,
        )
    return user_membership


# =============================================================================
# Account Fixtures
# =============================================================================

@pytest.fixture
def cash_account(db, company):
    """Create a cash account."""
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code="1000",
        name="Cash",
        name_ar="النقدية",
        account_type=Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        status=Account.Status.ACTIVE,
    )


@pytest.fixture
def revenue_account(db, company):
    """Create a revenue account."""
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code="4000",
        name="Sales Revenue",
        name_ar="إيرادات المبيعات",
        account_type=Account.AccountType.REVENUE,
        normal_balance=Account.NormalBalance.CREDIT,
        status=Account.Status.ACTIVE,
    )


@pytest.fixture
def expense_account(db, company):
    """Create an expense account."""
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code="5000",
        name="Operating Expenses",
        account_type=Account.AccountType.EXPENSE,
        normal_balance=Account.NormalBalance.DEBIT,
        status=Account.Status.ACTIVE,
    )


@pytest.fixture
def accounts_payable(db, company):
    """Create an accounts payable account."""
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code="2000",
        name="Accounts Payable",
        account_type=Account.AccountType.PAYABLE,
        normal_balance=Account.NormalBalance.CREDIT,
        status=Account.Status.ACTIVE,
    )


@pytest.fixture
def header_account(db, company):
    """Create a header account."""
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code="1",
        name="Assets",
        account_type=Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        is_header=True,
        status=Account.Status.ACTIVE,
    )


@pytest.fixture
def locked_account(db, company):
    """Create a locked account."""
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code="1001",
        name="Locked Cash",
        account_type=Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        status=Account.Status.LOCKED,
    )


@pytest.fixture
def memo_account(db, company):
    """Create a memo/statistical account."""
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code="9000",
        name="Employee Count",
        account_type=Account.AccountType.MEMO,
        normal_balance=Account.NormalBalance.DEBIT,
        unit_of_measure="employees",
        status=Account.Status.ACTIVE,
    )


# =============================================================================
# Journal Entry Fixtures
# =============================================================================

@pytest.fixture
def incomplete_journal_entry(db, company, user):
    """Create an incomplete journal entry."""
    return JournalEntry.objects.create(
        public_id=uuid4(),
        company=company,
        date=date.today(),
        memo="Test incomplete entry",
        status=JournalEntry.Status.INCOMPLETE,
        created_by=user,
    )


@pytest.fixture
def draft_journal_entry(db, company, user, cash_account, revenue_account):
    """Create a draft journal entry with balanced lines."""
    entry = JournalEntry.objects.create(
        public_id=uuid4(),
        company=company,
        date=date.today(),
        memo="Test draft entry",
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )
    
    JournalLine.objects.create(
        entry=entry,
        company=company,
        line_no=1,
        account=cash_account,
        description="Cash received",
        debit=Decimal("1000.00"),
        credit=Decimal("0.00"),
    )
    
    JournalLine.objects.create(
        entry=entry,
        company=company,
        line_no=2,
        account=revenue_account,
        description="Revenue earned",
        debit=Decimal("0.00"),
        credit=Decimal("1000.00"),
    )
    
    return entry


@pytest.fixture
def posted_journal_entry(db, company, user, cash_account, revenue_account):
    """Create a posted journal entry."""
    entry = JournalEntry.objects.create(
        public_id=uuid4(),
        company=company,
        date=date.today(),
        memo="Test posted entry",
        entry_number="JE-2024-0001",
        status=JournalEntry.Status.POSTED,
        posted_at=timezone.now(),
        posted_by=user,
        created_by=user,
    )
    
    JournalLine.objects.create(
        entry=entry,
        company=company,
        line_no=1,
        account=cash_account,
        description="Cash received",
        debit=Decimal("500.00"),
        credit=Decimal("0.00"),
    )
    
    JournalLine.objects.create(
        entry=entry,
        company=company,
        line_no=2,
        account=revenue_account,
        description="Revenue earned",
        debit=Decimal("0.00"),
        credit=Decimal("500.00"),
    )
    
    return entry


@pytest.fixture
def unbalanced_lines_data():
    """Line data that doesn't balance (for testing validation)."""
    return [
        {"account_public_id": "xxx", "debit": "100.00", "credit": "0.00"},
        {"account_public_id": "yyy", "debit": "0.00", "credit": "50.00"},  # Unbalanced!
    ]


# =============================================================================
# Event Fixtures (Updated for your API)
# =============================================================================

@pytest.fixture
def account_created_event(db, company, user, cash_account, actor_context):
    """Create an account.created event using emit_event_no_actor."""
    from events.emitter import emit_event_no_actor
    
    return emit_event_no_actor(
        company=company,
        user=user,
        event_type=EventTypes.ACCOUNT_CREATED,
        aggregate_type="Account",
        aggregate_id=str(cash_account.public_id),
        data={
            "account_public_id": str(cash_account.public_id),
            "code": cash_account.code,
            "name": cash_account.name,
            "account_type": cash_account.account_type,
            "normal_balance": cash_account.normal_balance,
            "is_header": False,
        },
        idempotency_key=f"test:account.created:{cash_account.public_id}",
    )


@pytest.fixture
def journal_entry_posted_event(db, company, user, posted_journal_entry, cash_account, revenue_account):
    """Create a journal_entry.posted event."""
    from events.emitter import emit_event_no_actor
    
    return emit_event_no_actor(
        company=company,
        user=user,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=str(posted_journal_entry.public_id),
        data={
            "entry_public_id": str(posted_journal_entry.public_id),
            "entry_number": posted_journal_entry.entry_number,
            "date": posted_journal_entry.date.isoformat(),
            "memo": posted_journal_entry.memo,
            "kind": "NORMAL",
            "posted_at": timezone.now().isoformat(),
            "posted_by_id": user.id,
            "posted_by_email": user.email,
            "total_debit": "500.00",
            "total_credit": "500.00",
            "lines": [
                {
                    "line_no": 1,
                    "account_public_id": str(cash_account.public_id),
                    "account_code": cash_account.code,
                    "description": "Cash received",
                    "debit": "500.00",
                    "credit": "0.00",
                },
                {
                    "line_no": 2,
                    "account_public_id": str(revenue_account.public_id),
                    "account_code": revenue_account.code,
                    "description": "Revenue earned",
                    "debit": "0.00",
                    "credit": "500.00",
                },
            ],
        },
        idempotency_key=f"test:journal_entry.posted:{posted_journal_entry.public_id}",
    )


# =============================================================================
# Projection Fixtures
# =============================================================================

@pytest.fixture
def account_balance(db, company, cash_account):
    """Create an account balance projection."""
    return AccountBalance.objects.create(
        company=company,
        account=cash_account,
        balance=Decimal("1000.00"),
        debit_total=Decimal("1500.00"),
        credit_total=Decimal("500.00"),
        entry_count=5,
        last_entry_date=date.today(),
    )


@pytest.fixture
def event_bookmark(db, company):
    """Create an event bookmark for projections."""
    return EventBookmark.objects.create(
        consumer_name="account_balance",
        company=company,
    )


# =============================================================================
# Analysis Dimension Fixtures
# =============================================================================

@pytest.fixture
def cost_center_dimension(db, company):
    """Create a cost center analysis dimension."""
    return AnalysisDimension.objects.create(
        public_id=uuid4(),
        company=company,
        code="CC",
        name="Cost Center",
        name_ar="مركز التكلفة",
        is_required_on_posting=False,
        display_order=1,
    )


# =============================================================================
# API Client Fixtures
# =============================================================================

@pytest.fixture
def api_client():
    """Create a DRF API client."""
    from rest_framework.test import APIClient
    return APIClient()


@pytest.fixture
def authenticated_client(api_client, user):
    """Create an authenticated API client."""
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def admin_authenticated_client(api_client, admin_user):
    """Create an authenticated API client for admin."""
    api_client.force_authenticate(user=admin_user)
    return api_client
