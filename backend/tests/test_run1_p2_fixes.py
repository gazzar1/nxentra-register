# tests/test_run1_p2_fixes.py
"""
Run-1 P2 fix batch (fresh-merchant E2E run, 2026-07-06/07).

F1 — CLEARING_BALANCE detector was dead on every real company: it
  filtered Account.role in {SHOPIFY_CLEARING, STRIPE_CLEARING}, but
  onboarding seeds 11500/11510 with role=LIQUIDITY — the clearing role
  lives on ModuleAccountMapping, which the detector never consulted.
  Live repro: Gazzar Store, 650.00 clearing residual, scan reported
  "0 new, 0 auto-resolved, 0 open".
  Fix: source clearing accounts from ModuleAccountMapping, keep the
  Account.role filter as a legacy fallback, dedupe across both.

F12 — refund restock posted the inventory VALUE (restock JE) but never
  the QUANTITY: no StockLedgerEntry / InventoryBalance movement, so the
  stock subledger diverged from GL 13000 by every restocked return and
  weighted-average costs computed off the wrong base thereafter.
  Live repro: Coffee Mug qty stayed -2 after a 1-unit restock; GL said
  inventory -300 while the ledger implied -400.
  Fix: _record_restock_receipt mirrors the fulfillment issue path via
  inventory.commands.record_stock_receipt — idempotent per refund
  (keyed on SALES_RETURN + refund.public_id) and also invoked from the
  replay path so rebuilds backfill pre-fix divergence.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from accounting.mappings import ModuleAccountMapping
from accounting.models import Account
from bank_connector.exceptions import detect_clearing_balance_anomalies
from bank_connector.models import ReconciliationException
from projections.models import AccountBalance
from projections.write_barrier import command_writes_allowed, projection_writes_allowed

# ── Shared fixtures ──────────────────────────────────────────────────


@pytest.fixture
def clearing_account(db, company):
    """11500 exactly as onboarding seeds it: role=LIQUIDITY, NOT
    SHOPIFY_CLEARING (accounts/commands.py seed tuple)."""
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="11500",
            name="Shopify Clearing",
            account_type=Account.AccountType.ASSET,
            role="LIQUIDITY",
            status=Account.Status.ACTIVE,
        )


def _set_balance(company, account, amount):
    with projection_writes_allowed():
        AccountBalance.objects.update_or_create(
            company=company,
            account=account,
            defaults={"balance": Decimal(amount)},
        )


def _make_legacy_clearing_account(company, code, name, role):
    """Simulate a pre-validation legacy row: Account.role carrying
    SHOPIFY_CLEARING/STRIPE_CLEARING directly. Today's AccountRole
    choices reject these on save (they were never valid choices — one
    more proof the original Account.role filter was dead code), so
    legacy rows can only exist via raw writes; mirror that with a
    queryset update that bypasses full_clean."""
    with projection_writes_allowed():
        acct = Account.objects.projection().create(
            company=company,
            code=code,
            name=name,
            account_type=Account.AccountType.ASSET,
            role="LIQUIDITY",
            status=Account.Status.ACTIVE,
        )
    Account.objects.filter(id=acct.id).update(role=role)
    acct.refresh_from_db()
    return acct


def _map_clearing(company, account, role="SHOPIFY_CLEARING", module="shopify_connector"):
    with command_writes_allowed():
        ModuleAccountMapping.objects.create(
            company=company,
            module=module,
            role=role,
            account=account,
        )


# ── F1: detector keys on the mapping role ────────────────────────────


def test_clearing_detector_fires_via_mapping_role(db, company, clearing_account):
    """The production repro: account role=LIQUIDITY, clearing role only
    on ModuleAccountMapping, 650.00 residual → one MEDIUM exception."""
    _map_clearing(company, clearing_account)
    _set_balance(company, clearing_account, "650.00")

    created = detect_clearing_balance_anomalies(company)

    assert len(created) == 1
    exc = created[0]
    assert exc.exception_type == ReconciliationException.ExceptionType.CLEARING_BALANCE
    assert exc.severity == ReconciliationException.Severity.MEDIUM
    assert exc.platform == "shopify"
    assert exc.amount == Decimal("650.00")
    assert exc.details["role"] == "SHOPIFY_CLEARING"


def test_clearing_detector_legacy_account_role_still_fires(db, company):
    """Pre-mapping data: Account.role carries SHOPIFY_CLEARING directly
    (no ModuleAccountMapping row). Must keep firing."""
    acct = _make_legacy_clearing_account(company, "11509", "Legacy Shopify Clearing", "SHOPIFY_CLEARING")
    _set_balance(company, acct, "6000.00")

    created = detect_clearing_balance_anomalies(company)

    assert len(created) == 1
    assert created[0].severity == ReconciliationException.Severity.HIGH


def test_clearing_detector_dedupes_mapping_and_legacy(db, company):
    """Same account reachable via BOTH the mapping and Account.role →
    exactly one exception, not two."""
    acct = _make_legacy_clearing_account(company, "11510", "Stripe Clearing", "STRIPE_CLEARING")
    _map_clearing(company, acct, role="STRIPE_CLEARING", module="platform_stripe")
    _set_balance(company, acct, "100.00")

    created = detect_clearing_balance_anomalies(company)

    assert len(created) == 1
    assert created[0].platform == "stripe"
    assert created[0].severity == ReconciliationException.Severity.LOW


def test_clearing_detector_silent_on_zero_balance(db, company, clearing_account):
    _map_clearing(company, clearing_account)
    _set_balance(company, clearing_account, "0.00")

    assert detect_clearing_balance_anomalies(company) == []


# ── F12: restock records the stock receipt ───────────────────────────


@pytest.fixture
def shopify_store(db, company):
    from shopify_connector.models import ShopifyStore

    return ShopifyStore.objects.create(
        company=company,
        shop_domain="p2fix-store.myshopify.com",
        status=ShopifyStore.Status.ACTIVE,
    )


@pytest.fixture
def refund_record(db, company, shopify_store):
    from shopify_connector.models import ShopifyOrder, ShopifyRefund

    order = ShopifyOrder.objects.create(
        company=company,
        store=shopify_store,
        shopify_order_id=91005,
        shopify_order_number="1005",
        shopify_order_name="#1005",
        total_price=Decimal("250.00"),
        subtotal_price=Decimal("250.00"),
        currency="USD",
        financial_status="refunded",
        shopify_created_at=datetime(2026, 7, 6, tzinfo=UTC),
        order_date=date(2026, 7, 6),
        status=ShopifyOrder.Status.PROCESSED,
    )
    return ShopifyRefund.objects.create(
        company=company,
        order=order,
        shopify_refund_id=95001,
        amount=Decimal("250.00"),
        currency="USD",
        shopify_created_at=datetime(2026, 7, 6, tzinfo=UTC),
        status=ShopifyRefund.Status.PROCESSED,
    )


@pytest.fixture
def stocked_item(db, company):
    from inventory.models import Warehouse
    from sales.models import Item

    with command_writes_allowed():
        Warehouse.objects.create(
            company=company,
            code="MAIN",
            name="Main Warehouse",
            is_default=True,
            is_active=True,
        )
        return Item.objects.create(
            company=company,
            code="MUG-01",
            name="Coffee Mug",
            item_type="INVENTORY",
            default_unit_price=Decimal("250.00"),
            default_cost=Decimal("100.00"),
            costing_method="WEIGHTED_AVERAGE",
            is_active=True,
        )


def _restock_lines(item, qty=1, unit_cost="100.00"):
    unit_cost = Decimal(unit_cost)
    return [
        {
            "sku": item.code,
            "title": item.name,
            "quantity": qty,
            "unit_cost": unit_cost,
            "total_cost": unit_cost * qty,
            "inventory_account": None,  # not used by the receipt path
            "cogs_account": None,
            "item": item,
        }
    ]


def _handler():
    from shopify_connector.projections import ShopifyAccountingHandler

    return ShopifyAccountingHandler()


def test_restock_receipt_moves_quantity_and_value(db, company, owner_membership, refund_record, stocked_item):
    """The F12 repro inverted: a 1-unit restock at cost 100 must create a
    StockLedgerEntry (+1 qty, +100 value) and move InventoryBalance."""
    from inventory.models import StockLedgerEntry as SLE
    from projections.models import InventoryBalance

    _handler()._record_restock_receipt(
        company,
        refund_record,
        _restock_lines(stocked_item),
        journal_entry=None,
    )

    entries = SLE.objects.filter(
        company=company,
        source_type=SLE.SourceType.SALES_RETURN,
        source_id=refund_record.public_id,
    )
    assert entries.count() == 1
    sle = entries.get()
    assert sle.qty_delta == Decimal("1")
    assert sle.value_delta == Decimal("100.00")
    assert sle.item_id == stocked_item.id

    bal = InventoryBalance.objects.get(company=company, item=stocked_item)
    assert bal.qty_on_hand == Decimal("1")
    assert bal.stock_value == Decimal("100.00")


def test_restock_receipt_is_idempotent_per_refund(db, company, owner_membership, refund_record, stocked_item):
    """Duplicate refund events / backfills must not double-receive: second
    call with the same refund is a no-op."""
    from inventory.models import StockLedgerEntry as SLE
    from projections.models import InventoryBalance

    handler = _handler()
    for _ in range(2):
        handler._record_restock_receipt(
            company,
            refund_record,
            _restock_lines(stocked_item),
            journal_entry=None,
        )

    assert (
        SLE.objects.filter(
            company=company,
            source_type=SLE.SourceType.SALES_RETURN,
            source_id=refund_record.public_id,
        ).count()
        == 1
    )
    bal = InventoryBalance.objects.get(company=company, item=stocked_item)
    assert bal.qty_on_hand == Decimal("1")


def test_restock_receipt_anchors_values_to_the_je_lines(db, company, owner_membership, refund_record, stocked_item):
    """Adversarial-review fix: on backfill, values must come from the posted
    JE's own inventory-debit lines — NOT replay-time item costs, which drift
    via product cost sync. Item cost says 100; the historic JE says 77 —
    the receipt must book 77."""
    import uuid as _uuid

    from accounting.models import Account, JournalEntry, JournalLine
    from inventory.models import StockLedgerEntry as SLE

    with projection_writes_allowed():
        inv_acct = Account.objects.projection().create(
            company=company,
            code="13000",
            name="Inventory",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        entry = JournalEntry.objects.projection().create(
            company=company,
            public_id=_uuid.uuid4(),
            date=date(2026, 7, 6),
            period=7,
            memo="Shopify restock: Order #1005 (Refund 95001)",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            currency="EGP",
            exchange_rate=Decimal("1"),
            source_module="shopify_connector",
            source_document="95001",
        )
        JournalLine.objects.projection().create(
            entry=entry,
            company=company,
            public_id=_uuid.uuid4(),
            line_no=1,
            account=inv_acct,
            description="Restock: Coffee Mug x1",
            debit=Decimal("77.00"),
            credit=Decimal("0"),
            currency="EGP",
            exchange_rate=Decimal("1"),
        )

    lines = _restock_lines(stocked_item)  # current item cost: 100.00
    lines[0]["inventory_account"] = inv_acct

    _handler()._record_restock_receipt(company, refund_record, lines, journal_entry=entry)

    sle = SLE.objects.get(
        company=company,
        source_type=SLE.SourceType.SALES_RETURN,
        source_id=refund_record.public_id,
    )
    assert sle.value_delta == Decimal("77.00")
    assert sle.unit_cost == Decimal("77.00")
    assert sle.journal_entry_id == entry.id


def test_restock_receipt_never_fx_converts_books_costs(db, company, owner_membership, refund_record, stocked_item):
    """Adversarial-review P1: item costs are books-currency; a foreign-
    currency refund must NOT inflate the receipt by the FX rate (the old
    code multiplied by 48 on USD-store/EGP-books setups and persisted the
    poison into avg_cost). The helper no longer takes fx args at all —
    assert the recorded value equals the raw books cost."""
    from inventory.models import StockLedgerEntry as SLE
    from projections.models import InventoryBalance

    _handler()._record_restock_receipt(
        company,
        refund_record,  # refund currency USD on this fixture
        _restock_lines(stocked_item),
        journal_entry=None,
    )

    sle = SLE.objects.get(
        company=company,
        source_type=SLE.SourceType.SALES_RETURN,
        source_id=refund_record.public_id,
    )
    assert sle.unit_cost == Decimal("100.00")  # NOT 4800.00
    bal = InventoryBalance.objects.get(company=company, item=stocked_item)
    assert bal.avg_cost == Decimal("100.00")


def test_restock_receipt_no_owner_propagates(db, company, refund_record, stocked_item):
    """Adversarial-review fix: failures must be operator-visible, not
    swallowed. With no active OWNER, system_actor_for_company raises and the
    exception PROPAGATES (BaseProjection.on_error → ProjectionFailureLog; on
    the fresh path the per-event transaction rolls the JE back too, so a
    retry recreates everything)."""
    from inventory.models import StockLedgerEntry as SLE

    with pytest.raises(ValueError):
        _handler()._record_restock_receipt(
            company,
            refund_record,
            _restock_lines(stocked_item),
            journal_entry=None,
        )

    assert not SLE.objects.filter(company=company).exists()


# ── F1 follow-up: CLEARING_BALANCE lifecycle ─────────────────────────


def test_clearing_exception_auto_resolves_on_zero_balance(db, company, clearing_account):
    """Adversarial-review fix: once the clearing balance returns to zero,
    the open exception must close on the next scan instead of sitting OPEN
    forever at its stale peak amount."""
    from bank_connector.exceptions import auto_resolve_matched

    _map_clearing(company, clearing_account)
    _set_balance(company, clearing_account, "650.00")
    created = detect_clearing_balance_anomalies(company)
    assert len(created) == 1

    # Payout drains the account.
    _set_balance(company, clearing_account, "0.00")
    resolved = auto_resolve_matched(company)

    assert resolved == 1
    created[0].refresh_from_db()
    assert created[0].status == ReconciliationException.Status.RESOLVED
    assert "zero" in created[0].resolution_note


def test_clearing_exception_stays_open_while_balance_nonzero(db, company, clearing_account):
    """The auto-resolve must NOT close exceptions whose residual persists."""
    from bank_connector.exceptions import auto_resolve_matched

    _map_clearing(company, clearing_account)
    _set_balance(company, clearing_account, "650.00")
    created = detect_clearing_balance_anomalies(company)

    resolved = auto_resolve_matched(company)

    assert resolved == 0
    created[0].refresh_from_db()
    assert created[0].status == ReconciliationException.Status.OPEN
