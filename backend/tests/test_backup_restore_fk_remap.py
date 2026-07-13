# tests/test_backup_restore_fk_remap.py
"""
A161 drill finding #2 (2026-07-13): the timed restore crashed at COMMIT
with `inventory_warehouse ... still referenced from
projections_inventorybalance`. Root cause: for a NON-nullable in-registry
FK whose target model imports LATER in registry order
(projections.InventoryBalance.warehouse -> inventory.Warehouse), the
importer passed the raw OLD id through with no deferred fixup — Postgres
FK constraints are deferred to commit, so the stale id detonated there
and took the whole restore down for any company with inventory balances.

Also fixed in the same pass, same mechanism:
- the BusinessEvent import branch dropped its deferred fixups entirely,
  silently nulling caused_by_event links on every restore;
- deferred-FK resolution scanned EVERY model's pk_map for the old id
  (mis-mapping whenever two models shared an old integer PK) — it now
  resolves via the related model's exact entry, and an unresolvable
  required FK raises RestoreError instead of a cryptic commit crash.
"""

import io
from decimal import Decimal

import pytest

from backups.exporter import export_company
from backups.importer import restore_company
from events.models import BusinessEvent
from inventory.models import Warehouse
from projections.models import InventoryBalance
from projections.write_barrier import command_writes_allowed, projection_writes_allowed
from sales.models import Item

pytestmark = pytest.mark.django_db


@pytest.fixture
def company_with_inventory(company):
    """The exact shape that killed the drill: a warehouse + item +
    InventoryBalance row (what any merchant with synced products and one
    fulfillment has)."""
    with command_writes_allowed():
        warehouse = Warehouse.objects.create(company=company, code="MAIN", name="Main", is_default=True, is_active=True)
        item = Item.objects.create(
            company=company,
            code="MUG-01",
            name="Coffee Mug",
            item_type=Item.ItemType.INVENTORY,
            default_cost=Decimal("100.00"),
            costing_method="WEIGHTED_AVERAGE",
            is_active=True,
        )
    with projection_writes_allowed():
        InventoryBalance.objects.create(
            company=company,
            item=item,
            warehouse=warehouse,
            qty_on_hand=Decimal("5"),
            avg_cost=Decimal("100.00"),
            stock_value=Decimal("500.00"),
        )
    return company


def test_restore_remaps_nonnullable_projection_fks(company_with_inventory):
    """The drill repro: RED before the fix with IntegrityError at commit."""
    company = company_with_inventory
    zip_bytes, _ = export_company(company)

    result = restore_company(company, io.BytesIO(zip_bytes))
    assert result is not None

    # The restored balance must point at the RESTORED warehouse/item rows.
    balance = InventoryBalance.objects.get(company=company)
    warehouse = Warehouse.objects.get(company=company, code="MAIN")
    item = Item.objects.get(company=company, code="MUG-01")
    assert balance.warehouse_id == warehouse.id
    assert balance.item_id == item.id
    assert balance.qty_on_hand == Decimal("5")
    assert balance.stock_value == Decimal("500.00")


def test_restore_preserves_caused_by_event_links(company):
    """The BusinessEvent branch dropped its deferred fixups — restored
    event chains lost their caused_by_event links silently."""
    parent = BusinessEvent.objects.create(
        company=company,
        event_type="drill.parent",
        aggregate_type="DrillAgg",
        aggregate_id="agg-1",
        idempotency_key="drill:parent:1",
        data={},
    )
    BusinessEvent.objects.create(
        company=company,
        event_type="drill.child",
        aggregate_type="DrillAgg",
        aggregate_id="agg-1",
        idempotency_key="drill:child:1",
        caused_by_event=parent,
        data={},
    )

    zip_bytes, _ = export_company(company)
    restore_company(company, io.BytesIO(zip_bytes))

    restored_parent = BusinessEvent.objects.get(company=company, idempotency_key="drill:parent:1")
    restored_child = BusinessEvent.objects.get(company=company, idempotency_key="drill:child:1")
    assert restored_child.caused_by_event_id == restored_parent.id, (
        "caused_by_event links must survive a restore, not silently null out"
    )
