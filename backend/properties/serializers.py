# properties/serializers.py
"""
Serializers for property management API.

Input validation and output formatting only.
Business logic happens in commands.py.
"""

from rest_framework import serializers
from .models import (
    Property, Unit, Lessee, Lease, RentScheduleLine,
    PaymentReceipt, PaymentAllocation, SecurityDepositTransaction,
    PropertyExpense, PropertyAccountMapping,
)


# =============================================================================
# Property Serializers
# =============================================================================

class PropertySerializer(serializers.ModelSerializer):
    unit_count = serializers.SerializerMethodField()

    class Meta:
        model = Property
        fields = [
            "id", "public_id", "code", "name", "name_ar",
            "property_type", "owner_entity_ref",
            "address", "city", "region", "country",
            "status", "acquisition_date", "area_sqm", "valuation",
            "notes", "unit_count", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "public_id", "created_at", "updated_at"]

    def get_unit_count(self, obj):
        return obj.units.count()


class PropertyCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=255)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    property_type = serializers.ChoiceField(choices=Property.PropertyType.choices)
    owner_entity_ref = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True, default=None)
    address = serializers.CharField(required=False, allow_blank=True, default="")
    city = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    region = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    country = serializers.CharField(max_length=3, required=False, default="SA")
    acquisition_date = serializers.DateField(required=False, allow_null=True, default=None)
    area_sqm = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, default=None)
    valuation = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class PropertyUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True)
    property_type = serializers.ChoiceField(choices=Property.PropertyType.choices, required=False)
    owner_entity_ref = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    address = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    region = serializers.CharField(max_length=100, required=False, allow_blank=True)
    country = serializers.CharField(max_length=3, required=False)
    status = serializers.ChoiceField(choices=Property.PropertyStatus.choices, required=False)
    acquisition_date = serializers.DateField(required=False, allow_null=True)
    area_sqm = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    valuation = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)


# =============================================================================
# Unit Serializers
# =============================================================================

class UnitSerializer(serializers.ModelSerializer):
    property_code = serializers.CharField(source="property.code", read_only=True)
    property_name = serializers.CharField(source="property.name", read_only=True)

    class Meta:
        model = Unit
        fields = [
            "id", "public_id", "property", "property_code", "property_name",
            "unit_code", "floor", "unit_type",
            "bedrooms", "bathrooms", "area_sqm",
            "status", "default_rent", "notes",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "public_id", "created_at", "updated_at"]


class UnitCreateSerializer(serializers.Serializer):
    property_id = serializers.IntegerField()
    unit_code = serializers.CharField(max_length=20)
    unit_type = serializers.ChoiceField(choices=Unit.UnitType.choices)
    floor = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True, default=None)
    bedrooms = serializers.IntegerField(required=False, allow_null=True, default=None)
    bathrooms = serializers.IntegerField(required=False, allow_null=True, default=None)
    area_sqm = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, default=None)
    default_rent = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class UnitUpdateSerializer(serializers.Serializer):
    unit_type = serializers.ChoiceField(choices=Unit.UnitType.choices, required=False)
    floor = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    bedrooms = serializers.IntegerField(required=False, allow_null=True)
    bathrooms = serializers.IntegerField(required=False, allow_null=True)
    area_sqm = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    status = serializers.ChoiceField(choices=Unit.UnitStatus.choices, required=False)
    default_rent = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)


# =============================================================================
# Lessee Serializers
# =============================================================================

class LesseeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lessee
        fields = [
            "id", "public_id", "code", "lessee_type",
            "display_name", "display_name_ar",
            "national_id", "phone", "whatsapp", "email",
            "address", "emergency_contact",
            "status", "risk_rating", "notes",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "public_id", "created_at", "updated_at"]


class LesseeCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20)
    lessee_type = serializers.ChoiceField(choices=Lessee.LesseeType.choices)
    display_name = serializers.CharField(max_length=255)
    display_name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    national_id = serializers.CharField(max_length=50, required=False, allow_blank=True, allow_null=True, default=None)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, allow_null=True, default=None)
    whatsapp = serializers.CharField(max_length=30, required=False, allow_blank=True, allow_null=True, default=None)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True, default=None)
    address = serializers.CharField(required=False, allow_blank=True, allow_null=True, default=None)
    emergency_contact = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True, default=None)
    risk_rating = serializers.ChoiceField(choices=Lessee.RiskRating.choices, required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class LesseeUpdateSerializer(serializers.Serializer):
    lessee_type = serializers.ChoiceField(choices=Lessee.LesseeType.choices, required=False)
    display_name = serializers.CharField(max_length=255, required=False)
    display_name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True)
    national_id = serializers.CharField(max_length=50, required=False, allow_blank=True, allow_null=True)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, allow_null=True)
    whatsapp = serializers.CharField(max_length=30, required=False, allow_blank=True, allow_null=True)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    emergency_contact = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    status = serializers.ChoiceField(choices=Lessee.LesseeStatus.choices, required=False)
    risk_rating = serializers.ChoiceField(choices=Lessee.RiskRating.choices, required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)


# =============================================================================
# Lease Serializers
# =============================================================================

class RentScheduleLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = RentScheduleLine
        fields = [
            "id", "public_id", "installment_no",
            "period_start", "period_end", "due_date",
            "base_rent", "adjustments", "penalties",
            "total_due", "total_allocated", "outstanding",
            "status", "created_at",
        ]
        read_only_fields = fields


class LeaseSerializer(serializers.ModelSerializer):
    property_code = serializers.CharField(source="property.code", read_only=True)
    property_name = serializers.CharField(source="property.name", read_only=True)
    unit_code = serializers.CharField(source="unit.unit_code", read_only=True, default=None)
    lessee_name = serializers.CharField(source="lessee.display_name", read_only=True)
    lessee_code = serializers.CharField(source="lessee.code", read_only=True)

    class Meta:
        model = Lease
        fields = [
            "id", "public_id", "contract_no",
            "property", "property_code", "property_name",
            "unit", "unit_code",
            "lessee", "lessee_name", "lessee_code",
            "start_date", "end_date", "handover_date",
            "payment_frequency", "rent_amount", "currency",
            "grace_days", "due_day_rule", "specific_due_day",
            "deposit_amount", "status",
            "renewed_from_lease", "renewal_option",
            "notice_period_days", "terms_summary", "document_ref",
            "activated_at", "terminated_at", "termination_reason",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "public_id",
            "property_code", "property_name", "unit_code",
            "lessee_name", "lessee_code",
            "activated_at", "terminated_at",
            "created_at", "updated_at",
        ]


class LeaseListSerializer(serializers.ModelSerializer):
    property_code = serializers.CharField(source="property.code", read_only=True)
    property_name = serializers.CharField(source="property.name", read_only=True)
    unit_code = serializers.CharField(source="unit.unit_code", read_only=True, default=None)
    lessee_name = serializers.CharField(source="lessee.display_name", read_only=True)

    class Meta:
        model = Lease
        fields = [
            "id", "public_id", "contract_no",
            "property_code", "property_name", "unit_code",
            "lessee_name", "start_date", "end_date",
            "rent_amount", "currency", "status",
            "created_at",
        ]
        read_only_fields = fields


class LeaseCreateSerializer(serializers.Serializer):
    contract_no = serializers.CharField(max_length=50)
    property_id = serializers.IntegerField()
    unit_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    lessee_id = serializers.IntegerField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    handover_date = serializers.DateField(required=False, allow_null=True, default=None)
    payment_frequency = serializers.ChoiceField(choices=Lease.PaymentFrequency.choices)
    rent_amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    currency = serializers.CharField(max_length=3, required=False, default="SAR")
    grace_days = serializers.IntegerField(required=False, default=0)
    due_day_rule = serializers.ChoiceField(choices=Lease.DueDayRule.choices)
    specific_due_day = serializers.IntegerField(required=False, allow_null=True, default=None)
    deposit_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default=0)
    renewal_option = serializers.BooleanField(required=False, default=False)
    notice_period_days = serializers.IntegerField(required=False, allow_null=True, default=None)
    terms_summary = serializers.CharField(required=False, allow_blank=True, allow_null=True, default=None)
    document_ref = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True, default=None)


class LeaseUpdateSerializer(serializers.Serializer):
    contract_no = serializers.CharField(max_length=50, required=False)
    property_id = serializers.IntegerField(required=False)
    unit_id = serializers.IntegerField(required=False, allow_null=True)
    lessee_id = serializers.IntegerField(required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    handover_date = serializers.DateField(required=False, allow_null=True)
    payment_frequency = serializers.ChoiceField(choices=Lease.PaymentFrequency.choices, required=False)
    rent_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    currency = serializers.CharField(max_length=3, required=False)
    grace_days = serializers.IntegerField(required=False)
    due_day_rule = serializers.ChoiceField(choices=Lease.DueDayRule.choices, required=False)
    specific_due_day = serializers.IntegerField(required=False, allow_null=True)
    deposit_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    renewal_option = serializers.BooleanField(required=False)
    notice_period_days = serializers.IntegerField(required=False, allow_null=True)
    terms_summary = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    document_ref = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)


class LeaseTerminateSerializer(serializers.Serializer):
    termination_reason = serializers.CharField()


class LeaseRenewSerializer(serializers.Serializer):
    new_contract_no = serializers.CharField(max_length=50)
    new_start_date = serializers.DateField()
    new_end_date = serializers.DateField()
    new_rent_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True, default=None)
    new_payment_frequency = serializers.ChoiceField(choices=Lease.PaymentFrequency.choices, required=False, allow_null=True, default=None)
    new_due_day_rule = serializers.ChoiceField(choices=Lease.DueDayRule.choices, required=False, allow_null=True, default=None)
    new_specific_due_day = serializers.IntegerField(required=False, allow_null=True, default=None)
    new_grace_days = serializers.IntegerField(required=False, allow_null=True, default=None)
    new_deposit_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True, default=None)


# =============================================================================
# Payment Serializers
# =============================================================================

class PaymentReceiptSerializer(serializers.ModelSerializer):
    lessee_name = serializers.CharField(source="lessee.display_name", read_only=True)
    lease_contract_no = serializers.CharField(source="lease.contract_no", read_only=True)

    class Meta:
        model = PaymentReceipt
        fields = [
            "id", "public_id", "receipt_no",
            "lessee", "lessee_name",
            "lease", "lease_contract_no",
            "payment_date", "amount", "currency",
            "method", "reference_no", "notes",
            "allocation_status", "voided", "voided_at", "voided_reason",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "public_id", "lessee_name", "lease_contract_no",
            "allocation_status", "voided", "voided_at", "voided_reason",
            "created_at", "updated_at",
        ]


class PaymentCreateSerializer(serializers.Serializer):
    receipt_no = serializers.CharField(max_length=50)
    lease_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    payment_date = serializers.DateField()
    method = serializers.ChoiceField(choices=PaymentReceipt.PaymentMethod.choices)
    currency = serializers.CharField(max_length=3, required=False, default="SAR")
    reference_no = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True, default=None)


class PaymentAllocationSerializer(serializers.ModelSerializer):
    installment_no = serializers.IntegerField(source="schedule_line.installment_no", read_only=True)
    due_date = serializers.DateField(source="schedule_line.due_date", read_only=True)

    class Meta:
        model = PaymentAllocation
        fields = [
            "id", "public_id", "payment", "schedule_line",
            "installment_no", "due_date",
            "allocated_amount", "created_at",
        ]
        read_only_fields = fields


class AllocatePaymentSerializer(serializers.Serializer):
    allocations = serializers.ListField(
        child=serializers.DictField(),
        min_length=1,
    )


class VoidPaymentSerializer(serializers.Serializer):
    reason = serializers.CharField()


class WaiveScheduleLineSerializer(serializers.Serializer):
    reason = serializers.CharField()


# =============================================================================
# Deposit Serializers
# =============================================================================

class SecurityDepositTransactionSerializer(serializers.ModelSerializer):
    lease_contract_no = serializers.CharField(source="lease.contract_no", read_only=True)

    class Meta:
        model = SecurityDepositTransaction
        fields = [
            "id", "public_id", "lease", "lease_contract_no",
            "transaction_type", "amount", "currency",
            "transaction_date", "reason", "reference",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "public_id", "lease_contract_no",
            "created_at", "updated_at",
        ]


class DepositCreateSerializer(serializers.Serializer):
    lease_id = serializers.IntegerField()
    transaction_type = serializers.ChoiceField(
        choices=SecurityDepositTransaction.DepositTransactionType.choices,
    )
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    transaction_date = serializers.DateField()
    currency = serializers.CharField(max_length=3, required=False, default="SAR")
    reason = serializers.CharField(required=False, allow_blank=True, allow_null=True, default=None)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True, default=None)


# =============================================================================
# Expense Serializers
# =============================================================================

class PropertyExpenseSerializer(serializers.ModelSerializer):
    property_code = serializers.CharField(source="property.code", read_only=True)
    property_name = serializers.CharField(source="property.name", read_only=True)
    unit_code = serializers.CharField(source="unit.unit_code", read_only=True, default=None)

    class Meta:
        model = PropertyExpense
        fields = [
            "id", "public_id",
            "property", "property_code", "property_name",
            "unit", "unit_code",
            "category", "vendor_ref", "expense_date",
            "amount", "currency", "payment_mode", "paid_status",
            "description", "document_ref",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "public_id", "property_code", "property_name",
            "unit_code", "created_at", "updated_at",
        ]


class ExpenseCreateSerializer(serializers.Serializer):
    property_id = serializers.IntegerField()
    unit_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    category = serializers.ChoiceField(choices=PropertyExpense.ExpenseCategory.choices)
    vendor_ref = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True, default=None)
    expense_date = serializers.DateField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    currency = serializers.CharField(max_length=3, required=False, default="SAR")
    payment_mode = serializers.ChoiceField(choices=PropertyExpense.PaymentMode.choices)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True, default=None)
    document_ref = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True, default=None)


# =============================================================================
# Account Mapping Serializer
# =============================================================================

class PropertyAccountMappingSerializer(serializers.ModelSerializer):
    rental_income_account_code = serializers.CharField(
        source="rental_income_account.code", read_only=True, default=None
    )
    other_income_account_code = serializers.CharField(
        source="other_income_account.code", read_only=True, default=None
    )
    accounts_receivable_account_code = serializers.CharField(
        source="accounts_receivable_account.code", read_only=True, default=None
    )
    cash_bank_account_code = serializers.CharField(
        source="cash_bank_account.code", read_only=True, default=None
    )
    unapplied_cash_account_code = serializers.CharField(
        source="unapplied_cash_account.code", read_only=True, default=None
    )
    security_deposit_account_code = serializers.CharField(
        source="security_deposit_account.code", read_only=True, default=None
    )
    accounts_payable_account_code = serializers.CharField(
        source="accounts_payable_account.code", read_only=True, default=None
    )
    property_expense_account_code = serializers.CharField(
        source="property_expense_account.code", read_only=True, default=None
    )

    class Meta:
        model = PropertyAccountMapping
        fields = [
            "id", "public_id",
            "rental_income_account", "rental_income_account_code",
            "other_income_account", "other_income_account_code",
            "accounts_receivable_account", "accounts_receivable_account_code",
            "cash_bank_account", "cash_bank_account_code",
            "unapplied_cash_account", "unapplied_cash_account_code",
            "security_deposit_account", "security_deposit_account_code",
            "accounts_payable_account", "accounts_payable_account_code",
            "property_expense_account", "property_expense_account_code",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "public_id", "created_at", "updated_at"]


class PropertyAccountMappingUpdateSerializer(serializers.Serializer):
    rental_income_account_id = serializers.IntegerField(required=False, allow_null=True)
    other_income_account_id = serializers.IntegerField(required=False, allow_null=True)
    accounts_receivable_account_id = serializers.IntegerField(required=False, allow_null=True)
    cash_bank_account_id = serializers.IntegerField(required=False, allow_null=True)
    unapplied_cash_account_id = serializers.IntegerField(required=False, allow_null=True)
    security_deposit_account_id = serializers.IntegerField(required=False, allow_null=True)
    accounts_payable_account_id = serializers.IntegerField(required=False, allow_null=True)
    property_expense_account_id = serializers.IntegerField(required=False, allow_null=True)
