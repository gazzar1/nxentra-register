"""
Aggregate definitions for event sourcing.

Aggregates are reconstituted by replaying events from their event stream.
Each aggregate type has a dedicated stream identified by (aggregate_type, aggregate_id).

IMPORTANT: Aggregate Boundary Rules
===================================
All events that modify an aggregate MUST be emitted with that aggregate's
type and ID. This ensures:
1. Aggregates are replayable from their own stream (no global scans)
2. Event ordering is consistent within the aggregate
3. Optimistic concurrency can be enforced per-aggregate

Example:
- JournalEntry events use aggregate_type="JournalEntry", aggregate_id=entry_public_id
- JournalLine analysis events ALSO use "JournalEntry" because lines belong to entries
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, List, Dict, Any

from events.emitter import get_aggregate_events
from events.types import EventTypes


@dataclass
class JournalEntryAggregate:
    public_id: str
    company: Any
    date: Optional[str] = None
    memo: str = ""
    memo_ar: str = ""
    kind: str = "NORMAL"
    currency: Optional[str] = None
    exchange_rate: Optional[str] = None
    status: str = "INCOMPLETE"
    lines: List[dict] = field(default_factory=list)
    deleted: bool = False
    reversed: bool = False

    def apply(self, event) -> None:
        data = event.data

        if event.event_type == EventTypes.JOURNAL_ENTRY_CREATED:
            self.date = data.get("date")
            self.memo = data.get("memo", "")
            self.memo_ar = data.get("memo_ar", "")
            self.kind = data.get("kind", self.kind)
            self.currency = data.get("currency", self.currency)
            self.exchange_rate = data.get("exchange_rate", self.exchange_rate)
            self.status = data.get("status", self.status)
            self.lines = data.get("lines", [])
            return

        if event.event_type == EventTypes.JOURNAL_ENTRY_UPDATED:
            changes = data.get("changes", {})
            for field, change in changes.items():
                if field in ["date", "memo", "memo_ar", "kind", "status"]:
                    setattr(self, field, change.get("new"))
                if field in ["currency", "exchange_rate"]:
                    setattr(self, field, change.get("new"))
            if data.get("lines") is not None:
                self.lines = data.get("lines", [])
            self.status = "INCOMPLETE"
            return

        if event.event_type == EventTypes.JOURNAL_ENTRY_SAVED_COMPLETE:
            self.date = data.get("date", self.date)
            self.memo = data.get("memo", self.memo)
            self.memo_ar = data.get("memo_ar", self.memo_ar)
            self.currency = data.get("currency", self.currency)
            self.exchange_rate = data.get("exchange_rate", self.exchange_rate)
            if data.get("lines") is not None:
                self.lines = data.get("lines", [])
            self.status = "DRAFT"
            return

        if event.event_type == EventTypes.JOURNAL_ENTRY_POSTED:
            self.date = data.get("date", self.date)
            self.memo = data.get("memo", self.memo)
            self.memo_ar = data.get("memo_ar", self.memo_ar)
            self.kind = data.get("kind", self.kind)
            self.currency = data.get("currency", self.currency)
            self.exchange_rate = data.get("exchange_rate", self.exchange_rate)
            self.status = "POSTED"
            if data.get("lines") is not None:
                self.lines = data.get("lines", [])
            return

        if event.event_type == EventTypes.JOURNAL_ENTRY_REVERSED:
            self.status = "REVERSED"
            self.reversed = True
            return

        if event.event_type == EventTypes.JOURNAL_ENTRY_DELETED:
            self.deleted = True
            return

        # Handle analysis events (these belong to the JournalEntry aggregate stream)
        if event.event_type == EventTypes.JOURNAL_LINE_ANALYSIS_SET:
            line_no = data.get("line_no")
            analysis_tags = data.get("analysis_tags", [])
            for line in self.lines:
                if line.get("line_no") == line_no:
                    line["analysis_tags"] = analysis_tags
                    break
            return

    @property
    def total_debit(self) -> Decimal:
        total = Decimal("0.00")
        for line in self.lines:
            total += Decimal(str(line.get("debit", "0")))
        return total

    @property
    def total_credit(self) -> Decimal:
        total = Decimal("0.00")
        for line in self.lines:
            total += Decimal(str(line.get("credit", "0")))
        return total


def load_journal_entry_aggregate(company, public_id: str) -> Optional[JournalEntryAggregate]:
    """
    Load a JournalEntry aggregate by replaying its event stream.

    All events for this aggregate (including JOURNAL_LINE_ANALYSIS_SET)
    are fetched from a single stream using get_aggregate_events().
    No global scans required.
    """
    events = get_aggregate_events(company, "JournalEntry", public_id)
    if not events:
        return None

    aggregate = JournalEntryAggregate(public_id=public_id, company=company)
    for event in events:
        aggregate.apply(event)

    return aggregate


@dataclass
class AccountAggregate:
    public_id: str
    company: Any
    code: str = ""
    name: str = ""
    name_ar: str = ""
    account_type: str = ""
    status: str = "ACTIVE"
    description: str = ""
    description_ar: str = ""
    unit_of_measure: str = ""
    parent_public_id: Optional[str] = None
    is_header: bool = False
    deleted: bool = False

    def apply(self, event) -> None:
        data = event.data
        if event.event_type == EventTypes.ACCOUNT_CREATED:
            self.code = data.get("code", "")
            self.name = data.get("name", "")
            self.name_ar = data.get("name_ar", "")
            self.account_type = data.get("account_type", "")
            self.status = data.get("status", self.status)
            self.description = data.get("description", "")
            self.description_ar = data.get("description_ar", "")
            self.unit_of_measure = data.get("unit_of_measure", "")
            self.parent_public_id = data.get("parent_public_id")
            self.is_header = data.get("is_header", False)
            return

        if event.event_type == EventTypes.ACCOUNT_UPDATED:
            changes = data.get("changes", {})
            for field, change in changes.items():
                if hasattr(self, field):
                    setattr(self, field, change.get("new"))
            return

        if event.event_type == EventTypes.ACCOUNT_DELETED:
            self.deleted = True


def load_account_aggregate(company, public_id: str) -> Optional[AccountAggregate]:
    events = get_aggregate_events(company, "Account", public_id)
    if not events:
        return None

    aggregate = AccountAggregate(public_id=public_id, company=company)
    for event in events:
        aggregate.apply(event)

    return aggregate


@dataclass
class FiscalPeriodAggregate:
    company: Any
    fiscal_year: int
    period: int
    closed: bool = False

    def apply(self, event) -> None:
        if event.event_type == EventTypes.FISCAL_PERIOD_CLOSED:
            self.closed = True
        elif event.event_type == EventTypes.FISCAL_PERIOD_OPENED:
            self.closed = False
        elif event.event_type == EventTypes.FISCAL_PERIOD_RANGE_SET:
            data = event.data
            open_from = data.get("open_from_period", 1)
            open_to = data.get("open_to_period", self.period)
            if open_from <= self.period <= open_to:
                self.closed = False
            else:
                self.closed = True


def load_fiscal_period_aggregate(company, fiscal_year: int, period: int) -> FiscalPeriodAggregate:
    aggregate_id = f"{company.public_id}:{fiscal_year}:{period}"
    events = get_aggregate_events(company, "FiscalPeriod", aggregate_id)
    aggregate = FiscalPeriodAggregate(
        company=company,
        fiscal_year=fiscal_year,
        period=period,
    )
    for event in events:
        aggregate.apply(event)
    return aggregate
