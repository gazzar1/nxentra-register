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
from decimal import Decimal
from datetime import datetime, date

from django.utils import timezone

from events.types import EventTypes
from events.models import BusinessEvent
from projections.base import BaseProjection
from projections.models import FiscalPeriod
from accounting.mappings import ModuleAccountMapping
from accounting.models import JournalEntry, JournalLine


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
                company, event.event_type,
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
        if not self._check_accounts(event, ar, revenue,
                                    ROLE_ACCOUNTS_RECEIVABLE, ROLE_CONSULTATION_REVENUE):
            return

        amount = Decimal(str(data.get("amount", "0")))
        invoice_no = data.get("invoice_no", data.get("document_ref", ""))
        memo = f"Clinic invoice: {invoice_no}"
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=ar,
            credit_account=revenue,
            amount=amount,
        )

    def _handle_payment_received(self, event, data, mapping):
        """DR Cash/Bank / CR Accounts Receivable."""
        cash = mapping.get(ROLE_CASH_BANK)
        ar = mapping.get(ROLE_ACCOUNTS_RECEIVABLE)
        if not self._check_accounts(event, cash, ar,
                                    ROLE_CASH_BANK, ROLE_ACCOUNTS_RECEIVABLE):
            return

        amount = Decimal(str(data.get("amount", "0")))
        doc_ref = data.get("document_ref", "")
        memo = f"Clinic payment: {doc_ref}"
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=cash,
            credit_account=ar,
            amount=amount,
        )

    def _handle_payment_voided(self, event, data, mapping):
        """DR Accounts Receivable / CR Cash/Bank (reversal)."""
        ar = mapping.get(ROLE_ACCOUNTS_RECEIVABLE)
        cash = mapping.get(ROLE_CASH_BANK)
        if not self._check_accounts(event, ar, cash,
                                    ROLE_ACCOUNTS_RECEIVABLE, ROLE_CASH_BANK):
            return

        amount = Decimal(str(data.get("amount", "0")))
        doc_ref = data.get("document_ref", "")
        memo = f"VOID clinic payment: {doc_ref}"
        entry_date = _parse_date(data.get("transaction_date")) or event.created_at.date()

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=ar,
            credit_account=cash,
            amount=amount,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_accounts(self, event, debit_account, credit_account,
                        debit_label, credit_label):
        missing = []
        if not debit_account:
            missing.append(debit_label)
        if not credit_account:
            missing.append(credit_label)
        if missing:
            logger.warning(
                "Clinic account mapping missing role(s) %s for company %s — "
                "skipping %s (event %s)",
                ", ".join(missing), event.company, event.event_type, event.id,
            )
            return False
        return True

    def _create_posted_entry(self, *, event, entry_date, memo,
                             debit_account, credit_account, amount):
        company = event.company

        if amount <= 0:
            logger.warning(
                "Skipping %s — non-positive amount %s (event %s)",
                event.event_type, amount, event.id,
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
                memo, entry_date,
            )
            return

        period = _resolve_period(company, entry_date)
        now = timezone.now()
        currency = getattr(company, "default_currency", "SAR")

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

        JournalLine.objects.projection().bulk_create([
            JournalLine(
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
            ),
            JournalLine(
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
            ),
        ])

        logger.info(
            "Created journal entry %s for %s (event %s): %s",
            entry.public_id, event.event_type, event.id, memo,
        )

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
