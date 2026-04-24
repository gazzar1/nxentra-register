# stripe_connector/connector.py
"""
Stripe platform connector.

Implements BasePlatformConnector to parse Stripe webhook payloads
into canonical dataclasses consumed by PlatformAccountingProjection.
"""

import hashlib
import hmac
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
)

from .models import StripeAccount

# Stripe event → canonical topic
STRIPE_TOPIC_MAP = {
    "charge.captured": "order_paid",
    "charge.refunded": "refund_created",
    "payout.paid": "payout_settled",
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
        ]

    @property
    def webhook_topics(self) -> list[str]:
        return list(STRIPE_TOPIC_MAP.keys())

    def get_module_key(self) -> str:
        return "stripe_connector"

    def verify_webhook(self, request: HttpRequest) -> bool:
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
        # Look up the webhook secret from the account
        # For the generic handler, we accept if any account's secret matches
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

        # Fallback: try all active accounts
        for account in StripeAccount.objects.filter(status=StripeAccount.Status.ACTIVE):
            if verify_stripe_signature(request.body, sig_header, account.webhook_secret):
                return True

        return False

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
        """Resolve company from the Stripe Connect account ID."""
        account_id = self._extract_account_id(request)
        if not account_id:
            return None

        try:
            account = StripeAccount.objects.select_related("company").get(
                stripe_account_id=account_id,
                status=StripeAccount.Status.ACTIVE,
            )
            return account.company
        except StripeAccount.DoesNotExist:
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

        billing = obj.get("billing_details", {})

        return ParsedOrder(
            platform_order_id=obj.get("id", ""),
            order_number=obj.get("id", ""),
            order_name=obj.get("description", obj.get("id", "")),
            total_price=amount,
            subtotal=subtotal,
            total_tax=tax,
            total_shipping=shipping,
            total_discounts=Decimal("0"),
            currency=(obj.get("currency") or "usd").upper(),
            financial_status="captured",
            gateway="stripe",
            customer_email=billing.get("email") or obj.get("receipt_email", ""),
            customer_name=billing.get("name", ""),
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
        """Parse a payout.paid event → ParsedPayout."""
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
        """Store a local StripeCharge/StripeRefund/StripePayout for reconciliation."""
        from .commands import store_charge, store_payout, store_refund

        handler = {
            "order_paid": store_charge,
            "refund_created": store_refund,
            "payout_settled": store_payout,
        }.get(canonical_topic)

        if handler:
            handler(company, parsed, payload, event_id)

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
