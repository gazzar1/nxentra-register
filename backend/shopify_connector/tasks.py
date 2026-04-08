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
from datetime import datetime, timedelta

import requests
from celery import shared_task
from django.utils import timezone as tz

from accounts.models import Company
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
        stores = list(
            ShopifyStore.objects
            .filter(status=ShopifyStore.Status.ACTIVE)
            .select_related("company")
        )

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
    Each order is processed through process_order_paid() which handles idempotency
    via the unique (company, shopify_order_id) constraint.
    """
    from .commands import process_order_paid
    from .models import ShopifyOrder

    if not store.access_token:
        return {"status": "error", "error": "No access token"}

    headers = {
        "X-Shopify-Access-Token": store.access_token,
        "Content-Type": "application/json",
    }

    fetched = 0
    created = 0
    skipped = 0
    errors = 0
    page_url = (
        f"https://{store.shop_domain}/admin/api/2025-01/orders.json"
    )
    params = {
        "status": "any",
        "financial_status": "paid",
        "created_at_min": created_at_min,
        "created_at_max": created_at_max,
        "limit": 250,
    }

    while page_url:
        try:
            resp = requests.get(page_url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("Failed to fetch orders from Shopify %s: %s", store.shop_domain, e)
            return {
                "status": "partial" if fetched > 0 else "error",
                "fetched": fetched, "created": created, "skipped": skipped,
                "error": str(e),
            }

        orders_data = resp.json().get("orders", [])
        fetched += len(orders_data)

        for order_payload in orders_data:
            shopify_order_id = order_payload.get("id")
            if not shopify_order_id:
                continue

            # Quick check: skip if already exists (avoid full command overhead)
            if ShopifyOrder.objects.filter(
                company=store.company, shopify_order_id=shopify_order_id
            ).exists():
                skipped += 1
                continue

            # Process through the standard webhook handler (handles event emission)
            try:
                result = process_order_paid(store, order_payload)
                if result.success:
                    if result.data and result.data.get("skipped"):
                        skipped += 1
                    else:
                        created += 1
                else:
                    errors += 1
                    logger.warning(
                        "Failed to process order %s from %s: %s",
                        shopify_order_id, store.shop_domain, result.error,
                    )
            except Exception as e:
                errors += 1
                logger.error(
                    "Error processing order %s from %s: %s",
                    shopify_order_id, store.shop_domain, e,
                )

        # Pagination: follow Link header for next page
        params = {}  # Clear params for subsequent pages (URL contains them)
        page_url = _get_next_page_url(resp)

    return {
        "status": "ok",
        "fetched": fetched,
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }


def _get_next_page_url(response) -> str | None:
    """Extract next page URL from Shopify Link header."""
    link_header = response.headers.get("Link", "")
    if not link_header:
        return None

    for part in link_header.split(","):
        if 'rel="next"' in part:
            url = part.split(";")[0].strip().strip("<>")
            return url

    return None
