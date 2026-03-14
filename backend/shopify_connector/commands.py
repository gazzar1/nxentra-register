# shopify_connector/commands.py
"""
Command layer for Shopify connector operations.

Commands enforce business rules and emit events.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime
from decimal import Decimal

import requests
from django.conf import settings
from django.db import transaction

from accounts.authz import ActorContext, require
from accounting.commands import CommandResult
from events.emitter import emit_event
from events.types import EventTypes
from projections.write_barrier import command_writes_allowed

from .models import ShopifyStore, ShopifyOrder, ShopifyRefund
from .event_types import (
    ShopifyStoreConnectedData,
    ShopifyStoreDisconnectedData,
    ShopifyOrderPaidData,
    ShopifyRefundCreatedData,
)


logger = logging.getLogger(__name__)

# Shopify API configuration — set these in Django settings or env vars
SHOPIFY_API_KEY = getattr(settings, "SHOPIFY_API_KEY", "")
SHOPIFY_API_SECRET = getattr(settings, "SHOPIFY_API_SECRET", "")
SHOPIFY_SCOPES = getattr(
    settings, "SHOPIFY_SCOPES",
    "read_orders,read_products,read_inventory",
)
SHOPIFY_APP_URL = getattr(settings, "SHOPIFY_APP_URL", "")

# Required webhooks to register
SHOPIFY_WEBHOOK_TOPICS = [
    "orders/paid",
    "refunds/create",
    "app/uninstalled",
]


# =============================================================================
# OAuth Commands
# =============================================================================

def get_install_url(company, shop_domain: str) -> dict:
    """
    Generate the Shopify OAuth authorization URL.

    Returns {url, nonce} for the frontend to redirect the merchant.
    """
    nonce = secrets.token_urlsafe(32)

    # Create or update the store record
    store, _ = ShopifyStore.objects.update_or_create(
        company=company,
        defaults={
            "shop_domain": shop_domain,
            "oauth_nonce": nonce,
            "status": ShopifyStore.Status.PENDING,
        },
    )

    redirect_uri = f"{SHOPIFY_APP_URL}/api/shopify/callback/"
    url = (
        f"https://{shop_domain}/admin/oauth/authorize"
        f"?client_id={SHOPIFY_API_KEY}"
        f"&scope={SHOPIFY_SCOPES}"
        f"&redirect_uri={redirect_uri}"
        f"&state={nonce}"
    )

    return {"url": url, "nonce": nonce}


@transaction.atomic
def complete_oauth(company, shop_domain: str, code: str, nonce: str) -> CommandResult:
    """
    Exchange the OAuth code for a permanent access token.
    Called from the OAuth callback endpoint.
    """
    try:
        store = ShopifyStore.objects.get(company=company, shop_domain=shop_domain)
    except ShopifyStore.DoesNotExist:
        return CommandResult.fail(f"No pending store for {shop_domain}.")

    if store.oauth_nonce != nonce:
        return CommandResult.fail("OAuth state mismatch — possible CSRF attack.")

    # Exchange code for access token
    token_url = f"https://{shop_domain}/admin/oauth/access_token"
    try:
        resp = requests.post(token_url, json={
            "client_id": SHOPIFY_API_KEY,
            "client_secret": SHOPIFY_API_SECRET,
            "code": code,
        }, timeout=15)
        resp.raise_for_status()
        token_data = resp.json()
    except requests.RequestException as e:
        with command_writes_allowed():
            store.status = ShopifyStore.Status.ERROR
            store.error_message = str(e)
            store.save()
        return CommandResult.fail(f"Failed to exchange OAuth code: {e}")

    access_token = token_data.get("access_token", "")
    scopes = token_data.get("scope", "")

    with command_writes_allowed():
        store.access_token = access_token
        store.scopes = scopes
        store.status = ShopifyStore.Status.ACTIVE
        store.oauth_nonce = ""
        store.error_message = ""
        store.save()

    return CommandResult.ok(data={"store": store})


@transaction.atomic
def register_webhooks(actor: ActorContext, store_id: int) -> CommandResult:
    """Register Shopify webhooks for the connected store."""
    require(actor, "settings.edit")

    try:
        store = ShopifyStore.objects.get(
            company=actor.company, pk=store_id,
        )
    except ShopifyStore.DoesNotExist:
        return CommandResult.fail("Store not found.")

    if store.status != ShopifyStore.Status.ACTIVE:
        return CommandResult.fail("Store is not active.")

    if not store.access_token:
        return CommandResult.fail("No access token — reconnect the store.")

    webhook_url = f"{SHOPIFY_APP_URL}/api/shopify/webhooks/"
    headers = {
        "X-Shopify-Access-Token": store.access_token,
        "Content-Type": "application/json",
    }

    registered = []
    errors = []
    for topic in SHOPIFY_WEBHOOK_TOPICS:
        try:
            resp = requests.post(
                f"https://{store.shop_domain}/admin/api/2026-01/webhooks.json",
                headers=headers,
                json={
                    "webhook": {
                        "topic": topic,
                        "address": webhook_url,
                        "format": "json",
                    }
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                registered.append(topic)
            elif resp.status_code == 422:
                # Already exists — that's fine
                registered.append(topic)
            else:
                errors.append(f"{topic}: {resp.status_code} {resp.text[:200]}")
        except requests.RequestException as e:
            errors.append(f"{topic}: {e}")

    with command_writes_allowed():
        store.webhooks_registered = len(errors) == 0
        store.save()

    if errors:
        logger.warning("Webhook registration errors for %s: %s", store.shop_domain, errors)
        return CommandResult.ok(data={
            "registered": registered,
            "errors": errors,
            "store": store,
        })

    # Emit connection event
    emit_event(
        actor=actor,
        event_type=EventTypes.SHOPIFY_STORE_CONNECTED,
        aggregate_type="ShopifyStore",
        aggregate_id=str(store.public_id),
        idempotency_key=f"shopify.store.connected:{store.public_id}",
        data=ShopifyStoreConnectedData(
            store_public_id=str(store.public_id),
            shop_domain=store.shop_domain,
            company_public_id=str(actor.company.public_id),
            connected_by_email=actor.user.email,
        ),
    )

    return CommandResult.ok(data={
        "registered": registered,
        "store": store,
    })


@transaction.atomic
def disconnect_store(actor: ActorContext) -> CommandResult:
    """Disconnect the Shopify store."""
    require(actor, "settings.edit")

    try:
        store = ShopifyStore.objects.get(company=actor.company)
    except ShopifyStore.DoesNotExist:
        return CommandResult.fail("No connected store.")

    with command_writes_allowed():
        store.status = ShopifyStore.Status.DISCONNECTED
        store.access_token = ""
        store.webhooks_registered = False
        store.save()

    emit_event(
        actor=actor,
        event_type=EventTypes.SHOPIFY_STORE_DISCONNECTED,
        aggregate_type="ShopifyStore",
        aggregate_id=str(store.public_id),
        idempotency_key=f"shopify.store.disconnected:{store.public_id}:{store.updated_at.isoformat()}",
        data=ShopifyStoreDisconnectedData(
            store_public_id=str(store.public_id),
            shop_domain=store.shop_domain,
            company_public_id=str(actor.company.public_id),
            reason="user_initiated",
        ),
    )

    return CommandResult.ok(data={"store": store})


# =============================================================================
# Webhook Processing Commands
# =============================================================================

def verify_webhook_hmac(body: bytes, hmac_header: str) -> bool:
    """Verify the Shopify webhook HMAC-SHA256 signature."""
    if not SHOPIFY_API_SECRET:
        logger.error("SHOPIFY_API_SECRET not configured")
        return False
    computed = hmac.new(
        SHOPIFY_API_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    import base64
    computed_b64 = base64.b64encode(computed).decode("utf-8")
    return hmac.compare_digest(computed_b64, hmac_header)


@transaction.atomic
def process_order_paid(store: ShopifyStore, payload: dict) -> CommandResult:
    """
    Process an orders/paid webhook.
    Creates local ShopifyOrder record and emits event for projection.
    """
    shopify_order_id = payload.get("id")
    if not shopify_order_id:
        return CommandResult.fail("Missing order ID in payload.")

    # Idempotency: skip if already processed
    if ShopifyOrder.objects.filter(
        company=store.company,
        shopify_order_id=shopify_order_id,
    ).exists():
        logger.info("Order %s already exists — skipping", shopify_order_id)
        return CommandResult.ok(data={"skipped": True})

    # Parse order data
    order_date_str = payload.get("created_at", "")
    try:
        order_date = datetime.fromisoformat(order_date_str.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        order_date = datetime.now().date()

    total_price = Decimal(str(payload.get("total_price", "0")))
    subtotal_price = Decimal(str(payload.get("subtotal_price", "0")))
    total_tax = Decimal(str(payload.get("total_tax", "0")))
    total_discounts = Decimal(str(payload.get("total_discounts", "0")))
    currency = payload.get("currency", "USD")

    # Calculate total shipping from shipping_lines
    total_shipping = Decimal("0")
    for sl in payload.get("shipping_lines", []):
        total_shipping += Decimal(str(sl.get("price", "0")))

    with command_writes_allowed():
        order = ShopifyOrder.objects.create(
            company=store.company,
            store=store,
            shopify_order_id=shopify_order_id,
            shopify_order_number=str(payload.get("order_number", "")),
            shopify_order_name=payload.get("name", ""),
            total_price=total_price,
            subtotal_price=subtotal_price,
            total_tax=total_tax,
            total_discounts=total_discounts,
            currency=currency,
            financial_status=payload.get("financial_status", ""),
            gateway=_extract_gateway(payload),
            shopify_created_at=order_date_str or datetime.now().isoformat(),
            order_date=order_date,
            raw_payload=payload,
        )

    # Build line items summary
    line_items = []
    for item in payload.get("line_items", []):
        line_items.append({
            "title": item.get("title", ""),
            "quantity": item.get("quantity", 1),
            "price": str(item.get("price", "0")),
            "sku": item.get("sku", ""),
        })

    # Extract customer info
    customer = payload.get("customer", {})

    # Emit event for projection
    from events.emitter import emit_event_no_actor
    event = emit_event_no_actor(
        company=store.company,
        event_type=EventTypes.SHOPIFY_ORDER_PAID,
        aggregate_type="ShopifyOrder",
        aggregate_id=str(order.public_id),
        idempotency_key=f"shopify.order.paid:{shopify_order_id}",
        metadata={"source": "shopify_webhook", "shop_domain": store.shop_domain},
        data=ShopifyOrderPaidData(
            amount=str(total_price),
            currency=currency,
            transaction_date=str(order_date),
            document_ref=order.shopify_order_name,
            store_public_id=str(store.public_id),
            shopify_order_id=str(shopify_order_id),
            order_number=str(payload.get("order_number", "")),
            order_name=order.shopify_order_name,
            subtotal=str(subtotal_price),
            total_tax=str(total_tax),
            total_shipping=str(total_shipping),
            total_discounts=str(total_discounts),
            financial_status=payload.get("financial_status", ""),
            gateway=order.gateway,
            line_items=line_items,
            customer_email=customer.get("email", ""),
            customer_name=f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip(),
        ),
    )

    with command_writes_allowed():
        order.event_id = event.id if event else None
        order.save(update_fields=["event_id"])

    return CommandResult.ok(data={"order": order, "event": event})


@transaction.atomic
def process_refund(store: ShopifyStore, payload: dict) -> CommandResult:
    """Process a refunds/create webhook."""
    shopify_refund_id = payload.get("id")
    order_id = payload.get("order_id")

    if not shopify_refund_id or not order_id:
        return CommandResult.fail("Missing refund or order ID.")

    if ShopifyRefund.objects.filter(
        company=store.company,
        shopify_refund_id=shopify_refund_id,
    ).exists():
        return CommandResult.ok(data={"skipped": True})

    try:
        order = ShopifyOrder.objects.get(
            company=store.company,
            shopify_order_id=order_id,
        )
    except ShopifyOrder.DoesNotExist:
        return CommandResult.fail(f"Order {order_id} not found locally.")

    # Calculate refund amount from transactions
    refund_amount = Decimal("0")
    for transaction in payload.get("transactions", []):
        if transaction.get("kind") == "refund" and transaction.get("status") == "success":
            refund_amount += Decimal(str(transaction.get("amount", "0")))

    # Fallback: sum refund line items
    if refund_amount == 0:
        for line in payload.get("refund_line_items", []):
            refund_amount += Decimal(str(line.get("subtotal", "0")))

    if refund_amount <= 0:
        return CommandResult.fail("Refund amount is zero or negative.")

    refund_date_str = payload.get("created_at", "")
    try:
        refund_date = datetime.fromisoformat(refund_date_str.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        refund_date = datetime.now().date()

    with command_writes_allowed():
        refund = ShopifyRefund.objects.create(
            company=store.company,
            order=order,
            shopify_refund_id=shopify_refund_id,
            amount=refund_amount,
            currency=order.currency,
            reason=payload.get("note", ""),
            shopify_created_at=refund_date_str or datetime.now().isoformat(),
            raw_payload=payload,
        )

    from events.emitter import emit_event_no_actor
    event = emit_event_no_actor(
        company=store.company,
        event_type=EventTypes.SHOPIFY_REFUND_CREATED,
        aggregate_type="ShopifyRefund",
        aggregate_id=str(refund.public_id),
        idempotency_key=f"shopify.refund.created:{shopify_refund_id}",
        metadata={"source": "shopify_webhook", "shop_domain": store.shop_domain},
        data=ShopifyRefundCreatedData(
            amount=str(refund_amount),
            currency=order.currency,
            transaction_date=str(refund_date),
            document_ref=order.shopify_order_name,
            store_public_id=str(store.public_id),
            shopify_refund_id=str(shopify_refund_id),
            shopify_order_id=str(order_id),
            order_number=order.shopify_order_number,
            reason=refund.reason,
        ),
    )

    with command_writes_allowed():
        refund.event_id = event.id if event else None
        refund.save(update_fields=["event_id"])

    return CommandResult.ok(data={"refund": refund, "event": event})


@transaction.atomic
def process_app_uninstalled(store: ShopifyStore, payload: dict) -> CommandResult:
    """Handle app/uninstalled webhook — mark store as disconnected."""
    with command_writes_allowed():
        store.status = ShopifyStore.Status.DISCONNECTED
        store.access_token = ""
        store.webhooks_registered = False
        store.error_message = "App uninstalled by merchant"
        store.save()

    from events.emitter import emit_event_no_actor
    emit_event_no_actor(
        company=store.company,
        event_type=EventTypes.SHOPIFY_STORE_DISCONNECTED,
        aggregate_type="ShopifyStore",
        aggregate_id=str(store.public_id),
        idempotency_key=f"shopify.store.uninstalled:{store.public_id}:{store.updated_at.isoformat()}",
        data=ShopifyStoreDisconnectedData(
            store_public_id=str(store.public_id),
            shop_domain=store.shop_domain,
            company_public_id=str(store.company.public_id),
            reason="app_uninstalled",
        ),
    )

    return CommandResult.ok(data={"store": store})


# =============================================================================
# Helpers
# =============================================================================

def _extract_gateway(payload: dict) -> str:
    """Extract payment gateway from order payload."""
    gateways = payload.get("payment_gateway_names", [])
    if gateways:
        return gateways[0]
    return payload.get("gateway", "")
