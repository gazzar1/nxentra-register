# projections/inventory_balance.py
"""
Inventory Balance Projection.

This projection maintains materialized inventory balances.
It consumes:
- inventory.stock_received: Apply stock receipts (purchase bills, opening balance, adjustments up)
- inventory.stock_issued: Apply stock issues (sales invoices, adjustments down)
- inventory.adjusted: Manual inventory adjustments
- inventory.opening_balance: Opening inventory balances

The projection maintains:
- InventoryBalance: Current qty_on_hand, avg_cost, stock_value per item per warehouse

This projection is the single source of truth for "what is the inventory on hand?"

NOTE: In normal operation, InventoryBalance is updated directly by the inventory commands
(record_stock_receipt, record_stock_issue, etc.) for immediate consistency.
This projection's handle() method is primarily used for:
1. Async processing if needed
2. Rebuilding projections from event history
"""

import logging
from decimal import Decimal
from typing import Any

from django.db import transaction

from accounts.models import Company
from events.models import BusinessEvent
from events.types import EventTypes
from inventory.models import Warehouse
from projections.base import BaseProjection
from projections.models import InventoryBalance
from sales.models import Item

logger = logging.getLogger(__name__)


class InventoryBalanceProjection(BaseProjection):
    """
    Maintains materialized inventory balances from stock events.

    Event Flow:
    1. Command records stock receipt/issue
    2. Inventory event is emitted (stock_received, stock_issued, etc.)
    3. This projection can consume the event (for async/rebuild scenarios)
    4. InventoryBalance records are updated

    The projection is idempotent: processing the same event twice
    will not double-count amounts (guaranteed by ProjectionAppliedEvent
    in BaseProjection.process_pending).
    """

    @property
    def name(self) -> str:
        return "inventory_balance"

    @property
    def consumes(self) -> list[str]:
        return [
            EventTypes.INVENTORY_STOCK_RECEIVED,
            EventTypes.INVENTORY_STOCK_ISSUED,
            EventTypes.INVENTORY_ADJUSTED,
            EventTypes.INVENTORY_OPENING_BALANCE,
        ]

    def handle(self, event: BusinessEvent) -> None:
        """
        Process an inventory event.

        This handles both receipt events (stock in) and issue events (stock out).
        Each event contains a list of entries with item/warehouse/qty/cost info.
        """
        if event.event_type == EventTypes.INVENTORY_STOCK_RECEIVED:
            self._handle_stock_received(event)
        elif event.event_type == EventTypes.INVENTORY_STOCK_ISSUED:
            self._handle_stock_issued(event)
        elif event.event_type == EventTypes.INVENTORY_ADJUSTED:
            self._handle_adjustment(event)
        elif event.event_type == EventTypes.INVENTORY_OPENING_BALANCE:
            self._handle_opening_balance(event)
        else:
            logger.warning(f"Unknown event type: {event.event_type}")

    def _handle_stock_received(self, event: BusinessEvent) -> None:
        """Handle inventory.stock_received event."""
        data = event.get_data()
        entries = data.get("entries", [])

        if not entries:
            logger.debug(f"Stock received event {event.id} has no entries")
            return

        for entry_data in entries:
            self._apply_receipt(
                company=event.company,
                entry_data=entry_data,
                event=event,
            )

    def _handle_stock_issued(self, event: BusinessEvent) -> None:
        """Handle inventory.stock_issued event."""
        data = event.get_data()
        entries = data.get("entries", [])

        if not entries:
            logger.debug(f"Stock issued event {event.id} has no entries")
            return

        for entry_data in entries:
            self._apply_issue(
                company=event.company,
                entry_data=entry_data,
                event=event,
            )

    def _handle_adjustment(self, event: BusinessEvent) -> None:
        """Handle inventory.adjusted event."""
        data = event.get_data()
        entries = data.get("entries", [])

        if not entries:
            logger.debug(f"Adjustment event {event.id} has no entries")
            return

        for entry_data in entries:
            qty_delta = Decimal(entry_data.get("qty_delta", "0"))
            if qty_delta > 0:
                self._apply_receipt(
                    company=event.company,
                    entry_data=entry_data,
                    event=event,
                )
            elif qty_delta < 0:
                self._apply_issue(
                    company=event.company,
                    entry_data=entry_data,
                    event=event,
                )

    def _handle_opening_balance(self, event: BusinessEvent) -> None:
        """Handle inventory.opening_balance event."""
        # Opening balances are receipts
        self._handle_stock_received(event)

    def _apply_receipt(
        self,
        company: Company,
        entry_data: dict[str, Any],
        event: BusinessEvent,
    ) -> None:
        """
        Apply a stock receipt entry to InventoryBalance.

        Uses select_for_update() to lock the balance row during updates.
        """
        item_public_id = entry_data.get("item_public_id")
        warehouse_public_id = entry_data.get("warehouse_public_id")
        qty_delta = Decimal(entry_data.get("qty_delta", "0"))
        unit_cost = Decimal(entry_data.get("unit_cost", "0"))

        if not item_public_id or not warehouse_public_id:
            logger.warning(f"Entry missing item or warehouse in event {event.id}")
            return

        if qty_delta <= 0:
            logger.warning(f"Receipt qty_delta must be positive, got {qty_delta}")
            return

        # Get item and warehouse
        try:
            item = Item.objects.get(public_id=item_public_id, company=company)
        except Item.DoesNotExist:
            logger.error(f"Item {item_public_id} not found in event {event.id}")
            return

        try:
            warehouse = Warehouse.objects.get(public_id=warehouse_public_id, company=company)
        except Warehouse.DoesNotExist:
            logger.error(f"Warehouse {warehouse_public_id} not found in event {event.id}")
            return

        with transaction.atomic():
            # Get or create balance with lock
            try:
                balance = InventoryBalance.objects.select_for_update().get(
                    company=company,
                    item=item,
                    warehouse=warehouse,
                )
                created = False
            except InventoryBalance.DoesNotExist:
                balance = InventoryBalance.objects.create(
                    company=company,
                    item=item,
                    warehouse=warehouse,
                    qty_on_hand=Decimal("0"),
                    avg_cost=Decimal("0"),
                    stock_value=Decimal("0"),
                )
                created = True

            # Note: Event-level idempotency is handled by ProjectionAppliedEvent
            # in BaseProjection.process_pending(). No per-item guard here
            # because a single event can have multiple entries for the same item/warehouse.

            # Apply weighted average calculation
            old_value = balance.qty_on_hand * balance.avg_cost
            new_value = qty_delta * unit_cost
            new_qty = balance.qty_on_hand + qty_delta

            if new_qty > 0:
                balance.avg_cost = (old_value + new_value) / new_qty
            else:
                balance.avg_cost = unit_cost

            balance.qty_on_hand = new_qty
            balance.stock_value = new_qty * balance.avg_cost
            balance.entry_count += 1
            balance.last_event = event
            balance.save()

            logger.debug(
                f"Applied receipt to {item.code}@{warehouse.code}: "
                f"qty={qty_delta}, cost={unit_cost}, new_qty={balance.qty_on_hand}"
            )

    def _apply_issue(
        self,
        company: Company,
        entry_data: dict[str, Any],
        event: BusinessEvent,
    ) -> None:
        """
        Apply a stock issue entry to InventoryBalance.

        Uses select_for_update() to lock the balance row during updates.
        Note: qty_delta in the event is already negative for issues.
        """
        item_public_id = entry_data.get("item_public_id")
        warehouse_public_id = entry_data.get("warehouse_public_id")
        qty_delta = Decimal(entry_data.get("qty_delta", "0"))

        if not item_public_id or not warehouse_public_id:
            logger.warning(f"Entry missing item or warehouse in event {event.id}")
            return

        # For issues, qty_delta is already negative in the event
        qty_to_issue = abs(qty_delta)

        # Get item and warehouse
        try:
            item = Item.objects.get(public_id=item_public_id, company=company)
        except Item.DoesNotExist:
            logger.error(f"Item {item_public_id} not found in event {event.id}")
            return

        try:
            warehouse = Warehouse.objects.get(public_id=warehouse_public_id, company=company)
        except Warehouse.DoesNotExist:
            logger.error(f"Warehouse {warehouse_public_id} not found in event {event.id}")
            return

        with transaction.atomic():
            # Get balance with lock
            try:
                balance = InventoryBalance.objects.select_for_update().get(
                    company=company,
                    item=item,
                    warehouse=warehouse,
                )
            except InventoryBalance.DoesNotExist:
                logger.error(f"No inventory balance for {item.code}@{warehouse.code} in event {event.id}")
                return

            # Note: Event-level idempotency is handled by ProjectionAppliedEvent
            # in BaseProjection.process_pending(). No per-item guard here
            # because a single event can have multiple entries for the same item/warehouse.

            # Apply issue (avg_cost doesn't change on issue)
            balance.qty_on_hand -= qty_to_issue
            balance.stock_value = balance.qty_on_hand * balance.avg_cost
            balance.entry_count += 1
            balance.last_event = event
            balance.save()

            logger.debug(
                f"Applied issue to {item.code}@{warehouse.code}: qty={qty_to_issue}, new_qty={balance.qty_on_hand}"
            )

    def _clear_projected_data(self, company: Company) -> None:
        """Clear all InventoryBalance records for rebuild."""
        cleared = InventoryBalance.objects.filter(company=company).update(
            qty_on_hand=Decimal("0"),
            avg_cost=Decimal("0"),
            stock_value=Decimal("0"),
            entry_count=0,
            last_entry_date=None,
            last_event=None,
        )
        logger.info(f"Reset {cleared} InventoryBalance records for {company.name}")

    def get_balance(
        self,
        company: Company,
        item: Item,
        warehouse: Warehouse,
    ) -> dict[str, Decimal]:
        """
        Get the current inventory balance for an item in a warehouse.

        Returns:
            {"qty_on_hand": Decimal, "avg_cost": Decimal, "stock_value": Decimal}
        """
        try:
            balance = InventoryBalance.objects.get(
                company=company,
                item=item,
                warehouse=warehouse,
            )
            return {
                "qty_on_hand": balance.qty_on_hand,
                "avg_cost": balance.avg_cost,
                "stock_value": balance.stock_value,
            }
        except InventoryBalance.DoesNotExist:
            return {
                "qty_on_hand": Decimal("0"),
                "avg_cost": Decimal("0"),
                "stock_value": Decimal("0"),
            }

    def get_inventory_summary(self, company: Company) -> dict[str, Any]:
        """
        Generate inventory summary for a company.

        Returns:
            {
                "total_items": 50,
                "total_value": "125000.00",
                "warehouses": [
                    {"code": "MAIN", "name": "Main Warehouse", "item_count": 30, "total_value": "100000.00"},
                    ...
                ],
                "items": [
                    {"code": "ITEM-001", "name": "Widget", "warehouse": "MAIN", "qty": "100", "avg_cost": "10.00", "value": "1000.00"},
                    ...
                ],
            }
        """

        balances = InventoryBalance.objects.filter(
            company=company,
            qty_on_hand__gt=0,
        ).select_related("item", "warehouse")

        # Aggregate by warehouse
        warehouse_summary = {}
        items = []
        total_value = Decimal("0")

        for balance in balances:
            wh_code = balance.warehouse.code
            if wh_code not in warehouse_summary:
                warehouse_summary[wh_code] = {
                    "code": wh_code,
                    "name": balance.warehouse.name,
                    "item_count": 0,
                    "total_value": Decimal("0"),
                }

            warehouse_summary[wh_code]["item_count"] += 1
            warehouse_summary[wh_code]["total_value"] += balance.stock_value
            total_value += balance.stock_value

            items.append(
                {
                    "code": balance.item.code,
                    "name": balance.item.name,
                    "warehouse": wh_code,
                    "qty": str(balance.qty_on_hand),
                    "avg_cost": str(balance.avg_cost),
                    "value": str(balance.stock_value),
                }
            )

        return {
            "total_items": len(items),
            "total_value": str(total_value),
            "warehouses": [{**wh, "total_value": str(wh["total_value"])} for wh in warehouse_summary.values()],
            "items": items,
        }

    def verify_all_balances(self, company: Company) -> dict[str, Any]:
        """
        Verify all projected inventory balances by replaying events.

        Events are the source of truth. This method replays all
        inventory events to compute expected balances per item/warehouse,
        then compares against the current projection state.

        Returns:
            {
                "total_balances": 10,
                "verified": 10,
                "mismatches": [],
                "events_processed": 50,
            }
        """
        # Build expected balances by replaying events
        expected: dict[str, dict[str, Decimal]] = {}  # {item_id:warehouse_id: {qty, value}}
        events_processed = 0

        events = BusinessEvent.objects.filter(
            company=company,
            event_type__in=self.consumes,
        ).order_by("company_sequence")

        for event in events:
            data = event.get_data()
            entries = data.get("entries", [])

            for entry_data in entries:
                item_id = entry_data.get("item_public_id")
                warehouse_id = entry_data.get("warehouse_public_id")
                qty_delta = Decimal(entry_data.get("qty_delta", "0"))
                unit_cost = Decimal(entry_data.get("unit_cost", "0"))

                if not item_id or not warehouse_id:
                    continue

                key = f"{item_id}:{warehouse_id}"

                if key not in expected:
                    expected[key] = {
                        "qty": Decimal("0"),
                        "value": Decimal("0"),
                        "avg_cost": Decimal("0"),
                    }

                # Apply receipt or issue
                old_qty = expected[key]["qty"]
                old_value = old_qty * expected[key]["avg_cost"]

                if qty_delta > 0:
                    # Receipt: recalculate weighted average
                    new_value = qty_delta * unit_cost
                    new_qty = old_qty + qty_delta
                    if new_qty > 0:
                        expected[key]["avg_cost"] = (old_value + new_value) / new_qty
                    else:
                        expected[key]["avg_cost"] = unit_cost
                    expected[key]["qty"] = new_qty
                else:
                    # Issue: just reduce qty
                    expected[key]["qty"] = old_qty + qty_delta  # qty_delta is negative

                expected[key]["value"] = expected[key]["qty"] * expected[key]["avg_cost"]
                events_processed += 1

        # Compare against projected balances
        balances = InventoryBalance.objects.filter(company=company).select_related("item", "warehouse")

        mismatches = []
        verified = 0

        for balance in balances:
            key = f"{balance.item.public_id}:{balance.warehouse.public_id}"
            exp = expected.get(key, {"qty": Decimal("0"), "avg_cost": Decimal("0")})

            # Compare with tolerance for floating point
            qty_match = abs(balance.qty_on_hand - exp["qty"]) < Decimal("0.0001")
            cost_match = abs(balance.avg_cost - exp["avg_cost"]) < Decimal("0.000001")

            if not qty_match or not cost_match:
                mismatches.append(
                    {
                        "item_code": balance.item.code,
                        "warehouse_code": balance.warehouse.code,
                        "projected_qty": str(balance.qty_on_hand),
                        "projected_avg_cost": str(balance.avg_cost),
                        "expected_qty": str(exp["qty"]),
                        "expected_avg_cost": str(exp["avg_cost"]),
                    }
                )
            else:
                verified += 1

        return {
            "total_balances": balances.count(),
            "verified": verified,
            "mismatches": mismatches,
            "events_processed": events_processed,
        }


# Registration is handled by ProjectionsConfig.ready() via AppConfig.projections.
# Do not add a module-level projection_registry.register() call here.
