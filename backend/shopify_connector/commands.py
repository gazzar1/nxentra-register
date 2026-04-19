# shopify_connector/commands.py
"""
Command layer for Shopify connector operations.

Commands enforce business rules and emit events.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime
from decimal import Decimal

import requests
from django.conf import settings
from django.db import IntegrityError, transaction

from accounting.commands import CommandResult
from accounts.authz import ActorContext, require
from events.emitter import emit_event
from events.types import EventTypes
from projections.write_barrier import command_writes_allowed, projection_writes_allowed

from .event_types import (
    ShopifyDisputeCreatedData,
    ShopifyDisputeWonData,
    ShopifyOrderFulfilledData,
    ShopifyOrderPaidData,
    ShopifyPayoutSettledData,
    ShopifyRefundCreatedData,
    ShopifyStoreConnectedData,
    ShopifyStoreDisconnectedData,
)
from .models import (
    ShopifyDispute,
    ShopifyFulfillment,
    ShopifyOrder,
    ShopifyPayout,
    ShopifyPayoutTransaction,
    ShopifyRefund,
    ShopifyStore,
)

logger = logging.getLogger(__name__)

# Shopify API configuration — set these in Django settings or env vars
SHOPIFY_API_KEY = getattr(settings, "SHOPIFY_API_KEY", "")
SHOPIFY_API_SECRET = getattr(settings, "SHOPIFY_API_SECRET", "")
SHOPIFY_SCOPES = getattr(
    settings,
    "SHOPIFY_SCOPES",
    "read_customers,read_discounts,read_fulfillments,read_inventory,read_locations,read_orders,read_products,read_returns,read_shopify_payments_payouts",
)
SHOPIFY_APP_URL = getattr(settings, "SHOPIFY_APP_URL", "")

# Required webhooks to register
SHOPIFY_WEBHOOK_TOPICS = [
    "orders/paid",
    "refunds/create",
    "fulfillments/create",
    "disputes/create",
    "disputes/update",
    "products/create",
    "products/update",
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
        shop_domain=shop_domain,
        defaults={
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
        resp = requests.post(
            token_url,
            json={
                "client_id": SHOPIFY_API_KEY,
                "client_secret": SHOPIFY_API_SECRET,
                "code": code,
            },
            timeout=15,
        )
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

    try:
        with command_writes_allowed():
            store.access_token = access_token
            store.scopes = scopes
            store.status = ShopifyStore.Status.ACTIVE
            store.oauth_nonce = ""
            store.error_message = ""
            store.save()
    except IntegrityError:
        return CommandResult.fail(
            "This Shopify store is already connected to another Nxentra company. "
            "Disconnect it from the other company first."
        )

    # Auto-create a Shopify warehouse for inventory tracking
    _ensure_shopify_warehouse(store)

    # Auto-create Customer + PostingProfile for Sales Invoice routing
    _ensure_shopify_sales_setup(store)

    return CommandResult.ok(data={"store": store})


@transaction.atomic
def register_webhooks(actor: ActorContext, store_id: int) -> CommandResult:
    """Register Shopify webhooks for the connected store."""
    require(actor, "settings.edit")

    try:
        store = ShopifyStore.objects.get(
            company=actor.company,
            pk=store_id,
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
                f"https://{store.shop_domain}/admin/api/2025-01/webhooks.json",
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
        return CommandResult.ok(
            data={
                "registered": registered,
                "errors": errors,
                "store": store,
            }
        )

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

    return CommandResult.ok(
        data={
            "registered": registered,
            "store": store,
        }
    )


@transaction.atomic
def disconnect_store(actor: ActorContext, store_public_id: str = None) -> CommandResult:
    """Disconnect a Shopify store. If store_public_id not given, disconnects the first active store."""
    require(actor, "settings.edit")

    try:
        if store_public_id:
            store = ShopifyStore.objects.get(
                company=actor.company,
                public_id=store_public_id,
            )
        else:
            store = (
                ShopifyStore.objects.filter(
                    company=actor.company,
                )
                .exclude(status=ShopifyStore.Status.DISCONNECTED)
                .first()
            )
            if not store:
                raise ShopifyStore.DoesNotExist
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


def process_order_paid(store: ShopifyStore, payload: dict) -> CommandResult:
    """
    Process an orders/paid webhook.
    Creates local ShopifyOrder record and emits event for projection.

    Wrapped in transaction.atomic internally so callers can handle
    failures without breaking their own transaction.
    """
    try:
        return _process_order_paid_inner(store, payload)
    except Exception as e:
        logger.error("process_order_paid failed for store %s: %s", store.shop_domain, e)
        return CommandResult.fail(str(e))


@transaction.atomic
def _process_order_paid_inner(store: ShopifyStore, payload: dict) -> CommandResult:
    """Inner implementation — runs inside transaction.atomic."""
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

    # Commerce-level payment verification: compare total_price against
    # sum of successful payment transactions in the webhook payload.
    transactions = payload.get("transactions", [])
    if transactions:
        payment_total = sum(
            Decimal(str(t.get("amount", "0")))
            for t in transactions
            if t.get("kind") in ("sale", "capture") and t.get("status") == "success"
        )
        refund_total = sum(
            Decimal(str(t.get("amount", "0")))
            for t in transactions
            if t.get("kind") == "refund" and t.get("status") == "success"
        )
        net_payment = payment_total - refund_total
        if net_payment != total_price:
            logger.warning(
                "Payment verification mismatch for order %s: total_price=%s "
                "but sum(transactions)=%s (payments=%s, refunds=%s)",
                shopify_order_id,
                total_price,
                net_payment,
                payment_total,
                refund_total,
            )

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

    # Build line items summary + auto-create Items for unknown SKUs
    line_items = []
    for item in payload.get("line_items", []):
        sku = item.get("sku", "")
        line_items.append(
            {
                "title": item.get("title", ""),
                "quantity": item.get("quantity", 1),
                "price": str(item.get("price", "0")),
                "sku": sku,
            }
        )

        # Auto-create Item if SKU exists but no matching Item in Nxentra
        if sku:
            _auto_create_item_from_line(store, sku, item)

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
    for txn in payload.get("transactions", []):
        if txn.get("kind") == "refund" and txn.get("status") == "success":
            refund_amount += Decimal(str(txn.get("amount", "0")))

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
# Payout Sync
# =============================================================================


@transaction.atomic
def sync_payouts(store: ShopifyStore) -> CommandResult:
    """
    Poll Shopify Payments API for recent payouts and emit events.

    Fetches payouts with status=paid that haven't been recorded yet.
    Each payout becomes a SHOPIFY_PAYOUT_SETTLED event for the projection.
    """
    if store.status != ShopifyStore.Status.ACTIVE:
        return CommandResult.fail("Store is not active.")

    if not store.access_token:
        return CommandResult.fail("No access token — reconnect the store.")

    headers = {
        "X-Shopify-Access-Token": store.access_token,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.get(
            f"https://{store.shop_domain}/admin/api/2025-01/shopify_payments/payouts.json",
            headers=headers,
            params={"status": "paid", "limit": 50},
            timeout=15,
        )
        resp.raise_for_status()
        payouts_data = resp.json().get("payouts", [])
    except requests.RequestException as e:
        logger.error("Failed to fetch payouts from Shopify: %s", e)
        return CommandResult.fail(f"Shopify API error: {e}")

    created_count = 0
    skipped_count = 0

    for p in payouts_data:
        shopify_payout_id = p.get("id")
        if not shopify_payout_id:
            continue

        # Idempotency
        if ShopifyPayout.objects.filter(
            company=store.company,
            shopify_payout_id=shopify_payout_id,
        ).exists():
            skipped_count += 1
            continue

        payout_date_str = p.get("date", "")
        try:
            payout_date = datetime.fromisoformat(payout_date_str.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            payout_date = datetime.now().date()

        # Shopify payout "amount" is the NET deposited to bank.
        # Gross and fees come from the summary breakdown.
        summary = p.get("summary", {})
        net_amount = Decimal(str(p.get("amount", "0")))
        currency = p.get("currency", "USD")

        # Fees: sum absolute values of all fee categories
        charges_fee = abs(Decimal(str(summary.get("charges_fee_amount", "0"))))
        refunds_fee = abs(Decimal(str(summary.get("refunds_fee_amount", "0"))))
        adjustments_fee = abs(Decimal(str(summary.get("adjustments_fee_amount", "0"))))
        reserved_fee = abs(Decimal(str(summary.get("reserved_funds_fee_amount", "0"))))
        fees = charges_fee + refunds_fee + adjustments_fee + reserved_fee

        # Gross: sum of all gross categories (what the clearing account should release)
        charges_gross = Decimal(str(summary.get("charges_gross_amount", "0")))
        refunds_gross = Decimal(str(summary.get("refunds_gross_amount", "0")))
        adjustments_gross = Decimal(str(summary.get("adjustments_gross_amount", "0")))
        reserved_gross = Decimal(str(summary.get("reserved_funds_gross_amount", "0")))
        gross_amount = charges_gross + refunds_gross + adjustments_gross + reserved_gross

        # Fallback: if summary is empty, derive from net + fees
        if gross_amount == 0 and net_amount != 0:
            gross_amount = net_amount + fees  # works for both positive and negative payouts

        with command_writes_allowed():
            payout = ShopifyPayout.objects.create(
                company=store.company,
                store=store,
                shopify_payout_id=shopify_payout_id,
                gross_amount=gross_amount,
                fees=fees,
                net_amount=net_amount,
                currency=currency,
                shopify_status=p.get("status", ""),
                payout_date=payout_date,
                # Fee breakdown by category
                charges_fee=charges_fee,
                refunds_fee=refunds_fee,
                adjustments_fee=adjustments_fee,
                charges_gross=charges_gross,
                refunds_gross=refunds_gross,
                adjustments_gross=adjustments_gross,
                raw_payload=p,
            )

        from events.emitter import emit_event_no_actor

        event = emit_event_no_actor(
            company=store.company,
            event_type=EventTypes.SHOPIFY_PAYOUT_SETTLED,
            aggregate_type="ShopifyPayout",
            aggregate_id=str(payout.public_id),
            idempotency_key=f"shopify.payout.settled:{shopify_payout_id}",
            metadata={"source": "shopify_payout_sync", "shop_domain": store.shop_domain},
            data=ShopifyPayoutSettledData(
                amount=str(gross_amount),
                currency=currency,
                transaction_date=str(payout_date),
                document_ref=f"Payout {shopify_payout_id}",
                store_public_id=str(store.public_id),
                shopify_payout_id=str(shopify_payout_id),
                gross_amount=str(gross_amount),
                fees=str(fees),
                net_amount=str(net_amount),
                shopify_status=p.get("status", ""),
                payout_date=str(payout_date),
            ),
        )

        with command_writes_allowed():
            payout.event_id = event.id if event else None
            payout.save(update_fields=["event_id"])

        created_count += 1

    # Update last_sync_at
    from django.utils import timezone as tz

    with command_writes_allowed():
        store.last_sync_at = tz.now()
        store.save(update_fields=["last_sync_at"])

    logger.info(
        "Payout sync for %s: %d new, %d skipped",
        store.shop_domain,
        created_count,
        skipped_count,
    )

    return CommandResult.ok(
        data={
            "created": created_count,
            "skipped": skipped_count,
        }
    )


# =============================================================================
# Payout Transaction-Level Verification (Layer 2)
# =============================================================================


@transaction.atomic
def fetch_payout_transactions(store: ShopifyStore, payout: ShopifyPayout) -> CommandResult:
    """
    Fetch individual transactions for a payout from Shopify's API.

    Stores each transaction and attempts to match to local orders/refunds.
    Verifies that sum(transactions) matches the payout's reported amounts.
    """
    if not store.access_token:
        return CommandResult.fail("No access token.")

    # Skip if transactions already fetched
    if payout.transactions.exists():
        return CommandResult.ok(data={"skipped": True, "reason": "Transactions already fetched."})

    headers = {
        "X-Shopify-Access-Token": store.access_token,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.get(
            f"https://{store.shop_domain}/admin/api/2025-01/shopify_payments/balance/transactions.json",
            headers=headers,
            params={"payout_id": payout.shopify_payout_id, "limit": 250},
            timeout=30,
        )
        resp.raise_for_status()
        transactions = resp.json().get("transactions", [])
    except requests.RequestException as e:
        logger.error("Failed to fetch payout transactions: %s", e)
        return CommandResult.fail(f"Shopify API error: {e}")

    created = 0
    verified = 0
    sum_amount = Decimal("0")
    sum_fee = Decimal("0")
    sum_net = Decimal("0")

    for txn in transactions:
        txn_id = txn.get("id")
        if not txn_id:
            continue

        amount = Decimal(str(txn.get("amount", "0")))
        fee = Decimal(str(txn.get("fee", "0")))
        net = Decimal(str(txn.get("net", "0")))
        txn_type = txn.get("type", "other")
        source_order_id = txn.get("source_order_id")

        sum_amount += amount
        sum_fee += fee
        sum_net += net

        # Map Shopify type to our enum
        type_map = {
            "charge": ShopifyPayoutTransaction.TransactionType.CHARGE,
            "refund": ShopifyPayoutTransaction.TransactionType.REFUND,
            "adjustment": ShopifyPayoutTransaction.TransactionType.ADJUSTMENT,
            "payout": ShopifyPayoutTransaction.TransactionType.PAYOUT,
        }
        transaction_type = type_map.get(txn_type, ShopifyPayoutTransaction.TransactionType.OTHER)

        # Try to match to local order
        local_order = None
        is_verified = False
        if source_order_id:
            local_order = ShopifyOrder.objects.filter(
                company=store.company,
                shopify_order_id=source_order_id,
            ).first()
            if local_order:
                is_verified = True
                verified += 1

        processed_at = None
        processed_str = txn.get("processed_at", "")
        if processed_str:
            try:
                processed_at = datetime.fromisoformat(processed_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        with command_writes_allowed():
            ShopifyPayoutTransaction.objects.create(
                company=store.company,
                payout=payout,
                shopify_transaction_id=txn_id,
                transaction_type=transaction_type,
                amount=amount,
                fee=fee,
                net=net,
                currency=txn.get("currency", payout.currency),
                source_order_id=source_order_id,
                source_type=txn.get("source_type", ""),
                verified=is_verified,
                local_order=local_order,
                processed_at=processed_at,
                raw_data=txn,
            )
            created += 1

    # Verification: compare transaction sums to payout summary
    discrepancies = []
    if sum_net != payout.net_amount:
        discrepancies.append(f"Net mismatch: transactions={sum_net}, payout={payout.net_amount}")
    if abs(sum_fee) != payout.fees:
        discrepancies.append(f"Fee mismatch: transactions={abs(sum_fee)}, payout={payout.fees}")

    if discrepancies:
        logger.warning(
            "Payout %s transaction verification discrepancies: %s",
            payout.shopify_payout_id,
            "; ".join(discrepancies),
        )

    return CommandResult.ok(
        data={
            "payout_id": payout.shopify_payout_id,
            "transactions_created": created,
            "transactions_verified": verified,
            "transactions_total": len(transactions),
            "sum_amount": str(sum_amount),
            "sum_fee": str(sum_fee),
            "sum_net": str(sum_net),
            "discrepancies": discrepancies,
            "balanced": len(discrepancies) == 0,
        }
    )


def verify_payout(store: ShopifyStore, payout_id: int) -> CommandResult:
    """
    Verify a single payout by fetching its transactions.

    If transactions already exist, re-runs verification against stored data.
    """
    try:
        payout = ShopifyPayout.objects.get(
            company=store.company,
            shopify_payout_id=payout_id,
        )
    except ShopifyPayout.DoesNotExist:
        return CommandResult.fail(f"Payout {payout_id} not found.")

    # If transactions already fetched, verify from stored data
    existing = payout.transactions.all()
    if existing.exists():
        sum_net = sum(t.net for t in existing)
        sum_fee = sum(abs(t.fee) for t in existing)
        verified_count = existing.filter(verified=True).count()

        discrepancies = []
        if sum_net != payout.net_amount:
            discrepancies.append(f"Net mismatch: transactions={sum_net}, payout={payout.net_amount}")
        if sum_fee != payout.fees:
            discrepancies.append(f"Fee mismatch: transactions={sum_fee}, payout={payout.fees}")

        return CommandResult.ok(
            data={
                "payout_id": payout.shopify_payout_id,
                "transactions_total": existing.count(),
                "transactions_verified": verified_count,
                "discrepancies": discrepancies,
                "balanced": len(discrepancies) == 0,
                "source": "cached",
            }
        )

    # Fetch from Shopify API
    return fetch_payout_transactions(store, payout)


# =============================================================================
# Fulfillment Processing
# =============================================================================


@transaction.atomic
def process_fulfillment(store: ShopifyStore, payload: dict) -> CommandResult:
    """
    Process a fulfillments/create webhook.

    Matches Shopify line item SKUs to Item.code in the inventory system.
    For each matched INVENTORY item, looks up current avg_cost from
    InventoryBalance and includes cost data in the event for COGS JE creation.
    """
    shopify_fulfillment_id = payload.get("id")
    shopify_order_id = payload.get("order_id")

    if not shopify_fulfillment_id:
        return CommandResult.fail("Missing fulfillment ID in payload.")

    # Idempotency
    if ShopifyFulfillment.objects.filter(
        company=store.company,
        shopify_fulfillment_id=shopify_fulfillment_id,
    ).exists():
        logger.info("Fulfillment %s already exists — skipping", shopify_fulfillment_id)
        return CommandResult.ok(data={"skipped": True})

    # Find the local order
    order = None
    if shopify_order_id:
        order = ShopifyOrder.objects.filter(
            company=store.company,
            shopify_order_id=shopify_order_id,
        ).first()

    if not order:
        return CommandResult.fail(
            f"Order {shopify_order_id} not found locally. Fulfillment cannot be processed without a matching order."
        )

    # Parse fulfillment date
    created_at_str = payload.get("created_at", "")
    try:
        fulfillment_date = datetime.fromisoformat(created_at_str.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        fulfillment_date = datetime.now().date()

    # Match SKUs to inventory Items and look up costs
    from inventory.models import Warehouse
    from projections.models import InventoryBalance
    from sales.models import Item

    line_items = payload.get("line_items", [])
    cogs_lines = []
    unmatched_skus = []
    total_cogs = Decimal("0")

    # Get warehouse for cost lookups — prefer the specific Shopify location
    shopify_location_id = str(payload.get("location_id", ""))
    warehouse = None
    if shopify_location_id:
        warehouse = Warehouse.objects.filter(
            company=store.company,
            platform="shopify",
            platform_location_id=shopify_location_id,
            is_platform_managed=True,
        ).first()
    if not warehouse:
        warehouse = _get_shopify_warehouse(store.company)
    if not warehouse:
        try:
            warehouse = Warehouse.objects.get(company=store.company, is_default=True)
        except Warehouse.DoesNotExist:
            warehouse = Warehouse.objects.filter(company=store.company, is_active=True).first()

    for li in line_items:
        sku = li.get("sku", "").strip()
        qty = Decimal(str(li.get("quantity", 1)))

        if not sku:
            unmatched_skus.append(
                {
                    "title": li.get("title", ""),
                    "reason": "no_sku",
                }
            )
            continue

        # Match SKU to Item.code
        try:
            item = Item.objects.get(
                company=store.company,
                code=sku,
                item_type=Item.ItemType.INVENTORY,
            )
        except Item.DoesNotExist:
            unmatched_skus.append(
                {
                    "sku": sku,
                    "title": li.get("title", ""),
                    "reason": "item_not_found",
                }
            )
            continue

        if not item.cogs_account or not item.inventory_account:
            unmatched_skus.append(
                {
                    "sku": sku,
                    "title": li.get("title", ""),
                    "reason": "missing_cogs_or_inventory_account",
                }
            )
            continue

        # Look up current avg_cost from InventoryBalance
        avg_cost = Decimal("0")
        if warehouse:
            try:
                balance = InventoryBalance.objects.get(
                    company=store.company,
                    item=item,
                    warehouse=warehouse,
                )
                avg_cost = balance.avg_cost
            except InventoryBalance.DoesNotExist:
                pass

        # Fallback to item's recorded average cost
        if avg_cost == 0:
            avg_cost = item.average_cost or item.default_cost or Decimal("0")

        cogs_value = qty * avg_cost
        total_cogs += cogs_value

        cogs_lines.append(
            {
                "sku": sku,
                "item_public_id": str(item.public_id),
                "item_code": item.code,
                "warehouse_public_id": str(warehouse.public_id) if warehouse else "",
                "qty": str(qty),
                "unit_cost": str(avg_cost),
                "cogs_value": str(cogs_value),
                "cogs_account_id": item.cogs_account_id,
                "inventory_account_id": item.inventory_account_id,
            }
        )

    # Determine status
    total_items = len(line_items)
    matched_items = len(cogs_lines)
    if matched_items == 0 and total_items > 0:
        fulfillment_status = ShopifyFulfillment.Status.ERROR
        error_msg = f"No SKUs matched inventory items ({len(unmatched_skus)} unmatched)"
    elif matched_items < total_items:
        fulfillment_status = ShopifyFulfillment.Status.PARTIAL
        error_msg = f"{len(unmatched_skus)} of {total_items} SKUs unmatched"
    else:
        fulfillment_status = ShopifyFulfillment.Status.RECEIVED
        error_msg = ""

    with command_writes_allowed():
        fulfillment = ShopifyFulfillment.objects.create(
            company=store.company,
            order=order,
            shopify_fulfillment_id=shopify_fulfillment_id,
            shopify_order_id=shopify_order_id,
            tracking_number=payload.get("tracking_number", "") or "",
            tracking_company=payload.get("tracking_company", "") or "",
            shopify_status=payload.get("status", ""),
            shopify_created_at=created_at_str or datetime.now().isoformat(),
            total_cogs=total_cogs,
            currency=order.currency,
            matched_items=matched_items,
            total_items=total_items,
            status=fulfillment_status,
            error_message=error_msg,
            raw_payload=payload,
        )

    # Only emit event if we have matched items (something to post COGS for)
    if cogs_lines:
        from events.emitter import emit_event_no_actor

        event = emit_event_no_actor(
            company=store.company,
            event_type=EventTypes.SHOPIFY_ORDER_FULFILLED,
            aggregate_type="ShopifyFulfillment",
            aggregate_id=str(fulfillment.public_id),
            idempotency_key=f"shopify.fulfillment:{shopify_fulfillment_id}",
            metadata={"source": "shopify_webhook", "shop_domain": store.shop_domain},
            data=ShopifyOrderFulfilledData(
                amount=str(total_cogs),
                currency=order.currency,
                transaction_date=str(fulfillment_date),
                document_ref=order.shopify_order_name,
                store_public_id=str(store.public_id),
                shopify_fulfillment_id=str(shopify_fulfillment_id),
                shopify_order_id=str(shopify_order_id),
                order_name=order.shopify_order_name,
                fulfillment_date=str(fulfillment_date),
                total_cogs=str(total_cogs),
                cogs_lines=cogs_lines,
                unmatched_skus=unmatched_skus,
            ),
        )

        with command_writes_allowed():
            fulfillment.event_id = event.id if event else None
            fulfillment.save(update_fields=["event_id"])

    # Create COGS journal entry + stock ledger entries via commands
    # (moved from projection to command layer — events come from commands)
    if cogs_lines and total_cogs > 0:
        _create_cogs_for_fulfillment(
            company=store.company,
            cogs_lines=cogs_lines,
            total_cogs=total_cogs,
            fulfillment=fulfillment,
            order=order,
            fulfillment_date=fulfillment_date,
        )

    if unmatched_skus:
        logger.warning(
            "Fulfillment %s: %d/%d SKUs unmatched: %s",
            shopify_fulfillment_id,
            len(unmatched_skus),
            total_items,
            [s.get("sku", s.get("title", "?")) for s in unmatched_skus],
        )

    return CommandResult.ok(
        data={
            "fulfillment": fulfillment,
            "matched": matched_items,
            "unmatched": len(unmatched_skus),
            "total_cogs": total_cogs,
        }
    )


def _create_cogs_for_fulfillment(company, cogs_lines, total_cogs, fulfillment, order, fulfillment_date):
    """
    Create COGS journal entry + stock ledger entries for a Shopify fulfillment.

    Called from process_fulfillment (command layer, not projection).

    Creates:
    1. JE: DR COGS / CR Inventory per item
    2. StockLedgerEntry via record_stock_issue (updates InventoryBalance)

    The stock issue uses the item's default_cost or average_cost.
    If no InventoryBalance exists, one is created with qty=0 to allow
    the stock issue (Shopify merchants don't manage stock in Nxentra).
    """
    from accounting.commands import create_journal_entry, post_journal_entry, save_journal_entry_complete
    from accounting.models import Account
    from accounts.authz import system_actor_for_company
    from inventory.commands import record_stock_issue
    from inventory.models import Warehouse
    from projections.models import InventoryBalance
    from sales.models import Item

    actor = system_actor_for_company(company)

    # Build JE lines
    je_lines = []
    stock_lines = []
    fulfillment_id = fulfillment.shopify_fulfillment_id
    order_name = order.shopify_order_name

    for cl in cogs_lines:
        cogs_value = Decimal(str(cl.get("cogs_value", "0")))
        if cogs_value <= 0:
            continue

        cogs_account_id = cl.get("cogs_account_id")
        inventory_account_id = cl.get("inventory_account_id")
        item_code = cl.get("item_code", "")
        qty = Decimal(str(cl.get("qty", "0")))

        try:
            cogs_account = Account.objects.get(id=cogs_account_id, company=company)
            inventory_account = Account.objects.get(id=inventory_account_id, company=company)
        except Account.DoesNotExist:
            logger.warning("COGS/Inventory account not found for %s — skipping", item_code)
            continue

        # JE: DR COGS
        je_lines.append(
            {
                "account_id": cogs_account.id,
                "description": f"COGS: {item_code} x {qty}",
                "debit": str(cogs_value),
                "credit": "0",
            }
        )

        # JE: CR Inventory
        je_lines.append(
            {
                "account_id": inventory_account.id,
                "description": f"Inventory issued: {item_code} x {qty}",
                "debit": "0",
                "credit": str(cogs_value),
            }
        )

        # Stock ledger line
        item = Item.objects.filter(company=company, code=item_code).first()
        if item:
            # Find the correct warehouse (Shopify location or default)
            warehouse = None
            wh_public_id = cl.get("warehouse_public_id")
            if wh_public_id:
                warehouse = Warehouse.objects.filter(
                    company=company,
                    public_id=wh_public_id,
                ).first()
            if not warehouse:
                warehouse = _get_shopify_warehouse(company)
            if not warehouse:
                warehouse = Warehouse.objects.filter(
                    company=company,
                    is_default=True,
                ).first()

            if warehouse:
                # Ensure InventoryBalance exists (Shopify merchants may not have one)
                InventoryBalance.objects.get_or_create(
                    company=company,
                    item=item,
                    warehouse=warehouse,
                    defaults={
                        "qty_on_hand": Decimal("0"),
                        "avg_cost": item.default_cost or Decimal("0"),
                        "stock_value": Decimal("0"),
                    },
                )

                stock_lines.append(
                    {
                        "item": item,
                        "warehouse": warehouse,
                        "qty": qty,
                        "source_line_id": str(fulfillment.public_id),
                    }
                )

    if not je_lines:
        return

    # Create and post the COGS JE
    memo = f"Shopify COGS: {order_name} (Fulfillment {fulfillment_id})"
    result = create_journal_entry(
        actor=actor,
        date=fulfillment_date,
        memo=memo,
        lines=je_lines,
        kind="NORMAL",
    )

    if not result.success:
        logger.error("Failed to create COGS JE for fulfillment %s: %s", fulfillment_id, result.error)
        return

    entry = result.data
    save_result = save_journal_entry_complete(actor, entry.id)
    if not save_result.success:
        logger.error("Failed to save COGS JE for fulfillment %s: %s", fulfillment_id, save_result.error)
        return

    entry = save_result.data
    post_result = post_journal_entry(actor, entry.id)
    if not post_result.success:
        logger.error("Failed to post COGS JE for fulfillment %s: %s", fulfillment_id, post_result.error)
        return

    journal_entry = post_result.data

    # Record stock issue (creates StockLedgerEntry + updates InventoryBalance)
    if stock_lines:
        from inventory.models import StockLedgerEntry as SLE

        # Allow negative inventory for Shopify (merchants don't manage stock in Nxentra)
        orig_allow = company.allow_negative_inventory
        try:
            company.allow_negative_inventory = True
            stock_result = record_stock_issue(
                actor=actor,
                source_type=SLE.SourceType.SALES_INVOICE,
                source_id=str(fulfillment.public_id),
                lines=stock_lines,
                journal_entry=journal_entry,
            )
            if not stock_result.success:
                logger.warning("Stock issue failed for fulfillment %s: %s", fulfillment_id, stock_result.error)
        finally:
            company.allow_negative_inventory = orig_allow

    # Update fulfillment record with JE
    with command_writes_allowed():
        fulfillment.journal_entry_id = journal_entry.public_id if journal_entry else None
        fulfillment.status = "PROCESSED"
        fulfillment.save(update_fields=["journal_entry_id", "status"])

    logger.info(
        "Created COGS JE %s + stock issue for fulfillment %s (%s)",
        journal_entry.public_id if journal_entry else "?",
        fulfillment_id,
        order_name,
    )


# =============================================================================
# Dispute / Chargeback Processing
# =============================================================================


@transaction.atomic
def process_dispute(store: ShopifyStore, payload: dict) -> CommandResult:
    """
    Process a disputes/create or disputes/update webhook.

    Creates a ShopifyDispute record and emits SHOPIFY_DISPUTE_CREATED event
    for the projection to create a chargeback reversal journal entry.
    """
    shopify_dispute_id = payload.get("id")
    if not shopify_dispute_id:
        return CommandResult.fail("Missing dispute ID in payload.")

    # Idempotency: skip if already processed
    existing = ShopifyDispute.objects.filter(
        company=store.company,
        shopify_dispute_id=shopify_dispute_id,
    ).first()

    if existing:
        # Update dispute status if changed
        new_status = payload.get("status", "")
        if new_status and new_status != existing.shopify_dispute_status:
            with command_writes_allowed():
                existing.shopify_dispute_status = new_status
                existing.raw_payload = payload
                if new_status == "won":
                    existing.status = ShopifyDispute.Status.WON
                elif new_status == "lost":
                    existing.status = ShopifyDispute.Status.LOST
                finalized_str = payload.get("finalized_on", "")
                if finalized_str:
                    try:
                        existing.finalized_on = datetime.fromisoformat(finalized_str.replace("Z", "+00:00")).date()
                    except (ValueError, AttributeError):
                        pass
                existing.save()
            logger.info("Dispute %s status updated to %s", shopify_dispute_id, new_status)

            # Emit reversal event when dispute is won
            if new_status == "won":
                order = existing.order
                order_name = order.shopify_order_name if order else f"Order {existing.shopify_order_id or '?'}"
                from events.emitter import emit_event_no_actor

                emit_event_no_actor(
                    company=store.company,
                    event_type=EventTypes.SHOPIFY_DISPUTE_WON,
                    aggregate_type="ShopifyDispute",
                    aggregate_id=str(existing.public_id),
                    idempotency_key=f"shopify.dispute.won:{shopify_dispute_id}",
                    metadata={"source": "shopify_webhook", "shop_domain": store.shop_domain},
                    data=ShopifyDisputeWonData(
                        amount=str(existing.amount),
                        currency=existing.currency,
                        transaction_date=str(datetime.now().date()),
                        document_ref=f"Dispute Won {shopify_dispute_id}",
                        store_public_id=str(store.public_id),
                        shopify_dispute_id=str(shopify_dispute_id),
                        shopify_order_id=str(existing.shopify_order_id or ""),
                        order_name=order_name,
                        dispute_amount=str(existing.amount),
                        chargeback_fee=str(existing.fee),
                    ),
                )

        return CommandResult.ok(data={"updated": True, "dispute_id": shopify_dispute_id})

    # Parse dispute data
    amount = Decimal(str(payload.get("amount", "0")))
    currency = payload.get("currency", "USD")
    shopify_order_id = payload.get("order_id")
    reason = payload.get("reason", "")
    dispute_status = payload.get("status", "")

    # Chargeback fee (Shopify charges a fee for chargebacks, typically $15-$25)
    # This comes from the network_reason_code or a fixed fee field
    chargeback_fee = Decimal(str(payload.get("fee", "0")))

    # Evidence due date
    evidence_due_by = None
    evidence_str = payload.get("evidence_due_by", "")
    if evidence_str:
        try:
            evidence_due_by = datetime.fromisoformat(evidence_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    # Link to local order
    order = None
    if shopify_order_id:
        order = ShopifyOrder.objects.filter(
            company=store.company,
            shopify_order_id=shopify_order_id,
        ).first()

    order_name = order.shopify_order_name if order else f"Order {shopify_order_id or '?'}"

    with command_writes_allowed():
        dispute = ShopifyDispute.objects.create(
            company=store.company,
            store=store,
            order=order,
            shopify_dispute_id=shopify_dispute_id,
            shopify_order_id=shopify_order_id,
            amount=amount,
            currency=currency,
            fee=chargeback_fee,
            reason=reason,
            shopify_dispute_status=dispute_status,
            evidence_due_by=evidence_due_by,
            raw_payload=payload,
        )

    # Emit event for projection to create reversal JE
    from events.emitter import emit_event_no_actor

    event = emit_event_no_actor(
        company=store.company,
        event_type=EventTypes.SHOPIFY_DISPUTE_CREATED,
        aggregate_type="ShopifyDispute",
        aggregate_id=str(dispute.public_id),
        idempotency_key=f"shopify.dispute.created:{shopify_dispute_id}",
        metadata={"source": "shopify_webhook", "shop_domain": store.shop_domain},
        data=ShopifyDisputeCreatedData(
            amount=str(amount),
            currency=currency,
            transaction_date=str(datetime.now().date()),
            document_ref=f"Dispute {shopify_dispute_id}",
            store_public_id=str(store.public_id),
            shopify_dispute_id=str(shopify_dispute_id),
            shopify_order_id=str(shopify_order_id or ""),
            order_name=order_name,
            dispute_amount=str(amount),
            chargeback_fee=str(chargeback_fee),
            reason=reason,
            dispute_status=dispute_status,
        ),
    )

    with command_writes_allowed():
        dispute.event_id = event.id if event else None
        dispute.save(update_fields=["event_id"])

    logger.info(
        "Processed dispute %s for %s %s (order: %s, reason: %s)",
        shopify_dispute_id,
        currency,
        amount,
        order_name,
        reason,
    )

    return CommandResult.ok(data={"dispute": dispute, "event": event})


# =============================================================================
# Helpers
# =============================================================================


def _ensure_shopify_warehouse(store):
    """Sync Shopify locations into platform-managed warehouses.

    Fetches all locations from the Shopify API and creates/updates
    a Warehouse for each one. Each warehouse is marked as platform-managed
    so the UI shows it as read-only (synced from Shopify).

    Falls back to creating a single generic "SHOPIFY" warehouse if the
    API call fails (e.g., during demo seeding with fake tokens).
    """
    from datetime import datetime as dt

    from inventory.models import Warehouse

    # Skip if platform-managed warehouses already exist for this store
    if Warehouse.objects.filter(
        company=store.company,
        platform="shopify",
        is_platform_managed=True,
    ).exists():
        return

    headers = {
        "X-Shopify-Access-Token": store.access_token,
        "Content-Type": "application/json",
    }

    locations = []
    try:
        resp = requests.get(
            f"https://{store.shop_domain}/admin/api/2025-01/locations.json",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        locations = resp.json().get("locations", [])
    except Exception as exc:
        logger.warning("Failed to fetch Shopify locations for %s: %s", store.shop_domain, exc)

    with command_writes_allowed():
        if locations:
            for loc in locations:
                loc_id = str(loc["id"])
                loc_name = loc.get("name", f"Location {loc_id}")
                code = f"SHOP-{loc_id[-6:]}"
                address_parts = [
                    loc.get("address1", ""),
                    loc.get("city", ""),
                    loc.get("country_name", ""),
                ]
                address = ", ".join(p for p in address_parts if p)

                Warehouse.objects.get_or_create(
                    company=store.company,
                    platform="shopify",
                    platform_location_id=loc_id,
                    is_platform_managed=True,
                    defaults={
                        "code": code,
                        "name": f"Shopify: {loc_name}",
                        "address": address,
                        "is_active": loc.get("active", True),
                        "last_synced_at": dt.now(UTC),
                    },
                )
            logger.info(
                "Synced %d Shopify locations for %s",
                len(locations),
                store.shop_domain,
            )
        else:
            # Fallback: create a single generic warehouse (demo/offline mode)
            store_label = store.shop_domain.replace(".myshopify.com", "")
            Warehouse.objects.get_or_create(
                company=store.company,
                code="SHOPIFY",
                defaults={
                    "name": f"Shopify — {store_label}",
                    "platform": "shopify",
                    "is_platform_managed": True,
                    "is_active": True,
                },
            )
            logger.info("Created fallback Shopify warehouse for %s", store.shop_domain)

    # Ensure at least one default warehouse exists
    if not Warehouse.objects.filter(company=store.company, is_default=True).exists():
        first = Warehouse.objects.filter(company=store.company, is_active=True).first()
        if first:
            first.is_default = True
            first.save(update_fields=["is_default"])


def _get_shopify_warehouse(company):
    """Get the primary Shopify warehouse for a company.

    Prefers platform-managed Shopify warehouses. Falls back to legacy
    code='SHOPIFY' warehouse or any active warehouse.
    """
    from inventory.models import Warehouse

    # Try platform-managed Shopify location first
    wh = Warehouse.objects.filter(
        company=company,
        platform="shopify",
        is_platform_managed=True,
        is_active=True,
    ).first()
    if wh:
        return wh

    # Fallback to legacy code-based lookup
    return Warehouse.objects.filter(company=company, code="SHOPIFY").first()


def _ensure_shopify_sales_setup(store):
    """Create Customer + PostingProfile for Shopify Sales Invoice routing.

    Called on Shopify store connection. Creates:
    1. A Customer record representing "Shopify Customers" (aggregate customer)
    2. A PostingProfile with the Shopify Clearing account as the control account
       (Shopify Clearing acts as the receivable — Shopify owes the merchant
       until payout is settled)

    These are stored on the ShopifyStore model for use by the Shopify
    accounting handler when creating SalesInvoices from order webhooks.
    """
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account, Customer
    from sales.models import PostingProfile

    company = store.company

    # Skip if already set up
    if store.default_customer_id and store.default_posting_profile_id:
        return

    store_label = store.shop_domain.replace(".myshopify.com", "")

    with command_writes_allowed(), projection_writes_allowed():
        # 1. Find or create the Shopify Clearing account
        mapping = ModuleAccountMapping.get_mapping(company, "shopify_connector")
        clearing_account = None
        if mapping:
            clearing_account = mapping.get("SHOPIFY_CLEARING")

        if not clearing_account:
            # Fallback: look by code convention
            clearing_account = Account.objects.filter(
                company=company,
                code__in=["1150", "11500"],
                status="ACTIVE",
            ).first()

        if not clearing_account:
            logger.warning(
                "Cannot set up Shopify sales routing for %s: no clearing account found",
                store.shop_domain,
            )
            return

        # 2. Create or get Customer
        customer, _ = Customer.objects.get_or_create(
            company=company,
            code=f"SHOPIFY-{store_label.upper()[:10]}",
            defaults={
                "name": f"Shopify: {store_label}",
                "name_ar": f"شوبيفاي: {store_label}",
                "default_ar_account": clearing_account,
                "currency": company.default_currency,
                "payment_terms_days": 0,
                "notes": f"Auto-created for Shopify store {store.shop_domain}",
                "status": Customer.Status.ACTIVE,
            },
        )

        # 3. Create or get PostingProfile (using Clearing as control account)
        profile, _ = PostingProfile.objects.get_or_create(
            company=company,
            code=f"SHOPIFY-{store_label.upper()[:10]}",
            defaults={
                "name": f"Shopify: {store_label}",
                "name_ar": f"شوبيفاي: {store_label}",
                "profile_type": PostingProfile.ProfileType.CUSTOMER,
                "control_account": clearing_account,
                "is_active": True,
                "description": (
                    f"Auto-created for Shopify store {store.shop_domain}. "
                    f"Uses Shopify Clearing as control account (funds held by Shopify until payout)."
                ),
            },
        )

        # 4. Link to store
        store.default_customer = customer
        store.default_posting_profile = profile
        store.save(update_fields=["default_customer", "default_posting_profile"])

    logger.info(
        "Set up Shopify sales routing for %s: customer=%s, profile=%s",
        store.shop_domain,
        customer.code,
        profile.code,
    )


def _fetch_variant_cost(store, variant_id) -> Decimal:
    """Fetch cost_per_item for a single variant from Shopify API."""
    if not variant_id or not store.access_token:
        return Decimal("0")

    headers = {
        "X-Shopify-Access-Token": store.access_token,
        "Content-Type": "application/json",
    }

    try:
        # Get the variant to find its inventory_item_id
        resp = requests.get(
            f"https://{store.shop_domain}/admin/api/2025-01/variants/{variant_id}.json",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        inv_item_id = resp.json().get("variant", {}).get("inventory_item_id")
        if not inv_item_id:
            return Decimal("0")

        # Fetch cost from inventory item
        cost_map = _fetch_inventory_item_costs(store, [inv_item_id], headers)
        return cost_map.get(inv_item_id, Decimal("0"))
    except Exception as exc:
        logger.warning("Failed to fetch variant cost for %s: %s", variant_id, exc)
        return Decimal("0")


def _auto_create_item_from_line(store, sku: str, line_item: dict):
    """Auto-create a Nxentra Item from a Shopify order line item if no match exists."""
    from sales.models import Item
    from shopify_connector.models import ShopifyProduct

    # Skip if Item already exists for this SKU
    if Item.objects.filter(company=store.company, code=sku).exists():
        return
    # Skip if ShopifyProduct mapping already exists
    if ShopifyProduct.objects.filter(company=store.company, sku=sku).exists():
        return

    title = line_item.get("title", sku)
    price = Decimal(str(line_item.get("price", "0")))
    variant_id = line_item.get("variant_id")
    product_id = line_item.get("product_id")

    # Resolve default accounts (COGS, Inventory, Sales) so fulfillment
    # can generate COGS journal entries later.
    _ensure_inventory_accounts(store.company)
    defaults = _resolve_default_item_accounts(store.company)

    # Fetch cost from Shopify's product data (cost_per_item on variant)
    cost = _fetch_variant_cost(store, variant_id)

    try:
        with transaction.atomic():
            with command_writes_allowed():
                item = Item.objects.create(
                    company=store.company,
                    code=sku,
                    name=title,
                    item_type="INVENTORY",
                    default_unit_price=price,
                    default_cost=cost,
                    is_active=True,
                    sales_account=defaults.get("sales"),
                    purchase_account=defaults.get("purchase"),
                    inventory_account=defaults.get("inventory"),
                    cogs_account=defaults.get("cogs"),
                    costing_method="WEIGHTED_AVERAGE",
                )

                # Create ShopifyProduct mapping
                ShopifyProduct.objects.create(
                    company=store.company,
                    store=store,
                    shopify_product_id=product_id or 0,
                    shopify_variant_id=variant_id or 0,
                    sku=sku,
                    title=title,
                    variant_title=line_item.get("variant_title") or "",
                    item=item,
                    auto_created=True,
                )
            logger.info(
                "Auto-created Item %s (%s) cost=%s (inventory=%s, cogs=%s)",
                sku,
                title,
                cost,
                defaults.get("inventory"),
                defaults.get("cogs"),
            )
    except Exception as exc:
        logger.warning("Failed to auto-create Item for SKU %s: %s", sku, exc)


def _extract_gateway(payload: dict) -> str:
    """Extract payment gateway from order payload."""
    gateways = payload.get("payment_gateway_names", [])
    if gateways:
        return gateways[0]
    return payload.get("gateway", "")


# =============================================================================
# Product Sync
# =============================================================================


def sync_products(store: ShopifyStore, inventory_account_id=None, cogs_account_id=None) -> CommandResult:
    """
    Pull products from Shopify and create/link Nxentra Items.

    For each variant with a SKU:
    - If a ShopifyProduct mapping exists, update Shopify data snapshot
    - If an Item with matching code exists, link it
    - If no Item exists, auto-create one with full account defaults

    Pulls: price, cost, product images, product type.
    Sets: sales_account, inventory_account, cogs_account, costing=WEIGHTED_AVERAGE.

    Returns CommandResult with counts: created, linked, updated, skipped.
    """
    from sales.models import Item

    from .models import ShopifyProduct

    if store.status != ShopifyStore.Status.ACTIVE:
        return CommandResult.fail("Store is not active.")

    if not store.access_token:
        return CommandResult.fail("No access token — reconnect the store.")

    # Ensure Shopify warehouse exists
    _ensure_shopify_warehouse(store)

    # Ensure Inventory + COGS accounts exist
    _ensure_inventory_accounts(store.company)

    company = store.company

    # Resolve default accounts from module mappings first, then fallback to params
    default_accounts = _resolve_default_item_accounts(company)
    inv_account = _resolve_account(company, inventory_account_id) or default_accounts.get("inventory")
    cogs_account = _resolve_account(company, cogs_account_id) or default_accounts.get("cogs")
    sales_account = default_accounts.get("sales")
    purchase_account = default_accounts.get("purchase")

    headers = {
        "X-Shopify-Access-Token": store.access_token,
        "Content-Type": "application/json",
    }

    created = 0
    linked = 0
    updated = 0
    skipped = 0
    errors = []

    url = f"https://{store.shop_domain}/admin/api/2025-01/products.json?limit=250"

    while url:
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("Shopify products API error: %s", e)
            return CommandResult.fail(f"Shopify API error: {e}")

        products = resp.json().get("products", [])

        # Collect inventory_item_ids to batch-fetch costs
        inventory_item_ids = []
        for product in products:
            for variant in product.get("variants", []):
                iid = variant.get("inventory_item_id")
                if iid:
                    inventory_item_ids.append(iid)

        # Batch fetch costs from Shopify Inventory Items API
        cost_map = _fetch_inventory_item_costs(store, inventory_item_ids, headers)

        for product in products:
            product_id = product.get("id")
            product_title = product.get("title", "")
            product_type = product.get("product_type", "")

            # Get product image URL (first image)
            images = product.get("images", [])
            image_url = images[0].get("src", "") if images else ""

            for variant in product.get("variants", []):
                variant_id = variant.get("id")
                sku = (variant.get("sku") or "").strip()

                if not sku:
                    skipped += 1
                    continue

                price = Decimal(str(variant.get("price", "0")))
                inv_item_id = variant.get("inventory_item_id")
                cost = cost_map.get(inv_item_id, Decimal("0"))
                variant_title = variant.get("title", "") if variant.get("title") != "Default Title" else ""

                # Check existing mapping
                mapping = ShopifyProduct.objects.filter(
                    company=company,
                    shopify_variant_id=variant_id,
                ).first()

                if mapping:
                    # Update snapshot
                    mapping.title = product_title
                    mapping.variant_title = variant_title
                    mapping.sku = sku
                    mapping.shopify_price = price
                    mapping.shopify_inventory_item_id = variant.get("inventory_item_id")
                    mapping.raw_data = variant
                    mapping.save()

                    # Update linked Item cost if auto-created and cost available
                    if mapping.auto_created and mapping.item and cost > 0:
                        with command_writes_allowed():
                            mapping.item.default_cost = cost
                            mapping.item.save(update_fields=["default_cost"])

                    updated += 1
                    continue

                # Find or create Item
                item = Item.objects.filter(company=company, code=sku).first()
                auto_created = False

                if not item:
                    item = _create_item_from_variant(
                        company,
                        sku,
                        product_title,
                        variant_title,
                        price,
                        cost,
                        inv_account,
                        cogs_account,
                        sales_account,
                        purchase_account,
                        image_url,
                    )
                    auto_created = True
                    created += 1
                else:
                    # Update existing item with cost + accounts if missing
                    _update_item_defaults(item, cost, inv_account, cogs_account, sales_account, purchase_account)
                    linked += 1

                # Create mapping
                ShopifyProduct.objects.create(
                    company=company,
                    store=store,
                    shopify_product_id=product_id,
                    shopify_variant_id=variant_id,
                    title=product_title,
                    variant_title=variant_title,
                    sku=sku,
                    shopify_price=price,
                    shopify_inventory_item_id=variant.get("inventory_item_id"),
                    item=item,
                    auto_created=auto_created,
                    raw_data=variant,
                )

        # Pagination: follow Link header
        url = _get_next_page_url(resp)

    logger.info(
        "Product sync for %s: %d created, %d linked, %d updated, %d skipped",
        store.shop_domain,
        created,
        linked,
        updated,
        skipped,
    )

    return CommandResult.ok(
        data={
            "created": created,
            "linked": linked,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
        }
    )


@transaction.atomic
def process_product_webhook(store: ShopifyStore, payload: dict) -> CommandResult:
    """
    Handle products/create and products/update webhooks.

    Updates ShopifyProduct mapping and linked Item if auto_created.
    If product_sync_enabled and no mapping exists, creates one.
    """
    from sales.models import Item

    from .models import ShopifyProduct

    company = store.company
    product_id = payload.get("id")
    product_title = payload.get("title", "")

    if not product_id:
        return CommandResult.fail("No product ID in payload.")

    created = 0
    updated = 0

    for variant in payload.get("variants", []):
        variant_id = variant.get("id")
        sku = (variant.get("sku") or "").strip()

        if not sku:
            continue

        price = Decimal(str(variant.get("price", "0")))
        variant_title = variant.get("title", "") if variant.get("title") != "Default Title" else ""

        mapping = ShopifyProduct.objects.filter(
            company=company,
            shopify_variant_id=variant_id,
        ).first()

        if mapping:
            # Update snapshot
            mapping.title = product_title
            mapping.variant_title = variant_title
            mapping.sku = sku
            mapping.shopify_price = price
            mapping.shopify_inventory_item_id = variant.get("inventory_item_id")
            mapping.raw_data = variant
            mapping.save()

            # Update auto-created Items (don't overwrite manually edited ones)
            if mapping.auto_created and mapping.item:
                with command_writes_allowed():
                    item = mapping.item
                    item.name = f"{product_title} - {variant_title}" if variant_title else product_title
                    item.default_unit_price = price
                    item.save(update_fields=["name", "default_unit_price", "updated_at"])

            updated += 1
        elif store.product_sync_enabled:
            # Auto-create mapping for new variants
            inv_account = _resolve_account(company, store.default_inventory_account_id)
            cogs_account = _resolve_account(company, store.default_cogs_account_id)

            item = Item.objects.filter(company=company, code=sku).first()
            auto_created = False

            if not item:
                item = _create_item_from_variant(
                    company,
                    sku,
                    product_title,
                    variant_title,
                    price,
                    inv_account,
                    cogs_account,
                )
                auto_created = True

            ShopifyProduct.objects.create(
                company=company,
                store=store,
                shopify_product_id=product_id,
                shopify_variant_id=variant_id,
                title=product_title,
                variant_title=variant_title,
                sku=sku,
                shopify_price=price,
                shopify_inventory_item_id=variant.get("inventory_item_id"),
                item=item,
                auto_created=auto_created,
                raw_data=variant,
            )
            created += 1

    return CommandResult.ok(data={"created": created, "updated": updated})


def _create_item_from_variant(
    company,
    sku,
    product_title,
    variant_title,
    price,
    cost,
    inv_account,
    cogs_account,
    sales_account,
    purchase_account,
    image_url="",
):
    """Create a Nxentra Item from a Shopify variant with full account defaults."""
    from sales.models import Item

    name = f"{product_title} - {variant_title}" if variant_title else product_title
    item_type = Item.ItemType.INVENTORY if (inv_account and cogs_account) else Item.ItemType.NON_STOCK

    with command_writes_allowed():
        item = Item.objects.create(
            company=company,
            code=sku,
            name=name[:255],
            item_type=item_type,
            default_unit_price=price,
            default_cost=cost,
            sales_account=sales_account,
            purchase_account=purchase_account,
            inventory_account=inv_account if item_type == Item.ItemType.INVENTORY else None,
            cogs_account=cogs_account if item_type == Item.ItemType.INVENTORY else None,
            costing_method=Item.CostingMethod.WEIGHTED_AVERAGE,
        )

    # Download and save product image if available
    if image_url:
        _download_item_image(item, image_url)

    logger.info(
        "Auto-created Item %s (%s) with cost=%s, accounts: sales=%s inv=%s cogs=%s",
        sku,
        item_type,
        cost,
        sales_account.code if sales_account else "none",
        inv_account.code if inv_account else "none",
        cogs_account.code if cogs_account else "none",
    )
    return item


def _update_item_defaults(item, cost, inv_account, cogs_account, sales_account, purchase_account):
    """Update an existing Item with missing defaults from Shopify."""
    updates = []
    if cost > 0 and not item.default_cost:
        item.default_cost = cost
        updates.append("default_cost")
    if sales_account and not item.sales_account:
        item.sales_account = sales_account
        updates.append("sales_account_id")
    if purchase_account and not item.purchase_account:
        item.purchase_account = purchase_account
        updates.append("purchase_account_id")
    if inv_account and not item.inventory_account:
        item.inventory_account = inv_account
        updates.append("inventory_account_id")
    if cogs_account and not item.cogs_account:
        item.cogs_account = cogs_account
        updates.append("cogs_account_id")
    if updates:
        with command_writes_allowed():
            item.save(update_fields=updates)


def _download_item_image(item, image_url):
    """Download a product image from Shopify and save to Item.image field."""
    try:
        resp = requests.get(image_url, timeout=15, stream=True)
        resp.raise_for_status()
        # Get filename from URL
        from urllib.parse import urlparse

        path = urlparse(image_url).path
        filename = path.split("/")[-1].split("?")[0] or "product.jpg"
        from django.core.files.base import ContentFile

        with command_writes_allowed():
            item.image.save(filename, ContentFile(resp.content), save=True)
        logger.info("Saved product image for Item %s", item.code)
    except Exception as exc:
        logger.warning("Failed to download image for Item %s: %s", item.code, exc)


def _resolve_account(company, account_id):
    """Resolve an account by ID, returning None if not found."""
    if not account_id:
        return None
    from accounting.models import Account

    return Account.objects.filter(company=company, id=account_id).first()


def _fetch_inventory_item_costs(store, inventory_item_ids, headers):
    """Batch fetch cost per item from Shopify Inventory Items API."""
    cost_map = {}
    if not inventory_item_ids:
        return cost_map

    # Shopify allows up to 100 IDs per request
    for i in range(0, len(inventory_item_ids), 100):
        batch = inventory_item_ids[i : i + 100]
        ids_param = ",".join(str(x) for x in batch)
        try:
            resp = requests.get(
                f"https://{store.shop_domain}/admin/api/2025-01/inventory_items.json",
                headers=headers,
                params={"ids": ids_param},
                timeout=15,
            )
            resp.raise_for_status()
            for item in resp.json().get("inventory_items", []):
                cost_str = item.get("cost")
                if cost_str:
                    cost_map[item["id"]] = Decimal(str(cost_str))
        except Exception as exc:
            logger.warning("Failed to fetch inventory item costs: %s", exc)

    return cost_map


def _ensure_inventory_accounts(company):
    """Ensure Inventory and COGS GL accounts exist for the company."""
    from accounting.models import Account
    from projections.write_barrier import projection_writes_allowed

    ACCOUNTS = [
        ("1300", "Inventory", "المخزون", "ASSET", "INVENTORY"),
        ("5100", "Cost of Goods Sold", "تكلفة البضاعة المباعة", "EXPENSE", "COGS"),
    ]
    with command_writes_allowed(), projection_writes_allowed():
        for code, name, name_ar, acct_type, role in ACCOUNTS:
            try:
                Account.objects.get_or_create(
                    company=company,
                    code=code,
                    defaults={
                        "name": name,
                        "name_ar": name_ar,
                        "account_type": acct_type,
                        "role": role,
                        "ledger_domain": "FINANCIAL",
                        "status": "ACTIVE",
                        "normal_balance": "DEBIT",
                    },
                )
            except Exception as exc:
                logger.warning("Failed to ensure account %s: %s", code, exc)


def _resolve_default_item_accounts(company):
    """Resolve default accounts for new Items from module mappings and GL."""
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account

    result = {}

    # Sales account from Shopify module mapping
    mapping = ModuleAccountMapping.get_mapping(company, "shopify_connector")
    if mapping:
        result["sales"] = mapping.get("SALES_REVENUE")
        result["purchase"] = mapping.get("SHOPIFY_CLEARING")

    # Inventory + COGS by account code convention
    result["inventory"] = Account.objects.filter(
        company=company,
        code="1300",
        status="ACTIVE",
    ).first()
    result["cogs"] = Account.objects.filter(
        company=company,
        code="5100",
        status="ACTIVE",
    ).first()

    return result


def _get_next_page_url(response):
    """Extract next page URL from Shopify's Link header pagination."""
    link_header = response.headers.get("Link", "")
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            url = part.split(";")[0].strip().strip("<>")
            return url
    return None
