# projections/property.py
"""
Property accounting projection.

Consumes property management events (rent, deposits, expenses) and creates
corresponding journal entries in the accounting system.

Journal entry mapping follows PRD Section 10.2.
"""

import logging
import uuid
from decimal import Decimal
from datetime import datetime, date

from django.utils import timezone

from events.types import EventTypes, JournalEntryPostedData
from events.models import BusinessEvent
from events.emitter import emit_event_no_actor
from projections.base import BaseProjection
from projections.models import FiscalPeriod
from properties.models import PropertyAccountMapping
from accounting.models import Account, JournalEntry, JournalLine


logger = logging.getLogger(__name__)

PROJECTION_NAME = "property_accounting"


def _parse_date(value):
    """Parse a date value from event data."""
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    return value


def _resolve_period(company, entry_date):
    """
    Resolve the fiscal period number for a given date and company.

    Returns the period number if a matching open FiscalPeriod exists,
    or the month number as a fallback.
    """
    fp = FiscalPeriod.objects.filter(
        company=company,
        start_date__lte=entry_date,
        end_date__gte=entry_date,
        period_type=FiscalPeriod.PeriodType.NORMAL,
    ).first()
    if fp:
        return fp.period
    # Fallback: use calendar month
    return entry_date.month


class PropertyAccountingProjection(BaseProjection):
    """
    Projection that creates journal entries from property management events.

    For each property event (rent due, payment, deposit, expense) this
    projection writes a fully-posted JournalEntry with the appropriate
    debit/credit lines based on the company's PropertyAccountMapping.

    Safety mechanisms:
    - Recursion guard: events emitted by this projection are skipped.
    - Idempotency: duplicate memo check prevents double-posting.
    - Missing mapping: logs a warning and skips silently.
    """

    @property
    def name(self) -> str:
        return PROJECTION_NAME

    @property
    def consumes(self):
        return [
            EventTypes.RENT_DUE_POSTED,
            EventTypes.RENT_PAYMENT_RECEIVED,
            EventTypes.RENT_PAYMENT_ALLOCATED,
            EventTypes.RENT_PAYMENT_VOIDED,
            EventTypes.DEPOSIT_RECEIVED,
            EventTypes.DEPOSIT_ADJUSTED,
            EventTypes.DEPOSIT_REFUNDED,
            EventTypes.DEPOSIT_FORFEITED,
            EventTypes.PROPERTY_EXPENSE_RECORDED,
        ]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def handle(self, event: BusinessEvent) -> None:
        # Recursion guard (PRD A.3): skip events that originated from
        # this projection to prevent infinite loops.
        metadata = event.metadata or {}
        if metadata.get("source_projection") == PROJECTION_NAME:
            return

        data = event.get_data()
        company = event.company

        # Load the company's property account mapping
        try:
            mapping = PropertyAccountMapping.objects.select_related(
                "rental_income_account",
                "other_income_account",
                "accounts_receivable_account",
                "cash_bank_account",
                "unapplied_cash_account",
                "security_deposit_account",
                "accounts_payable_account",
                "property_expense_account",
            ).get(company=company)
        except PropertyAccountMapping.DoesNotExist:
            logger.warning(
                "No PropertyAccountMapping for company %s — skipping %s",
                company, event.event_type,
            )
            return

        handler = self._get_handler(event.event_type)
        if handler:
            handler(event, data, mapping)

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------

    def _get_handler(self, event_type):
        return {
            EventTypes.RENT_DUE_POSTED: self._handle_rent_due_posted,
            EventTypes.RENT_PAYMENT_RECEIVED: self._handle_payment_received,
            EventTypes.RENT_PAYMENT_ALLOCATED: self._handle_payment_allocated,
            EventTypes.RENT_PAYMENT_VOIDED: self._handle_payment_voided,
            EventTypes.DEPOSIT_RECEIVED: self._handle_deposit_received,
            EventTypes.DEPOSIT_ADJUSTED: self._handle_deposit_adjusted,
            EventTypes.DEPOSIT_REFUNDED: self._handle_deposit_refunded,
            EventTypes.DEPOSIT_FORFEITED: self._handle_deposit_forfeited,
            EventTypes.PROPERTY_EXPENSE_RECORDED: self._handle_expense_recorded,
        }.get(event_type)

    # ------------------------------------------------------------------
    # Individual event handlers
    # ------------------------------------------------------------------

    def _handle_rent_due_posted(self, event, data, mapping):
        """DR Accounts Receivable / CR Rental Income."""
        debit_account = mapping.accounts_receivable_account
        credit_account = mapping.rental_income_account
        if not self._check_accounts(event, debit_account, credit_account,
                                    "accounts_receivable", "rental_income"):
            return

        amount = Decimal(str(data.get("total_due", 0)))
        contract_no = data.get("contract_no", "")
        installment_no = data.get("installment_no", "")
        memo = f"Rent due: {contract_no} #{installment_no}"
        entry_date = _parse_date(data.get("due_date")) or event.occurred_at.date()

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=debit_account,
            credit_account=credit_account,
            amount=amount,
        )

    def _handle_payment_received(self, event, data, mapping):
        """DR Cash/Bank / CR Unapplied Cash."""
        debit_account = mapping.cash_bank_account
        credit_account = mapping.unapplied_cash_account
        if not self._check_accounts(event, debit_account, credit_account,
                                    "cash_bank", "unapplied_cash"):
            return

        amount = Decimal(str(data.get("amount", 0)))
        receipt_no = data.get("receipt_no", "")
        memo = f"Payment received: {receipt_no}"
        entry_date = _parse_date(data.get("payment_date")) or event.occurred_at.date()

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=debit_account,
            credit_account=credit_account,
            amount=amount,
        )

    def _handle_payment_allocated(self, event, data, mapping):
        """DR Unapplied Cash / CR Accounts Receivable."""
        debit_account = mapping.unapplied_cash_account
        credit_account = mapping.accounts_receivable_account
        if not self._check_accounts(event, debit_account, credit_account,
                                    "unapplied_cash", "accounts_receivable"):
            return

        amount = Decimal(str(data.get("allocated_amount", 0)))
        receipt_no = data.get("receipt_no", "")
        contract_no = data.get("contract_no", "")
        memo = f"Rent payment: {receipt_no} \u2192 {contract_no}"
        entry_date = event.occurred_at.date()

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=debit_account,
            credit_account=credit_account,
            amount=amount,
        )

    def _handle_payment_voided(self, event, data, mapping):
        """DR Unapplied Cash / CR Cash/Bank."""
        debit_account = mapping.unapplied_cash_account
        credit_account = mapping.cash_bank_account
        if not self._check_accounts(event, debit_account, credit_account,
                                    "unapplied_cash", "cash_bank"):
            return

        amount = Decimal(str(data.get("amount", 0)))
        receipt_no = data.get("receipt_no", "")
        memo = f"VOID: {receipt_no}"
        entry_date = event.occurred_at.date()

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=debit_account,
            credit_account=credit_account,
            amount=amount,
        )

    def _handle_deposit_received(self, event, data, mapping):
        """DR Cash/Bank / CR Security Deposits."""
        debit_account = mapping.cash_bank_account
        credit_account = mapping.security_deposit_account
        if not self._check_accounts(event, debit_account, credit_account,
                                    "cash_bank", "security_deposit"):
            return

        amount = Decimal(str(data.get("amount", 0)))
        contract_no = data.get("contract_no", "")
        memo = f"Deposit received: {contract_no}"
        entry_date = _parse_date(data.get("transaction_date")) or event.occurred_at.date()

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=debit_account,
            credit_account=credit_account,
            amount=amount,
        )

    def _handle_deposit_adjusted(self, event, data, mapping):
        """DR Security Deposits / CR Cash/Bank (or reverse for increase)."""
        security_account = mapping.security_deposit_account
        cash_account = mapping.cash_bank_account
        if not self._check_accounts(event, security_account, cash_account,
                                    "security_deposit", "cash_bank"):
            return

        amount = Decimal(str(data.get("amount", 0)))
        contract_no = data.get("contract_no", "")
        memo = f"Deposit adjustment: {contract_no}"
        entry_date = _parse_date(data.get("transaction_date")) or event.occurred_at.date()

        # Positive amount = decrease in deposit (refund-like):
        #   DR Security Deposits / CR Cash/Bank
        # Negative amount = increase in deposit:
        #   DR Cash/Bank / CR Security Deposits
        if amount >= 0:
            debit_account = security_account
            credit_account = cash_account
        else:
            debit_account = cash_account
            credit_account = security_account
            amount = abs(amount)

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=debit_account,
            credit_account=credit_account,
            amount=amount,
        )

    def _handle_deposit_refunded(self, event, data, mapping):
        """DR Security Deposits / CR Cash/Bank."""
        debit_account = mapping.security_deposit_account
        credit_account = mapping.cash_bank_account
        if not self._check_accounts(event, debit_account, credit_account,
                                    "security_deposit", "cash_bank"):
            return

        amount = Decimal(str(data.get("amount", 0)))
        contract_no = data.get("contract_no", "")
        memo = f"Deposit refund: {contract_no}"
        entry_date = _parse_date(data.get("transaction_date")) or event.occurred_at.date()

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=debit_account,
            credit_account=credit_account,
            amount=amount,
        )

    def _handle_deposit_forfeited(self, event, data, mapping):
        """DR Security Deposits / CR Other Income."""
        debit_account = mapping.security_deposit_account
        credit_account = mapping.other_income_account
        if not self._check_accounts(event, debit_account, credit_account,
                                    "security_deposit", "other_income"):
            return

        amount = Decimal(str(data.get("amount", 0)))
        contract_no = data.get("contract_no", "")
        memo = f"Deposit forfeited: {contract_no}"
        entry_date = _parse_date(data.get("transaction_date")) or event.occurred_at.date()

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=debit_account,
            credit_account=credit_account,
            amount=amount,
        )

    def _handle_expense_recorded(self, event, data, mapping):
        """
        DR Expense / CR Cash/Bank  (if cash_paid)
        DR Expense / CR AP          (if credit)
        """
        debit_account = mapping.property_expense_account
        payment_mode = data.get("payment_mode", "cash_paid")

        if payment_mode == "credit":
            credit_account = mapping.accounts_payable_account
            account_label = "accounts_payable"
        else:
            credit_account = mapping.cash_bank_account
            account_label = "cash_bank"

        if not self._check_accounts(event, debit_account, credit_account,
                                    "property_expense", account_label):
            return

        amount = Decimal(str(data.get("amount", 0)))
        category = data.get("category", "")
        entry_date = _parse_date(data.get("expense_date")) or event.occurred_at.date()

        if payment_mode == "credit":
            memo = f"Property expense (credit): {category}"
        else:
            memo = f"Property expense: {category}"

        self._create_posted_entry(
            event=event,
            entry_date=entry_date,
            memo=memo,
            debit_account=debit_account,
            credit_account=credit_account,
            amount=amount,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_accounts(self, event, debit_account, credit_account,
                        debit_label, credit_label):
        """
        Verify both accounts are configured. Logs a warning and returns
        False if either is missing.
        """
        missing = []
        if not debit_account:
            missing.append(debit_label)
        if not credit_account:
            missing.append(credit_label)
        if missing:
            logger.warning(
                "PropertyAccountMapping missing account(s) %s for company %s — "
                "skipping %s (event %s)",
                ", ".join(missing), event.company, event.event_type, event.id,
            )
            return False
        return True

    def _create_posted_entry(self, *, event, entry_date, memo,
                             debit_account, credit_account, amount):
        """
        Create a fully-posted JournalEntry with two lines (debit and credit).

        Uses direct model creation via the projection manager, matching the
        pattern established by JournalEntryProjection in projections/accounting.py.
        This avoids going through commands (which would emit events and require
        an ActorContext with permissions).

        Idempotency safety net: if a journal entry with the same memo already
        exists for this company and date, skip creation.
        """
        company = event.company

        if amount <= 0:
            logger.warning(
                "Skipping %s — non-positive amount %s (event %s)",
                event.event_type, amount, event.id,
            )
            return

        # Idempotency check: prevent duplicate journal entries
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
            idempotency_key=f"property.je.posted:{entry.public_id}",
            metadata={"source_projection": PROJECTION_NAME},
            data=JournalEntryPostedData(
                entry_public_id=str(entry.public_id),
                entry_number="",
                date=str(entry_date),
                memo=memo,
                kind="NORMAL",
                posted_at=str(now),
                posted_by_id=0,
                posted_by_email="system@property",
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
            entry.public_id, event.event_type, event.id, memo,
        )


# Registration is handled by ProjectionsConfig.ready() via AppConfig.projections.
# Do not add a module-level projection_registry.register() call here.
