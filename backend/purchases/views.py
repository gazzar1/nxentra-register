# purchases/views.py
"""
API views for purchases module.

Views handle HTTP requests and delegate business logic to commands.
"""

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from accounts.authz import resolve_actor
from accounts.module_permissions import ModuleEnabled
from .models import PurchaseBill
from .serializers import (
    PurchaseBillSerializer, PurchaseBillCreateSerializer, PurchaseBillListSerializer,
)
from .commands import (
    create_purchase_bill, post_purchase_bill, void_purchase_bill,
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

        bills = PurchaseBill.objects.filter(company=actor.company).select_related(
            "vendor"
        ).order_by("-bill_date", "-created_at")

        # Optional filters
        if "status" in request.query_params:
            bills = bills.filter(status=request.query_params["status"])
        if "vendor_id" in request.query_params:
            bills = bills.filter(vendor_id=request.query_params["vendor_id"])

        serializer = PurchaseBillListSerializer(bills, many=True)
        return Response(serializer.data)

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
            return Response({"detail": f"Internal error: {str(e)}"}, status=500)


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
