# accounting/tests/test_journal_workflow.py
"""
Integration tests for journal entry workflow.

These tests use API-level testing to verify the full lifecycle
of journal entries: create -> complete -> post -> reverse.

Tests verify the API responses rather than direct database queries
to avoid transaction isolation issues between the API client and
test database connections.
"""
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from accounts.models import Company, CompanyMembership
from accounts.permissions import grant_role_defaults
from accounting.models import Account, JournalEntry
from projections.models import FiscalPeriod
from datetime import date


class TestJournalEntryThinFlow(TransactionTestCase):
    """
    Thin integration test (API-level):
    - Create JE (INCOMPLETE)
    - Complete -> DRAFT
    - Post -> POSTED
    - Reverse -> creates REVERSAL + original becomes REVERSED
    """

    def setUp(self):
        self.client = APIClient()
        User = get_user_model()

        # 1) Create user
        self.user = User.objects.create_user(
            email="tester@example.com",
            password="pass12345",
            name="Tester",
        )

        # 2) Create company (using actual model fields)
        self.company = Company.objects.create(
            name="Test Co",
            slug="testco",
            default_currency="EGP",
        )

        # 3) Create OWNER membership
        self.membership = CompanyMembership.objects.create(
            user=self.user,
            company=self.company,
            role=CompanyMembership.Role.OWNER,
            is_active=True,
        )

        # 4) Grant default permissions
        grant_role_defaults(self.membership, granted_by=self.user)

        # 5) Set active company
        self.user.active_company = self.company
        self.user.save(update_fields=["active_company"])

        # 6) Authenticate
        self.client.force_authenticate(user=self.user)

        # 7) Create accounts via projection (read models require .projection())
        # In production, accounts are created via the command layer which emits
        # events that projections consume.
        self.cash = Account.objects.projection().create(
            company=self.company,
            public_id=uuid.uuid4(),
            code="1000",
            name="Cash",
            account_type="ASSET",
            status="ACTIVE",
        )
        self.sales = Account.objects.projection().create(
            company=self.company,
            public_id=uuid.uuid4(),
            code="4000",
            name="Sales",
            account_type="REVENUE",
            status="ACTIVE",
        )

        # 8) Create fiscal period for the test dates
        # FiscalPeriod is a projection model but allows writes when TESTING=True
        FiscalPeriod.objects.create(
            company=self.company,
            fiscal_year=2026,
            period=1,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            status=FiscalPeriod.Status.OPEN,
            is_current=True,
        )

    def test_journal_entry_full_lifecycle(self):
        """Test complete JE lifecycle: create -> complete -> post -> reverse"""

        # 1) Create JE -> INCOMPLETE
        payload_create = {
            "date": "2026-01-17",
            "memo": "Test JE",
            "lines": [
                {"account_id": self.cash.id, "description": "Cash", "debit": "100.00", "credit": "0.00"},
                {"account_id": self.sales.id, "description": "Sales", "debit": "0.00", "credit": "100.00"},
            ],
        }
        r = self.client.post("/api/accounting/journal-entries/", payload_create, format="json")
        self.assertEqual(r.status_code, 201, r.data if hasattr(r, 'data') else r.content)

        je_id = r.data["id"]
        je_public_id = r.data["public_id"]

        # Verify response data
        self.assertEqual(r.data["company"], self.company.id)
        self.assertEqual(r.data["status"], JournalEntry.Status.INCOMPLETE)
        self.assertEqual(r.data["kind"], JournalEntry.Kind.NORMAL)

        # Lines exist in response
        self.assertEqual(len(r.data["lines"]), 2)
        line1 = next(l for l in r.data["lines"] if l["line_no"] == 1)
        line2 = next(l for l in r.data["lines"] if l["line_no"] == 2)
        self.assertEqual(line1["account"], self.cash.id)
        self.assertEqual(Decimal(line1["debit"]), Decimal("100.00"))
        self.assertEqual(line2["account"], self.sales.id)
        self.assertEqual(Decimal(line2["credit"]), Decimal("100.00"))

        # 2) Complete -> DRAFT
        payload_complete = {
            "date": "2026-01-17",
            "memo": "Test JE (complete)",
            "lines": [
                {"account_id": self.cash.id, "description": "Cash", "debit": "100.00", "credit": "0.00"},
                {"account_id": self.sales.id, "description": "Sales", "debit": "0.00", "credit": "100.00"},
            ],
        }
        r = self.client.put(f"/api/accounting/journal-entries/{je_id}/complete/", payload_complete, format="json")
        self.assertEqual(r.status_code, 200, r.data if hasattr(r, 'data') else r.content)
        self.assertEqual(r.data["status"], JournalEntry.Status.DRAFT)

        # 3) Post -> POSTED
        r = self.client.post(f"/api/accounting/journal-entries/{je_id}/post/", {}, format="json")
        self.assertEqual(r.status_code, 200, r.data if hasattr(r, 'data') else r.content)

        self.assertEqual(r.data["status"], JournalEntry.Status.POSTED)
        self.assertEqual(r.data["kind"], JournalEntry.Kind.NORMAL)
        self.assertIsNotNone(r.data["posted_at"])
        self.assertIsNotNone(r.data["entry_number"])
        self.assertEqual(r.data["posted_by"], self.user.id)

        # 4) Reverse -> creates REVERSAL + original becomes REVERSED
        r = self.client.post(f"/api/accounting/journal-entries/{je_id}/reverse/", {}, format="json")
        self.assertEqual(r.status_code, 201, r.data if hasattr(r, 'data') else r.content)

        reversal_id = r.data["id"]
        reversal_data = r.data

        self.assertEqual(reversal_data["kind"], JournalEntry.Kind.REVERSAL)
        self.assertEqual(reversal_data["status"], JournalEntry.Status.POSTED)
        self.assertEqual(reversal_data["reverses_entry"], je_id)
        self.assertEqual(reversal_data["posted_by"], self.user.id)
        self.assertIsNotNone(reversal_data["posted_at"])
        # entry_number might be in the response - check if present
        if "entry_number" in reversal_data:
            self.assertIsNotNone(reversal_data["entry_number"])

        # Fetch original to verify it's now REVERSED
        r = self.client.get(f"/api/accounting/journal-entries/{je_id}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], JournalEntry.Status.REVERSED)
        self.assertEqual(r.data["reversed_by"], self.user.id)
        self.assertIsNotNone(r.data["reversed_at"])

        # Fetch reversal to verify lines are swapped
        r = self.client.get(f"/api/accounting/journal-entries/{reversal_id}/")
        self.assertEqual(r.status_code, 200)
        rev_lines = sorted(r.data["lines"], key=lambda l: l["line_no"])
        self.assertEqual(len(rev_lines), 2)

        self.assertEqual(rev_lines[0]["account"], self.cash.id)
        self.assertEqual(Decimal(rev_lines[0]["debit"]), Decimal("0.00"))
        self.assertEqual(Decimal(rev_lines[0]["credit"]), Decimal("100.00"))

        self.assertEqual(rev_lines[1]["account"], self.sales.id)
        self.assertEqual(Decimal(rev_lines[1]["debit"]), Decimal("100.00"))
        self.assertEqual(Decimal(rev_lines[1]["credit"]), Decimal("0.00"))

    def test_unbalanced_entry_cannot_be_completed(self):
        """Test that unbalanced entries fail validation"""
        payload = {
            "date": "2026-01-17",
            "memo": "Unbalanced",
            "lines": [
                {"account_id": self.cash.id, "description": "Cash", "debit": "100.00", "credit": "0.00"},
                {"account_id": self.sales.id, "description": "Sales", "debit": "0.00", "credit": "50.00"},
            ],
        }
        r = self.client.post("/api/accounting/journal-entries/", payload, format="json")
        self.assertEqual(r.status_code, 201)
        je_id = r.data["id"]

        # Try to complete - should fail
        r = self.client.put(f"/api/accounting/journal-entries/{je_id}/complete/", payload, format="json")
        self.assertEqual(r.status_code, 400)
        # Error should mention either "balanced" or "line"
        error_text = str(r.data).lower()
        self.assertTrue(
            "balanced" in error_text or "line" in error_text,
            f"Expected error about balance or lines, got: {r.data}"
        )

    def test_posted_entry_cannot_be_edited(self):
        """Test that posted entries are immutable"""
        # Create, complete, post
        payload = {
            "date": "2026-01-17",
            "memo": "Test",
            "lines": [
                {"account_id": self.cash.id, "debit": "100.00", "credit": "0.00"},
                {"account_id": self.sales.id, "debit": "0.00", "credit": "100.00"},
            ],
        }
        r = self.client.post("/api/accounting/journal-entries/", payload, format="json")
        je_id = r.data["id"]
        self.client.put(f"/api/accounting/journal-entries/{je_id}/complete/", payload, format="json")
        self.client.post(f"/api/accounting/journal-entries/{je_id}/post/", {}, format="json")

        # Try to edit - should fail
        r = self.client.patch(f"/api/accounting/journal-entries/{je_id}/", {"memo": "Changed"}, format="json")
        self.assertEqual(r.status_code, 400)
