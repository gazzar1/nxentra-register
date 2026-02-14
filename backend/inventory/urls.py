# inventory/urls.py
"""
URL routing for inventory API endpoints.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    WarehouseViewSet,
    InventoryBalanceViewSet,
    StockLedgerViewSet,
    InventoryAdjustmentViewSet,
    InventoryOpeningBalanceViewSet,
    StockAvailabilityViewSet,
)


router = DefaultRouter()
router.register(r"warehouses", WarehouseViewSet, basename="warehouse")
router.register(r"balances", InventoryBalanceViewSet, basename="inventory-balance")
router.register(r"ledger", StockLedgerViewSet, basename="stock-ledger")
router.register(r"adjustments", InventoryAdjustmentViewSet, basename="inventory-adjustment")
router.register(r"opening-balance", InventoryOpeningBalanceViewSet, basename="inventory-opening")
router.register(r"availability", StockAvailabilityViewSet, basename="stock-availability")

urlpatterns = [
    path("", include(router.urls)),
]
