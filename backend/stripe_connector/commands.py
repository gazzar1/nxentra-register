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

# The Stripe restricted-key READ scopes a merchant must grant. ONE canonical list
# so the connect validation, every rejection message, and the settings-page hint
# stay in lockstep. These are the ONLY scopes the S1 reconciliation pull needs:
# sync_payouts reads Payouts + their Balance Transactions, and the connect probe
# (api_client.probe) validates exactly those. We deliberately do NOT require:
#   * reading the account's business/KYC info (GET /v1/account, scope
#     accounts_kyc_basic_read) — Stripe doesn't even expose that toggle in the
#     restricted-key editor, so account id + livemode are captured BEST-EFFORT and
#     connect never depends on it;
#   * Charges — never read via the API key (StripeCharge rows arrive via webhooks);
#   * Disputes — Phase 3.
# Asking a merchant for any of those just to connect reconciliation is wrong.
REQUIRED_READ_SCOPES = ("Balance", "Payouts")


def required_scopes_phrase() -> str:
    """Render the canonical scope list for a user-facing message, e.g.
    'Balance and Payouts'."""
    *head, last = REQUIRED_READ_SCOPES
    return f"{', '.join(head)} and {last}" if head else last


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
    charge = StripeCharge.objects.filter(company=company, stripe_charge_id=stripe_charge_id).first()

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
            charge = StripeCharge.objects.get(company=company, stripe_charge_id=stripe_charge_id)

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
    return StripeAccount.objects.filter(company=company, status=StripeAccount.Status.ACTIVE).first()


def connect_stripe_account(company, credential: str, display_name: str = ""):
    """Validate + persist a restricted read key as an ACTIVE Stripe connection
    (ADR-0002 S1). The single place a real Stripe credential first enters the
    system, so it enforces the safety invariants:

      * reject SECRET (sk_) + publishable (pk_) keys — only restricted READ keys
        (rk_…) are accepted, so Nxentra can never write to a merchant's Stripe;
      * live-probe the key against Stripe to confirm it's valid + read-scoped and
        to capture the account id + livemode;
      * store the credential A47-encrypted (credential_ref is an EncryptedTextField);
      * seed the platform_stripe accounts + SettlementProvider and kick an
        initial backfill.
    """
    from django.conf import settings as dj_settings

    from accounting.commands import CommandResult
    from projections.write_barrier import command_writes_allowed

    from .api_client import StripeAccessDenied, StripeApiClient, StripeApiError
    from .seed import setup_stripe_platform

    credential = (credential or "").strip()
    if not credential:
        return CommandResult.fail("Enter your Stripe restricted read-only API key.")
    if credential.startswith("sk_"):
        return CommandResult.fail(
            "That looks like a SECRET key (sk_…), which grants write access. Nxentra is "
            f"read-only — create a RESTRICTED key (rk_…) with {required_scopes_phrase()} set to Read."
        )
    if credential.startswith("pk_"):
        return CommandResult.fail("That's a publishable key (pk_…). Provide a restricted read-only key (rk_…).")
    if not credential.startswith(("rk_test_", "rk_live_")):
        return CommandResult.fail(
            "Invalid key format. Provide a Stripe restricted read-only API key (starts with rk_test_ or rk_live_)."
        )

    client = StripeApiClient(credential, api_version=getattr(dj_settings, "STRIPE_API_VERSION", "") or None)

    # The ONLY hard requirement: the key can READ the resources the pull needs
    # (Payouts + Balance Transactions). probe() is the gate — a key that passes it
    # can do the whole reconciliation pull, so we never persist a connected-but-
    # unusable account (Codex P2). Reading /v1/account is NOT required.
    try:
        client.probe()
    except StripeAccessDenied:
        return CommandResult.fail(
            "Stripe rejected that key — it's invalid or lacks read scope. Ensure the restricted "
            f"key has {required_scopes_phrase()} set to Read."
        )
    except StripeApiError as exc:
        return CommandResult.fail(f"Couldn't validate the key with Stripe: {exc}")

    # Best-effort metadata: the real account id + livemode + name come from
    # GET /v1/account, which needs the account-read (KYC) permission Stripe doesn't
    # expose in the restricted-key editor. If it's denied we fall back to the key
    # prefix for livemode and a stable per-company id — connect still succeeds.
    acct_id = ""
    livemode = credential.startswith("rk_live_")
    name = display_name or "Stripe"
    try:
        acct = client.retrieve_account()
        acct_id = acct.get("id") or ""
        livemode = bool(acct.get("livemode", livemode))
        name = display_name or (acct.get("business_profile") or {}).get("name") or acct.get("email") or "Stripe"
    except StripeApiError:
        logger.info("Stripe account read unavailable for the connect key — using best-effort metadata.")

    # Key on the account's REAL id when we have it, so a different real account
    # gets its own row and the same one updates in place — never clobbering a
    # synced account's identity (its payouts FK to this row). When /v1/account is
    # denied we fall back to a stable per-company synthetic id (reused across
    # account-read-denied reconnects). update_or_create resolves the unique
    # (company, stripe_account_id) safely either way.
    account, _ = StripeAccount.objects.update_or_create(
        company=company,
        stripe_account_id=acct_id or f"stripe:company:{company.id}",
        defaults={
            "auth_type": StripeAccount.AuthType.RESTRICTED_KEY,
            "credential_ref": credential,  # EncryptedTextField → encrypted at rest (A47)
            "status": StripeAccount.Status.ACTIVE,
            "livemode": livemode,
            "display_name": name,
            "error_message": "",
        },
    )

    # Seed the platform_stripe accounts + SettlementProvider so the first synced
    # payout's JE can resolve its mapping (idempotent).
    with command_writes_allowed():
        setup_stripe_platform(company)

    # Kick an initial backfill (best-effort — a broker hiccup must not fail connect).
    try:
        from .tasks import initial_stripe_sync

        initial_stripe_sync.delay(account.id)
    except Exception:
        logger.warning("Could not enqueue initial Stripe sync for account %s", account.id)

    return CommandResult.ok(data={"account": account})
