# tests/e2e/test_a4_shopify_scheduled_rls.py
"""A4 — scheduled Shopify tenant/RLS execution (P1) and paid-order remote
prepare/apply (P2) proofs on real PostgreSQL with RLS ENFORCED.

Since PR #119 every covered Shopify command takes a fresh ``Company`` admission
lock. A scheduled Celery task has no request, so ``TenantRlsMiddleware`` never
ran: under production ``RLS_BYPASS=False`` that lock's ``Company`` query is
hidden by RLS unless the task sets the tenant's RLS session context. These tests
run with ``settings.RLS_BYPASS=False`` and the connection's RLS session
explicitly enforced (the ``rls_enforced`` fixture reproduces a production worker
start: no tenant context, no bypass), and prove:

P1 — ``shopify_connector.tasks._shopify_tenant_execution`` /
``_reassert_shopify_rls``:
  * a shared tenant becomes visible & lockable with ``app.current_company_id``
    set and ``app.rls_bypass`` OFF; a dedicated tenant runs with bypass ON;
  * context is restored on normal exit and on exception, and company A never
    leaks into a later company B task;
  * the event emitter's post-emit ``clear_rls_context`` no longer breaks the
    NEXT admission lock — each unit re-asserts (the regression #119 would
    otherwise introduce, reproduced here);
  * the real scheduled task/command paths (payout, order, refund, product) run
    to completion with no ``Company.DoesNotExist`` under enforced RLS, and the
    admission lock body observes bypass OFF and the exact company.

P2 — ``process_order_paid`` remote prepare / locked apply:
  * unknown-item remote reads happen BEFORE the admission lock;
  * the locked apply is network-free, rechecks the locked Item/pilot state, and
    reuses a concurrently-created Item without duplication;
  * remote failure preserves the documented fallback with no partial mutation.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import connection, transaction

from accounts import rls
from accounts.models import Company

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="scheduled-task tenant/RLS execution is only provable on PostgreSQL",
    ),
]

ISO = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _guc(name: str) -> str:
    with connection.cursor() as cur:
        cur.execute("SELECT current_setting(%s, true)", [name])
        return cur.fetchone()[0]


@pytest.fixture
def rls_enforced(db, settings):
    """Reproduce a production Celery worker connection: ``RLS_BYPASS`` off and the
    default connection's RLS session ENFORCED — no tenant context, no bypass. The
    autouse suite fixture keeps bypass ON, so a scheduled path must set the
    tenant's context itself; here we strip it to expose the raw production state."""
    settings.RLS_BYPASS = False
    rls.set_rls_bypass(False, conn=connection)
    rls.set_current_company_id(None, conn=connection)
    yield
    # Restore the shared bypass the rest of the suite relies on.
    rls.set_rls_bypass(True, conn=connection)
    rls.set_current_company_id(None, conn=connection)


@pytest.fixture(autouse=True)
def _suppress_projection_dispatch(monkeypatch):
    """Record — never run — the post-commit projection dispatch. These tests
    exercise the COMMAND layer (admission lock + row/event write) under enforced
    RLS; the downstream projection owns its own tenant context and is out of
    scope here (mirrors test_a4_runtime_admission_serialization.py)."""
    import events.emitter as emitter

    dispatched: list[int] = []
    monkeypatch.setattr(emitter, "_schedule_projection_processing", dispatched.append)
    return dispatched


def _order_payload(oid, amount="100.00", currency="USD", line_items=None):
    return {
        "id": oid,
        "currency": currency,
        "created_at": "2026-08-01T10:00:00Z",
        "updated_at": "2026-08-01T10:00:00Z",
        "total_price": amount,
        "subtotal_price": amount,
        "total_tax": "0",
        "total_discounts": "0",
        "financial_status": "paid",
        "order_number": str(oid),
        "name": f"#{oid}",
        "line_items": line_items or [],
        "customer": {},
        "transactions": [],
        "shipping_lines": [],
    }


class _FakeClient:
    """Minimal ShopifyAdminClient double — every method the scheduled paths call,
    returning empty / benign data. Overridable per test."""

    def __init__(self, orders=None, currency="USD", unit_cost=("0", "")):
        self._orders = orders or []
        self._currency = currency
        self._unit_cost = unit_cost

    def iter_orders(self, created_at_min, created_at_max):
        yield from self._orders

    def iter_refunded_orders(self, updated_at_min, updated_at_max):
        return iter(())

    def get_order_fulfillments(self, order_id):
        return []

    def get_order_refunds(self, order_id):
        return []

    def list_payouts(self, status="paid", limit=None):
        return []

    def get_shop_currency(self):
        return self._currency

    def get_variant_unit_cost(self, variant_id):
        return self._unit_cost

    def iter_product_pages(self):
        return iter(())


def _make_company(*, currency="USD", pilot=False, with_store=True):
    """A committed Company (+ optional ACTIVE ShopifyStore) created under bypass."""
    from projections.write_barrier import command_writes_allowed
    from shopify_connector.models import ShopifyStore

    uid = uuid4().hex[:8]
    with rls.rls_bypass():
        co = Company.objects.create(
            public_id=uuid4(),
            name=f"Sched Co {uid}",
            slug=f"sched-{uid}",
            default_currency=currency,
            functional_currency=currency,
            fiscal_year_start_month=1,
            is_active=True,
            pilot_profile=(ISO if pilot else Company.PilotProfile.NONE),
        )
        store = None
        if with_store:
            with command_writes_allowed():
                store = ShopifyStore.objects.create(
                    company=co,
                    shop_domain=f"sched-{uid}.myshopify.com",
                    access_token="tok",
                    status=ShopifyStore.Status.ACTIVE,
                )
    return co, store


def _spy_lock(monkeypatch, recorder):
    """Record the RLS session state observed at each admission-lock acquisition."""
    import accounts.pilot_policy as pp

    real = pp.lock_company_for_admission

    def spy(company_pk):
        recorder.append({"bypass": _guc("app.rls_bypass"), "company_id": _guc("app.current_company_id")})
        return real(company_pk)

    monkeypatch.setattr(pp, "lock_company_for_admission", spy)


# =============================================================================
# P1 — _shopify_tenant_execution mechanics
# =============================================================================


def test_shared_tenant_execution_makes_company_visible_and_lockable(rls_enforced):
    from accounts.pilot_policy import lock_company_for_admission
    from shopify_connector.tasks import _shopify_tenant_execution

    co, _ = _make_company()
    cid = co.id

    # Precondition (the #119 bug): with no tenant context, RLS hides the Company.
    with pytest.raises(Company.DoesNotExist):
        Company.objects.get(pk=cid)

    with _shopify_tenant_execution(cid):
        assert _guc("app.rls_bypass") == "off", "shared-tenant command body must run with bypass OFF"
        assert _guc("app.current_company_id") == str(cid)
        assert Company.objects.get(pk=cid).id == cid
        with transaction.atomic():
            assert lock_company_for_admission(cid).id == cid

    # Restored to the enforced no-context precondition on exit.
    with pytest.raises(Company.DoesNotExist):
        Company.objects.get(pk=cid)


def test_dedicated_tenant_execution_runs_with_bypass_on(rls_enforced, monkeypatch):
    from shopify_connector.tasks import _shopify_tenant_execution
    from tenant.models import TenantDirectory

    co, _ = _make_company()

    # A dedicated-DB tenant has no RLS (single tenant per DB). We cannot spin a
    # real tenant database here, so route its alias to 'default' and assert the CM
    # applies the dedicated-tenant semantics: bypass ON.
    monkeypatch.setattr(
        TenantDirectory,
        "get_tenant_info",
        classmethod(
            lambda cls, company_id: {"db_alias": "default", "is_shared": False, "status": "ACTIVE", "is_writable": True}
        ),
    )
    with _shopify_tenant_execution(co.id):
        assert _guc("app.rls_bypass") == "on", "dedicated tenant runs with RLS bypassed"
        assert Company.objects.get(pk=co.id).id == co.id
    # Restored to the shared enforced precondition.
    assert _guc("app.rls_bypass") == "off"


def test_context_isolation_company_a_never_leaks_into_company_b(rls_enforced):
    from shopify_connector.tasks import _shopify_tenant_execution

    a, _ = _make_company()
    b, _ = _make_company()

    with _shopify_tenant_execution(a.id):
        assert Company.objects.get(pk=a.id).id == a.id
        with pytest.raises(Company.DoesNotExist):
            Company.objects.get(pk=b.id)  # B invisible inside A's context

    # A's context cleared on the SAME connection before B runs.
    with _shopify_tenant_execution(b.id):
        assert Company.objects.get(pk=b.id).id == b.id
        with pytest.raises(Company.DoesNotExist):
            Company.objects.get(pk=a.id)  # A invisible inside B's context


def test_exception_inside_tenant_execution_still_restores_context(rls_enforced):
    from shopify_connector.tasks import _shopify_tenant_execution

    a, _ = _make_company()
    b, _ = _make_company()

    with pytest.raises(RuntimeError, match="boom"):
        with _shopify_tenant_execution(a.id):
            assert Company.objects.get(pk=a.id).id == a.id
            raise RuntimeError("boom (test-injected)")

    # Context restored despite the exception — the next task runs correctly.
    assert _guc("app.current_company_id") == ""
    with _shopify_tenant_execution(b.id):
        assert Company.objects.get(pk=b.id).id == b.id


def test_reassert_recovers_after_emitter_clears_context(rls_enforced):
    """The #119 regression: emit_event_no_actor clears the connection RLS session
    in its finally, so the NEXT admission lock would be hidden by RLS. Each
    scheduled unit of work re-asserts before it locks — proven here end to end."""
    from accounts.pilot_policy import lock_company_for_admission
    from shopify_connector.tasks import _reassert_shopify_rls, _shopify_tenant_execution

    co, _ = _make_company()
    cid = co.id
    with _shopify_tenant_execution(cid):
        assert Company.objects.get(pk=cid).id == cid
        # Simulate the emitter's finally wiping the connection's RLS session.
        rls.clear_rls_context(conn=connection)
        with pytest.raises(Company.DoesNotExist):
            Company.objects.get(pk=cid)  # unmitigated, the next lock would fail
        # Re-assert (exactly what each scheduled unit of work does) → recovered.
        _reassert_shopify_rls()
        assert _guc("app.rls_bypass") == "off"
        assert _guc("app.current_company_id") == str(cid)
        with transaction.atomic():
            assert lock_company_for_admission(cid).id == cid


# =============================================================================
# P1 — real scheduled COMMAND execution under enforced RLS
# =============================================================================


def test_scheduled_payout_execution_locks_the_exact_company(rls_enforced, monkeypatch):
    from shopify_connector import commands as cmds
    from shopify_connector.tasks import _reassert_shopify_rls, _shopify_tenant_execution

    co, store = _make_company()
    monkeypatch.setattr(cmds, "_admin_client", lambda s: _FakeClient())
    locks: list[dict] = []
    _spy_lock(monkeypatch, locks)

    with _shopify_tenant_execution(co.id):
        _reassert_shopify_rls()
        fresh = cmds.ShopifyStore.objects.select_related("company").get(id=store.id)
        result = cmds.sync_payouts(fresh)

    assert result.success, result.error  # no Company.DoesNotExist
    assert locks, "sync_payouts must acquire the admission lock for a NONE-profile company"
    assert locks[0]["company_id"] == str(co.id)
    assert locks[0]["bypass"] != "on", "shared-tenant command body must not run under broad bypass"


def test_scheduled_order_and_refund_processing_acquire_admission(rls_enforced, monkeypatch):
    from shopify_connector import commands as cmds
    from shopify_connector.models import ShopifyOrder, ShopifyRefund
    from shopify_connector.tasks import _reassert_shopify_rls, _shopify_tenant_execution

    co, store = _make_company()
    monkeypatch.setattr(cmds, "_admin_client", lambda s: _FakeClient())
    locks: list[dict] = []
    _spy_lock(monkeypatch, locks)

    order_payload = _order_payload(92001, "120.00")
    refund_payload = {
        "id": 8892001,
        "order_id": 92001,
        "created_at": "2026-08-02T10:00:00Z",
        "note": "test refund",
        "transactions": [{"kind": "refund", "status": "success", "amount": "20.00"}],
        "refund_line_items": [],
    }

    with _shopify_tenant_execution(co.id):
        _reassert_shopify_rls()
        fresh = cmds.ShopifyStore.objects.select_related("company").get(id=store.id)
        order_result = cmds.process_order_paid(fresh, order_payload)
        _reassert_shopify_rls()
        refund_result = cmds.process_refund(fresh, refund_payload)

    assert order_result.success, order_result.error
    assert refund_result.success, refund_result.error
    with rls.rls_bypass():
        assert ShopifyOrder.objects.filter(company=co, shopify_order_id=92001).exists()
        assert ShopifyRefund.objects.filter(company=co, shopify_refund_id=8892001).exists()
    # Both admission locks saw the exact company with bypass off.
    assert len(locks) >= 2
    assert all(rec["company_id"] == str(co.id) and rec["bypass"] != "on" for rec in locks)


def test_scheduled_product_processing_runs_under_tenant_context(rls_enforced, monkeypatch):
    from shopify_connector import commands as cmds
    from shopify_connector.tasks import _reassert_shopify_rls, _shopify_tenant_execution

    co, store = _make_company()
    monkeypatch.setattr(cmds, "_admin_client", lambda s: _FakeClient())

    with _shopify_tenant_execution(co.id):
        _reassert_shopify_rls()
        fresh = cmds.ShopifyStore.objects.select_related("company").get(id=store.id)
        result = cmds.sync_products(fresh)  # touches Account (RLS) via default-account resolution

    assert result.success, result.error  # no Company.DoesNotExist under enforced RLS


def test_scheduled_orders_task_processes_multiple_orders_without_doesnotexist(rls_enforced, monkeypatch):
    """The gold integration proof: the REAL sync_shopify_store_orders task →
    _shopify_tenant_execution → _sync_orders → process_order_paid for TWO orders.
    Order 2's admission lock is acquired AFTER order 1 emitted (and the emitter
    cleared context); without the per-order re-assert it would raise
    Company.DoesNotExist. Both must book."""
    from shopify_connector import commands as cmds
    from shopify_connector.models import ShopifyOrder
    from shopify_connector.tasks import sync_shopify_store_orders

    co, store = _make_company()
    fake = _FakeClient(orders=[_order_payload(93001, "100.00"), _order_payload(93002, "150.00")])
    monkeypatch.setattr(cmds, "_admin_client", lambda s: fake)
    locks: list[dict] = []
    _spy_lock(monkeypatch, locks)

    result = sync_shopify_store_orders(store.id)

    assert result["status"] == "ok", result
    assert result["created"] == 2, result
    with rls.rls_bypass():
        assert ShopifyOrder.objects.filter(company=co, shopify_order_id__in=[93001, 93002]).count() == 2
    # Each per-order admission lock saw the exact company with bypass OFF — proving
    # the emitter-clear regression is closed across the chained loop.
    assert len(locks) >= 2
    assert all(rec["company_id"] == str(co.id) and rec["bypass"] != "on" for rec in locks)


def test_full_sync_store_survives_chained_emits_across_every_subcommand(rls_enforced, monkeypatch):
    """The whole _sync_store path (orders → payouts → products → refunds →
    deferred-COGS) under the REAL initial_store_sync task, RLS enforced. Orders
    emit first and the emitter clears the connection context; payouts/products
    then take fresh admission locks that would raise Company.DoesNotExist without
    the per-subcommand re-assert. Proves each re-assert on the primary scheduled
    entrypoint is load-bearing (the case no other test exercises)."""
    from shopify_connector import commands as cmds
    from shopify_connector.models import ShopifyOrder
    from shopify_connector.tasks import initial_store_sync

    co, store = _make_company()
    fake = _FakeClient(orders=[_order_payload(96001, "100.00"), _order_payload(96002, "150.00")])
    monkeypatch.setattr(cmds, "_admin_client", lambda s: fake)
    locks: list[dict] = []
    _spy_lock(monkeypatch, locks)

    result = initial_store_sync(store.id)

    assert result["status"] == "ok", result
    # Orders booked (proves _sync_orders ran under context inside _sync_store).
    assert result["orders"]["created"] == 2, result["orders"]
    # Payouts + products ran AFTER the orders' emits with no DoesNotExist — the
    # re-assert before each is load-bearing (the payout path locks at 1913).
    assert result["payouts"].get("status") != "error", result["payouts"]
    assert result["products"].get("status") != "error", result["products"]
    assert result["refunds"].get("status") != "error", result["refunds"]
    with rls.rls_bypass():
        assert ShopifyOrder.objects.filter(company=co, shopify_order_id__in=[96001, 96002]).count() == 2
    # Orders (×2) + the payout admission lock all saw the exact company, bypass off.
    assert len(locks) >= 3, locks
    assert all(rec["company_id"] == str(co.id) and rec["bypass"] != "on" for rec in locks)


def test_non_writable_tenant_is_skipped_by_scheduled_sync(rls_enforced, monkeypatch):
    """A frozen tenant (MIGRATING / READ_ONLY / SUSPENDED → is_writable False) must
    be SKIPPED by the scheduled writer — no Shopify calls, no order/event writes —
    mirroring TenantRlsMiddleware CASE 3 (which 503s writes to such tenants) and
    the projections per-company task guard. Otherwise the sync writes past a
    migration snapshot and split-brains at cutover."""
    from shopify_connector import commands as cmds
    from shopify_connector.models import ShopifyOrder
    from shopify_connector.tasks import initial_store_sync
    from tenant.models import TenantDirectory

    co, store = _make_company()
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

    result = initial_store_sync(store.id)

    assert result["status"] == "skipped", result
    assert "writable" in result["reason"], result
    assert admin_calls["n"] == 0, "a frozen tenant must trigger no Shopify client construction / calls"
    with rls.rls_bypass():
        assert not ShopifyOrder.objects.filter(company=co).exists(), "no orders may be written for a frozen tenant"


def test_p2_out_of_scope_non_egp_order_makes_no_remote_reads(rls_enforced, monkeypatch):
    """P2 currency HINT: a USD order for an EGP pilot is out of scope and skipped;
    the pre-lock preparation must make ZERO Shopify metadata reads (restoring the
    pre-split behavior — #119 skipped non-EGP orders before any remote read)."""
    from shopify_connector import commands as cmds
    from shopify_connector.tasks import _reassert_shopify_rls, _shopify_tenant_execution

    co, store = _make_company(currency="EGP", pilot=True)
    seq = {"fetch": 0, "cur": 0}
    real_fetch = cmds._fetch_variant_cost
    real_cur = cmds._get_shopify_store_currency
    monkeypatch.setattr(cmds, "_admin_client", lambda s: _FakeClient(currency="USD"))
    monkeypatch.setattr(
        cmds,
        "_fetch_variant_cost",
        lambda *a, **k: (seq.__setitem__("fetch", seq["fetch"] + 1), real_fetch(*a, **k))[1],
    )
    monkeypatch.setattr(
        cmds,
        "_get_shopify_store_currency",
        lambda *a, **k: (seq.__setitem__("cur", seq["cur"] + 1), real_cur(*a, **k))[1],
    )

    with _shopify_tenant_execution(co.id):
        _reassert_shopify_rls()
        fresh = cmds.ShopifyStore.objects.select_related("company").get(id=store.id)
        result = cmds.process_order_paid(fresh, _unknown_sku_order(96601, "USD-SKU", 818, currency="USD"))

    assert result.success, result.error  # structured pilot-scope skip is success
    assert seq["fetch"] == 0 and seq["cur"] == 0, f"out-of-scope order must make no remote reads, got {seq}"


# =============================================================================
# P2 — remote prepare BEFORE lock / locked apply is network-free
# =============================================================================


def _unknown_sku_order(oid, sku, variant_id, currency="USD", price="40.00"):
    return _order_payload(
        oid,
        price,
        currency=currency,
        line_items=[{"sku": sku, "title": f"Item {sku}", "price": price, "quantity": 1, "variant_id": variant_id}],
    )


def test_p2_remote_reads_happen_before_admission_lock(rls_enforced, monkeypatch):
    """Remote item-metadata reads must occur in Phase 1, before the admission
    lock — never while it is held."""
    from shopify_connector import commands as cmds
    from shopify_connector.tasks import _reassert_shopify_rls, _shopify_tenant_execution

    co, store = _make_company()
    monkeypatch.setattr(cmds, "_admin_client", lambda s: _FakeClient())

    seq: list[str] = []
    real_fetch = cmds._fetch_variant_cost
    real_cur = cmds._get_shopify_store_currency
    import accounts.pilot_policy as pp

    real_lock = pp.lock_company_for_admission

    monkeypatch.setattr(cmds, "_fetch_variant_cost", lambda *a, **k: (seq.append("fetch_cost"), real_fetch(*a, **k))[1])
    monkeypatch.setattr(
        cmds, "_get_shopify_store_currency", lambda *a, **k: (seq.append("fetch_cur"), real_cur(*a, **k))[1]
    )
    monkeypatch.setattr(pp, "lock_company_for_admission", lambda pk: (seq.append("lock"), real_lock(pk))[1])

    with _shopify_tenant_execution(co.id):
        _reassert_shopify_rls()
        fresh = cmds.ShopifyStore.objects.select_related("company").get(id=store.id)
        result = cmds.process_order_paid(fresh, _unknown_sku_order(94001, "P2-SKU-1", 811))

    assert result.success, result.error
    assert "lock" in seq and "fetch_cost" in seq
    first_lock = seq.index("lock")
    # Every remote metadata read is strictly before the FIRST admission lock.
    assert all(i < first_lock for i, name in enumerate(seq) if name in ("fetch_cost", "fetch_cur")), seq


def test_p2_locked_apply_makes_no_remote_call(rls_enforced, monkeypatch):
    """After admission ownership is acquired, no remote metadata helper may run."""
    from shopify_connector import commands as cmds
    from shopify_connector.tasks import _reassert_shopify_rls, _shopify_tenant_execution

    co, store = _make_company()
    monkeypatch.setattr(cmds, "_admin_client", lambda s: _FakeClient())

    import accounts.pilot_policy as pp

    lock_held = {"v": False}
    real_lock = pp.lock_company_for_admission

    def lock_spy(pk):
        row = real_lock(pk)
        lock_held["v"] = True
        return row

    def forbid_if_locked(name, real):
        def wrapper(*a, **k):
            assert not lock_held["v"], f"{name} was called while the admission lock was held"
            return real(*a, **k)

        return wrapper

    monkeypatch.setattr(pp, "lock_company_for_admission", lock_spy)
    monkeypatch.setattr(cmds, "_fetch_variant_cost", forbid_if_locked("_fetch_variant_cost", cmds._fetch_variant_cost))
    monkeypatch.setattr(
        cmds,
        "_get_shopify_store_currency",
        forbid_if_locked("_get_shopify_store_currency", cmds._get_shopify_store_currency),
    )

    with _shopify_tenant_execution(co.id):
        _reassert_shopify_rls()
        fresh = cmds.ShopifyStore.objects.select_related("company").get(id=store.id)
        result = cmds.process_order_paid(fresh, _unknown_sku_order(94101, "P2-SKU-2", 812))

    assert result.success, result.error
    assert lock_held["v"], "the order must have acquired the admission lock"


def test_p2_egp_unknown_sku_creates_non_stock_item(rls_enforced, monkeypatch):
    from sales.models import Item
    from shopify_connector import commands as cmds
    from shopify_connector.tasks import _reassert_shopify_rls, _shopify_tenant_execution

    co, store = _make_company(currency="EGP", pilot=True)
    monkeypatch.setattr(cmds, "_admin_client", lambda s: _FakeClient(currency="EGP"))

    with _shopify_tenant_execution(co.id):
        _reassert_shopify_rls()
        fresh = cmds.ShopifyStore.objects.select_related("company").get(id=store.id)
        result = cmds.process_order_paid(fresh, _unknown_sku_order(94201, "EGP-SKU-1", 813, currency="EGP"))

    assert result.success, result.error
    with rls.rls_bypass():
        item = Item.objects.get(company=co, code="EGP-SKU-1")
        assert item.item_type == Item.ItemType.NON_STOCK
        assert item.inventory_account_id is None and item.cogs_account_id is None


def test_p2_existing_item_skips_remote_fetch(rls_enforced, monkeypatch):
    from projections.write_barrier import command_writes_allowed
    from sales.models import Item
    from shopify_connector import commands as cmds
    from shopify_connector.tasks import _reassert_shopify_rls, _shopify_tenant_execution

    co, store = _make_company()
    with rls.rls_bypass(), command_writes_allowed():
        Item.objects.create(company=co, code="KNOWN-SKU", name="Known", item_type=Item.ItemType.NON_STOCK)

    called = {"fetch": 0}
    real_fetch = cmds._fetch_variant_cost
    monkeypatch.setattr(cmds, "_admin_client", lambda s: _FakeClient())
    monkeypatch.setattr(
        cmds,
        "_fetch_variant_cost",
        lambda *a, **k: (called.__setitem__("fetch", called["fetch"] + 1), real_fetch(*a, **k))[1],
    )

    with _shopify_tenant_execution(co.id):
        _reassert_shopify_rls()
        fresh = cmds.ShopifyStore.objects.select_related("company").get(id=store.id)
        result = cmds.process_order_paid(fresh, _unknown_sku_order(94301, "KNOWN-SKU", 814))

    assert result.success, result.error
    assert called["fetch"] == 0, "a known SKU must not trigger a remote variant-cost fetch"


def test_p2_prepare_apply_race_reuses_existing_item(rls_enforced, monkeypatch):
    """Item created concurrently between Phase-1 prepare and Phase-2 apply → the
    locked apply reuses it; no duplicate, no uniqueness failure."""
    from projections.write_barrier import command_writes_allowed
    from sales.models import Item
    from shopify_connector import commands as cmds
    from shopify_connector.tasks import _reassert_shopify_rls, _shopify_tenant_execution

    co, store = _make_company()
    monkeypatch.setattr(cmds, "_admin_client", lambda s: _FakeClient())

    real_prepare = cmds._prepare_order_item_metadata

    def prepare_then_race(store_arg, payload):
        prepared = real_prepare(store_arg, payload)
        # Simulate a concurrent transaction creating the SAME item AFTER prep,
        # BEFORE the locked apply.
        with rls.rls_bypass(), command_writes_allowed():
            Item.objects.get_or_create(
                company=co, code="RACE-SKU", defaults={"name": "Race", "item_type": Item.ItemType.NON_STOCK}
            )
        return prepared

    monkeypatch.setattr(cmds, "_prepare_order_item_metadata", prepare_then_race)

    with _shopify_tenant_execution(co.id):
        _reassert_shopify_rls()
        fresh = cmds.ShopifyStore.objects.select_related("company").get(id=store.id)
        result = cmds.process_order_paid(fresh, _unknown_sku_order(94401, "RACE-SKU", 815))

    assert result.success, result.error
    with rls.rls_bypass():
        assert Item.objects.filter(company=co, code="RACE-SKU").count() == 1, "locked apply must not create a duplicate"


def test_p2_remote_error_preserves_fallback_no_partial_mutation(rls_enforced, monkeypatch):
    """A remote cost fetch that errors preserves the documented fallback (cost 0)
    and never leaves a partial local mutation before admission."""
    from sales.models import Item
    from shopify_connector import commands as cmds
    from shopify_connector.tasks import _reassert_shopify_rls, _shopify_tenant_execution

    co, store = _make_company()

    class _BoomCostClient(_FakeClient):
        def get_variant_unit_cost(self, variant_id):
            raise RuntimeError("shopify variant cost unavailable")

    monkeypatch.setattr(cmds, "_admin_client", lambda s: _BoomCostClient())

    with _shopify_tenant_execution(co.id):
        _reassert_shopify_rls()
        fresh = cmds.ShopifyStore.objects.select_related("company").get(id=store.id)
        result = cmds.process_order_paid(fresh, _unknown_sku_order(94501, "ERR-SKU", 816))

    assert result.success, result.error
    with rls.rls_bypass():
        item = Item.objects.get(company=co, code="ERR-SKU")
        assert item.default_cost == Decimal("0")  # documented fallback, no invented default
