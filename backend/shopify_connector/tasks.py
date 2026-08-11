# shopify_connector/tasks.py
"""
Celery tasks for automated Shopify data synchronization.

Tasks:
- sync_shopify_all: Periodic catch-up task that syncs missed orders, payouts,
  and products for all active Shopify stores. Runs every 4 hours to catch
  any webhooks that were missed.
- sync_shopify_store_orders: Sync orders for a specific store within a date range.
"""

import logging
from contextlib import contextmanager
from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone as tz

from accounts import rls
from accounts.rls import rls_bypass

logger = logging.getLogger(__name__)


# =============================================================================
# A4 — scheduled-task tenant / RLS execution context
# =============================================================================
# A Celery task has no request, so TenantRlsMiddleware never runs: it has no
# tenant routing and no PostgreSQL RLS session context. Cross-tenant store
# discovery legitimately runs under a short ``rls_bypass()`` (system / default
# DB), but the COMMANDS that discovery feeds must NOT execute under broad bypass
# — since PR #119 every covered Shopify command takes a fresh ``Company``
# admission lock, and under production ``RLS_BYPASS=False`` that lock's
# ``Company`` query is hidden by RLS unless ``app.current_company_id`` is set on
# the tenant connection (``Company.DoesNotExist`` otherwise). The two helpers
# below give a scheduled task the SAME tenant routing + RLS session context that
# ``accounts.middleware.TenantRlsMiddleware`` gives a request — using only the
# existing tenant/RLS primitives — without ever holding a transaction or a row
# lock across the remote Shopify sync.


def _reassert_shopify_rls() -> None:
    """Re-apply the PostgreSQL RLS session context for the CURRENT tenant.

    ``events.emitter.emit_event_no_actor`` establishes its own tenant/RLS context
    for each event write and then CLEARS the connection's RLS session settings in
    its ``finally`` (``accounts.rls.clear_rls_context``). So after the first event
    a scheduled command emits, ``app.current_company_id`` / ``app.rls_bypass`` are
    wiped, and the NEXT admission lock's fresh ``Company`` query is hidden by RLS
    (``Company.DoesNotExist``). Each admission-locking unit of work therefore
    re-asserts the session context — derived from the tenant contextvar
    established by :func:`_shopify_tenant_execution` (scheduled path) or by
    ``TenantRlsMiddleware`` (the in-request re-sync view, which chains the same
    per-order locks and has the identical post-emit exposure) — immediately before
    it acquires its lock. No-op when there is no tenant context (nothing to
    re-assert), so it is safe to call from paths that run without one.
    """
    from tenant.context import get_current_tenant

    ctx = get_current_tenant()
    if ctx is None:
        return
    conn = connections[ctx.db_alias]
    if ctx.is_shared:
        rls.set_rls_context(ctx.company_id, bypass=settings.RLS_BYPASS, conn=conn)
    else:
        # Dedicated DB: single tenant in the database, RLS bypassed — the
        # established dedicated-tenant semantics (mirrors TenantRlsMiddleware).
        rls.set_rls_bypass(True, conn=conn)


@contextmanager
def _shopify_tenant_execution(company_id: int):
    """Run a scheduled Shopify command for ONE tenant under the same tenant
    routing + RLS session context ``TenantRlsMiddleware`` establishes for a
    request — its private, Celery-side twin, built only from existing primitives.

    Mirrors ``TenantRlsMiddleware.__call__`` CASE 3 exactly:

    - look up ``TenantDirectory.get_tenant_info`` on the system / default DB
      (short cross-tenant discovery);
    - enter ``override_tenant_context`` FIRST so database routing is established
      before the RLS connection is selected — restored on exit, even on
      exception, via its contextvar token;
    - THEN select the connection for the routed alias and set the RLS session
      context: for a SHARED tenant, ``app.current_company_id`` + bypass =
      ``settings.RLS_BYPASS`` (``False`` in production — RLS enforced); for a
      DEDICATED-DB tenant, bypass on (single tenant, no RLS) — the established
      dedicated-tenant semantics;
    - RESTORE the connection's prior RLS session state in ``finally`` (the prior
      state is clean in a production worker, the signal-installed bypass under
      tests) so nothing — least of all ``app.rls_bypass='on'`` — leaks into the
      next task on a reused worker connection, and company A never leaks into a
      later company B task.

    Callers refetch the ``ShopifyStore`` through the routed connection inside this
    scope; a store instance loaded during cross-tenant discovery is never the
    authoritative command instance. This CM holds NO transaction and NO row lock:
    the tenant / RLS context may span the remote Shopify sync, but the ``Company``
    admission lock (taken inside each command) may not.
    """
    from tenant.context import override_tenant_context, system_db_context
    from tenant.models import TenantDirectory

    with system_db_context():
        tenant_info = TenantDirectory.get_tenant_info(company_id)
    db_alias = tenant_info["db_alias"]
    is_shared = tenant_info["is_shared"]

    with override_tenant_context(company_id=company_id, db_alias=db_alias, is_shared=is_shared):
        conn = connections[db_alias]
        prev_company = rls.get_current_company_id(conn=conn)
        prev_bypass = rls.is_rls_bypassed(conn=conn)
        if is_shared:
            rls.set_rls_context(company_id, bypass=settings.RLS_BYPASS, conn=conn)
        else:
            rls.set_rls_bypass(True, conn=conn)
        try:
            yield tenant_info
        finally:
            # Restore (never blindly clear) so a reused worker connection keeps
            # whatever state it had before this task — mirrors rls_bypass().
            rls.set_current_company_id(prev_company, conn=conn)
            rls.set_rls_bypass(prev_bypass, conn=conn)


def _execute_scheduled_store_sync(store_id: int, company_id: int, runner):
    """Run a discovered store's scheduled Shopify sync under its tenant/RLS
    execution context — the single per-tenant execution path for every scheduled
    entrypoint.

    Enters :func:`_shopify_tenant_execution`, SKIPS a tenant that is not writable
    (``is_writable`` False — MIGRATING / READ_ONLY / SUSPENDED), refetches the
    ``ShopifyStore`` through the routed connection, and calls ``runner(store)``.

    The writable skip mirrors ``TenantRlsMiddleware`` CASE 3 (which 503s every
    non-GET request to a non-writable tenant) and ``projections.tasks`` (which
    excludes MIGRATING tenants from per-company background processing): a
    scheduled WRITER must never emit orders/events into a frozen tenant — during a
    shared→dedicated migration ``get_tenant_info`` forces the alias back to the
    source ``default`` DB, so an un-skipped sync would write past the migration
    snapshot and split-brain at cutover.
    """
    from .models import ShopifyStore

    with _shopify_tenant_execution(company_id) as tenant_info:
        if not tenant_info["is_writable"]:
            return {"status": "skipped", "reason": "tenant not writable (migrating / read-only / suspended)"}
        store = ShopifyStore.objects.select_related("company").get(id=store_id)
        return runner(store)


@shared_task(
    name="shopify.sync_all_stores",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def sync_shopify_all(self, lookback_hours: int = 48) -> dict:
    """
    Periodic catch-up: sync missed orders, payouts, and products for all active stores.

    This task is the primary mechanism for catching webhooks that were missed
    due to downtime, network issues, or Shopify delivery failures.

    Runs every 4 hours by default (configure via Django admin Periodic Tasks).

    Args:
        lookback_hours: How far back to look for missed orders/payouts (default 48h).

    Returns:
        Summary dict with per-store results.
    """
    from .models import ShopifyStore

    # Cross-tenant discovery: a short system/default-DB lookup under bypass. The
    # discovery rows are used only for their identity (id / company_id / domain);
    # the authoritative command instance is refetched under each tenant's context.
    with rls_bypass():
        discovered = list(
            ShopifyStore.objects.filter(status=ShopifyStore.Status.ACTIVE).values_list(
                "id", "company_id", "shop_domain"
            )
        )

    results = {}
    for store_id, company_id, shop_domain in discovered:
        try:
            # Per-tenant execution: tenant routing + RLS session context (bypass
            # OFF for shared tenants in production), non-writable tenants skipped,
            # refetched store, command run, guaranteed cleanup.
            results[shop_domain] = _execute_scheduled_store_sync(
                store_id, company_id, lambda store: _sync_store(store, lookback_hours)
            )
        except Exception:
            logger.exception("Shopify re-sync failed for store %s", shop_domain)
            results[shop_domain] = {"status": "error", "error": "See logs"}

    return {
        "stores_processed": len(discovered),
        "results": results,
    }


@shared_task(
    name="shopify.initial_store_sync",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def initial_store_sync(self, store_id: int) -> dict:
    """
    First data pull right after a store connects (OAuth callback, token
    exchange, or Shopify-initiated install finalization).

    Without this, a freshly connected merchant — or the App Store reviewer —
    sees an empty dashboard until they click the manual sync buttons or the
    4-hour periodic catch-up fires. Pulls a 7-day order window plus products
    and payouts; every downstream handler is idempotent, so overlapping with
    the periodic task is harmless.
    """
    from .models import ShopifyStore

    with rls_bypass():
        disc = ShopifyStore.objects.filter(id=store_id).values_list("id", "company_id", "status").first()
    if disc is None:
        return {"status": "error", "error": "Store not found"}
    _id, company_id, status = disc
    if status != ShopifyStore.Status.ACTIVE:
        return {"status": "skipped", "reason": "Store not active"}

    return _execute_scheduled_store_sync(store_id, company_id, lambda store: _sync_store(store, lookback_hours=24 * 7))


@shared_task(
    name="shopify.sync_store_orders",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def sync_shopify_store_orders(
    self,
    store_id: int,
    created_at_min: str | None = None,
    created_at_max: str | None = None,
) -> dict:
    """
    Sync orders for a specific store within a date range.

    Args:
        store_id: ShopifyStore.id
        created_at_min: ISO datetime string (default: 7 days ago)
        created_at_max: ISO datetime string (default: now)
    """
    from .models import ShopifyStore

    with rls_bypass():
        disc = ShopifyStore.objects.filter(id=store_id).values_list("id", "company_id", "status").first()
    if disc is None:
        return {"status": "error", "error": "Store not found"}
    _id, company_id, status = disc
    if status != ShopifyStore.Status.ACTIVE:
        return {"status": "skipped", "reason": "Store not active"}

    now = tz.now()
    min_date = created_at_min or (now - timedelta(days=7)).isoformat()
    max_date = created_at_max or now.isoformat()

    return _execute_scheduled_store_sync(store_id, company_id, lambda store: _sync_orders(store, min_date, max_date))


def _sync_store(store, lookback_hours: int) -> dict:
    """Sync orders, payouts, and products for a single store."""
    from .commands import sync_payouts, sync_products

    result = {"orders": {}, "payouts": {}, "products": {}}

    # 1. Sync missed orders
    now = tz.now()
    min_date = (now - timedelta(hours=lookback_hours)).isoformat()
    max_date = now.isoformat()

    try:
        _reassert_shopify_rls()
        order_result = _sync_orders(store, min_date, max_date)
        result["orders"] = order_result
    except Exception as e:
        logger.error("Order re-sync failed for %s: %s", store.shop_domain, e)
        result["orders"] = {"status": "error", "error": str(e)}

    # 2. Sync payouts (existing function handles idempotency)
    try:
        _reassert_shopify_rls()
        payout_result = sync_payouts(store)
        if payout_result.success:
            result["payouts"] = payout_result.data or {"status": "ok"}
        else:
            result["payouts"] = {"status": "error", "error": payout_result.error}
    except Exception as e:
        logger.error("Payout sync failed for %s: %s", store.shop_domain, e)
        result["payouts"] = {"status": "error", "error": str(e)}

    # 3. Sync products (existing function handles idempotency)
    try:
        _reassert_shopify_rls()
        product_result = sync_products(store)
        if product_result.success:
            result["products"] = product_result.data or {"status": "ok"}
        else:
            result["products"] = {"status": "error", "error": product_result.error}
    except Exception as e:
        logger.error("Product sync failed for %s: %s", store.shop_domain, e)
        result["products"] = {"status": "error", "error": str(e)}

    # 4. A159: refund catch-up. The webhook view can drop refunds (it used
    # to blanket-200 even on failure) and step 1's created_at window can
    # never see a refund issued against an order created before the
    # lookback — this pass searches by updated_at + financial_status and
    # is the durable recovery path for both.
    try:
        _reassert_shopify_rls()
        result["refunds"] = _sync_refunds(store, min_date, max_date)
    except Exception as e:
        logger.error("Refund catch-up failed for %s: %s", store.shop_domain, e)
        result["refunds"] = {"status": "error", "error": str(e)}

    # 5. F13: deferred-COD-COGS safety net. process_order_paid books
    # deferred COGS inline, but its failure path is log-only and a crash
    # between the paid event and the booking would strand the fulfillment
    # forever (the paid webhook's idempotency skip never re-enters). Any
    # COGS_PENDING fulfillment whose order is already paid gets booked
    # here.
    try:
        _reassert_shopify_rls()
        result["deferred_cogs"] = {"booked": _sweep_deferred_cogs(store)}
    except Exception as e:
        logger.error("Deferred-COGS sweep failed for %s: %s", store.shop_domain, e)
        result["deferred_cogs"] = {"status": "error", "error": str(e)}

    result["status"] = "ok"
    return result


def _sweep_deferred_cogs(store) -> int:
    """F13: book COGS for COGS_PENDING fulfillments whose order is already
    paid (event_id set). Dated order.order_date — for a promoted COD order
    that IS the collection date. Idempotent: a successful booking flips
    the fulfillment to PROCESSED, removing it from this queryset."""
    from .commands import _book_deferred_cogs
    from .models import ShopifyFulfillment, ShopifyOrder

    order_ids = (
        ShopifyFulfillment.objects.filter(
            company=store.company,
            status=ShopifyFulfillment.Status.COGS_PENDING,
            order__event_id__isnull=False,
        )
        .values_list("order_id", flat=True)
        .distinct()
    )

    booked = 0
    for order in ShopifyOrder.objects.filter(id__in=list(order_ids)):
        try:
            _reassert_shopify_rls()
            with transaction.atomic():
                booked += _book_deferred_cogs(store, order, order.order_date or tz.now().date())
        except Exception as e:
            logger.warning(
                "F13 sweep: deferred COGS failed for order %s on %s: %s",
                order.shopify_order_id,
                store.shop_domain,
                e,
            )
            from django.db import connection

            if connection.needs_rollback:
                connection.rollback()
    return booked


def _sync_refunds(store, updated_at_min: str, updated_at_max: str) -> dict:
    """A159: durable refund recovery. Finds orders whose refund state
    changed in the window (updated_at search — catches refunds on orders
    created long before the lookback), books the parent order first if it
    was never seen (refund-before-order), then backfills its refunds via
    the idempotent process_refund."""
    from .commands import _admin_client, process_order_paid

    client = _admin_client(store)
    if not client:
        return {"status": "error", "error": "Token expired or revoked — please reconnect the store."}

    scanned = 0
    refunds_created = 0
    errors = 0
    for order_payload in client.iter_refunded_orders(updated_at_min, updated_at_max):
        scanned += 1
        shopify_order_id = order_payload.get("id")
        if not shopify_order_id:
            continue
        # Cancelled orders route through process_order_cancelled in step 1;
        # booking revenue for them here would be wrong.
        if order_payload.get("cancelled_at"):
            continue

        # Ensure the parent order is booked BEFORE the refund events emit —
        # the projection's A41 defer window is 24h, so order-then-refund
        # ordering within the same pass keeps stale refunds processable.
        # process_order_paid is idempotent (skips already-booked orders).
        try:
            _reassert_shopify_rls()
            with transaction.atomic():
                order_result = process_order_paid(store, order_payload)
            if not order_result.success:
                errors += 1
                logger.warning(
                    "[A159] Could not book parent order %s on %s before refund backfill: %s",
                    shopify_order_id,
                    store.shop_domain,
                    order_result.error,
                )
                continue
        except Exception as e:
            errors += 1
            logger.warning(
                "[A159] Parent-order booking failed for %s on %s: %s",
                shopify_order_id,
                store.shop_domain,
                e,
            )
            from django.db import connection

            if connection.needs_rollback:
                connection.rollback()
            continue

        refunds_created += _backfill_order_refunds(store, client, shopify_order_id)

    return {"status": "ok", "scanned": scanned, "refunds_created": refunds_created, "errors": errors}


def _backfill_order_refunds(store, client, shopify_order_id) -> int:
    """A159: pull an order's refunds and book each via process_refund
    (idempotent on shopify_refund_id — re-runs skip already-booked ones).

    Best-effort by contract, mirroring _backfill_order_fulfillments: a
    refund fetch/processing failure is logged and swallowed — it must
    never roll back the order or abort the rest of the sync batch.
    Returns the count of refunds newly booked.
    """
    from .commands import process_refund

    try:
        refunds = client.get_order_refunds(shopify_order_id)
    except Exception as e:
        logger.warning(
            "[A159] Refund fetch failed for order %s on %s: %s",
            shopify_order_id,
            store.shop_domain,
            e,
        )
        return 0

    booked = 0
    for refund in refunds:
        try:
            _reassert_shopify_rls()
            with transaction.atomic():
                result = process_refund(store, refund)
            if result.success and not (result.data and result.data.get("skipped")):
                booked += 1
            elif not result.success:
                logger.warning(
                    "[A159] Refund backfill failed for refund %s on order %s (%s): %s",
                    refund.get("id"),
                    shopify_order_id,
                    store.shop_domain,
                    result.error,
                )
        except Exception as e:
            logger.warning(
                "[A159] Refund backfill failed for refund %s on order %s (%s): %s",
                refund.get("id"),
                shopify_order_id,
                store.shop_domain,
                e,
            )
            from django.db import connection

            if connection.needs_rollback:
                connection.rollback()

    return booked


def _sync_orders(store, created_at_min: str, created_at_max: str) -> dict:
    """
    Fetch orders from Shopify REST Admin API and process any that are missing locally.

    Uses the orders.json endpoint with date filtering to catch missed webhooks.
    Orders are routed by financial_status / cancelled_at:
      - paid / authorized / partially_paid -> process_order_paid (books invoice)
      - pending                             -> process_order_pending (metadata only)
      - cancelled (with cancelled_at set)   -> process_order_cancelled
      - anything else                       -> skipped

    All downstream handlers are idempotent.
    """
    from .commands import (
        _admin_client,
        process_order_cancelled,
        process_order_paid,
        process_order_pending,
    )

    client = _admin_client(store)
    if not client:
        return {"status": "error", "error": "Token expired or revoked — please reconnect the store."}

    fetched = 0
    created = 0
    skipped = 0
    errors = 0
    cogs_fulfillments = 0  # A125: COGS booked from backfilled fulfillments
    refunds_backfilled = 0  # A159: refunds booked for first-seen-refunded orders

    # A52 (2026-05-15): diagnostic logging while we hunt down why re-sync(7d)
    # returns 0 orders despite orders existing in the store. Root cause found
    # 2026-06-10: REST orders.json silently excludes dev-store test orders.
    # The GraphQL orders query below returns them.
    logger.info(
        "[A52] _sync_orders start shop=%s store_id=%s created_at_min=%s created_at_max=%s",
        store.shop_domain,
        store.id,
        created_at_min,
        created_at_max,
    )

    orders_iter = client.iter_orders(created_at_min, created_at_max)
    while True:
        try:
            order_payload = next(orders_iter)
        except StopIteration:
            break
        except requests.RequestException as e:
            # A120 (2026-06-01): treat access denials as "unavailable" rather
            # than a hard failure. Same rationale as sync_products/sync_payouts
            # — Shopify denies read-orders for tokens missing the scope. We
            # want the UI to say "Shopify denied access" instead of a generic
            # failure toast.
            from .commands import _is_auth_revoked, _mark_needs_reauth, _shopify_access_denied

            denial = _shopify_access_denied(e)
            # A49: 401 = token revoked -> reconnect banner (403 stays quiet).
            if denial and _is_auth_revoked(e):
                _mark_needs_reauth(store, "Shopify rejected the access token (401) — please reconnect the store.")
            if denial and fetched == 0:
                logger.info(
                    "Skipping order re-sync for %s: %s (scope not granted on this store)",
                    store.shop_domain,
                    denial,
                )
                return {
                    "status": "unavailable",
                    "fetched": 0,
                    "created": 0,
                    "skipped": 0,
                    "errors": 0,
                    "message": (
                        "Shopify didn't grant read access to orders on this "
                        "store. Disconnect and reconnect to re-grant the "
                        "read_orders scope, then try again."
                    ),
                }
            logger.error("Failed to fetch orders from Shopify %s: %s", store.shop_domain, e)
            return {
                "status": "partial" if fetched > 0 else "error",
                "fetched": fetched,
                "created": created,
                "skipped": skipped,
                "error": str(e),
            }

        fetched += 1
        shopify_order_id = order_payload.get("id")
        if not shopify_order_id:
            continue

        # Route to the right handler based on current order state.
        handler = _pick_order_handler(
            order_payload,
            process_order_paid,
            process_order_pending,
            process_order_cancelled,
        )
        if handler is None:
            skipped += 1
            continue

        # Process each order in its own savepoint so one failure
        # doesn't break the entire batch
        booked_paid = False
        try:
            _reassert_shopify_rls()
            with transaction.atomic():
                result = handler(store, order_payload)
                if result.success:
                    if result.data and result.data.get("skipped"):
                        skipped += 1
                    else:
                        created += 1
                    # Only paid orders book revenue + a SalesInvoice; their
                    # fulfillments are what carry COGS. "Skipped" here means
                    # already-booked — those still backfill so historical
                    # orders booked before fulfillment processing get COGS.
                    booked_paid = handler is process_order_paid
                else:
                    errors += 1
                    logger.warning(
                        "Failed to process order %s from %s: %s",
                        shopify_order_id,
                        store.shop_domain,
                        result.error,
                    )
        except Exception as e:
            errors += 1
            logger.error(
                "Error processing order %s from %s [%s]: %s",
                shopify_order_id,
                store.shop_domain,
                type(e).__name__,
                e,
            )
            # If the connection is in a broken state, reset it
            from django.db import connection

            if connection.needs_rollback:
                connection.rollback()

        # A125: book COGS for the order's fulfillments. Runs AFTER the order's
        # transaction committed and is best-effort — a fulfillment failure must
        # never roll back the already-booked order or break the batch.
        if booked_paid:
            cogs_fulfillments += _backfill_order_fulfillments(store, client, shopify_order_id)
            # A159: an order first seen already-refunded needs its refunds
            # backfilled too (its refunds/create webhooks were missed along
            # with orders/paid). Same best-effort contract as fulfillments.
            if (order_payload.get("financial_status") or "").lower() in ("refunded", "partially_refunded"):
                refunds_backfilled += _backfill_order_refunds(store, client, shopify_order_id)

    # A52: warning when fetch returns nothing — likely indicates token/date/
    # API-version issue rather than a "genuinely zero orders" situation.
    if fetched == 0:
        logger.warning(
            "[A52] _sync_orders fetched zero orders shop=%s created_at_min=%s created_at_max=%s "
            "(check: access_token valid? store has orders in this date range? "
            "API version still supported by shop?)",
            store.shop_domain,
            created_at_min,
            created_at_max,
        )
    else:
        logger.info(
            "[A52] _sync_orders done shop=%s fetched=%d created=%d skipped=%d errors=%d",
            store.shop_domain,
            fetched,
            created,
            skipped,
            errors,
        )

    # Sync-UX (2026-06-04): refresh last_sync_at on every successful pull
    # so the settings page "Last Sync" widget actually changes when the
    # merchant clicks Re-sync. Previously only sync_payouts did this, so
    # the widget stuck on "Never" no matter how many times the merchant
    # hit Re-sync Orders — the broken-looking signal the App Store
    # reviewer would see immediately.
    from django.utils import timezone as tz

    from projections.write_barrier import command_writes_allowed

    with command_writes_allowed():
        store.last_sync_at = tz.now()
        store.save(update_fields=["last_sync_at"])

    return {
        "status": "ok",
        "fetched": fetched,
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "cogs_fulfillments": cogs_fulfillments,
        "refunds_backfilled": refunds_backfilled,
    }


def _backfill_order_fulfillments(store, client, shopify_order_id) -> int:
    """A125: pull an order's fulfillments and book COGS for each via
    process_fulfillment (idempotent — re-runs skip already-booked ones).

    Best-effort by contract: the order is already committed by the time this
    runs, so a fulfillment fetch/processing failure is logged and swallowed —
    it must never roll back the order or abort the rest of the sync batch.
    Returns the count of fulfillments that newly booked COGS.
    """
    from .commands import process_fulfillment

    try:
        fulfillments = client.get_order_fulfillments(shopify_order_id)
    except Exception as e:
        logger.warning(
            "[A125] Fulfillment fetch failed for order %s on %s: %s",
            shopify_order_id,
            store.shop_domain,
            e,
        )
        return 0

    booked = 0
    for fulfillment in fulfillments:
        try:
            _reassert_shopify_rls()
            with transaction.atomic():
                result = process_fulfillment(store, fulfillment)
            if result.success and not (result.data and result.data.get("skipped")):
                booked += 1
        except Exception as e:
            logger.warning(
                "[A125] COGS backfill failed for fulfillment %s on order %s (%s): %s",
                fulfillment.get("id"),
                shopify_order_id,
                store.shop_domain,
                e,
            )
            from django.db import connection

            if connection.needs_rollback:
                connection.rollback()

    return booked


def _pick_order_handler(order_payload, paid_handler, pending_handler, cancelled_handler):
    """
    Decide which handler to call for a Shopify order based on its state.

    Returns None for orders we deliberately skip (e.g. voided).
    """
    if order_payload.get("cancelled_at"):
        return cancelled_handler

    financial_status = (order_payload.get("financial_status") or "").lower()
    # A159: an order first seen already-refunded must still book its revenue
    # invoice (process_order_paid is idempotent) — the refund backfill then
    # books the offsetting credit note. Previously these were skipped
    # entirely: neither revenue nor refund ever hit the books.
    if financial_status in ("paid", "authorized", "partially_paid", "refunded", "partially_refunded"):
        return paid_handler
    if financial_status == "pending":
        return pending_handler
    return None


@shared_task(name="shopify.process_gdpr_request")
def process_gdpr_request_task(gdpr_request_id: int) -> dict:
    """A124 fast path: enqueued by the webhook view right after the 200
    ack so requests process within minutes. The beat catch-up below is the
    durable safety net for dropped enqueues."""
    from .gdpr import process_gdpr_request
    from .models import GdprRequest

    with rls_bypass():
        req = GdprRequest.objects.filter(pk=gdpr_request_id, status=GdprRequest.Status.PENDING).first()
        if req is None:
            return {"status": "skipped", "reason": "not found or not PENDING"}
        ok = process_gdpr_request(req)
    return {"status": "ok" if ok else "failed", "request_id": gdpr_request_id}


@shared_task(
    name="shopify.process_gdpr_requests",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def process_gdpr_requests(self) -> dict:
    """A124 beat catch-up: process every PENDING GdprRequest. Register in
    Django admin -> Periodic Tasks (DatabaseScheduler), every 15-30 min —
    same ops step as shopify.sync_all_stores. Idempotent; a row the fast
    path already completed is skipped by the PENDING filter; concurrent
    workers are serialized per-row via select_for_update(skip_locked).
    """
    from django.db import transaction

    from .gdpr import process_gdpr_request
    from .models import GdprRequest

    processed = 0
    failed = 0
    with rls_bypass():
        pending_ids = list(GdprRequest.objects.filter(status=GdprRequest.Status.PENDING).values_list("pk", flat=True))
        for pk in pending_ids:
            try:
                with transaction.atomic():
                    req = (
                        GdprRequest.objects.select_for_update(skip_locked=True)
                        .filter(pk=pk, status=GdprRequest.Status.PENDING)
                        .first()
                    )
                    if req is None:
                        continue
                    if process_gdpr_request(req):
                        processed += 1
                    else:
                        failed += 1
            except Exception:
                logger.exception("GDPR beat pass failed on request %s", pk)
                failed += 1
                from django.db import connection

                if connection.needs_rollback:
                    connection.rollback()

    return {"status": "ok", "processed": processed, "failed": failed, "pending_seen": len(pending_ids)}


@shared_task(name="shopify.cleanup_stale_installs")
def cleanup_stale_installs(max_pending_age_hours: int = 24) -> dict:
    """A56: sweep abandoned install state. Register in Django admin →
    Periodic Tasks (DatabaseScheduler), hourly — same ops step as
    shopify.sync_all_stores.

    Deletes:
    - PENDING ShopifyStore rows older than ``max_pending_age_hours``: a
      merchant who bounced off the Shopify authorize screen leaves one
      forever (the B2 per-company sweep only fires when THAT merchant
      clicks Connect again), holding the uniq_company_shop_domain slot.
      get_install_url recreates the row on the next Connect click.
    - Unconsumed PendingShopifyInstall rows >1h past their 30-min
      expires_at: they hold encrypted access/refresh tokens indefinitely
      and nothing else ever deletes them.
    - Consumed PendingShopifyInstall rows older than 30 days: their
      tokens were copied to the store at finalize time.
    """
    from datetime import timedelta

    from projections.write_barrier import command_writes_allowed

    from .models import PendingShopifyInstall, ShopifyStore

    now = tz.now()
    with rls_bypass(), command_writes_allowed():
        stale_pending = ShopifyStore.objects.filter(
            status=ShopifyStore.Status.PENDING,
            updated_at__lt=now - timedelta(hours=max_pending_age_hours),
        )
        pending_domains = list(stale_pending.values_list("shop_domain", flat=True))
        pending_deleted, _ = stale_pending.delete()

        expired_installs = PendingShopifyInstall.objects.filter(
            consumed_at__isnull=True,
            expires_at__lt=now - timedelta(hours=1),
        )
        install_domains = list(expired_installs.values_list("shop_domain", flat=True))
        installs_deleted, _ = expired_installs.delete()

        consumed_deleted, _ = PendingShopifyInstall.objects.filter(
            consumed_at__isnull=False,
            consumed_at__lt=now - timedelta(days=30),
        ).delete()

    if pending_domains or install_domains or consumed_deleted:
        logger.info(
            "A56 sweep: deleted %d stale PENDING stores %s, %d expired installs %s, %d old consumed installs",
            pending_deleted,
            pending_domains,
            installs_deleted,
            install_domains,
            consumed_deleted,
        )
    return {
        "pending_stores_deleted": pending_deleted,
        "expired_installs_deleted": installs_deleted,
        "consumed_installs_deleted": consumed_deleted,
    }
