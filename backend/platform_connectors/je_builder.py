# platform_connectors/je_builder.py
"""
Shared journal entry builder for platform connectors.

Extracts the repeated ~100-line JE creation pattern (create entry, add lines,
validate balance, assign entry number, emit JOURNAL_ENTRY_POSTED) into a
reusable helper.

Used by PlatformAccountingProjection and can be called directly by
platform-specific projections that need custom JE logic.
"""

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import timezone

from accounting.commands import _next_company_sequence
from accounting.models import ExchangeRate, JournalEntry, JournalLine
from events.emitter import emit_event_no_actor
from events.models import BusinessEvent
from events.types import EventTypes, JournalEntryPostedData
from projections.models import FiscalPeriod

logger = logging.getLogger(__name__)


@dataclass
class JELine:
    """A single journal entry line to be created."""

    account: object  # Account model instance
    description: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")


@dataclass
class JERequest:
    """
    Everything needed to create a journal entry.

    Pass this to build_journal_entry() instead of repeating the
    creation + validation + numbering + event-emission boilerplate.
    """

    company: object  # Company model instance
    entry_date: object  # date
    memo: str
    source_module: str
    source_document: str = ""
    currency: str = "USD"
    exchange_rate: Decimal = Decimal("1.0")
    kind: str = "NORMAL"
    lines: list[JELine] = field(default_factory=list)
    # The event that caused this JE (for caused_by_event linkage)
    caused_by_event: BusinessEvent | None = None
    # Projection name for metadata tagging
    projection_name: str = ""
    # Posted-by info for the JOURNAL_ENTRY_POSTED event
    posted_by_email: str = "system@platform"
    # Dimension context for tagging JE lines (e.g. {"platform": "shopify", "store": "shopify:my-store"})
    dimension_context: dict[str, str] = field(default_factory=dict)


def _resolve_period(company, entry_date):
    """Resolve fiscal period for a date, falling back to month number."""
    fp = FiscalPeriod.objects.filter(
        company=company,
        start_date__lte=entry_date,
        end_date__gte=entry_date,
        period_type=FiscalPeriod.PeriodType.NORMAL,
    ).first()
    if fp:
        return fp.period
    return entry_date.month


def _fix_fx_rounding(lines, entry, company, currency, fx_rate):
    """
    Fix penny rounding imbalance caused by independent per-line FX conversion.

    Adds a visible rounding line to a dedicated FX Rounding account
    (following SAP/Oracle/NetSuite convention). Falls back to adjusting the
    largest line if no rounding account is configured.
    Only applies for trivial imbalances (≤ 0.05).
    """
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account

    total_debit = sum(l.debit for l in lines)
    total_credit = sum(l.credit for l in lines)
    diff = total_debit - total_credit

    if diff == Decimal("0") or abs(diff) > Decimal("0.05"):
        return

    rounding_account = ModuleAccountMapping.get_account(company, "core", "FX_ROUNDING")
    if not rounding_account:
        rounding_account = Account.objects.filter(
            company=company,
            role=Account.AccountRole.FX_ROUNDING,
            is_postable=True,
        ).first()

    if not rounding_account:
        # Fallback: adjust the largest line
        if diff > 0:
            credit_lines = [l for l in lines if l.credit > 0]
            target = max(credit_lines, key=lambda l: l.credit) if credit_lines else max(lines, key=lambda l: l.debit)
            if target.credit > 0:
                target.credit += diff
            else:
                target.debit -= diff
        else:
            debit_lines = [l for l in lines if l.debit > 0]
            target = max(debit_lines, key=lambda l: l.debit) if debit_lines else max(lines, key=lambda l: l.credit)
            if target.debit > 0:
                target.debit -= diff
            else:
                target.credit += diff
        logger.debug("FX rounding adjustment (no rounding account): %s on line %s", diff, target.line_no)
        return

    next_line_no = max(l.line_no for l in lines) + 1
    if diff > 0:
        rounding_line = JournalLine(
            entry=entry,
            company=company,
            public_id=uuid.uuid4(),
            line_no=next_line_no,
            account=rounding_account,
            description="FX rounding adjustment",
            debit=Decimal("0"),
            credit=diff,
            currency=currency,
            exchange_rate=fx_rate,
        )
    else:
        rounding_line = JournalLine(
            entry=entry,
            company=company,
            public_id=uuid.uuid4(),
            line_no=next_line_no,
            account=rounding_account,
            description="FX rounding adjustment",
            debit=abs(diff),
            credit=Decimal("0"),
            currency=currency,
            exchange_rate=fx_rate,
        )

    lines.append(rounding_line)
    logger.info(
        "FX rounding line added: %s %s to account %s", "CR" if diff > 0 else "DR", abs(diff), rounding_account.code
    )


def build_journal_entry(req: JERequest) -> JournalEntry | None:
    """
    Create a balanced, posted journal entry with lines and emit the
    JOURNAL_ENTRY_POSTED event.

    Returns the created JournalEntry, or None if:
    - A posted JE with the same memo already exists (idempotency)
    - The entry is unbalanced (saved as INCOMPLETE, notification sent)
    - Validation fails and on_closed_period="reject" (default)

    Args:
        req: A JERequest containing all the information for the entry.
    """
    # Idempotency check
    if JournalEntry.objects.filter(
        company=req.company,
        memo=req.memo,
        status=JournalEntry.Status.POSTED,
    ).exists():
        logger.info("Journal entry already exists for '%s' — skipping", req.memo)
        return None

    # Shared validation: period, account postability, balance
    from accounting.validation import validate_system_journal_postable

    validation_lines = [{"account": line.account, "debit": line.debit, "credit": line.credit} for line in req.lines]
    validation = validate_system_journal_postable(
        company=req.company,
        entry_date=req.entry_date,
        lines=validation_lines,
        source_module=req.source_module,
        allow_missing_counterparty=True,  # Platform connectors typically don't use AR/AP counterparties
        on_closed_period="incomplete",  # Don't lose data — quarantine as INCOMPLETE
    )

    force_incomplete = False
    if not validation.ok:
        # Hard validation failure (bad accounts, etc.) — create INCOMPLETE + notify
        logger.warning(
            "Validation failed for '%s' (%s): %s",
            req.memo,
            req.source_module,
            "; ".join(validation.errors),
        )
        force_incomplete = True
    elif validation.errors:
        # Soft failure (closed period with on_closed_period="incomplete")
        logger.info(
            "Period closed for '%s' (%s) — creating as INCOMPLETE: %s",
            req.memo,
            req.source_module,
            "; ".join(validation.errors),
        )
        force_incomplete = True

    # Multi-currency: auto-resolve exchange rate if not explicitly provided
    functional_currency = req.company.functional_currency or req.company.default_currency or "USD"
    is_foreign = req.currency != functional_currency
    fx_rate = req.exchange_rate

    if is_foreign and fx_rate == Decimal("1.0"):
        looked_up_rate = ExchangeRate.get_rate(req.company, req.currency, functional_currency, req.entry_date)
        if looked_up_rate:
            fx_rate = looked_up_rate
        else:
            logger.warning(
                "No exchange rate found for %s→%s on %s (company %s) — using 1.0",
                req.currency,
                functional_currency,
                req.entry_date,
                req.company,
            )

    period = _resolve_period(req.company, req.entry_date)
    now = timezone.now()

    initial_status = JournalEntry.Status.INCOMPLETE if force_incomplete else JournalEntry.Status.POSTED

    entry = JournalEntry.objects.projection().create(
        company=req.company,
        public_id=uuid.uuid4(),
        date=req.entry_date,
        period=period,
        memo=req.memo,
        kind=JournalEntry.Kind.NORMAL,
        status=initial_status,
        posted_at=now if not force_incomplete else None,
        currency=req.currency,
        exchange_rate=fx_rate,
        source_module=req.source_module,
        source_document=req.source_document,
    )

    # Create journal lines — convert amounts if foreign currency
    db_lines = []
    for i, line in enumerate(req.lines, start=1):
        if is_foreign and fx_rate != Decimal("1.0"):
            converted_debit = (line.debit * fx_rate).quantize(Decimal("0.01"))
            converted_credit = (line.credit * fx_rate).quantize(Decimal("0.01"))
            amount_currency = line.debit if line.debit > 0 else (-line.credit if line.credit > 0 else Decimal("0"))
        else:
            converted_debit = line.debit
            converted_credit = line.credit
            amount_currency = None

        db_lines.append(
            JournalLine(
                entry=entry,
                company=req.company,
                public_id=uuid.uuid4(),
                line_no=i,
                account=line.account,
                description=line.description,
                debit=converted_debit,
                credit=converted_credit,
                amount_currency=amount_currency,
                currency=req.currency,
                exchange_rate=fx_rate,
            )
        )

    # Fix FX rounding imbalance before saving
    if is_foreign and fx_rate != Decimal("1.0"):
        _fix_fx_rounding(db_lines, entry, req.company, req.currency, fx_rate)

    JournalLine.objects.projection().bulk_create(db_lines)

    # Balance validation
    total_debit = sum(l.debit for l in db_lines)
    total_credit = sum(l.credit for l in db_lines)

    if total_debit != total_credit:
        force_incomplete = True
        validation_errors_str = f"Unbalanced: debit={total_debit} credit={total_credit}"
        if validation.errors:
            validation_errors_str += "; " + "; ".join(validation.errors)
        logger.error(
            "Unbalanced JE for '%s': debit=%s credit=%s — saved as INCOMPLETE",
            req.memo,
            total_debit,
            total_credit,
        )

    if force_incomplete and entry.status != JournalEntry.Status.INCOMPLETE:
        entry.status = JournalEntry.Status.INCOMPLETE
        entry.posted_at = None
        entry.save(update_fields=["status", "posted_at"])

    if entry.status == JournalEntry.Status.INCOMPLETE:
        from accounts.models import Notification

        error_detail = (
            "; ".join(validation.errors)
            if validation.errors
            else f"Unbalanced: debit={total_debit} credit={total_credit}"
        )
        Notification.notify_company_admins(
            company=req.company,
            title=f"Entry needs review: {req.memo}",
            message=(
                f"Journal entry '{req.memo}' was saved as INCOMPLETE. "
                f"Reason: {error_detail}. "
                f"Please review and post manually."
            ),
            level=Notification.Level.ERROR,
            link=f"/accounting/journal-entries/{entry.id}",
            source_module=req.source_module,
        )
        return entry  # Return INCOMPLETE entry so caller can handle

    # Assign proper entry number
    seq = _next_company_sequence(req.company, "journal_entry_number")
    entry_number = f"JE-{req.company.id}-{seq:06d}"
    entry.entry_number = entry_number
    entry.save(update_fields=["entry_number"])

    # Build lines data for the posted event
    lines_data = []
    for db_line in db_lines:
        lines_data.append(
            {
                "line_public_id": str(db_line.public_id),
                "line_no": db_line.line_no,
                "account_public_id": str(db_line.account.public_id),
                "account_code": db_line.account.code,
                "description": db_line.description,
                "debit": str(db_line.debit),
                "credit": str(db_line.credit),
                "currency": req.currency,
                "exchange_rate": str(fx_rate),
            }
        )

    # Attach dimension tags if context provided
    if req.dimension_context:
        _attach_dimensions(req.company, db_lines, req.dimension_context)

    # Emit JOURNAL_ENTRY_POSTED
    idem_prefix = req.projection_name or req.source_module
    emit_event_no_actor(
        company=req.company,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=str(entry.public_id),
        idempotency_key=f"{idem_prefix}.je.posted:{entry.public_id}",
        metadata={"source_projection": req.projection_name} if req.projection_name else {},
        data=JournalEntryPostedData(
            entry_public_id=str(entry.public_id),
            entry_number=entry_number,
            date=str(req.entry_date),
            memo=req.memo,
            kind=req.kind,
            posted_at=str(now),
            posted_by_id=0,
            posted_by_email=req.posted_by_email,
            total_debit=str(total_debit),
            total_credit=str(total_credit),
            lines=lines_data,
            period=period,
            currency=req.currency,
            exchange_rate=str(fx_rate),
        ),
        caused_by_event=req.caused_by_event,
    )

    return entry


def _attach_dimensions(company, lines, dimension_context):
    """
    Create JournalLineAnalysis records for the given journal lines.

    Mirrors the pattern from PropertyAccountingProjection._attach_dimensions().

    Args:
        company: Company instance
        lines: list of JournalLine instances
        dimension_context: dict of {dimension_code: value_code}
    """
    if not dimension_context:
        return

    from accounting.models import (
        AnalysisDimension,
        AnalysisDimensionValue,
        JournalLineAnalysis,
    )

    dim_codes = list(dimension_context.keys())
    dimensions = {
        d.code: d
        for d in AnalysisDimension.objects.filter(
            company=company,
            code__in=dim_codes,
            is_active=True,
        )
    }

    if not dimensions:
        return

    from django.db.models import Q

    value_lookups = []
    for dim_code, val_code in dimension_context.items():
        dim = dimensions.get(dim_code)
        if dim:
            value_lookups.append((dim.id, val_code))

    if not value_lookups:
        return

    q = Q()
    for dim_id, val_code in value_lookups:
        q |= Q(dimension_id=dim_id, code=val_code)

    values = {
        (v.dimension_id, v.code): v for v in AnalysisDimensionValue.objects.filter(q, company=company, is_active=True)
    }

    analysis_records = []
    for line in lines:
        for dim_code, val_code in dimension_context.items():
            dim = dimensions.get(dim_code)
            if not dim:
                continue
            val = values.get((dim.id, val_code))
            if not val:
                logger.debug(
                    "Dimension value %s=%s not found for company %s — skipping",
                    dim_code,
                    val_code,
                    company,
                )
                continue
            analysis_records.append(
                JournalLineAnalysis(
                    journal_line=line,
                    company=company,
                    dimension=dim,
                    dimension_value=val,
                )
            )

    if analysis_records:
        JournalLineAnalysis.objects.projection().bulk_create(
            analysis_records,
            ignore_conflicts=True,
        )
