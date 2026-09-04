# tests/e2e/test_g1_f1_store_sweep_serialization.py
"""G1-F1 — PostgreSQL two-connection proof that the stale-PENDING disposition
(``retire_or_delete_stale_pending_stores``) can never delete or downgrade a
store whose OAuth completion is IN FLIGHT.

The SQLite suite (``tests/test_g1_f1_store_sweep_history_guard.py``) proves the
committed-before-selection case only. This suite proves the lock-held
orderings, which only PostgreSQL can exhibit:

- **OAuth first.** The REAL ``complete_oauth`` holds the ShopifyStore row lock
  (its ACTIVE ``save()`` inside ``@transaction.atomic``) and is paused INSIDE
  that transaction; the sweep then runs against the stale PENDING candidate
  and must observably wait on the row lock. After OAuth commits, the sweep's
  ``SELECT ... FOR UPDATE`` re-read filtered on ``status=PENDING`` returns
  nothing: result all-zero, row ACTIVE with its fresh token.

- **Sweep first.** The REAL sweep holds the row lock (its ``select_for_update``
  re-read) and is paused inside ``store_has_canonical_history``; OAuth then
  runs and must observably wait. After the sweep commits — delete (no
  history) — OAuth's ``save()`` UPDATE affects zero rows and Django re-inserts
  the row as ACTIVE on the same pk (benign: the store still ends connected);
  with history the row is retained DISCONNECTED and OAuth's save then wins
  the same row (ACTIVE, tokens set), mirrors intact either way.

Test mechanics mirror ``tests/e2e/test_a4_runtime_admission_serialization.py``:
pause points sit inside the production transaction after the lock is held; a
test never manufactures the lock.
"""

import threading
import time
from datetime import date
from unittest import mock

import pytest
from django.db import connection, connections
from django.utils import timezone

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="row-lock serialization is only provable on PostgreSQL",
    ),
]

WATCHDOG = 30
DOMAIN = "f1-race.myshopify.com"


def _wait(predicate, why: str, timeout: float = WATCHDOG):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(f"WATCHDOG: gave up waiting for: {why}")


def _someone_is_lock_waiting() -> bool:
    with connections["default"].cursor() as cur:
        cur.execute(
            """
            SELECT count(*)
            FROM pg_locks l
            JOIN pg_stat_activity a ON a.pid = l.pid
            WHERE NOT l.granted AND a.datname = current_database()
            """
        )
        return cur.fetchone()[0] > 0


class _Worker:
    def __init__(self, fn, name: str):
        self.result = None
        self.error = None
        self.name = name

        def target():
            try:
                self.result = fn()
            except BaseException as exc:
                self.error = exc
            finally:
                connections.close_all()

        self.thread = threading.Thread(target=target, name=name, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def join(self, timeout: float = WATCHDOG):
        self.thread.join(timeout)
        if self.thread.is_alive():
            pytest.fail(f"WATCHDOG: worker {self.name!r} did not finish within {timeout}s")
        return self


@pytest.fixture(autouse=True)
def _oauth_side_effects_stubbed(monkeypatch):
    """The token exchange and the post-connect setup need real infrastructure —
    not this suite's subject. ``_ensure_shopify_warehouse`` is left to the
    per-test pause hook (it is the first call after the ACTIVE save, inside
    ``complete_oauth``'s transaction)."""
    from shopify_connector import commands

    fake_resp = mock.Mock()
    fake_resp.json.return_value = {"access_token": "fresh-token", "scope": "read_orders"}
    fake_resp.raise_for_status.return_value = None
    monkeypatch.setattr(commands.requests, "post", lambda *a, **kw: fake_resp)
    monkeypatch.setattr(commands, "_ensure_shopify_sales_setup", lambda s: None)
    monkeypatch.setattr(commands, "_schedule_initial_sync", lambda s: None)


def _seed_pending(company, *, with_history: bool):
    from projections.write_barrier import command_writes_allowed
    from shopify_connector.models import ShopifyOrder, ShopifyStore

    with command_writes_allowed():
        store = ShopifyStore.objects.create(
            company=company,
            shop_domain=DOMAIN,
            access_token="",
            oauth_nonce="race-nonce",
            status=ShopifyStore.Status.PENDING,
        )
        if with_history:
            ShopifyOrder.objects.create(
                company=company,
                store=store,
                shopify_order_id=4242,
                shopify_order_number="4242",
                shopify_order_name="#4242",
                total_price="100.00",
                subtotal_price="100.00",
                total_tax="0.00",
                currency="EGP",
                financial_status="paid",
                shopify_created_at=timezone.now(),
                order_date=date(2026, 5, 1),
            )
    return store


def _run_oauth(company):
    from shopify_connector.commands import complete_oauth

    return complete_oauth(company, DOMAIN, "code", "race-nonce")


def _run_sweep(candidate):
    from projections.write_barrier import command_writes_allowed
    from shopify_connector.commands import retire_or_delete_stale_pending_stores

    with command_writes_allowed():
        return retire_or_delete_stale_pending_stores([candidate])


def _pause_after_original(monkeypatch, module, attr, held: threading.Event, release: threading.Event):
    """Run the ORIGINAL, then pause once (signal ``held``, wait ``release``) —
    inside the production transaction while the production-acquired row lock
    is held."""
    original = getattr(module, attr)
    fired = {"n": 0}

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        fired["n"] += 1
        if fired["n"] == 1:
            held.set()
            _wait(release.is_set, f"release of paused {attr}")
        return result

    monkeypatch.setattr(module, attr, wrapper)


@pytest.mark.parametrize("with_history", [False, True], ids=["no-history", "with-history"])
def test_oauth_first_sweep_waits_then_leaves_the_active_row_alone(company, monkeypatch, with_history):
    from shopify_connector import commands
    from shopify_connector.models import ShopifyOrder, ShopifyStore

    store = _seed_pending(company, with_history=with_history)
    stale_candidate = ShopifyStore.objects.get(pk=store.pk)  # the sweep's earlier selection

    held, release = threading.Event(), threading.Event()
    # _ensure_shopify_warehouse is the first call after the ACTIVE save inside
    # complete_oauth's @transaction.atomic — stub the real setup, then pause
    # there while the production-acquired row lock is held.
    monkeypatch.setattr(commands, "_ensure_shopify_warehouse", lambda s: None)
    _pause_after_original(monkeypatch, commands, "_ensure_shopify_warehouse", held, release)

    oauth = _Worker(lambda: _run_oauth(company), "oauth").start()
    _wait(held.is_set, "complete_oauth holds the row lock after its ACTIVE save")

    sweep = _Worker(lambda: _run_sweep(stale_candidate), "sweep").start()
    _wait(_someone_is_lock_waiting, "sweep observably waiting on the row lock held by complete_oauth")

    release.set()
    oauth.join()
    sweep.join()

    assert oauth.error is None, f"oauth worker crashed: {oauth.error}"
    assert oauth.result.success, oauth.result.error
    assert sweep.error is None, f"sweep worker crashed: {sweep.error}"
    assert sweep.result == {"deleted": 0, "retained": 0, "deleted_domains": [], "retained_domains": []}

    store.refresh_from_db()
    assert store.status == ShopifyStore.Status.ACTIVE
    assert store.access_token == "fresh-token"
    assert store.oauth_nonce == ""
    assert ShopifyOrder.objects.filter(store=store).count() == (1 if with_history else 0)


@pytest.mark.parametrize("with_history", [False, True], ids=["no-history", "with-history"])
def test_sweep_first_oauth_waits_then_the_store_still_ends_connected(company, monkeypatch, with_history):
    from shopify_connector import commands
    from shopify_connector.models import ShopifyOrder, ShopifyStore

    store = _seed_pending(company, with_history=with_history)
    stale_candidate = ShopifyStore.objects.get(pk=store.pk)
    monkeypatch.setattr(commands, "_ensure_shopify_warehouse", lambda s: None)

    held, release = threading.Event(), threading.Event()
    # store_has_canonical_history runs on the locked re-read, inside the
    # sweep's per-row atomic — the pause holds the row lock.
    _pause_after_original(monkeypatch, commands, "store_has_canonical_history", held, release)

    sweep = _Worker(lambda: _run_sweep(stale_candidate), "sweep").start()
    _wait(held.is_set, "sweep holds the row lock after its select_for_update re-read")

    oauth = _Worker(lambda: _run_oauth(company), "oauth").start()
    _wait(_someone_is_lock_waiting, "complete_oauth observably waiting on the row lock held by the sweep")

    release.set()
    sweep.join()
    oauth.join()

    assert sweep.error is None, f"sweep worker crashed: {sweep.error}"
    assert oauth.error is None, f"oauth worker crashed: {oauth.error}"
    if with_history:
        assert sweep.result["retained"] == 1 and sweep.result["deleted"] == 0
    else:
        assert sweep.result["deleted"] == 1 and sweep.result["retained"] == 0

    # Either way the merchant's connection completes: the store ends ACTIVE
    # with the fresh token — on the same pk (Django re-inserts on a zero-row
    # UPDATE after the delete; the retained row is simply updated).
    assert oauth.result.success, oauth.result.error
    live = ShopifyStore.objects.get(company=company, shop_domain=DOMAIN)
    assert live.pk == store.pk
    assert live.status == ShopifyStore.Status.ACTIVE
    assert live.access_token == "fresh-token"
    assert ShopifyOrder.objects.filter(store=live).count() == (1 if with_history else 0)
