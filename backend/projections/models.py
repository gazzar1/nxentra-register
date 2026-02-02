# projections/models.py
"""
Projection models (materialized views).

These tables are DERIVED from events. They can be:
- Rebuilt from scratch by replaying events
- Updated incrementally as new events arrive

NEVER modify these tables directly. They are owned by their projections.
"""

from decimal import Decimal
from django.db import models
from django.conf import settings

from accounts.models import Company
from accounting.models import Account
from events.models import BusinessEvent
from projections.write_barrier import write_context_allowed


class ProjectionOwnedModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                f"{self.__class__.__name__} is a projection-owned read model. "
                "Direct saves are only allowed from projections within projection_writes_allowed()."
            )
        super().save(*args, **kwargs)


class AccountBalance(ProjectionOwnedModel):
    """
    Materialized account balance.
    
    This is the single source of truth for "what is the balance of account X?"
    It is computed by consuming journal_entry.posted and journal_entry.reversed events.
    
    The balance follows accounting conventions:
    - For DEBIT-normal accounts (Assets, Expenses): balance = debits - credits
    - For CREDIT-normal accounts (Liabilities, Equity, Revenue): balance = credits - debits
    
    Attributes:
        company: Tenant isolation
        account: The account this balance belongs to
        balance: Current balance (positive = normal, negative = opposite)
        debit_total: Sum of all debits ever posted
        credit_total: Sum of all credits ever posted
        entry_count: Number of journal entries affecting this account
        last_entry_date: Date of most recent entry (for reporting)
        last_event: Last event that updated this balance (for consistency checks)
    """
    
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="account_balances",
    )
    
    account = models.OneToOneField(
        Account,
        on_delete=models.CASCADE,
        related_name="projected_balance",
    )
    
    # Current balance (computed based on normal_balance)
    balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Current balance (positive = normal direction)",
    )
    
    # Running totals for audit/verification
    debit_total = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Sum of all debits ever posted to this account",
    )
    
    credit_total = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Sum of all credits ever posted to this account",
    )
    
    # Statistics
    entry_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of journal entries affecting this account",
    )
    
    last_entry_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of most recent journal entry",
    )
    
    # Event tracking for consistency
    last_event = models.ForeignKey(
        BusinessEvent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Last event that updated this balance",
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Account Balance"
        verbose_name_plural = "Account Balances"
        indexes = [
            models.Index(fields=["company", "account"]),
            models.Index(fields=["company", "balance"]),
        ]

    def __str__(self):
        return f"{self.account.code}: {self.balance}"

    def apply_debit(self, amount: Decimal):
        """Apply a debit to this balance."""
        self.debit_total += amount
        self._recalculate_balance()

    def apply_credit(self, amount: Decimal):
        """Apply a credit to this balance."""
        self.credit_total += amount
        self._recalculate_balance()

    def _recalculate_balance(self):
        """Recalculate balance based on account's normal balance."""
        if self.account.normal_balance == Account.NormalBalance.DEBIT:
            self.balance = self.debit_total - self.credit_total
        elif self.account.normal_balance == Account.NormalBalance.CREDIT:
            self.balance = self.credit_total - self.debit_total
        else:
            # MEMO accounts: debit = increase, credit = decrease
            self.balance = self.debit_total - self.credit_total

    def verify_integrity(self) -> dict:
        """
        Verify this balance matches what we'd get from replaying events.

        Events are the source of truth. This method replays all
        journal_entry.posted events to compute expected totals,
        then compares against the current projection state.

        Returns:
            dict with:
                - is_valid: bool
                - expected_debit: Decimal
                - expected_credit: Decimal
                - actual_debit: Decimal
                - actual_credit: Decimal
                - events_processed: int
        """
        from events.models import BusinessEvent
        from events.types import EventTypes

        expected_debit = Decimal("0.00")
        expected_credit = Decimal("0.00")
        events_processed = 0

        # Replay all posted events for this company
        events = BusinessEvent.objects.filter(
            company=self.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        ).order_by("company_sequence")

        account_public_id = str(self.account.public_id)

        for event in events:
            lines = event.data.get("lines", [])
            for line_data in lines:
                if line_data.get("account_public_id") != account_public_id:
                    continue
                if line_data.get("is_memo_line", False):
                    continue

                debit = Decimal(line_data.get("debit", "0"))
                credit = Decimal(line_data.get("credit", "0"))

                expected_debit += debit
                expected_credit += credit
                events_processed += 1

        is_valid = (
            self.debit_total == expected_debit and
            self.credit_total == expected_credit
        )

        return {
            "is_valid": is_valid,
            "expected_debit": expected_debit,
            "expected_credit": expected_credit,
            "actual_debit": self.debit_total,
            "actual_credit": self.credit_total,
            "events_processed": events_processed,
        }


class FiscalPeriod(ProjectionOwnedModel):
    """
    Fiscal period read model.

    Periods are derived from events and used to enforce posting rules.
    """

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="fiscal_periods",
    )
    fiscal_year = models.PositiveIntegerField()
    period = models.PositiveSmallIntegerField()
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )
    is_current = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "fiscal_year", "period"],
                name="uniq_fiscal_period",
            )
        ]
        indexes = [
            models.Index(fields=["company", "fiscal_year", "period"]),
            models.Index(fields=["company", "start_date", "end_date"]),
        ]

    def __str__(self):
        return f"{self.company_id} FY{self.fiscal_year} P{self.period} ({self.status})"


class FiscalPeriodConfig(ProjectionOwnedModel):
    """
    Configuration for fiscal periods per company per year.

    Tracks how many periods the year is divided into and which
    range of periods is currently open for posting.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="fiscal_period_configs",
    )
    fiscal_year = models.PositiveIntegerField()
    period_count = models.PositiveSmallIntegerField(default=12)
    current_period = models.PositiveSmallIntegerField(null=True, blank=True)
    open_from_period = models.PositiveSmallIntegerField(null=True, blank=True)
    open_to_period = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "fiscal_year"],
                name="uniq_fiscal_period_config",
            )
        ]

    def __str__(self):
        return f"{self.company_id} FY{self.fiscal_year} ({self.period_count} periods)"


class PeriodAccountBalance(ProjectionOwnedModel):
    """
    Account balance for a specific fiscal period.
    
    Used for:
    - Period-over-period comparisons
    - Monthly/quarterly reports
    - Year-end closing
    
    Note: This is a future enhancement. The AccountBalance projection
    will be extended to maintain period balances as well.
    """
    
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="period_balances",
    )
    
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="period_balances",
    )
    
    # Period identification
    fiscal_year = models.PositiveSmallIntegerField()
    period = models.PositiveSmallIntegerField()  # 1-12 or custom
    
    # Balances
    opening_balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    
    period_debit = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    
    period_credit = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    
    closing_balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    
    # Status
    is_closed = models.BooleanField(
        default=False,
        help_text="Period is closed, no more postings allowed",
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Period Account Balance"
        verbose_name_plural = "Period Account Balances"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "account", "fiscal_year", "period"],
                name="uniq_period_account_balance",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "fiscal_year", "period"]),
        ]

    def __str__(self):
        return f"{self.account.code} FY{self.fiscal_year} P{self.period}: {self.closing_balance}"


class ProjectionAppliedEvent(ProjectionOwnedModel):
    """
    Tracks which events were applied by each projection to ensure idempotency.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="applied_projection_events",
    )

    projection_name = models.CharField(max_length=100)

    event = models.ForeignKey(
        BusinessEvent,
        on_delete=models.CASCADE,
        related_name="+",
    )

    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "projection_name", "event"],
                name="uniq_projection_event",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "projection_name"]),
        ]

    def __str__(self):
        return f"{self.projection_name} applied {self.event_id}"
