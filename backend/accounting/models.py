# accounting/models.py
"""
Accounting READ MODELS for Nxentra ERP.

IMPORTANT: These are READ MODELS (projections), not primary state.
=================================================================
Events are the source of truth. These tables are materialized views
built by projections that consume events from the event store.

DO NOT:
- Call .save() directly on these models (use commands)
- Call .create() directly (use commands)
- Call .update() directly (use commands)
- Call .delete() directly (use commands)

All mutations MUST go through the command layer (accounting/commands.py),
which emits events that projections consume to update these tables.

The only code allowed to write to these models is:
- projections/accounting.py (AccountProjection, JournalEntryProjection, etc.)

Models:
- Account: Chart of Accounts (read model)
- JournalEntry: Journal entry headers (read model)
- JournalLine: Journal entry lines (read model)
- AnalysisDimension: User-defined analysis dimensions (read model)
- AnalysisDimensionValue: Values within dimensions (read model)
- JournalLineAnalysis: Analysis tags on journal lines (read model)
- AccountAnalysisDefault: Default analysis values for accounts (read model)
"""

from django.db import models, transaction
from django.db.models import Sum, Q
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal
import uuid
from django.conf import settings
from projections.write_barrier import write_context_allowed


class ProjectionWriteQuerySet(models.QuerySet):
    """
    Custom QuerySet that supports projection writes.

    Projections should use .projection() to get a manager that allows writes.
    """

    def __init__(self, *args, _projection_write: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._projection_write = _projection_write

    def _clone(self):
        c = super()._clone()
        c._projection_write = self._projection_write
        return c

    def projection(self):
        """Return a queryset that allows projection writes."""
        clone = self._clone()
        clone._projection_write = True
        return clone

    def update_or_create(self, defaults=None, create_defaults=None, **kwargs):
        """Override to pass _projection_write to save()."""
        if self._projection_write:
            # Inject _projection_write into the save call
            defaults = defaults or {}
            original_defaults = defaults.copy()

            def _save_with_projection(obj, update_fields=None):
                obj.save(_projection_write=True, update_fields=update_fields)

            # Use the standard implementation but intercept the save
            with transaction.atomic(using=self.db):
                obj, created = self._update_or_create_impl(
                    defaults, create_defaults, **kwargs
                )
                return obj, created

        return super().update_or_create(defaults=defaults, create_defaults=create_defaults, **kwargs)

    def _update_or_create_impl(self, defaults, create_defaults, **kwargs):
        """Implementation that uses _projection_write=True for saves."""
        defaults = defaults or {}
        create_defaults = create_defaults or {}

        self._for_write = True
        with transaction.atomic(using=self.db):
            try:
                obj = self.select_for_update().get(**kwargs)
            except self.model.DoesNotExist:
                params = {**kwargs, **create_defaults, **defaults}
                obj = self.model(**params)
                obj.save(_projection_write=True, using=self.db)
                return obj, True

            for k, v in defaults.items():
                setattr(obj, k, v)
            obj.save(_projection_write=True, using=self.db)
            return obj, False

    def get_or_create(self, defaults=None, **kwargs):
        """Override to pass _projection_write to save()."""
        if self._projection_write:
            defaults = defaults or {}
            self._for_write = True
            with transaction.atomic(using=self.db):
                try:
                    obj = self.get(**kwargs)
                    return obj, False
                except self.model.DoesNotExist:
                    params = {**kwargs, **defaults}
                    obj = self.model(**params)
                    obj.save(_projection_write=True, using=self.db)
                    return obj, True

        return super().get_or_create(defaults=defaults, **kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        """Override bulk_create - objects must be pre-validated since save() isn't called."""
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                f"{self.model.__name__} is a read model. "
                "bulk_create is only allowed from projections within projection_writes_allowed()."
            )
        return super().bulk_create(objs, *args, **kwargs)

    def create(self, **kwargs):
        """Override create to enforce projection writes."""
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                f"{self.model.__name__} is a read model. "
                "create is only allowed from projections within projection_writes_allowed()."
            )
        obj = self.model(**kwargs)
        obj.save(_projection_write=True, using=self.db)
        return obj

    def delete(self):
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                f"{self.model.__name__} is a read model. "
                "delete is only allowed from projections within projection_writes_allowed()."
            )
        return super().delete()


class ProjectionWriteManager(models.Manager):
    """
    Custom manager that supports projection writes.

    Usage in projections:
        Account.objects.projection().update_or_create(...)
        JournalLine.objects.projection().bulk_create(...)
    """

    def get_queryset(self):
        return ProjectionWriteQuerySet(self.model, using=self._db)

    def projection(self):
        """Return a queryset that allows projection writes."""
        return self.get_queryset().projection()

from accounts.models import Company


class AccountingReadModel(models.Model):
    class Meta:
        abstract = True

    def delete(self, *args, **kwargs):
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                f"{self.__class__.__name__} is a read model. "
                "Direct deletes are only allowed from projections within projection_writes_allowed()."
            )
        return super().delete(*args, **kwargs)


class CompanySequence(models.Model):
    """
    Per-company counters for sequential identifiers.

    This is a write model (not a projection) used by commands
    to allocate unique numbers under concurrency.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="sequences",
    )
    name = models.CharField(max_length=100)
    next_value = models.BigIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"],
                name="uniq_company_sequence_name",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "name"]),
        ]

    def __str__(self):
        return f"{self.company_id}:{self.name}={self.next_value}"

    def save(self, *args, **kwargs):
        if not write_context_allowed({"command", "migration", "bootstrap", "admin_emergency"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "CompanySequence is a command-owned write model. "
                "Direct saves are only allowed within command_writes_allowed()."
            )
        super().save(*args, **kwargs)


class Account(AccountingReadModel):
    """
    Chart of Accounts entry.
    
    Supports:
    - Hierarchical structure (parent/child)
    - Account types with normal balance rules
    - Header accounts (non-postable groupings)
    - Soft status (ACTIVE/INACTIVE/LOCKED)
    - Multilingual names (English/Arabic)
    - Memo/Statistical accounts for non-monetary tracking
    """

    class AccountType(models.TextChoices):
        # Balance Sheet - Assets
        ASSET = "ASSET", "Asset"
        RECEIVABLE = "RECEIVABLE", "Accounts Receivable"
        CONTRA_ASSET = "CONTRA_ASSET", "Contra Asset"
        
        # Balance Sheet - Liabilities
        LIABILITY = "LIABILITY", "Liability"
        PAYABLE = "PAYABLE", "Accounts Payable"
        CONTRA_LIABILITY = "CONTRA_LIABILITY", "Contra Liability"
        
        # Balance Sheet - Equity
        EQUITY = "EQUITY", "Equity"
        CONTRA_EQUITY = "CONTRA_EQUITY", "Contra Equity"
        
        # Income Statement
        REVENUE = "REVENUE", "Revenue"
        CONTRA_REVENUE = "CONTRA_REVENUE", "Contra Revenue"
        EXPENSE = "EXPENSE", "Expense"
        CONTRA_EXPENSE = "CONTRA_EXPENSE", "Contra Expense"
        
        # Statistical (Non-financial)
        MEMO = "MEMO", "Memo/Statistical"

    class NormalBalance(models.TextChoices):
        DEBIT = "DEBIT", "Debit"
        CREDIT = "CREDIT", "Credit"
        NONE = "NONE", "None"  # For MEMO accounts

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        LOCKED = "LOCKED", "Locked"  # Has transactions, cannot delete

    # Map account types to their normal balance
    NORMAL_BALANCE_MAP = {
        AccountType.ASSET: NormalBalance.DEBIT,
        AccountType.RECEIVABLE: NormalBalance.DEBIT,
        AccountType.CONTRA_ASSET: NormalBalance.CREDIT,
        AccountType.LIABILITY: NormalBalance.CREDIT,
        AccountType.PAYABLE: NormalBalance.CREDIT,
        AccountType.CONTRA_LIABILITY: NormalBalance.DEBIT,
        AccountType.EQUITY: NormalBalance.CREDIT,
        AccountType.CONTRA_EQUITY: NormalBalance.DEBIT,
        AccountType.REVENUE: NormalBalance.CREDIT,
        AccountType.CONTRA_REVENUE: NormalBalance.DEBIT,
        AccountType.EXPENSE: NormalBalance.DEBIT,
        AccountType.CONTRA_EXPENSE: NormalBalance.CREDIT,
        AccountType.MEMO: NormalBalance.NONE,
    }

    # Account types that are subsets of ASSET for hierarchy validation
    ASSET_FAMILY = {AccountType.ASSET, AccountType.RECEIVABLE, AccountType.CONTRA_ASSET}
    LIABILITY_FAMILY = {AccountType.LIABILITY, AccountType.PAYABLE, AccountType.CONTRA_LIABILITY}

    # Custom manager for projection writes
    objects = ProjectionWriteManager()

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="accounts",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    
    code = models.CharField(max_length=20)
    
    # Multilingual names
    name = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255, blank=True, default="")
    
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        db_column="type",
    )
    
    normal_balance = models.CharField(
        max_length=10,
        choices=NormalBalance.choices,
        editable=False,
    )
    
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    # Hierarchy
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    
    is_header = models.BooleanField(
        default=False,
        help_text="Header accounts group other accounts and cannot receive postings",
    )

    # Metadata - Multilingual
    description = models.TextField(blank=True, default="")
    description_ar = models.TextField(blank=True, default="")
    
    # For MEMO accounts only
    unit_of_measure = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Unit for memo accounts: 'units', 'kg', 'hours', 'sqm', etc.",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="uniq_account_code_per_company",
            )
        ]
        ordering = ["code"]
        indexes = [
            models.Index(fields=["company", "account_type"]),
            models.Index(fields=["company", "parent"]),
            models.Index(fields=["company", "status"]),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_localized_name(self, language: str = "en") -> str:
        """Get name in specified language, fallback to English."""
        if language == "ar" and self.name_ar:
            return self.name_ar
        return self.name

    def get_localized_description(self, language: str = "en") -> str:
        """Get description in specified language, fallback to English."""
        if language == "ar" and self.description_ar:
            return self.description_ar
        return self.description

    @property
    def is_memo_account(self) -> bool:
        """Returns True if this is a memo/statistical account."""
        return self.account_type == self.AccountType.MEMO

    @property
    def is_receivable(self) -> bool:
        """Returns True if this is a receivable account."""
        return self.account_type == self.AccountType.RECEIVABLE

    @property
    def is_payable(self) -> bool:
        """Returns True if this is a payable account."""
        return self.account_type == self.AccountType.PAYABLE

    def clean(self):
        # Validate parent belongs to same company
        if self.parent and self.parent.company_id != self.company_id:
            raise ValidationError("Parent account must belong to the same company.")

        # Validate parent is a header account
        if self.parent and not self.parent.is_header:
            raise ValidationError("Parent account must be a header account.")

        # Validate account type consistency with parent
        if self.parent:
            parent_type = self.parent.account_type
            child_type = self.account_type
            
            # Define allowed parent-child relationships
            allowed = False
            
            # Same type is always allowed
            if parent_type == child_type:
                allowed = True
            # ASSET family relationships
            elif parent_type == self.AccountType.ASSET and child_type in self.ASSET_FAMILY:
                allowed = True
            elif parent_type in self.ASSET_FAMILY and child_type == self.AccountType.CONTRA_ASSET:
                allowed = True
            # LIABILITY family relationships
            elif parent_type == self.AccountType.LIABILITY and child_type in self.LIABILITY_FAMILY:
                allowed = True
            elif parent_type in self.LIABILITY_FAMILY and child_type == self.AccountType.CONTRA_LIABILITY:
                allowed = True
            # EQUITY relationships
            elif parent_type == self.AccountType.EQUITY and child_type == self.AccountType.CONTRA_EQUITY:
                allowed = True
            # REVENUE relationships
            elif parent_type == self.AccountType.REVENUE and child_type == self.AccountType.CONTRA_REVENUE:
                allowed = True
            # EXPENSE relationships
            elif parent_type == self.AccountType.EXPENSE and child_type == self.AccountType.CONTRA_EXPENSE:
                allowed = True
            
            if not allowed:
                raise ValidationError(
                    f"Account type {child_type} cannot be a child of {parent_type}."
                )

        # Validate unit_of_measure only for MEMO accounts
        if self.unit_of_measure and self.account_type != self.AccountType.MEMO:
            raise ValidationError("Unit of measure can only be set for MEMO accounts.")

    def save(self, *args, _projection_write: bool = False, **kwargs):
        """
        Save the account. Only projections should call this directly.

        Args:
            _projection_write: Must be True when called from projections.
                              Prevents accidental direct writes.
        """
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "Account is a read model. Use accounting.commands to modify accounts. "
                "Direct saves are only allowed from projections within projection_writes_allowed()."
            )

        # Auto-set normal balance from account type
        self.normal_balance = self.NORMAL_BALANCE_MAP.get(
            self.account_type,
            self.NormalBalance.DEBIT,
        )
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_postable(self) -> bool:
        """Returns True if this account can receive journal line postings."""
        return not self.is_header and self.status == self.Status.ACTIVE

    @property
    def full_code(self) -> str:
        """Returns the full hierarchical code (e.g., '1000.1100.1110')."""
        if self.parent:
            return f"{self.parent.full_code}.{self.code}"
        return self.code

    def get_ancestors(self) -> list["Account"]:
        """Returns list of ancestor accounts from root to immediate parent."""
        ancestors = []
        current = self.parent
        while current:
            ancestors.insert(0, current)
            current = current.parent
        return ancestors

    def get_descendants(self) -> list["Account"]:
        """Returns all descendant accounts (children, grandchildren, etc.)."""
        descendants = list(self.children.all())
        for child in list(descendants):
            descendants.extend(child.get_descendants())
        return descendants

    def get_balance(self, as_of_date=None) -> Decimal:
        """
        Get account balance from the AccountBalance projection.

        Events are the source of truth. This method reads from the
        materialized projection, NOT from journal tables.

        Args:
            as_of_date: If provided, raises NotImplementedError.
                        Historical balance queries require event replay.

        Returns:
            Current balance from the projection (Decimal)

        Raises:
            NotImplementedError: If as_of_date is specified (use event replay)
        """
        if as_of_date is not None:
            raise NotImplementedError(
                "Historical balance queries require event replay. "
                "Use AccountBalanceProjection.get_balance_as_of(account, date) "
                "or rebuild the projection with a date filter."
            )

        # Import here to avoid circular imports
        from projections.models import AccountBalance

        try:
            projection = AccountBalance.objects.get(
                company=self.company,
                account=self,
            )
            return projection.balance
        except AccountBalance.DoesNotExist:
            return Decimal("0.00")


class JournalEntry(AccountingReadModel):
    """
    Journal Entry header.
    
    Workflow: INCOMPLETE -> DRAFT -> POSTED -> REVERSED
    - INCOMPLETE: Entry being edited, may be unbalanced
    - DRAFT: Entry is complete and balanced, ready for posting
    - POSTED: Entry is finalized, affects account balances
    - REVERSED: Entry has been reversed by another entry
    """

    class Status(models.TextChoices):
        INCOMPLETE = "INCOMPLETE", "Incomplete"
        DRAFT = "DRAFT", "Draft"
        POSTED = "POSTED", "Posted"
        REVERSED = "REVERSED", "Reversed"

    class Kind(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        REVERSAL = "REVERSAL", "Reversal"
        OPENING = "OPENING", "Opening Balance"
        CLOSING = "CLOSING", "Closing Entry"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"

    # Custom manager for projection writes
    objects = ProjectionWriteManager()

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="journal_entries",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    # Entry identification
    entry_number = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Auto-generated or manual entry number",
    )
    
    date = models.DateField()
    
    period = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Fiscal period (1-12 or custom)",
    )
    
    # Multilingual memo
    memo = models.CharField(max_length=255, blank=True, default="")
    memo_ar = models.CharField(max_length=255, blank=True, default="")

    # Currency (transaction vs base)
    currency = models.CharField(
        max_length=3,
        default="USD",
        help_text="Transaction currency for this entry",
    )
    exchange_rate = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("1.0"),
        help_text="Rate to convert entry currency to company base currency",
    )

    # Classification
    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        default=Kind.NORMAL,
    )
    
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.INCOMPLETE,
    )

    # Source tracking (for integrations)
    source_module = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Module that created this entry (e.g., 'inventory', 'payroll')",
    )
    
    source_document = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Reference to source document (e.g., invoice number)",
    )

    # Posting metadata
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="posted_journal_entries",
        db_constraint=False,  # Cross-database FK (User in system DB, JournalEntry in tenant DB)
    )

    # Reversal metadata
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reversed_journal_entries",
        db_constraint=False,  # Cross-database FK (User in system DB, JournalEntry in tenant DB)
    )
    reverses_entry = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reversal_entry",
    )

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_journal_entries",
        db_constraint=False,  # Cross-database FK (User in system DB, JournalEntry in tenant DB)
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company", "date", "id"]),
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "entry_number"]),
        ]
        ordering = ["-date", "-id"]

    def __str__(self):
        num = self.entry_number or f"#{self.id}"
        return f"JE {num} ({self.date}) {self.status}"

    def get_localized_memo(self, language: str = "en") -> str:
        """Get memo in specified language, fallback to English."""
        if language == "ar" and self.memo_ar:
            return self.memo_ar
        return self.memo

    def clean(self):
        if self.reverses_entry_id:
            if self.kind != self.Kind.REVERSAL:
                raise ValidationError("If reverses_entry is set, kind must be REVERSAL.")
            if self.status != self.Status.POSTED:
                raise ValidationError("Reversal entries must be POSTED.")

    def _validate_posting_rules(self):
        """Validate entry can be posted."""
        if self.status != self.Status.DRAFT:
            raise ValidationError("Only DRAFT entries can be posted.")

        if self.kind not in [self.Kind.NORMAL, self.Kind.OPENING, self.Kind.ADJUSTMENT]:
            raise ValidationError(f"Cannot post {self.kind} entries using post().")

        lines_qs = self.lines.select_related("account", "account__company")

        if lines_qs.count() < 2:
            raise ValidationError("Journal entry must have at least 2 lines.")

        # Validate all accounts belong to same company
        if lines_qs.exclude(account__company_id=self.company_id).exists():
            raise ValidationError(
                "All journal lines must use accounts from the same company as the entry."
            )

        # Validate all accounts are postable
        non_postable = lines_qs.filter(
            Q(account__is_header=True) | ~Q(account__status=Account.Status.ACTIVE)
        )
        if non_postable.exists():
            codes = list(non_postable.values_list("account__code", flat=True))
            raise ValidationError(
                f"Cannot post to non-postable accounts: {', '.join(codes)}"
            )

        # Separate financial and memo lines
        financial_lines = lines_qs.exclude(account__account_type=Account.AccountType.MEMO)
        memo_lines = lines_qs.filter(account__account_type=Account.AccountType.MEMO)

        # Validate financial lines balance
        totals = financial_lines.aggregate(
            debit_total=Sum("debit"),
            credit_total=Sum("credit"),
        )

        debit_total = (totals["debit_total"] or Decimal("0.00")).quantize(Decimal("0.01"))
        credit_total = (totals["credit_total"] or Decimal("0.00")).quantize(Decimal("0.01"))

        # Allow entries with only memo lines (no balance required)
        if financial_lines.exists():
            if debit_total == Decimal("0.00") and credit_total == Decimal("0.00"):
                raise ValidationError("Financial totals cannot both be zero.")

            if debit_total != credit_total:
                raise ValidationError(
                    f"Entry is not balanced. Debit={debit_total} Credit={credit_total}"
                )

        # Per-line validation (applies to both financial and memo)
        for ln in lines_qs:
            if ln.debit < 0 or ln.credit < 0:
                raise ValidationError("Debit/Credit cannot be negative.")
            if ln.debit == 0 and ln.credit == 0:
                raise ValidationError("A line cannot have both debit and credit = 0.")
            if ln.debit > 0 and ln.credit > 0:
                raise ValidationError("A line cannot have both debit and credit.")

    def save(self, *args, _projection_write: bool = False, **kwargs):
        """
        Save the journal entry. Only projections should call this directly.

        Args:
            _projection_write: Must be True when called from projections.
                              Prevents accidental direct writes.

        Note:
            This method only enforces TRUE INVARIANTS (always true regardless
            of workflow stage). Workflow rules (status transitions, header
            immutability after posting) are enforced by policies and commands.

        Invariants enforced:
            - If reverses_entry is set, kind MUST be REVERSAL
            - If reverses_entry is set, status MUST be POSTED
        """
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "JournalEntry is a read model. Use accounting.commands to modify entries. "
                "Direct saves are only allowed from projections within projection_writes_allowed()."
            )

        # TRUE INVARIANT: reversal entries must have correct kind and status
        # This is always true - a reversal entry by definition must be:
        # 1. Kind = REVERSAL
        # 2. Status = POSTED (reversals are created already posted)
        if self.reverses_entry_id:
            if self.kind != self.Kind.REVERSAL:
                raise ValidationError("If reverses_entry is set, kind must be REVERSAL.")
            if self.status != self.Status.POSTED:
                raise ValidationError("Reversal entries must be POSTED.")

        # Note: Workflow rules (status transitions, header immutability) are NOT
        # enforced here. They are enforced by:
        # - accounting.policies.validate_status_transition()
        # - accounting.policies.can_modify_entry_header()
        # - accounting.commands (which call policies before emitting events)
        #
        # This separation ensures:
        # - Models enforce invariants (always true)
        # - Policies enforce workflow (depends on stage)
        # - No hidden behavior changes when projections update models

        super().save(*args, **kwargs)

    @transaction.atomic
    def post(self, user):
        raise ValueError("Use accounting.commands.post_journal_entry to post entries.")

    @property
    def total_debit(self) -> Decimal:
        """Sum of all debit amounts (excluding memo accounts)."""
        return self.lines.exclude(
            account__account_type=Account.AccountType.MEMO
        ).aggregate(total=Sum("debit"))["total"] or Decimal("0.00")

    @property
    def total_credit(self) -> Decimal:
        """Sum of all credit amounts (excluding memo accounts)."""
        return self.lines.exclude(
            account__account_type=Account.AccountType.MEMO
        ).aggregate(total=Sum("credit"))["total"] or Decimal("0.00")

    @property
    def is_balanced(self) -> bool:
        """Check if debits equal credits (excluding memo accounts)."""
        return self.total_debit == self.total_credit


class JournalLine(AccountingReadModel):
    """
    Individual line within a journal entry.
    Each line affects one account with either a debit or credit amount.
    """

    # Custom manager for projection writes
    objects = ProjectionWriteManager()

    entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.CASCADE,
        related_name="lines",
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="journal_lines",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    
    line_no = models.PositiveIntegerField()
    
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="journal_lines",
    )
    
    # Multilingual description
    description = models.CharField(max_length=255, blank=True, default="")
    description_ar = models.CharField(max_length=255, blank=True, default="")
    
    debit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # Transaction currency details (optional)
    amount_currency = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
    )
    currency = models.CharField(
        max_length=3,
        blank=True,
        default="",
    )
    exchange_rate = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )

    class Meta:
        unique_together = ("entry", "line_no")
        ordering = ["entry", "line_no"]
        constraints = [
            models.CheckConstraint(
                check=~(Q(debit__gt=0) & Q(credit__gt=0)),
                name="chk_line_not_both_debit_credit",
            ),
            models.CheckConstraint(
                check=~(Q(debit__exact=0) & Q(credit__exact=0)),
                name="chk_line_not_both_zero",
            ),
            models.CheckConstraint(
                check=Q(debit__gte=0) & Q(credit__gte=0),
                name="chk_line_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "entry"]),
        ]

    def __str__(self):
        return f"JE#{self.entry_id} L{self.line_no}"

    def get_localized_description(self, language: str = "en") -> str:
        """Get description in specified language, fallback to English."""
        if language == "ar" and self.description_ar:
            return self.description_ar
        return self.description

    def save(self, *args, _projection_write: bool = False, **kwargs):
        """
        Save the journal line. Only projections should call this directly.

        Args:
            _projection_write: Must be True when called from projections.
                              Prevents accidental direct writes.

        Note:
            This method only enforces the projection-write guard.
            Workflow rules (e.g., "cannot modify lines after posting") are
            enforced by accounting.policies.can_modify_entry_lines() and
            the command layer.

            JournalLine has no model-level invariants beyond what the
            database constraints enforce (non-negative amounts, not both zero).
        """
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "JournalLine is a read model. Use accounting.commands to modify lines. "
                "Direct saves are only allowed from projections within projection_writes_allowed()."
            )

        # Note: Workflow rule "cannot modify lines for posted entries" is NOT
        # enforced here. It's enforced by:
        # - accounting.policies.can_modify_entry_lines()
        # - accounting.commands (which call policies before emitting events)
        #
        # Projections may need to create lines for posted entries (e.g., when
        # processing JOURNAL_ENTRY_POSTED events), so we don't block that here.

        if self.entry_id and self.company_id and self.entry.company_id != self.company_id:
            raise ValidationError("JournalLine company must match entry company.")
        if self.account_id and self.company_id and self.account.company_id != self.company_id:
            raise ValidationError("JournalLine company must match account company.")

        super().save(*args, **kwargs)

    @property
    def amount(self) -> Decimal:
        """Returns the non-zero amount (debit or credit)."""
        return self.debit if self.debit > 0 else self.credit

    @property
    def is_debit(self) -> bool:
        """Returns True if this is a debit line."""
        return self.debit > 0

    @property
    def is_memo_line(self) -> bool:
        """Returns True if this line is for a memo/statistical account."""
        return self.account.is_memo_account if self.account_id else False


# =============================================================================
# Analysis Dimensions
# =============================================================================

class AnalysisDimension(AccountingReadModel):
    """
    User-defined analysis dimension.
    Each company can create their own dimensions for cost tracking,
    project accounting, departmental reporting, etc.

    Examples: Cost Center, Project, Department, Location, Customer Segment
    """

    # Custom manager for projection writes
    objects = ProjectionWriteManager()

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="analysis_dimensions",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    
    code = models.CharField(
        max_length=20,
        help_text="Short code: COST_CENTER, PROJECT, DEPT",
    )
    
    # Multilingual names
    name = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100, blank=True, default="")
    
    description = models.TextField(blank=True, default="")
    description_ar = models.TextField(blank=True, default="")
    
    # Configuration
    is_required_on_posting = models.BooleanField(
        default=False,
        help_text="If True, lines must have this dimension when posting",
    )
    
    is_active = models.BooleanField(default=True)
    
    # Which account types require this dimension?
    # Empty list = applies to all account types
    applies_to_account_types = models.JSONField(
        default=list,
        blank=True,
        help_text='e.g., ["EXPENSE", "REVENUE"] or [] for all',
    )
    
    # Ordering for UI display
    display_order = models.PositiveSmallIntegerField(default=0)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="uniq_dimension_code_per_company",
            ),
        ]
        ordering = ["display_order", "code"]
        verbose_name = "Analysis Dimension"
        verbose_name_plural = "Analysis Dimensions"

    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_localized_name(self, language: str = "en") -> str:
        """Get name in specified language, fallback to English."""
        if language == "ar" and self.name_ar:
            return self.name_ar
        return self.name

    def applies_to_account(self, account: Account) -> bool:
        """Check if this dimension applies to the given account type."""
        if not self.applies_to_account_types:
            return True  # Empty list = applies to all
        return account.account_type in self.applies_to_account_types

    def save(self, *args, _projection_write: bool = False, **kwargs):
        """
        Save the dimension. Only projections should call this directly.

        Args:
            _projection_write: Must be True when called from projections.
                              Prevents accidental direct writes.
        """
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "AnalysisDimension is a read model. Use accounting.commands to modify. "
                "Direct saves are only allowed from projections within projection_writes_allowed()."
            )
        super().save(*args, **kwargs)


class AnalysisDimensionValue(AccountingReadModel):
    """
    Values within a dimension.
    e.g., Dimension "Cost Center" has values "Sales", "IT", "HR"
    
    Supports hierarchical structure for drill-down reporting.
    e.g., IT > Infrastructure > Servers
    """

    # Custom manager for projection writes
    objects = ProjectionWriteManager()

    dimension = models.ForeignKey(
        AnalysisDimension,
        on_delete=models.CASCADE,
        related_name="values",
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="analysis_dimension_values",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    
    code = models.CharField(max_length=20)
    
    # Multilingual names
    name = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100, blank=True, default="")
    
    description = models.TextField(blank=True, default="")
    description_ar = models.TextField(blank=True, default="")
    
    # Hierarchical structure
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    
    is_active = models.BooleanField(default=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dimension", "code"],
                name="uniq_dimension_value_code",
            ),
        ]
        ordering = ["dimension", "code"]
        indexes = [
            models.Index(fields=["company", "dimension"]),
        ]
        verbose_name = "Analysis Dimension Value"
        verbose_name_plural = "Analysis Dimension Values"

    def __str__(self):
        return f"{self.dimension.code}:{self.code} - {self.name}"

    def get_localized_name(self, language: str = "en") -> str:
        """Get name in specified language, fallback to English."""
        if language == "ar" and self.name_ar:
            return self.name_ar
        return self.name

    @property
    def full_path(self) -> str:
        """Returns the full hierarchical path (e.g., 'IT > Infrastructure > Servers')."""
        if self.parent:
            return f"{self.parent.full_path} > {self.name}"
        return self.name

    def get_ancestors(self) -> list["AnalysisDimensionValue"]:
        """Returns list of ancestor values from root to immediate parent."""
        ancestors = []
        current = self.parent
        while current:
            ancestors.insert(0, current)
            current = current.parent
        return ancestors

    def get_descendants(self) -> list["AnalysisDimensionValue"]:
        """Returns all descendant values."""
        descendants = list(self.children.all())
        for child in list(descendants):
            descendants.extend(child.get_descendants())
        return descendants

    def save(self, *args, _projection_write: bool = False, **kwargs):
        """
        Save the dimension value. Only projections should call this directly.

        Args:
            _projection_write: Must be True when called from projections.
                              Prevents accidental direct writes.
        """
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "AnalysisDimensionValue is a read model. Use accounting.commands to modify. "
                "Direct saves are only allowed from projections within projection_writes_allowed()."
            )
        if self.dimension_id and self.company_id and self.dimension.company_id != self.company_id:
            raise ValidationError("AnalysisDimensionValue company must match dimension company.")
        super().save(*args, **kwargs)


class JournalLineAnalysis(AccountingReadModel):
    """
    Analysis tags on journal lines.
    Each line can have one value per dimension.

    This enables multi-dimensional reporting:
    - By Cost Center
    - By Project
    - By Cost Center AND Project
    """

    # Custom manager for projection writes
    objects = ProjectionWriteManager()

    journal_line = models.ForeignKey(
        JournalLine,
        on_delete=models.CASCADE,
        related_name="analysis_tags",
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="journal_line_analysis",
    )
    
    dimension = models.ForeignKey(
        AnalysisDimension,
        on_delete=models.PROTECT,
    )
    
    dimension_value = models.ForeignKey(
        AnalysisDimensionValue,
        on_delete=models.PROTECT,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["journal_line", "dimension"],
                name="uniq_line_dimension",  # One value per dimension per line
            ),
        ]
        indexes = [
            models.Index(fields=["company", "journal_line"]),
            models.Index(fields=["dimension_value"]),
            models.Index(fields=["dimension", "dimension_value"]),
        ]
        verbose_name = "Journal Line Analysis"
        verbose_name_plural = "Journal Line Analyses"

    def __str__(self):
        return f"Line {self.journal_line_id}: {self.dimension.code}={self.dimension_value.code}"

    def clean(self):
        # Validate that dimension_value belongs to the dimension
        if self.dimension_value.dimension_id != self.dimension_id:
            raise ValidationError(
                f"Value '{self.dimension_value.code}' does not belong to dimension '{self.dimension.code}'."
            )

    def save(self, *args, _projection_write: bool = False, **kwargs):
        """
        Save the analysis tag. Only projections should call this directly.

        Args:
            _projection_write: Must be True when called from projections.
                              Prevents accidental direct writes.
        """
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "JournalLineAnalysis is a read model. Use accounting.commands to modify. "
                "Direct saves are only allowed from projections within projection_writes_allowed()."
            )
        if self.journal_line_id and self.company_id and self.journal_line.company_id != self.company_id:
            raise ValidationError("JournalLineAnalysis company must match journal line company.")
        if self.dimension_id and self.company_id and self.dimension.company_id != self.company_id:
            raise ValidationError("JournalLineAnalysis company must match dimension company.")
        if self.dimension_value_id and self.company_id and self.dimension_value.company_id != self.company_id:
            raise ValidationError("JournalLineAnalysis company must match dimension value company.")
        super().save(*args, **kwargs)


class AccountAnalysisDefault(AccountingReadModel):
    """
    Default analysis values for an account.
    When posting to this account, auto-fill these dimensions.

    e.g., "Salaries Expense" account defaults to "HR" cost center,
    but can be overridden per transaction.
    """

    # Custom manager for projection writes
    objects = ProjectionWriteManager()

    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="analysis_defaults",
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="account_analysis_defaults",
    )
    
    dimension = models.ForeignKey(
        AnalysisDimension,
        on_delete=models.CASCADE,
    )
    
    default_value = models.ForeignKey(
        AnalysisDimensionValue,
        on_delete=models.CASCADE,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["account", "dimension"],
                name="uniq_account_dimension_default",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "account"]),
        ]
        verbose_name = "Account Analysis Default"
        verbose_name_plural = "Account Analysis Defaults"

    def __str__(self):
        return f"{self.account.code}: {self.dimension.code}={self.default_value.code}"

    def clean(self):
        # Validate that default_value belongs to the dimension
        if self.default_value.dimension_id != self.dimension_id:
            raise ValidationError(
                f"Value '{self.default_value.code}' does not belong to dimension '{self.dimension.code}'."
            )

        # Validate dimension applies to this account type
        if not self.dimension.applies_to_account(self.account):
            raise ValidationError(
                f"Dimension '{self.dimension.code}' does not apply to account type '{self.account.account_type}'."
            )

    def save(self, *args, _projection_write: bool = False, **kwargs):
        """
        Save the default. Only projections should call this directly.

        Args:
            _projection_write: Must be True when called from projections.
                              Prevents accidental direct writes.
        """
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "AccountAnalysisDefault is a read model. Use accounting.commands to modify. "
                "Direct saves are only allowed from projections within projection_writes_allowed()."
            )
        if self.account_id and self.company_id and self.account.company_id != self.company_id:
            raise ValidationError("AccountAnalysisDefault company must match account company.")
        if self.dimension_id and self.company_id and self.dimension.company_id != self.company_id:
            raise ValidationError("AccountAnalysisDefault company must match dimension company.")
        if self.default_value_id and self.company_id and self.default_value.company_id != self.company_id:
            raise ValidationError("AccountAnalysisDefault company must match value company.")
        super().save(*args, **kwargs)
