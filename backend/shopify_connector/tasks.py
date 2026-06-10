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
from datetime import timedelta

import requests
from celery import shared_task
from django.db import transaction
from django.utils import timezone as tz

from accounts.rls import rls_bypass

logger = logging.getLogger(__name__)


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

    with rls_bypass():
        stores = list(ShopifyStore.objects.filter(status=ShopifyStore.Status.ACTIVE).select_related("company"))

    results = {}
    for store in stores:
        try:
            result = _sync_store(store, lookback_hours)
            results[store.shop_domain] = result
        except Exception:
            logger.exception("Shopify re-sync failed for store %s", store.shop_domain)
            results[store.shop_domain] = {"status": "error", "error": "See logs"}

    return {
        "stores_processed": len(stores),
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
        try:
            store = ShopifyStore.objects.select_related("company").get(id=store_id)
        except ShopifyStore.DoesNotExist:
            return {"status": "error", "error": "Store not found"}

    if store.status != ShopifyStore.Status.ACTIVE:
        return {"status": "skipped", "reason": "Store not active"}

    return _sync_store(store, lookback_hours=24 * 7)


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
        try:
            store = ShopifyStore.objects.select_related("company").get(id=store_id)
        except ShopifyStore.DoesNotExist:
            return {"status": "error", "error": "Store not found"}

    if store.status != ShopifyStore.Status.ACTIVE:
        return {"status": "skipped", "reason": "Store not active"}

    now = tz.now()
    min_date = created_at_min or (now - timedelta(days=7)).isoformat()
    max_date = created_at_max or now.isoformat()

    return _sync_orders(store, min_date, max_date)


def _sync_store(store, lookback_hours: int) -> dict:
    """Sync orders, payouts, and products for a single store."""
    from .commands import sync_payouts, sync_products

    result = {"orders": {}, "payouts": {}, "products": {}}

    # 1. Sync missed orders
    now = tz.now()
    min_date = (now - timedelta(hours=lookback_hours)).isoformat()
    max_date = now.isoformat()

    try:
        order_result = _sync_orders(store, min_date, max_date)
        result["orders"] = order_result
    except Exception as e:
        logger.error("Order re-sync failed for %s: %s", store.shop_domain, e)
        result["orders"] = {"status": "error", "error": str(e)}

    # 2. Sync payouts (existing function handles idempotency)
    try:
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
        product_result = sync_products(store)
        if product_result.success:
            result["products"] = product_result.data or {"status": "ok"}
        else:
            result["products"] = {"status": "error", "error": product_result.error}
    except Exception as e:
        logger.error("Product sync failed for %s: %s", store.shop_domain, e)
        result["products"] = {"status": "error", "error": str(e)}

    result["status"] = "ok"
    return result


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
            from .commands import _shopify_access_denied

            denial = _shopify_access_denied(e)
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
        try:
            with transaction.atomic():
                result = handler(store, order_payload)
                if result.success:
                    if result.data and result.data.get("skipped"):
                        skipped += 1
                    else:
                        created += 1
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
    }


def _pick_order_handler(order_payload, paid_handler, pending_handler, cancelled_handler):
    """
    Decide which handler to call for a Shopify order based on its state.

    Returns None for orders we deliberately skip (e.g. voided / refunded-only).
    """
    if order_payload.get("cancelled_at"):
        return cancelled_handler

    financial_status = (order_payload.get("financial_status") or "").lower()
    if financial_status in ("paid", "authorized", "partially_paid"):
        return paid_handler
    if financial_status == "pending":
        return pending_handler
    return None
