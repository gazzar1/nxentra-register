# projections/dimension_balance.py
"""
DimensionBalance projection — maintains pre-aggregated balances
per dimension value × account.

Consumes JOURNAL_ENTRY_POSTED events. For each journal line that has
JournalLineAnalysis records, increments the corresponding
DimensionBalance row (debit_total, credit_total, entry_count).

This allows the Dimension Analysis report to read aggregated data
directly from DimensionBalance instead of scanning JournalLineAnalysis
on every request.
"""

import logging

from django.db.models import F

from accounting.models import (
    JournalEntry,
    JournalLine,
    JournalLineAnalysis,
)
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection, projection_registry
from projections.models import DimensionBalance

logger = logging.getLogger(__name__)

PROJECTION_NAME = "dimension_balance"


class DimensionBalanceProjection(BaseProjection):
    """
    Maintains running debit/credit totals per dimension value × account.

    For each JOURNAL_ENTRY_POSTED event:
    1. Look up the journal lines
    2. For each line, find its JournalLineAnalysis records
    3. Upsert DimensionBalance (dimension_value, account) with the amounts
    """

    @property
    def name(self) -> str:
        return PROJECTION_NAME

    @property
    def consumes(self) -> list[str]:
        return [EventTypes.JOURNAL_ENTRY_POSTED]

    def handle(self, event: BusinessEvent) -> None:
        company = event.company
        data = event.get_data()

        entry_public_id = data.get("entry_public_id")
        if not entry_public_id:
            return

        try:
            entry = JournalEntry.objects.get(
                company=company, public_id=entry_public_id,
            )
        except JournalEntry.DoesNotExist:
            return

        # Get all lines with their analysis records
        lines = JournalLine.objects.filter(
            entry=entry, company=company,
        ).select_related("account")

        for line in lines:
            analysis_records = JournalLineAnalysis.objects.filter(
                journal_line=line, company=company,
            ).select_related("dimension", "dimension_value")

            for analysis in analysis_records:
                # Upsert DimensionBalance
                bal, created = DimensionBalance.objects.get_or_create(
                    company=company,
                    dimension=analysis.dimension,
                    dimension_value=analysis.dimension_value,
                    account=line.account,
                    defaults={
                        "debit_total": line.debit,
                        "credit_total": line.credit,
                        "entry_count": 1,
                    },
                )
                if not created:
                    # Increment existing
                    DimensionBalance.objects.filter(pk=bal.pk).update(
                        debit_total=F("debit_total") + line.debit,
                        credit_total=F("credit_total") + line.credit,
                        entry_count=F("entry_count") + 1,
                    )

    def _clear_projected_data(self, company) -> None:
        DimensionBalance.objects.filter(company=company).delete()


projection_registry.register(DimensionBalanceProjection())
