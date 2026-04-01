# purchases/views.py
"""
API views for purchases module.

Views handle HTTP requests and delegate business logic to commands.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authz import resolve_actor
from accounts.module_permissions import ModuleEnabled

from .commands import (
    create_purchase_bill,
    post_purchase_bill,
    void_purchase_bill,
)
from .models import PurchaseBill
from .serializers import (
    PurchaseBillCreateSerializer,
    PurchaseBillListSerializer,
    PurchaseBillSerializer,
)

# =============================================================================
# Purchase Bill Views
# =============================================================================

class PurchaseBillListCreateView(APIView):
    """List all purchase bills or create a new one."""
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from django.db.models import Q

        from nxentra_backend.pagination import paginate_queryset

        bills = PurchaseBill.objects.filter(company=actor.company).select_related(
            "vendor"
        )

        # Optional filters
        if "status" in request.query_params:
            bills = bills.filter(status=request.query_params["status"])
        if "vendor_id" in request.query_params:
            bills = bills.filter(vendor_id=request.query_params["vendor_id"])

        search = request.query_params.get("search", "")
        if search:
            bills = bills.filter(
                Q(bill_number__icontains=search)
                | Q(vendor__name__icontains=search)
                | Q(vendor__code__icontains=search)
            )

        return paginate_queryset(
            request, bills, PurchaseBillListSerializer,
            default_ordering="-bill_date",
            allowed_sort_fields=["bill_number", "bill_date", "due_date", "total_amount", "status"],
        )

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = PurchaseBillCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_purchase_bill(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            PurchaseBillSerializer(result.data["bill"]).data,
            status=status.HTTP_201_CREATED
        )


class PurchaseBillDetailView(APIView):
    """Retrieve, update or delete a purchase bill."""
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            bill = PurchaseBill.objects.select_related(
                "vendor", "posting_profile", "posted_by", "posted_journal_entry"
            ).prefetch_related(
                "lines__item", "lines__account", "lines__tax_code"
            ).get(company=actor.company, pk=pk)
        except PurchaseBill.DoesNotExist:
            return Response({"detail": "Bill not found."}, status=404)

        serializer = PurchaseBillSerializer(bill)
        return Response(serializer.data)


class PurchaseBillPostView(APIView):
    """Post a purchase bill."""
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        import traceback
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            result = post_purchase_bill(actor, pk)
            if not result.success:
                return Response({"detail": result.error}, status=400)

            return Response({
                "detail": "Bill posted successfully.",
                "bill": PurchaseBillSerializer(result.data["bill"]).data,
                "journal_entry_id": result.data["journal_entry"].id,
                "journal_entry_number": result.data["journal_entry"].entry_number,
            })
        except Exception as e:
            traceback.print_exc()
            return Response({"detail": f"Internal error: {e!s}"}, status=500)


class PurchaseBillVoidView(APIView):
    """Void a purchase bill."""
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        reason = request.data.get("reason", "")
        result = void_purchase_bill(actor, pk, reason=reason)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response({
            "detail": "Bill voided successfully.",
            "bill": PurchaseBillSerializer(result.data["bill"]).data,
            "reversing_entry_id": result.data["reversing_entry"].id,
        })


# =============================================================================
# Purchase Order Views
# =============================================================================

class PurchaseOrderListCreateView(APIView):
    """List purchase orders or create a new one."""
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from nxentra_backend.pagination import paginate_queryset

        from .models import PurchaseOrder
        from .serializers import PurchaseOrderListSerializer

        orders = PurchaseOrder.objects.filter(
            company=actor.company
        ).select_related("vendor")

        if "status" in request.query_params:
            orders = orders.filter(status=request.query_params["status"])
        if "vendor_id" in request.query_params:
            orders = orders.filter(vendor_id=request.query_params["vendor_id"])

        return paginate_queryset(
            request, orders, PurchaseOrderListSerializer,
            default_ordering="-order_date",
            allowed_sort_fields=["order_number", "order_date", "expected_delivery_date", "total_amount", "status"],
        )

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .commands import create_purchase_order
        from .serializers import PurchaseOrderCreateSerializer, PurchaseOrderSerializer

        serializer = PurchaseOrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_purchase_order(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            PurchaseOrderSerializer(result.data["order"]).data,
            status=status.HTTP_201_CREATED,
        )


class PurchaseOrderDetailView(APIView):
    """Retrieve a purchase order."""
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .models import PurchaseOrder
        from .serializers import PurchaseOrderSerializer

        try:
            order = PurchaseOrder.objects.select_related(
                "vendor", "posting_profile"
            ).prefetch_related(
                "lines", "lines__account", "lines__tax_code"
            ).get(company=actor.company, pk=pk)
        except PurchaseOrder.DoesNotExist:
            return Response({"detail": "Purchase order not found."}, status=404)

        return Response(PurchaseOrderSerializer(order).data)


class PurchaseOrderApproveView(APIView):
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .commands import approve_purchase_order
        from .serializers import PurchaseOrderSerializer

        result = approve_purchase_order(actor, pk)
        if not result.success:
            return Response({"detail": result.error}, status=400)
        return Response(PurchaseOrderSerializer(result.data["order"]).data)


class PurchaseOrderCancelView(APIView):
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .commands import cancel_purchase_order
        from .serializers import PurchaseOrderSerializer

        reason = request.data.get("reason", "")
        result = cancel_purchase_order(actor, pk, reason=reason)
        if not result.success:
            return Response({"detail": result.error}, status=400)
        return Response(PurchaseOrderSerializer(result.data["order"]).data)


class PurchaseOrderCloseView(APIView):
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .commands import close_purchase_order
        from .serializers import PurchaseOrderSerializer

        result = close_purchase_order(actor, pk)
        if not result.success:
            return Response({"detail": result.error}, status=400)
        return Response(PurchaseOrderSerializer(result.data["order"]).data)


class PurchaseOrderCreateBillView(APIView):
    """Create a vendor bill from a PO's unbilled lines."""
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .commands import create_bill_from_po
        from .serializers import CreateBillFromPOSerializer

        serializer = CreateBillFromPOSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_bill_from_po(actor, pk, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            PurchaseBillSerializer(result.data["bill"]).data,
            status=status.HTTP_201_CREATED,
        )


# =============================================================================
# Goods Receipt Views
# =============================================================================

class GoodsReceiptListCreateView(APIView):
    """List goods receipts or create a new one."""
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from nxentra_backend.pagination import paginate_queryset

        from .models import GoodsReceipt
        from .serializers import GoodsReceiptListSerializer

        receipts = GoodsReceipt.objects.filter(
            company=actor.company
        ).select_related("purchase_order", "vendor", "warehouse")

        if "status" in request.query_params:
            receipts = receipts.filter(status=request.query_params["status"])
        if "purchase_order_id" in request.query_params:
            receipts = receipts.filter(purchase_order_id=request.query_params["purchase_order_id"])

        return paginate_queryset(
            request, receipts, GoodsReceiptListSerializer,
            default_ordering="-receipt_date",
            allowed_sort_fields=["receipt_number", "receipt_date", "status"],
        )

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .commands import create_goods_receipt
        from .serializers import GoodsReceiptCreateSerializer, GoodsReceiptSerializer

        serializer = GoodsReceiptCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_goods_receipt(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            GoodsReceiptSerializer(result.data["receipt"]).data,
            status=status.HTTP_201_CREATED,
        )


class GoodsReceiptDetailView(APIView):
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .models import GoodsReceipt
        from .serializers import GoodsReceiptSerializer

        try:
            receipt = GoodsReceipt.objects.select_related(
                "purchase_order", "vendor", "warehouse"
            ).prefetch_related(
                "lines", "lines__po_line", "lines__item"
            ).get(company=actor.company, pk=pk)
        except GoodsReceipt.DoesNotExist:
            return Response({"detail": "Goods receipt not found."}, status=404)

        return Response(GoodsReceiptSerializer(receipt).data)


class GoodsReceiptPostView(APIView):
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .commands import post_goods_receipt
        from .serializers import GoodsReceiptSerializer

        result = post_goods_receipt(actor, pk)
        if not result.success:
            return Response({"detail": result.error}, status=400)
        return Response(GoodsReceiptSerializer(result.data["receipt"]).data)


class GoodsReceiptVoidView(APIView):
    module_key = "purchases"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .commands import void_goods_receipt
        from .serializers import GoodsReceiptSerializer

        reason = request.data.get("reason", "")
        result = void_goods_receipt(actor, pk, reason=reason)
        if not result.success:
            return Response({"detail": result.error}, status=400)
        return Response(GoodsReceiptSerializer(result.data["receipt"]).data)
