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
