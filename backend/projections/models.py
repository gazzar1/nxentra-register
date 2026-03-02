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
            lines = event.get_data().get("lines", [])
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


class FiscalYear(ProjectionOwnedModel):
    """
    Fiscal year read model with close/open status.

    Tracks whether a fiscal year has been formally closed (year-end close
    procedure completed) or is still open for posting.

    State machine:
        OPEN -> CLOSED  (via close_fiscal_year command)
        CLOSED -> OPEN  (via reopen_fiscal_year command, requires reason + permission)

    Invariant: A CLOSED fiscal year must not contain any OPEN periods.
    """

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="fiscal_years",
    )
    fiscal_year = models.PositiveIntegerField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    close_reason = models.TextField(blank=True, default="")
    retained_earnings_entry_public_id = models.CharField(
        max_length=36,
        blank=True,
        default="",
        help_text="Public ID of the closing journal entry that transferred P&L to retained earnings",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Fiscal Year"
        verbose_name_plural = "Fiscal Years"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "fiscal_year"],
                name="uniq_fiscal_year",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "fiscal_year"]),
            models.Index(fields=["company", "status"]),
        ]

    def __str__(self):
        return f"{self.company_id} FY{self.fiscal_year} ({self.status})"


class FiscalPeriod(ProjectionOwnedModel):
    """
    Fiscal period read model.

    Periods are derived from events and used to enforce posting rules.
    Standard setup: 12 monthly periods (NORMAL) + 1 adjustment period (ADJUSTMENT).

    Period 13 (ADJUSTMENT type) rules:
    - Only allows ADJUSTMENT and CLOSING kind journal entries
    - Blocks sales invoices, purchase bills, inventory ops, receipts, payments
    - Has the same end date as Period 12 (it's a logical period, not calendar)
    - Required for year-end closing entries

    State machine for status:
        OPEN -> CLOSED  (via close_period command)
        CLOSED -> OPEN  (via open_period command, requires fiscal year to be OPEN)
    """

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"

    class PeriodType(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="fiscal_periods",
    )
    fiscal_year = models.PositiveIntegerField()
    period = models.PositiveSmallIntegerField()
    period_type = models.CharField(
        max_length=12,
        choices=PeriodType.choices,
        default=PeriodType.NORMAL,
        help_text="NORMAL for periods 1-12, ADJUSTMENT for period 13",
    )
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
        ptype = " (ADJ)" if self.period_type == self.PeriodType.ADJUSTMENT else ""
        return f"{self.company_id} FY{self.fiscal_year} P{self.period}{ptype} ({self.status})"

    @property
    def is_adjustment_period(self):
        return self.period_type == self.PeriodType.ADJUSTMENT


class FiscalPeriodConfig(ProjectionOwnedModel):
    """
    Configuration for fiscal periods per company per year.

    Tracks how many periods the year is divided into and which
    range of periods is currently open for posting.
    Always includes 12 normal periods + 1 adjustment period (period 13).
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="fiscal_period_configs",
    )
    fiscal_year = models.PositiveIntegerField()
    period_count = models.PositiveSmallIntegerField(
        default=13,
        help_text="Total periods including adjustment period (always 13 for standard ERP)",
    )
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
    - Year-end closing (calculating net income for retained earnings)
    - Opening balance carry-forward to next fiscal year

    Populated by PeriodAccountBalanceProjection consuming journal_entry.posted events.
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
    period = models.PositiveSmallIntegerField()  # 1-13

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

    # Event tracking
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
            models.Index(fields=["company", "account", "fiscal_year"]),
        ]

    def __str__(self):
        return f"{self.account.code} FY{self.fiscal_year} P{self.period}: {self.closing_balance}"

    def recalculate_closing(self):
        """Recalculate closing balance from opening + period movements."""
        if self.account.normal_balance == Account.NormalBalance.DEBIT:
            self.closing_balance = self.opening_balance + self.period_debit - self.period_credit
        elif self.account.normal_balance == Account.NormalBalance.CREDIT:
            self.closing_balance = self.opening_balance + self.period_credit - self.period_debit
        else:
            self.closing_balance = self.opening_balance + self.period_debit - self.period_credit


class InventoryBalance(ProjectionOwnedModel):
    """
    Materialized inventory balance per item per warehouse.

    This is the single source of truth for "what is the quantity/value of item X in warehouse Y?"
    It is computed by consuming inventory stock_received and stock_issued events.

    The stock ledger (StockLedgerEntry) is the SOURCE OF TRUTH for movements.
    This projection provides a query-efficient view of current inventory state.

    Note: Unlike other projections, InventoryBalance can also be written from commands
    because stock availability checks require synchronous, up-to-date values.
    The projection can still be rebuilt from StockLedgerEntry if needed.

    Attributes:
        company: Tenant isolation
        item: The inventory item
        warehouse: The warehouse location
        qty_on_hand: Current quantity in stock
        avg_cost: Current weighted average cost per unit
        stock_value: Total value = qty_on_hand * avg_cost
        last_event: Last event that updated this balance
        entry_count: Number of stock ledger entries
    """

    # Allow writes from both projection and command contexts
    _allowed_write_contexts = {"projection", "command"}

    def save(self, *args, **kwargs):
        if not write_context_allowed(self._allowed_write_contexts) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                f"{self.__class__.__name__} is a projection-owned read model. "
                "Direct saves are only allowed from projections within projection_writes_allowed() "
                "or from inventory commands within command_writes_allowed()."
            )
        # Call Model.save directly, skipping ProjectionOwnedModel.save
        models.Model.save(self, *args, **kwargs)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="inventory_balances",
    )

    item = models.ForeignKey(
        "sales.Item",
        on_delete=models.CASCADE,
        related_name="inventory_balances",
    )

    warehouse = models.ForeignKey(
        "inventory.Warehouse",
        on_delete=models.CASCADE,
        related_name="inventory_balances",
    )

    # Current state
    qty_on_hand = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0"),
        help_text="Current quantity on hand",
    )

    avg_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("0"),
        help_text="Current weighted average cost per unit",
    )

    stock_value = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Total stock value = qty_on_hand * avg_cost",
    )

    # Event tracking for idempotency
    last_event = models.ForeignKey(
        BusinessEvent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Last event that updated this balance",
    )

    last_entry_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of most recent stock movement",
    )

    entry_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of stock ledger entries affecting this balance",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Inventory Balance"
        verbose_name_plural = "Inventory Balances"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "item", "warehouse"],
                name="uniq_inventory_balance",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "item"]),
            models.Index(fields=["company", "warehouse"]),
            models.Index(fields=["company", "item", "warehouse"]),
            models.Index(fields=["company", "qty_on_hand"]),
        ]

    def __str__(self):
        return f"{self.item.code} @ {self.warehouse.code}: {self.qty_on_hand} units"

    def apply_receipt(self, qty: Decimal, unit_cost: Decimal):
        """
        Apply a stock receipt (purchase, return from customer, adjustment up).

        Recalculates weighted average cost:
        new_avg = (old_value + new_value) / new_qty
        """
        old_value = self.qty_on_hand * self.avg_cost
        new_value = qty * unit_cost
        new_qty = self.qty_on_hand + qty

        if new_qty > 0:
            self.avg_cost = (old_value + new_value) / new_qty
        else:
            self.avg_cost = unit_cost

        self.qty_on_hand = new_qty
        self.stock_value = self.qty_on_hand * self.avg_cost

    def apply_issue(self, qty: Decimal):
        """
        Apply a stock issue (sale, return to vendor, adjustment down).

        Uses current avg_cost - does not change it.
        Note: qty should be positive, the sign is handled by the caller.
        """
        self.qty_on_hand -= qty
        self.stock_value = self.qty_on_hand * self.avg_cost

    def get_issue_cost(self, qty: Decimal) -> Decimal:
        """
        Calculate the cost value for issuing a quantity.

        Returns qty * avg_cost.
        """
        return qty * self.avg_cost


class CustomerBalance(ProjectionOwnedModel):
    """
    Materialized customer balance (subledger).

    This is the single source of truth for "what is the balance owed by customer X?"
    It is computed by consuming journal_entry.posted events where lines have
    a customer counterparty.

    The balance represents the customer's AR balance:
    - Positive = customer owes us (normal for AR)
    - Negative = we owe customer (overpayment/credit)

    Attributes:
        company: Tenant isolation
        customer: The customer
        balance: Current outstanding balance
        debit_total: Sum of all debits (invoices, debit notes)
        credit_total: Sum of all credits (payments, credit notes)
        last_invoice_date: Date of most recent invoice
        last_payment_date: Date of most recent payment
        oldest_open_date: Date of oldest unpaid item (for aging)
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="customer_balances",
    )

    customer = models.OneToOneField(
        "accounting.Customer",
        on_delete=models.CASCADE,
        related_name="projected_balance",
    )

    # Current balance (debit - credit for AR)
    balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Current outstanding balance (positive = owed to us)",
    )

    # Running totals for audit/verification
    debit_total = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Sum of all debits (invoices, debit notes)",
    )

    credit_total = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Sum of all credits (payments, credit notes)",
    )

    # Statistics
    transaction_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of transactions affecting this customer",
    )

    last_invoice_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of most recent invoice",
    )

    last_payment_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of most recent payment received",
    )

    oldest_open_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of oldest unpaid item (for aging reports)",
    )

    # Event tracking for idempotency
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
        verbose_name = "Customer Balance"
        verbose_name_plural = "Customer Balances"
        indexes = [
            models.Index(fields=["company", "customer"]),
            models.Index(fields=["company", "balance"]),
            models.Index(fields=["company", "oldest_open_date"]),
        ]

    def __str__(self):
        return f"{self.customer.code}: {self.balance}"

    def apply_debit(self, amount: Decimal):
        """Apply a debit (increases balance owed)."""
        self.debit_total += amount
        self.balance = self.debit_total - self.credit_total

    def apply_credit(self, amount: Decimal):
        """Apply a credit (decreases balance owed)."""
        self.credit_total += amount
        self.balance = self.debit_total - self.credit_total


class VendorBalance(ProjectionOwnedModel):
    """
    Materialized vendor balance (subledger).

    This is the single source of truth for "what do we owe vendor X?"
    It is computed by consuming journal_entry.posted events where lines have
    a vendor counterparty.

    The balance represents the vendor's AP balance:
    - Positive = we owe vendor (normal for AP)
    - Negative = vendor owes us (overpayment/debit balance)

    Attributes:
        company: Tenant isolation
        vendor: The vendor
        balance: Current outstanding balance
        credit_total: Sum of all credits (bills, credit notes from vendor)
        debit_total: Sum of all debits (payments, debit notes to vendor)
        last_bill_date: Date of most recent bill
        last_payment_date: Date of most recent payment
        oldest_open_date: Date of oldest unpaid item (for aging)
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="vendor_balances",
    )

    vendor = models.OneToOneField(
        "accounting.Vendor",
        on_delete=models.CASCADE,
        related_name="projected_balance",
    )

    # Current balance (credit - debit for AP)
    balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Current outstanding balance (positive = we owe vendor)",
    )

    # Running totals for audit/verification
    debit_total = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Sum of all debits (payments, debit notes)",
    )

    credit_total = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Sum of all credits (bills, credit notes from vendor)",
    )

    # Statistics
    transaction_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of transactions affecting this vendor",
    )

    last_bill_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of most recent bill",
    )

    last_payment_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of most recent payment made",
    )

    oldest_open_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of oldest unpaid item (for aging reports)",
    )

    # Event tracking for idempotency
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
        verbose_name = "Vendor Balance"
        verbose_name_plural = "Vendor Balances"
        indexes = [
            models.Index(fields=["company", "vendor"]),
            models.Index(fields=["company", "balance"]),
            models.Index(fields=["company", "oldest_open_date"]),
        ]

    def __str__(self):
        return f"{self.vendor.code}: {self.balance}"

    def apply_debit(self, amount: Decimal):
        """Apply a debit (decreases balance owed)."""
        self.debit_total += amount
        self.balance = self.credit_total - self.debit_total

    def apply_credit(self, amount: Decimal):
        """Apply a credit (increases balance owed)."""
        self.credit_total += amount
        self.balance = self.credit_total - self.debit_total


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


class ProjectionStatus(models.Model):
    """
    Tracks the status of each projection for each company.

    Used for:
    - Monitoring projection health
    - Tracking rebuild progress
    - Coordinating rebuild operations
    - Providing status to frontend admin

    This is NOT a projection-owned model because it's metadata about projections,
    not derived data from events.
    """

    class Status(models.TextChoices):
        READY = "READY", "Ready"
        REBUILDING = "REBUILDING", "Rebuilding"
        ERROR = "ERROR", "Error"
        PAUSED = "PAUSED", "Paused"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="projection_statuses",
    )

    projection_name = models.CharField(
        max_length=100,
        help_text="Name of the projection (e.g., 'account_balance')",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.READY,
    )

    # Progress tracking
    events_total = models.PositiveIntegerField(
        default=0,
        help_text="Total events to process during rebuild",
    )

    events_processed = models.PositiveIntegerField(
        default=0,
        help_text="Events processed so far during rebuild",
    )

    # Timing
    last_rebuild_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the last rebuild started",
    )

    last_rebuild_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the last rebuild completed",
    )

    last_rebuild_duration_seconds = models.FloatField(
        null=True,
        blank=True,
        help_text="Duration of last rebuild in seconds",
    )

    # Error tracking
    error_message = models.TextField(
        blank=True,
        default="",
        help_text="Last error message if status is ERROR",
    )

    error_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of errors during current/last rebuild",
    )

    # Metadata
    last_event_sequence = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Last processed event sequence (for lag calculation)",
    )

    rebuild_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="User who requested the rebuild",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Projection Status"
        verbose_name_plural = "Projection Statuses"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "projection_name"],
                name="uniq_projection_status",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["projection_name", "status"]),
        ]

    def __str__(self):
        return f"{self.projection_name} @ {self.company.slug}: {self.status}"

    @property
    def progress_percent(self) -> float:
        """Calculate rebuild progress as percentage."""
        if self.events_total == 0:
            return 100.0 if self.status == self.Status.READY else 0.0
        return round((self.events_processed / self.events_total) * 100, 2)

    @property
    def is_rebuilding(self) -> bool:
        """Check if projection is currently rebuilding."""
        return self.status == self.Status.REBUILDING

    def mark_rebuild_started(self, total_events: int, requested_by=None):
        """Mark projection as rebuilding."""
        from django.utils import timezone
        self.status = self.Status.REBUILDING
        self.events_total = total_events
        self.events_processed = 0
        self.last_rebuild_started_at = timezone.now()
        self.last_rebuild_completed_at = None
        self.last_rebuild_duration_seconds = None
        self.error_message = ""
        self.error_count = 0
        self.rebuild_requested_by = requested_by
        self.save()

    def update_progress(self, events_processed: int):
        """Update rebuild progress."""
        self.events_processed = events_processed
        self.save(update_fields=["events_processed", "updated_at"])

    def mark_rebuild_completed(self, last_event_sequence: int = None):
        """Mark projection as ready after successful rebuild."""
        from django.utils import timezone
        now = timezone.now()
        self.status = self.Status.READY
        self.last_rebuild_completed_at = now
        if self.last_rebuild_started_at:
            self.last_rebuild_duration_seconds = (
                now - self.last_rebuild_started_at
            ).total_seconds()
        if last_event_sequence is not None:
            self.last_event_sequence = last_event_sequence
        self.save()

    def mark_rebuild_error(self, error_message: str):
        """Mark projection as having an error."""
        self.status = self.Status.ERROR
        self.error_message = error_message
        self.error_count += 1
        self.save()
