# projections/period_balance.py
"""
Period Account Balance Projection.

Populates the PeriodAccountBalance table by consuming journal_entry.posted events
and distributing debit/credit amounts to the appropriate fiscal period.

This enables:
- Period-over-period comparisons (this month vs last month)
- Year-end closing calculations (net income = sum of revenue - expenses)
- Opening balance carry-forward to the next fiscal year
"""

import logging
from decimal import Decimal
from typing import Any

from django.db import transaction

from accounting.models import Account
from accounts.models import Company
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection, projection_registry
from projections.models import FiscalPeriod, PeriodAccountBalance

logger = logging.getLogger(__name__)


def _determine_period(company: Company, entry_date, explicit_period: int = None):
    """
    Determine which fiscal period a journal entry belongs to.

    If explicit_period is provided (e.g., period=13 for adjustment entries),
    use that. Otherwise, determine from the entry date.

    For Period 13: first determine the fiscal year from the entry date,
    then find Period 13 within that specific fiscal year. Only falls back
    to "most recent" if no date context is available.

    Returns:
        (fiscal_year, period_number) or (None, None) if no matching period
    """
    if explicit_period == 13:
        # First try to resolve fiscal year from the entry date
        if entry_date:
            date_fp = FiscalPeriod.objects.filter(
                company=company,
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date__lte=entry_date,
                end_date__gte=entry_date,
            ).first()
            if date_fp:
                # Found the fiscal year from date context - use that year's P13
                p13 = FiscalPeriod.objects.filter(
                    company=company,
                    fiscal_year=date_fp.fiscal_year,
                    period=13,
                ).first()
                if p13:
                    return p13.fiscal_year, 13

        # Fallback: no date context, use most recent P13
        fp = (
            FiscalPeriod.objects.filter(
                company=company,
                period=13,
            )
            .order_by("-fiscal_year")
            .first()
        )
        if fp:
            return fp.fiscal_year, 13
        return None, None

    if entry_date:
        # Find the normal period that contains this date
        fp = FiscalPeriod.objects.filter(
            company=company,
            period_type=FiscalPeriod.PeriodType.NORMAL,
            start_date__lte=entry_date,
            end_date__gte=entry_date,
        ).first()
        if fp:
            return fp.fiscal_year, fp.period

    return None, None


class PeriodAccountBalanceProjection(BaseProjection):
    """
    Maintains period-level account balances from journal entry events.

    For each posted journal entry, this projection:
    1. Determines which fiscal period the entry belongs to
    2. Updates the PeriodAccountBalance for each line's account in that period
    3. Recalculates closing balance (opening + period movements)
    """

    @property
    def name(self) -> str:
        return "period_account_balance"

    @property
    def consumes(self):
        return [
            EventTypes.JOURNAL_ENTRY_POSTED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        data = event.get_data()
        entry_date_str = data.get("date")
        explicit_period = data.get("period")
        lines = data.get("lines", [])

        if not lines:
            return

        from datetime import datetime

        entry_date = None
        if entry_date_str:
            entry_date = datetime.fromisoformat(entry_date_str).date()

        # Determine fiscal year and period
        fiscal_year, period_num = _determine_period(event.company, entry_date, explicit_period)
        if fiscal_year is None or period_num is None:
            logger.warning(
                f"Could not determine period for entry date={entry_date_str}, "
                f"explicit_period={explicit_period}, event={event.id}"
            )
            return

        # Process each line
        for line_data in lines:
            self._apply_line(
                company=event.company,
                line_data=line_data,
                fiscal_year=fiscal_year,
                period=period_num,
                event=event,
            )

    def _apply_line(
        self,
        company: Company,
        line_data: dict[str, Any],
        fiscal_year: int,
        period: int,
        event: BusinessEvent,
    ) -> None:
        account_public_id = line_data.get("account_public_id")
        debit = Decimal(line_data.get("debit", "0"))
        credit = Decimal(line_data.get("credit", "0"))
        is_memo = line_data.get("is_memo_line", False)

        if not account_public_id or is_memo:
            return
        if debit == 0 and credit == 0:
            return

        try:
            account = Account.objects.get(public_id=account_public_id, company=company)
        except Account.DoesNotExist:
            logger.error(f"Account {account_public_id} not found in event {event.id}")
            return

        with transaction.atomic():
            try:
                pab = PeriodAccountBalance.objects.select_for_update().get(
                    company=company,
                    account=account,
                    fiscal_year=fiscal_year,
                    period=period,
                )
            except PeriodAccountBalance.DoesNotExist:
                # Compute opening balance:
                # For period 1: opening = 0 for P&L accounts, carry-forward for BS accounts
                # For period N: opening = closing balance of period N-1
                opening = Decimal("0.00")
                if period > 1:
                    prev_period = period - 1
                    prev_pab = PeriodAccountBalance.objects.filter(
                        company=company,
                        account=account,
                        fiscal_year=fiscal_year,
                        period=prev_period,
                    ).first()
                    if prev_pab:
                        opening = prev_pab.closing_balance

                pab = PeriodAccountBalance.objects.create(
                    company=company,
                    account=account,
                    fiscal_year=fiscal_year,
                    period=period,
                    opening_balance=opening,
                    period_debit=Decimal("0.00"),
                    period_credit=Decimal("0.00"),
                    closing_balance=opening,
                )

            # Note: Event-level idempotency is handled by ProjectionAppliedEvent
            # in BaseProjection.process_pending(). No per-account guard here
            # because a single event can have multiple lines for the same account.

            # Apply movements
            if debit > 0:
                pab.period_debit += debit
            if credit > 0:
                pab.period_credit += credit

            pab.recalculate_closing()
            pab.last_event = event
            pab.save()

    def _clear_projected_data(self, company: Company) -> None:
        cleared = PeriodAccountBalance.objects.filter(company=company).delete()
        logger.info(f"Cleared PeriodAccountBalance records for {company.name}: {cleared}")


projection_registry.register(PeriodAccountBalanceProjection())
