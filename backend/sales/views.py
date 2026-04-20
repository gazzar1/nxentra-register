# sales/views.py
"""
API views for sales module.

Views handle HTTP requests and delegate business logic to commands.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authz import resolve_actor
from accounts.module_permissions import ModuleEnabled

from .commands import (
    create_item,
    create_posting_profile,
    create_sales_invoice,
    create_tax_code,
    post_sales_invoice,
    update_item,
    update_posting_profile,
    update_sales_invoice,
    update_tax_code,
    void_sales_invoice,
)
from .models import Item, PostingProfile, SalesInvoice, TaxCode
from .serializers import (
    ItemCreateSerializer,
    ItemSerializer,
    ItemUpdateSerializer,
    PostingProfileCreateSerializer,
    PostingProfileSerializer,
    PostingProfileUpdateSerializer,
    SalesInvoiceCreateSerializer,
    SalesInvoiceListSerializer,
    SalesInvoiceSerializer,
    SalesInvoiceUpdateSerializer,
    TaxCodeCreateSerializer,
    TaxCodeSerializer,
    TaxCodeUpdateSerializer,
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

        return Response(ItemSerializer(result.data["item"]).data, status=status.HTTP_201_CREATED)


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


class ItemImageUploadView(APIView):
    """
    POST /api/sales/items/<pk>/image/ — Upload item photo
    DELETE /api/sales/items/<pk>/image/ — Remove item photo
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        import os

        from django.core.files.storage import default_storage

        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            item = Item.objects.get(company=actor.company, pk=pk)
        except Item.DoesNotExist:
            return Response({"detail": "Item not found."}, status=404)

        if "image" not in request.FILES:
            return Response({"detail": "No image file provided."}, status=400)

        image_file = request.FILES["image"]

        # Validate file type by extension OR content type
        allowed_extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        allowed_mimes = {"image/png", "image/jpeg", "image/webp", "image/gif"}
        ext = os.path.splitext(image_file.name)[1].lower()
        content_type = getattr(image_file, "content_type", "")

        if ext not in allowed_extensions and content_type not in allowed_mimes:
            return Response(
                {"detail": f"Invalid file type '{ext or content_type}'. Allowed: PNG, JPG, WEBP, GIF."},
                status=400,
            )

        # If file has no extension, add one based on content type
        if not ext and content_type:
            mime_to_ext = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/webp": ".webp",
                "image/gif": ".gif",
            }
            inferred_ext = mime_to_ext.get(content_type, "")
            if inferred_ext:
                image_file.name = image_file.name + inferred_ext

        # Validate size (10MB — matches UI label)
        if image_file.size > 10 * 1024 * 1024:
            return Response({"detail": "File too large. Maximum size is 10MB."}, status=400)

        # Delete old image if exists
        if item.image:
            try:
                default_storage.delete(item.image.name)
            except Exception:
                pass

        from projections.write_barrier import command_writes_allowed

        with command_writes_allowed():
            item.image = image_file
            item.save(update_fields=["image"])

        image_url = request.build_absolute_uri(item.image.url) if item.image else None
        return Response({"image_url": image_url})

    def delete(self, request, pk):
        from django.core.files.storage import default_storage

        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            item = Item.objects.get(company=actor.company, pk=pk)
        except Item.DoesNotExist:
            return Response({"detail": "Item not found."}, status=404)

        if item.image:
            try:
                default_storage.delete(item.image.name)
            except Exception:
                pass

            from projections.write_barrier import command_writes_allowed

            with command_writes_allowed():
                item.image = None
                item.save(update_fields=["image"])

        return Response(status=status.HTTP_204_NO_CONTENT)


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

        return Response(TaxCodeSerializer(result.data["tax_code"]).data, status=status.HTTP_201_CREATED)


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

        return Response(PostingProfileSerializer(result.data["posting_profile"]).data, status=status.HTTP_201_CREATED)


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

        from django.db.models import Q

        from nxentra_backend.pagination import paginate_queryset

        invoices = SalesInvoice.objects.filter(company=actor.company).select_related("customer")

        # Optional filters
        if "status" in request.query_params:
            invoices = invoices.filter(status=request.query_params["status"])
        if "customer_id" in request.query_params:
            invoices = invoices.filter(customer_id=request.query_params["customer_id"])

        search = request.query_params.get("search", "")
        if search:
            invoices = invoices.filter(
                Q(invoice_number__icontains=search)
                | Q(customer__name__icontains=search)
                | Q(customer__code__icontains=search)
            )

        return paginate_queryset(
            request,
            invoices,
            SalesInvoiceListSerializer,
            default_ordering="-invoice_date",
            allowed_sort_fields=["invoice_number", "invoice_date", "due_date", "total_amount", "status"],
        )

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        serializer = SalesInvoiceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_sales_invoice(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(SalesInvoiceSerializer(result.data["invoice"]).data, status=status.HTTP_201_CREATED)


class SalesInvoiceDetailView(APIView):
    """Retrieve, update or delete a sales invoice."""

    module_key = "sales"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            invoice = (
                SalesInvoice.objects.select_related("customer", "posting_profile", "posted_by", "posted_journal_entry")
                .prefetch_related("lines__item", "lines__account", "lines__tax_code")
                .get(company=actor.company, pk=pk)
            )
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
        invoice = (
            SalesInvoice.objects.select_related("customer", "posting_profile", "posted_by", "posted_journal_entry")
            .prefetch_related("lines__item", "lines__account", "lines__tax_code")
            .get(pk=pk)
        )

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

        return Response(
            {
                "detail": "Invoice posted successfully.",
                "invoice": SalesInvoiceSerializer(result.data["invoice"]).data,
                "journal_entry_id": result.data["journal_entry"].id,
                "journal_entry_number": result.data["journal_entry"].entry_number,
            }
        )


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

        return Response(
            {
                "detail": "Invoice voided successfully.",
                "invoice": SalesInvoiceSerializer(result.data["invoice"]).data,
                "reversing_entry_id": result.data["reversing_entry"].id,
            }
        )


# =============================================================================
# PDF Generation
# =============================================================================


class SalesInvoicePDFView(APIView):
    """
    GET /api/sales/invoices/<pk>/pdf/

    Returns a PDF file for the sales invoice.
    Query params:
    - inline: if "1", display in browser instead of downloading
    """

    module_key = "sales"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request, pk):
        import base64
        from pathlib import Path

        from django.http import HttpResponse
        from django.template.loader import render_to_string

        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            invoice = (
                SalesInvoice.objects.select_related("customer", "posting_profile", "posted_by", "posted_journal_entry")
                .prefetch_related("lines__item", "lines__account", "lines__tax_code")
                .get(company=actor.company, pk=pk)
            )
        except SalesInvoice.DoesNotExist:
            return Response({"detail": "Invoice not found."}, status=404)

        # Company logo as data URI for embedding in PDF
        company = actor.company
        company_logo_uri = ""
        if company.logo:
            try:
                logo_path = Path(company.logo.path)
                if logo_path.exists():
                    logo_data = logo_path.read_bytes()
                    ext = logo_path.suffix.lower().lstrip(".")
                    mime = {
                        "png": "image/png",
                        "jpg": "image/jpeg",
                        "jpeg": "image/jpeg",
                        "gif": "image/gif",
                        "svg": "image/svg+xml",
                    }.get(ext, "image/png")
                    company_logo_uri = f"data:{mime};base64,{base64.b64encode(logo_data).decode()}"
            except Exception:
                pass

        currency = getattr(company, "default_currency", "USD") or "USD"

        context = {
            "invoice": invoice,
            "company_name": company.name,
            "company_logo_uri": company_logo_uri,
            "currency": currency,
        }

        html_string = render_to_string("pdf/sales_invoice.html", context)

        import weasyprint

        pdf_bytes = weasyprint.HTML(string=html_string).write_pdf()

        disposition = "inline" if request.query_params.get("inline") == "1" else "attachment"
        filename = f"{invoice.invoice_number}.pdf"

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        return response


# =============================================================================
# Email Invoice
# =============================================================================


class SalesInvoiceEmailView(APIView):
    """
    POST /api/sales/invoices/<pk>/email/

    Sends the invoice as a PDF attachment to the specified email address.

    Request body:
    - recipient_email: Email address to send to (defaults to customer email)
    - message: Optional custom message to include in the email body
    """

    module_key = "sales"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        import base64
        import logging
        from pathlib import Path

        from django.conf import settings
        from django.core.mail import EmailMultiAlternatives
        from django.template.loader import render_to_string
        from django.utils.html import strip_tags

        logger = logging.getLogger(__name__)
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            invoice = (
                SalesInvoice.objects.select_related("customer", "posting_profile", "posted_journal_entry")
                .prefetch_related("lines__item", "lines__account", "lines__tax_code")
                .get(company=actor.company, pk=pk)
            )
        except SalesInvoice.DoesNotExist:
            return Response({"detail": "Invoice not found."}, status=404)

        # Recipient
        recipient_email = request.data.get("recipient_email", "").strip()
        if not recipient_email:
            recipient_email = invoice.customer.email
        if not recipient_email:
            return Response(
                {"detail": "No recipient email provided and customer has no email on file."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        custom_message = request.data.get("message", "").strip()
        company = actor.company
        currency = getattr(company, "default_currency", "USD") or "USD"

        # Format dates
        def fmt_date(d):
            if not d:
                return None
            return d.strftime("%B %d, %Y")

        def fmt_amount(val):
            return f"{val:,.2f}"

        # Build the invoice URL
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        invoice_url = f"{frontend_url}/accounting/sales-invoices/{invoice.id}"

        # Render email HTML
        email_context = {
            "company_name": company.name,
            "customer_name": invoice.customer.name,
            "invoice_number": invoice.invoice_number,
            "invoice_date": fmt_date(invoice.invoice_date),
            "due_date": fmt_date(invoice.due_date),
            "subtotal": fmt_amount(invoice.subtotal),
            "total_tax": fmt_amount(invoice.total_tax),
            "total_amount": fmt_amount(invoice.total_amount),
            "currency": currency,
            "invoice_url": invoice_url,
            "custom_message": custom_message,
        }
        html_message = render_to_string("emails/sales_invoice.html", email_context)
        plain_message = strip_tags(html_message)

        # Generate PDF
        company_logo_uri = ""
        if company.logo:
            try:
                logo_path = Path(company.logo.path)
                if logo_path.exists():
                    logo_data = logo_path.read_bytes()
                    ext = logo_path.suffix.lower().lstrip(".")
                    mime = {
                        "png": "image/png",
                        "jpg": "image/jpeg",
                        "jpeg": "image/jpeg",
                        "gif": "image/gif",
                        "svg": "image/svg+xml",
                    }.get(ext, "image/png")
                    company_logo_uri = f"data:{mime};base64,{base64.b64encode(logo_data).decode()}"
            except Exception:
                pass

        pdf_context = {
            "invoice": invoice,
            "company_name": company.name,
            "company_logo_uri": company_logo_uri,
            "currency": currency,
        }
        pdf_html = render_to_string("pdf/sales_invoice.html", pdf_context)

        import weasyprint

        pdf_bytes = weasyprint.HTML(string=pdf_html).write_pdf()

        # Send email with PDF attachment
        subject = f"Invoice {invoice.invoice_number} from {company.name}"
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@nxentra.com")

        try:
            email = EmailMultiAlternatives(
                subject=subject,
                body=plain_message,
                from_email=from_email,
                to=[recipient_email],
            )
            email.attach_alternative(html_message, "text/html")
            email.attach(
                f"{invoice.invoice_number}.pdf",
                pdf_bytes,
                "application/pdf",
            )
            email.send(fail_silently=False)

            logger.info(
                "Invoice %s emailed to %s by user %s",
                invoice.invoice_number,
                recipient_email,
                actor.user.email,
            )

            return Response(
                {
                    "detail": f"Invoice emailed to {recipient_email}",
                    "recipient_email": recipient_email,
                }
            )

        except Exception as e:
            logger.error(
                "Failed to email invoice %s to %s: %s",
                invoice.invoice_number,
                recipient_email,
                e,
            )
            return Response(
                {"detail": f"Failed to send email: {e!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


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
        from decimal import Decimal

        from accounting.models import Customer

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
                open_invoices.append(
                    {
                        "id": inv.id,
                        "public_id": str(inv.public_id),
                        "invoice_number": inv.invoice_number,
                        "invoice_date": inv.invoice_date.isoformat(),
                        "due_date": inv.due_date.isoformat() if inv.due_date else None,
                        "total_amount": str(inv.total_amount),
                        "amount_paid": str(inv.amount_paid),
                        "amount_due": str(amount_due),
                        "reference": inv.reference,
                    }
                )

        return Response(
            {
                "customer_id": customer_id,
                "customer_code": customer.code,
                "customer_name": customer.name,
                "open_invoices": open_invoices,
                "total_outstanding": str(
                    sum(
                        inv.total_amount - inv.amount_paid
                        for inv in invoices
                        if (inv.total_amount - inv.amount_paid) > Decimal("0")
                    )
                ),
            }
        )


# =============================================================================
# Credit Note Views
# =============================================================================


class CreditNoteListCreateView(APIView):
    """List credit notes or create a new one."""

    module_key = "sales"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from nxentra_backend.pagination import paginate_queryset

        from .models import SalesCreditNote
        from .serializers import CreditNoteListSerializer

        credit_notes = SalesCreditNote.objects.filter(company=actor.company).select_related("customer", "invoice")

        if "status" in request.query_params:
            credit_notes = credit_notes.filter(status=request.query_params["status"])
        if "invoice_id" in request.query_params:
            credit_notes = credit_notes.filter(invoice_id=request.query_params["invoice_id"])

        return paginate_queryset(
            request,
            credit_notes,
            CreditNoteListSerializer,
            default_ordering="-credit_note_date",
            allowed_sort_fields=["credit_note_number", "credit_note_date", "total_amount", "status"],
        )

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .commands import create_credit_note
        from .serializers import CreditNoteCreateSerializer, CreditNoteSerializer

        serializer = CreditNoteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_credit_note(actor, **serializer.validated_data)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(
            CreditNoteSerializer(result.data["credit_note"]).data,
            status=status.HTTP_201_CREATED,
        )


class CreditNoteDetailView(APIView):
    """Retrieve a credit note."""

    module_key = "sales"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def get(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .models import SalesCreditNote
        from .serializers import CreditNoteSerializer

        try:
            cn = (
                SalesCreditNote.objects.select_related("customer", "invoice", "posting_profile")
                .prefetch_related("lines", "lines__account", "lines__tax_code")
                .get(company=actor.company, pk=pk)
            )
        except SalesCreditNote.DoesNotExist:
            return Response({"detail": "Credit note not found."}, status=404)

        return Response(CreditNoteSerializer(cn).data)


class CreditNotePostView(APIView):
    """Post a credit note."""

    module_key = "sales"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .commands import post_credit_note
        from .serializers import CreditNoteSerializer

        result = post_credit_note(actor, pk)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(CreditNoteSerializer(result.data["credit_note"]).data)


class CreditNoteVoidView(APIView):
    """Void a posted credit note."""

    module_key = "sales"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .commands import void_credit_note
        from .serializers import CreditNoteSerializer

        reason = request.data.get("reason", "")
        result = void_credit_note(actor, pk, reason=reason)
        if not result.success:
            return Response({"detail": result.error}, status=400)

        return Response(CreditNoteSerializer(result.data["credit_note"]).data)
