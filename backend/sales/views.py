# sales/views.py
"""
API views for sales module.

Views handle HTTP requests and delegate business logic to commands.
"""

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from accounts.authz import resolve_actor
from accounts.module_permissions import ModuleEnabled
from .models import Item, TaxCode, PostingProfile, SalesInvoice
from .serializers import (
    ItemSerializer, ItemCreateSerializer, ItemUpdateSerializer,
    TaxCodeSerializer, TaxCodeCreateSerializer, TaxCodeUpdateSerializer,
    PostingProfileSerializer, PostingProfileCreateSerializer, PostingProfileUpdateSerializer,
    SalesInvoiceSerializer, SalesInvoiceCreateSerializer, SalesInvoiceUpdateSerializer, SalesInvoiceListSerializer,
)
from .commands import (
    create_item, update_item,
    create_tax_code, update_tax_code,
    create_posting_profile, update_posting_profile,
    create_sales_invoice, update_sales_invoice, post_sales_invoice, void_sales_invoice,
)


# =============================================================================
# Item Views
# =============================================================================

class ItemListCreateView(APIView):
    """List all items or create a new item."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        items = Item.objects.filter(company=actor.company).order_by("code")

        # Optional filters
        if "is_active" in request.query_params:
            is_active = request.query_params["is_active"].lower() == "true"
            items = items.filter(is_active=is_active)
        if "item_type" in request.query_params:
            items = items.filter(item_type=request.query_params["item_type"])

        serializer = ItemSerializer(items, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = ItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_item(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            ItemSerializer(result.data["item"]).data,
            status=status.HTTP_201_CREATED
        )


class ItemDetailView(APIView):
    """Retrieve, update or delete an item."""
    permission_classes = [IsAuthenticated]

    def get_object(self, actor, pk):
        try:
            return Item.objects.get(company=actor.company, pk=pk)
        except Item.DoesNotExist:
            return None

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        item = self.get_object(actor, pk)
        if not item:
            return Response({"detail": "Item not found."}, status=404)

        serializer = ItemSerializer(item)
        return Response(serializer.data)

    def patch(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        item = self.get_object(actor, pk)
        if not item:
            return Response({"detail": "Item not found."}, status=404)

        serializer = ItemUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        result = update_item(actor, pk, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(ItemSerializer(result.data["item"]).data)


# =============================================================================
# Tax Code Views
# =============================================================================

class TaxCodeListCreateView(APIView):
    """List all tax codes or create a new one."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        tax_codes = TaxCode.objects.filter(company=actor.company).order_by("code")

        # Optional filters
        if "is_active" in request.query_params:
            is_active = request.query_params["is_active"].lower() == "true"
            tax_codes = tax_codes.filter(is_active=is_active)
        if "direction" in request.query_params:
            tax_codes = tax_codes.filter(direction=request.query_params["direction"])

        serializer = TaxCodeSerializer(tax_codes, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = TaxCodeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_tax_code(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            TaxCodeSerializer(result.data["tax_code"]).data,
            status=status.HTTP_201_CREATED
        )


class TaxCodeDetailView(APIView):
    """Retrieve or update a tax code."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            tax_code = TaxCode.objects.get(company=actor.company, pk=pk)
        except TaxCode.DoesNotExist:
            return Response({"detail": "Tax code not found."}, status=404)

        serializer = TaxCodeSerializer(tax_code)
        return Response(serializer.data)

    def patch(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = TaxCodeUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = update_tax_code(actor, pk, **serializer.validated_data)
        if not result.success:
            return Response({"error": result.error}, status=400)

        return Response(TaxCodeSerializer(result.data["tax_code"]).data)


# =============================================================================
# Posting Profile Views
# =============================================================================

class PostingProfileListCreateView(APIView):
    """List all posting profiles or create a new one."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        profiles = PostingProfile.objects.filter(company=actor.company).order_by("profile_type", "code")

        # Optional filters
        if "is_active" in request.query_params:
            is_active = request.query_params["is_active"].lower() == "true"
            profiles = profiles.filter(is_active=is_active)
        if "profile_type" in request.query_params:
            profiles = profiles.filter(profile_type=request.query_params["profile_type"])

        serializer = PostingProfileSerializer(profiles, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = PostingProfileCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_posting_profile(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            PostingProfileSerializer(result.data["posting_profile"]).data,
            status=status.HTTP_201_CREATED
        )


class PostingProfileDetailView(APIView):
    """Retrieve or update a posting profile."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            profile = PostingProfile.objects.get(company=actor.company, pk=pk)
        except PostingProfile.DoesNotExist:
            return Response({"detail": "Posting profile not found."}, status=404)

        serializer = PostingProfileSerializer(profile)
        return Response(serializer.data)

    def patch(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = PostingProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = update_posting_profile(actor, pk, **serializer.validated_data)
        if not result.success:
            return Response({"error": result.error}, status=400)

        return Response(PostingProfileSerializer(result.data["posting_profile"]).data)


# =============================================================================
# Sales Invoice Views
# =============================================================================

class SalesInvoiceListCreateView(APIView):
    """List all sales invoices or create a new one."""
    module_key = "sales"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        invoices = SalesInvoice.objects.filter(company=actor.company).select_related(
            "customer"
        ).order_by("-invoice_date", "-created_at")

        # Optional filters
        if "status" in request.query_params:
            invoices = invoices.filter(status=request.query_params["status"])
        if "customer_id" in request.query_params:
            invoices = invoices.filter(customer_id=request.query_params["customer_id"])

        serializer = SalesInvoiceListSerializer(invoices, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = SalesInvoiceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_sales_invoice(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            SalesInvoiceSerializer(result.data["invoice"]).data,
            status=status.HTTP_201_CREATED
        )


class SalesInvoiceDetailView(APIView):
    """Retrieve, update or delete a sales invoice."""
    module_key = "sales"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            invoice = SalesInvoice.objects.select_related(
                "customer", "posting_profile", "posted_by", "posted_journal_entry"
            ).prefetch_related(
                "lines__item", "lines__account", "lines__tax_code"
            ).get(company=actor.company, pk=pk)
        except SalesInvoice.DoesNotExist:
            return Response({"detail": "Invoice not found."}, status=404)

        serializer = SalesInvoiceSerializer(invoice)
        return Response(serializer.data)

    def patch(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = SalesInvoiceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = update_sales_invoice(actor, pk, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        # Refresh the invoice with related data
        invoice = SalesInvoice.objects.select_related(
            "customer", "posting_profile", "posted_by", "posted_journal_entry"
        ).prefetch_related(
            "lines__item", "lines__account", "lines__tax_code"
        ).get(pk=pk)

        return Response(SalesInvoiceSerializer(invoice).data)


class SalesInvoicePostView(APIView):
    """Post a sales invoice."""
    module_key = "sales"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        result = post_sales_invoice(actor, pk)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response({
            "detail": "Invoice posted successfully.",
            "invoice": SalesInvoiceSerializer(result.data["invoice"]).data,
            "journal_entry_id": result.data["journal_entry"].id,
            "journal_entry_number": result.data["journal_entry"].entry_number,
        })


class SalesInvoiceVoidView(APIView):
    """Void a sales invoice."""
    module_key = "sales"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        reason = request.data.get("reason", "")
        result = void_sales_invoice(actor, pk, reason=reason)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response({
            "detail": "Invoice voided successfully.",
            "invoice": SalesInvoiceSerializer(result.data["invoice"]).data,
            "reversing_entry_id": result.data["reversing_entry"].id,
        })


# =============================================================================
# Open Invoices View (for receipt allocation)
# =============================================================================

class CustomerOpenInvoicesView(APIView):
    """
    GET /api/sales/customers/<customer_id>/open-invoices/

    Returns all posted invoices for a customer that have an outstanding balance.
    Used by the receipt form to show invoices available for allocation.
    """
    module_key = "sales"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request, customer_id):
        from accounting.models import Customer
        from decimal import Decimal

        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        # Validate customer
        try:
            customer = Customer.objects.get(company=actor.company, pk=customer_id)
        except Customer.DoesNotExist:
            return Response({"detail": "Customer not found."}, status=404)

        # Get posted invoices with outstanding balance
        invoices = SalesInvoice.objects.filter(
            company=actor.company,
            customer=customer,
            status=SalesInvoice.Status.POSTED,
        ).order_by("invoice_date", "invoice_number")

        open_invoices = []
        for inv in invoices:
            amount_due = inv.total_amount - inv.amount_paid
            if amount_due > Decimal("0"):
                open_invoices.append({
                    "id": inv.id,
                    "public_id": str(inv.public_id),
                    "invoice_number": inv.invoice_number,
                    "invoice_date": inv.invoice_date.isoformat(),
                    "due_date": inv.due_date.isoformat() if inv.due_date else None,
                    "total_amount": str(inv.total_amount),
                    "amount_paid": str(inv.amount_paid),
                    "amount_due": str(amount_due),
                    "reference": inv.reference,
                })

        return Response({
            "customer_id": customer_id,
            "customer_code": customer.code,
            "customer_name": customer.name,
            "open_invoices": open_invoices,
            "total_outstanding": str(sum(
                inv.total_amount - inv.amount_paid
                for inv in invoices
                if (inv.total_amount - inv.amount_paid) > Decimal("0")
            )),
        })
