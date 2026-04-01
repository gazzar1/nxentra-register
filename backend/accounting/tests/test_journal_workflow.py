# accounting/tests/test_journal_workflow.py
"""
Integration tests for journal entry workflow.

These tests use API-level testing to verify the full lifecycle
of journal entries: create -> complete -> post -> reverse.

Tests verify the API responses rather than direct database queries
to avoid transaction isolation issues between the API client and
test database connections.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from accounting.models import Account, JournalEntry
from accounts.models import Company, CompanyMembership
from accounts.permissions import grant_role_defaults
from projections.models import FiscalPeriod


@pytest.fixture
def je_company(db):
    return Company.objects.create(
        name="Test Co",
        slug=f"testco-{uuid.uuid4().hex[:6]}",
        default_currency="EGP",
    )


@pytest.fixture
def je_user(db, je_company):
    User = get_user_model()
    uid = uuid.uuid4().hex[:8]
    user = User.objects.create_user(
        email=f"tester-{uid}@example.com",
        password="pass12345",
        name="Tester",
    )
    user.active_company = je_company
    user.save(update_fields=["active_company"])
    return user


@pytest.fixture
def je_membership(db, je_company, je_user):
    membership = CompanyMembership.objects.create(
        user=je_user,
        company=je_company,
        role=CompanyMembership.Role.OWNER,
        is_active=True,
    )
    grant_role_defaults(membership, granted_by=je_user)
    return membership


@pytest.fixture
def je_client(je_user, je_membership):
    client = APIClient()
    client.force_authenticate(user=je_user)
    return client


@pytest.fixture
def je_accounts(je_company):
    cash = Account.objects.projection().create(
        company=je_company,
        public_id=uuid.uuid4(),
        code="1000",
        name="Cash",
        account_type="ASSET",
        status="ACTIVE",
    )
    sales = Account.objects.projection().create(
        company=je_company,
        public_id=uuid.uuid4(),
        code="4000",
        name="Sales",
        account_type="REVENUE",
        status="ACTIVE",
    )
    return cash, sales


@pytest.fixture
def je_fiscal_period(je_company):
    return FiscalPeriod.objects.create(
        company=je_company,
        fiscal_year=2026,
        period=1,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        status=FiscalPeriod.Status.OPEN,
        is_current=True,
    )


@pytest.mark.django_db(transaction=True)
class TestJournalEntryThinFlow:
    """
    Thin integration test (API-level):
    - Create JE (INCOMPLETE)
    - Complete -> DRAFT
    - Post -> POSTED
    - Reverse -> creates REVERSAL + original becomes REVERSED
    """

    def test_journal_entry_full_lifecycle(self, je_client, je_company, je_user,
                                           je_accounts, je_fiscal_period):
        """Test complete JE lifecycle: create -> complete -> post -> reverse"""
        cash, sales = je_accounts

        # 1) Create JE -> INCOMPLETE
        payload_create = {
            "date": "2026-01-17",
            "memo": "Test JE",
            "lines": [
                {"account_id": cash.id, "description": "Cash", "debit": "100.00", "credit": "0.00"},
                {"account_id": sales.id, "description": "Sales", "debit": "0.00", "credit": "100.00"},
            ],
        }
        r = je_client.post("/api/accounting/journal-entries/", payload_create, format="json")
        assert r.status_code == 201, r.data if hasattr(r, 'data') else r.content

        je_id = r.data["id"]

        # Verify response data
        assert r.data["company"] == je_company.id
        assert r.data["status"] == JournalEntry.Status.INCOMPLETE
        assert r.data["kind"] == JournalEntry.Kind.NORMAL

        # Lines exist in response
        assert len(r.data["lines"]) == 2
        line1 = next(l for l in r.data["lines"] if l["line_no"] == 1)
        line2 = next(l for l in r.data["lines"] if l["line_no"] == 2)
        assert line1["account"] == cash.id
        assert Decimal(line1["debit"]) == Decimal("100.00")
        assert line2["account"] == sales.id
        assert Decimal(line2["credit"]) == Decimal("100.00")

        # 2) Complete -> DRAFT
        payload_complete = {
            "date": "2026-01-17",
            "memo": "Test JE (complete)",
            "lines": [
                {"account_id": cash.id, "description": "Cash", "debit": "100.00", "credit": "0.00"},
                {"account_id": sales.id, "description": "Sales", "debit": "0.00", "credit": "100.00"},
            ],
        }
        r = je_client.put(f"/api/accounting/journal-entries/{je_id}/complete/", payload_complete, format="json")
        assert r.status_code == 200, r.data if hasattr(r, 'data') else r.content
        assert r.data["status"] == JournalEntry.Status.DRAFT

        # 3) Post -> POSTED
        r = je_client.post(f"/api/accounting/journal-entries/{je_id}/post/", {}, format="json")
        assert r.status_code == 200, r.data if hasattr(r, 'data') else r.content

        assert r.data["status"] == JournalEntry.Status.POSTED
        assert r.data["kind"] == JournalEntry.Kind.NORMAL
        assert r.data["posted_at"] is not None
        assert r.data["entry_number"] is not None
        assert r.data["posted_by"] == je_user.id

        # 4) Reverse -> creates REVERSAL + original becomes REVERSED
        r = je_client.post(f"/api/accounting/journal-entries/{je_id}/reverse/", {}, format="json")
        assert r.status_code == 201, r.data if hasattr(r, 'data') else r.content

        reversal_id = r.data["id"]
        reversal_data = r.data

        assert reversal_data["kind"] == JournalEntry.Kind.REVERSAL
        assert reversal_data["status"] == JournalEntry.Status.POSTED
        assert reversal_data["reverses_entry"] == je_id
        assert reversal_data["posted_by"] == je_user.id
        assert reversal_data["posted_at"] is not None
        if "entry_number" in reversal_data:
            assert reversal_data["entry_number"] is not None

        # Fetch original to verify it's now REVERSED
        r = je_client.get(f"/api/accounting/journal-entries/{je_id}/")
        assert r.status_code == 200
        assert r.data["status"] == JournalEntry.Status.REVERSED
        assert r.data["reversed_by"] == je_user.id
        assert r.data["reversed_at"] is not None

        # Fetch reversal to verify lines are swapped
        r = je_client.get(f"/api/accounting/journal-entries/{reversal_id}/")
        assert r.status_code == 200
        rev_lines = sorted(r.data["lines"], key=lambda l: l["line_no"])
        assert len(rev_lines) == 2

        assert rev_lines[0]["account"] == cash.id
        assert Decimal(rev_lines[0]["debit"]) == Decimal("0.00")
        assert Decimal(rev_lines[0]["credit"]) == Decimal("100.00")

        assert rev_lines[1]["account"] == sales.id
        assert Decimal(rev_lines[1]["debit"]) == Decimal("100.00")
        assert Decimal(rev_lines[1]["credit"]) == Decimal("0.00")

    def test_unbalanced_entry_cannot_be_completed(self, je_client, je_accounts,
                                                    je_fiscal_period):
        """Test that unbalanced entries fail validation"""
        cash, sales = je_accounts
        payload = {
            "date": "2026-01-17",
            "memo": "Unbalanced",
            "lines": [
                {"account_id": cash.id, "description": "Cash", "debit": "100.00", "credit": "0.00"},
                {"account_id": sales.id, "description": "Sales", "debit": "0.00", "credit": "50.00"},
            ],
        }
        r = je_client.post("/api/accounting/journal-entries/", payload, format="json")
        assert r.status_code == 201
        je_id = r.data["id"]

        # Try to complete - should fail
        r = je_client.put(f"/api/accounting/journal-entries/{je_id}/complete/", payload, format="json")
        assert r.status_code == 400
        # Error should mention either "balanced" or "line"
        error_text = str(r.data).lower()
        assert "balanced" in error_text or "line" in error_text, \
            f"Expected error about balance or lines, got: {r.data}"

    def test_posted_entry_cannot_be_edited(self, je_client, je_accounts,
                                            je_fiscal_period):
        """Test that posted entries are immutable"""
        cash, sales = je_accounts
        payload = {
            "date": "2026-01-17",
            "memo": "Test",
            "lines": [
                {"account_id": cash.id, "debit": "100.00", "credit": "0.00"},
                {"account_id": sales.id, "debit": "0.00", "credit": "100.00"},
            ],
        }
        r = je_client.post("/api/accounting/journal-entries/", payload, format="json")
        je_id = r.data["id"]
        je_client.put(f"/api/accounting/journal-entries/{je_id}/complete/", payload, format="json")
        je_client.post(f"/api/accounting/journal-entries/{je_id}/post/", {}, format="json")

        # Try to edit - should fail
        r = je_client.patch(f"/api/accounting/journal-entries/{je_id}/", {"memo": "Changed"}, format="json")
        assert r.status_code == 400
