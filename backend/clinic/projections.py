# clinic/projections.py
"""
Clinic accounting projection.

Consumes clinic financial events and creates corresponding journal entries
in the accounting system using ModuleAccountMapping for account resolution.

Account roles used by this module:
    ACCOUNTS_RECEIVABLE — Patient receivable account
    CONSULTATION_REVENUE — Consultation/service revenue
    CASH_BANK — Cash or bank account for payments
"""

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone

from accounting.mappings import ModuleAccountMapping
from accounting.models import (
    AnalysisDimension,
    AnalysisDimensionValue,
    JournalEntry,
    JournalLine,
    JournalLineAnalysis,
)
from clinic.models import Patient
from events.emitter import emit_event_no_actor
from events.models import BusinessEvent
from events.types import EventTypes, JournalEntryPostedData
from projections.base import BaseProjection
from projections.models import FiscalPeriod

logger = logging.getLogger(__name__)

MODULE_NAME = "clinic"
PROJECTION_NAME = "clinic_accounting"

# Account roles this module requires
ROLE_ACCOUNTS_RECEIVABLE = "ACCOUNTS_RECEIVABLE"
ROLE_CONSULTATION_REVENUE = "CONSULTATION_REVENUE"
ROLE_CASH_BANK = "CASH_BANK"

REQUIRED_ROLES = [ROLE_ACCOUNTS_RECEIVABLE, ROLE_CONSULTATION_REVENUE, ROLE_CASH_BANK]


def _parse_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    return value


def _resolve_period(company, entry_date):
    fp = FiscalPeriod.objects.filter(
        company=company,
        start_date__lte=entry_date,
        end_date__gte=entry_date,
        period_type=FiscalPeriod.PeriodType.NORMAL,
    ).first()
    if fp:
        return fp.period
    return entry_date.month


class ClinicAccountingProjection(BaseProjection):
    """
    Creates journal entries from clinic financial events.

    Invoice issued:   DR Accounts Receivable / CR Consultation Revenue
    Payment received: DR Cash/Bank / CR Accounts Receivable
    Payment voided:   DR Accounts Receivable / CR Cash/Bank (reversal)
    """

    @property
    def name(self) -> str:
        return PROJECTION_NAME

    @property
    def consumes(self):
        return [
            EventTypes.CLINIC_INVOICE_ISSUED,
            EventTypes.CLINIC_PAYMENT_RECEIVED,
            EventTypes.CLINIC_PAYMENT_VOIDED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        metadata = event.metadata or {}
        if metadata.get("source_projection") == PROJECTION_NAME:
            return

        data = event.get_data()
        company = event.company

        mapping = ModuleAccountMapping.get_mapping(company, MODULE_NAME)
        if not mapping:
            logger.warning(
                "No ModuleAccountMapping for clinic module, company %s — skipping %s",
                company,
                event.event_type,
            )
            return

        handler = {
            EventTypes.CLINIC_INVOICE_ISSUED: self._handle_invoice_issued,
            EventTypes.CLINIC_PAYMENT_RECEIVED: self._handle_payment_received,
            EventTypes.CLINIC_PAYMENT_VOIDED: self._handle_payment_voided,
        }.get(event.event_type)

        if handler:
            handler(event, data, mapping)

    def _handle_invoice_issued(self, event, data, mapping):
        """DR Accounts Receivable / CR Consultation Revenue."""
        ar = mapping.get(ROLE_ACCOUNTS_RECEIVABLE)
        revenue = mapping.get(ROLE_CONSULTATION_REVENUE)
        if not self._check_accounts(event, ar, revenue, ROLE_ACCOUNTS_RECEIVABLE, ROLE_CONSULTATION_REVENUE):
            return

        amount = Decimal(str(data.get("amount", "0")))
        invoice_no = data.get("invoice_no", data.get("document_ref", ""))
        memo = f"Clinic invoice: {invoice_no}"
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()

        dimension_context = self._resolve_clinic_dimensions(
            event.company,
            patient_public_id=data.get("patient_public_id"),
            visit_public_id=data.get("visit_public_id"),
        )

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=ar,
            credit_account=revenue,
            amount=amount,
            dimension_context=dimension_context,
        )

    def _handle_payment_received(self, event, data, mapping):
        """DR Cash/Bank / CR Accounts Receivable."""
        cash = mapping.get(ROLE_CASH_BANK)
        ar = mapping.get(ROLE_ACCOUNTS_RECEIVABLE)
        if not self._check_accounts(event, cash, ar, ROLE_CASH_BANK, ROLE_ACCOUNTS_RECEIVABLE):
            return

        amount = Decimal(str(data.get("amount", "0")))
        doc_ref = data.get("document_ref", "")
        memo = f"Clinic payment: {doc_ref}"
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()

        dimension_context = self._resolve_clinic_dimensions(
            event.company,
            patient_public_id=data.get("patient_public_id"),
            invoice_public_id=data.get("invoice_public_id"),
        )

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=cash,
            credit_account=ar,
            amount=amount,
            dimension_context=dimension_context,
        )

    def _handle_payment_voided(self, event, data, mapping):
        """DR Accounts Receivable / CR Cash/Bank (reversal)."""
        ar = mapping.get(ROLE_ACCOUNTS_RECEIVABLE)
        cash = mapping.get(ROLE_CASH_BANK)
        if not self._check_accounts(event, ar, cash, ROLE_ACCOUNTS_RECEIVABLE, ROLE_CASH_BANK):
            return

        amount = Decimal(str(data.get("amount", "0")))
        doc_ref = data.get("document_ref", "")
        memo = f"VOID clinic payment: {doc_ref}"
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()

        dimension_context = self._resolve_clinic_dimensions(
            event.company,
            patient_public_id=data.get("patient_public_id"),
            invoice_public_id=data.get("invoice_public_id"),
        )

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=ar,
            credit_account=cash,
            amount=amount,
            dimension_context=dimension_context,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_accounts(self, event, debit_account, credit_account, debit_label, credit_label):
        missing = []
        if not debit_account:
            missing.append(debit_label)
        if not credit_account:
            missing.append(credit_label)
        if missing:
            logger.warning(
                "Clinic account mapping missing role(s) %s for company %s — skipping %s (event %s)",
                ", ".join(missing),
                event.company,
                event.event_type,
                event.id,
            )
            return False
        return True

    def _create_posted_entry(
        self, *, event, entry_date, memo, debit_account, credit_account, amount, dimension_context=None
    ):
        company = event.company

        if amount <= 0:
            logger.warning(
                "Skipping %s — non-positive amount %s (event %s)",
                event.event_type,
                amount,
                event.id,
            )
            return

        # Idempotency check
        if JournalEntry.objects.filter(
            company=company,
            date=entry_date,
            memo=memo,
            status=JournalEntry.Status.POSTED,
        ).exists():
            logger.info(
                "Journal entry already exists for memo '%s' on %s — skipping",
                memo,
                entry_date,
            )
            return

        period = _resolve_period(company, entry_date)
        now = timezone.now()
        currency = getattr(company, "default_currency", "USD")

        entry = JournalEntry.objects.projection().create(
            company=company,
            public_id=uuid.uuid4(),
            date=entry_date,
            period=period,
            memo=memo,
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            posted_at=now,
            currency=currency,
            exchange_rate=Decimal("1.0"),
        )

        debit_line = JournalLine(
            entry=entry,
            company=company,
            public_id=uuid.uuid4(),
            line_no=1,
            account=debit_account,
            description=memo,
            debit=amount,
            credit=Decimal("0"),
            currency=currency,
            exchange_rate=Decimal("1.0"),
        )
        credit_line = JournalLine(
            entry=entry,
            company=company,
            public_id=uuid.uuid4(),
            line_no=2,
            account=credit_account,
            description=memo,
            debit=Decimal("0"),
            credit=amount,
            currency=currency,
            exchange_rate=Decimal("1.0"),
        )
        JournalLine.objects.projection().bulk_create([debit_line, credit_line])

        # Attach analysis dimensions (patient, doctor) to journal lines
        if dimension_context:
            self._attach_dimensions(company, [debit_line, credit_line], dimension_context)

        # Emit JOURNAL_ENTRY_POSTED so AccountBalanceProjection updates balances
        lines_data = [
            {
                "line_public_id": str(debit_line.public_id),
                "line_no": 1,
                "account_public_id": str(debit_account.public_id),
                "account_code": debit_account.code,
                "description": memo,
                "debit": str(amount),
                "credit": "0",
                "currency": currency,
                "exchange_rate": "1.0",
            },
            {
                "line_public_id": str(credit_line.public_id),
                "line_no": 2,
                "account_public_id": str(credit_account.public_id),
                "account_code": credit_account.code,
                "description": memo,
                "debit": "0",
                "credit": str(amount),
                "currency": currency,
                "exchange_rate": "1.0",
            },
        ]

        emit_event_no_actor(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry.public_id),
            idempotency_key=f"clinic.je.posted:{entry.public_id}",
            metadata={"source_projection": PROJECTION_NAME},
            data=JournalEntryPostedData(
                entry_public_id=str(entry.public_id),
                entry_number="",
                date=str(entry_date),
                memo=memo,
                kind="NORMAL",
                posted_at=str(now),
                posted_by_id=0,
                posted_by_email="system@clinic",
                total_debit=str(amount),
                total_credit=str(amount),
                lines=lines_data,
                period=period,
                currency=currency,
                exchange_rate="1.0",
            ),
            caused_by_event=event,
        )

        logger.info(
            "Created journal entry %s for %s (event %s): %s",
            entry.public_id,
            event.event_type,
            event.id,
            memo,
        )

    # ------------------------------------------------------------------
    # Dimension derivation
    # ------------------------------------------------------------------

    def _resolve_clinic_dimensions(self, company, patient_public_id=None, visit_public_id=None, invoice_public_id=None):
        """
        Derive dimension context from clinic entities.

        Returns dict like {"patient": "PAT001", "doctor": "DOC01"}
        mapping dimension codes to value codes.
        """
        context = {}

        # Resolve patient
        if patient_public_id:
            try:
                patient = Patient.objects.get(
                    company=company,
                    public_id=patient_public_id,
                )
                context["patient"] = patient.code
            except Patient.DoesNotExist:
                logger.warning("Patient %s not found for dimension derivation", patient_public_id)

        # Resolve doctor from visit or invoice→visit
        doctor = None
        if visit_public_id:
            from clinic.models import Visit

            try:
                visit = Visit.objects.select_related("doctor").get(
                    company=company,
                    public_id=visit_public_id,
                )
                doctor = visit.doctor
            except Visit.DoesNotExist:
                pass
        elif invoice_public_id:
            from clinic.models import Invoice

            try:
                invoice = Invoice.objects.select_related("visit__doctor").get(
                    company=company,
                    public_id=invoice_public_id,
                )
                if invoice.visit:
                    doctor = invoice.visit.doctor
            except Invoice.DoesNotExist:
                pass

        if doctor:
            context["doctor"] = doctor.code

        return context

    def _attach_dimensions(self, company, lines, dimension_context):
        """
        Create JournalLineAnalysis records for the given journal lines.

        Args:
            company: Company instance
            lines: list of JournalLine instances
            dimension_context: dict of {dimension_code: value_code}
        """
        if not dimension_context:
            return

        # Batch-fetch matching dimensions and values
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

        # Fetch all matching values in one query
        value_lookups = []
        for dim_code, val_code in dimension_context.items():
            dim = dimensions.get(dim_code)
            if dim:
                value_lookups.append((dim.id, val_code))

        if not value_lookups:
            return

        from django.db.models import Q

        q = Q()
        for dim_id, val_code in value_lookups:
            q |= Q(dimension_id=dim_id, code=val_code)

        values = {
            (v.dimension_id, v.code): v
            for v in AnalysisDimensionValue.objects.filter(q, company=company, is_active=True)
        }

        # Create JournalLineAnalysis for each line x dimension
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
                        company.name,
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
            JournalLineAnalysis.objects.projection().bulk_create(analysis_records, ignore_conflicts=True)

    def _clear_projected_data(self, company) -> None:
        """Clear clinic-generated journal entries for rebuild."""
        JournalEntry.objects.filter(
            company=company,
            memo__startswith="Clinic ",
        ).delete()
        JournalEntry.objects.filter(
            company=company,
            memo__startswith="VOID clinic ",
        ).delete()
