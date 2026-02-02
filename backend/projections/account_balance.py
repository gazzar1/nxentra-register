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

            # LEPH chunked journal events (for large batch imports)
            EventTypes.JOURNAL_LINES_CHUNK_ADDED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        """
        Process a journal entry posted event.

        For each line in the entry:
        1. Get or create AccountBalance for the account
        2. Apply debit/credit based on the line
        3. Update statistics and last_event

        Also handles LEPH chunked events (JOURNAL_LINES_CHUNK_ADDED).
        """
        if event.event_type == EventTypes.JOURNAL_ENTRY_POSTED:
            self._handle_posted(event)
        elif event.event_type == EventTypes.JOURNAL_LINES_CHUNK_ADDED:
            self._handle_chunk(event)
        else:
            logger.warning(f"Unknown event type: {event.event_type}")
    
    def _handle_posted(self, event: BusinessEvent) -> None:
        """Handle journal_entry.posted event."""
        # Use get_data() for LEPH compatibility (handles inline, external, and chunked payloads)
        data = event.get_data()
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

    def _handle_chunk(self, event: BusinessEvent) -> None:
        """
        Handle LEPH journal.lines_chunk_added event.

        This processes a chunk of journal lines from a large journal entry.
        Each chunk is processed independently, and the projection accumulates
        balances across all chunks.

        For chunk events, we get the entry date from the parent JOURNAL_CREATED
        event via the caused_by_event relationship.
        """
        # Use get_data() for LEPH compatibility
        data = event.get_data()
        lines = data.get("lines", [])

        if not lines:
            logger.debug(f"Chunk event {event.id} has no lines")
            return

        # Get entry date from parent event if available
        entry_date = None
        if event.caused_by_event:
            parent_data = event.caused_by_event.get_data()
            entry_date_str = parent_data.get("date")
            if entry_date_str:
                from datetime import datetime
                entry_date = datetime.fromisoformat(entry_date_str).date()

        # Process each line in the chunk
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
        if account.company_id != company.id:
            raise RuntimeError(
                f"Account/company mismatch for account {account_public_id}: "
                f"account.company_id={account.company_id} company.id={company.id}"
            )
        
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
                "as_of_date": "2026-01-26",
                "accounts": [
                    {"code": "1000", "name": "Cash", "name_ar": "...", "debit": "1000.00", "credit": "0.00", "balance": "1000.00", "normal_balance": "DEBIT", "account_type": "ASSET"},
                    ...
                ],
                "total_debit": "10000.00",
                "total_credit": "10000.00",
                "is_balanced": True,
            }
        """
        from datetime import date

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
                "name_ar": account.name_ar or account.name,
                "account_type": account.account_type,
                "debit": str(debit),
                "credit": str(credit),
                "balance": str(bal.balance),
                "normal_balance": account.normal_balance,
            })

            total_debit += debit
            total_credit += credit

        return {
            "as_of_date": date.today().isoformat(),
            "accounts": accounts,
            "total_debit": str(total_debit),
            "total_credit": str(total_credit),
            "is_balanced": total_debit == total_credit,
        }
    
    def verify_all_balances(self, company: Company) -> Dict[str, Any]:
        """
        Verify all projected balances by replaying events.

        Events are the source of truth. This method replays all
        journal_entry.posted events to compute expected totals per account,
        then compares against the current projection state.

        Returns:
            {
                "total_accounts": 10,
                "verified": 10,
                "mismatches": [],
                "events_processed": 50,
            }
        """
        from events.models import BusinessEvent
        from events.types import EventTypes

        # Build expected totals by replaying events
        expected_totals: Dict[str, Dict[str, Decimal]] = {}
        events_processed = 0

        events = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        ).order_by("company_sequence")

        for event in events:
            # Use get_data() for LEPH compatibility
            data = event.get_data()
            lines = data.get("lines", [])
            for line_data in lines:
                account_public_id = line_data.get("account_public_id")
                if not account_public_id:
                    continue
                if line_data.get("is_memo_line", False):
                    continue

                debit = Decimal(line_data.get("debit", "0"))
                credit = Decimal(line_data.get("credit", "0"))

                if debit == 0 and credit == 0:
                    continue

                if account_public_id not in expected_totals:
                    expected_totals[account_public_id] = {
                        "debit": Decimal("0.00"),
                        "credit": Decimal("0.00"),
                    }

                expected_totals[account_public_id]["debit"] += debit
                expected_totals[account_public_id]["credit"] += credit
                events_processed += 1

        # Compare against projected balances
        balances = AccountBalance.objects.filter(company=company).select_related("account")

        mismatches = []
        verified = 0

        for bal in balances:
            account_id = str(bal.account.public_id)
            expected = expected_totals.get(account_id, {
                "debit": Decimal("0.00"),
                "credit": Decimal("0.00"),
            })

            if (bal.debit_total != expected["debit"] or
                    bal.credit_total != expected["credit"]):
                mismatches.append({
                    "account_code": bal.account.code,
                    "account_public_id": account_id,
                    "projected_debit": str(bal.debit_total),
                    "projected_credit": str(bal.credit_total),
                    "expected_debit": str(expected["debit"]),
                    "expected_credit": str(expected["credit"]),
                })
            else:
                verified += 1

        # Check for accounts in events but missing from projections
        projected_ids = {str(bal.account.public_id) for bal in balances}
        for account_id, totals in expected_totals.items():
            if account_id not in projected_ids:
                mismatches.append({
                    "account_code": "(missing projection)",
                    "account_public_id": account_id,
                    "projected_debit": "0.00",
                    "projected_credit": "0.00",
                    "expected_debit": str(totals["debit"]),
                    "expected_credit": str(totals["credit"]),
                })

        return {
            "total_accounts": balances.count(),
            "verified": verified,
            "mismatches": mismatches,
            "events_processed": events_processed,
        }


# Register the projection
projection_registry.register(AccountBalanceProjection())
