# purchases/urls.py
"""
URL configuration for purchases API.

Endpoints:
- /bills/ - Purchase Bill CRUD with workflow actions
- /orders/ - Purchase Order lifecycle
- /receipts/ - Goods Receipt management
"""

from django.urls import path

from .views import (
    # Goods Receipt views
    GoodsReceiptDetailView,
    GoodsReceiptListCreateView,
    GoodsReceiptPostView,
    GoodsReceiptVoidView,
    # Purchase Bill views
    PurchaseBillDetailView,
    PurchaseBillListCreateView,
    PurchaseBillPostView,
    PurchaseBillVoidView,
    # Purchase Credit Note views
    PurchaseCreditNoteDetailView,
    PurchaseCreditNoteListCreateView,
    PurchaseCreditNotePostView,
    PurchaseCreditNoteVoidView,
    # Purchase Order views
    PurchaseOrderApproveView,
    PurchaseOrderCancelView,
    PurchaseOrderCloseView,
    PurchaseOrderCreateBillView,
    PurchaseOrderDetailView,
    PurchaseOrderListCreateView,
)

app_name = "purchases"

urlpatterns = [
    # ==========================================================================
    # Purchase Orders
    # ==========================================================================
    path("orders/", PurchaseOrderListCreateView.as_view(), name="order-list-create"),
    path("orders/<int:pk>/", PurchaseOrderDetailView.as_view(), name="order-detail"),
    path("orders/<int:pk>/approve/", PurchaseOrderApproveView.as_view(), name="order-approve"),
    path("orders/<int:pk>/cancel/", PurchaseOrderCancelView.as_view(), name="order-cancel"),
    path("orders/<int:pk>/close/", PurchaseOrderCloseView.as_view(), name="order-close"),
    path("orders/<int:pk>/create-bill/", PurchaseOrderCreateBillView.as_view(), name="order-create-bill"),

    # ==========================================================================
    # Goods Receipts
    # ==========================================================================
    path("receipts/", GoodsReceiptListCreateView.as_view(), name="receipt-list-create"),
    path("receipts/<int:pk>/", GoodsReceiptDetailView.as_view(), name="receipt-detail"),
    path("receipts/<int:pk>/post/", GoodsReceiptPostView.as_view(), name="receipt-post"),
    path("receipts/<int:pk>/void/", GoodsReceiptVoidView.as_view(), name="receipt-void"),

    # ==========================================================================
    # Purchase Bills
    # ==========================================================================
    path("bills/", PurchaseBillListCreateView.as_view(), name="bill-list-create"),
    path("bills/<int:pk>/", PurchaseBillDetailView.as_view(), name="bill-detail"),
    path("bills/<int:pk>/post/", PurchaseBillPostView.as_view(), name="bill-post"),
    path("bills/<int:pk>/void/", PurchaseBillVoidView.as_view(), name="bill-void"),

    # ==========================================================================
    # Purchase Credit Notes (Vendor Returns / Debit Notes)
    # ==========================================================================
    path("credit-notes/", PurchaseCreditNoteListCreateView.as_view(), name="credit-note-list-create"),
    path("credit-notes/<int:pk>/", PurchaseCreditNoteDetailView.as_view(), name="credit-note-detail"),
    path("credit-notes/<int:pk>/post/", PurchaseCreditNotePostView.as_view(), name="credit-note-post"),
    path("credit-notes/<int:pk>/void/", PurchaseCreditNoteVoidView.as_view(), name="credit-note-void"),
]
