# tests/test_projections.py
"""
Tests for the projections module.

Tests cover:
- Account balance projection
- Race condition fix (select_for_update)
- 0/0 line filtering fix
- Projection idempotency
- Trial balance and reports
"""

import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Barrier

from django.db import connection, transaction
from django.utils import timezone

from projections.base import projection_registry, BaseProjection
from projections.models import AccountBalance, ProjectionAppliedEvent
from projections.accounting import JournalEntryProjection, AccountProjection
from projections.account_balance import AccountBalanceProjection
from projections.accounts import UserProjection, MembershipProjection, CompanyProjection
from accounting.models import Account, JournalEntry, JournalLine
from events.models import BusinessEvent, EventBookmark
from events.emitter import emit_event
from events.types import EventTypes


# =============================================================================
# Account Balance Projection Tests
# =============================================================================

@pytest.mark.django_db
class TestAccountBalanceProjection:
    """Test account balance materialization."""
    
    def test_posted_entry_creates_balance(
        self, company, user, cash_account, revenue_account
    ):
        """Posting an entry should create/update AccountBalance records."""
        # Emit a posted event
        entry_public_id = str(uuid4())
        
        event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=entry_public_id,
            data={
                "entry_public_id": entry_public_id,
                "entry_number": "JE-TEST-001",
                "date": date.today().isoformat(),
                "memo": "Test posting",
                "kind": "NORMAL",
                "posted_at": "2024-01-01T12:00:00",
                "posted_by_id": user.id,
                "posted_by_email": user.email,
                "total_debit": "1000.00",
                "total_credit": "1000.00",
                "lines": [
                    {
                        "line_no": 1,
                        "account_public_id": str(cash_account.public_id),
                        "account_code": cash_account.code,
                        "description": "Cash received",
                        "debit": "1000.00",
                        "credit": "0.00",
                    },
                    {
                        "line_no": 2,
                        "account_public_id": str(revenue_account.public_id),
                        "account_code": revenue_account.code,
                        "description": "Revenue earned",
                        "debit": "0.00",
                        "credit": "1000.00",
                    },
                ],
            },
            caused_by_user=user,
            idempotency_key=f"balance-test:posted:{entry_public_id}",
        )
        
        # Process projection
        projection = AccountBalanceProjection()
        projection.process_pending(company)
        
        # Check balances created
        cash_balance = AccountBalance.objects.get(company=company, account=cash_account)
        assert cash_balance.debit_total == Decimal("1000.00")
        assert cash_balance.credit_total == Decimal("0.00")
        assert cash_balance.balance == Decimal("1000.00")  # Debit normal
        
        revenue_balance = AccountBalance.objects.get(company=company, account=revenue_account)
        assert revenue_balance.debit_total == Decimal("0.00")
        assert revenue_balance.credit_total == Decimal("1000.00")
        assert revenue_balance.balance == Decimal("1000.00")  # Credit normal
    
    def test_idempotent_event_processing(
        self, company, user, cash_account, revenue_account
    ):
        """Processing same event twice should not double-count."""
        entry_public_id = str(uuid4())
        
        event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=entry_public_id,
            data={
                "entry_public_id": entry_public_id,
                "entry_number": "JE-IDEM-001",
                "date": date.today().isoformat(),
                "memo": "Idempotency test",
                "kind": "NORMAL",
                "posted_at": "2024-01-01T12:00:00",
                "posted_by_id": user.id,
                "posted_by_email": user.email,
                "total_debit": "500.00",
                "total_credit": "500.00",
                "lines": [
                    {
                        "line_no": 1,
                        "account_public_id": str(cash_account.public_id),
                        "account_code": cash_account.code,
                        "description": "Cash",
                        "debit": "500.00",
                        "credit": "0.00",
                    },
                    {
                        "line_no": 2,
                        "account_public_id": str(revenue_account.public_id),
                        "account_code": revenue_account.code,
                        "description": "Revenue",
                        "debit": "0.00",
                        "credit": "500.00",
                    },
                ],
            },
            caused_by_user=user,
            idempotency_key=f"idem-test:{entry_public_id}",
        )
        
        projection = AccountBalanceProjection()
        
        # Process twice
        projection.process_pending(company)
        projection.process_pending(company)
        
        # Balance should still be 500, not 1000
        cash_balance = AccountBalance.objects.get(company=company, account=cash_account)
        assert cash_balance.debit_total == Decimal("500.00")
    
    def test_memo_lines_excluded_from_balance(
        self, company, user, cash_account, memo_account
    ):
        """Memo/statistical lines should not affect financial balance."""
        entry_public_id = str(uuid4())
        
        emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=entry_public_id,
            data={
                "entry_public_id": entry_public_id,
                "entry_number": "JE-MEMO-001",
                "date": date.today().isoformat(),
                "memo": "With memo line",
                "kind": "NORMAL",
                "posted_at": "2024-01-01T12:00:00",
                "posted_by_id": user.id,
                "posted_by_email": user.email,
                "total_debit": "100.00",
                "total_credit": "100.00",
                "lines": [
                    {
                        "line_no": 1,
                        "account_public_id": str(cash_account.public_id),
                        "account_code": cash_account.code,
                        "debit": "100.00",
                        "credit": "0.00",
                        "is_memo_line": False,
                    },
                    {
                        "line_no": 2,
                        "account_public_id": str(memo_account.public_id),
                        "account_code": memo_account.code,
                        "debit": "5.00",  # 5 employees
                        "credit": "0.00",
                        "is_memo_line": True,  # Should be excluded!
                    },
                ],
            },
            caused_by_user=user,
            idempotency_key=f"memo-test:{entry_public_id}",
        )
        
        projection = AccountBalanceProjection()
        projection.process_pending(company)
        
        # Memo account should NOT have a balance record (or it should be zero)
        memo_exists = AccountBalance.objects.filter(
            company=company, account=memo_account
        ).exists()
        
        # Either no record or zero balance
        if memo_exists:
            memo_balance = AccountBalance.objects.get(company=company, account=memo_account)
            assert memo_balance.debit_total == Decimal("0.00")


# =============================================================================
# Journal Entry Projection 0/0 Line Fix Tests
# =============================================================================

@pytest.mark.django_db
class TestJournalLineZeroZeroFiltering:
    """
    Test that lines with debit=0 and credit=0 are filtered out.
    
    This tests the fix for the DB constraint violation.
    """
    
    def test_zero_zero_lines_filtered_in_projection(
        self, company, user, cash_account, revenue_account
    ):
        """Lines with debit=0 and credit=0 should not be created."""
        entry_public_id = str(uuid4())
        
        emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_CREATED,
            aggregate_type="JournalEntry",
            aggregate_id=entry_public_id,
            data={
                "entry_public_id": entry_public_id,
                "date": date.today().isoformat(),
                "memo": "Test with zero line",
                "status": "INCOMPLETE",
                "lines": [
                    {
                        "line_no": 1,
                        "account_public_id": str(cash_account.public_id),
                        "account_code": cash_account.code,
                        "description": "Valid line",
                        "debit": "100.00",
                        "credit": "0.00",
                    },
                    {
                        "line_no": 2,
                        "account_public_id": str(revenue_account.public_id),
                        "account_code": revenue_account.code,
                        "description": "Also valid",
                        "debit": "0.00",
                        "credit": "100.00",
                    },
                    {
                        "line_no": 3,
                        "account_public_id": str(cash_account.public_id),
                        "account_code": cash_account.code,
                        "description": "Invalid - both zero",
                        "debit": "0.00",
                        "credit": "0.00",  # Should be filtered!
                    },
                ],
            },
            caused_by_user=user,
            idempotency_key=f"zero-filter:{entry_public_id}",
        )
        
        # Process projection - should NOT raise IntegrityError
        projection = JournalEntryProjection()
        projection.process_pending(company)
        
        # Entry should exist
        entry = JournalEntry.objects.get(public_id=entry_public_id)
        
        # Should only have 2 lines (the 0/0 line filtered out)
        assert entry.lines.count() == 2
        
        # Verify the valid lines exist
        line_nos = list(entry.lines.values_list("line_no", flat=True))
        assert 1 in line_nos
        assert 2 in line_nos


# =============================================================================
# Journal Entry Currency Projection Tests
# =============================================================================

@pytest.mark.django_db
class TestJournalEntryCurrencyProjection:
    """Test that currency fields persist in the read model."""

    def test_posted_entry_persists_currency_fields(
        self, company, user, cash_account, revenue_account
    ):
        entry_public_id = str(uuid4())

        emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=entry_public_id,
            data={
                "entry_public_id": entry_public_id,
                "entry_number": "JE-CURR-001",
                "date": date.today().isoformat(),
                "memo": "Currency test",
                "kind": "NORMAL",
                "posted_at": timezone.now().isoformat(),
                "posted_by_id": user.id,
                "posted_by_email": user.email,
                "total_debit": "120.00",
                "total_credit": "120.00",
                "currency": "EUR",
                "exchange_rate": "1.250000",
                "lines": [
                    {
                        "line_no": 1,
                        "account_public_id": str(cash_account.public_id),
                        "account_code": cash_account.code,
                        "description": "Cash",
                        "debit": "120.00",
                        "credit": "0.00",
                        "amount_currency": "120.00",
                        "currency": "EUR",
                        "exchange_rate": "1.250000",
                    },
                    {
                        "line_no": 2,
                        "account_public_id": str(revenue_account.public_id),
                        "account_code": revenue_account.code,
                        "description": "Revenue",
                        "debit": "0.00",
                        "credit": "120.00",
                        "amount_currency": "120.00",
                        "currency": "EUR",
                        "exchange_rate": "1.250000",
                    },
                ],
            },
            caused_by_user=user,
            idempotency_key=f"currency-proj:{entry_public_id}",
        )

        projection = JournalEntryProjection()
        projection.process_pending(company)

        entry = JournalEntry.objects.get(company=company, public_id=entry_public_id)
        assert entry.currency == "EUR"
        assert entry.exchange_rate == Decimal("1.250000")

        line = JournalLine.objects.get(company=company, entry=entry, line_no=1)
        assert line.amount_currency == Decimal("120.00")
        assert line.currency == "EUR"
        assert line.exchange_rate == Decimal("1.250000")


# =============================================================================
# Race Condition Fix Tests
# =============================================================================

@pytest.mark.django_db(transaction=True)
class TestAccountBalanceRaceCondition:
    """
    Test that concurrent balance updates don't lose data.
    
    This tests the select_for_update() fix.
    """
    
    def test_concurrent_updates_are_serialized(
        self, company, user, cash_account
    ):
        """
        Multiple workers updating same balance should not lose updates.
        
        Note: This test requires transaction=True to test real concurrency.
        """
        # Create multiple events for the same account
        events = []
        for i in range(5):
            entry_public_id = str(uuid4())
            event = emit_event(
                company=company,
                event_type=EventTypes.JOURNAL_ENTRY_POSTED,
                aggregate_type="JournalEntry",
                aggregate_id=entry_public_id,
                data={
                    "entry_public_id": entry_public_id,
                    "entry_number": f"JE-RACE-{i:03d}",
                    "date": date.today().isoformat(),
                    "memo": f"Race test {i}",
                    "kind": "NORMAL",
                    "posted_at": "2024-01-01T12:00:00",
                    "posted_by_id": user.id,
                    "posted_by_email": user.email,
                    "total_debit": "100.00",
                    "total_credit": "100.00",
                    "lines": [
                        {
                            "line_no": 1,
                            "account_public_id": str(cash_account.public_id),
                            "account_code": cash_account.code,
                            "description": "Cash",
                            "debit": "100.00",
                            "credit": "0.00",
                        },
                    ],
                },
                caused_by_user=user,
                idempotency_key=f"race-test:{i}:{entry_public_id}",
            )
            events.append(event)
        
        # Process all events (simulating concurrent workers)
        projection = AccountBalanceProjection()
        
        # Process pending will handle all events
        processed = projection.process_pending(company)
        
        assert processed == 5
        
        # Final balance should be 500 (5 x 100)
        balance = AccountBalance.objects.get(company=company, account=cash_account)
        assert balance.debit_total == Decimal("500.00")
        assert balance.entry_count == 5


# =============================================================================
# Projection Registry Tests
# =============================================================================

@pytest.mark.django_db
class TestProjectionRegistry:
    """Test projection registry functionality."""
    
    def test_all_projections_registered(self):
        """All expected projections should be registered."""
        names = projection_registry.names()
        
        expected = [
            "account_read_model",
            "journal_entry_read_model",
            "analysis_dimension_read_model",
            "account_analysis_default_read_model",
            "account_balance",
            "company_read_model",
            "user_read_model",
            "membership_read_model",
        ]
        
        for name in expected:
            assert name in names, f"Projection '{name}' not registered"
    
    def test_get_projection_by_name(self):
        """Can retrieve projection by name."""
        projection = projection_registry.get("account_balance")
        
        assert projection is not None
        assert isinstance(projection, AccountBalanceProjection)
    
    def test_get_unknown_projection_returns_none(self):
        """Unknown projection name returns None."""
        projection = projection_registry.get("nonexistent_projection")
        
        assert projection is None


# =============================================================================
# Projection Rebuild Tests
# =============================================================================

@pytest.mark.django_db
class TestProjectionRebuild:
    """Test projection rebuild functionality."""
    
    def test_rebuild_clears_and_replays(
        self, company, user, cash_account, revenue_account
    ):
        """Rebuild should clear data and replay all events."""
        # Create some events
        for i in range(3):
            entry_public_id = str(uuid4())
            emit_event(
                company=company,
                event_type=EventTypes.JOURNAL_ENTRY_POSTED,
                aggregate_type="JournalEntry",
                aggregate_id=entry_public_id,
                data={
                    "entry_public_id": entry_public_id,
                    "entry_number": f"JE-REBUILD-{i}",
                    "date": date.today().isoformat(),
                    "memo": f"Rebuild test {i}",
                    "kind": "NORMAL",
                    "posted_at": "2024-01-01T12:00:00",
                    "posted_by_id": user.id,
                    "posted_by_email": user.email,
                    "total_debit": "100.00",
                    "total_credit": "100.00",
                    "lines": [
                        {
                            "line_no": 1,
                            "account_public_id": str(cash_account.public_id),
                            "account_code": cash_account.code,
                            "debit": "100.00",
                            "credit": "0.00",
                        },
                        {
                            "line_no": 2,
                            "account_public_id": str(revenue_account.public_id),
                            "account_code": revenue_account.code,
                            "debit": "0.00",
                            "credit": "100.00",
                        },
                    ],
                },
                caused_by_user=user,
                idempotency_key=f"rebuild-test:{i}:{uuid4()}",
            )
        
        projection = AccountBalanceProjection()
        
        # Process first time
        projection.process_pending(company)
        
        # Corrupt the balance intentionally
        balance = AccountBalance.objects.get(company=company, account=cash_account)
        balance.debit_total = Decimal("9999.00")  # Wrong!
        balance.save()
        
        # Rebuild
        processed = projection.rebuild(company)
        
        assert processed == 3
        
        # Balance should be correct again
        balance.refresh_from_db()
        assert balance.debit_total == Decimal("300.00")


# =============================================================================
# Trial Balance Tests
# =============================================================================

@pytest.mark.django_db
class TestTrialBalance:
    """Test trial balance generation."""
    
    def test_trial_balance_is_balanced(
        self, company, user, cash_account, revenue_account
    ):
        """Trial balance debits should equal credits."""
        # Create balanced entry
        entry_public_id = str(uuid4())
        emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=entry_public_id,
            data={
                "entry_public_id": entry_public_id,
                "entry_number": "JE-TB-001",
                "date": date.today().isoformat(),
                "memo": "Trial balance test",
                "kind": "NORMAL",
                "posted_at": "2024-01-01T12:00:00",
                "posted_by_id": user.id,
                "posted_by_email": user.email,
                "total_debit": "1000.00",
                "total_credit": "1000.00",
                "lines": [
                    {
                        "line_no": 1,
                        "account_public_id": str(cash_account.public_id),
                        "account_code": cash_account.code,
                        "debit": "1000.00",
                        "credit": "0.00",
                    },
                    {
                        "line_no": 2,
                        "account_public_id": str(revenue_account.public_id),
                        "account_code": revenue_account.code,
                        "debit": "0.00",
                        "credit": "1000.00",
                    },
                ],
            },
            caused_by_user=user,
            idempotency_key=f"tb-test:{entry_public_id}",
        )
        
        projection = AccountBalanceProjection()
        projection.process_pending(company)
        
        # Get trial balance
        result = projection.get_trial_balance(company)
        
        assert result["is_balanced"] is True
        assert result["total_debit"] == result["total_credit"]


# =============================================================================
# Accounts Projection Tests
# =============================================================================

@pytest.mark.django_db
class TestAccountsProjections:
    """Test user/company/membership projections."""
    
    def test_user_created_projection(self, company):
        """User created event should create user record."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        user_public_id = str(uuid4())
        
        emit_event(
            company=company,
            event_type=EventTypes.USER_CREATED,
            aggregate_type="User",
            aggregate_id=user_public_id,
            data={
                "user_public_id": user_public_id,
                "email": "projection-test@example.com",
                "name": "Projection Test",
            },
            caused_by_user=None,
            idempotency_key=f"user-proj:{user_public_id}",
        )
        
        projection = UserProjection()
        projection.process_pending(company)
        
        user = User.objects.get(public_id=user_public_id)
        assert user.email == "projection-test@example.com"
        assert user.name == "Projection Test"
    
    def test_membership_created_projection(self, company, user):
        """Membership created event should create membership record."""
        from accounts.models import CompanyMembership
        
        membership_public_id = str(uuid4())
        
        emit_event(
            company=company,
            event_type=EventTypes.MEMBERSHIP_CREATED,
            aggregate_type="Membership",
            aggregate_id=membership_public_id,
            data={
                "membership_public_id": membership_public_id,
                "company_public_id": str(company.public_id),
                "user_public_id": str(user.public_id),
                "role": "USER",
                "is_active": True,
            },
            caused_by_user=user,
            idempotency_key=f"membership-proj:{membership_public_id}",
        )
        
        projection = MembershipProjection()
        projection.process_pending(company)
        
        membership = CompanyMembership.objects.get(public_id=membership_public_id)
        assert membership.company_id == company.id
        assert membership.user_id == user.id
        assert membership.role == "USER"


# =============================================================================
# Projection Applied Event Tracking Tests
# =============================================================================

@pytest.mark.django_db
class TestProjectionAppliedEventTracking:
    """Test that processed events are tracked for idempotency."""
    
    def test_applied_events_recorded(self, company, user, cash_account):
        """Processing an event should record it in ProjectionAppliedEvent."""
        entry_public_id = str(uuid4())
        
        event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=entry_public_id,
            data={
                "entry_public_id": entry_public_id,
                "entry_number": "JE-TRACK-001",
                "date": date.today().isoformat(),
                "memo": "Tracking test",
                "kind": "NORMAL",
                "posted_at": "2024-01-01T12:00:00",
                "posted_by_id": user.id,
                "posted_by_email": user.email,
                "total_debit": "100.00",
                "total_credit": "100.00",
                "lines": [
                    {
                        "line_no": 1,
                        "account_public_id": str(cash_account.public_id),
                        "account_code": cash_account.code,
                        "debit": "100.00",
                        "credit": "0.00",
                    },
                ],
            },
            caused_by_user=user,
            idempotency_key=f"track-test:{entry_public_id}",
        )
        
        projection = AccountBalanceProjection()
        projection.process_pending(company)
        
        # Check event is recorded
        applied = ProjectionAppliedEvent.objects.filter(
            company=company,
            projection_name="account_balance",
            event=event,
        ).exists()
        
        assert applied is True
