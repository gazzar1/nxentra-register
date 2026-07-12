# shopify_connector/gdpr.py
"""
A124 — GDPR export + redaction jobs for the three Shopify-mandatory
webhooks. Before this module, the handlers only wrote PENDING
GdprRequest audit rows while the published app promised 30/90-day SLAs;
PII persisted indefinitely in ShopifyOrder/Fulfillment/Refund
raw_payloads, GdprRequest payloads, and merchant-entered Customer rows.

Policy (owner decision, 2026-07-11): scrub every MUTABLE store; the
append-only BusinessEvent ledger keeps a documented lawful-basis
retention exception — events are immutable with SHA-256 payload hashes,
so rewriting them would break integrity verification and replay
determinism. Matching events are COUNTED into evidence
(events_exempted), never rewritten.

All jobs are idempotent (re-runnable over already-scrubbed data),
cross-tenant (GdprRequest has no company FK; shoppers can span
companies sharing a shop_domain), and loud on failure (status=FAILED +
processing_notes; the beat catch-up retries).
"""

import logging

from django.utils import timezone

from accounts.rls import rls_bypass
from events.emitter import emit_event_no_actor
from events.types import EventTypes
from projections.write_barrier import command_writes_allowed

from .event_types import ShopifyGdprRequestCompletedData
from .models import GdprRequest, PendingShopifyInstall, ShopifyFulfillment, ShopifyOrder, ShopifyRefund, ShopifyStore

logger = logging.getLogger(__name__)

REDACTED_SENTINEL = "_gdpr_redacted"

# Payload subtrees that carry shopper PII. Financial/id fields
# (id, amounts, financial_status, line_items) stay — projections and the
# reconciliation joins depend on them.
_ORDER_PII_KEYS = (
    "customer",
    "email",
    "contact_email",
    "phone",
    "billing_address",
    "shipping_address",
    "note_attributes",
)
_FULFILLMENT_PII_KEYS = ("destination", "receipt", "email")
_REFUND_PII_KEYS = ("order_adjustments_note", "note", "user_id")


def _scrub_payload(payload: dict, keys: tuple) -> bool:
    """Replace PII subtrees with a sentinel. Returns True if anything changed.
    Idempotent: already-scrubbed payloads change nothing."""
    if not isinstance(payload, dict):
        return False
    changed = False
    for key in keys:
        if key in payload and payload[key] is not None and payload[key] != REDACTED_SENTINEL:
            payload[key] = REDACTED_SENTINEL
            changed = True
    if changed:
        payload[REDACTED_SENTINEL] = True
    return changed


def _matching_orders(store, customer_id, customer_email, order_ids):
    """Orders belonging to the shopper: by explicit Shopify order ids when
    the webhook names them, else by customer id/email inside raw_payload."""
    qs = ShopifyOrder.objects.filter(company=store.company, store=store)
    if order_ids:
        return list(qs.filter(shopify_order_id__in=order_ids))

    matched = []
    for order in qs.iterator(chunk_size=500):
        customer = (order.raw_payload or {}).get("customer")
        if not isinstance(customer, dict):
            continue
        if (customer_id and str(customer.get("id")) == str(customer_id)) or (
            customer_email and (customer.get("email") or "").lower() == customer_email.lower()
        ):
            matched.append(order)
    return matched


def _count_exempt_events(company, customer_email) -> int:
    """Lawful-basis exception: count (never rewrite) immutable events that
    carry the shopper's email."""
    from events.models import BusinessEvent

    if not customer_email:
        return 0
    return BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.SHOPIFY_ORDER_PAID,
        data__customer_email=customer_email,
    ).count()


def _stores_for_domain(shop_domain):
    """ALL statuses: shop/redact fires ~48h after uninstall, when the store
    is DISCONNECTED, and multiple companies can hold rows for one domain."""
    return list(ShopifyStore.objects.filter(shop_domain=shop_domain).select_related("company"))


def _emit_completed(req, company, *, orders_matched, records_scrubbed, events_exempted):
    emit_event_no_actor(
        company=company,
        user=None,
        event_type=EventTypes.SHOPIFY_GDPR_REQUEST_COMPLETED,
        aggregate_type="GdprRequest",
        aggregate_id=str(req.id),
        idempotency_key=f"shopify.gdpr:{req.topic}:{req.payload_signature}:{company.public_id}",
        data=ShopifyGdprRequestCompletedData(
            gdpr_request_id=req.id,
            topic=req.topic,
            shop_domain=req.shop_domain,
            customer_id=str(req.customer_id or ""),
            orders_matched=orders_matched,
            records_scrubbed=records_scrubbed,
            events_exempted=events_exempted,
            completed_at=timezone.now().isoformat(),
        ),
    )


def _complete(req, evidence: dict, note: str = ""):
    with command_writes_allowed():
        req.status = GdprRequest.Status.COMPLETED
        req.processed_at = timezone.now()
        req.evidence = evidence
        req.processing_notes = note or "Processed by A124 GDPR job."
        req.save(update_fields=["status", "processed_at", "evidence", "processing_notes"])


def _fail(req, error: str):
    with command_writes_allowed():
        req.status = GdprRequest.Status.FAILED
        req.processed_at = timezone.now()
        req.processing_notes = str(error)[:2000]
        req.save(update_fields=["status", "processed_at", "processing_notes"])


def execute_customer_data_request(req: GdprRequest) -> dict:
    """customers/data_request → assemble the shopper's data for the
    merchant (the controller) to serve within the 30-day SLA. The export
    lands in req.evidence and company admins are notified."""
    from accounts.models import Notification

    payload = req.payload or {}
    order_ids = payload.get("orders_requested") or []
    evidence = {"companies_matched": 0, "orders_matched": 0, "export": []}

    with rls_bypass():
        for store in _stores_for_domain(req.shop_domain):
            orders = _matching_orders(store, req.customer_id, req.customer_email, order_ids)
            if not orders and not order_ids:
                continue
            evidence["companies_matched"] += 1
            evidence["orders_matched"] += len(orders)
            for order in orders:
                raw = order.raw_payload or {}
                evidence["export"].append(
                    {
                        "company": str(store.company.public_id),
                        "order_id": order.shopify_order_id,
                        "order_name": order.shopify_order_name,
                        "order_date": order.order_date.isoformat() if order.order_date else "",
                        "total_price": str(order.total_price),
                        "currency": order.currency,
                        "customer": raw.get("customer"),
                        "billing_address": raw.get("billing_address"),
                        "shipping_address": raw.get("shipping_address"),
                    }
                )
            with command_writes_allowed():
                Notification.notify_company_admins(
                    store.company,
                    title="GDPR data request received",
                    message=(
                        f"Shopify forwarded a customer data request for {req.shop_domain} "
                        f"(customer {req.customer_id or req.customer_email}). The export is "
                        f"attached to GDPR request #{req.id}; you must provide it to the "
                        f"customer within 30 days."
                    ),
                    level="WARNING",
                    source_module="shopify_connector",
                )
            _emit_completed(
                req,
                store.company,
                orders_matched=len(orders),
                records_scrubbed=0,
                events_exempted=0,
            )

    _complete(req, evidence, note=f"Export assembled for {evidence['orders_matched']} order(s).")
    return evidence


def execute_customer_redact(req: GdprRequest) -> dict:
    """customers/redact → scrub the shopper's PII from every mutable store."""
    payload = req.payload or {}
    order_ids = payload.get("orders_to_redact") or []
    evidence = {
        "companies_matched": 0,
        "orders_matched": 0,
        "records_scrubbed": 0,
        "events_exempted": 0,
        "policy": "mutable stores scrubbed; append-only event ledger retained under documented lawful basis",
    }

    with rls_bypass():
        for store in _stores_for_domain(req.shop_domain):
            orders = _matching_orders(store, req.customer_id, req.customer_email, order_ids)
            if not orders:
                continue
            evidence["companies_matched"] += 1
            evidence["orders_matched"] += len(orders)

            with command_writes_allowed():
                for order in orders:
                    raw = order.raw_payload or {}
                    if _scrub_payload(raw, _ORDER_PII_KEYS):
                        order.raw_payload = raw
                        order.save(update_fields=["raw_payload"])
                        evidence["records_scrubbed"] += 1

                    for fulfillment in ShopifyFulfillment.objects.filter(company=store.company, order=order):
                        fraw = fulfillment.raw_payload or {}
                        if _scrub_payload(fraw, _FULFILLMENT_PII_KEYS):
                            fulfillment.raw_payload = fraw
                            fulfillment.save(update_fields=["raw_payload"])
                            evidence["records_scrubbed"] += 1

                    for refund in ShopifyRefund.objects.filter(company=store.company, order=order):
                        rraw = refund.raw_payload or {}
                        if _scrub_payload(rraw, _REFUND_PII_KEYS):
                            refund.raw_payload = rraw
                            refund.save(update_fields=["raw_payload"])
                            evidence["records_scrubbed"] += 1

                # Best-effort: a merchant-entered accounting Customer whose
                # email exactly matches the shopper (rare; the Shopify
                # pipeline books against one aggregate customer per store).
                if req.customer_email:
                    from accounting.models import Customer

                    for cust in Customer.objects.filter(company=store.company, email__iexact=req.customer_email):
                        cust.name = f"Redacted customer #{cust.id}"
                        cust.email = ""
                        cust.phone = ""
                        cust.address = ""
                        cust.notes = ""
                        cust.save(update_fields=["name", "email", "phone", "address", "notes"])
                        evidence["records_scrubbed"] += 1

            exempted = _count_exempt_events(store.company, req.customer_email)
            evidence["events_exempted"] += exempted
            _emit_completed(
                req,
                store.company,
                orders_matched=len(orders),
                records_scrubbed=evidence["records_scrubbed"],
                events_exempted=exempted,
            )

        # Scrub the shopper's PII from earlier GDPR audit rows for the same
        # customer (the GDPR payload itself carries email + phone). Keep
        # payload_signature — it is the idempotency key.
        with command_writes_allowed():
            for older in GdprRequest.objects.filter(shop_domain=req.shop_domain, customer_id=req.customer_id).exclude(
                pk=req.pk
            ):
                changed = _scrub_payload(older.payload or {}, ("customer",))
                if older.customer_email or changed:
                    older.customer_email = ""
                    older.save(update_fields=["customer_email", "payload"])

    _complete(
        req,
        evidence,
        note=(
            f"Scrubbed {evidence['records_scrubbed']} record(s) across "
            f"{evidence['companies_matched']} company(ies); "
            f"{evidence['events_exempted']} immutable event(s) retained under lawful basis."
        ),
    )
    return evidence


def execute_shop_redact(req: GdprRequest) -> dict:
    """shop/redact → scrub Shopify-sourced PII + credentials for every
    store row matching the domain (any status). The merchant company's own
    ledger stays — merchant-as-controller bookkeeping with a legal
    retention basis; full company deletion is a separate owner-authorized
    operation."""
    evidence = {
        "companies_matched": 0,
        "records_scrubbed": 0,
        "pending_installs_deleted": 0,
        "policy": "store PII + credentials scrubbed; merchant ledger retained (controller's books)",
    }

    with rls_bypass():
        for store in _stores_for_domain(req.shop_domain):
            evidence["companies_matched"] += 1
            company_scrubbed = 0
            with command_writes_allowed():
                # Defensive credential blank (uninstall usually did this).
                store.access_token = ""
                store.refresh_token = ""
                store.save(update_fields=["access_token", "refresh_token"])

                for order in ShopifyOrder.objects.filter(company=store.company, store=store).iterator(chunk_size=500):
                    raw = order.raw_payload or {}
                    if _scrub_payload(raw, _ORDER_PII_KEYS):
                        order.raw_payload = raw
                        order.save(update_fields=["raw_payload"])
                        company_scrubbed += 1

                for fulfillment in ShopifyFulfillment.objects.filter(company=store.company).iterator(chunk_size=500):
                    fraw = fulfillment.raw_payload or {}
                    if _scrub_payload(fraw, _FULFILLMENT_PII_KEYS):
                        fulfillment.raw_payload = fraw
                        fulfillment.save(update_fields=["raw_payload"])
                        company_scrubbed += 1

                for refund in ShopifyRefund.objects.filter(company=store.company).iterator(chunk_size=500):
                    rraw = refund.raw_payload or {}
                    if _scrub_payload(rraw, _REFUND_PII_KEYS):
                        refund.raw_payload = rraw
                        refund.save(update_fields=["raw_payload"])
                        company_scrubbed += 1

            evidence["records_scrubbed"] += company_scrubbed
            _emit_completed(
                req,
                store.company,
                orders_matched=0,
                records_scrubbed=company_scrubbed,
                events_exempted=0,
            )

        with command_writes_allowed():
            deleted, _ = PendingShopifyInstall.objects.filter(shop_domain=req.shop_domain).delete()
            evidence["pending_installs_deleted"] = deleted

    _complete(
        req,
        evidence,
        note=(
            f"Shop redacted: {evidence['records_scrubbed']} record(s) across "
            f"{evidence['companies_matched']} company(ies), "
            f"{evidence['pending_installs_deleted']} pending install(s) deleted."
        ),
    )
    return evidence


_EXECUTORS = {
    GdprRequest.Topic.CUSTOMERS_DATA_REQUEST: execute_customer_data_request,
    GdprRequest.Topic.CUSTOMERS_REDACT: execute_customer_redact,
    GdprRequest.Topic.SHOP_REDACT: execute_shop_redact,
}


def process_gdpr_request(req: GdprRequest) -> bool:
    """Run the executor for one PENDING request. Loud on failure
    (status=FAILED + notes) — the beat catch-up retries FAILED rows only
    after the operator investigates; PENDING rows retry automatically."""
    executor = _EXECUTORS.get(req.topic)
    if executor is None:
        _fail(req, f"No executor for topic {req.topic!r}")
        return False
    try:
        executor(req)
        return True
    except Exception as exc:
        logger.exception("GDPR job failed for request %s (%s)", req.id, req.topic)
        _fail(req, f"{type(exc).__name__}: {exc}")
        return False
