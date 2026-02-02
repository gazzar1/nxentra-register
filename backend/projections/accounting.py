# projections/accounting.py
"""
Accounting projections (read models).

This module contains projections that maintain the read models for
accounting entities. Projections are the ONLY code allowed to write
to the accounting models (Account, JournalEntry, JournalLine, etc.).

All writes use _projection_write=True to bypass the read-model guard.
"""

import logging
from decimal import Decimal
from datetime import datetime, date

from django.utils import timezone

from events.types import EventTypes
from events.models import BusinessEvent
from projections.base import BaseProjection, projection_registry
from accounting.models import (
    Account,
    JournalEntry,
    JournalLine,
    AnalysisDimension,
    AnalysisDimensionValue,
    JournalLineAnalysis,
    AccountAnalysisDefault,
)


logger = logging.getLogger(__name__)


def _parse_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    return value


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


class AccountProjection(BaseProjection):
    @property
    def name(self) -> str:
        return "account_read_model"

    @property
    def consumes(self):
        return [
            EventTypes.ACCOUNT_CREATED,
            EventTypes.ACCOUNT_UPDATED,
            EventTypes.ACCOUNT_DELETED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        data = event.data
        if event.event_type == EventTypes.ACCOUNT_CREATED:
            parent = None
            parent_public_id = data.get("parent_public_id")
            if parent_public_id:
                parent = Account.objects.filter(
                    company=event.company,
                    public_id=parent_public_id,
                ).first()

            Account.objects.projection().update_or_create(
                company=event.company,
                public_id=data["account_public_id"],
                defaults={
                    "code": data["code"],
                    "name": data["name"],
                    "name_ar": data.get("name_ar", ""),
                    "account_type": data["account_type"],
                    "normal_balance": data.get("normal_balance", Account.NormalBalance.DEBIT),
                    "parent": parent,
                    "is_header": data.get("is_header", False),
                    "description": data.get("description", ""),
                    "description_ar": data.get("description_ar", ""),
                    "unit_of_measure": data.get("unit_of_measure", ""),
                },
            )
            return

        if event.event_type == EventTypes.ACCOUNT_UPDATED:
            account = Account.objects.filter(
                company=event.company,
                public_id=data["account_public_id"],
            ).first()
            if not account:
                logger.warning("Account not found for update: %s", data["account_public_id"])
                return

            for field, change in data.get("changes", {}).items():
                setattr(account, field, change.get("new"))
            account.save(_projection_write=True)
            return

        if event.event_type == EventTypes.ACCOUNT_DELETED:
            Account.objects.filter(
                company=event.company,
                public_id=data["account_public_id"],
            ).update(status=Account.Status.INACTIVE)
            return

        logger.warning("Unhandled event type for AccountProjection: %s", event.event_type)


class AnalysisDimensionProjection(BaseProjection):
    @property
    def name(self) -> str:
        return "analysis_dimension_read_model"

    @property
    def consumes(self):
        return [
            EventTypes.ANALYSIS_DIMENSION_CREATED,
            EventTypes.ANALYSIS_DIMENSION_UPDATED,
            EventTypes.ANALYSIS_DIMENSION_DELETED,
            EventTypes.ANALYSIS_DIMENSION_VALUE_CREATED,
            EventTypes.ANALYSIS_DIMENSION_VALUE_UPDATED,
            EventTypes.ANALYSIS_DIMENSION_VALUE_DELETED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        data = event.data
        if event.event_type == EventTypes.ANALYSIS_DIMENSION_CREATED:
            AnalysisDimension.objects.projection().update_or_create(
                company=event.company,
                public_id=data["dimension_public_id"],
                defaults={
                    "code": data["code"],
                    "name": data["name"],
                    "name_ar": data.get("name_ar", ""),
                    "description": data.get("description", ""),
                    "description_ar": data.get("description_ar", ""),
                    "is_required_on_posting": data.get("is_required_on_posting", False),
                    "applies_to_account_types": data.get("applies_to_account_types", []),
                    "display_order": data.get("display_order", 0),
                },
            )
            return

        if event.event_type == EventTypes.ANALYSIS_DIMENSION_UPDATED:
            dimension = AnalysisDimension.objects.filter(
                company=event.company,
                public_id=data["dimension_public_id"],
            ).first()
            if not dimension:
                logger.warning("Dimension not found for update: %s", data["dimension_public_id"])
                return
            for field, change in data.get("changes", {}).items():
                setattr(dimension, field, change.get("new"))
            dimension.save(_projection_write=True)
            return

        if event.event_type == EventTypes.ANALYSIS_DIMENSION_DELETED:
            AnalysisDimension.objects.filter(
                company=event.company,
                public_id=data["dimension_public_id"],
            ).delete()
            return

        if event.event_type == EventTypes.ANALYSIS_DIMENSION_VALUE_CREATED:
            dimension = AnalysisDimension.objects.filter(
                company=event.company,
                public_id=data["dimension_public_id"],
            ).first()
            if not dimension:
                logger.warning("Dimension not found for value create: %s", data["dimension_public_id"])
                return

            parent = None
            parent_public_id = data.get("parent_public_id")
            if parent_public_id:
                parent = AnalysisDimensionValue.objects.filter(
                    dimension=dimension,
                    company=event.company,
                    public_id=parent_public_id,
                ).first()

            AnalysisDimensionValue.objects.projection().update_or_create(
                company=event.company,
                dimension=dimension,
                public_id=data["value_public_id"],
                defaults={
                    "code": data["code"],
                    "name": data["name"],
                    "name_ar": data.get("name_ar", ""),
                    "description": data.get("description", ""),
                    "description_ar": data.get("description_ar", ""),
                    "parent": parent,
                },
            )
            return

        if event.event_type == EventTypes.ANALYSIS_DIMENSION_VALUE_UPDATED:
            dimension = AnalysisDimension.objects.filter(
                company=event.company,
                public_id=data["dimension_public_id"],
            ).first()
            if not dimension:
                logger.warning("Dimension not found for value update: %s", data["dimension_public_id"])
                return
            value = AnalysisDimensionValue.objects.filter(
                company=event.company,
                dimension=dimension,
                public_id=data["value_public_id"],
            ).first()
            if not value:
                logger.warning("Dimension value not found: %s", data["value_public_id"])
                return
            for field, change in data.get("changes", {}).items():
                setattr(value, field, change.get("new"))
            value.save(_projection_write=True)
            return

        if event.event_type == EventTypes.ANALYSIS_DIMENSION_VALUE_DELETED:
            dimension = AnalysisDimension.objects.filter(
                company=event.company,
                public_id=data["dimension_public_id"],
            ).first()
            if not dimension:
                return
            AnalysisDimensionValue.objects.filter(
                company=event.company,
                dimension=dimension,
                public_id=data["value_public_id"],
            ).delete()
            return

        logger.warning("Unhandled event type for AnalysisDimensionProjection: %s", event.event_type)


class AccountAnalysisDefaultProjection(BaseProjection):
    @property
    def name(self) -> str:
        return "account_analysis_default_read_model"

    @property
    def consumes(self):
        return [
            EventTypes.ACCOUNT_ANALYSIS_DEFAULT_SET,
            EventTypes.ACCOUNT_ANALYSIS_DEFAULT_REMOVED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        data = event.data
        account = Account.objects.filter(
            company=event.company,
            public_id=data["account_public_id"],
        ).first()
        dimension = AnalysisDimension.objects.filter(
            company=event.company,
            public_id=data["dimension_public_id"],
        ).first()
        if not account or not dimension:
            logger.warning("Missing account/dimension for default projection.")
            return

        if event.event_type == EventTypes.ACCOUNT_ANALYSIS_DEFAULT_SET:
            value = AnalysisDimensionValue.objects.filter(
                dimension=dimension,
                public_id=data["value_public_id"],
            ).first()
            if not value:
                logger.warning("Missing dimension value for default projection.")
                return
            AccountAnalysisDefault.objects.projection().update_or_create(
                company=event.company,
                account=account,
                dimension=dimension,
                defaults={"default_value": value},
            )
            return

        if event.event_type == EventTypes.ACCOUNT_ANALYSIS_DEFAULT_REMOVED:
            AccountAnalysisDefault.objects.filter(
                company=event.company,
                account=account,
                dimension=dimension,
            ).delete()
            return

        logger.warning("Unhandled event type for AccountAnalysisDefaultProjection: %s", event.event_type)


class JournalEntryProjection(BaseProjection):
    @property
    def name(self) -> str:
        return "journal_entry_read_model"

    @property
    def consumes(self):
        return [
            EventTypes.JOURNAL_ENTRY_CREATED,
            EventTypes.JOURNAL_ENTRY_UPDATED,
            EventTypes.JOURNAL_ENTRY_SAVED_COMPLETE,
            EventTypes.JOURNAL_ENTRY_POSTED,
            EventTypes.JOURNAL_ENTRY_REVERSED,
            EventTypes.JOURNAL_ENTRY_DELETED,
            EventTypes.JOURNAL_LINE_ANALYSIS_SET,
        ]

    def handle(self, event: BusinessEvent) -> None:
        data = event.data

        if event.event_type == EventTypes.JOURNAL_ENTRY_CREATED:
            entry, _ = JournalEntry.objects.projection().get_or_create(
                company=event.company,
                public_id=data["entry_public_id"],
                defaults={
                    "date": _parse_date(data["date"]),
                    "period": data.get("period"),
                    "memo": data.get("memo", ""),
                    "memo_ar": data.get("memo_ar", ""),
                    "kind": data.get("kind", JournalEntry.Kind.NORMAL),
                    "status": data.get("status", JournalEntry.Status.INCOMPLETE),
                    "created_by_id": data.get("created_by_id"),
                    "currency": data.get("currency", event.company.default_currency),
                    "exchange_rate": Decimal(str(data.get("exchange_rate", "1.0"))),
                },
            )
            lines = data.get("lines", [])
            if lines:
                self._replace_lines(entry, lines)
            return

        if event.event_type == EventTypes.JOURNAL_ENTRY_UPDATED:
            entry = JournalEntry.objects.filter(
                company=event.company,
                public_id=data["entry_public_id"],
            ).first()
            if not entry:
                logger.warning("Journal entry not found for update: %s", data["entry_public_id"])
                return
            for field, change in data.get("changes", {}).items():
                # Skip "lines" - it's handled separately by _replace_lines
                if field == "lines":
                    continue
                if field == "date":
                    setattr(entry, field, _parse_date(change.get("new")))
                else:
                    setattr(entry, field, change.get("new"))
            entry.status = JournalEntry.Status.INCOMPLETE
            entry.save(_projection_write=True)
            if data.get("lines") is not None:
                self._replace_lines(entry, data.get("lines", []))
            return

        if event.event_type == EventTypes.JOURNAL_ENTRY_SAVED_COMPLETE:
            entry = JournalEntry.objects.filter(
                company=event.company,
                public_id=data["entry_public_id"],
            ).first()
            if not entry:
                logger.warning("Journal entry not found for save_complete: %s", data["entry_public_id"])
                return
            if data.get("date"):
                entry.date = _parse_date(data.get("date"))
            if data.get("period") is not None:
                entry.period = data.get("period")
            entry.memo = data.get("memo", entry.memo)
            entry.memo_ar = data.get("memo_ar", entry.memo_ar)
            if data.get("currency"):
                entry.currency = data.get("currency")
            if data.get("exchange_rate"):
                entry.exchange_rate = Decimal(str(data.get("exchange_rate")))
            entry.status = JournalEntry.Status.DRAFT
            entry.save(_projection_write=True)
            if data.get("lines"):
                self._replace_lines(entry, data.get("lines", []))
            return

        if event.event_type == EventTypes.JOURNAL_ENTRY_POSTED:
            entry, _ = JournalEntry.objects.projection().get_or_create(
                company=event.company,
                public_id=data["entry_public_id"],
                defaults={
                    "date": _parse_date(data.get("date")),
                    "period": data.get("period"),
                    "memo": data.get("memo", ""),
                    "memo_ar": data.get("memo_ar", ""),
                    "kind": data.get("kind", JournalEntry.Kind.NORMAL),
                    "status": JournalEntry.Status.POSTED,
                    "posted_at": _parse_datetime(data.get("posted_at")),
                    "posted_by_id": data.get("posted_by_id"),
                    "entry_number": data.get("entry_number", ""),
                    "currency": data.get("currency", event.company.default_currency),
                    "exchange_rate": Decimal(str(data.get("exchange_rate", "1.0"))),
                },
            )
            if data.get("date"):
                entry.date = _parse_date(data.get("date"))
            if data.get("period") is not None:
                entry.period = data.get("period")
            entry.memo = data.get("memo", entry.memo)
            entry.memo_ar = data.get("memo_ar", entry.memo_ar)
            entry.kind = data.get("kind", entry.kind)
            entry.status = JournalEntry.Status.POSTED
            entry.posted_at = _parse_datetime(data.get("posted_at"))
            entry.posted_by_id = data.get("posted_by_id")
            entry.entry_number = data.get("entry_number", "")
            if data.get("currency"):
                entry.currency = data.get("currency")
            if data.get("exchange_rate"):
                entry.exchange_rate = Decimal(str(data.get("exchange_rate")))
            entry.save(_projection_write=True)
            self._replace_lines(entry, data.get("lines", []))
            return

        if event.event_type == EventTypes.JOURNAL_ENTRY_REVERSED:
            original = JournalEntry.objects.filter(
                company=event.company,
                public_id=data["original_entry_public_id"],
            ).first()
            reversal = JournalEntry.objects.filter(
                company=event.company,
                public_id=data["reversal_entry_public_id"],
            ).first()

            if original:
                original.status = JournalEntry.Status.REVERSED
                original.reversed_at = _parse_datetime(data.get("reversed_at")) or timezone.now()
                original.reversed_by_id = data.get("reversed_by_id")
                original.save(_projection_write=True, update_fields=["status", "reversed_at", "reversed_by_id"])

            if original and reversal:
                JournalEntry.objects.filter(
                    pk=reversal.pk
                ).update(reverses_entry=original)
            return

        if event.event_type == EventTypes.JOURNAL_ENTRY_DELETED:
            JournalEntry.objects.filter(
                company=event.company,
                public_id=data["entry_public_id"],
            ).delete()
            return

        if event.event_type == EventTypes.JOURNAL_LINE_ANALYSIS_SET:
            entry = JournalEntry.objects.filter(
                company=event.company,
                public_id=data["entry_public_id"],
            ).first()
            if not entry:
                logger.warning("Entry not found for line analysis set.")
                return
            line = JournalLine.objects.filter(
                entry=entry,
                line_no=data["line_no"],
                company=entry.company,
            ).first()
            if not line:
                logger.warning("Line not found for analysis set.")
                return

            JournalLineAnalysis.objects.filter(journal_line=line, company=entry.company).delete()
            for tag in data.get("analysis_tags", []):
                dimension = AnalysisDimension.objects.filter(
                    company=event.company,
                    public_id=tag.get("dimension_public_id"),
                ).first()
                value = AnalysisDimensionValue.objects.filter(
                    company=event.company,
                    dimension=dimension,
                    public_id=tag.get("value_public_id"),
                ).first() if dimension else None
                if not dimension or not value:
                    continue
                JournalLineAnalysis.objects.projection().create(
                    journal_line=line,
                    company=entry.company,
                    dimension=dimension,
                    dimension_value=value,
                )
            return

        logger.warning("Unhandled event type for JournalEntryProjection: %s", event.event_type)

    def _replace_lines(self, entry: JournalEntry, lines: list[dict]) -> None:
        entry.lines.all().delete()
        line_objects = []
        line_analysis_tags = {}  # line_no -> analysis_tags
        line_no = 1
        for line in lines:
            account_public_id = line.get("account_public_id")
            if not account_public_id:
                continue
            account = Account.objects.filter(
                company=entry.company,
                public_id=account_public_id,
            ).first()
            if not account:
                continue
            debit = Decimal(str(line.get("debit", "0")))
            credit = Decimal(str(line.get("credit", "0")))

            # Skip invalid lines (DB constraint: not both zero)
            if debit == 0 and credit == 0:
                continue

            line_objects.append(JournalLine(
                entry=entry,
                company=entry.company,
                line_no=line_no,
                account=account,
                description=line.get("description", ""),
                description_ar=line.get("description_ar", ""),
                debit=debit,
                credit=credit,
                amount_currency=line.get("amount_currency"),
                currency=line.get("currency") or entry.currency or "",
                exchange_rate=line.get("exchange_rate") or entry.exchange_rate,
            ))
            # Store analysis tags for this line
            if line.get("analysis_tags"):
                line_analysis_tags[line_no] = line.get("analysis_tags")
            line_no += 1
        if line_objects:
            JournalLine.objects.projection().bulk_create(line_objects)

            # Create analysis tags for each line
            if line_analysis_tags:
                created_lines = JournalLine.objects.filter(
                    entry=entry, company=entry.company
                ).order_by("line_no")
                for journal_line in created_lines:
                    tags = line_analysis_tags.get(journal_line.line_no, [])
                    for tag in tags:
                        dimension = AnalysisDimension.objects.filter(
                            company=entry.company,
                            public_id=tag.get("dimension_public_id"),
                        ).first()
                        value = AnalysisDimensionValue.objects.filter(
                            company=entry.company,
                            dimension=dimension,
                            public_id=tag.get("value_public_id"),
                        ).first() if dimension else None
                        if dimension and value:
                            JournalLineAnalysis.objects.projection().create(
                                journal_line=journal_line,
                                company=entry.company,
                                dimension=dimension,
                                dimension_value=value,
                            )


projection_registry.register(AccountProjection())
projection_registry.register(AnalysisDimensionProjection())
projection_registry.register(AccountAnalysisDefaultProjection())
projection_registry.register(JournalEntryProjection())
