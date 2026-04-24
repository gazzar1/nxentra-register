# accounting/serializers.py
"""
Serializers for accounting API.

Note: These serializers are used for:
1. Input validation
2. Output formatting

The actual business logic happens in commands.py.
For operations that need events, views should call commands directly.
"""

from decimal import Decimal, InvalidOperation

from rest_framework import serializers

from accounts.authz import resolve_actor
from accounts.models import CompanyMembership

from .commands import (
    create_journal_entry,
    save_journal_entry_complete,
    update_journal_entry,
)
from .models import (
    Account,
    AccountAnalysisDefault,
    AnalysisDimension,
    AnalysisDimensionValue,
    Customer,
    JournalEntry,
    JournalLine,
    JournalLineAnalysis,
    StatisticalEntry,
    Vendor,
)

MONEY_Q = Decimal("0.01")


def _to_decimal(x) -> Decimal:
    """Convert input to Decimal, handling various input types."""
    if x is None or x == "":
        return Decimal("0.00")
    if isinstance(x, Decimal):
        return x
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        raise serializers.ValidationError("Invalid decimal amount.")


def _account_id(value, line_index: int, company=None) -> int:
    """
    Validate account_id is a valid integer.

    Contract: value MUST be an integer account ID.
    """
    if value is None:
        raise serializers.ValidationError(f"Line {line_index}: account_id is required.")

    if isinstance(value, int):
        return value

    if isinstance(value, str) and value.isdigit():
        return int(value)

    raise serializers.ValidationError(f"Line {line_index}: account_id must be an integer.")


# =============================================================================
# Account Serializers
# =============================================================================


class AccountSerializer(serializers.ModelSerializer):
    """
    Serializer for Account model.
    Used for listing, retrieving, and basic updates.
    For creates/updates that need events, use commands directly.

    Includes:
    - role: Behavioral classification (RECEIVABLE_CONTROL, PAYABLE_CONTROL, etc.)
    - ledger_domain: FINANCIAL, STATISTICAL, or OFF_BALANCE
    - Derived flags: requires_counterparty, counterparty_kind, allow_manual_posting
    """

    has_transactions = serializers.SerializerMethodField()
    parent_code = serializers.CharField(source="parent.code", read_only=True, default=None)

    class Meta:
        model = Account
        fields = [
            "id",
            "public_id",
            "company",
            "code",
            "name",
            "name_ar",
            "account_type",
            "role",
            "ledger_domain",
            "status",
            "normal_balance",
            # Derived flags (read-only, computed from role)
            "requires_counterparty",
            "counterparty_kind",
            "allow_manual_posting",
            "is_header",
            "parent",
            "parent_code",
            "description",
            "description_ar",
            "unit_of_measure",
            # Computed properties
            "is_postable",
            "is_memo_account",
            "is_statistical",
            "is_off_balance",
            "is_financial",
            "is_control_account",
            "is_receivable",
            "is_payable",
            "has_transactions",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "company",
            "normal_balance",
            # Derived flags are read-only (computed from role)
            "requires_counterparty",
            "counterparty_kind",
            # Computed properties
            "is_postable",
            "is_memo_account",
            "is_statistical",
            "is_off_balance",
            "is_financial",
            "is_control_account",
            "is_receivable",
            "is_payable",
            "has_transactions",
            "parent_code",
            "created_at",
            "updated_at",
        ]

    def get_has_transactions(self, obj):
        # Use annotated value if available (from list view), else query
        if hasattr(obj, "_has_transactions"):
            return obj._has_transactions
        return obj.journal_lines.exists()


class AccountCreateSerializer(serializers.Serializer):
    """
    Serializer for creating accounts via command.

    Includes role and ledger_domain for the new 5-type + role architecture.
    """

    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=255)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    account_type = serializers.ChoiceField(choices=Account.AccountType.choices)
    role = serializers.ChoiceField(
        choices=Account.AccountRole.choices,
        required=False,
        allow_blank=True,
        default="",
        help_text="Behavioral role. If not provided, a default role for the type is used.",
    )
    ledger_domain = serializers.ChoiceField(
        choices=Account.LedgerDomain.choices,
        required=False,
        default=Account.LedgerDomain.FINANCIAL,
        help_text="Financial, Statistical, or Off-Balance ledger",
    )
    parent_id = serializers.IntegerField(required=False, allow_null=True)
    is_header = serializers.BooleanField(required=False, default=False)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    description_ar = serializers.CharField(required=False, allow_blank=True, default="")
    unit_of_measure = serializers.CharField(
        max_length=20,
        required=False,
        allow_blank=True,
        default="",
        help_text="Required for STATISTICAL and OFF_BALANCE ledger domains",
    )
    allow_manual_posting = serializers.BooleanField(
        required=False,
        default=True,
        help_text="Admin override to allow manual posting to control accounts",
    )


class AccountUpdateSerializer(serializers.Serializer):
    """
    Serializer for updating accounts via command.

    Note: Changing role may affect derived flags (requires_counterparty, etc.)
    """

    name = serializers.CharField(max_length=255, required=False)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True)
    code = serializers.CharField(max_length=20, required=False)
    account_type = serializers.ChoiceField(choices=Account.AccountType.choices, required=False)
    role = serializers.ChoiceField(
        choices=Account.AccountRole.choices,
        required=False,
        allow_blank=True,
    )
    ledger_domain = serializers.ChoiceField(
        choices=Account.LedgerDomain.choices,
        required=False,
    )
    status = serializers.ChoiceField(choices=Account.Status.choices, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    description_ar = serializers.CharField(required=False, allow_blank=True)
    unit_of_measure = serializers.CharField(max_length=20, required=False, allow_blank=True)
    allow_manual_posting = serializers.BooleanField(
        required=False,
        help_text="Admin override to allow manual posting to control accounts",
    )


# =============================================================================
# Journal Entry Serializers
# =============================================================================


class JournalLineAnalysisOutputSerializer(serializers.ModelSerializer):
    """Serializer for analysis tags in journal line response."""

    dimension_id = serializers.IntegerField(source="dimension.id", read_only=True)
    dimension_code = serializers.CharField(source="dimension.code", read_only=True)
    dimension_name = serializers.CharField(source="dimension.name", read_only=True)
    dimension_value_id = serializers.IntegerField(source="dimension_value.id", read_only=True)
    value_code = serializers.CharField(source="dimension_value.code", read_only=True)
    value_name = serializers.CharField(source="dimension_value.name", read_only=True)

    class Meta:
        model = JournalLineAnalysis
        fields = [
            "dimension_id",
            "dimension_code",
            "dimension_name",
            "dimension_value_id",
            "value_code",
            "value_name",
        ]


class JournalLineSerializer(serializers.ModelSerializer):
    """Serializer for individual journal lines with counterparty support."""

    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    account_name_ar = serializers.CharField(source="account.name_ar", read_only=True)
    analysis_tags = JournalLineAnalysisOutputSerializer(many=True, read_only=True)
    # Counterparty info
    customer_code = serializers.CharField(source="customer.code", read_only=True, default=None)
    customer_name = serializers.CharField(source="customer.name", read_only=True, default=None)
    vendor_code = serializers.CharField(source="vendor.code", read_only=True, default=None)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default=None)

    class Meta:
        model = JournalLine
        fields = [
            "public_id",
            "line_no",
            "account",
            "account_code",
            "account_name",
            "account_name_ar",
            "description",
            "description_ar",
            "debit",
            "credit",
            "amount_currency",
            "currency",
            "exchange_rate",
            "is_debit",
            "amount",
            "analysis_tags",
            # Counterparty
            "customer",
            "customer_code",
            "customer_name",
            "vendor",
            "vendor_code",
            "vendor_name",
            "has_counterparty",
            "counterparty_kind",
            # Bank reconciliation
            "reconciled",
            "reconciled_date",
        ]
        read_only_fields = [
            "public_id",
            "line_no",
            "is_debit",
            "amount",
            "account_code",
            "account_name",
            "account_name_ar",
            "analysis_tags",
            "customer_code",
            "customer_name",
            "vendor_code",
            "vendor_name",
            "has_counterparty",
            "counterparty_kind",
            "reconciled",
            "reconciled_date",
        ]


class AnalysisTagInputSerializer(serializers.Serializer):
    """Serializer for analysis tags on journal lines."""

    dimension_id = serializers.IntegerField()
    dimension_value_id = serializers.IntegerField()


class JournalLineInputSerializer(serializers.Serializer):
    """
    Serializer for journal line input (creation/update).

    Standardized contract: ALWAYS use account_id (integer).
    For control accounts, provide customer_id or vendor_id.
    """

    account_id = serializers.IntegerField(required=True, help_text="Account ID (integer)")
    description = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    description_ar = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    debit = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default=0)
    credit = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default=0)
    amount_currency = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    currency = serializers.CharField(max_length=3, required=False, allow_blank=True, default="")
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=6, required=False)
    analysis_tags = AnalysisTagInputSerializer(many=True, required=False, default=list)
    # Counterparty for AR/AP control accounts
    customer_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Customer ID for AR control account lines",
    )
    vendor_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Vendor ID for AP control account lines",
    )


class JournalEntrySerializer(serializers.ModelSerializer):
    """
    Full journal entry serializer with nested lines.
    Used for retrieval and display.
    """

    lines = JournalLineSerializer(many=True, read_only=True)
    total_debit = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total_credit = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    is_balanced = serializers.BooleanField(read_only=True)

    class Meta:
        model = JournalEntry
        fields = [
            "id",
            "public_id",
            "company",
            "entry_number",
            "date",
            "period",
            "memo",
            "memo_ar",
            "currency",
            "exchange_rate",
            "kind",
            "status",
            "source_module",
            "source_document",
            "posted_at",
            "posted_by",
            "reversed_at",
            "reversed_by",
            "reverses_entry",
            "created_at",
            "created_by",
            "updated_at",
            "lines",
            "total_debit",
            "total_credit",
            "is_balanced",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "company",
            "entry_number",
            "status",
            "kind",
            "posted_at",
            "posted_by",
            "reversed_at",
            "reversed_by",
            "reverses_entry",
            "created_at",
            "created_by",
            "updated_at",
            "total_debit",
            "total_credit",
            "is_balanced",
        ]


class JournalEntryAutoSaveSerializer(serializers.ModelSerializer):
    """
    Autosave serializer:
    - Allows unbalanced entries
    - Drops 0/0 placeholder lines
    - Sets status to INCOMPLETE

    For balanced saving (DRAFT), use JournalEntrySaveCompleteSerializer.
    """

    lines = JournalLineInputSerializer(many=True, required=False)

    class Meta:
        model = JournalEntry
        fields = [
            "id",
            "date",
            "period",
            "memo",
            "memo_ar",
            "currency",
            "exchange_rate",
            "company",
            "status",
            "posted_at",
            "posted_by",
            "lines",
        ]
        read_only_fields = ["id", "company", "posted_at", "posted_by", "status"]

    def _get_company(self):
        from accounts.rls import rls_bypass

        request = self.context.get("request")
        user = getattr(request, "user", None)

        if not request or not user or not user.is_authenticated:
            raise serializers.ValidationError("Authentication is required.")

        with rls_bypass():
            company = getattr(user, "active_company", None)
            if not company:
                raise serializers.ValidationError("No active company selected for this user.")

            # Verify user is an active member
            is_member = CompanyMembership.objects.filter(
                user=user,
                company=company,
                is_active=True,
            ).exists()

            if not is_member:
                raise serializers.ValidationError("You are not an active member of the selected company.")

        return company

    def _get_actor(self):
        request = self.context.get("request")
        if not request:
            raise serializers.ValidationError("Request context is required.")
        return resolve_actor(request)

    def validate(self, attrs):
        company = self._get_company()
        lines = attrs.get("lines") or []
        account_ids = set()

        for i, line in enumerate(lines, start=1):
            debit = _to_decimal(line.get("debit"))
            credit = _to_decimal(line.get("credit"))

            if debit < 0 or credit < 0:
                raise serializers.ValidationError(f"Line {i}: negative debit/credit is not allowed.")

            if debit > 0 and credit > 0:
                raise serializers.ValidationError(f"Line {i}: cannot have both debit and credit > 0.")

            # Validate account_id
            acc_id = line.get("account_id")
            if acc_id:
                validated_id = _account_id(acc_id, i, company=company)
                account_ids.add(validated_id)

        # Validate all accounts belong to company
        if account_ids:
            valid_count = Account.objects.filter(company=company, id__in=account_ids).count()
            if valid_count != len(account_ids):
                raise serializers.ValidationError("One or more accounts do not belong to your company.")

        return attrs

    def _clean_lines_drop_placeholders(self, lines_data, company):
        """Clean lines and drop placeholders (0/0 lines)."""
        cleaned = []
        for line in lines_data:
            debit = _to_decimal(line.get("debit"))
            credit = _to_decimal(line.get("credit"))

            # Drop placeholders (DB constraint blocks 0/0)
            if debit == 0 and credit == 0:
                continue

            line = dict(line)
            line.pop("line_no", None)

            # Validate account_id
            acc_id = line.get("account_id")
            if acc_id:
                line["account_id"] = _account_id(acc_id, len(cleaned) + 1, company=company)

            # Normalize analysis_tags format for command layer
            analysis_tags = line.get("analysis_tags") or []
            normalized_tags = []
            for tag in analysis_tags:
                # Convert dimension_value_id to value_id for command layer
                normalized_tags.append(
                    {
                        "dimension_id": tag.get("dimension_id"),
                        "value_id": tag.get("dimension_value_id") or tag.get("value_id"),
                    }
                )
            line["analysis_tags"] = normalized_tags

            cleaned.append(line)
        return cleaned

    def create(self, validated_data):
        lines_data = validated_data.pop("lines", [])
        actor = self._get_actor()

        cleaned_lines = self._clean_lines_drop_placeholders(lines_data, actor.company)
        command_lines = []
        for line in cleaned_lines:
            command_lines.append(
                {
                    "account_id": line.get("account_id"),
                    "description": line.get("description", ""),
                    "description_ar": line.get("description_ar", ""),
                    "debit": line.get("debit", 0),
                    "credit": line.get("credit", 0),
                    "analysis_tags": line.get("analysis_tags", []),
                }
            )

        result = create_journal_entry(
            actor,
            date=validated_data.get("date"),
            memo=validated_data.get("memo", ""),
            memo_ar=validated_data.get("memo_ar", ""),
            lines=command_lines,
        )
        if not result.success:
            raise serializers.ValidationError(result.error)

        return result.data

    def update(self, instance, validated_data):
        if instance.status not in [JournalEntry.Status.INCOMPLETE, JournalEntry.Status.DRAFT]:
            raise serializers.ValidationError("Only INCOMPLETE/DRAFT entries can be edited.")

        actor = self._get_actor()
        if instance.company_id != actor.company.id:
            raise serializers.ValidationError("You cannot modify entries outside your active company.")

        lines_data = validated_data.pop("lines", None)

        kwargs = {}
        for field in ["date", "memo", "memo_ar"]:
            if field in validated_data:
                kwargs[field] = validated_data[field]

        if lines_data is not None:
            cleaned_lines = self._clean_lines_drop_placeholders(lines_data, actor.company)
            command_lines = []
            for line in cleaned_lines:
                command_lines.append(
                    {
                        "account_id": line.get("account_id"),
                        "description": line.get("description", ""),
                        "description_ar": line.get("description_ar", ""),
                        "debit": line.get("debit", 0),
                        "credit": line.get("credit", 0),
                        "analysis_tags": line.get("analysis_tags", []),
                    }
                )
            kwargs["lines"] = command_lines

        result = update_journal_entry(actor, instance.id, **kwargs)
        if not result.success:
            raise serializers.ValidationError(result.error)

        return result.data


class JournalEntrySaveCompleteSerializer(JournalEntryAutoSaveSerializer):
    """
    Save complete serializer:
    - Enforces balanced entry
    - Requires at least 2 effective lines
    - Sets status to DRAFT
    """

    def validate(self, attrs):
        attrs = super().validate(attrs)
        company = self._get_company()

        # If client didn't send lines, validate against DB lines
        if "lines" not in attrs and getattr(self, "instance", None):
            inst = self.instance
            attrs["lines"] = [
                {"account_id": ln.account_id, "debit": ln.debit, "credit": ln.credit} for ln in inst.lines.all()
            ]

        lines = attrs.get("lines") or []
        total_debit = Decimal("0.00")
        total_credit = Decimal("0.00")
        effective = 0

        for i, line in enumerate(lines, start=1):
            debit = _to_decimal(line.get("debit"))
            credit = _to_decimal(line.get("credit"))

            if debit == 0 and credit == 0:
                continue  # Ignore placeholders

            # Enforce exactly one side > 0
            if (debit > 0) == (credit > 0):
                raise serializers.ValidationError(f"Line {i}: must have either debit or credit (not both).")

            effective += 1
            total_debit += debit
            total_credit += credit

        if effective < 2:
            raise serializers.ValidationError("To save as complete, entry must have at least 2 non-empty lines.")

        total_debit = total_debit.quantize(MONEY_Q)
        total_credit = total_credit.quantize(MONEY_Q)

        if total_debit == Decimal("0.00") and total_credit == Decimal("0.00"):
            raise serializers.ValidationError("To save as complete, totals cannot both be zero.")

        if total_debit != total_credit:
            raise serializers.ValidationError(
                f"To save as complete, journal entry must be balanced. Debit={total_debit}, Credit={total_credit}"
            )

        return attrs

    def update(self, instance, validated_data):
        actor = self._get_actor()

        lines_data = validated_data.pop("lines", None)
        kwargs = {}
        for field in ["date", "memo", "memo_ar"]:
            if field in validated_data:
                kwargs[field] = validated_data[field]

        if lines_data is not None:
            cleaned_lines = self._clean_lines_drop_placeholders(lines_data, actor.company)
            command_lines = []
            for line in cleaned_lines:
                command_lines.append(
                    {
                        "account_id": line.get("account_id"),
                        "description": line.get("description", ""),
                        "description_ar": line.get("description_ar", ""),
                        "debit": line.get("debit", 0),
                        "credit": line.get("credit", 0),
                        "analysis_tags": line.get("analysis_tags", []),
                    }
                )
            kwargs["lines"] = command_lines

        result = save_journal_entry_complete(actor, instance.id, **kwargs)
        if not result.success:
            raise serializers.ValidationError(result.error)

        return result.data


# =============================================================================
# Analysis Dimension Serializers
# =============================================================================


class AnalysisDimensionValueSerializer(serializers.ModelSerializer):
    """Serializer for dimension values."""

    full_path = serializers.CharField(read_only=True)

    class Meta:
        model = AnalysisDimensionValue
        fields = [
            "id",
            "public_id",
            "dimension",
            "code",
            "name",
            "name_ar",
            "description",
            "description_ar",
            "parent",
            "is_active",
            "full_path",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id", "dimension", "full_path", "created_at", "updated_at"]


class AnalysisDimensionSerializer(serializers.ModelSerializer):
    """Serializer for analysis dimensions."""

    values = AnalysisDimensionValueSerializer(many=True, read_only=True)

    class Meta:
        model = AnalysisDimension
        fields = [
            "id",
            "public_id",
            "company",
            "code",
            "name",
            "name_ar",
            "description",
            "description_ar",
            "dimension_kind",
            "is_required_on_posting",
            "is_active",
            "applies_to_account_types",
            "display_order",
            "values",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id", "company", "values", "created_at", "updated_at"]


class AnalysisDimensionCreateSerializer(serializers.Serializer):
    """Serializer for creating dimensions via command."""

    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=100)
    name_ar = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    description_ar = serializers.CharField(required=False, allow_blank=True, default="")
    dimension_kind = serializers.ChoiceField(
        choices=AnalysisDimension.DimensionKind.choices,
        required=False,
        default="ANALYTIC",
    )
    is_required_on_posting = serializers.BooleanField(required=False, default=False)
    applies_to_account_types = serializers.ListField(
        child=serializers.ChoiceField(choices=Account.AccountType.choices),
        required=False,
        default=list,
    )
    display_order = serializers.IntegerField(required=False, default=0)


class DimensionValueCreateSerializer(serializers.Serializer):
    """Serializer for creating dimension values via command."""

    dimension_id = serializers.IntegerField()
    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=100)
    name_ar = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    description_ar = serializers.CharField(required=False, allow_blank=True, default="")
    parent_id = serializers.IntegerField(required=False, allow_null=True)


class JournalLineAnalysisSerializer(serializers.ModelSerializer):
    """Serializer for journal line analysis tags."""

    dimension_code = serializers.CharField(source="dimension.code", read_only=True)
    dimension_name = serializers.CharField(source="dimension.name", read_only=True)
    value_code = serializers.CharField(source="dimension_value.code", read_only=True)
    value_name = serializers.CharField(source="dimension_value.name", read_only=True)

    class Meta:
        model = JournalLineAnalysis
        fields = [
            "id",
            "journal_line",
            "dimension",
            "dimension_value",
            "dimension_code",
            "dimension_name",
            "value_code",
            "value_name",
        ]
        read_only_fields = ["id"]


class JournalLineAnalysisInputSerializer(serializers.Serializer):
    """Serializer for setting analysis tags."""

    dimension_id = serializers.IntegerField()
    value_id = serializers.IntegerField()


class AccountAnalysisDefaultSerializer(serializers.ModelSerializer):
    """Serializer for account analysis defaults."""

    dimension_code = serializers.CharField(source="dimension.code", read_only=True)
    dimension_name = serializers.CharField(source="dimension.name", read_only=True)
    value_code = serializers.CharField(source="default_value.code", read_only=True)
    value_name = serializers.CharField(source="default_value.name", read_only=True)

    class Meta:
        model = AccountAnalysisDefault
        fields = [
            "id",
            "account",
            "dimension",
            "default_value",
            "dimension_code",
            "dimension_name",
            "value_code",
            "value_name",
        ]
        read_only_fields = ["id"]


# =============================================================================
# Customer Serializers (AR Subledger)
# =============================================================================


class CustomerSerializer(serializers.ModelSerializer):
    """
    Serializer for Customer model (AR subledger).

    Customers are counterparties for accounts receivable, not COA entries.
    """

    default_ar_account_code = serializers.CharField(source="default_ar_account.code", read_only=True, default=None)
    default_ar_account_name = serializers.CharField(source="default_ar_account.name", read_only=True, default=None)

    class Meta:
        model = Customer
        fields = [
            "id",
            "public_id",
            "company",
            "code",
            "name",
            "name_ar",
            "default_ar_account",
            "default_ar_account_code",
            "default_ar_account_name",
            "email",
            "phone",
            "address",
            "address_ar",
            "credit_limit",
            "payment_terms_days",
            "currency",
            "tax_id",
            "status",
            "is_active",
            "notes",
            "notes_ar",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "company",
            "is_active",
            "default_ar_account_code",
            "default_ar_account_name",
            "created_at",
            "updated_at",
        ]


class CustomerCreateSerializer(serializers.Serializer):
    """Serializer for creating customers via command."""

    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=255)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    default_ar_account_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Default AR control account ID",
    )
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    address = serializers.CharField(required=False, allow_blank=True, default="")
    address_ar = serializers.CharField(required=False, allow_blank=True, default="")
    credit_limit = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    payment_terms_days = serializers.IntegerField(required=False, default=30)
    currency = serializers.CharField(max_length=3, required=False, default="USD")
    tax_id = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    notes_ar = serializers.CharField(required=False, allow_blank=True, default="")


class CustomerUpdateSerializer(serializers.Serializer):
    """Serializer for updating customers via command."""

    name = serializers.CharField(max_length=255, required=False)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True)
    code = serializers.CharField(max_length=20, required=False)
    default_ar_account_id = serializers.IntegerField(required=False, allow_null=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=50, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    address_ar = serializers.CharField(required=False, allow_blank=True)
    credit_limit = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    payment_terms_days = serializers.IntegerField(required=False)
    currency = serializers.CharField(max_length=3, required=False)
    tax_id = serializers.CharField(max_length=50, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=Customer.Status.choices, required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    notes_ar = serializers.CharField(required=False, allow_blank=True)


# =============================================================================
# Vendor Serializers (AP Subledger)
# =============================================================================


class VendorSerializer(serializers.ModelSerializer):
    """
    Serializer for Vendor model (AP subledger).

    Vendors are counterparties for accounts payable, not COA entries.
    """

    default_ap_account_code = serializers.CharField(source="default_ap_account.code", read_only=True, default=None)
    default_ap_account_name = serializers.CharField(source="default_ap_account.name", read_only=True, default=None)

    class Meta:
        model = Vendor
        fields = [
            "id",
            "public_id",
            "company",
            "code",
            "name",
            "name_ar",
            "default_ap_account",
            "default_ap_account_code",
            "default_ap_account_name",
            "email",
            "phone",
            "address",
            "address_ar",
            "payment_terms_days",
            "currency",
            "tax_id",
            "bank_name",
            "bank_account",
            "bank_iban",
            "bank_swift",
            "status",
            "is_active",
            "notes",
            "notes_ar",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "company",
            "is_active",
            "default_ap_account_code",
            "default_ap_account_name",
            "created_at",
            "updated_at",
        ]


class VendorCreateSerializer(serializers.Serializer):
    """Serializer for creating vendors via command."""

    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=255)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    default_ap_account_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Default AP control account ID",
    )
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    address = serializers.CharField(required=False, allow_blank=True, default="")
    address_ar = serializers.CharField(required=False, allow_blank=True, default="")
    payment_terms_days = serializers.IntegerField(required=False, default=30)
    currency = serializers.CharField(max_length=3, required=False, default="USD")
    tax_id = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    bank_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    bank_account = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    bank_iban = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    bank_swift = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    notes_ar = serializers.CharField(required=False, allow_blank=True, default="")


class VendorUpdateSerializer(serializers.Serializer):
    """Serializer for updating vendors via command."""

    name = serializers.CharField(max_length=255, required=False)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True)
    code = serializers.CharField(max_length=20, required=False)
    default_ap_account_id = serializers.IntegerField(required=False, allow_null=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=50, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    address_ar = serializers.CharField(required=False, allow_blank=True)
    payment_terms_days = serializers.IntegerField(required=False)
    currency = serializers.CharField(max_length=3, required=False)
    tax_id = serializers.CharField(max_length=50, required=False, allow_blank=True)
    bank_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    bank_account = serializers.CharField(max_length=100, required=False, allow_blank=True)
    bank_iban = serializers.CharField(max_length=50, required=False, allow_blank=True)
    bank_swift = serializers.CharField(max_length=20, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=Vendor.Status.choices, required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    notes_ar = serializers.CharField(required=False, allow_blank=True)


# =============================================================================
# Statistical Entry Serializers
# =============================================================================


class StatisticalEntrySerializer(serializers.ModelSerializer):
    """
    Serializer for StatisticalEntry model.

    Statistical entries track quantities for statistical/off-balance accounts.
    They never affect trial balance or debit/credit validation.
    """

    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    signed_quantity = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    related_journal_entry_number = serializers.CharField(
        source="related_journal_entry.entry_number", read_only=True, default=None
    )

    class Meta:
        model = StatisticalEntry
        fields = [
            "id",
            "public_id",
            "company",
            "account",
            "account_code",
            "account_name",
            "date",
            "memo",
            "memo_ar",
            "quantity",
            "direction",
            "unit",
            "signed_quantity",
            "status",
            "related_journal_entry",
            "related_journal_entry_number",
            "source_module",
            "source_document",
            "reverses_entry",
            "posted_at",
            "posted_by",
            "created_at",
            "created_by",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "company",
            "account_code",
            "account_name",
            "signed_quantity",
            "related_journal_entry_number",
            "status",
            "posted_at",
            "posted_by",
            "created_at",
            "created_by",
            "updated_at",
        ]


class StatisticalEntryCreateSerializer(serializers.Serializer):
    """Serializer for creating statistical entries via command."""

    account_id = serializers.IntegerField(help_text="Account ID (must be STATISTICAL or OFF_BALANCE ledger domain)")
    date = serializers.DateField()
    memo = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    memo_ar = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(
        max_digits=18, decimal_places=4, help_text="Positive quantity (use direction for sign)"
    )
    direction = serializers.ChoiceField(choices=StatisticalEntry.Direction.choices, help_text="INCREASE or DECREASE")
    unit = serializers.CharField(
        max_length=20, help_text="Unit of measure (must match account's unit_of_measure if set)"
    )
    related_journal_entry_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Optional link to a financial journal entry",
    )
    source_module = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    source_document = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")


class StatisticalEntryUpdateSerializer(serializers.Serializer):
    """Serializer for updating statistical entries via command."""

    date = serializers.DateField(required=False)
    memo = serializers.CharField(max_length=255, required=False, allow_blank=True)
    memo_ar = serializers.CharField(max_length=255, required=False, allow_blank=True)
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4, required=False)
    direction = serializers.ChoiceField(choices=StatisticalEntry.Direction.choices, required=False)
    unit = serializers.CharField(max_length=20, required=False)
    source_module = serializers.CharField(max_length=50, required=False, allow_blank=True)
    source_document = serializers.CharField(max_length=100, required=False, allow_blank=True)
