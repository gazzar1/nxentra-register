# projections/account_balance.py
"""
Account Balance Projection.

This is the core projection that maintains account balances.
It consumes:
- journal_entry.posted: Apply debits and credits to accounts
- journal_entry.reversed: The reversal entry is also posted, so we just process it

The projection maintains:
- AccountBalance: Current balance per account
- (Future) PeriodAccountBalance: Balance per fiscal period

This projection is the single source of truth for "what is the balance?"
"""

from decimal import Decimal
from typing import List, Dict, Any
import logging

from django.db import transaction

from accounts.models import Company
from accounting.models import Account
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection, projection_registry
from projections.models import AccountBalance


logger = logging.getLogger(__name__)


class AccountBalanceProjection(BaseProjection):
    """
    Maintains materialized account balances from journal entry events.
    
    Event Flow:
    1. Command posts journal entry
    2. journal_entry.posted event is emitted
    3. This projection consumes the event
    4. AccountBalance records are updated
    
    The projection is idempotent: processing the same event twice
    will not double-count amounts (we track last_event per account).
    """
    
    @property
    def name(self) -> str:
        return "account_balance"
    
    @property
    def consumes(self) -> List[str]:
        return [
            EventTypes.JOURNAL_ENTRY_POSTED,
            # Note: REVERSED entries emit a new POSTED event for the reversal
            # so we don't need to handle REVERSED separately
        ]
    
    def handle(self, event: BusinessEvent) -> None:
        """
        Process a journal entry posted event.
        
        For each line in the entry:
        1. Get or create AccountBalance for the account
        2. Apply debit/credit based on the line
        3. Update statistics and last_event
        """
        if event.event_type == EventTypes.JOURNAL_ENTRY_POSTED:
            self._handle_posted(event)
        else:
            logger.warning(f"Unknown event type: {event.event_type}")
    
    def _handle_posted(self, event: BusinessEvent) -> None:
        """Handle journal_entry.posted event."""
        data = event.data
        entry_date_str = data.get("date")
        lines = data.get("lines", [])
        
        if not lines:
            logger.warning(f"Posted entry {data.get('entry_public_id')} has no lines")
            return
        
        # Parse entry date
        from datetime import datetime
        if entry_date_str:
            entry_date = datetime.fromisoformat(entry_date_str).date()
        else:
            entry_date = None
        
        # Process each line
        for line_data in lines:
            self._apply_line(
                company=event.company,
                line_data=line_data,
                entry_date=entry_date,
                event=event,
            )
    
    def _apply_line(
    self,
    company: Company,
    line_data: Dict[str, Any],
    entry_date,
    event: BusinessEvent,
    ) -> None:
        """
        Apply a single journal line to AccountBalance.
        
        This method is now protected against race conditions using
        select_for_update() to lock the balance row during updates.
        
        Args:
            company: The company
            line_data: Line data from the event
            entry_date: Date of the journal entry
            event: The source event
        """
        account_public_id = line_data.get("account_public_id")
        debit = Decimal(line_data.get("debit", "0"))
        credit = Decimal(line_data.get("credit", "0"))
        is_memo = line_data.get("is_memo_line", False)
        
        # Validation: must have account
        if not account_public_id:
            logger.warning(f"Line missing account_public_id in event {event.id}")
            return
        
        # Skip memo lines for financial balances
        if is_memo:
            return
        
        # Skip if no actual amount
        if debit == 0 and credit == 0:
            return
        
        # Get the account
        try:
            account = Account.objects.get(public_id=account_public_id, company=company)
        except Account.DoesNotExist:
            logger.error(
                f"Account {account_public_id} not found for company {company.id} "
                f"in event {event.id}"
            )
            return
        
        # ═══════════════════════════════════════════════════════════════════════
        # CRITICAL: Use transaction + select_for_update to prevent race conditions
        # ═══════════════════════════════════════════════════════════════════════
        with transaction.atomic():
            # Try to get existing balance with a lock
            try:
                balance = AccountBalance.objects.select_for_update().get(
                    company=company,
                    account=account,
                )
                created = False
            except AccountBalance.DoesNotExist:
                # Create new balance (still inside transaction, so safe)
                balance = AccountBalance.objects.create(
                    company=company,
                    account=account,
                    balance=Decimal("0.00"),
                    debit_total=Decimal("0.00"),
                    credit_total=Decimal("0.00"),
                    entry_count=0,
                )
                created = True
            
            # ═══════════════════════════════════════════════════════════════════
            # Idempotency guard: don't apply the same event twice
            # ═══════════════════════════════════════════════════════════════════
            if balance.last_event_id == event.id:
                logger.debug(f"Event {event.id} already applied to account {account.code}")
                return
            
            # ═══════════════════════════════════════════════════════════════════
            # Apply the debit/credit
            # ═══════════════════════════════════════════════════════════════════
            if debit > 0:
                balance.apply_debit(debit)
            if credit > 0:
                balance.apply_credit(credit)
            
            # Update statistics
            balance.entry_count += 1
            
            if entry_date:
                if not balance.last_entry_date or entry_date > balance.last_entry_date:
                    balance.last_entry_date = entry_date
            
            # Track which event last updated this balance
            balance.last_event = event
            
            # Save (still inside transaction, lock released on commit)
            balance.save()
            
            logger.debug(
                f"Updated balance for {account.code}: "
                f"debit={debit}, credit={credit}, new_balance={balance.balance}"
            )
        
    def _clear_projected_data(self, company: Company) -> None:
        """Clear all AccountBalance records for rebuild."""
        cleared = AccountBalance.objects.filter(company=company).update(
            balance=Decimal("0.00"),
            debit_total=Decimal("0.00"),
            credit_total=Decimal("0.00"),
            entry_count=0,
            last_entry_date=None,
            last_event=None,
        )
        logger.info(f"Reset {cleared} AccountBalance records for {company.name}")
    
    def get_balance(self, company: Company, account: Account) -> Decimal:
        """
        Get the current balance for an account.
        
        This is the method that should be used instead of
        Account.get_balance() to ensure consistency.
        """
        try:
            balance = AccountBalance.objects.get(company=company, account=account)
            return balance.balance
        except AccountBalance.DoesNotExist:
            return Decimal("0.00")
    
    def get_trial_balance(self, company: Company) -> Dict[str, Any]:
        """
        Generate trial balance from projected balances.
        
        Returns:
            {
                "accounts": [
                    {"code": "1000", "name": "Cash", "debit": 1000, "credit": 0},
                    ...
                ],
                "total_debit": 10000,
                "total_credit": 10000,
                "is_balanced": True,
            }
        """
        balances = AccountBalance.objects.filter(
            company=company,
        ).select_related("account").order_by("account__code")
        
        accounts = []
        total_debit = Decimal("0.00")
        total_credit = Decimal("0.00")
        
        for bal in balances:
            account = bal.account
            
            # For trial balance, show debit or credit based on balance sign
            # and normal balance direction
            if account.normal_balance == Account.NormalBalance.DEBIT:
                if bal.balance >= 0:
                    debit = bal.balance
                    credit = Decimal("0.00")
                else:
                    debit = Decimal("0.00")
                    credit = abs(bal.balance)
            else:  # CREDIT normal
                if bal.balance >= 0:
                    debit = Decimal("0.00")
                    credit = bal.balance
                else:
                    debit = abs(bal.balance)
                    credit = Decimal("0.00")
            
            accounts.append({
                "code": account.code,
                "name": account.name,
                "type": account.account_type,
                "debit": debit,
                "credit": credit,
            })
            
            total_debit += debit
            total_credit += credit
        
        return {
            "accounts": accounts,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "is_balanced": total_debit == total_credit,
        }
    
    def verify_all_balances(self, company: Company) -> Dict[str, Any]:
        """
        Verify all projected balances match journal line totals.
        
        Returns:
            {
                "total_accounts": 10,
                "verified": 10,
                "mismatches": [],
            }
        """
        from accounting.models import JournalEntry, JournalLine
        from django.db.models import Sum
        
        balances = AccountBalance.objects.filter(company=company)
        
        mismatches = []
        verified = 0
        
        for bal in balances:
            # Calculate expected from journal lines
            totals = JournalLine.objects.filter(
                account=bal.account,
                entry__status=JournalEntry.Status.POSTED,
            ).aggregate(
                debit_sum=Sum("debit"),
                credit_sum=Sum("credit"),
            )
            
            expected_debit = totals["debit_sum"] or Decimal("0.00")
            expected_credit = totals["credit_sum"] or Decimal("0.00")
            
            if bal.debit_total != expected_debit or bal.credit_total != expected_credit:
                mismatches.append({
                    "account_code": bal.account.code,
                    "projected_debit": str(bal.debit_total),
                    "projected_credit": str(bal.credit_total),
                    "expected_debit": str(expected_debit),
                    "expected_credit": str(expected_credit),
                })
            else:
                verified += 1
        
        return {
            "total_accounts": balances.count(),
            "verified": verified,
            "mismatches": mismatches,
        }


# Register the projection
projection_registry.register(AccountBalanceProjection())
