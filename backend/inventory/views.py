# inventory/views.py
"""
API views for inventory module.
"""

from decimal import Decimal

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.authz import resolve_actor
from accounts.module_permissions import ModuleEnabled
from projections.models import InventoryBalance
from sales.models import Item

from .commands import (
    adjust_inventory,
    check_stock_availability,
    create_warehouse,
    record_opening_balance,
    update_warehouse,
)
from .models import StockLedgerEntry, Warehouse
from .serializers import (
    InventoryAdjustmentSerializer,
    InventoryBalanceSerializer,
    InventoryOpeningBalanceSerializer,
    StockLedgerEntrySerializer,
    WarehouseCreateSerializer,
    WarehouseSerializer,
    WarehouseUpdateSerializer,
)


class WarehouseViewSet(viewsets.ModelViewSet):
    """
    ViewSet for warehouse management.

    Endpoints:
    - GET /api/inventory/warehouses/ - List warehouses
    - POST /api/inventory/warehouses/ - Create warehouse
    - GET /api/inventory/warehouses/{id}/ - Get warehouse
    - PATCH /api/inventory/warehouses/{id}/ - Update warehouse

    Query params:
    - code: Filter by code (contains)
    - name: Filter by name (contains)
    - is_active: Filter by active status
    - is_default: Filter by default status
    """

    permission_classes = [IsAuthenticated]
    serializer_class = WarehouseSerializer

    def get_queryset(self):
        if not self.request.user.is_authenticated:
            return Warehouse.objects.none()
        company = getattr(self.request.user, "active_company", None)
        if not company:
            return Warehouse.objects.none()

        queryset = Warehouse.objects.filter(company=company)

        # Manual filtering
        code = self.request.query_params.get("code")
        if code:
            queryset = queryset.filter(code__icontains=code)

        name = self.request.query_params.get("name")
        if name:
            queryset = queryset.filter(name__icontains=name)

        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")

        is_default = self.request.query_params.get("is_default")
        if is_default is not None:
            queryset = queryset.filter(is_default=is_default.lower() == "true")

        return queryset.order_by("code")

    def create(self, request, *args, **kwargs):
        serializer = WarehouseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = create_warehouse(
                actor=resolve_actor(request),
                **serializer.validated_data,
            )
        except Exception as e:
            import traceback

            traceback.print_exc()
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not result.success:
            return Response({"error": result.error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            WarehouseSerializer(result.data["warehouse"]).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = WarehouseUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        result = update_warehouse(
            actor=resolve_actor(request),
            warehouse_id=instance.id,
            **serializer.validated_data,
        )

        if not result.success:
            return Response({"error": result.error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(WarehouseSerializer(result.data["warehouse"]).data)

    def update(self, request, *args, **kwargs):
        # Use partial_update for both PUT and PATCH
        return self.partial_update(request, *args, **kwargs)


class InventoryBalanceViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing inventory balances.

    Endpoints:
    - GET /api/inventory/balances/ - List inventory balances
    - GET /api/inventory/balances/{id}/ - Get inventory balance
    - GET /api/inventory/balances/summary/ - Get inventory summary

    Query params:
    - item_code: Filter by item code (contains)
    - warehouse_code: Filter by warehouse code
    - min_qty: Filter by minimum quantity
    - max_qty: Filter by maximum quantity
    - has_stock: Filter by whether has stock (true/false)
    """

    module_key = "inventory"
    permission_classes = [IsAuthenticated, ModuleEnabled]
    serializer_class = InventoryBalanceSerializer

    def get_queryset(self):
        if not self.request.user.is_authenticated:
            return InventoryBalance.objects.none()
        company = getattr(self.request.user, "active_company", None)
        if not company:
            return InventoryBalance.objects.none()

        queryset = InventoryBalance.objects.filter(company=company).select_related("item", "warehouse")

        # Manual filtering
        item_code = self.request.query_params.get("item_code")
        if item_code:
            queryset = queryset.filter(item__code__icontains=item_code)

        warehouse_code = self.request.query_params.get("warehouse_code")
        if warehouse_code:
            queryset = queryset.filter(warehouse__code=warehouse_code)

        min_qty = self.request.query_params.get("min_qty")
        if min_qty:
            queryset = queryset.filter(qty_on_hand__gte=Decimal(min_qty))

        max_qty = self.request.query_params.get("max_qty")
        if max_qty:
            queryset = queryset.filter(qty_on_hand__lte=Decimal(max_qty))

        has_stock = self.request.query_params.get("has_stock")
        if has_stock is not None:
            if has_stock.lower() == "true":
                queryset = queryset.filter(qty_on_hand__gt=0)
            else:
                queryset = queryset.filter(qty_on_hand__lte=0)

        return queryset.order_by("item__code", "warehouse__code")

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Get inventory summary by warehouse."""
        from projections.inventory_balance import InventoryBalanceProjection

        company = request.user.active_company
        if not company:
            return Response({"error": "No active company"}, status=status.HTTP_400_BAD_REQUEST)

        projection = InventoryBalanceProjection()
        summary = projection.get_inventory_summary(company)

        return Response(summary)


class StockLedgerViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing stock ledger entries.

    Endpoints:
    - GET /api/inventory/ledger/ - List stock ledger entries
    - GET /api/inventory/ledger/{id}/ - Get stock ledger entry

    Query params:
    - item_code: Filter by item code (contains)
    - warehouse_code: Filter by warehouse code
    - source_type: Filter by source type
    - posted_after: Filter by posted after date
    - posted_before: Filter by posted before date
    """

    module_key = "inventory"
    permission_classes = [IsAuthenticated, ModuleEnabled]
    serializer_class = StockLedgerEntrySerializer

    def get_queryset(self):
        if not self.request.user.is_authenticated:
            return StockLedgerEntry.objects.none()
        company = getattr(self.request.user, "active_company", None)
        if not company:
            return StockLedgerEntry.objects.none()

        queryset = StockLedgerEntry.objects.filter(company=company).select_related(
            "item", "warehouse", "posted_by", "journal_entry"
        )

        # Manual filtering
        item_code = self.request.query_params.get("item_code")
        if item_code:
            queryset = queryset.filter(item__code__icontains=item_code)

        warehouse_code = self.request.query_params.get("warehouse_code")
        if warehouse_code:
            queryset = queryset.filter(warehouse__code=warehouse_code)

        source_type = self.request.query_params.get("source_type")
        if source_type:
            queryset = queryset.filter(source_type=source_type)

        posted_after = self.request.query_params.get("posted_after")
        if posted_after:
            queryset = queryset.filter(posted_at__gte=posted_after)

        posted_before = self.request.query_params.get("posted_before")
        if posted_before:
            queryset = queryset.filter(posted_at__lte=posted_before)

        return queryset.order_by("-sequence")


class InventoryAdjustmentViewSet(viewsets.ViewSet):
    """
    ViewSet for inventory adjustments.

    Endpoints:
    - POST /api/inventory/adjustments/ - Create adjustment
    """

    module_key = "inventory"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def create(self, request):
        serializer = InventoryAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        company = request.user.active_company
        if not company:
            return Response({"error": "No active company"}, status=status.HTTP_400_BAD_REQUEST)

        # Resolve item and warehouse IDs
        lines = []
        for line_data in serializer.validated_data["lines"]:
            try:
                item = Item.objects.get(company=company, pk=line_data["item_id"])
            except Item.DoesNotExist:
                return Response(
                    {"error": f"Item {line_data['item_id']} not found"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            warehouse = None
            if line_data.get("warehouse_id"):
                try:
                    warehouse = Warehouse.objects.get(company=company, pk=line_data["warehouse_id"])
                except Warehouse.DoesNotExist:
                    return Response(
                        {"error": f"Warehouse {line_data['warehouse_id']} not found"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            lines.append(
                {
                    "item": item,
                    "warehouse": warehouse,
                    "qty_delta": line_data["qty_delta"],
                    "unit_cost": line_data.get("unit_cost"),
                }
            )

        result = adjust_inventory(
            actor=resolve_actor(request),
            adjustment_date=serializer.validated_data["adjustment_date"],
            reason=serializer.validated_data["reason"],
            lines=lines,
            adjustment_account_id=serializer.validated_data["adjustment_account_id"],
        )

        if not result.success:
            return Response({"error": result.error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "adjustment_public_id": result.data["adjustment_public_id"],
                "journal_entry_public_id": str(result.data["journal_entry"].public_id),
                "entry_count": len(result.data["entries"]),
            },
            status=status.HTTP_201_CREATED,
        )


class InventoryOpeningBalanceViewSet(viewsets.ViewSet):
    """
    ViewSet for inventory opening balances.

    Endpoints:
    - POST /api/inventory/opening-balance/ - Record opening balances
    """

    module_key = "inventory"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def create(self, request):
        serializer = InventoryOpeningBalanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        company = request.user.active_company
        if not company:
            return Response({"error": "No active company"}, status=status.HTTP_400_BAD_REQUEST)

        # Resolve item and warehouse IDs
        lines = []
        for line_data in serializer.validated_data["lines"]:
            try:
                item = Item.objects.get(company=company, pk=line_data["item_id"])
            except Item.DoesNotExist:
                return Response(
                    {"error": f"Item {line_data['item_id']} not found"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            warehouse = None
            if line_data.get("warehouse_id"):
                try:
                    warehouse = Warehouse.objects.get(company=company, pk=line_data["warehouse_id"])
                except Warehouse.DoesNotExist:
                    return Response(
                        {"error": f"Warehouse {line_data['warehouse_id']} not found"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            lines.append(
                {
                    "item": item,
                    "warehouse": warehouse,
                    "qty": line_data["qty"],
                    "unit_cost": line_data["unit_cost"],
                }
            )

        result = record_opening_balance(
            actor=resolve_actor(request),
            as_of_date=serializer.validated_data["as_of_date"],
            lines=lines,
            opening_balance_equity_account_id=serializer.validated_data["opening_balance_equity_account_id"],
        )

        if not result.success:
            return Response({"error": result.error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "opening_public_id": result.data["opening_public_id"],
                "journal_entry_public_id": str(result.data["journal_entry"].public_id),
                "entry_count": len(result.data["entries"]),
            },
            status=status.HTTP_201_CREATED,
        )


class StockAvailabilityViewSet(viewsets.ViewSet):
    """
    ViewSet for checking stock availability.

    Endpoints:
    - GET /api/inventory/availability/{item_id}/ - Check stock for an item

    Query params:
    - warehouse_id: Warehouse ID (optional, uses default)
    - qty: Quantity to check (default 1)
    """

    module_key = "inventory"
    permission_classes = [IsAuthenticated, ModuleEnabled]

    def retrieve(self, request, pk=None):
        company = request.user.active_company
        if not company:
            return Response({"error": "No active company"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            item = Item.objects.get(company=company, pk=pk)
        except Item.DoesNotExist:
            return Response({"error": "Item not found"}, status=status.HTTP_404_NOT_FOUND)

        warehouse_id = request.query_params.get("warehouse_id")
        qty_requested = Decimal(request.query_params.get("qty", "1"))

        # Get warehouse
        warehouse = None
        if warehouse_id:
            try:
                warehouse = Warehouse.objects.get(company=company, pk=warehouse_id)
            except Warehouse.DoesNotExist:
                return Response(
                    {"error": "Warehouse not found"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            # Use default warehouse
            try:
                warehouse = Warehouse.objects.get(company=company, is_default=True)
            except Warehouse.DoesNotExist:
                warehouse = Warehouse.objects.filter(company=company, is_active=True).first()

        if not warehouse:
            return Response(
                {"error": "No warehouse available"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_available, error = check_stock_availability(company, item, warehouse, qty_requested)

        # Get current balance
        try:
            balance = InventoryBalance.objects.get(company=company, item=item, warehouse=warehouse)
            qty_on_hand = balance.qty_on_hand
        except InventoryBalance.DoesNotExist:
            qty_on_hand = Decimal("0")

        return Response(
            {
                "item_public_id": str(item.public_id),
                "item_code": item.code,
                "warehouse_public_id": str(warehouse.public_id),
                "warehouse_code": warehouse.code,
                "qty_on_hand": str(qty_on_hand),
                "qty_requested": str(qty_requested),
                "is_available": is_available,
                "error": error if not is_available else None,
            }
        )
