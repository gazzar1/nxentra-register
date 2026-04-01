# sales/urls.py
"""
URL configuration for sales API.

Endpoints:
- /items/ - Item CRUD
- /tax-codes/ - Tax Code CRUD
- /posting-profiles/ - Posting Profile CRUD
- /invoices/ - Sales Invoice CRUD with workflow actions
- /customers/<id>/open-invoices/ - Open invoices for receipt allocation
"""

from django.urls import path

from .views import (
    # Credit Note views
    CreditNoteDetailView,
    CreditNoteListCreateView,
    CreditNotePostView,
    CreditNoteVoidView,
    # Open invoices for allocation
    CustomerOpenInvoicesView,
    ItemDetailView,
    ItemImageUploadView,
    # Item views
    ItemListCreateView,
    PostingProfileDetailView,
    # Posting Profile views
    PostingProfileListCreateView,
    SalesInvoiceDetailView,
    SalesInvoiceEmailView,
    # Sales Invoice views
    SalesInvoiceListCreateView,
    SalesInvoicePDFView,
    SalesInvoicePostView,
    SalesInvoiceVoidView,
    TaxCodeDetailView,
    # Tax Code views
    TaxCodeListCreateView,
)

app_name = "sales"

urlpatterns = [
    # ==========================================================================
    # Items
    # ==========================================================================
    path(
        "items/",
        ItemListCreateView.as_view(),
        name="item-list-create",
    ),
    path(
        "items/<int:pk>/",
        ItemDetailView.as_view(),
        name="item-detail",
    ),
    path(
        "items/<int:pk>/image/",
        ItemImageUploadView.as_view(),
        name="item-image",
    ),

    # ==========================================================================
    # Tax Codes
    # ==========================================================================
    path(
        "tax-codes/",
        TaxCodeListCreateView.as_view(),
        name="taxcode-list-create",
    ),
    path(
        "tax-codes/<int:pk>/",
        TaxCodeDetailView.as_view(),
        name="taxcode-detail",
    ),

    # ==========================================================================
    # Posting Profiles
    # ==========================================================================
    path(
        "posting-profiles/",
        PostingProfileListCreateView.as_view(),
        name="postingprofile-list-create",
    ),
    path(
        "posting-profiles/<int:pk>/",
        PostingProfileDetailView.as_view(),
        name="postingprofile-detail",
    ),

    # ==========================================================================
    # Sales Invoices
    # ==========================================================================
    path(
        "invoices/",
        SalesInvoiceListCreateView.as_view(),
        name="invoice-list-create",
    ),
    path(
        "invoices/<int:pk>/",
        SalesInvoiceDetailView.as_view(),
        name="invoice-detail",
    ),
    path(
        "invoices/<int:pk>/post/",
        SalesInvoicePostView.as_view(),
        name="invoice-post",
    ),
    path(
        "invoices/<int:pk>/void/",
        SalesInvoiceVoidView.as_view(),
        name="invoice-void",
    ),
    path(
        "invoices/<int:pk>/pdf/",
        SalesInvoicePDFView.as_view(),
        name="invoice-pdf",
    ),
    path(
        "invoices/<int:pk>/email/",
        SalesInvoiceEmailView.as_view(),
        name="invoice-email",
    ),

    # ==========================================================================
    # Credit Notes
    # ==========================================================================
    path(
        "credit-notes/",
        CreditNoteListCreateView.as_view(),
        name="creditnote-list-create",
    ),
    path(
        "credit-notes/<int:pk>/",
        CreditNoteDetailView.as_view(),
        name="creditnote-detail",
    ),
    path(
        "credit-notes/<int:pk>/post/",
        CreditNotePostView.as_view(),
        name="creditnote-post",
    ),
    path(
        "credit-notes/<int:pk>/void/",
        CreditNoteVoidView.as_view(),
        name="creditnote-void",
    ),

    # ==========================================================================
    # Open Invoices (for receipt allocation)
    # ==========================================================================
    path(
        "customers/<int:customer_id>/open-invoices/",
        CustomerOpenInvoicesView.as_view(),
        name="customer-open-invoices",
    ),
]
