# stripe_connector/connector.py
"""
Stripe platform connector.

Implements BasePlatformConnector to parse Stripe webhook payloads
into canonical dataclasses consumed by PlatformAccountingProjection.
"""

import hashlib
import hmac
import logging
import time
from datetime import UTC
from decimal import Decimal

from django.http import HttpRequest

from platform_connectors.base import BasePlatformConnector
from platform_connectors.canonical import (
    ParsedDispute,
    ParsedOrder,
    ParsedPayout,
    ParsedRefund,
    ProviderCapabilities,
)

from .models import StripeAccount

logger = logging.getLogger(__name__)

# Stripe event → canonical topic.
#
# payout.paid is intentionally ABSENT (ADR-0002 S1): the pull/backfill
# (sync.py) is the SOLE settlement emitter — it derives the real fee/net split
# from Balance Transactions and emits PAYMENT_SETTLEMENT_RECEIVED, whereas the
# webhook payout.paid carries no fee split (would post fees=0) and emitting both
# would double-credit clearing. The webhook payload is acknowledged (200) but
# does not post.
STRIPE_TOPIC_MAP = {
    # A140: immediate-capture charges (all normal PaymentIntents / Checkout /
    # Charges-API traffic) fire ONLY charge.succeeded — charge.captured fires
    # solely for auth-then-capture flows. Both map to order_paid; double
    # delivery on a capture flow is safe (emit idempotency key
    # stripe.order_paid:<charge_id> + store_charge's (company,
    # stripe_charge_id) unique constraint dedupe the second event).
    "charge.succeeded": "order_paid",
    "charge.captured": "order_paid",
    "charge.refunded": "refund_created",
    "charge.dispute.created": "dispute_created",
    "charge.dispute.updated": "dispute_created",
}


def verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """
    Verify a Stripe webhook signature (v1 scheme).

    Stripe sends: t=<timestamp>,v1=<signature>[,v1=<signature>...]
    """
    if not sig_header or not secret:
        return False

    try:
        elements = dict(pair.split("=", 1) for pair in sig_header.split(",") if "=" in pair)
        timestamp = elements.get("t", "")
        signatures = [v for k, v in (pair.split("=", 1) for pair in sig_header.split(",") if "=" in pair) if k == "v1"]

        if not timestamp or not signatures:
            return False

        # Reject timestamps older than 5 minutes (replay protection)
        if abs(time.time() - int(timestamp)) > 300:
            return False

        signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
        expected = hmac.new(
            secret.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return any(hmac.compare_digest(expected, sig) for sig in signatures)
    except Exception:
        return False


class StripeConnector(BasePlatformConnector):
    """Stripe platform connector."""

    @property
    def platform_slug(self) -> str:
        return "stripe"

    @property
    def platform_name(self) -> str:
        return "Stripe"

    @property
    def account_roles(self) -> list[str]:
        return [
            "SALES_REVENUE",
            "STRIPE_CLEARING",
            "PAYMENT_PROCESSING_FEES",
            "SALES_TAX_PAYABLE",
            "CASH_BANK",
            "CHARGEBACK_EXPENSE",
            # Settlement-drain roles (PaymentSettlementProjection): EBD is
            # load-bearing — the projection skips the batch if it's unmapped.
            "EXPECTED_BANK_DEPOSIT",
            "SALES_RETURNS",
        ]

    @property
    def webhook_topics(self) -> list[str]:
        return list(STRIPE_TOPIC_MAP.keys())

    @property
    def capabilities(self) -> ProviderCapabilities:
        # Stripe's BalanceTransactions API gives gross/fee/net per txn + the link
        # to payouts; payout.paid alone lacks the fee split, so fees are DERIVED
        # from balance transactions (not given in the payout). See ADR-0002.
        return ProviderCapabilities(
            pull_payouts=True,
            pull_transactions=True,
            payout_line_breakdown=True,
            webhooks=True,
            refunds=True,
            disputes=True,
            dispute_resolution=True,
            reserves=True,
            adjustments=True,
            multi_currency=True,
            fee_in_payout="derived",
            auth="restricted_read_key",
            csv_import=False,
        )

    # get_module_key() inherited from BasePlatformConnector → resolves
    # 'platform_stripe' via module_key_for_provider (ADR-0002 module-key unify).

    def verify_webhook(self, request: HttpRequest) -> bool:
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
        account_id = self._extract_account_id(request)
        if account_id:
            try:
                account = StripeAccount.objects.get(
                    stripe_account_id=account_id,
                    status=StripeAccount.Status.ACTIVE,
                )
                return verify_stripe_signature(request.body, sig_header, account.webhook_secret)
            except StripeAccount.DoesNotExist:
                pass

        # Single-merchant / restricted-key accounts carry no Connect account id,
        # so accept if any active account's webhook secret verifies the request.
        return self._account_by_webhook_signature(request) is not None

    def parse_webhook_topic(self, request: HttpRequest) -> str:
        """Stripe sends event type in the JSON body."""
        import json

        try:
            body = json.loads(request.body)
            return body.get("type", "")
        except (json.JSONDecodeError, AttributeError):
            return ""

    def map_topic_to_canonical(self, topic: str) -> str | None:
        return STRIPE_TOPIC_MAP.get(topic)

    def resolve_company_from_webhook(self, request: HttpRequest):
        """Resolve the company for an incoming Stripe webhook.

        Connect events carry the account id; a single-merchant restricted-key
        account does not, so fall back to the active account whose webhook secret
        verifies the request signature (re-keyed off the Connect-account-id
        assumption, ADR-0002).
        """
        account_id = self._extract_account_id(request)
        if account_id:
            try:
                return (
                    StripeAccount.objects.select_related("company")
                    .get(stripe_account_id=account_id, status=StripeAccount.Status.ACTIVE)
                    .company
                )
            except StripeAccount.DoesNotExist:
                pass

        account = self._account_by_webhook_signature(request)
        return account.company if account else None

    @staticmethod
    def _account_by_webhook_signature(request: HttpRequest):
        """The active StripeAccount whose webhook secret verifies this request's
        signature, or None. Resolves single-merchant accounts that carry no
        Connect account id (pull + signed webhooks, not Connect routing)."""
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
        for account in StripeAccount.objects.filter(status=StripeAccount.Status.ACTIVE).select_related("company"):
            if account.webhook_secret and verify_stripe_signature(request.body, sig_header, account.webhook_secret):
                return account
        return None

    def parse_order(self, payload: dict) -> ParsedOrder:
        """Parse a Stripe charge.captured event → ParsedOrder."""
        obj = payload.get("data", {}).get("object", {})
        amount_cents = obj.get("amount", 0)
        amount = Decimal(amount_cents) / 100

        metadata = obj.get("metadata", {})
        tax_cents = int(metadata.get("tax_amount", 0))
        shipping_cents = int(metadata.get("shipping_amount", 0))
        tax = Decimal(tax_cents) / 100
        shipping = Decimal(shipping_cents) / 100
        subtotal = amount - tax - shipping

        # Stripe sends optional string fields as explicit null; `dict.get(k, "")`
        # returns None for a present-but-null key, so coalesce with `or ""`.
        # billing_details itself can be null too.
        billing = obj.get("billing_details") or {}

        return ParsedOrder(
            platform_order_id=obj.get("id", ""),
            order_number=obj.get("id", ""),
            order_name=obj.get("description") or obj.get("id") or "",
            total_price=amount,
            subtotal=subtotal,
            total_tax=tax,
            total_shipping=shipping,
            total_discounts=Decimal("0"),
            currency=(obj.get("currency") or "usd").upper(),
            financial_status="captured",
            gateway="stripe",
            customer_email=billing.get("email") or obj.get("receipt_email") or "",
            customer_name=billing.get("name") or "",
            order_date=self._ts_to_date(obj.get("created", 0)),
        )

    def parse_refund(self, payload: dict) -> ParsedRefund:
        """Parse a charge.refunded event → ParsedRefund."""
        obj = payload.get("data", {}).get("object", {})
        # charge.refunded: obj is the charge; refunds are nested
        refunds = obj.get("refunds", {}).get("data", [])
        latest = refunds[0] if refunds else {}

        amount_cents = latest.get("amount", obj.get("amount_refunded", 0))
        amount = Decimal(amount_cents) / 100

        return ParsedRefund(
            platform_refund_id=latest.get("id", ""),
            platform_order_id=obj.get("id", ""),
            order_number=obj.get("id", ""),
            amount=amount,
            currency=(obj.get("currency") or "usd").upper(),
            reason=latest.get("reason", ""),
            refund_date=self._ts_to_date(latest.get("created", 0)),
        )

    def parse_payout(self, payload: dict) -> ParsedPayout:
        """Parse a payout.paid event → ParsedPayout.

        SUPERSEDED by the pull (sync.py / normalize.derive_payout_breakdown),
        which derives real fees from Balance Transactions. Retained only for the
        abstract contract; payout.paid is no longer in STRIPE_TOPIC_MAP, so this
        is not on any live path. fees=0 below is therefore inert."""
        obj = payload.get("data", {}).get("object", {})
        amount_cents = obj.get("amount", 0)
        net = Decimal(amount_cents) / 100

        return ParsedPayout(
            platform_payout_id=obj.get("id", ""),
            gross_amount=net,  # Stripe payout = net; fees already deducted
            fees=Decimal("0"),
            net_amount=net,
            currency=(obj.get("currency") or "usd").upper(),
            status=obj.get("status", ""),
            payout_date=self._ts_to_date(obj.get("arrival_date", 0)),
        )

    def parse_dispute(self, payload: dict) -> ParsedDispute | None:
        """Parse a charge.dispute.created event → ParsedDispute."""
        obj = payload.get("data", {}).get("object", {})
        amount_cents = obj.get("amount", 0)

        return ParsedDispute(
            platform_dispute_id=obj.get("id", ""),
            platform_order_id=obj.get("charge", ""),
            order_name=obj.get("charge", ""),
            dispute_amount=Decimal(amount_cents) / 100,
            chargeback_fee=Decimal("1500") / 100,  # Stripe's standard $15 fee
            currency=(obj.get("currency") or "usd").upper(),
            reason=obj.get("reason", ""),
            status=obj.get("status", ""),
            evidence_due_by=self._ts_to_date(obj.get("evidence_details", {}).get("due_by", 0)),
        )

    # ── Local record storage ──────────────────────────────────────

    def store_webhook_record(self, canonical_topic, parsed, payload, company, event_id):
        """Store a local StripeCharge/StripeRefund for reconciliation.

        Payouts are intentionally NOT handled here — the pull (sync.py) owns the
        StripePayout read-model with real derived fees (ADR-0002 S1)."""
        from .commands import store_charge, store_refund

        handler = {
            "order_paid": store_charge,
            "refund_created": store_refund,
        }.get(canonical_topic)

        if handler:
            handler(company, parsed, payload, event_id)

    def on_unhandled_topic(self, *, company, topic, payload):
        """`payout.paid` TRIGGERS the pull (the sole settlement emitter) so it
        picks up the new payout with REAL fees derived from Balance Transactions.

        Hard boundary (ADR-0002 S1): this must NOT emit a settlement event, post
        a JE, or write the settlement read-model — it only enqueues the
        idempotent, debounced backfill. The pull dedupes by payout id, so a
        duplicate payout.paid delivery cannot double-post or double-emit.
        """
        if topic != "payout.paid":
            return
        from .tasks import enqueue_account_sync

        accounts = self._resolve_sync_accounts(company, payload)
        if not accounts:
            # Connection resolution failed — acknowledge safely, log non-secret.
            logger.warning(
                "Stripe payout.paid webhook for company %s: no ACTIVE account to sync; acknowledging.",
                getattr(company, "id", "?"),
            )
            return
        for account in accounts:
            enqueue_account_sync(account.id)

    @staticmethod
    def _resolve_sync_accounts(company, payload):
        """ACTIVE StripeAccount(s) to sync for this payout. A Connect event names
        the account; a restricted-key (single-merchant) one doesn't, so fall back
        to the company's active account(s)."""
        qs = StripeAccount.objects.filter(company=company, status=StripeAccount.Status.ACTIVE)
        account_id = payload.get("account")
        if account_id:
            qs = qs.filter(stripe_account_id=account_id)
        return list(qs)

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _extract_account_id(request: HttpRequest) -> str | None:
        """Extract Stripe Connect account ID from the payload."""
        import json

        try:
            body = json.loads(request.body)
            return body.get("account")
        except Exception:
            return None

    @staticmethod
    def _ts_to_date(ts: int) -> str:
        """Convert Unix timestamp → ISO date string."""
        if not ts:
            return ""
        from datetime import datetime

        return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")
