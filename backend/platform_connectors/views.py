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


class PlatformWebhookView(APIView):
    """
    POST /api/platforms/<slug>/webhooks/

    Generic webhook receiver for any registered platform connector.
    No authentication — platforms send these directly with their own
    verification mechanisms (HMAC, signing secrets, etc.).
    """

    permission_classes = [AllowAny]
    authentication_classes = []

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

        if not canonical_topic or canonical_topic not in TOPIC_HANDLERS:
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

        # Step 7: Convert parsed canonical to event data and emit
        try:
            event_data = _canonical_to_event_data(parsed, data_class, platform_slug)
            aggregate_id = _extract_aggregate_id(parsed)

            business_event = emit_event_no_actor(
                company=company,
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

        # Step 8: Store platform-specific local record for reconciliation
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
            kwargs[f.name] = str(parsed_dict[f.name])
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
