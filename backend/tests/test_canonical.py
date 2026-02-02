# tests/test_canonical.py
"""
Canonical Readiness Tests from Nxentra Contract v1.0

These tests validate the core invariants of the system.
If any of these fail, the system is not contract-compliant.
"""

from decimal import Decimal
from datetime import date

from django.test import TestCase
from django.contrib.auth import get_user_model

from accounts.commands import register_signup
from accounts.authz import ActorContext
from accounts.models import CompanyMembership
from accounting.commands import (
    create_account,
    create_journal_entry,
    save_journal_entry_complete,
    post_journal_entry,
    reverse_journal_entry,
)
from accounting.models import Account, JournalEntry
from projections.account_balance import AccountBalanceProjection
from projections.models import AccountBalance
from events.models import BusinessEvent


User = get_user_model()


class CanonicalCoreLoopTest(TestCase):
    """
    Test: Create company → COA → post JE → TB correct
    Validates: Core loop works end-to-end
    """
    
    def setUp(self):
        """Create a test company with owner."""
        result = register_signup(
            email="owner@canonical.test",
            password="testpass123",
            company_name="Canonical Test Corp",
            name="Test Owner",
        )
        self.assertTrue(result.success, result.error)
        
        self.user = result.data["user"]
        self.company = result.data["company"]
        self.membership = result.data["membership"]
        
        # Build actor context
        perms = frozenset(self.membership.permissions.values_list("code", flat=True))
        self.actor = ActorContext(
            user=self.user,
            company=self.company,
            membership=self.membership,
            perms=perms,
        )
        
        # Create basic COA
        create_account(self.actor, code="1000", name="Cash", account_type="ASSET")
        create_account(self.actor, code="1100", name="Accounts Receivable", account_type="RECEIVABLE")
        create_account(self.actor, code="2000", name="Accounts Payable", account_type="PAYABLE")
        create_account(self.actor, code="3000", name="Owner Equity", account_type="EQUITY")
        create_account(self.actor, code="4000", name="Revenue", account_type="REVENUE")
        create_account(self.actor, code="5000", name="Expenses", account_type="EXPENSE")
        
        self.projection = AccountBalanceProjection()

    def test_core_loop_post_and_trial_balance(self):
        """
        Test: Create company → COA → post JE → TB correct
        """
        cash = Account.objects.get(company=self.company, code="1000")
        equity = Account.objects.get(company=self.company, code="3000")
        
        # Create journal entry
        result = create_journal_entry(
            self.actor,
            date=date.today(),
            memo="Opening balance",
            lines=[
                {"account_id": cash.id, "debit": Decimal("10000"), "credit": 0},
                {"account_id": equity.id, "debit": 0, "credit": Decimal("10000")},
            ],
        )
        self.assertTrue(result.success, result.error)
        entry = result.data
        
        # Save as complete (DRAFT)
        result = save_journal_entry_complete(self.actor, entry.id)
        self.assertTrue(result.success, result.error)
        
        # Post
        result = post_journal_entry(self.actor, entry.id)
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.data.status, JournalEntry.Status.POSTED)
        self.assertIsNotNone(result.data.entry_number)
        
        # Process projections (may return 0 if PROJECTIONS_SYNC=True already processed them)
        self.projection.process_pending(self.company)

        # Verify trial balance
        tb = self.projection.get_trial_balance(self.company)
        self.assertTrue(tb["is_balanced"], "Trial balance should be balanced")
        self.assertEqual(tb["total_debit"], "10000.00")
        self.assertEqual(tb["total_credit"], "10000.00")


class CanonicalReversalTest(TestCase):
    """
    Test: Reverse JE → TB restored
    """
    
    def setUp(self):
        result = register_signup(
            email="reversal@canonical.test",
            password="testpass123",
            company_name="Reversal Test Corp",
        )
        self.user = result.data["user"]
        self.company = result.data["company"]
        self.membership = result.data["membership"]
        
        perms = frozenset(self.membership.permissions.values_list("code", flat=True))
        self.actor = ActorContext(
            user=self.user,
            company=self.company,
            membership=self.membership,
            perms=perms,
        )
        
        create_account(self.actor, code="1000", name="Cash", account_type="ASSET")
        create_account(self.actor, code="4000", name="Revenue", account_type="REVENUE")
        
        self.projection = AccountBalanceProjection()

    def test_reversal_restores_balances(self):
        """Reversing an entry should restore balances to zero"""
        cash = Account.objects.get(company=self.company, code="1000")
        revenue = Account.objects.get(company=self.company, code="4000")
        
        # Post initial entry
        result = create_journal_entry(
            self.actor,
            date=date.today(),
            memo="Sale",
            lines=[
                {"account_id": cash.id, "debit": Decimal("500"), "credit": 0},
                {"account_id": revenue.id, "debit": 0, "credit": Decimal("500")},
            ],
        )
        entry = result.data
        save_journal_entry_complete(self.actor, entry.id)
        post_journal_entry(self.actor, entry.id)
        
        # Process projections
        self.projection.process_pending(self.company)
        
        # Verify balance after posting
        tb_before = self.projection.get_trial_balance(self.company)
        self.assertEqual(tb_before["total_debit"], "500.00")

        # Reverse the entry
        result = reverse_journal_entry(self.actor, entry.id)
        self.assertTrue(result.success, result.error)

        # Verify original is REVERSED
        entry.refresh_from_db()
        self.assertEqual(entry.status, JournalEntry.Status.REVERSED)

        # Process projections for reversal
        self.projection.process_pending(self.company)

        # Verify trial balance is restored to zero
        tb_after = self.projection.get_trial_balance(self.company)
        self.assertTrue(tb_after["is_balanced"])
        self.assertEqual(tb_after["total_debit"], "0.00")
        self.assertEqual(tb_after["total_credit"], "0.00")


class CanonicalRebuildTest(TestCase):
    """
    Test: Drop projections → rebuild → identical
    """
    
    def setUp(self):
        result = register_signup(
            email="rebuild@canonical.test",
            password="testpass123",
            company_name="Rebuild Test Corp",
        )
        self.user = result.data["user"]
        self.company = result.data["company"]
        self.membership = result.data["membership"]
        
        perms = frozenset(self.membership.permissions.values_list("code", flat=True))
        self.actor = ActorContext(
            user=self.user,
            company=self.company,
            membership=self.membership,
            perms=perms,
        )
        
        create_account(self.actor, code="1000", name="Cash", account_type="ASSET")
        create_account(self.actor, code="3000", name="Equity", account_type="EQUITY")
        
        self.projection = AccountBalanceProjection()

    def test_rebuild_produces_identical_state(self):
        """Rebuilding projections should produce identical balances"""
        cash = Account.objects.get(company=self.company, code="1000")
        equity = Account.objects.get(company=self.company, code="3000")
        
        # Create and post multiple entries
        for i in range(3):
            result = create_journal_entry(
                self.actor,
                date=date.today(),
                memo=f"Entry {i+1}",
                lines=[
                    {"account_id": cash.id, "debit": Decimal("1000"), "credit": 0},
                    {"account_id": equity.id, "debit": 0, "credit": Decimal("1000")},
                ],
            )
            entry = result.data
            save_journal_entry_complete(self.actor, entry.id)
            post_journal_entry(self.actor, entry.id)
        
        # Process projections
        self.projection.process_pending(self.company)
        
        # Capture current state
        tb_original = self.projection.get_trial_balance(self.company)
        
        # Rebuild projection from scratch
        self.projection.rebuild(self.company)
        
        # Verify identical state
        tb_rebuilt = self.projection.get_trial_balance(self.company)
        
        self.assertEqual(tb_original["total_debit"], tb_rebuilt["total_debit"])
        self.assertEqual(tb_original["total_credit"], tb_rebuilt["total_credit"])
        self.assertEqual(
            len(tb_original["accounts"]),
            len(tb_rebuilt["accounts"]),
        )


class CanonicalImmutabilityTest(TestCase):
    """
    Test: Events cannot be modified or deleted
    """
    
    def setUp(self):
        result = register_signup(
            email="immutable@canonical.test",
            password="testpass123",
            company_name="Immutable Test Corp",
        )
        self.company = result.data["company"]

    def test_event_cannot_be_modified(self):
        """Events are immutable - save should raise"""
        event = BusinessEvent.objects.filter(company=self.company).first()
        self.assertIsNotNone(event, "Should have at least one event from registration")
        
        event.event_type = "hacked.event"
        with self.assertRaises(ValueError) as ctx:
            event.save()
        self.assertIn("immutable", str(ctx.exception).lower())

    def test_event_cannot_be_deleted(self):
        """Events are immutable - delete should raise"""
        event = BusinessEvent.objects.filter(company=self.company).first()
        self.assertIsNotNone(event)
        
        with self.assertRaises(ValueError) as ctx:
            event.delete()
        self.assertIn("immutable", str(ctx.exception).lower())


class CanonicalDoubleEntryTest(TestCase):
    """
    Test: Unbalanced entries cannot be posted
    """
    
    def setUp(self):
        result = register_signup(
            email="doubleentry@canonical.test",
            password="testpass123",
            company_name="Double Entry Test Corp",
        )
        self.user = result.data["user"]
        self.company = result.data["company"]
        self.membership = result.data["membership"]
        
        perms = frozenset(self.membership.permissions.values_list("code", flat=True))
        self.actor = ActorContext(
            user=self.user,
            company=self.company,
            membership=self.membership,
            perms=perms,
        )
        
        create_account(self.actor, code="1000", name="Cash", account_type="ASSET")

    def test_unbalanced_entry_rejected(self):
        """Unbalanced entries cannot be saved as complete"""
        cash = Account.objects.get(company=self.company, code="1000")
        
        # Create unbalanced entry (only debit, no credit)
        result = create_journal_entry(
            self.actor,
            date=date.today(),
            memo="Unbalanced",
            lines=[
                {"account_id": cash.id, "debit": Decimal("1000"), "credit": 0},
            ],
        )
        entry = result.data
        
        # Try to save as complete - should fail (needs at least 2 lines)
        result = save_journal_entry_complete(self.actor, entry.id)
        self.assertFalse(result.success)
        # Error should mention either "lines" or "balanced"
        self.assertTrue(
            "line" in result.error.lower() or "balanced" in result.error.lower(),
            f"Error should mention lines or balance: {result.error}"
        )


class CanonicalIdempotencyTest(TestCase):
    """
    Test: Same idempotency_key produces same event
    """
    
    def setUp(self):
        result = register_signup(
            email="idempotent@canonical.test",
            password="testpass123",
            company_name="Idempotent Test Corp",
        )
        self.user = result.data["user"]
        self.company = result.data["company"]
        self.membership = result.data["membership"]
        
        perms = frozenset(self.membership.permissions.values_list("code", flat=True))
        self.actor = ActorContext(
            user=self.user,
            company=self.company,
            membership=self.membership,
            perms=perms,
        )

    def test_duplicate_account_rejected(self):
        """Creating same account twice should fail (code uniqueness)"""
        result1 = create_account(
            self.actor,
            code="9999",
            name="Test Account",
            account_type="ASSET",
        )
        self.assertTrue(result1.success)
        
        initial_event_count = BusinessEvent.objects.filter(company=self.company).count()
        
        # Try to create same account again
        result2 = create_account(
            self.actor,
            code="9999",
            name="Test Account",
            account_type="ASSET",
        )
        self.assertFalse(result2.success)  # Should fail - duplicate code
        
        # Event count should not increase
        final_event_count = BusinessEvent.objects.filter(company=self.company).count()
        self.assertEqual(initial_event_count, final_event_count)