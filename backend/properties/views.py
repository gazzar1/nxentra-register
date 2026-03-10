# properties/views.py
"""
API views for property management module.

Views handle HTTP requests and delegate business logic to commands.
"""

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from accounts.authz import resolve_actor
from accounts.module_permissions import ModuleEnabled
from .models import (
    Property, Unit, Lessee, Lease, RentScheduleLine,
    PaymentReceipt, PaymentAllocation, SecurityDepositTransaction,
    PropertyExpense, PropertyAccountMapping,
)
from .serializers import (
    PropertySerializer, PropertyCreateSerializer, PropertyUpdateSerializer,
    UnitSerializer, UnitCreateSerializer, UnitUpdateSerializer,
    LesseeSerializer, LesseeCreateSerializer, LesseeUpdateSerializer,
    LeaseSerializer, LeaseListSerializer, LeaseCreateSerializer,
    LeaseTerminateSerializer, LeaseRenewSerializer,
    RentScheduleLineSerializer,
    PaymentReceiptSerializer, PaymentCreateSerializer,
    PaymentAllocationSerializer, AllocatePaymentSerializer,
    VoidPaymentSerializer, WaiveScheduleLineSerializer,
    SecurityDepositTransactionSerializer, DepositCreateSerializer,
    PropertyExpenseSerializer, ExpenseCreateSerializer,
    PropertyAccountMappingSerializer, PropertyAccountMappingUpdateSerializer,
)
from .commands import (
    create_property, update_property,
    create_unit, update_unit,
    create_lessee, update_lessee,
    create_lease, activate_lease, terminate_lease, renew_lease,
    record_rent_payment, allocate_rent_payment, void_payment,
    record_deposit_transaction, waive_schedule_line,
    record_property_expense,
    update_property_account_mapping,
)


# =============================================================================
# Property Views
# =============================================================================

class PropertyListCreateView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = Property.objects.filter(company=actor.company).order_by("code")

        # Filters
        if "status" in request.query_params:
            qs = qs.filter(status=request.query_params["status"])
        if "property_type" in request.query_params:
            qs = qs.filter(property_type=request.query_params["property_type"])

        serializer = PropertySerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = PropertyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_property(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            PropertySerializer(result.data["property"]).data,
            status=status.HTTP_201_CREATED,
        )


class PropertyDetailView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get_object(self, actor, pk):
        try:
            return Property.objects.get(company=actor.company, pk=pk)
        except Property.DoesNotExist:
            return None

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        prop = self.get_object(actor, pk)
        if not prop:
            return Response({"detail": "Property not found."}, status=404)

        return Response(PropertySerializer(prop).data)

    def put(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = PropertyUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = update_property(actor, property_id=pk, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(PropertySerializer(result.data["property"]).data)

    patch = put


# =============================================================================
# Unit Views
# =============================================================================

class UnitListCreateView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = Unit.objects.filter(company=actor.company).select_related("property")

        # Filters
        if "property" in request.query_params:
            qs = qs.filter(property_id=request.query_params["property"])
        if "status" in request.query_params:
            qs = qs.filter(status=request.query_params["status"])

        serializer = UnitSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = UnitCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_unit(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            UnitSerializer(result.data["unit"]).data,
            status=status.HTTP_201_CREATED,
        )


class UnitDetailView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get_object(self, actor, pk):
        try:
            return Unit.objects.select_related("property").get(
                company=actor.company, pk=pk
            )
        except Unit.DoesNotExist:
            return None

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        unit = self.get_object(actor, pk)
        if not unit:
            return Response({"detail": "Unit not found."}, status=404)

        return Response(UnitSerializer(unit).data)

    def put(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = UnitUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = update_unit(actor, unit_id=pk, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(UnitSerializer(result.data["unit"]).data)

    patch = put


# =============================================================================
# Lessee Views
# =============================================================================

class LesseeListCreateView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = Lessee.objects.filter(company=actor.company).order_by("code")

        # Filters
        if "status" in request.query_params:
            qs = qs.filter(status=request.query_params["status"])
        if "lessee_type" in request.query_params:
            qs = qs.filter(lessee_type=request.query_params["lessee_type"])

        serializer = LesseeSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = LesseeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_lessee(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            LesseeSerializer(result.data["lessee"]).data,
            status=status.HTTP_201_CREATED,
        )


class LesseeDetailView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get_object(self, actor, pk):
        try:
            return Lessee.objects.get(company=actor.company, pk=pk)
        except Lessee.DoesNotExist:
            return None

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        lessee = self.get_object(actor, pk)
        if not lessee:
            return Response({"detail": "Lessee not found."}, status=404)

        return Response(LesseeSerializer(lessee).data)

    def put(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = LesseeUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = update_lessee(actor, lessee_id=pk, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(LesseeSerializer(result.data["lessee"]).data)

    patch = put


# =============================================================================
# Lease Views
# =============================================================================

class LeaseListCreateView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = Lease.objects.filter(company=actor.company).select_related(
            "property", "unit", "lessee"
        )

        # Filters
        if "status" in request.query_params:
            qs = qs.filter(status=request.query_params["status"])
        if "property" in request.query_params:
            qs = qs.filter(property_id=request.query_params["property"])
        if "lessee" in request.query_params:
            qs = qs.filter(lessee_id=request.query_params["lessee"])

        serializer = LeaseListSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = LeaseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_lease(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            LeaseSerializer(result.data["lease"]).data,
            status=status.HTTP_201_CREATED,
        )


class LeaseDetailView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            lease = Lease.objects.select_related(
                "property", "unit", "lessee"
            ).get(company=actor.company, pk=pk)
        except Lease.DoesNotExist:
            return Response({"detail": "Lease not found."}, status=404)

        return Response(LeaseSerializer(lease).data)


class LeaseActivateView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        result = activate_lease(actor, lease_id=pk)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(LeaseSerializer(result.data["lease"]).data)


class LeaseTerminateView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = LeaseTerminateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = terminate_lease(
            actor,
            lease_id=pk,
            **serializer.validated_data,
        )
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(LeaseSerializer(result.data["lease"]).data)


class LeaseRenewView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = LeaseRenewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = renew_lease(
            actor,
            lease_id=pk,
            **serializer.validated_data,
        )
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response({
            "old_lease": LeaseSerializer(result.data["old_lease"]).data,
            "new_lease": LeaseSerializer(result.data["new_lease"]).data,
        })


class LeaseScheduleView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            lease = Lease.objects.get(company=actor.company, pk=pk)
        except Lease.DoesNotExist:
            return Response({"detail": "Lease not found."}, status=404)

        lines = RentScheduleLine.objects.filter(lease=lease).order_by("installment_no")
        return Response(RentScheduleLineSerializer(lines, many=True).data)


class WaiveScheduleLineView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = WaiveScheduleLineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = waive_schedule_line(
            actor,
            schedule_line_id=pk,
            **serializer.validated_data,
        )
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(RentScheduleLineSerializer(result.data["schedule_line"]).data)


# =============================================================================
# Payment Views
# =============================================================================

class PaymentListCreateView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = PaymentReceipt.objects.filter(
            company=actor.company
        ).select_related("lessee", "lease")

        if "lease" in request.query_params:
            qs = qs.filter(lease_id=request.query_params["lease"])
        if "lessee" in request.query_params:
            qs = qs.filter(lessee_id=request.query_params["lessee"])
        if "voided" in request.query_params:
            qs = qs.filter(voided=request.query_params["voided"].lower() == "true")

        return Response(PaymentReceiptSerializer(qs, many=True).data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = record_rent_payment(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            PaymentReceiptSerializer(result.data["payment"]).data,
            status=status.HTTP_201_CREATED,
        )


class PaymentDetailView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            payment = PaymentReceipt.objects.select_related(
                "lessee", "lease"
            ).get(company=actor.company, pk=pk)
        except PaymentReceipt.DoesNotExist:
            return Response({"detail": "Payment not found."}, status=404)

        return Response(PaymentReceiptSerializer(payment).data)


class PaymentAllocateView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = AllocatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = allocate_rent_payment(
            actor,
            payment_id=pk,
            allocations=serializer.validated_data["allocations"],
        )
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            PaymentReceiptSerializer(result.data["payment"]).data,
        )


class PaymentAllocationsListView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            payment = PaymentReceipt.objects.get(company=actor.company, pk=pk)
        except PaymentReceipt.DoesNotExist:
            return Response({"detail": "Payment not found."}, status=404)

        allocs = PaymentAllocation.objects.filter(
            payment=payment
        ).select_related("schedule_line")
        return Response(PaymentAllocationSerializer(allocs, many=True).data)


class PaymentVoidView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = VoidPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = void_payment(
            actor,
            payment_id=pk,
            **serializer.validated_data,
        )
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(PaymentReceiptSerializer(result.data["payment"]).data)


# =============================================================================
# Deposit Views
# =============================================================================

class DepositListCreateView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = SecurityDepositTransaction.objects.filter(
            company=actor.company
        ).select_related("lease")

        if "lease" in request.query_params:
            qs = qs.filter(lease_id=request.query_params["lease"])

        return Response(SecurityDepositTransactionSerializer(qs, many=True).data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = DepositCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = record_deposit_transaction(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            SecurityDepositTransactionSerializer(result.data["transaction"]).data,
            status=status.HTTP_201_CREATED,
        )


# =============================================================================
# Expense Views
# =============================================================================

class ExpenseListCreateView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = PropertyExpense.objects.filter(
            company=actor.company
        ).select_related("property", "unit")

        if "property" in request.query_params:
            qs = qs.filter(property_id=request.query_params["property"])
        if "category" in request.query_params:
            qs = qs.filter(category=request.query_params["category"])
        if "payment_mode" in request.query_params:
            qs = qs.filter(payment_mode=request.query_params["payment_mode"])

        return Response(PropertyExpenseSerializer(qs, many=True).data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = ExpenseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = record_property_expense(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            PropertyExpenseSerializer(result.data["expense"]).data,
            status=status.HTTP_201_CREATED,
        )


class ExpenseDetailView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            expense = PropertyExpense.objects.select_related(
                "property", "unit"
            ).get(company=actor.company, pk=pk)
        except PropertyExpense.DoesNotExist:
            return Response({"detail": "Expense not found."}, status=404)

        return Response(PropertyExpenseSerializer(expense).data)


# =============================================================================
# Account Mapping View
# =============================================================================

class PropertyAccountMappingView(APIView):
    module_key = "properties"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

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
            ).get(company=actor.company)
        except PropertyAccountMapping.DoesNotExist:
            return Response({})

        return Response(PropertyAccountMappingSerializer(mapping).data)

    def put(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = PropertyAccountMappingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = update_property_account_mapping(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        mapping = result.data["mapping"]
        # Refresh with related objects
        mapping = PropertyAccountMapping.objects.select_related(
            "rental_income_account",
            "other_income_account",
            "accounts_receivable_account",
            "cash_bank_account",
            "unapplied_cash_account",
            "security_deposit_account",
            "accounts_payable_account",
            "property_expense_account",
        ).get(pk=mapping.pk)

        return Response(PropertyAccountMappingSerializer(mapping).data)
