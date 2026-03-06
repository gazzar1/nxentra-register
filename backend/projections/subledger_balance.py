# projections/subledger_balance.py
"""
Subledger Balance Projection.

This projection maintains customer and vendor balances (subledgers).
It consumes:
- journal_entry.posted: Apply debits/credits to customer/vendor balances

The projection maintains:
- CustomerBalance: Current balance per customer (AR subledger)
- VendorBalance: Current balance per vendor (AP subledger)

Key Design:
- Journal lines with customer_public_id update CustomerBalance
- Journal lines with vendor_public_id update VendorBalance
- Balances are updated in real-time as journal entries are posted
- Supports aging reports by tracking oldest_open_date
"""

from decimal import Decimal
from typing import List, Dict, Any
import logging

from django.db import transaction

from accounts.models import Company
from accounting.models import Customer, Vendor
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection, projection_registry
from projections.models import CustomerBalance, VendorBalance


logger = logging.getLogger(__name__)


class SubledgerBalanceProjection(BaseProjection):
    """
    Maintains materialized customer and vendor balances from journal entry events.

    Event Flow:
    1. Command posts journal entry with customer/vendor counterparties
    2. journal_entry.posted event is emitted
    3. This projection consumes the event
    4. CustomerBalance/VendorBalance records are updated

    The projection is idempotent: processing the same event twice
    will not double-count amounts (guaranteed by ProjectionAppliedEvent
    in BaseProjection.process_pending).
    """

    @property
    def name(self) -> str:
        return "subledger_balance"

    @property
    def consumes(self) -> List[str]:
        return [
            EventTypes.JOURNAL_ENTRY_POSTED,
            # LEPH chunked journal events
            EventTypes.JOURNAL_LINES_CHUNK_ADDED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        """
        Process a journal entry posted event.

        For each line that has a customer or vendor counterparty:
        1. Get or create the appropriate balance record
        2. Apply debit/credit
        3. Update statistics and last_event
        """
        if event.event_type == EventTypes.JOURNAL_ENTRY_POSTED:
            self._handle_posted(event)
        elif event.event_type == EventTypes.JOURNAL_LINES_CHUNK_ADDED:
            self._handle_chunk(event)
        else:
            logger.warning(f"Unknown event type: {event.event_type}")

    def _handle_posted(self, event: BusinessEvent) -> None:
        """Handle journal_entry.posted event."""
        data = event.get_data()
        entry_date_str = data.get("date")
        lines = data.get("lines", [])

        if not lines:
            return

        # Parse entry date
        from datetime import datetime
        entry_date = None
        if entry_date_str:
            entry_date = datetime.fromisoformat(entry_date_str).date()

        # Process each line
        for line_data in lines:
            self._apply_line(
                company=event.company,
                line_data=line_data,
                entry_date=entry_date,
                event=event,
            )

    def _handle_chunk(self, event: BusinessEvent) -> None:
        """Handle LEPH journal.lines_chunk_added event."""
        data = event.get_data()
        lines = data.get("lines", [])

        if not lines:
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
        Apply a single journal line to CustomerBalance or VendorBalance.

        Only processes lines that have a customer_public_id or vendor_public_id.
        """
        customer_public_id = line_data.get("customer_public_id")
        vendor_public_id = line_data.get("vendor_public_id")

        # Skip lines without counterparties
        if not customer_public_id and not vendor_public_id:
            return

        debit = Decimal(line_data.get("debit", "0"))
        credit = Decimal(line_data.get("credit", "0"))
        is_memo = line_data.get("is_memo_line", False)

        # Skip memo lines for financial balances
        if is_memo:
            return

        # Skip if no actual amount
        if debit == 0 and credit == 0:
            return

        # Process customer balance
        if customer_public_id:
            self._apply_customer_line(
                company=company,
                customer_public_id=customer_public_id,
                debit=debit,
                credit=credit,
                entry_date=entry_date,
                event=event,
            )

        # Process vendor balance
        if vendor_public_id:
            self._apply_vendor_line(
                company=company,
                vendor_public_id=vendor_public_id,
                debit=debit,
                credit=credit,
                entry_date=entry_date,
                event=event,
            )

    def _apply_customer_line(
        self,
        company: Company,
        customer_public_id: str,
        debit: Decimal,
        credit: Decimal,
        entry_date,
        event: BusinessEvent,
    ) -> None:
        """Apply a journal line to CustomerBalance."""
        try:
            customer = Customer.objects.get(
                public_id=customer_public_id,
                company=company,
            )
        except Customer.DoesNotExist:
            logger.error(
                f"Customer {customer_public_id} not found for company {company.id} "
                f"in event {event.id}"
            )
            return

        with transaction.atomic():
            # Get or create balance with lock
            try:
                balance = CustomerBalance.objects.select_for_update().get(
                    company=company,
                    customer=customer,
                )
                created = False
            except CustomerBalance.DoesNotExist:
                balance = CustomerBalance.objects.create(
                    company=company,
                    customer=customer,
                    balance=Decimal("0.00"),
                    debit_total=Decimal("0.00"),
                    credit_total=Decimal("0.00"),
                    transaction_count=0,
                )
                created = True

            # Note: Event-level idempotency is handled by ProjectionAppliedEvent
            # in BaseProjection.process_pending(). No per-entity guard here
            # because a single event can have multiple lines for the same customer.

            # Apply the debit/credit
            if debit > 0:
                balance.apply_debit(debit)
                # Track invoice date (debits to AR are typically invoices)
                if entry_date:
                    if not balance.last_invoice_date or entry_date > balance.last_invoice_date:
                        balance.last_invoice_date = entry_date

            if credit > 0:
                balance.apply_credit(credit)
                # Track payment date (credits to AR are typically payments)
                if entry_date:
                    if not balance.last_payment_date or entry_date > balance.last_payment_date:
                        balance.last_payment_date = entry_date

            # Update statistics
            balance.transaction_count += 1

            # Track oldest open date for aging
            # If balance is positive and we don't have an oldest_open_date, set it
            if balance.balance > 0 and entry_date:
                if not balance.oldest_open_date:
                    balance.oldest_open_date = entry_date

            # If balance is zero or negative, clear oldest_open_date
            if balance.balance <= 0:
                balance.oldest_open_date = None

            balance.last_event = event
            balance.save()

            logger.debug(
                f"Updated customer balance for {customer.code}: "
                f"debit={debit}, credit={credit}, new_balance={balance.balance}"
            )

    def _apply_vendor_line(
        self,
        company: Company,
        vendor_public_id: str,
        debit: Decimal,
        credit: Decimal,
        entry_date,
        event: BusinessEvent,
    ) -> None:
        """Apply a journal line to VendorBalance."""
        try:
            vendor = Vendor.objects.get(
                public_id=vendor_public_id,
                company=company,
            )
        except Vendor.DoesNotExist:
            logger.error(
                f"Vendor {vendor_public_id} not found for company {company.id} "
                f"in event {event.id}"
            )
            return

        with transaction.atomic():
            # Get or create balance with lock
            try:
                balance = VendorBalance.objects.select_for_update().get(
                    company=company,
                    vendor=vendor,
                )
                created = False
            except VendorBalance.DoesNotExist:
                balance = VendorBalance.objects.create(
                    company=company,
                    vendor=vendor,
                    balance=Decimal("0.00"),
                    debit_total=Decimal("0.00"),
                    credit_total=Decimal("0.00"),
                    transaction_count=0,
                )
                created = True

            # Note: Event-level idempotency is handled by ProjectionAppliedEvent
            # in BaseProjection.process_pending(). No per-entity guard here
            # because a single event can have multiple lines for the same vendor.

            # Apply the debit/credit
            if debit > 0:
                balance.apply_debit(debit)
                # Track payment date (debits to AP are typically payments)
                if entry_date:
                    if not balance.last_payment_date or entry_date > balance.last_payment_date:
                        balance.last_payment_date = entry_date

            if credit > 0:
                balance.apply_credit(credit)
                # Track bill date (credits to AP are typically bills)
                if entry_date:
                    if not balance.last_bill_date or entry_date > balance.last_bill_date:
                        balance.last_bill_date = entry_date

            # Update statistics
            balance.transaction_count += 1

            # Track oldest open date for aging
            # If balance is positive and we don't have an oldest_open_date, set it
            if balance.balance > 0 and entry_date:
                if not balance.oldest_open_date:
                    balance.oldest_open_date = entry_date

            # If balance is zero or negative, clear oldest_open_date
            if balance.balance <= 0:
                balance.oldest_open_date = None

            balance.last_event = event
            balance.save()

            logger.debug(
                f"Updated vendor balance for {vendor.code}: "
                f"debit={debit}, credit={credit}, new_balance={balance.balance}"
            )

    def _clear_projected_data(self, company: Company) -> None:
        """Clear all CustomerBalance and VendorBalance records for rebuild."""
        customer_cleared = CustomerBalance.objects.filter(company=company).update(
            balance=Decimal("0.00"),
            debit_total=Decimal("0.00"),
            credit_total=Decimal("0.00"),
            transaction_count=0,
            last_invoice_date=None,
            last_payment_date=None,
            oldest_open_date=None,
            last_event=None,
        )

        vendor_cleared = VendorBalance.objects.filter(company=company).update(
            balance=Decimal("0.00"),
            debit_total=Decimal("0.00"),
            credit_total=Decimal("0.00"),
            transaction_count=0,
            last_bill_date=None,
            last_payment_date=None,
            oldest_open_date=None,
            last_event=None,
        )

        logger.info(
            f"Reset {customer_cleared} CustomerBalance and "
            f"{vendor_cleared} VendorBalance records for {company.name}"
        )

    def get_customer_balance(self, company: Company, customer: Customer) -> Decimal:
        """Get the current balance for a customer."""
        try:
            balance = CustomerBalance.objects.get(company=company, customer=customer)
            return balance.balance
        except CustomerBalance.DoesNotExist:
            return Decimal("0.00")

    def get_vendor_balance(self, company: Company, vendor: Vendor) -> Decimal:
        """Get the current balance for a vendor."""
        try:
            balance = VendorBalance.objects.get(company=company, vendor=vendor)
            return balance.balance
        except VendorBalance.DoesNotExist:
            return Decimal("0.00")

    def get_customer_aging(self, company: Company) -> Dict[str, Any]:
        """
        Generate customer aging report.

        Returns customers grouped by aging buckets:
        - Current (0-30 days)
        - 31-60 days
        - 61-90 days
        - Over 90 days
        """
        from datetime import date, timedelta

        today = date.today()
        buckets = {
            "current": [],  # 0-30 days
            "days_31_60": [],
            "days_61_90": [],
            "over_90": [],
        }
        totals = {
            "current": Decimal("0.00"),
            "days_31_60": Decimal("0.00"),
            "days_61_90": Decimal("0.00"),
            "over_90": Decimal("0.00"),
            "total": Decimal("0.00"),
        }

        balances = CustomerBalance.objects.filter(
            company=company,
            balance__gt=0,  # Only customers who owe us
        ).select_related("customer").order_by("oldest_open_date")

        for bal in balances:
            if not bal.oldest_open_date:
                # No date, put in current
                bucket = "current"
            else:
                days_old = (today - bal.oldest_open_date).days
                if days_old <= 30:
                    bucket = "current"
                elif days_old <= 60:
                    bucket = "days_31_60"
                elif days_old <= 90:
                    bucket = "days_61_90"
                else:
                    bucket = "over_90"

            buckets[bucket].append({
                "customer_code": bal.customer.code,
                "customer_name": bal.customer.name,
                "balance": str(bal.balance),
                "oldest_open_date": bal.oldest_open_date.isoformat() if bal.oldest_open_date else None,
            })
            totals[bucket] += bal.balance
            totals["total"] += bal.balance

        return {
            "as_of_date": today.isoformat(),
            "buckets": buckets,
            "totals": {k: str(v) for k, v in totals.items()},
        }

    def get_vendor_aging(self, company: Company) -> Dict[str, Any]:
        """
        Generate vendor aging report.

        Returns vendors grouped by aging buckets.
        """
        from datetime import date

        today = date.today()
        buckets = {
            "current": [],
            "days_31_60": [],
            "days_61_90": [],
            "over_90": [],
        }
        totals = {
            "current": Decimal("0.00"),
            "days_31_60": Decimal("0.00"),
            "days_61_90": Decimal("0.00"),
            "over_90": Decimal("0.00"),
            "total": Decimal("0.00"),
        }

        balances = VendorBalance.objects.filter(
            company=company,
            balance__gt=0,  # Only vendors we owe
        ).select_related("vendor").order_by("oldest_open_date")

        for bal in balances:
            if not bal.oldest_open_date:
                bucket = "current"
            else:
                days_old = (today - bal.oldest_open_date).days
                if days_old <= 30:
                    bucket = "current"
                elif days_old <= 60:
                    bucket = "days_31_60"
                elif days_old <= 90:
                    bucket = "days_61_90"
                else:
                    bucket = "over_90"

            buckets[bucket].append({
                "vendor_code": bal.vendor.code,
                "vendor_name": bal.vendor.name,
                "balance": str(bal.balance),
                "oldest_open_date": bal.oldest_open_date.isoformat() if bal.oldest_open_date else None,
            })
            totals[bucket] += bal.balance
            totals["total"] += bal.balance

        return {
            "as_of_date": today.isoformat(),
            "buckets": buckets,
            "totals": {k: str(v) for k, v in totals.items()},
        }


# Register the projection
projection_registry.register(SubledgerBalanceProjection())
