# tests/e2e/test_a4_shopify_operator_entrypoints.py
"""A4 — manual OPERATOR Shopify entrypoints on real PostgreSQL.

The two manual management commands run the same covered Shopify work as the
scheduled Celery tasks, so since PR #119 they hit the same fresh ``Company``
admission lock — and a management command has no request/middleware, so without
the tenant/RLS execution context that lock's ``Company`` query is hidden by
production RLS. These proofs cover:

``resync_shopify_orders`` (RLS visibility, ``rls_enforced``): orders / payouts /
products run under ``_execute_scheduled_store_sync`` (two-plane tenant context,
bypass OFF for a shared tenant), the admission lock sees the exact company with no
``Company.DoesNotExist``, a non-writable tenant is skipped with no Shopify calls,
and a dedicated tenant reaches the control-plane admission lock.

``backfill_settlement_providers``: idempotent NONE-profile backfill and ``--dry-run``
under enforced RLS; the LOCKED ``Company`` (not the cached ``store.company``) is
passed to ``_setup_shopify_accounts``; a mid-store failure rolls the WHOLE per-store
mutation back; and — via the two-connection activation/mutation harness —
serialization against pilot activation both ways: activation-first leaves NO
INVENTORY/COGS module mappings, backfill-first is refused by the activation
preflight (``module_inv_cogs_mapping``) with the profile left NONE.

The RLS-visibility proofs reuse the self-validating ``rls_enforced`` role fixture
(SET ROLE to a NOBYPASSRLS role on a FORCE-RLS table); the serialization proofs
reuse the two-connection threading harness (serialization ≠ RLS visibility, so
they run under the suite default like the merged admission-serialization suite).
"""

import io
import threading

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from accounts import rls
from accounts.models import Company

# Two-connection activation/mutation serialization harness.
from .test_a4_runtime_admission_serialization import (
    _activation_clean,
    _activation_first,
    _fresh_profile,
    _pause_hook,
    _run_activation,
    _seed_store,
    _someone_is_lock_waiting,
    _wait,
    _Worker,
)

# RLS-ENFORCED role fixture + committed-company / fake-client / lock-spy helpers.
from .test_a4_shopify_scheduled_rls import (  # noqa: F401  (rls_enforced used as a fixture)
    TENANT_DATA_ALIAS,
    _FakeClient,
    _guc,
    _make_company,
    _mock_dedicated_routing,
    _order_payload,
    _spy_lock,
    rls_enforced,
)

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", TENANT_DATA_ALIAS]),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="operator-entrypoint tenant/RLS + admission serialization is only provable on PostgreSQL",
    ),
]

ISO = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1


@pytest.fixture(autouse=True)
def _suppress_projection_dispatch(monkeypatch):
    """Record — never run — the post-commit projection dispatch (mirrors the two
    reused suites): these tests exercise the COMMAND layer under the admission lock,
    not the downstream projection which owns its own tenant context."""
    import events.emitter as emitter

    dispatched: list[int] = []
    monkeypatch.setattr(emitter, "_schedule_projection_processing", dispatched.append)
    return dispatched


@pytest.fixture(autouse=True)
def _no_foreign_active_stores(db):
    """Quarantine leftover ACTIVE ShopifyStores before each test. Both commands
    discover EVERY ACTIVE store (backfill has no --company flag at all), and the
    project default is ``--reuse-db``, so a store stranded by any aborted previous
    session would be swept into the run — booking fake orders into a foreign
    company, polluting the lock recorder, and (for the backfill-first race) pausing
    the FIRST _setup_shopify_accounts call on the foreign company's admission lock.
    Each test then creates the only ACTIVE store itself."""
    from projections.write_barrier import command_writes_allowed
    from shopify_connector.models import ShopifyStore

    with rls.rls_bypass(), command_writes_allowed():
        ShopifyStore.objects.filter(status=ShopifyStore.Status.ACTIVE).update(status=ShopifyStore.Status.DISCONNECTED)


# =============================================================================
# resync_shopify_orders — tenant/RLS execution under enforced RLS
# =============================================================================


def test_resync_shared_tenant_books_orders_under_enforced_rls(rls_enforced, monkeypatch):  # noqa: F811  (rls_enforced imported fixture used as a param)
    """The manual re-sync routes per-store work through the tenant execution path,
    so a shared tenant's orders book with the admission lock scoped to the exact
    company (bypass OFF) and no Company.DoesNotExist — and the connection is
    restored to the no-context baseline on return."""
    from shopify_connector import commands as cmds
    from shopify_connector.models import ShopifyOrder

    co, _store = _make_company()
    fake = _FakeClient(orders=[_order_payload(70001, "100.00"), _order_payload(70002, "150.00")])
    monkeypatch.setattr(cmds, "_admin_client", lambda s: fake)
    locks: list[dict] = []
    _spy_lock(monkeypatch, locks)

    # Precondition (the #119 bug): with no tenant context, RLS hides the Company.
    with pytest.raises(Company.DoesNotExist):
        Company.objects.get(pk=co.id)

    call_command("resync_shopify_orders", "--company", co.slug, stdout=io.StringIO())

    with rls.rls_bypass():
        assert ShopifyOrder.objects.filter(company=co, shopify_order_id__in=[70001, 70002]).count() == 2
    assert len(locks) >= 2, locks
    assert all(r["company_id"] == str(co.id) and r["bypass"] == "off" for r in locks), locks

    # Context restored to the enforced no-context baseline on return.
    assert _guc("app.current_company_id") == "" and _guc("app.rls_bypass") == "off"
    with pytest.raises(Company.DoesNotExist):
        Company.objects.get(pk=co.id)


class _PayoutEmittingClient(_FakeClient):
    """Fake client whose payout sync BOOKS one payout (and therefore EMITS,
    wiping the connection's RLS session in the emitter's finally) and whose
    product sync has ONE real SKU'd variant to create — so the products leg
    performs company-scoped writes and its re-assert is load-bearing rather
    than incidentally covered by an empty page iterator."""

    def list_payouts(self, status="paid", limit=None):
        return [{"id": 771001, "amount": "50.00", "currency": "USD", "status": "paid", "date": "2026-08-01"}]

    def iter_product_pages(self):
        products = [
            {
                "id": 881001,
                "title": "Resync Widget",
                "product_type": "widget",
                "images": [],
                "variants": [
                    {
                        "id": 881002,
                        "sku": "RESYNC-SKU-1",
                        "price": "25.00",
                        "inventory_item_id": 881003,
                        "title": "Default Title",
                    }
                ],
            }
        ]
        yield (products, {})


def test_resync_include_payouts_and_products_run_after_emitter_clear(rls_enforced, monkeypatch):  # noqa: F811  (rls_enforced imported fixture used as a param)
    """--include-payouts / --include-products run AFTER earlier emits (the emitter
    clears the connection RLS session): the order leg emits before payouts, and the
    payout leg BOOKS+emits before products — so each leg's re-assert is
    load-bearing. Both legs must COMPLETE (asserted on the command's own output),
    with every admission lock scoped to the exact company, bypass OFF."""
    from shopify_connector import commands as cmds
    from shopify_connector.models import ShopifyPayout

    co, _store = _make_company()
    fake = _PayoutEmittingClient(orders=[_order_payload(71001, "100.00")])
    monkeypatch.setattr(cmds, "_admin_client", lambda s: fake)
    locks: list[dict] = []
    _spy_lock(monkeypatch, locks)

    # Must not raise Company.DoesNotExist on the payout/product admission locks that
    # run after the preceding leg's emit cleared the RLS session.
    out = io.StringIO()
    call_command("resync_shopify_orders", "--company", co.slug, "--include-payouts", "--include-products", stdout=out)

    # COMPLETION is asserted, not just absence of a raise: the payout actually
    # booked (created=1) and the products leg ran with no error line.
    output = out.getvalue()
    assert "Payouts: created=1" in output, output
    assert "Payout error" not in output, output
    assert "Products: created=1" in output, output
    assert "Product error" not in output, output
    with rls.rls_bypass():
        assert ShopifyPayout.objects.filter(company=co, shopify_payout_id=771001).exists()
        from sales.models import Item

        assert Item.objects.filter(company=co, code="RESYNC-SKU-1").exists(), "product leg must have created the Item"

    # Order + payout admission locks ran after prior emits, under the exact
    # company / bypass off — the load-bearing re-assert proof for the manual path.
    assert len(locks) >= 2, locks
    assert all(r["company_id"] == str(co.id) and r["bypass"] == "off" for r in locks), locks


def test_resync_non_writable_tenant_is_skipped(rls_enforced, monkeypatch):  # noqa: F811  (rls_enforced imported fixture used as a param)
    """A frozen tenant (is_writable False) is skipped by the shared execution path:
    no Shopify client construction, no orders written."""
    from shopify_connector import commands as cmds
    from shopify_connector.models import ShopifyOrder
    from tenant.models import TenantDirectory

    co, _store = _make_company()
    monkeypatch.setattr(
        TenantDirectory,
        "get_tenant_info",
        classmethod(
            lambda cls, company_id: {
                "db_alias": "default",
                "is_shared": True,
                "status": "MIGRATING",
                "is_writable": False,
            }
        ),
    )
    admin_calls = {"n": 0}
    real_admin = cmds._admin_client
    monkeypatch.setattr(
        cmds, "_admin_client", lambda s: (admin_calls.__setitem__("n", admin_calls["n"] + 1), real_admin(s))[1]
    )

    out = io.StringIO()
    call_command("resync_shopify_orders", "--company", co.slug, "--include-payouts", "--include-products", stdout=out)

    # The skip is REPORTED, not silent (and not a fabricated zero-count result).
    assert "Skipped: tenant not writable" in out.getvalue(), out.getvalue()
    assert admin_calls["n"] == 0, "a frozen tenant must trigger no Shopify client construction / calls"
    with rls.rls_bypass():
        assert not ShopifyOrder.objects.filter(company=co).exists()


def test_resync_dedicated_routing_reaches_control_plane_admission(rls_enforced, monkeypatch):  # noqa: F811  (rls_enforced imported fixture used as a param)
    """A dedicated-DB tenant: the manual re-sync reaches the Company admission lock
    on the CONTROL plane (default) with the exact company scoped and bypass OFF — no
    Company.DoesNotExist. (Full booking for a dedicated tenant additionally needs the
    events-emitter tenant-DB transaction handling — a separate pre-existing
    limitation outside this envelope; there are no dedicated tenants today.)"""
    from shopify_connector import commands as cmds

    a, _store = _make_company()
    _mock_dedicated_routing(monkeypatch)
    monkeypatch.setattr(cmds, "_admin_client", lambda s: _FakeClient(orders=[_order_payload(72001, "100.00")]))
    locks: list[dict] = []
    _spy_lock(monkeypatch, locks)

    call_command("resync_shopify_orders", "--company", a.slug, stdout=io.StringIO())

    assert locks, "the manual re-sync must reach the control-plane admission lock (no Company.DoesNotExist)"
    assert all(r["company_id"] == str(a.id) and r["bypass"] == "off" for r in locks), locks


# =============================================================================
# backfill_settlement_providers — admission serialization + RLS
# =============================================================================


def _shopify_provider_count(company):
    from accounting.settlement_provider import SettlementProvider

    with rls.rls_bypass():
        return SettlementProvider.objects.filter(company=company, external_system="shopify").count()


def _inv_cogs_module_mappings(company):
    from accounting.mappings import ModuleAccountMapping

    with rls.rls_bypass():
        return ModuleAccountMapping.objects.filter(
            company=company, module="shopify_connector", role__in=("INVENTORY", "COGS")
        ).count()


def test_backfill_none_profile_is_idempotent_under_enforced_rls(rls_enforced, monkeypatch):  # noqa: F811  (rls_enforced imported fixture used as a param)
    """The NONE-profile backfill admits under the Company lock (bypass OFF, exact
    company, no DoesNotExist) and is idempotent: a second run adds nothing."""
    co, _store = _make_company()
    locks: list[dict] = []
    _spy_lock(monkeypatch, locks)

    out1 = io.StringIO()
    call_command("backfill_settlement_providers", stdout=out1)
    first = _shopify_provider_count(co)
    assert first > 0, "backfill must create the default settlement providers"
    assert f"({first} added)" in out1.getvalue(), out1.getvalue()
    assert "Backfill complete." in out1.getvalue(), out1.getvalue()
    assert locks and all(r["company_id"] == str(co.id) and r["bypass"] == "off" for r in locks), locks

    out2 = io.StringIO()
    call_command("backfill_settlement_providers", stdout=out2)
    assert _shopify_provider_count(co) == first, "re-run must be idempotent (no new providers)"
    assert f"-> now {first} row(s) (0 added)" in out2.getvalue(), out2.getvalue()


def test_backfill_dry_run_makes_no_writes_and_no_lock(rls_enforced, monkeypatch):  # noqa: F811  (rls_enforced imported fixture used as a param)
    """--dry-run takes NO admission lock and writes nothing — while still RUNNING
    (per-store report + dry-run notice asserted on the command's own output, so a
    no-op command cannot pass)."""
    co, _store = _make_company()
    locks: list[dict] = []
    _spy_lock(monkeypatch, locks)

    out = io.StringIO()
    call_command("backfill_settlement_providers", "--dry-run", stdout=out)

    output = out.getvalue()
    assert co.name in output, output  # the store was discovered and reported
    assert "0 existing row(s)" in output, output
    assert "Dry-run — no writes made." in output, output
    assert not locks, "dry-run must not take the admission lock"
    assert _shopify_provider_count(co) == 0, "dry-run must create no SettlementProvider rows"
    assert _inv_cogs_module_mappings(co) == 0, "dry-run must wire no module mappings"


def test_backfill_passes_locked_company_to_setup(monkeypatch):
    """_setup_shopify_accounts must receive the YIELDED LOCKED Company (the row
    returned by lock_company_for_admission), not the cached store.company — so its
    is_supported(INVENTORY) decision is made on the serialized profile."""
    import accounts.pilot_policy as pp
    from shopify_connector.management.commands import backfill_settlement_providers as backfill_cmd

    _make_company()

    locked_holder: dict = {}
    real_lock = pp.lock_company_for_admission

    def lock_spy(pk):
        row = real_lock(pk)
        locked_holder["row"] = row
        return row

    setup_arg: dict = {}
    real_setup = backfill_cmd._setup_shopify_accounts

    def setup_spy(company):
        setup_arg["company"] = company
        return real_setup(company)

    monkeypatch.setattr(pp, "lock_company_for_admission", lock_spy)
    monkeypatch.setattr(backfill_cmd, "_setup_shopify_accounts", setup_spy)

    call_command("backfill_settlement_providers", stdout=io.StringIO())

    assert "row" in locked_holder and "company" in setup_arg
    assert setup_arg["company"] is locked_holder["row"], (
        "_setup_shopify_accounts got a different Company instance than the locked admission row"
    )


def test_backfill_mid_store_failure_rolls_back_whole_mutation(rls_enforced, monkeypatch):  # noqa: F811  (rls_enforced imported fixture used as a param)
    """SHARED tenant: a failure AFTER _setup_shopify_accounts rolls the WHOLE
    per-store mutation back (the admission block is one default-connection
    transaction): no accounts, mappings, or providers persist — and the connection
    RLS context is restored. Scope note: for a DEDICATED tenant the accounting
    writes route to the data-plane alias OUTSIDE this transaction (disclosed,
    deferred — see the command's module docstring; no dedicated tenants today)."""
    from accounting.models import Account
    from shopify_connector.management.commands import backfill_settlement_providers as backfill_cmd

    co, _store = _make_company()

    def boom(store):
        raise RuntimeError("boom mid-backfill")

    monkeypatch.setattr(backfill_cmd, "_ensure_shopify_sales_setup", boom)

    with pytest.raises(RuntimeError, match="boom mid-backfill"):
        call_command("backfill_settlement_providers", stdout=io.StringIO())

    with rls.rls_bypass():
        assert not Account.objects.filter(company=co, code="41000").exists(), "SALES_REVENUE account must roll back"
    assert _shopify_provider_count(co) == 0, "no providers may persist after rollback"
    assert _inv_cogs_module_mappings(co) == 0, "no module mappings may persist after rollback"
    # Connection restored to the enforced no-context baseline despite the exception.
    assert _guc("app.current_company_id") == "" and _guc("app.rls_bypass") == "off"


def test_activation_first_backfill_skips_inventory_cogs_mappings(company, owner_membership, monkeypatch):
    """Activation-first: the REAL activation holds its Company FOR UPDATE; the
    backfill observably waits on the admission row, then runs under the ACTIVE ISO
    profile and — reading is_supported(INVENTORY)=False on the LOCKED company —
    wires NO INVENTORY/COGS module mappings."""
    _activation_clean(company)
    _seed_store(company)

    m = _activation_first(
        monkeypatch, company, lambda: call_command("backfill_settlement_providers", stdout=io.StringIO())
    )

    assert m.error is None, f"backfill worker crashed: {m.error}"
    assert _fresh_profile(company) == ISO
    assert _inv_cogs_module_mappings(company) == 0, (
        "an activation-first backfill must leave NO INVENTORY/COGS module mappings on the constrained pilot"
    )
    assert _shopify_provider_count(company) > 0, "the backfill still created its settlement providers"


def test_backfill_first_activation_refuses_on_durable_inventory_mapping(company, owner_membership, monkeypatch):
    """Backfill-first: the backfill admits (reading the NONE profile) and wires the
    INVENTORY/COGS module mappings under the admission lock; activation observably
    waits on the Company row, then its preflight REFUSES on the durable
    module_inv_cogs_mapping — the profile stays NONE."""
    from shopify_connector.management.commands import backfill_settlement_providers as backfill_cmd

    _activation_clean(company)
    _seed_store(company)

    admitted, release = threading.Event(), threading.Event()
    # Pause AFTER _setup_shopify_accounts wired the mappings (admission lock held,
    # uncommitted) so activation blocks on the Company row while they are pending.
    _pause_hook(monkeypatch, backfill_cmd, "_setup_shopify_accounts", admitted, release)

    m = _Worker(lambda: call_command("backfill_settlement_providers", stdout=io.StringIO()), "backfill").start()
    _wait(admitted.is_set, "backfill admitted (admission lock held) and paused after _setup_shopify_accounts")

    a = _Worker(lambda: _run_activation(company.id), "activation").start()
    _wait(_someone_is_lock_waiting, "activation observably waiting on the Company admission row")
    assert a.thread.is_alive(), "activation must block while the backfill holds the admission lock"

    release.set()
    m.join()
    a.join()

    assert m.error is None, f"backfill worker crashed: {m.error}"
    refusal, output = a.result
    assert isinstance(refusal, CommandError), f"activation must REFUSE on the durable inventory mappings: {output}"
    assert "module_inv_cogs_mapping" in output, output
    assert _fresh_profile(company) == Company.PilotProfile.NONE
    # The backfill (reading NONE) did wire them — that is exactly the forbidden
    # durable state the preflight caught.
    assert _inv_cogs_module_mappings(company) == 2
