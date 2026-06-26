# stripe_connector/tasks.py
"""Celery tasks for the Stripe pull/backfill (ADR-0002 S1).

- sync_stripe_all: periodic catch-up over all ACTIVE accounts (the pull is the
  primary settlement truth source; register at ~4h via Django admin Periodic
  Tasks, mirroring shopify.sync_all_stores).
- initial_stripe_sync: one-shot deeper backfill kicked off right after connect.
"""

import logging

from celery import shared_task

from accounts.rls import rls_bypass

logger = logging.getLogger(__name__)


@shared_task(
    name="stripe.sync_all_accounts",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def sync_stripe_all(self, lookback_hours: int = 168) -> dict:
    """Periodic catch-up: pull recent payouts for every ACTIVE Stripe account.

    The 7-day default arrival_date rescan window re-catches payouts that were
    in_progress on a prior run once they complete (idempotency dedups re-listed
    ones)."""
    from .models import StripeAccount
    from .sync import sync_payouts

    with rls_bypass():
        accounts = list(StripeAccount.objects.filter(status=StripeAccount.Status.ACTIVE).select_related("company"))

    results = {}
    for account in accounts:
        try:
            with rls_bypass():
                results[str(account.public_id)] = sync_payouts(account, lookback_hours=lookback_hours)
        except Exception:
            logger.exception("Stripe sync failed for account %s", account.id)
            results[str(account.public_id)] = {"status": "error", "error": "See logs"}

    return {"accounts_processed": len(accounts), "results": results}


@shared_task(name="stripe.initial_account_sync", bind=True, max_retries=2, default_retry_delay=60)
def initial_stripe_sync(self, account_id: int) -> dict:
    """Deeper backfill (default ~90 days) right after a merchant connects."""
    from .models import StripeAccount
    from .sync import sync_payouts

    with rls_bypass():
        try:
            account = StripeAccount.objects.select_related("company").get(id=account_id)
        except StripeAccount.DoesNotExist:
            return {"status": "error", "error": "account not found"}
        return sync_payouts(account, lookback_hours=24 * 90)


@shared_task(name="stripe.sync_account", bind=True, max_retries=2, default_retry_delay=60)
def sync_stripe_account(self, account_id: int) -> dict:
    """Pull recent payouts for ONE account — the webhook-triggered, near-real-time
    path. Reuses the idempotent sync_payouts (dedupes by payout id), so a repeat
    run is safe."""
    from .models import StripeAccount
    from .sync import sync_payouts

    with rls_bypass():
        try:
            account = StripeAccount.objects.select_related("company").get(id=account_id)
        except StripeAccount.DoesNotExist:
            return {"status": "error", "error": "account not found"}
        return sync_payouts(account)


# How long to suppress re-enqueuing a sync for the same account (debounce a burst
# of webhooks into one pull). A missed debounce is still safe — the pull is
# idempotent — this only avoids a sync storm.
_SYNC_ENQUEUE_DEBOUNCE_SECONDS = 60


def enqueue_account_sync(account_id: int) -> bool:
    """Enqueue a single-account pull, debounced so a burst of payout.paid
    webhooks doesn't spawn a sync storm. Returns True if enqueued, False if a
    recent enqueue already covers it."""
    from django.core.cache import cache

    # cache.add() is atomic set-if-absent: only the first caller within the
    # window enqueues. If the cache backend is unavailable it raises → caller's
    # try/except keeps the webhook ack safe.
    if not cache.add(f"stripe:sync:enqueued:{account_id}", 1, timeout=_SYNC_ENQUEUE_DEBOUNCE_SECONDS):
        return False
    sync_stripe_account.delay(account_id)
    return True
