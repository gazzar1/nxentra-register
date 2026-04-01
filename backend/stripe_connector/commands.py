# stripe_connector/commands.py
"""
Command functions for storing Stripe webhook records locally.

These are called by StripeConnector.store_webhook_record() after the
generic platform view has emitted a PLATFORM_* event.  They create
local StripeCharge / StripeRefund / StripePayout rows so the
reconciliation views have data to query.

All writes are idempotent — duplicate webhook deliveries are safely
skipped via unique_together on (company, stripe_*_id).
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal

from django.db import IntegrityError

from .models import StripeAccount, StripeCharge, StripePayout, StripeRefund

logger = logging.getLogger(__name__)


def store_charge(company, parsed, payload, event_id):
    """
    Create a StripeCharge record from a parsed charge.captured webhook.

    Idempotent: skips if stripe_charge_id already exists for this company.
    """
    obj = payload.get("data", {}).get("object", {})
    stripe_charge_id = obj.get("id", "")
    if not stripe_charge_id:
        return

    account = _resolve_account(company, payload)
    if not account:
        logger.warning("No active StripeAccount for company %s — skipping charge store", company)
        return

    amount_cents = obj.get("amount", 0)
    fee_cents = obj.get("application_fee_amount") or 0
    amount = Decimal(amount_cents) / 100
    fee = Decimal(fee_cents) / 100
    net = amount - fee

    billing = obj.get("billing_details", {})
    created_ts = obj.get("created", 0)
    created_dt = datetime.fromtimestamp(created_ts, tz=UTC) if created_ts else datetime.now(tz=UTC)

    try:
        StripeCharge.objects.create(
            company=company,
            account=account,
            stripe_charge_id=stripe_charge_id,
            stripe_payment_intent_id=obj.get("payment_intent", ""),
            amount=amount,
            fee=fee,
            net=net,
            currency=(obj.get("currency") or "usd").upper(),
            description=obj.get("description", ""),
            customer_email=billing.get("email") or obj.get("receipt_email", ""),
            customer_name=billing.get("name", ""),
            charge_date=created_dt.date(),
            stripe_created_at=created_dt,
            status=StripeCharge.Status.PROCESSED,
            event_id=event_id,
            raw_payload=payload,
        )
        logger.info("Stored StripeCharge %s for company %s", stripe_charge_id, company)
    except IntegrityError:
        logger.info("StripeCharge %s already exists — skipping", stripe_charge_id)


def store_refund(company, parsed, payload, event_id):
    """
    Create a StripeRefund record from a parsed charge.refunded webhook.

    Links to existing StripeCharge if found.
    Idempotent: skips if stripe_refund_id already exists for this company.
    """
    obj = payload.get("data", {}).get("object", {})
    # obj is the charge; refunds are nested
    refunds_data = obj.get("refunds", {}).get("data", [])
    latest = refunds_data[0] if refunds_data else {}

    stripe_refund_id = latest.get("id", "")
    stripe_charge_id = obj.get("id", "")
    if not stripe_refund_id:
        return

    # Find or create the parent charge
    charge = StripeCharge.objects.filter(
        company=company, stripe_charge_id=stripe_charge_id
    ).first()

    if not charge:
        # Charge not stored yet — create a minimal record
        account = _resolve_account(company, payload)
        if not account:
            logger.warning("No active StripeAccount for company %s — skipping refund store", company)
            return

        created_ts = obj.get("created", 0)
        created_dt = datetime.fromtimestamp(created_ts, tz=UTC) if created_ts else datetime.now(tz=UTC)
        amount_cents = obj.get("amount", 0)

        try:
            charge = StripeCharge.objects.create(
                company=company,
                account=account,
                stripe_charge_id=stripe_charge_id,
                amount=Decimal(amount_cents) / 100,
                currency=(obj.get("currency") or "usd").upper(),
                charge_date=created_dt.date(),
                stripe_created_at=created_dt,
                status=StripeCharge.Status.PROCESSED,
                raw_payload={},
            )
        except IntegrityError:
            charge = StripeCharge.objects.get(
                company=company, stripe_charge_id=stripe_charge_id
            )

    refund_amount = Decimal(latest.get("amount", 0)) / 100
    refund_ts = latest.get("created", 0)
    refund_dt = datetime.fromtimestamp(refund_ts, tz=UTC) if refund_ts else datetime.now(tz=UTC)

    try:
        StripeRefund.objects.create(
            company=company,
            charge=charge,
            stripe_refund_id=stripe_refund_id,
            amount=refund_amount,
            currency=(obj.get("currency") or "usd").upper(),
            reason=latest.get("reason", ""),
            stripe_created_at=refund_dt,
            status=StripeRefund.Status.PROCESSED,
            event_id=event_id,
            raw_payload=payload,
        )
        logger.info("Stored StripeRefund %s for company %s", stripe_refund_id, company)
    except IntegrityError:
        logger.info("StripeRefund %s already exists — skipping", stripe_refund_id)


def store_payout(company, parsed, payload, event_id):
    """
    Create a StripePayout record from a parsed payout.paid webhook.

    Idempotent: skips if stripe_payout_id already exists for this company.
    """
    obj = payload.get("data", {}).get("object", {})
    stripe_payout_id = obj.get("id", "")
    if not stripe_payout_id:
        return

    account = _resolve_account(company, payload)
    if not account:
        logger.warning("No active StripeAccount for company %s — skipping payout store", company)
        return

    amount_cents = obj.get("amount", 0)
    net = Decimal(amount_cents) / 100
    arrival_ts = obj.get("arrival_date", 0)
    arrival_date = datetime.fromtimestamp(arrival_ts, tz=UTC).date() if arrival_ts else None

    try:
        StripePayout.objects.create(
            company=company,
            account=account,
            stripe_payout_id=stripe_payout_id,
            gross_amount=net,  # Stripe payouts are already net of fees
            fees=Decimal("0"),
            net_amount=net,
            currency=(obj.get("currency") or "usd").upper(),
            stripe_status=obj.get("status", ""),
            payout_date=arrival_date or datetime.now(tz=UTC).date(),
            status=StripePayout.Status.PROCESSED,
            event_id=event_id,
            raw_payload=payload,
        )
        logger.info("Stored StripePayout %s for company %s", stripe_payout_id, company)
    except IntegrityError:
        logger.info("StripePayout %s already exists — skipping", stripe_payout_id)


def _resolve_account(company, payload):
    """Resolve the StripeAccount from the webhook payload's 'account' field."""
    account_id = payload.get("account", "")
    if account_id:
        try:
            return StripeAccount.objects.get(
                company=company,
                stripe_account_id=account_id,
                status=StripeAccount.Status.ACTIVE,
            )
        except StripeAccount.DoesNotExist:
            pass
    # Fallback: first active account for this company
    return StripeAccount.objects.filter(
        company=company, status=StripeAccount.Status.ACTIVE
    ).first()
