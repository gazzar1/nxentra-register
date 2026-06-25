# stripe_connector/sync.py
"""Stripe pull/backfill sync (ADR-0002 S1).

The PRIMARY truth source for Stripe settlements: pull Payouts + their Balance
Transactions, write the raw objects to the provenance cache, derive REAL fees
(``payout.paid`` reports none), and emit ONE canonical
``PAYMENT_SETTLEMENT_RECEIVED`` per payout so the PaymentSettlementProjection
posts the drain JE with a real fee leg. The webhook ``payout.paid`` path is
demoted to a non-posting status update (see connector.store_webhook_record).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from events.emitter import emit_event_no_actor
from events.types import EventTypes, PaymentSettlementReceivedData
from platform_connectors.models import ProviderRawObject

from .api_client import StripeAccessDenied, StripeApiClient
from .models import StripeAccount, StripePayout, StripePayoutTransaction
from .normalize import derive_payout_breakdown

logger = logging.getLogger(__name__)

# Stripe BalanceTransaction.type → StripePayoutTransaction.TransactionType
_BT_TYPE_MAP = {
    "charge": StripePayoutTransaction.TransactionType.CHARGE,
    "payment": StripePayoutTransaction.TransactionType.CHARGE,
    "refund": StripePayoutTransaction.TransactionType.REFUND,
    "payment_refund": StripePayoutTransaction.TransactionType.REFUND,
    "adjustment": StripePayoutTransaction.TransactionType.ADJUSTMENT,
    "payout": StripePayoutTransaction.TransactionType.PAYOUT,
}


def _dec(cents) -> Decimal:
    return (Decimal(int(cents or 0)) / 100).quantize(Decimal("0.01"))


def _api_version() -> str:
    return getattr(settings, "STRIPE_API_VERSION", "") or ""


def _stripe_client(account: StripeAccount) -> StripeApiClient | None:
    """Build a read client from the connection (auth-agnostic). Returns None when
    no credential is stored yet. A Connect ``stripe_account`` header is sent only
    for OAuth/Stripe-App connections; a merchant's own restricted key is already
    account-scoped."""
    credential = account.credential_ref  # EncryptedTextField → plaintext on read
    if not credential:
        return None
    connect_acct = account.stripe_account_id if account.auth_type == StripeAccount.AuthType.OAUTH else None
    return StripeApiClient(credential, stripe_account_id=connect_acct, api_version=_api_version() or None)


def _arrival_cutoff(account: StripeAccount, lookback_hours: int) -> int:
    """Unix `arrival_date >=` filter.

    Reaches back to whichever is EARLIER:
      * a fixed rescan window back from NOW (re-lists recent payouts each run so
        an ``in_progress`` one is re-caught once it completes — idempotency
        dedups), and
      * ``last_sync_at`` minus a small overlap, so after an outage LONGER than
        the rescan window the catch-up still covers every payout that arrived
        while we were down (Codex P2).

    With no prior sync (initial backfill), only the window applies.
    """
    rescan = timezone.now() - timedelta(hours=lookback_hours)
    if account.last_sync_at:
        rescan = min(rescan, account.last_sync_at - timedelta(hours=1))
    return int(rescan.timestamp())


def sync_payouts(account: StripeAccount, *, lookback_hours: int = 168) -> dict:
    """Pull recent paid payouts for one account, emit canonical settlement
    events, and refresh the read-models. Idempotent (event idempotency_key +
    read-model upsert), so re-runs are safe."""
    if account.status != StripeAccount.Status.ACTIVE:
        return {"status": "skipped", "reason": "account not active", "created": 0, "skipped": 0}

    client = _stripe_client(account)
    if client is None:
        return {"status": "unavailable", "reason": "no credential stored", "created": 0, "skipped": 0}

    try:
        payouts = client.list_payouts(arrival_date_gte=_arrival_cutoff(account, lookback_hours), status="paid")
    except StripeAccessDenied as exc:
        logger.info("Stripe payout sync unavailable for account %s: %s", account.id, exc)
        _mark_error(account, f"Stripe access denied: {exc}")
        return {"status": "unavailable", "reason": str(exc), "created": 0, "skipped": 0}

    company = account.company
    now = timezone.now()
    created = 0
    skipped = 0

    for po in payouts:
        payout_id = po.get("id")
        if not payout_id:
            continue
        # Provenance snapshot of the payout itself (idempotent on payload hash).
        ProviderRawObject.record(
            company=company,
            provider="stripe",
            object_type="payout",
            external_id=payout_id,
            payload=po,
            source=ProviderRawObject.Source.API,
            api_version=_api_version(),
            fetched_at=now,
        )

        # Only `completed` reconciliation guarantees the payout's balance
        # transactions are all available; `in_progress` means "soon". Emitting on
        # an incomplete payout would derive understated fees that the idempotent
        # event then locks in (Codex P1). Skip — the arrival_date rescan window
        # re-catches it once it completes.
        if po.get("reconciliation_status") == "in_progress":
            logger.info("Stripe payout %s reconciliation in_progress — deferring to next sync", payout_id)
            skipped += 1
            continue

        try:
            txns = client.list_balance_transactions(payout_id)
        except StripeAccessDenied as exc:
            logger.info("Stripe balance-txn fetch denied for %s: %s", payout_id, exc)
            _mark_error(account, f"Stripe access denied: {exc}")
            return {"status": "unavailable", "reason": str(exc), "created": created, "skipped": skipped}

        for bt in txns:
            ProviderRawObject.record(
                company=company,
                provider="stripe",
                object_type="balance_transaction",
                external_id=bt.get("id", ""),
                payload=bt,
                source=ProviderRawObject.Source.API,
                api_version=_api_version(),
                fetched_at=now,
            )

        # Defensive: a non-zero payout with no itemized constituent transactions
        # would derive fees=0/gross=net — the very bug we're fixing. Skip rather
        # than emit a wrong (and idempotently-locked) settlement.
        has_constituents = any(bt.get("type") != "payout" for bt in txns)
        if not has_constituents and int(po.get("amount") or 0) != 0:
            logger.warning(
                "Stripe payout %s has no itemized balance transactions "
                "(reconciliation_status=%s) — skipping to avoid an understated settlement.",
                payout_id,
                po.get("reconciliation_status"),
            )
            skipped += 1
            continue

        breakdown = derive_payout_breakdown(po, txns)
        _emit_settlement(company, payout_id, breakdown)
        _upsert_read_models(account, payout_id, po, txns, breakdown)
        created += 1

    account.last_sync_at = now
    account.error_message = ""
    account.save(update_fields=["last_sync_at", "error_message", "updated_at"])
    return {"status": "ok", "created": created, "skipped": skipped}


def _emit_settlement(company, payout_id: str, breakdown: dict):
    """Emit the canonical settlement event (idempotent on the payout id)."""
    payout_date = breakdown["payout_date"]
    event = emit_event_no_actor(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
        aggregate_type="PaymentSettlement",
        aggregate_id=f"stripe:{payout_id}",
        idempotency_key=f"payment.settlement.received:stripe:{payout_id}",
        metadata={"source": "stripe_payout_sync"},
        data=PaymentSettlementReceivedData(
            amount=str(breakdown["gross"]),
            currency=breakdown["currency"],
            transaction_date=payout_date,
            document_ref=payout_id,
            provider_normalized_code="stripe",
            external_system="stripe",
            payout_batch_id=payout_id,
            gross_amount=str(breakdown["gross"]),
            fees=str(breakdown["fees"]),
            net_amount=str(breakdown["net"]),
            uncollected_amount="0",
            payment_method="card",
            payout_date=payout_date,
            line_items=breakdown["line_items"],
        ),
    )
    return event


def _upsert_read_models(account, payout_id, po, txns, breakdown):
    """Refresh the StripePayout / StripePayoutTransaction read-models with the
    derived (real) fees so the reconciliation views reflect the pull. These are
    denormalized caches pending the Phase-2 sole-writer projection."""
    company = account.company
    payout_date = breakdown["payout_date"] or timezone.now().date()
    payout, _ = StripePayout.objects.update_or_create(
        company=company,
        stripe_payout_id=payout_id,
        defaults={
            "account": account,
            "gross_amount": breakdown["gross"],
            "fees": breakdown["fees"],
            "net_amount": breakdown["net"],
            "currency": breakdown["currency"],
            "stripe_status": breakdown["status"],
            "payout_date": payout_date,
            "raw_payload": po,
        },
    )
    for bt in txns:
        if bt.get("type") == "payout":
            continue
        bt_id = bt.get("id", "")
        if not bt_id:
            continue
        amount = int(bt.get("amount", 0))
        fee = int(bt.get("fee", 0))
        StripePayoutTransaction.objects.update_or_create(
            company=company,
            stripe_balance_txn_id=bt_id,
            defaults={
                "payout": payout,
                "transaction_type": _BT_TYPE_MAP.get(bt.get("type"), StripePayoutTransaction.TransactionType.OTHER),
                "amount": _dec(amount),
                "fee": _dec(fee),
                "net": _dec(amount - fee),
                "currency": breakdown["currency"],
                "source_id": bt.get("source") or "",
                "raw_data": bt,
            },
        )


def _mark_error(account: StripeAccount, message: str):
    account.error_message = message[:500]
    account.save(update_fields=["error_message", "updated_at"])
