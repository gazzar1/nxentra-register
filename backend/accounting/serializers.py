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

from accounts.models import CompanyMembership
from accounts.authz import resolve_actor
from .models import (
    Account,
    JournalEntry,
    JournalLine,
    AnalysisDimension,
    AnalysisDimensionValue,
    JournalLineAnalysis,
    AccountAnalysisDefault,
)
from .commands import (
    create_journal_entry,
    update_journal_entry,
    save_journal_entry_complete,
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
    """
    has_transactions = serializers.SerializerMethodField()
    parent_code = serializers.CharField(source="parent.code", read_only=True, default=None)

    class Meta:
        model = Account
        fields = [
            "id", "public_id", "company", "code", "name", "name_ar",
            "account_type", "status", "normal_balance",
            "is_header", "parent", "parent_code", "description", "description_ar",
            "unit_of_measure", "is_postable", "is_memo_account",
            "is_receivable", "is_payable",
            "has_transactions",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "public_id", "company", "normal_balance",
            "is_postable", "is_memo_account", "is_receivable", "is_payable",
            "has_transactions", "parent_code",
            "created_at", "updated_at",
        ]

    def get_has_transactions(self, obj):
        # Use annotated value if available (from list view), else query
        if hasattr(obj, '_has_transactions'):
            return obj._has_transactions
        return obj.journal_lines.exists()


class AccountCreateSerializer(serializers.Serializer):
    """Serializer for creating accounts via command."""
    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=255)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    account_type = serializers.ChoiceField(choices=Account.AccountType.choices)
    parent_id = serializers.IntegerField(required=False, allow_null=True)
    is_header = serializers.BooleanField(required=False, default=False)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    description_ar = serializers.CharField(required=False, allow_blank=True, default="")
    unit_of_measure = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")


class AccountUpdateSerializer(serializers.Serializer):
    """Serializer for updating accounts via command."""
    name = serializers.CharField(max_length=255, required=False)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True)
    code = serializers.CharField(max_length=20, required=False)
    account_type = serializers.ChoiceField(choices=Account.AccountType.choices, required=False)
    status = serializers.ChoiceField(choices=Account.Status.choices, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    description_ar = serializers.CharField(required=False, allow_blank=True)
    unit_of_measure = serializers.CharField(max_length=20, required=False, allow_blank=True)


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
            "dimension_id", "dimension_code", "dimension_name",
            "dimension_value_id", "value_code", "value_name",
        ]


class JournalLineSerializer(serializers.ModelSerializer):
    """Serializer for individual journal lines."""
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    account_name_ar = serializers.CharField(source="account.name_ar", read_only=True)
    analysis_tags = JournalLineAnalysisOutputSerializer(many=True, read_only=True)

    class Meta:
        model = JournalLine
        fields = [
            "public_id", "line_no", "account", "account_code", "account_name", "account_name_ar",
            "description", "description_ar",
            "debit", "credit", "amount_currency", "currency", "exchange_rate",
            "is_debit", "amount", "analysis_tags",
        ]
        read_only_fields = ["public_id", "line_no", "is_debit", "amount", "account_code", "account_name", "account_name_ar", "analysis_tags"]


class AnalysisTagInputSerializer(serializers.Serializer):
    """Serializer for analysis tags on journal lines."""
    dimension_id = serializers.IntegerField()
    dimension_value_id = serializers.IntegerField()


class JournalLineInputSerializer(serializers.Serializer):
    """
    Serializer for journal line input (creation/update).

    Standardized contract: ALWAYS use account_id (integer).
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
            "id", "public_id", "company", "entry_number", "date", "period",
            "memo", "memo_ar", "currency", "exchange_rate", "kind", "status",
            "source_module", "source_document",
            "posted_at", "posted_by",
            "reversed_at", "reversed_by", "reverses_entry",
            "created_at", "created_by", "updated_at",
            "lines", "total_debit", "total_credit", "is_balanced",
        ]
        read_only_fields = [
            "id", "public_id", "company", "entry_number", "status", "kind",
            "posted_at", "posted_by", "reversed_at", "reversed_by",
            "reverses_entry", "created_at", "created_by", "updated_at",
            "total_debit", "total_credit", "is_balanced",
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
            "id", "date", "period", "memo", "memo_ar", "currency", "exchange_rate",
            "company", "status",
            "posted_at", "posted_by", "lines",
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
                user=user, company=company, is_active=True,
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
                normalized_tags.append({
                    "dimension_id": tag.get("dimension_id"),
                    "value_id": tag.get("dimension_value_id") or tag.get("value_id"),
                })
            line["analysis_tags"] = normalized_tags

            cleaned.append(line)
        return cleaned

    def create(self, validated_data):
        lines_data = validated_data.pop("lines", [])
        actor = self._get_actor()

        cleaned_lines = self._clean_lines_drop_placeholders(lines_data, actor.company)
        command_lines = []
        for line in cleaned_lines:
            command_lines.append({
                "account_id": line.get("account_id"),
                "description": line.get("description", ""),
                "description_ar": line.get("description_ar", ""),
                "debit": line.get("debit", 0),
                "credit": line.get("credit", 0),
                "analysis_tags": line.get("analysis_tags", []),
            })

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
                command_lines.append({
                    "account_id": line.get("account_id"),
                    "description": line.get("description", ""),
                    "description_ar": line.get("description_ar", ""),
                    "debit": line.get("debit", 0),
                    "credit": line.get("credit", 0),
                    "analysis_tags": line.get("analysis_tags", []),
                })
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
                {"account_id": ln.account_id, "debit": ln.debit, "credit": ln.credit}
                for ln in inst.lines.all()
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
                raise serializers.ValidationError(
                    f"Line {i}: must have either debit or credit (not both)."
                )
            
            effective += 1
            total_debit += debit
            total_credit += credit

        if effective < 2:
            raise serializers.ValidationError(
                "To save as complete, entry must have at least 2 non-empty lines."
            )

        total_debit = total_debit.quantize(MONEY_Q)
        total_credit = total_credit.quantize(MONEY_Q)

        if total_debit == Decimal("0.00") and total_credit == Decimal("0.00"):
            raise serializers.ValidationError(
                "To save as complete, totals cannot both be zero."
            )

        if total_debit != total_credit:
            raise serializers.ValidationError(
                f"To save as complete, journal entry must be balanced. "
                f"Debit={total_debit}, Credit={total_credit}"
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
                command_lines.append({
                    "account_id": line.get("account_id"),
                    "description": line.get("description", ""),
                    "description_ar": line.get("description_ar", ""),
                    "debit": line.get("debit", 0),
                    "credit": line.get("credit", 0),
                    "analysis_tags": line.get("analysis_tags", []),
                })
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
            "id", "public_id", "dimension", "code", "name", "name_ar",
            "description", "description_ar", "parent",
            "is_active", "full_path", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "public_id", "dimension", "full_path", "created_at", "updated_at"]


class AnalysisDimensionSerializer(serializers.ModelSerializer):
    """Serializer for analysis dimensions."""
    values = AnalysisDimensionValueSerializer(many=True, read_only=True)
    
    class Meta:
        model = AnalysisDimension
        fields = [
            "id", "public_id", "company", "code", "name", "name_ar",
            "description", "description_ar",
            "is_required_on_posting", "is_active",
            "applies_to_account_types", "display_order",
            "values", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "public_id", "company", "values", "created_at", "updated_at"]


class AnalysisDimensionCreateSerializer(serializers.Serializer):
    """Serializer for creating dimensions via command."""
    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=100)
    name_ar = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    description_ar = serializers.CharField(required=False, allow_blank=True, default="")
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
            "id", "journal_line", "dimension", "dimension_value",
            "dimension_code", "dimension_name", "value_code", "value_name",
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
            "id", "account", "dimension", "default_value",
            "dimension_code", "dimension_name", "value_code", "value_name",
        ]
        read_only_fields = ["id"]
