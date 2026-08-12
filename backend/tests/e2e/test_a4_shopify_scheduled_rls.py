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
    pytest.mark.django_db(transaction=True, databases=["default", "tenant_rls_test"]),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="scheduled-task tenant/RLS execution is only provable on PostgreSQL",
    ),
]

ISO = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1

# The DEDICATED-tenant DATA plane: a distinct Django/PostgreSQL session (a MIRROR
# of default → same physical test DB) configured in test_settings. The dedicated
# proofs route through this alias, never a substituted "default".
TENANT_DATA_ALIAS = "tenant_rls_test"

# Ephemeral NOLOGIN / NOSUPERUSER / NOBYPASSRLS role. Forced RLS does not apply to
# a superuser or a BYPASSRLS role, so when the CI/bootstrap role bypasses RLS
# (the postgres image's superuser) we SET ROLE to this one to make the RLS proof
# real. A role that already enforces RLS (e.g. a local NOBYPASSRLS user) is used
# as-is.
EPHEMERAL_RLS_ROLE = "nxentra_rls_e2e_role"


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _conn(alias: str = "default"):
    from django.db import connections

    return connections[alias]


def _guc(name: str, alias: str = "default") -> str:
    with _conn(alias).cursor() as cur:
        cur.execute("SELECT current_setting(%s, true)", [name])
        return cur.fetchone()[0]


def _role_evidence(conn) -> dict:
    """RAW effective-role attributes + accounts_company RLS flags for a connection.
    The RLS proof is only real if the effective role does NOT bypass RLS and the
    table is FORCE ROW LEVEL SECURITY."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT current_user, session_user,"
            " (SELECT rolsuper FROM pg_roles WHERE rolname = current_user),"
            " (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)"
        )
        current_user, session_user, rolsuper, rolbypassrls = cur.fetchone()
        cur.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'accounts_company'")
        relrowsecurity, relforcerowsecurity = cur.fetchone()
    return {
        "current_user": current_user,
        "session_user": session_user,
        "rolsuper": rolsuper,
        "rolbypassrls": rolbypassrls,
        "relrowsecurity": relrowsecurity,
        "relforcerowsecurity": relforcerowsecurity,
    }


def _make_role_enforce_rls(conn) -> bool:
    """If the effective role on ``conn`` bypasses RLS (superuser or BYPASSRLS),
    create the ephemeral NOBYPASSRLS role, grant it this isolated suite's
    privileges, and ``SET ROLE`` to it. Returns True if ``SET ROLE`` was applied
    (caller must ``RESET ROLE`` in finally). Idempotent."""
    ev = _role_evidence(conn)
    if not (ev["rolsuper"] or ev["rolbypassrls"]):
        return False
    with conn.cursor() as cur:
        cur.execute(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{EPHEMERAL_RLS_ROLE}') "
            f"THEN CREATE ROLE {EPHEMERAL_RLS_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$;"
        )
        cur.execute(f"GRANT USAGE ON SCHEMA public TO {EPHEMERAL_RLS_ROLE}")
        cur.execute(f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {EPHEMERAL_RLS_ROLE}")
        cur.execute(f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {EPHEMERAL_RLS_ROLE}")
        cur.execute(f"SET ROLE {EPHEMERAL_RLS_ROLE}")
    return True


@pytest.fixture
def rls_enforced(db, settings):
    """Reproduce a production Celery worker with RLS GENUINELY enforced on BOTH
    the control (default) and data (tenant_rls_test) planes.

    ``settings.RLS_BYPASS`` off, and — because forced RLS is inert for a
    superuser/BYPASSRLS bootstrap role (exactly the CI postgres image) — SET ROLE
    to an ephemeral NOBYPASSRLS role on every plane connection when needed. The
    fixture then SELF-VALIDATES: if the effective role still bypasses RLS, or the
    table is not FORCE RLS, it fails LOUDLY rather than run vacuous assertions.
    Finally, the default plane is left at the raw production worker start:
    ``app.rls_bypass`` off, no ``app.current_company_id``. Yields the role
    evidence."""
    settings.RLS_BYPASS = False

    from django.db import connections

    aliases = ["default"] + ([TENANT_DATA_ALIAS] if TENANT_DATA_ALIAS in connections.databases else [])
    conns = {a: connections[a] for a in aliases}
    role_applied = {a: _make_role_enforce_rls(conns[a]) for a in aliases}

    # Self-validating guard: the proof is only real under enforced RLS.
    ev = _role_evidence(conns["default"])
    assert ev["relrowsecurity"] and ev["relforcerowsecurity"], (
        f"accounts_company must be ROW LEVEL SECURITY + FORCE for a real RLS proof; got {ev}"
    )
    assert not ev["rolsuper"] and not ev["rolbypassrls"], (
        f"effective role must NOT bypass RLS for a real RLS proof; got {ev}. "
        "SET ROLE to a NOBYPASSRLS role failed — the assertions would be vacuous."
    )

    # Raw production worker start on EVERY plane: bypass off, no company_id — so
    # restore-to-prior assertions are deterministic across control and data planes.
    for a in aliases:
        rls.set_rls_bypass(False, conn=conns[a])
        rls.set_current_company_id(None, conn=conns[a])

    yield ev

    # Teardown: RESET ROLE and restore the shared bypass on every plane.
    for a in aliases:
        c = conns[a]
        if c.vendor == "postgresql":
            if role_applied[a]:
                with c.cursor() as cur:
                    cur.execute("RESET ROLE")
            rls.set_rls_bypass(True, conn=c)
            rls.set_current_company_id(None, conn=c)


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


def _mock_dedicated_routing(monkeypatch, alias=TENANT_DATA_ALIAS, writable=True):
    """Route the tenant as a REAL dedicated-DB tenant to the distinct data-plane
    alias (never a substituted 'default')."""
    from tenant.models import TenantDirectory

    monkeypatch.setattr(
        TenantDirectory,
        "get_tenant_info",
        classmethod(
            lambda cls, company_id: {
                "db_alias": alias,
                "is_shared": False,
                "status": "ACTIVE",
                "is_writable": writable,
            }
        ),
    )


def test_role_evidence_proves_rls_is_really_enforced(rls_enforced):
    """The suite's RLS proof is only meaningful under a non-BYPASSRLS role against
    a FORCE-RLS table. Record and assert the raw evidence; the fixture already
    fails loudly otherwise, this pins it as an explicit, visible test."""
    ev = rls_enforced  # the fixture yields the role evidence
    assert ev["relrowsecurity"] is True and ev["relforcerowsecurity"] is True, ev
    assert ev["rolsuper"] is False, f"effective role is a superuser — RLS would be bypassed: {ev}"
    assert ev["rolbypassrls"] is False, f"effective role has BYPASSRLS — RLS would be bypassed: {ev}"


def test_dedicated_tenant_execution_two_plane(rls_enforced, monkeypatch):
    """A REAL dedicated-DB tenant. ``accounts.Company`` and the ``shopify_connector``
    models route to the CONTROL plane (``default``) and must be company-scoped
    there with bypass OFF; the tenant DATA plane (``tenant_rls_test``, a distinct
    session) runs company-scoped with bypass ON. Uses a genuinely distinct alias —
    never a substituted ``default`` — so the previously-masked defect is exposed."""
    from django.db import router

    from accounts.pilot_policy import lock_company_for_admission
    from events.models import BusinessEvent
    from shopify_connector.models import ShopifyStore
    from shopify_connector.tasks import _shopify_tenant_execution

    a, store_a = _make_company()
    b, _ = _make_company()
    _mock_dedicated_routing(monkeypatch)

    # Precondition: unscoped Company hidden on the control plane.
    with pytest.raises(Company.DoesNotExist):
        Company.objects.get(pk=a.id)

    with _shopify_tenant_execution(a.id):
        # CONTROL plane (default): company-scoped, bypass OFF (never broad bypass).
        assert _guc("app.current_company_id", "default") == str(a.id)
        assert _guc("app.rls_bypass", "default") == "off", "control plane must NOT bypass RLS for a dedicated tenant"
        assert Company.objects.get(pk=a.id).id == a.id  # Company visible on default
        assert ShopifyStore.objects.select_related("company").get(id=store_a.id).id == store_a.id  # store visible
        with pytest.raises(Company.DoesNotExist):
            Company.objects.get(pk=b.id)  # another company hidden on default
        with transaction.atomic():
            assert lock_company_for_admission(a.id).id == a.id  # admission lock occurs on default

        # DATA plane (tenant_rls_test): company-scoped, bypass ON.
        assert _guc("app.current_company_id", TENANT_DATA_ALIAS) == str(a.id)
        assert _guc("app.rls_bypass", TENANT_DATA_ALIAS) == "on"
        # A tenant-routed model resolves to the DATA plane alias, not default.
        assert router.db_for_read(BusinessEvent) == TENANT_DATA_ALIAS

    # Both planes restored to the no-context worker baseline on normal return.
    with pytest.raises(Company.DoesNotExist):
        Company.objects.get(pk=a.id)
    assert _guc("app.rls_bypass", "default") == "off"
    assert _guc("app.current_company_id", "default") == ""
    assert _guc("app.current_company_id", TENANT_DATA_ALIAS) == ""
    assert _guc("app.rls_bypass", TENANT_DATA_ALIAS) == "off"


def test_dedicated_context_restores_both_planes_after_exception(rls_enforced, monkeypatch):
    from shopify_connector.tasks import _shopify_tenant_execution

    a, _ = _make_company()
    _mock_dedicated_routing(monkeypatch)

    with pytest.raises(RuntimeError, match="boom"):
        with _shopify_tenant_execution(a.id):
            assert _guc("app.current_company_id", TENANT_DATA_ALIAS) == str(a.id)
            raise RuntimeError("boom (dedicated)")

    # Exact raw restoration on BOTH planes despite the exception.
    for alias in ("default", TENANT_DATA_ALIAS):
        assert _guc("app.current_company_id", alias) == ""
        assert _guc("app.rls_bypass", alias) == "off"


def test_reassert_restores_both_planes_after_emitter_clear_dedicated(rls_enforced, monkeypatch):
    """Dedicated tenant: an emit clears the DATA plane it wrote on; the re-assert
    must restore BOTH the control and data planes before the next unit."""
    from accounts.pilot_policy import lock_company_for_admission
    from shopify_connector.tasks import _reassert_shopify_rls, _shopify_tenant_execution

    a, _ = _make_company()
    _mock_dedicated_routing(monkeypatch)

    with _shopify_tenant_execution(a.id):
        # Simulate emit_event_no_actor clearing BOTH connections' RLS session.
        rls.clear_rls_context(conn=_conn("default"))
        rls.clear_rls_context(conn=_conn(TENANT_DATA_ALIAS))
        with pytest.raises(Company.DoesNotExist):
            Company.objects.get(pk=a.id)  # control plane wiped -> lock would fail
        _reassert_shopify_rls()
        assert _guc("app.rls_bypass", "default") == "off"
        assert _guc("app.current_company_id", "default") == str(a.id)
        assert _guc("app.rls_bypass", TENANT_DATA_ALIAS) == "on"
        assert _guc("app.current_company_id", TENANT_DATA_ALIAS) == str(a.id)
        with transaction.atomic():
            assert lock_company_for_admission(a.id).id == a.id


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


def test_scheduled_entrypoints_reach_control_plane_admission_for_dedicated_routing(rls_enforced, monkeypatch):
    """Blocker-1 proof at the ENTRYPOINT level. The REAL orders-only / initial /
    periodic entrypoints for a DEDICATED-DB tenant reach the ``Company`` admission
    lock on the CONTROL plane (``default``) with the exact company scoped and
    bypass OFF — i.e. no ``Company.DoesNotExist`` (the previously-masked defect).

    Scope note: full order *booking* for a dedicated tenant additionally requires
    tenant-DB event-transaction handling in ``events.emitter`` (``_emit_event_core``
    opens its atomic on ``default`` while the ``events`` writes route to the tenant
    DB) — a SEPARATE, pre-existing limitation outside this envelope; there are no
    dedicated tenants today. What Blocker 1 raised — the control-plane RLS context
    for ``Company`` / ``ShopifyStore`` — is what this proves closed (see also
    ``test_dedicated_tenant_execution_two_plane``, a direct control-plane lock)."""
    from shopify_connector import commands as cmds
    from shopify_connector.tasks import initial_store_sync, sync_shopify_all, sync_shopify_store_orders

    a, store = _make_company()
    _mock_dedicated_routing(monkeypatch)
    monkeypatch.setattr(cmds, "_admin_client", lambda s: _FakeClient(orders=[_order_payload(97001, "100.00")]))
    locks: list[dict] = []
    _spy_lock(monkeypatch, locks)

    for label, entry in (
        ("orders-only", lambda: sync_shopify_store_orders(store.id)),
        ("initial", lambda: initial_store_sync(store.id)),
        ("periodic", lambda: sync_shopify_all()),
    ):
        locks.clear()
        result = entry()
        assert result is not None, label
        # The admission lock was reached on the control plane (default) — the fresh
        # Company query was visible, so NO Company.DoesNotExist — with the exact
        # company scoped and bypass OFF (never broad bypass on default).
        assert locks, f"{label}: no admission lock reached (likely Company.DoesNotExist): {result}"
        assert all(r["company_id"] == str(a.id) and r["bypass"] == "off" for r in locks), (label, locks)


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


# =============================================================================
# C — verify_payout: remote fetch BEFORE the admission lock, network-free locked
# apply, and remote-failure preservation of the existing cache. These tests do
# NOT use the rls_enforced fixture — they prove lock ordering / duration, not RLS
# visibility, so they run under the suite default (bypass on).
# =============================================================================


class _PayoutClient:
    """Minimal client double for payout-transaction fetch."""

    def __init__(self, txns=None, raise_on_list=False):
        self._txns = txns if txns is not None else []
        self._raise = raise_on_list
        self.list_calls = 0

    def list_payout_transactions(self, payout_id):
        self.list_calls += 1
        if self._raise:
            import requests

            raise requests.RequestException("simulated Shopify payout-transactions failure")
        return list(self._txns)


def _make_payout(company, store, *, net="100.00", fees="5.00", currency="EGP"):
    from datetime import date

    from projections.write_barrier import command_writes_allowed
    from shopify_connector.models import ShopifyPayout

    with rls.rls_bypass(), command_writes_allowed():
        return ShopifyPayout.objects.create(
            company=company,
            store=store,
            shopify_payout_id=int(uuid4().int % 1_000_000_000),
            gross_amount=Decimal(net) + Decimal(fees),
            fees=Decimal(fees),
            net_amount=Decimal(net),
            currency=currency,
            shopify_status="paid",
            payout_date=date.today(),
            raw_payload={},
        )


def _seed_txns(payout, company, n, *, net="0.01", fee="0.00"):
    from projections.write_barrier import command_writes_allowed
    from shopify_connector.models import ShopifyPayoutTransaction

    with rls.rls_bypass(), command_writes_allowed():
        for i in range(n):
            ShopifyPayoutTransaction.objects.create(
                company=company,
                payout=payout,
                shopify_transaction_id=1_000_000 + i,
                transaction_type=ShopifyPayoutTransaction.TransactionType.CHARGE,
                amount=Decimal(net),
                fee=Decimal(fee),
                net=Decimal(net),
                currency=payout.currency,
                source_order_id=None,
                source_type="",
                verified=False,
                local_order=None,
                processed_at=None,
                raw_data={},
            )


def _remote_txn(txn_id, *, amount="100.00", fee="5.00", net="100.00"):
    return {"id": txn_id, "amount": amount, "fee": fee, "net": net, "type": "charge", "currency": "EGP"}


def _count_txns(payout):
    from shopify_connector.models import ShopifyPayoutTransaction

    with rls.rls_bypass():
        return ShopifyPayoutTransaction.objects.filter(payout=payout).count()


def _spy_calls(monkeypatch, target, attr, recorder, label):
    """Record `label` in `recorder` on each call, then delegate to the original."""
    original = getattr(target, attr)

    def wrapper(*args, **kwargs):
        recorder.append(label)
        return original(*args, **kwargs)

    monkeypatch.setattr(target, attr, wrapper)
    return original


def test_verify_payout_remote_fetch_before_admission_lock(monkeypatch):
    """C1 / C2 / C9: the remote list_payout_transactions runs BEFORE the first
    Company admission lock and while NO transaction/lock is held (so activation
    could proceed concurrently — Phase 1 is lock-free)."""
    import accounts.pilot_policy as pp
    from shopify_connector import commands as cmds

    co, store = _make_company(currency="EGP")
    payout = _make_payout(co, store)
    client = _PayoutClient(txns=[_remote_txn(5001)])
    monkeypatch.setattr(cmds, "_admin_client", lambda s: client)

    seq: list[str] = []
    _spy_calls(monkeypatch, pp, "lock_company_for_admission", seq, "lock")
    _spy_calls(monkeypatch, client, "list_payout_transactions", seq, "fetch")

    result = cmds.verify_payout(store, payout.shopify_payout_id)

    assert result.success, result.error
    # The remote fetch is the FIRST privileged operation and the admission lock is
    # acquired strictly AFTER it — so no Company row lock is held during the fetch,
    # and a concurrent activation could acquire the Company lock meanwhile (C2).
    assert seq and seq[0] == "fetch" and "lock" in seq, seq


def test_verify_payout_locked_apply_makes_no_network_call(monkeypatch):
    """C4: after admission ownership is acquired, no provider/network call runs."""
    import accounts.pilot_policy as pp
    from shopify_connector import commands as cmds

    co, store = _make_company(currency="EGP")
    payout = _make_payout(co, store)
    client = _PayoutClient(txns=[_remote_txn(5101)])
    monkeypatch.setattr(cmds, "_admin_client", lambda s: client)

    lock_held = {"v": False}
    real_lock = pp.lock_company_for_admission

    def lock_spy(pk):
        row = real_lock(pk)
        lock_held["v"] = True
        return row

    real_list = client.list_payout_transactions

    def guarded_list(pid):
        assert not lock_held["v"], "list_payout_transactions ran while the admission lock was held"
        return real_list(pid)

    monkeypatch.setattr(pp, "lock_company_for_admission", lock_spy)
    monkeypatch.setattr(client, "list_payout_transactions", guarded_list)

    result = cmds.verify_payout(store, payout.shopify_payout_id)
    assert result.success, result.error
    assert lock_held["v"] and client.list_calls == 1


def test_verify_payout_activation_wins_writes_nothing(monkeypatch):
    """C3: if the pilot profile is active at lock time, the locked recheck
    (require_supported) blocks and no ShopifyPayoutTransaction row is written."""
    from accounts.pilot_policy import PilotScopeBlocked
    from shopify_connector import commands as cmds

    co, store = _make_company(currency="EGP", pilot=True)  # payout accounting blocked for the pilot
    payout = _make_payout(co, store)
    monkeypatch.setattr(cmds, "_admin_client", lambda s: _PayoutClient(txns=[_remote_txn(5201)]))

    with pytest.raises(PilotScopeBlocked):
        cmds.verify_payout(store, payout.shopify_payout_id)
    assert _count_txns(payout) == 0, "a blocked verify must write no transaction rows"


def test_verify_payout_remote_failure_preserves_stale_cache(monkeypatch):
    """C5 (the data-loss fix): a remote failure during stale-cache remediation
    must leave every existing cache row intact — nothing deleted."""
    from shopify_connector import commands as cmds

    co, store = _make_company(currency="EGP")
    payout = _make_payout(co, store, net="100.00", fees="5.00")
    _seed_txns(payout, co, 250, net="0.01")  # 250 discrepant rows -> stale (>=250 + mismatch)
    assert _count_txns(payout) == 250
    monkeypatch.setattr(cmds, "_admin_client", lambda s: _PayoutClient(raise_on_list=True))

    result = cmds.verify_payout(store, payout.shopify_payout_id)

    assert not result.success, "a remote failure must surface as failure"
    assert _count_txns(payout) == 250, "existing cache must be preserved on remote failure (no delete)"


def test_verify_payout_stale_replacement_is_atomic_and_balanced(monkeypatch):
    """C7: a successful stale remediation replaces the truncated cache atomically
    and recomputes verification totals from the complete payload."""
    from shopify_connector import commands as cmds

    co, store = _make_company(currency="EGP")
    payout = _make_payout(co, store, net="100.00", fees="5.00")
    _seed_txns(payout, co, 250, net="0.01")  # stale truncated cache
    monkeypatch.setattr(
        cmds,
        "_admin_client",
        lambda s: _PayoutClient(txns=[_remote_txn(5301, amount="100.00", fee="5.00", net="100.00")]),
    )

    result = cmds.verify_payout(store, payout.shopify_payout_id)

    assert result.success, result.error
    assert result.data.get("balanced") is True, result.data
    assert _count_txns(payout) == 1, "the 250 truncated rows must be replaced by the complete set"


def test_verify_payout_concurrent_cache_population_is_reused(monkeypatch):
    """C6: if another verifier populates the cache while Phase 1 fetches, the
    locked Phase 2 reuses the committed cache rather than inserting duplicates."""
    from shopify_connector import commands as cmds

    co, store = _make_company(currency="EGP")
    payout = _make_payout(co, store)

    real_remote = cmds._fetch_payout_transactions_remote

    def remote_then_populate(store_arg, payout_arg):
        out = real_remote(store_arg, payout_arg)
        # Simulate a concurrent verifier committing the cache after our fetch.
        _seed_txns(payout, co, 2, net="50.00")
        return out

    monkeypatch.setattr(cmds, "_admin_client", lambda s: _PayoutClient(txns=[_remote_txn(5401), _remote_txn(5402)]))
    monkeypatch.setattr(cmds, "_fetch_payout_transactions_remote", remote_then_populate)

    result = cmds.verify_payout(store, payout.shopify_payout_id)

    assert result.success, result.error
    assert result.data.get("source") == "cached", result.data  # reused, not re-applied
    assert _count_txns(payout) == 2, "no duplicate rows created"


def test_verify_payout_cached_non_stale_is_lock_free(monkeypatch):
    """C8: the ordinary cached, non-stale verification path takes no admission
    lock and makes no network call."""
    from shopify_connector import commands as cmds

    co, store = _make_company(currency="EGP")
    payout = _make_payout(co, store, net="100.00", fees="5.00")
    # A small BALANCED cache (net 100, fee 5): not stale, no discrepancy.
    _seed_txns(payout, co, 1, net="100.00", fee="5.00")

    client = _PayoutClient(txns=[_remote_txn(9999)])
    monkeypatch.setattr(cmds, "_admin_client", lambda s: client)
    locks: list[dict] = []
    _spy_lock(monkeypatch, locks)

    result = cmds.verify_payout(store, payout.shopify_payout_id)

    assert result.success and result.data.get("source") == "cached", result.data
    assert not locks, "cached non-stale verification must be lock-free"
    assert client.list_calls == 0, "cached non-stale verification must make no network call"
