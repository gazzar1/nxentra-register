# inventory/commands.py
"""
Command layer for inventory operations.

Commands are the single point where business operations happen.
Views call commands; commands enforce rules and emit events.

Key commands:
- record_stock_receipt: Record stock received from purchase bill
- record_stock_issue: Record stock issued for sales invoice
- check_stock_availability: Check if stock is available
- adjust_inventory: Manual inventory adjustment
- record_opening_balance: Record opening inventory balances
"""

import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounting.commands import (
    CommandResult,
    create_journal_entry,
    post_journal_entry,
    save_journal_entry_complete,
)
from accounting.models import Account, JournalEntry
from accounts.authz import ActorContext, require
from events.emitter import emit_event
from events.types import (
    EventTypes,
    InventoryAdjustedData,
    InventoryOpeningBalanceData,
    StockIssuedData,
    StockLedgerEntryData,
    StockReceivedData,
    WarehouseCreatedData,
    WarehouseUpdatedData,
)
from projections.models import InventoryBalance
from projections.write_barrier import command_writes_allowed

from .models import StockLedgerEntry, StockLedgerSequenceCounter, Warehouse


def _get_next_sequence(company) -> int:
    """Get the next monotonic sequence number for stock ledger entries."""
    counter, created = StockLedgerSequenceCounter.objects.select_for_update().get_or_create(
        company=company,
        defaults={"last_sequence": 0}
    )
    counter.last_sequence += 1
    counter.save()
    return counter.last_sequence


class NoWarehouseError(Exception):
    """Raised when no warehouse is available for a company."""
    pass


def _get_default_warehouse(company) -> Warehouse:
    """Get the default warehouse for a company, or create one if none exists."""
    try:
        return Warehouse.objects.get(company=company, is_default=True)
    except Warehouse.DoesNotExist:
        # No default warehouse - look for any active warehouse
        warehouse = Warehouse.objects.filter(company=company, is_active=True).first()
        if warehouse:
            return warehouse
        # No warehouses at all - this shouldn't happen in normal use
        raise NoWarehouseError("No warehouse available for company. Create a warehouse first.")


@transaction.atomic
def create_warehouse(
    actor: ActorContext,
    code: str,
    name: str,
    name_ar: str = "",
    address: str = "",
    is_default: bool = False,
) -> CommandResult:
    """
    Create a new warehouse.
    """
    require(actor, "inventory.warehouse.create")

    # Validate unique code per company
    if Warehouse.objects.filter(company=actor.company, code=code).exists():
        return CommandResult.fail(f"Warehouse code '{code}' already exists.")

    with command_writes_allowed():
        # If this is the first warehouse or is_default=True, handle default flag
        if is_default:
            # Unset any existing default
            Warehouse.objects.filter(company=actor.company, is_default=True).update(
                is_default=False
            )

        # If no warehouses exist, make this one default
        is_first = not Warehouse.objects.filter(company=actor.company).exists()
        if is_first:
            is_default = True

        warehouse = Warehouse.objects.create(
            company=actor.company,
            code=code,
            name=name,
            name_ar=name_ar,
            address=address,
            is_default=is_default,
            is_active=True,
        )

    event = emit_event(
        actor=actor,
        event_type=EventTypes.INVENTORY_WAREHOUSE_CREATED,
        aggregate_type="Warehouse",
        aggregate_id=str(warehouse.public_id),
        idempotency_key=f"warehouse.created:{warehouse.public_id}",
        data=WarehouseCreatedData(
            warehouse_public_id=str(warehouse.public_id),
            company_public_id=str(actor.company.public_id),
            code=warehouse.code,
            name=warehouse.name,
            name_ar=warehouse.name_ar,
            is_default=warehouse.is_default,
            is_active=warehouse.is_active,
        ).to_dict(),
    )

    return CommandResult.ok(data={"warehouse": warehouse}, event=event)


@transaction.atomic
def update_warehouse(
    actor: ActorContext,
    warehouse_id: int,
    **updates,
) -> CommandResult:
    """
    Update a warehouse.

    Allowed updates: name, name_ar, address, is_active, is_default
    """
    require(actor, "inventory.warehouse.update")

    try:
        warehouse = Warehouse.objects.select_for_update().get(
            company=actor.company, pk=warehouse_id
        )
    except Warehouse.DoesNotExist:
        return CommandResult.fail("Warehouse not found.")

    allowed_fields = {"name", "name_ar", "address", "is_active", "is_default"}
    changes = {}

    with command_writes_allowed():
        for field, new_value in updates.items():
            if field not in allowed_fields:
                continue
            old_value = getattr(warehouse, field)
            if old_value != new_value:
                changes[field] = {"old": old_value, "new": new_value}
                setattr(warehouse, field, new_value)

                # Handle is_default: unset other defaults
                if field == "is_default" and new_value:
                    Warehouse.objects.filter(
                        company=actor.company, is_default=True
                    ).exclude(pk=warehouse_id).update(is_default=False)

        if changes:
            warehouse.save()

    if not changes:
        return CommandResult.ok(data={"warehouse": warehouse})

    event = emit_event(
        actor=actor,
        event_type=EventTypes.INVENTORY_WAREHOUSE_UPDATED,
        aggregate_type="Warehouse",
        aggregate_id=str(warehouse.public_id),
        idempotency_key=f"warehouse.updated:{warehouse.public_id}:{timezone.now().isoformat()}",
        data=WarehouseUpdatedData(
            warehouse_public_id=str(warehouse.public_id),
            company_public_id=str(actor.company.public_id),
            changes=changes,
        ).to_dict(),
    )

    return CommandResult.ok(data={"warehouse": warehouse}, event=event)


def check_stock_availability(
    company,
    item,
    warehouse,
    qty_required: Decimal,
) -> tuple[bool, str]:
    """
    Check if stock is available for an item in a warehouse.

    Returns:
        (is_available, error_message)
    """
    try:
        balance = InventoryBalance.objects.get(
            company=company,
            item=item,
            warehouse=warehouse,
        )
        if balance.qty_on_hand >= qty_required:
            return True, ""
        else:
            return False, (
                f"Insufficient stock for {item.code} in {warehouse.code}. "
                f"Required: {qty_required}, Available: {balance.qty_on_hand}"
            )
    except InventoryBalance.DoesNotExist:
        return False, (
            f"No inventory record for {item.code} in {warehouse.code}. "
            f"Required: {qty_required}, Available: 0"
        )


def get_current_avg_cost(company, item, warehouse) -> Decimal:
    """
    Get the current average cost for an item in a warehouse.

    Returns 0 if no inventory balance exists.
    """
    try:
        balance = InventoryBalance.objects.get(
            company=company,
            item=item,
            warehouse=warehouse,
        )
        return balance.avg_cost
    except InventoryBalance.DoesNotExist:
        return Decimal("0")


def _fifo_consume(company, item, warehouse, qty_to_issue: Decimal) -> tuple[Decimal, Decimal]:
    """
    Consume FIFO layers oldest-first for a stock issue.

    Returns:
        (weighted_issue_cost, total_value) where:
        - weighted_issue_cost = total_value / qty_to_issue
        - total_value = sum of (qty_consumed * layer.unit_cost) across layers

    Raises CommandResult.fail equivalent via ValueError if insufficient layers.
    """
    from .models import FifoLayer

    layers = FifoLayer.objects.filter(
        company=company,
        item=item,
        warehouse=warehouse,
        qty_remaining__gt=0,
    ).order_by("sequence").select_for_update()

    remaining = qty_to_issue
    total_value = Decimal("0")

    for layer in layers:
        if remaining <= 0:
            break

        consume = min(remaining, layer.qty_remaining)
        total_value += consume * layer.unit_cost
        layer.qty_remaining -= consume
        layer.save(update_fields=["qty_remaining"])
        remaining -= consume

    if remaining > 0:
        # Not enough FIFO layers — shouldn't happen if stock availability was checked
        # Use zero cost for the remainder (defensive)
        pass

    weighted_cost = (total_value / qty_to_issue).quantize(Decimal("0.000001")) if qty_to_issue > 0 else Decimal("0")
    return weighted_cost, total_value


@transaction.atomic
def record_stock_receipt(
    actor: ActorContext,
    source_type: str,
    source_id: str,
    lines: list,
    journal_entry=None,
) -> CommandResult:
    """
    Record stock receipt from purchase bill or opening balance.

    Called by post_purchase_bill.

    For each line: {
        item: Item instance,
        warehouse: Warehouse instance (optional, uses default),
        qty: Decimal (positive),
        unit_cost: Decimal,
        source_line_id: str (optional)
    }

    Creates StockLedgerEntry with positive qty_delta.
    Updates InventoryBalance projection.
    """
    require(actor, "inventory.stock.receive")

    if not lines:
        return CommandResult.fail("No lines provided for stock receipt.")

    posted_at = timezone.now()
    created_entries = []
    event_entries = []

    with command_writes_allowed():
        for line in lines:
            item = line["item"]
            try:
                warehouse = line.get("warehouse") or _get_default_warehouse(actor.company)
            except NoWarehouseError as e:
                return CommandResult.fail(str(e))
            qty = Decimal(str(line["qty"]))
            unit_cost = Decimal(str(line["unit_cost"]))
            source_line_id = line.get("source_line_id")

            if qty <= 0:
                return CommandResult.fail(f"Quantity must be positive for stock receipt. Got: {qty}")

            # Calculate value
            value_delta = qty * unit_cost

            # Get or create inventory balance
            balance, _ = InventoryBalance.objects.select_for_update().get_or_create(
                company=actor.company,
                item=item,
                warehouse=warehouse,
                defaults={
                    "qty_on_hand": Decimal("0"),
                    "avg_cost": Decimal("0"),
                    "stock_value": Decimal("0"),
                }
            )

            # Calculate new weighted average
            old_value = balance.qty_on_hand * balance.avg_cost
            new_value = qty * unit_cost
            new_qty = balance.qty_on_hand + qty
            new_avg_cost = (old_value + new_value) / new_qty if new_qty > 0 else unit_cost

            # Get next sequence
            sequence = _get_next_sequence(actor.company)

            # Create stock ledger entry
            entry = StockLedgerEntry.objects.create(
                company=actor.company,
                sequence=sequence,
                source_type=source_type,
                source_id=uuid.UUID(source_id) if isinstance(source_id, str) else source_id,
                source_line_id=uuid.UUID(source_line_id) if source_line_id else None,
                warehouse=warehouse,
                item=item,
                qty_delta=qty,
                unit_cost=unit_cost,
                value_delta=value_delta,
                costing_method_snapshot=item.costing_method,
                qty_balance_after=new_qty,
                value_balance_after=new_qty * new_avg_cost,
                avg_cost_after=new_avg_cost,
                posted_at=posted_at,
                posted_by=actor.user,
                journal_entry=journal_entry,
            )
            created_entries.append(entry)

            # Update balance
            balance.qty_on_hand = new_qty
            balance.avg_cost = new_avg_cost
            balance.stock_value = new_qty * new_avg_cost
            balance.entry_count += 1
            balance.last_entry_date = posted_at.date()
            balance.save()

            # Also update the item's average_cost and last_cost
            item.average_cost = new_avg_cost
            item.last_cost = unit_cost
            item.save(update_fields=["average_cost", "last_cost"])

            # Create FIFO layer if item uses FIFO costing
            if item.costing_method == "FIFO":
                from .models import FifoLayer
                FifoLayer.objects.create(
                    company=actor.company,
                    item=item,
                    warehouse=warehouse,
                    receipt_entry=entry,
                    qty_original=qty,
                    qty_remaining=qty,
                    unit_cost=unit_cost,
                    sequence=sequence,
                )

            event_entries.append(StockLedgerEntryData(
                item_public_id=str(item.public_id),
                warehouse_public_id=str(warehouse.public_id),
                qty_delta=str(qty),
                unit_cost=str(unit_cost),
                value_delta=str(value_delta),
                costing_method_snapshot=item.costing_method,
                source_line_id=source_line_id,
            ).to_dict())

    event = emit_event(
        actor=actor,
        event_type=EventTypes.INVENTORY_STOCK_RECEIVED,
        aggregate_type="StockLedger",
        aggregate_id=str(actor.company.public_id),
        idempotency_key=f"stock.received:{source_type}:{source_id}",
        data=StockReceivedData(
            source_type=source_type,
            source_id=source_id,
            company_public_id=str(actor.company.public_id),
            entries=[e for e in event_entries],
            journal_entry_public_id=str(journal_entry.public_id) if journal_entry else None,
            posted_at=posted_at.isoformat(),
            posted_by_id=actor.user.id if actor.user else None,
            posted_by_email=actor.user.email if actor.user else "",
        ).to_dict(),
    )

    return CommandResult.ok(
        data={"entries": created_entries},
        event=event
    )


@transaction.atomic
def record_stock_issue(
    actor: ActorContext,
    source_type: str,
    source_id: str,
    lines: list,
    journal_entry=None,
) -> CommandResult:
    """
    Record stock issue for sales invoice.

    Called by post_sales_invoice.

    For each line: {
        item: Item instance,
        warehouse: Warehouse instance (optional, uses default),
        qty: Decimal (positive - will be made negative internally),
        source_line_id: str (optional)
    }

    Fetches current avg_cost from InventoryBalance.
    Creates StockLedgerEntry with negative qty_delta.
    Returns total COGS value.
    """
    require(actor, "inventory.stock.issue")

    if not lines:
        return CommandResult.fail("No lines provided for stock issue.")

    posted_at = timezone.now()
    created_entries = []
    event_entries = []
    total_cogs = Decimal("0")

    with command_writes_allowed():
        for line in lines:
            item = line["item"]
            try:
                warehouse = line.get("warehouse") or _get_default_warehouse(actor.company)
            except NoWarehouseError as e:
                return CommandResult.fail(str(e))
            qty = Decimal(str(line["qty"]))
            source_line_id = line.get("source_line_id")

            if qty <= 0:
                return CommandResult.fail(f"Quantity must be positive for stock issue. Got: {qty}")

            # Check availability (unless negative inventory allowed)
            if not actor.company.allow_negative_inventory:
                is_available, error = check_stock_availability(
                    actor.company, item, warehouse, qty
                )
                if not is_available:
                    return CommandResult.fail(error)

            # Get inventory balance for avg_cost
            try:
                balance = InventoryBalance.objects.select_for_update().get(
                    company=actor.company,
                    item=item,
                    warehouse=warehouse,
                )
            except InventoryBalance.DoesNotExist:
                return CommandResult.fail(
                    f"No inventory record for {item.code} in {warehouse.code}."
                )

            # Determine issue cost based on costing method
            if item.costing_method == "FIFO":
                # FIFO: consume oldest layers first
                issue_cost, value_delta = _fifo_consume(
                    actor.company, item, warehouse, qty
                )
            else:
                # Weighted average (default)
                issue_cost = balance.avg_cost
                value_delta = qty * issue_cost  # Positive value for COGS

            total_cogs += value_delta

            # New balance after issue
            new_qty = balance.qty_on_hand - qty
            new_value = balance.stock_value - value_delta
            # avg_cost doesn't change on issue (for weighted avg)
            # For FIFO, avg_cost is recalculated from remaining layers
            new_avg_cost = balance.avg_cost
            if item.costing_method == "FIFO" and new_qty > 0:
                from .models import FifoLayer
                remaining_layers = FifoLayer.objects.filter(
                    company=actor.company, item=item, warehouse=warehouse,
                    qty_remaining__gt=0,
                )
                total_remaining_value = sum(l.qty_remaining * l.unit_cost for l in remaining_layers)
                new_avg_cost = (total_remaining_value / new_qty).quantize(Decimal("0.000001")) if new_qty > 0 else Decimal("0")
                new_value = total_remaining_value

            # Get next sequence
            sequence = _get_next_sequence(actor.company)

            # Create stock ledger entry with NEGATIVE qty_delta
            entry = StockLedgerEntry.objects.create(
                company=actor.company,
                sequence=sequence,
                source_type=source_type,
                source_id=uuid.UUID(source_id) if isinstance(source_id, str) else source_id,
                source_line_id=uuid.UUID(source_line_id) if source_line_id else None,
                warehouse=warehouse,
                item=item,
                qty_delta=-qty,  # Negative for issue
                unit_cost=issue_cost,
                value_delta=-value_delta,  # Negative for issue
                costing_method_snapshot=item.costing_method,
                qty_balance_after=new_qty,
                value_balance_after=new_value,
                avg_cost_after=new_avg_cost,
                posted_at=posted_at,
                posted_by=actor.user,
                journal_entry=journal_entry,
            )
            created_entries.append(entry)

            # Update balance
            balance.qty_on_hand = new_qty
            balance.avg_cost = new_avg_cost
            balance.stock_value = new_value
            balance.entry_count += 1
            balance.last_entry_date = posted_at.date()
            balance.save()

            event_entries.append(StockLedgerEntryData(
                item_public_id=str(item.public_id),
                warehouse_public_id=str(warehouse.public_id),
                qty_delta=str(-qty),
                unit_cost=str(issue_cost),
                value_delta=str(-value_delta),
                costing_method_snapshot=item.costing_method,
                source_line_id=source_line_id,
            ).to_dict())

    event = emit_event(
        actor=actor,
        event_type=EventTypes.INVENTORY_STOCK_ISSUED,
        aggregate_type="StockLedger",
        aggregate_id=str(actor.company.public_id),
        idempotency_key=f"stock.issued:{source_type}:{source_id}",
        data=StockIssuedData(
            source_type=source_type,
            source_id=source_id,
            company_public_id=str(actor.company.public_id),
            entries=[e for e in event_entries],
            total_cogs=str(total_cogs),
            journal_entry_public_id=str(journal_entry.public_id) if journal_entry else None,
            posted_at=posted_at.isoformat(),
            posted_by_id=actor.user.id if actor.user else None,
            posted_by_email=actor.user.email if actor.user else "",
        ).to_dict(),
    )

    return CommandResult.ok(
        data={
            "entries": created_entries,
            "total_cogs": total_cogs,
        },
        event=event
    )


@transaction.atomic
def adjust_inventory(
    actor: ActorContext,
    adjustment_date,
    reason: str,
    lines: list,
    adjustment_account_id: int,
) -> CommandResult:
    """
    Manual inventory adjustment with journal entry.

    For each line: {
        item: Item instance,
        warehouse: Warehouse instance (optional, uses default),
        qty_delta: Decimal (positive = increase, negative = decrease),
        unit_cost: Decimal (for increases, or None to use current avg_cost),
        source_line_id: str (optional)
    }

    Creates journal entry:
    - If qty_delta > 0: Dr Inventory, Cr Adjustment Account
    - If qty_delta < 0: Dr Adjustment Account, Cr Inventory
    """
    require(actor, "inventory.adjustment.create")

    if not lines:
        return CommandResult.fail("No lines provided for adjustment.")

    # Validate adjustment account
    try:
        adjustment_account = Account.objects.get(
            company=actor.company, pk=adjustment_account_id
        )
    except Account.DoesNotExist:
        return CommandResult.fail("Adjustment account not found.")

    if not adjustment_account.is_postable:
        return CommandResult.fail("Adjustment account is not postable.")

    adjustment_public_id = str(uuid.uuid4())
    posted_at = timezone.now()

    # Build journal entry lines
    je_lines = []
    inventory_by_account = {}  # {inventory_account_id: total_value}

    processed_lines = []

    with command_writes_allowed():
        for line in lines:
            item = line["item"]
            try:
                warehouse = line.get("warehouse") or _get_default_warehouse(actor.company)
            except NoWarehouseError as e:
                return CommandResult.fail(str(e))
            qty_delta = Decimal(str(line["qty_delta"]))

            if qty_delta == 0:
                continue

            if not item.is_inventory_item:
                return CommandResult.fail(f"Item {item.code} is not an inventory item.")

            if not item.inventory_account:
                return CommandResult.fail(f"Item {item.code} has no inventory account configured.")

            # Get or create inventory balance
            balance, _ = InventoryBalance.objects.select_for_update().get_or_create(
                company=actor.company,
                item=item,
                warehouse=warehouse,
                defaults={
                    "qty_on_hand": Decimal("0"),
                    "avg_cost": Decimal("0"),
                    "stock_value": Decimal("0"),
                }
            )

            # Determine unit cost
            if qty_delta > 0:
                # Increase: use provided cost or current avg_cost
                unit_cost = Decimal(str(line.get("unit_cost") or balance.avg_cost or "0"))
            else:
                # Decrease: use current avg_cost
                unit_cost = balance.avg_cost

            value_delta = qty_delta * unit_cost

            # Check availability for decreases (unless negative inventory allowed)
            if qty_delta < 0 and not actor.company.allow_negative_inventory:
                required = abs(qty_delta)
                if balance.qty_on_hand < required:
                    return CommandResult.fail(
                        f"Insufficient stock for {item.code} in {warehouse.code}. "
                        f"Required: {required}, Available: {balance.qty_on_hand}"
                    )

            processed_lines.append({
                "item": item,
                "warehouse": warehouse,
                "qty_delta": qty_delta,
                "unit_cost": unit_cost,
                "value_delta": value_delta,
                "balance": balance,
                "source_line_id": line.get("source_line_id"),
            })

            # Accumulate by inventory account
            inv_account_id = item.inventory_account_id
            inventory_by_account[inv_account_id] = (
                inventory_by_account.get(inv_account_id, Decimal("0")) + value_delta
            )

    # Build journal entry lines
    for inv_account_id, total_value in inventory_by_account.items():
        if total_value > 0:
            # Increase: Dr Inventory, Cr Adjustment
            je_lines.append({
                "account_id": inv_account_id,
                "description": f"Inventory Adjustment: {reason}",
                "debit": total_value,
                "credit": Decimal("0"),
            })
            je_lines.append({
                "account_id": adjustment_account_id,
                "description": f"Inventory Adjustment: {reason}",
                "debit": Decimal("0"),
                "credit": total_value,
            })
        elif total_value < 0:
            # Decrease: Dr Adjustment, Cr Inventory
            abs_value = abs(total_value)
            je_lines.append({
                "account_id": adjustment_account_id,
                "description": f"Inventory Adjustment: {reason}",
                "debit": abs_value,
                "credit": Decimal("0"),
            })
            je_lines.append({
                "account_id": inv_account_id,
                "description": f"Inventory Adjustment: {reason}",
                "debit": Decimal("0"),
                "credit": abs_value,
            })

    # Create and post journal entry
    je_result = create_journal_entry(
        actor=actor,
        date=adjustment_date,
        memo=f"Inventory Adjustment: {reason}",
        lines=je_lines,
        kind=JournalEntry.Kind.NORMAL,
    )

    if not je_result.success:
        return CommandResult.fail(f"Failed to create journal entry: {je_result.error}")

    journal_entry = je_result.data

    save_result = save_journal_entry_complete(actor, journal_entry.id)
    if not save_result.success:
        return CommandResult.fail(f"Failed to complete journal entry: {save_result.error}")

    journal_entry = save_result.data

    post_result = post_journal_entry(actor, journal_entry.id)
    if not post_result.success:
        return CommandResult.fail(f"Failed to post journal entry: {post_result.error}")

    # Now create stock ledger entries
    created_entries = []
    event_entries = []

    with command_writes_allowed():
        for pline in processed_lines:
            item = pline["item"]
            warehouse = pline["warehouse"]
            qty_delta = pline["qty_delta"]
            unit_cost = pline["unit_cost"]
            value_delta = pline["value_delta"]
            balance = pline["balance"]

            # Calculate new balance
            if qty_delta > 0:
                # Apply receipt
                old_value = balance.qty_on_hand * balance.avg_cost
                new_value = qty_delta * unit_cost
                new_qty = balance.qty_on_hand + qty_delta
                new_avg_cost = (old_value + new_value) / new_qty if new_qty > 0 else unit_cost
            else:
                # Apply issue
                new_qty = balance.qty_on_hand + qty_delta  # qty_delta is negative
                new_avg_cost = balance.avg_cost

            # Get next sequence
            sequence = _get_next_sequence(actor.company)

            entry = StockLedgerEntry.objects.create(
                company=actor.company,
                sequence=sequence,
                source_type=StockLedgerEntry.SourceType.ADJUSTMENT,
                source_id=uuid.UUID(adjustment_public_id),
                source_line_id=uuid.UUID(pline["source_line_id"]) if pline.get("source_line_id") else None,
                warehouse=warehouse,
                item=item,
                qty_delta=qty_delta,
                unit_cost=unit_cost,
                value_delta=value_delta,
                costing_method_snapshot=item.costing_method,
                qty_balance_after=new_qty,
                value_balance_after=new_qty * new_avg_cost,
                avg_cost_after=new_avg_cost,
                posted_at=posted_at,
                posted_by=actor.user,
                journal_entry=journal_entry,
            )
            created_entries.append(entry)

            # Update balance
            balance.qty_on_hand = new_qty
            balance.avg_cost = new_avg_cost
            balance.stock_value = new_qty * new_avg_cost
            balance.entry_count += 1
            balance.last_entry_date = posted_at.date()
            balance.save()

            # Update item avg_cost if increased
            if qty_delta > 0:
                item.average_cost = new_avg_cost
                item.last_cost = unit_cost
                item.save(update_fields=["average_cost", "last_cost"])

            event_entries.append(StockLedgerEntryData(
                item_public_id=str(item.public_id),
                warehouse_public_id=str(warehouse.public_id),
                qty_delta=str(qty_delta),
                unit_cost=str(unit_cost),
                value_delta=str(value_delta),
                costing_method_snapshot=item.costing_method,
                source_line_id=pline.get("source_line_id"),
            ).to_dict())

    event = emit_event(
        actor=actor,
        event_type=EventTypes.INVENTORY_ADJUSTED,
        aggregate_type="InventoryAdjustment",
        aggregate_id=adjustment_public_id,
        idempotency_key=f"inventory.adjusted:{adjustment_public_id}",
        data=InventoryAdjustedData(
            adjustment_public_id=adjustment_public_id,
            company_public_id=str(actor.company.public_id),
            adjustment_date=adjustment_date.isoformat() if hasattr(adjustment_date, 'isoformat') else str(adjustment_date),
            reason=reason,
            entries=[e for e in event_entries],
            journal_entry_public_id=str(journal_entry.public_id),
            adjusted_at=posted_at.isoformat(),
            adjusted_by_id=actor.user.id,
            adjusted_by_email=actor.user.email,
        ).to_dict(),
    )

    return CommandResult.ok(
        data={
            "entries": created_entries,
            "journal_entry": journal_entry,
            "adjustment_public_id": adjustment_public_id,
        },
        event=event
    )


@transaction.atomic
def record_opening_balance(
    actor: ActorContext,
    as_of_date,
    lines: list,
    opening_balance_equity_account_id: int,
) -> CommandResult:
    """
    Record opening balances for inventory items.

    Dr Inventory, Cr Opening Balance Equity.

    For each line: {
        item: Item instance,
        warehouse: Warehouse instance (optional, uses default),
        qty: Decimal (positive),
        unit_cost: Decimal,
    }
    """
    require(actor, "inventory.opening_balance.create")

    if not lines:
        return CommandResult.fail("No lines provided for opening balance.")

    # Validate opening balance equity account
    try:
        equity_account = Account.objects.get(
            company=actor.company, pk=opening_balance_equity_account_id
        )
    except Account.DoesNotExist:
        return CommandResult.fail("Opening balance equity account not found.")

    if not equity_account.is_postable:
        return CommandResult.fail("Opening balance equity account is not postable.")

    opening_public_id = str(uuid.uuid4())
    posted_at = timezone.now()

    # Build journal entry lines grouped by inventory account
    inventory_by_account = {}
    processed_lines = []

    for line in lines:
        item = line["item"]
        try:
            warehouse = line.get("warehouse") or _get_default_warehouse(actor.company)
        except NoWarehouseError as e:
            return CommandResult.fail(str(e))
        qty = Decimal(str(line["qty"]))
        unit_cost = Decimal(str(line["unit_cost"]))

        if qty <= 0:
            return CommandResult.fail(f"Quantity must be positive for opening balance. Got: {qty}")

        if not item.is_inventory_item:
            return CommandResult.fail(f"Item {item.code} is not an inventory item.")

        if not item.inventory_account:
            return CommandResult.fail(f"Item {item.code} has no inventory account configured.")

        value = qty * unit_cost

        processed_lines.append({
            "item": item,
            "warehouse": warehouse,
            "qty": qty,
            "unit_cost": unit_cost,
            "value": value,
        })

        inv_account_id = item.inventory_account_id
        inventory_by_account[inv_account_id] = (
            inventory_by_account.get(inv_account_id, Decimal("0")) + value
        )

    # Build journal entry lines
    je_lines = []
    total_value = Decimal("0")

    for inv_account_id, value in inventory_by_account.items():
        je_lines.append({
            "account_id": inv_account_id,
            "description": "Opening Inventory Balance",
            "debit": value,
            "credit": Decimal("0"),
        })
        total_value += value

    # Credit Opening Balance Equity
    je_lines.append({
        "account_id": opening_balance_equity_account_id,
        "description": "Opening Inventory Balance",
        "debit": Decimal("0"),
        "credit": total_value,
    })

    # Create and post journal entry
    je_result = create_journal_entry(
        actor=actor,
        date=as_of_date,
        memo="Opening Inventory Balance",
        lines=je_lines,
        kind=JournalEntry.Kind.OPENING,
    )

    if not je_result.success:
        return CommandResult.fail(f"Failed to create journal entry: {je_result.error}")

    journal_entry = je_result.data

    save_result = save_journal_entry_complete(actor, journal_entry.id)
    if not save_result.success:
        return CommandResult.fail(f"Failed to complete journal entry: {save_result.error}")

    journal_entry = save_result.data

    post_result = post_journal_entry(actor, journal_entry.id)
    if not post_result.success:
        return CommandResult.fail(f"Failed to post journal entry: {post_result.error}")

    # Now create stock ledger entries
    created_entries = []
    event_entries = []

    with command_writes_allowed():
        for pline in processed_lines:
            item = pline["item"]
            warehouse = pline["warehouse"]
            qty = pline["qty"]
            unit_cost = pline["unit_cost"]
            value = pline["value"]

            # Get or create inventory balance
            balance, _ = InventoryBalance.objects.select_for_update().get_or_create(
                company=actor.company,
                item=item,
                warehouse=warehouse,
                defaults={
                    "qty_on_hand": Decimal("0"),
                    "avg_cost": Decimal("0"),
                    "stock_value": Decimal("0"),
                }
            )

            # Apply receipt
            old_value = balance.qty_on_hand * balance.avg_cost
            new_value = qty * unit_cost
            new_qty = balance.qty_on_hand + qty
            new_avg_cost = (old_value + new_value) / new_qty if new_qty > 0 else unit_cost

            # Get next sequence
            sequence = _get_next_sequence(actor.company)

            entry = StockLedgerEntry.objects.create(
                company=actor.company,
                sequence=sequence,
                source_type=StockLedgerEntry.SourceType.OPENING_BALANCE,
                source_id=uuid.UUID(opening_public_id),
                warehouse=warehouse,
                item=item,
                qty_delta=qty,
                unit_cost=unit_cost,
                value_delta=value,
                costing_method_snapshot=item.costing_method,
                qty_balance_after=new_qty,
                value_balance_after=new_qty * new_avg_cost,
                avg_cost_after=new_avg_cost,
                posted_at=posted_at,
                posted_by=actor.user,
                journal_entry=journal_entry,
            )
            created_entries.append(entry)

            # Update balance
            balance.qty_on_hand = new_qty
            balance.avg_cost = new_avg_cost
            balance.stock_value = new_qty * new_avg_cost
            balance.entry_count += 1
            balance.last_entry_date = posted_at.date()
            balance.save()

            # Update item
            item.average_cost = new_avg_cost
            item.last_cost = unit_cost
            item.save(update_fields=["average_cost", "last_cost"])

            event_entries.append(StockLedgerEntryData(
                item_public_id=str(item.public_id),
                warehouse_public_id=str(warehouse.public_id),
                qty_delta=str(qty),
                unit_cost=str(unit_cost),
                value_delta=str(value),
                costing_method_snapshot=item.costing_method,
            ).to_dict())

    event = emit_event(
        actor=actor,
        event_type=EventTypes.INVENTORY_OPENING_BALANCE,
        aggregate_type="InventoryOpeningBalance",
        aggregate_id=opening_public_id,
        idempotency_key=f"inventory.opening_balance:{opening_public_id}",
        data=InventoryOpeningBalanceData(
            company_public_id=str(actor.company.public_id),
            as_of_date=as_of_date.isoformat() if hasattr(as_of_date, 'isoformat') else str(as_of_date),
            entries=[e for e in event_entries],
            journal_entry_public_id=str(journal_entry.public_id),
            recorded_at=posted_at.isoformat(),
            recorded_by_id=actor.user.id,
            recorded_by_email=actor.user.email,
        ).to_dict(),
    )

    return CommandResult.ok(
        data={
            "entries": created_entries,
            "journal_entry": journal_entry,
            "opening_public_id": opening_public_id,
        },
        event=event
    )
