# platform_connectors/views.py
"""
Generic webhook endpoint for all platform connectors.

POST /api/platforms/<slug>/webhooks/

The view looks up the connector from the registry, verifies the webhook,
parses the topic, and dispatches to the appropriate parse method.
The parsed canonical data is then emitted as a PLATFORM_* event.
"""

import json
import logging

from django.http import HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from events.emitter import emit_event_no_actor
from events.types import (
    EventTypes,
    PlatformDisputeCreatedData,
    PlatformFulfillmentCreatedData,
    PlatformOrderPaidData,
    PlatformPayoutSettledData,
    PlatformRefundCreatedData,
)

from .registry import connector_registry
from .throttles import PlatformWebhookThrottle

logger = logging.getLogger(__name__)

# Maps canonical topic categories to (event_type, data_class, parse_method)
TOPIC_HANDLERS = {
    "order_paid": (
        EventTypes.PLATFORM_ORDER_PAID,
        PlatformOrderPaidData,
        "parse_order",
    ),
    "refund_created": (
        EventTypes.PLATFORM_REFUND_CREATED,
        PlatformRefundCreatedData,
        "parse_refund",
    ),
    "payout_settled": (
        EventTypes.PLATFORM_PAYOUT_SETTLED,
        PlatformPayoutSettledData,
        "parse_payout",
    ),
    "dispute_created": (
        EventTypes.PLATFORM_DISPUTE_CREATED,
        PlatformDisputeCreatedData,
        "parse_dispute",
    ),
    "fulfillment_created": (
        EventTypes.PLATFORM_FULFILLMENT_CREATED,
        PlatformFulfillmentCreatedData,
        "parse_fulfillment",
    ),
}


def _required_capability(platform_slug: str, canonical_topic: str | None):
    """A4: the capability required to PROCESS a webhook for ``platform_slug`` /
    ``canonical_topic``, or ``None`` when the delivery is in scope.

    Per-platform, per-topic — not a blanket block:

      - Stripe is entirely out of scope, for every topic (call with
        ``canonical_topic=None`` to block it early, before ``on_unhandled_topic``
        would enqueue its payout pull);
      - Shopify payout settlement → ``SHOPIFY_PAYOUT_ACCOUNTING``; Shopify
        disputes → ``SHOPIFY_DISPUTES``; Shopify order/refund/fulfillment
        accounting remains available (returns ``None``);
      - any other platform is in scope by default.
    """
    from accounts.pilot_policy import Capability

    if platform_slug == "stripe":
        return Capability.STRIPE
    if platform_slug == "shopify":
        return {
            "payout_settled": Capability.SHOPIFY_PAYOUT_ACCOUNTING,
            "dispute_created": Capability.SHOPIFY_DISPUTES,
        }.get(canonical_topic or "")
    return None


class PlatformWebhookView(APIView):
    """
    POST /api/platforms/<slug>/webhooks/

    Generic webhook receiver for any registered platform connector.
    No authentication — platforms send these directly with their own
    verification mechanisms (HMAC, signing secrets, etc.).

    F3: dedicated webhook throttle scope — NOT the shared anon bucket. Keep
    the throttled view set in lockstep with the runbook's §I4 enumeration.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PlatformWebhookThrottle]

    def post(self, request, platform_slug):
        connector = connector_registry.get(platform_slug)
        if not connector:
            logger.warning("Webhook received for unknown platform: %s", platform_slug)
            return HttpResponse(status=404)

        # Step 1: Verify webhook authenticity
        if not connector.verify_webhook(request):
            logger.warning("Webhook verification failed for %s", platform_slug)
            return HttpResponse(status=401)

        # Step 2: Resolve company from the webhook
        company = connector.resolve_company_from_webhook(request)
        if not company:
            logger.warning("Could not resolve company from %s webhook", platform_slug)
            return HttpResponse(status=200)  # Acknowledge but skip

        # A4: constrained-pilot scope. Acknowledge the delivery (so the provider
        # does not retry-storm) but emit NO canonical event and take NO side
        # effect for an out-of-scope platform/topic — never silently process the
        # financial payload. Not A5's full terminal-state model.
        #
        # A fully out-of-scope platform (Stripe) is blocked HERE, before
        # `on_unhandled_topic` runs — Stripe enqueues its payout pull there, which
        # must not fire for a pilot company. Per-topic Shopify blocks (payout,
        # dispute) are applied below, once the canonical topic is known, so
        # Shopify order/refund/fulfillment accounting stays in scope.
        from accounts.pilot_policy import skip_if_unsupported

        platform_cap = _required_capability(platform_slug, None)
        if (
            platform_cap is not None
            and skip_if_unsupported(company, platform_cap, task=f"platform_webhook:{platform_slug}") is not None
        ):
            return HttpResponse(status=200)

        # Step 3: Parse topic
        topic = connector.parse_webhook_topic(request)
        if not topic:
            logger.warning("No topic in %s webhook", platform_slug)
            return HttpResponse(status=400)

        # Step 4: Parse body
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            logger.error("Invalid JSON in %s webhook body", platform_slug)
            return HttpResponse(status=400)

        # Step 5: Map topic to canonical handler
        canonical_topic = (
            connector.map_topic_to_canonical(topic) if hasattr(connector, "map_topic_to_canonical") else None
        )

        # A4: per-topic pilot scope (e.g. Shopify payout / dispute). Acknowledge
        # but process nothing — no event, no `on_unhandled_topic` side effect.
        topic_cap = _required_capability(platform_slug, canonical_topic)
        if (
            topic_cap is not None
            and skip_if_unsupported(company, topic_cap, task=f"platform_webhook:{platform_slug}:{canonical_topic}")
            is not None
        ):
            return HttpResponse(status=200)

        if not canonical_topic or canonical_topic not in TOPIC_HANDLERS:
            # Not a JE-posting topic. Give the connector a chance to react
            # WITHOUT touching the ledger — e.g. Stripe enqueues its pull on
            # payout.paid (the pull is the sole settlement emitter). Never let
            # this fail the webhook ack.
            try:
                connector.on_unhandled_topic(company=company, topic=topic, payload=payload)
            except Exception:
                logger.exception("Error in %s on_unhandled_topic for %s", platform_slug, topic)
            logger.info("Unhandled %s webhook topic: %s", platform_slug, topic)
            return HttpResponse(status=200)

        event_type, data_class, parse_method = TOPIC_HANDLERS[canonical_topic]

        # Step 6: Parse platform payload → canonical dataclass
        parser = getattr(connector, parse_method, None)
        if not parser:
            logger.warning("Connector %s has no %s method", platform_slug, parse_method)
            return HttpResponse(status=200)

        try:
            parsed = parser(payload)
        except Exception:
            logger.exception("Error parsing %s webhook topic %s", platform_slug, topic)
            return HttpResponse(status=500)

        if parsed is None:
            # Connector chose to skip (e.g. optional dispute handler)
            return HttpResponse(status=200)

        # Step 7: emit the canonical event, SERIALIZED against pilot activation for
        # capability-GATED topics — the capability is re-checked on the LOCKED
        # profile before the emit, so a delivery admitted on a cached NONE profile
        # cannot emit after a concurrent activation. For an in-scope topic
        # (topic_cap None) no capability can be raced, so no lock is taken.
        #
        # Only the EMIT is wrapped. Step 8 (store_webhook_record) stays
        # AUTOCOMMIT-separate below, exactly as before: some connector handlers
        # (e.g. Stripe store_refund) recover from IntegrityError WITHOUT a
        # savepoint, which is only safe in autocommit — running them inside this
        # admission transaction would poison the connection and silently roll back
        # the just-emitted event. Serializing the emit alone is sufficient (the
        # canonical event is the raceable fact; the connector record is its
        # derived read-model, keyed by the committed event_id).
        from contextlib import nullcontext

        from accounts.pilot_policy import is_supported, serialized_company_admission

        emit_ctx = serialized_company_admission(company.pk) if topic_cap is not None else nullcontext(company)
        try:
            with emit_ctx as emit_company:
                if topic_cap is not None and not is_supported(emit_company, topic_cap):
                    # A concurrent activation landed between the unlocked topic skip
                    # above and here — acknowledge, emit nothing.
                    return HttpResponse(status=200)

                event_data = _canonical_to_event_data(parsed, data_class, platform_slug)
                aggregate_id = _extract_aggregate_id(parsed)
                business_event = emit_event_no_actor(
                    company=emit_company,
                    event_type=event_type,
                    aggregate_type=f"Platform{canonical_topic.split('_')[0].title()}",
                    aggregate_id=aggregate_id,
                    idempotency_key=f"{platform_slug}.{canonical_topic}:{aggregate_id}",
                    data=event_data,
                )
            logger.info(
                "Emitted %s for %s (company=%s, id=%s)",
                event_type,
                platform_slug,
                company,
                aggregate_id,
            )
        except Exception:
            logger.exception("Error emitting event for %s webhook %s", platform_slug, topic)
            return HttpResponse(status=500)

        # Step 8: Store platform-specific local record for reconciliation —
        # OUTSIDE the admission transaction (autocommit, as before).
        event_id = getattr(business_event, "public_id", None)
        try:
            connector.store_webhook_record(
                canonical_topic=canonical_topic,
                parsed=parsed,
                payload=payload,
                company=company,
                event_id=event_id,
            )
        except Exception:
            logger.exception(
                "Error storing local record for %s webhook %s (event emitted OK)",
                platform_slug,
                topic,
            )
            # Don't fail the webhook — event was already emitted successfully

        return HttpResponse(status=200)


def _canonical_to_event_data(parsed, data_class, platform_slug):
    """Convert a canonical parsed object to the matching event data class."""
    from dataclasses import asdict
    from dataclasses import fields as dc_fields

    # Build kwargs from the parsed object's fields
    parsed_dict = asdict(parsed) if hasattr(parsed, "__dataclass_fields__") else {}
    kwargs = {"platform_slug": platform_slug}

    # Map canonical fields to event data fields
    for f in dc_fields(data_class):
        if f.name == "platform_slug":
            continue
        if f.name in parsed_dict:
            val = parsed_dict[f.name]
            # List/dict fields (e.g. line_items) must keep their native type —
            # the event schema validates line_items as a list. Only scalar fields
            # (Decimal/date/str) are stringified for the payload. Without this a
            # Stripe charge's empty line_items[] became the string "[]" and the
            # platform.order_paid validator raised InvalidEventPayload (the
            # webhook 500'd before the charge could be stored).
            kwargs[f.name] = val if isinstance(val, list | dict) else str(val)
        elif f.name == "amount" and "total_price" in parsed_dict:
            kwargs["amount"] = str(parsed_dict["total_price"])
        elif f.name == "amount" and "amount" in parsed_dict:
            kwargs["amount"] = str(parsed_dict["amount"])
        elif f.name == "transaction_date":
            # Try common date fields
            for date_field in ("order_date", "refund_date", "payout_date", "fulfillment_date"):
                if parsed_dict.get(date_field):
                    kwargs["transaction_date"] = str(parsed_dict[date_field])
                    break
        elif f.name == "currency" and "currency" in parsed_dict:
            kwargs["currency"] = parsed_dict["currency"]
        elif f.name == "document_ref":
            # Use the platform-specific ID as document_ref
            for ref_field in (
                "platform_order_id",
                "platform_refund_id",
                "platform_payout_id",
                "platform_dispute_id",
                "platform_fulfillment_id",
            ):
                if parsed_dict.get(ref_field):
                    kwargs["document_ref"] = str(parsed_dict[ref_field])
                    break

    return data_class(**kwargs)


def _extract_aggregate_id(parsed) -> str:
    """Extract a stable ID from a parsed canonical object."""
    for attr in (
        "platform_order_id",
        "platform_refund_id",
        "platform_payout_id",
        "platform_dispute_id",
        "platform_fulfillment_id",
    ):
        val = getattr(parsed, attr, None)
        if val:
            return str(val)
    return "unknown"
