# purchases/urls.py
"""
URL configuration for purchases API.

Endpoints:
- /bills/ - Purchase Bill CRUD with workflow actions
"""

from django.urls import path

from .views import (
    PurchaseBillListCreateView,
    PurchaseBillDetailView,
    PurchaseBillPostView,
    PurchaseBillVoidView,
)

app_name = "purchases"

urlpatterns = [
    # ==========================================================================
    # Purchase Bills
    # ==========================================================================
    path(
        "bills/",
        PurchaseBillListCreateView.as_view(),
        name="bill-list-create",
    ),
    path(
        "bills/<int:pk>/",
        PurchaseBillDetailView.as_view(),
        name="bill-detail",
    ),
    path(
        "bills/<int:pk>/post/",
        PurchaseBillPostView.as_view(),
        name="bill-post",
    ),
    path(
        "bills/<int:pk>/void/",
        PurchaseBillVoidView.as_view(),
        name="bill-void",
    ),
]
