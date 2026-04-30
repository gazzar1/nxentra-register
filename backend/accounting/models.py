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

import logging
import uuid
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum

from projections.write_barrier import write_context_allowed


class ProjectionWriteQuerySet(models.QuerySet):
    """
    Custom QuerySet that supports projection writes.

    Projections should use .projection() to get a manager that allows writes.
    """

    def __init__(self, *args, _projection_write: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._projection_write = _projection_write

    def _clone(self):  # type: ignore[override]
        c = super()._clone()  # type: ignore[misc]
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
                obj, created = self._update_or_create_impl(defaults, create_defaults, **kwargs)
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
        if not write_context_allowed({"command", "migration", "bootstrap", "admin_emergency"}) and not getattr(
            settings, "TESTING", False
        ):
            raise RuntimeError(
                "CompanySequence is a command-owned write model. "
                "Direct saves are only allowed within command_writes_allowed()."
            )
        super().save(*args, **kwargs)


class Account(AccountingReadModel):
    """
    Chart of Accounts entry.

    Architecture (5-Type + Role + Ledger Domain):
    =============================================
    - account_type: 5 core types (ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE)
    - role: Behavioral classification that determines derived properties
    - ledger_domain: FINANCIAL, STATISTICAL, or OFF_BALANCE

    Derived properties (computed from role):
    - normal_balance: Debit or Credit
    - requires_counterparty: True for AR/AP control accounts
    - counterparty_kind: CUSTOMER or VENDOR
    - allow_manual_posting: False for control accounts by default

    Supports:
    - Hierarchical structure (parent/child)
    - Header accounts (non-postable groupings)
    - Soft status (ACTIVE/INACTIVE/LOCKED)
    - Multilingual names (English/Arabic)
    - Statistical/Off-balance accounts (separate from financial ledger)
    """

    class AccountType(models.TextChoices):
        """
        5 core account types - the accounting ontology.

        All behavior is determined by the combination of type + role.
        Old types (RECEIVABLE, PAYABLE, CONTRA_*, MEMO) are migrated to
        the appropriate type + role combination.
        """

        ASSET = "ASSET", "Asset"
        LIABILITY = "LIABILITY", "Liability"
        EQUITY = "EQUITY", "Equity"
        REVENUE = "REVENUE", "Revenue"
        EXPENSE = "EXPENSE", "Expense"
        # Legacy values kept for migration compatibility
        # TODO: Remove after data migration is complete
        RECEIVABLE = "RECEIVABLE", "Accounts Receivable (Legacy)"
        CONTRA_ASSET = "CONTRA_ASSET", "Contra Asset (Legacy)"
        PAYABLE = "PAYABLE", "Accounts Payable (Legacy)"
        CONTRA_LIABILITY = "CONTRA_LIABILITY", "Contra Liability (Legacy)"
        CONTRA_EQUITY = "CONTRA_EQUITY", "Contra Equity (Legacy)"
        CONTRA_REVENUE = "CONTRA_REVENUE", "Contra Revenue (Legacy)"
        CONTRA_EXPENSE = "CONTRA_EXPENSE", "Contra Expense (Legacy)"
        MEMO = "MEMO", "Memo/Statistical (Legacy)"

    class AccountRole(models.TextChoices):
        """
        Behavioral classification within a type.

        Role determines derived properties like normal_balance,
        requires_counterparty, and allow_manual_posting.
        """

        # Asset roles
        ASSET_GENERAL = "ASSET_GENERAL", "General Asset"
        LIQUIDITY = "LIQUIDITY", "Cash/Bank"
        RECEIVABLE_CONTROL = "RECEIVABLE_CONTROL", "Accounts Receivable Control"
        INVENTORY_VALUE = "INVENTORY_VALUE", "Inventory Value"
        PREPAID = "PREPAID", "Prepaid Expense"
        FIXED_ASSET_COST = "FIXED_ASSET_COST", "Fixed Asset Cost"
        ACCUM_DEPRECIATION = "ACCUM_DEPRECIATION", "Accumulated Depreciation"
        OTHER_ASSET = "OTHER_ASSET", "Other Asset"

        # Liability roles
        LIABILITY_GENERAL = "LIABILITY_GENERAL", "General Liability"
        PAYABLE_CONTROL = "PAYABLE_CONTROL", "Accounts Payable Control"
        ACCRUED_EXPENSE = "ACCRUED_EXPENSE", "Accrued Expense"
        DEFERRED_REVENUE = "DEFERRED_REVENUE", "Deferred Revenue"
        TAX_PAYABLE = "TAX_PAYABLE", "Tax Payable"
        LOAN = "LOAN", "Loan/Borrowing"
        OTHER_LIABILITY = "OTHER_LIABILITY", "Other Liability"

        # Equity roles
        CAPITAL = "CAPITAL", "Capital"
        RETAINED_EARNINGS = "RETAINED_EARNINGS", "Retained Earnings"
        CURRENT_YEAR_EARNINGS = "CURRENT_YEAR_EARNINGS", "Current Year Earnings"
        DRAWINGS = "DRAWINGS", "Drawings/Distributions"
        RESERVE = "RESERVE", "Reserve"
        OTHER_EQUITY = "OTHER_EQUITY", "Other Equity"

        # Revenue roles
        SALES = "SALES", "Sales Revenue"
        SERVICE = "SERVICE", "Service Revenue"
        OTHER_INCOME = "OTHER_INCOME", "Other Income"
        FINANCIAL_INCOME = "FINANCIAL_INCOME", "Financial Income"
        FX_ROUNDING = "FX_ROUNDING", "FX Rounding Differences"
        CONTRA_REVENUE = "CONTRA_REVENUE", "Contra Revenue"

        # Expense roles
        COGS = "COGS", "Cost of Goods Sold"
        OPERATING_EXPENSE = "OPERATING_EXPENSE", "Operating Expense"
        ADMIN_EXPENSE = "ADMIN_EXPENSE", "Administrative Expense"
        FINANCIAL_EXPENSE = "FINANCIAL_EXPENSE", "Financial Expense"
        DEPRECIATION_EXPENSE = "DEPRECIATION_EXPENSE", "Depreciation Expense"
        TAX_EXPENSE = "TAX_EXPENSE", "Tax Expense"
        OTHER_EXPENSE = "OTHER_EXPENSE", "Other Expense"

        # Statistical/Off-balance roles
        STAT_GENERAL = "STAT_GENERAL", "Statistical General"
        STAT_INVENTORY_QTY = "STAT_INVENTORY_QTY", "Inventory Quantity"
        STAT_PRODUCTION_QTY = "STAT_PRODUCTION_QTY", "Production Quantity"
        OBS_GENERAL = "OBS_GENERAL", "Off-Balance General"
        OBS_CONTINGENT = "OBS_CONTINGENT", "Contingent Liability"

    class LedgerDomain(models.TextChoices):
        """
        Which ledger this account belongs to.

        FINANCIAL: Affects trial balance, P&L, balance sheet
        STATISTICAL: Quantity tracking only, no financial impact
        OFF_BALANCE: Off-balance-sheet items (contingencies, commitments)
        """

        FINANCIAL = "FINANCIAL", "Financial"
        STATISTICAL = "STATISTICAL", "Statistical"
        OFF_BALANCE = "OFF_BALANCE", "Off-Balance Sheet"

    class NormalBalance(models.TextChoices):
        DEBIT = "DEBIT", "Debit"
        CREDIT = "CREDIT", "Credit"
        NONE = "NONE", "None"  # For statistical/off-balance accounts

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        LOCKED = "LOCKED", "Locked"  # Has transactions, cannot delete

    # Roles that require counterparty
    CONTROL_ACCOUNT_ROLES = {
        AccountRole.RECEIVABLE_CONTROL: "CUSTOMER",
        AccountRole.PAYABLE_CONTROL: "VENDOR",
    }

    # Roles with contra behavior (opposite normal balance)
    CONTRA_ASSET_ROLES = {AccountRole.ACCUM_DEPRECIATION}
    CONTRA_REVENUE_ROLES = {AccountRole.CONTRA_REVENUE}

    # Statistical/off-balance roles
    STAT_ROLES = {
        AccountRole.STAT_GENERAL,
        AccountRole.STAT_INVENTORY_QTY,
        AccountRole.STAT_PRODUCTION_QTY,
        AccountRole.OBS_GENERAL,
        AccountRole.OBS_CONTINGENT,
    }

    # Map account types to their default normal balance (used if role not set)
    NORMAL_BALANCE_MAP = {
        AccountType.ASSET: NormalBalance.DEBIT,
        AccountType.LIABILITY: NormalBalance.CREDIT,
        AccountType.EQUITY: NormalBalance.CREDIT,
        AccountType.REVENUE: NormalBalance.CREDIT,
        AccountType.EXPENSE: NormalBalance.DEBIT,
        # Legacy types (for backward compatibility during migration)
        AccountType.RECEIVABLE: NormalBalance.DEBIT,
        AccountType.CONTRA_ASSET: NormalBalance.CREDIT,
        AccountType.PAYABLE: NormalBalance.CREDIT,
        AccountType.CONTRA_LIABILITY: NormalBalance.DEBIT,
        AccountType.CONTRA_EQUITY: NormalBalance.DEBIT,
        AccountType.CONTRA_REVENUE: NormalBalance.DEBIT,
        AccountType.CONTRA_EXPENSE: NormalBalance.CREDIT,
        AccountType.MEMO: NormalBalance.NONE,
    }

    # Valid roles per account type
    VALID_ROLES_BY_TYPE = {
        AccountType.ASSET: {
            AccountRole.ASSET_GENERAL,
            AccountRole.LIQUIDITY,
            AccountRole.RECEIVABLE_CONTROL,
            AccountRole.INVENTORY_VALUE,
            AccountRole.PREPAID,
            AccountRole.FIXED_ASSET_COST,
            AccountRole.ACCUM_DEPRECIATION,
            AccountRole.OTHER_ASSET,
            AccountRole.STAT_GENERAL,
            AccountRole.STAT_INVENTORY_QTY,
            AccountRole.STAT_PRODUCTION_QTY,
        },
        AccountType.LIABILITY: {
            AccountRole.LIABILITY_GENERAL,
            AccountRole.PAYABLE_CONTROL,
            AccountRole.ACCRUED_EXPENSE,
            AccountRole.DEFERRED_REVENUE,
            AccountRole.TAX_PAYABLE,
            AccountRole.LOAN,
            AccountRole.OTHER_LIABILITY,
            AccountRole.OBS_GENERAL,
            AccountRole.OBS_CONTINGENT,
        },
        AccountType.EQUITY: {
            AccountRole.CAPITAL,
            AccountRole.RETAINED_EARNINGS,
            AccountRole.CURRENT_YEAR_EARNINGS,
            AccountRole.DRAWINGS,
            AccountRole.RESERVE,
            AccountRole.OTHER_EQUITY,
        },
        AccountType.REVENUE: {
            AccountRole.SALES,
            AccountRole.SERVICE,
            AccountRole.OTHER_INCOME,
            AccountRole.FINANCIAL_INCOME,
            AccountRole.CONTRA_REVENUE,
        },
        AccountType.EXPENSE: {
            AccountRole.COGS,
            AccountRole.OPERATING_EXPENSE,
            AccountRole.ADMIN_EXPENSE,
            AccountRole.FINANCIAL_EXPENSE,
            AccountRole.DEPRECIATION_EXPENSE,
            AccountRole.TAX_EXPENSE,
            AccountRole.OTHER_EXPENSE,
            AccountRole.FX_ROUNDING,
        },
    }

    # Default role per account type (for new accounts)
    DEFAULT_ROLE_BY_TYPE = {
        AccountType.ASSET: AccountRole.ASSET_GENERAL,
        AccountType.LIABILITY: AccountRole.LIABILITY_GENERAL,
        AccountType.EQUITY: AccountRole.CAPITAL,
        AccountType.REVENUE: AccountRole.SALES,
        AccountType.EXPENSE: AccountRole.OPERATING_EXPENSE,
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

    # Role determines behavioral properties (normal_balance, requires_counterparty, etc.)
    role = models.CharField(
        max_length=30,
        choices=AccountRole.choices,
        blank=True,
        default="",
        help_text="Behavioral role that determines derived properties",
    )

    # Ledger domain separates financial from statistical/off-balance accounts
    ledger_domain = models.CharField(
        max_length=15,
        choices=LedgerDomain.choices,
        default=LedgerDomain.FINANCIAL,
        help_text="Financial, Statistical, or Off-Balance ledger",
    )

    normal_balance = models.CharField(
        max_length=10,
        choices=NormalBalance.choices,
        editable=False,
    )

    # Derived flags (computed from role, stored for query performance)
    requires_counterparty = models.BooleanField(
        default=False,
        editable=False,
        help_text="True for AR/AP control accounts (derived from role)",
    )

    counterparty_kind = models.CharField(
        max_length=10,
        blank=True,
        default="",
        editable=False,
        help_text="CUSTOMER or VENDOR for control accounts (derived from role)",
    )

    allow_manual_posting = models.BooleanField(
        default=True,
        help_text="False for control accounts (system-only posting by default)",
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

    # For statistical/off-balance accounts (required for STATISTICAL and OFF_BALANCE domains)
    unit_of_measure = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Unit for statistical accounts: 'units', 'kg', 'L', 'hours', 'sqm', etc.",
    )

    # System protection flag for seeded accounts
    is_system_protected = models.BooleanField(
        default=False,
        help_text="Protected accounts: type/role/domain locked, cannot delete once has transactions",
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
            models.Index(fields=["company", "role"]),
            models.Index(fields=["company", "ledger_domain"]),
            models.Index(fields=["company", "requires_counterparty"]),
            models.Index(fields=["company", "is_system_protected"]),
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
        # New architecture: check ledger_domain or legacy MEMO type
        if self.ledger_domain in (self.LedgerDomain.STATISTICAL, self.LedgerDomain.OFF_BALANCE):
            return True
        return self.account_type == self.AccountType.MEMO

    @property
    def is_statistical(self) -> bool:
        """Returns True if this is a statistical (non-financial) account."""
        return self.ledger_domain == self.LedgerDomain.STATISTICAL

    @property
    def is_off_balance(self) -> bool:
        """Returns True if this is an off-balance sheet account."""
        return self.ledger_domain == self.LedgerDomain.OFF_BALANCE

    @property
    def is_financial(self) -> bool:
        """Returns True if this account affects financial statements."""
        return self.ledger_domain == self.LedgerDomain.FINANCIAL

    @property
    def is_receivable(self) -> bool:
        """Returns True if this is a receivable control account."""
        # New architecture: check role
        if self.role == self.AccountRole.RECEIVABLE_CONTROL:
            return True
        # Legacy: check old type
        return self.account_type == self.AccountType.RECEIVABLE

    @property
    def is_payable(self) -> bool:
        """Returns True if this is a payable control account."""
        # New architecture: check role
        if self.role == self.AccountRole.PAYABLE_CONTROL:
            return True
        # Legacy: check old type
        return self.account_type == self.AccountType.PAYABLE

    @property
    def is_control_account(self) -> bool:
        """Returns True if this is an AR/AP control account."""
        return self.role in (self.AccountRole.RECEIVABLE_CONTROL, self.AccountRole.PAYABLE_CONTROL)

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
            if (
                parent_type == child_type
                or (parent_type == self.AccountType.ASSET and child_type in self.ASSET_FAMILY)
                or (parent_type in self.ASSET_FAMILY and child_type == self.AccountType.CONTRA_ASSET)
                or (parent_type == self.AccountType.LIABILITY and child_type in self.LIABILITY_FAMILY)
                or (parent_type in self.LIABILITY_FAMILY and child_type == self.AccountType.CONTRA_LIABILITY)
                or (parent_type == self.AccountType.EQUITY and child_type == self.AccountType.CONTRA_EQUITY)
                or (parent_type == self.AccountType.REVENUE and child_type == self.AccountType.CONTRA_REVENUE)
                or (parent_type == self.AccountType.EXPENSE and child_type == self.AccountType.CONTRA_EXPENSE)
            ):
                allowed = True

            if not allowed:
                raise ValidationError(f"Account type {child_type} cannot be a child of {parent_type}.")

        # Validate type/role combination (if role is set)
        if self.role:
            from .behaviors import validate_type_role_combination

            is_valid, error_msg = validate_type_role_combination(self.account_type, self.role)
            if not is_valid:
                raise ValidationError(error_msg)

        # Validate unit_of_measure for statistical/off-balance accounts
        if self.ledger_domain in (self.LedgerDomain.STATISTICAL, self.LedgerDomain.OFF_BALANCE):
            if not self.unit_of_measure:
                raise ValidationError("Unit of measure is required for statistical and off-balance accounts.")
        # Legacy: validate for MEMO type
        elif self.unit_of_measure and self.account_type != self.AccountType.MEMO:
            if self.role not in self.STAT_ROLES:
                raise ValidationError("Unit of measure can only be set for statistical/off-balance accounts.")

    def save(self, *args, _projection_write: bool = False, **kwargs):
        """
        Save the account. Only projections should call this directly.

        Args:
            _projection_write: Must be True when called from projections.
                              Prevents accidental direct writes.

        Derived fields (normal_balance, requires_counterparty, etc.) are
        automatically computed from (account_type, role, ledger_domain).
        """
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "Account is a read model. Use accounting.commands to modify accounts. "
                "Direct saves are only allowed from projections within projection_writes_allowed()."
            )

        # Apply derived fields from role and type
        from .behaviors import apply_derived_fields

        apply_derived_fields(self)

        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        Delete the account with system protection validation.

        System-protected accounts with transactions cannot be deleted.
        This prevents data integrity issues in the event history.
        """
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "Account is a read model. "
                "Direct deletes are only allowed from projections within projection_writes_allowed()."
            )

        # System-protected accounts with transactions cannot be deleted
        if self.is_system_protected and self.has_transactions:
            raise ValidationError(
                f"Cannot delete system-protected account '{self.code}' that has transactions. "
                "Mark it as INACTIVE instead."
            )

        return super().delete(*args, **kwargs)

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

    @property
    def has_transactions(self) -> bool:
        """
        Check if this account has any posted journal lines.

        Used to determine if system-protected accounts can be deleted.
        """
        return self.journal_lines.filter(entry__status="POSTED").exists()

    def get_ancestors(self) -> list["Account"]:
        """Returns list of ancestor accounts from root to immediate parent."""
        ancestors: list[Account] = []
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


# =============================================================================
# Counterparty Models (AR/AP Subledgers)
# =============================================================================


class Customer(AccountingReadModel):
    """
    Customer entity for Accounts Receivable subledger.

    Customers are NOT chart of accounts entries. They are counterparties
    linked to AR control accounts for subledger tracking.

    The subledger invariant:
        AR Control Account Balance = Sum of all Customer Balances

    This is enforced by the projection layer, not database constraints.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        BLOCKED = "BLOCKED", "Blocked"  # Cannot create new receivables

    # Custom manager for projection writes
    objects = ProjectionWriteManager()

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="customers",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    # Customer identification
    code = models.CharField(
        max_length=20,
        help_text="Customer code (e.g., CUST001)",
    )

    # Multilingual names
    name = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255, blank=True, default="")

    # Default AR control account for this customer
    default_ar_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="customers",
        null=True,
        blank=True,
        help_text="Default AR control account. Must have role=RECEIVABLE_CONTROL",
    )

    # Contact information
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    address = models.TextField(blank=True, default="")
    address_ar = models.TextField(blank=True, default="")

    # Credit management
    credit_limit = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Maximum credit allowed (null = unlimited)",
    )

    # Payment terms (days)
    payment_terms_days = models.PositiveIntegerField(
        default=30,
        help_text="Default payment terms in days",
    )

    # Preferred currency for transactions
    currency = models.CharField(
        max_length=3,
        default="USD",
        help_text="Preferred transaction currency",
    )

    # Tax identification
    tax_id = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Tax identification number (VAT, TIN, etc.)",
    )

    # Status
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    # Notes - Multilingual
    notes = models.TextField(blank=True, default="")
    notes_ar = models.TextField(blank=True, default="")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="uniq_customer_code_per_company",
            ),
        ]
        ordering = ["code"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "name"]),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_localized_name(self, language: str = "en") -> str:
        """Get name in specified language, fallback to English."""
        if language == "ar" and self.name_ar:
            return self.name_ar
        return self.name

    def get_localized_address(self, language: str = "en") -> str:
        """Get address in specified language, fallback to English."""
        if language == "ar" and self.address_ar:
            return self.address_ar
        return self.address

    def clean(self):
        # Validate default_ar_account is a receivable control account
        if self.default_ar_account:
            if self.default_ar_account.company_id != self.company_id:
                raise ValidationError("Default AR account must belong to the same company.")
            if self.default_ar_account.role != Account.AccountRole.RECEIVABLE_CONTROL:
                raise ValidationError("Default AR account must have role=RECEIVABLE_CONTROL.")

    def save(self, *args, _projection_write: bool = False, **kwargs):
        """
        Save the customer. Only projections should call this directly.

        Args:
            _projection_write: Must be True when called from projections.
                              Prevents accidental direct writes.
        """
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "Customer is a read model. Use accounting.commands to modify. "
                "Direct saves are only allowed from projections within projection_writes_allowed()."
            )
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        """Returns True if customer can receive new transactions."""
        return self.status == self.Status.ACTIVE

    def get_balance(self) -> Decimal:
        """
        Get customer balance from the CustomerBalance projection.

        Returns:
            Current balance from the projection (Decimal)
        """
        # Import here to avoid circular imports
        from projections.models import CustomerBalance

        try:
            projection = CustomerBalance.objects.get(
                company=self.company,
                customer=self,
            )
            return projection.balance
        except CustomerBalance.DoesNotExist:
            return Decimal("0.00")


class Vendor(AccountingReadModel):
    """
    Vendor entity for Accounts Payable subledger.

    Vendors are NOT chart of accounts entries. They are counterparties
    linked to AP control accounts for subledger tracking.

    The subledger invariant:
        AP Control Account Balance = Sum of all Vendor Balances

    This is enforced by the projection layer, not database constraints.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        BLOCKED = "BLOCKED", "Blocked"  # Cannot create new payables

    # Custom manager for projection writes
    objects = ProjectionWriteManager()

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="vendors",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    # Vendor identification
    code = models.CharField(
        max_length=20,
        help_text="Vendor code (e.g., VEND001)",
    )

    # Multilingual names
    name = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255, blank=True, default="")

    # Default AP control account for this vendor
    default_ap_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="vendors",
        null=True,
        blank=True,
        help_text="Default AP control account. Must have role=PAYABLE_CONTROL",
    )

    # Contact information
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    address = models.TextField(blank=True, default="")
    address_ar = models.TextField(blank=True, default="")

    # Payment terms (days)
    payment_terms_days = models.PositiveIntegerField(
        default=30,
        help_text="Default payment terms in days",
    )

    # Preferred currency for transactions
    currency = models.CharField(
        max_length=3,
        default="USD",
        help_text="Preferred transaction currency",
    )

    # Tax identification
    tax_id = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Tax identification number (VAT, TIN, etc.)",
    )

    # Bank details for payments
    bank_name = models.CharField(max_length=255, blank=True, default="")
    bank_account = models.CharField(max_length=100, blank=True, default="")
    bank_iban = models.CharField(max_length=50, blank=True, default="")
    bank_swift = models.CharField(max_length=20, blank=True, default="")

    # Status
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    # Notes - Multilingual
    notes = models.TextField(blank=True, default="")
    notes_ar = models.TextField(blank=True, default="")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="uniq_vendor_code_per_company",
            ),
        ]
        ordering = ["code"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "name"]),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_localized_name(self, language: str = "en") -> str:
        """Get name in specified language, fallback to English."""
        if language == "ar" and self.name_ar:
            return self.name_ar
        return self.name

    def get_localized_address(self, language: str = "en") -> str:
        """Get address in specified language, fallback to English."""
        if language == "ar" and self.address_ar:
            return self.address_ar
        return self.address

    def clean(self):
        # Validate default_ap_account is a payable control account
        if self.default_ap_account:
            if self.default_ap_account.company_id != self.company_id:
                raise ValidationError("Default AP account must belong to the same company.")
            if self.default_ap_account.role != Account.AccountRole.PAYABLE_CONTROL:
                raise ValidationError("Default AP account must have role=PAYABLE_CONTROL.")

    def save(self, *args, _projection_write: bool = False, **kwargs):
        """
        Save the vendor. Only projections should call this directly.

        Args:
            _projection_write: Must be True when called from projections.
                              Prevents accidental direct writes.
        """
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "Vendor is a read model. Use accounting.commands to modify. "
                "Direct saves are only allowed from projections within projection_writes_allowed()."
            )
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        """Returns True if vendor can receive new transactions."""
        return self.status == self.Status.ACTIVE

    def get_balance(self) -> Decimal:
        """
        Get vendor balance from the VendorBalance projection.

        Returns:
            Current balance from the projection (Decimal)
        """
        # Import here to avoid circular imports
        from projections.models import VendorBalance

        try:
            projection = VendorBalance.objects.get(
                company=self.company,
                vendor=self,
            )
            return projection.balance
        except VendorBalance.DoesNotExist:
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
        ordering = ["-entry_number", "-date", "-id"]

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

        if self.kind not in [self.Kind.NORMAL, self.Kind.OPENING, self.Kind.ADJUSTMENT, self.Kind.CLOSING]:
            raise ValidationError(f"Cannot post {self.kind} entries using post().")

        lines_qs = self.lines.select_related("account", "account__company")

        if lines_qs.count() < 2:
            raise ValidationError("Journal entry must have at least 2 lines.")

        # Validate all accounts belong to same company
        if lines_qs.exclude(account__company_id=self.company_id).exists():
            raise ValidationError("All journal lines must use accounts from the same company as the entry.")

        # Validate all accounts are postable
        non_postable = lines_qs.filter(Q(account__is_header=True) | ~Q(account__status=Account.Status.ACTIVE))
        if non_postable.exists():
            codes = list(non_postable.values_list("account__code", flat=True))
            raise ValidationError(f"Cannot post to non-postable accounts: {', '.join(codes)}")

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
                raise ValidationError(f"Entry is not balanced. Debit={debit_total} Credit={credit_total}")

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
        return self.lines.exclude(account__account_type=Account.AccountType.MEMO).aggregate(total=Sum("debit"))[
            "total"
        ] or Decimal("0.00")

    @property
    def total_credit(self) -> Decimal:
        """Sum of all credit amounts (excluding memo accounts)."""
        return self.lines.exclude(account__account_type=Account.AccountType.MEMO).aggregate(total=Sum("credit"))[
            "total"
        ] or Decimal("0.00")

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

    # Counterparty fields for AR/AP subledger
    # Required when posting to control accounts (RECEIVABLE_CONTROL, PAYABLE_CONTROL)
    customer = models.ForeignKey(
        "Customer",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="journal_lines",
        help_text="Required when posting to AR control accounts",
    )

    vendor = models.ForeignKey(
        "Vendor",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="journal_lines",
        help_text="Required when posting to AP control accounts",
    )

    # Bank reconciliation fields
    reconciled = models.BooleanField(
        default=False,
        help_text="Whether this line has been reconciled against a bank statement",
    )
    reconciled_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date this line was reconciled",
    )

    class Meta:
        unique_together = ("entry", "line_no")
        ordering = ["entry", "line_no"]
        constraints = [
            models.CheckConstraint(
                check=~(Q(debit__gt=0) & Q(credit__gt=0)),  # type: ignore[call-arg]
                name="chk_line_not_both_debit_credit",
            ),
            models.CheckConstraint(
                check=~(Q(debit__exact=0) & Q(credit__exact=0)),  # type: ignore[call-arg]
                name="chk_line_not_both_zero",
            ),
            models.CheckConstraint(
                check=Q(debit__gte=0) & Q(credit__gte=0),  # type: ignore[call-arg]
                name="chk_line_non_negative",
            ),
            # A line cannot have both customer and vendor
            models.CheckConstraint(
                check=~(Q(customer__isnull=False) & Q(vendor__isnull=False)),  # type: ignore[call-arg]
                name="chk_line_not_both_counterparty",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "entry"]),
            models.Index(fields=["company", "customer"]),
            models.Index(fields=["company", "vendor"]),
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

        # Validate counterparty company
        if self.customer_id and self.company_id:
            customer = self.customer
            if customer is not None and customer.company_id != self.company_id:
                raise ValidationError("JournalLine customer must belong to the same company.")
        if self.vendor_id and self.company_id:
            vendor = self.vendor
            if vendor is not None and vendor.company_id != self.company_id:
                raise ValidationError("JournalLine vendor must belong to the same company.")

        # Cannot have both customer and vendor on same line
        if self.customer_id and self.vendor_id:
            raise ValidationError("A journal line cannot have both customer and vendor.")

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

    @property
    def has_counterparty(self) -> bool:
        """Returns True if this line has a customer or vendor."""
        return self.customer_id is not None or self.vendor_id is not None

    @property
    def counterparty(self):
        """
        Returns the counterparty (Customer or Vendor) if set.

        Returns:
            Customer, Vendor, or None
        """
        if self.customer_id:
            return self.customer
        if self.vendor_id:
            return self.vendor
        return None

    @property
    def counterparty_kind(self) -> str:
        """
        Returns the type of counterparty: 'CUSTOMER', 'VENDOR', or ''.

        Returns:
            String indicating counterparty type
        """
        if self.customer_id:
            return "CUSTOMER"
        if self.vendor_id:
            return "VENDOR"
        return ""


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

    # Semantic classification
    class DimensionKind(models.TextChoices):
        CONTEXT = "CONTEXT", "Context"  # Business meaning (property, doctor, project)
        ANALYTIC = "ANALYTIC", "Analytic"  # Optional enrichment (campaign, segment)

    dimension_kind = models.CharField(
        max_length=10,
        choices=DimensionKind.choices,
        default=DimensionKind.ANALYTIC,
        help_text="CONTEXT = business meaning of the transaction; ANALYTIC = optional reporting enrichment.",
    )

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
        ancestors: list[AnalysisDimensionValue] = []
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


# =============================================================================
# Account Dimension Rules
# =============================================================================


class AccountDimensionRule(models.Model):
    """
    Rules for which dimensions are required/forbidden per account.

    Extends the global AnalysisDimension.is_required_on_posting with
    fine-grained per-account control. For example:
    - "Cost Center" required for Expense accounts
    - "Project" required for specific project expense accounts
    - "Department" forbidden for certain inter-company accounts
    """

    class RuleType(models.TextChoices):
        REQUIRED = "REQUIRED", "Required"
        FORBIDDEN = "FORBIDDEN", "Forbidden"
        OPTIONAL = "OPTIONAL", "Optional"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="dimension_rules",
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="dimension_rules",
    )
    dimension = models.ForeignKey(
        AnalysisDimension,
        on_delete=models.CASCADE,
        related_name="account_rules",
    )
    rule_type = models.CharField(
        max_length=12,
        choices=RuleType.choices,
        default=RuleType.OPTIONAL,
    )
    # Optional: default value when required (for auto-fill suggestions)
    default_value = models.ForeignKey(
        AnalysisDimensionValue,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Default value to suggest when this dimension is required",
    )

    class Meta:
        db_table = "scratchpad_accountdimensionrule"  # Keep existing table
        constraints = [
            models.UniqueConstraint(
                fields=["account", "dimension"],
                name="uniq_account_dimension_rule",
            ),
        ]
        verbose_name = "Account Dimension Rule"
        verbose_name_plural = "Account Dimension Rules"

    def __str__(self):
        return f"{self.account.code} - {self.dimension.code}: {self.rule_type}"


# =============================================================================
# Statistical Entries
# =============================================================================


class StatisticalEntry(AccountingReadModel):
    """
    Quantity tracking for statistical and off-balance sheet accounts.

    IMPORTANT: Statistical entries NEVER affect:
    - Trial balance
    - P&L statement
    - Debit = Credit validation

    They track quantities (inventory units, production hours, etc.) separately
    from financial accounting.

    Design Decision (locked): Uses direction enum (INCREASE/DECREASE) instead
    of signed quantities for clarity and consistency.
    """

    class Direction(models.TextChoices):
        INCREASE = "INCREASE", "Increase"
        DECREASE = "DECREASE", "Decrease"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        POSTED = "POSTED", "Posted"
        REVERSED = "REVERSED", "Reversed"

    # Custom manager for projection writes
    objects = ProjectionWriteManager()

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="statistical_entries",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    # Optional link to financial document
    # Can reference a JournalEntry to correlate financial and statistical movements
    related_journal_entry = models.ForeignKey(
        JournalEntry,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="statistical_entries",
        help_text="Related financial journal entry (optional)",
    )

    # Must be statistical or off-balance account
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="statistical_entries",
        help_text="Must be a statistical or off-balance account",
    )

    # Date and description
    date = models.DateField()
    memo = models.CharField(max_length=255, blank=True, default="")
    memo_ar = models.CharField(max_length=255, blank=True, default="")

    # Quantity tracking (direction enum, not signed)
    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Positive quantity (direction indicates increase/decrease)",
    )
    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
        help_text="INCREASE or DECREASE",
    )
    unit = models.CharField(
        max_length=20,
        help_text="Unit of measure: 'units', 'kg', 'L', 'hours', 'sqm', etc.",
    )

    # Status workflow
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    # For reversals
    reverses_entry = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reversal_entry",
    )

    # Source tracking
    source_module = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Module that created this entry (e.g., 'inventory', 'production')",
    )
    source_document = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Reference to source document",
    )

    # Posting metadata
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="posted_statistical_entries",
        db_constraint=False,
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_statistical_entries",
        db_constraint=False,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [
            models.Index(fields=["company", "account", "date"]),
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "related_journal_entry"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0),  # type: ignore[call-arg]
                name="chk_stat_quantity_positive",
            ),
        ]
        verbose_name = "Statistical Entry"
        verbose_name_plural = "Statistical Entries"

    def __str__(self):
        sign = "+" if self.direction == self.Direction.INCREASE else "-"
        return f"STAT {self.date} {self.account.code}: {sign}{self.quantity} {self.unit}"

    def get_localized_memo(self, language: str = "en") -> str:
        """Get memo in specified language, fallback to English."""
        if language == "ar" and self.memo_ar:
            return self.memo_ar
        return self.memo

    @property
    def signed_quantity(self) -> Decimal:
        """Returns the quantity with sign based on direction."""
        if self.direction == self.Direction.DECREASE:
            return -self.quantity
        return self.quantity

    def clean(self):
        # Validate account is statistical or off-balance
        if self.account:
            if self.account.ledger_domain == Account.LedgerDomain.FINANCIAL:
                raise ValidationError(
                    "Statistical entries can only use statistical or off-balance accounts. "
                    f"Account {self.account.code} has ledger_domain=FINANCIAL."
                )
            if self.account.company_id != self.company_id:
                raise ValidationError("Account must belong to the same company.")

        # Validate quantity is positive
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError("Quantity must be positive. Use direction to indicate increase/decrease.")

        # Validate unit matches account's unit_of_measure (if set)
        if self.account and self.account.unit_of_measure:
            if self.unit != self.account.unit_of_measure:
                raise ValidationError(
                    f"Unit '{self.unit}' does not match account's unit '{self.account.unit_of_measure}'."
                )

    def save(self, *args, _projection_write: bool = False, **kwargs):
        """
        Save the statistical entry. Only projections should call this directly.

        Args:
            _projection_write: Must be True when called from projections.
                              Prevents accidental direct writes.
        """
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "StatisticalEntry is a read model. Use accounting.commands to modify. "
                "Direct saves are only allowed from projections within projection_writes_allowed()."
            )
        self.full_clean()
        super().save(*args, **kwargs)


class ExchangeRate(models.Model):
    """
    Exchange rate model for multi-currency support.

    Stores historical exchange rates between currencies.
    Used for converting foreign currency transactions to the company's
    functional currency.
    """

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    company = models.ForeignKey(
        "accounts.Company",
        on_delete=models.CASCADE,
        related_name="exchange_rates",
    )

    from_currency = models.CharField(
        max_length=3,
        help_text="Source currency code (ISO 4217, e.g., USD)",
    )

    to_currency = models.CharField(
        max_length=3,
        help_text="Target currency code (ISO 4217, e.g., EUR)",
    )

    rate = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        help_text="Exchange rate: 1 from_currency = rate to_currency",
    )

    effective_date = models.DateField(
        help_text="Date from which this rate is effective",
    )

    rate_type = models.CharField(
        max_length=20,
        choices=[
            ("SPOT", "Spot Rate"),
            ("AVERAGE", "Average Rate"),
            ("CLOSING", "Closing Rate"),
        ],
        default="SPOT",
        help_text="Type of exchange rate",
    )

    source = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Source of the rate (e.g., 'Manual', 'ECB', 'XE')",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Exchange Rate"
        verbose_name_plural = "Exchange Rates"
        ordering = ["-effective_date", "from_currency", "to_currency"]
        indexes = [
            models.Index(
                fields=["company", "from_currency", "to_currency", "effective_date"],
                name="idx_exchange_rate_lookup",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "from_currency", "to_currency", "effective_date", "rate_type"],
                name="unique_exchange_rate_per_date",
            ),
        ]

    def __str__(self):
        return f"{self.from_currency}/{self.to_currency} = {self.rate} ({self.effective_date})"

    def clean(self):
        """Validate exchange rate."""
        if self.rate is not None and self.rate <= 0:
            raise ValidationError("Exchange rate must be positive.")
        if self.from_currency == self.to_currency:
            raise ValidationError("From and to currencies must be different.")

    @classmethod
    def get_rate(cls, company, from_currency: str, to_currency: str, date, rate_type: str = "SPOT"):
        """
        Get the exchange rate for a given date.

        Returns the rate effective on or before the given date.
        If no rate is found, returns None.
        """
        if from_currency == to_currency:
            return Decimal("1.0")

        rate = (
            cls.objects.filter(
                company=company,
                from_currency=from_currency,
                to_currency=to_currency,
                effective_date__lte=date,
                rate_type=rate_type,
            )
            .order_by("-effective_date")
            .first()
        )

        if rate:
            return rate.rate

        # Try reverse rate
        reverse_rate = (
            cls.objects.filter(
                company=company,
                from_currency=to_currency,
                to_currency=from_currency,
                effective_date__lte=date,
                rate_type=rate_type,
            )
            .order_by("-effective_date")
            .first()
        )

        if reverse_rate and reverse_rate.rate != 0:
            return (Decimal("1.0") / reverse_rate.rate).quantize(Decimal("0.000001"))

        # Auto-fetch from external API as last resort
        fetched = cls._auto_fetch_rate(company, from_currency, to_currency, date, rate_type)
        if fetched is not None:
            return fetched

        return None

    @classmethod
    def _auto_fetch_rate(cls, company, from_currency: str, to_currency: str, date, rate_type: str):
        """
        Fetch exchange rate from Frankfurter API (ECB data, free, no key).
        Saves the rate with source='ECB (auto)' so users can review/override.
        Returns the rate Decimal or None if fetch fails.
        """
        import requests

        try:
            # Frankfurter API: free, based on ECB reference rates
            url = f"https://api.frankfurter.dev/{date}"
            resp = requests.get(url, params={"from": from_currency, "to": to_currency}, timeout=5)
            if resp.status_code != 200:
                return None

            data = resp.json()
            rate_value = data.get("rates", {}).get(to_currency)
            if rate_value is None:
                return None

            rate_decimal = Decimal(str(rate_value))

            # Save for future lookups so we don't hit the API again
            from projections.write_barrier import command_writes_allowed

            with command_writes_allowed():
                cls.objects.update_or_create(
                    company=company,
                    from_currency=from_currency,
                    to_currency=to_currency,
                    effective_date=date,
                    rate_type=rate_type,
                    defaults={
                        "rate": rate_decimal,
                        "source": "ECB (auto-fetched)",
                    },
                )

            logger.info(
                "Auto-fetched exchange rate %s→%s = %s for %s from ECB",
                from_currency,
                to_currency,
                rate_decimal,
                date,
            )
            return rate_decimal

        except Exception as e:
            logger.warning("Failed to auto-fetch exchange rate %s→%s for %s: %s", from_currency, to_currency, date, e)
            return None


# =============================================================================
# Bank Reconciliation
# =============================================================================


class BankStatement(models.Model):
    """
    An imported bank statement for reconciliation.

    A statement covers a date range for a specific bank/cash account
    and contains individual transaction lines to be matched against
    journal entries.
    """

    class Status(models.TextChoices):
        IMPORTED = "IMPORTED", "Imported"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        RECONCILED = "RECONCILED", "Reconciled"

    class Source(models.TextChoices):
        MANUAL = "MANUAL", "Manual Entry"
        CSV = "CSV", "CSV Import"
        OFX = "OFX", "OFX/QFX Import"
        BANK_FEED = "BANK_FEED", "Bank Feed"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="bank_statements",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="bank_statements",
        help_text="The bank/cash GL account this statement is for.",
    )

    # Statement period
    statement_date = models.DateField(
        help_text="As-of date on the bank statement",
    )
    period_start = models.DateField()
    period_end = models.DateField()

    # Balances from the bank
    opening_balance = models.DecimalField(max_digits=18, decimal_places=2)
    closing_balance = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")

    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.CSV,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.IMPORTED,
    )

    # Reference / notes
    reference = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bank_statement"
        ordering = ["-statement_date"]
        indexes = [
            models.Index(fields=["company", "account", "statement_date"]),
        ]

    def __str__(self):
        return f"Statement {self.account.code} {self.statement_date}"

    @property
    def line_count(self):
        return self.lines.count()

    @property
    def matched_count(self):
        return self.lines.exclude(match_status="UNMATCHED").count()


class BankStatementLine(models.Model):
    """
    Individual transaction line from a bank statement.

    Positive amount = deposit (money in), negative = withdrawal (money out).
    Matched against JournalLine records during reconciliation.
    """

    class MatchStatus(models.TextChoices):
        UNMATCHED = "UNMATCHED", "Unmatched"
        AUTO_MATCHED = "AUTO_MATCHED", "Auto-Matched"
        MANUAL_MATCHED = "MANUAL_MATCHED", "Manual Match"
        EXCLUDED = "EXCLUDED", "Excluded"

    class TransactionType(models.TextChoices):
        DEPOSIT = "DEPOSIT", "Deposit"
        WITHDRAWAL = "WITHDRAWAL", "Withdrawal"
        FEE = "FEE", "Fee"
        INTEREST = "INTEREST", "Interest"
        TRANSFER = "TRANSFER", "Transfer"
        OTHER = "OTHER", "Other"

    statement = models.ForeignKey(
        BankStatement,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="bank_statement_lines",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Transaction data from bank
    line_date = models.DateField()
    description = models.CharField(max_length=500)
    reference = models.CharField(
        max_length=255,
        blank=True,
        help_text="Check number, transfer ref, etc.",
    )
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="Positive = deposit, negative = withdrawal",
    )
    transaction_type = models.CharField(
        max_length=20,
        choices=TransactionType.choices,
        default=TransactionType.OTHER,
    )

    # Matching
    match_status = models.CharField(
        max_length=20,
        choices=MatchStatus.choices,
        default=MatchStatus.UNMATCHED,
    )
    matched_journal_line = models.ForeignKey(
        JournalLine,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bank_matches",
        help_text="The journal line this bank transaction was matched to.",
    )
    match_confidence = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Auto-match confidence score (0-100)",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bank_statement_line"
        ordering = ["line_date", "id"]
        indexes = [
            models.Index(fields=["company", "match_status"]),
            models.Index(fields=["statement", "match_status"]),
        ]

    def __str__(self):
        return f"{self.line_date} {self.description[:40]} {self.amount}"


class BankReconciliation(models.Model):
    """
    A completed or in-progress bank reconciliation session.

    Tracks the reconciliation of a bank statement against the GL,
    computing the difference between the bank's closing balance
    and the GL balance for the account.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        COMPLETED = "COMPLETED", "Completed"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="bank_reconciliations",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="reconciliations",
    )
    statement = models.ForeignKey(
        BankStatement,
        on_delete=models.CASCADE,
        related_name="reconciliations",
    )

    reconciliation_date = models.DateField()

    # Balances
    statement_closing_balance = models.DecimalField(max_digits=18, decimal_places=2)
    gl_balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="GL balance for this account as of reconciliation date",
    )
    adjusted_gl_balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0,
        help_text="GL balance after outstanding items",
    )
    difference = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0,
        help_text="Statement closing - adjusted GL = should be 0 when reconciled",
    )

    # Stats
    matched_count = models.IntegerField(default=0)
    unmatched_count = models.IntegerField(default=0)
    outstanding_deposits = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    outstanding_withdrawals = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    reconciled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reconciled_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bank_reconciliation"
        ordering = ["-reconciliation_date"]

    def __str__(self):
        return f"Recon {self.account.code} {self.reconciliation_date} ({self.status})"


# Import ModuleAccountMapping so Django discovers it for migrations.
from accounting.mappings import ModuleAccountMapping  # noqa: F401

# Import PaymentGateway so Django discovers it for migrations.
from accounting.payment_gateway import PaymentGateway  # noqa: F401
