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
# Keep in sync with shopify.app.toml [access_scopes].
# read_shopify_payments_accounts: required by the GraphQL
# shopifyPaymentsAccount field (payout sync); REST only needed _payouts.
# read_all_orders (A126): lifts the read_orders 60-day historical window.
# NOTE: settings.SHOPIFY_SCOPES shadows this default (getattr reads settings
# first), so the settings.py value is the effective one — keep both in sync.
SHOPIFY_SCOPES = getattr(
    settings,
    "SHOPIFY_SCOPES",
    "read_customers,read_discounts,read_fulfillments,read_inventory,read_locations,read_orders,read_all_orders,read_products,read_returns,read_shopify_payments_accounts,read_shopify_payments_payouts",
)
SHOPIFY_APP_URL = getattr(settings, "SHOPIFY_APP_URL", "")

# Shopify Admin API version lives in graphql_client (single Admin API entry
# point since the GraphQL migration). Re-exported here because callers and
# tests historically read it from commands.
from .graphql_client import (
    SHOPIFY_API_VERSION,
    ShopifyAdminClient,
    ShopifyGraphQLDenied,
)


def _shopify_api_root(shop_domain: str) -> str:
    return f"https://{shop_domain}/admin/api/{SHOPIFY_API_VERSION}"


def _admin_client(store) -> "ShopifyAdminClient | None":
    """ShopifyAdminClient for the store, or None when no valid token."""
    token = _get_valid_access_token(store)
    if not token:
        return None
    return ShopifyAdminClient(store.shop_domain, token)


def _schedule_initial_sync(store) -> None:
    """
    Queue the first data pull (orders 7d + products + payouts) right after a
    store connects. A broken Celery broker must never fail the OAuth flow —
    the 4-hour periodic catch-up covers the gap if the enqueue is lost.
    """
    try:
        from .tasks import initial_store_sync

        initial_store_sync.delay(store.id)
        logger.info("Queued initial Shopify sync for %s", store.shop_domain)
    except Exception as exc:
        logger.warning(
            "Could not queue initial Shopify sync for %s: %s",
            store.shop_domain,
            exc,
        )


def _shopify_access_denied(exc: "requests.RequestException") -> str | None:
    """
    Classify a Shopify Admin API error as a recoverable "access denied"
    condition.

    Returns a human-readable reason when Shopify replied with a 401/402/403/404
    (transport layer) or a GraphQL-level ACCESS_DENIED that the merchant can
    self-diagnose (missing scope, Shopify Payments not enabled, resource hidden
    behind app review). Returns None for everything else (real network failure,
    5xx, rate limit) — those should still bubble as command failures.

    This exists so the App Store reviewer's bare dev store (no Shopify Payments
    configured, no products created) stops seeing red "Failed to sync" toasts
    when the sync simply has nothing to do or is gated behind a permission the
    merchant must grant.
    """
    if isinstance(exc, ShopifyGraphQLDenied):
        return "Shopify denied access to this resource (missing scope or approval)."
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    code = resp.status_code
    if code in (401, 402, 403, 404):
        return f"Shopify returned HTTP {code} for this resource."
    return None


# B15 (2026-06-07): caller-facing classification of denial reasons. The
# generic _shopify_access_denied above lumps every 4xx together so callers
# can default to a graceful "skip" path. This helper inspects the body so
# the user-facing toast can tell *why* — token format vs scope vs payments
# not enabled. Misdiagnosis (a "deprecated token" looking like "missing
# scope") cost us hours of debug on 2026-06-07; never again.
def _shopify_denial_reason(exc: "requests.RequestException") -> str | None:
    """Returns one of: 'non_expiring_token', 'scope_missing', 'payments_disabled',
    'not_found', or None when the body doesn't match a known reason."""
    resp = getattr(exc, "response", None)
    if isinstance(exc, ShopifyGraphQLDenied):
        body = str(exc)
    else:
        if resp is None:
            return None
        try:
            body = resp.text or ""
        except Exception:
            body = ""
    lower = body.lower()
    if "non-expiring" in lower or "non expiring" in lower:
        return "non_expiring_token"
    if "scope" in lower or "access denied" in lower:
        return "scope_missing"
    if "shopify payments" in lower or ("payouts" in lower and "not" in lower):
        return "payments_disabled"
    if resp is not None and resp.status_code == 404:
        return "not_found"
    return None


# =============================================================================
# OAuth Commands
# =============================================================================


EMBEDDED_STATE_SUFFIX = ".embedded"


def get_install_url(company, shop_domain: str, embedded: bool = False) -> dict:
    """
    Generate the Shopify OAuth authorization URL.

    Returns {url, nonce} for the frontend to redirect the merchant.

    B17.2 (2026-06-07): when the merchant kicked off the OAuth from
    inside the Shopify admin iframe, append an `.embedded` suffix to
    the OAuth `state` so the callback can route the success redirect
    back to admin.shopify.com (B17). Standalone-started OAuth flows
    keep the bare nonce and land at app.nxentra.com/shopify/settings —
    the merchant stays in the context they started in.

    The stored ShopifyStore.oauth_nonce only holds the random nonce
    (suffix is consumed in the callback before lookup), so the DB
    schema stays unchanged.
    """
    nonce = secrets.token_urlsafe(32)
    state_in_url = nonce + (EMBEDDED_STATE_SUFFIX if embedded else "")

    # B2 (2026-06-04): sweep abandoned PENDING rows for this company before
    # creating the new one. OAuth round-trip normally completes in seconds; a
    # PENDING row older than an hour means the merchant bounced out of the
    # Shopify authorize screen. Leaving these rows behind is what produced the
    # App-Store-reviewer state on Shopify_R (multiple shop_domains tried over
    # rejection cycles, each leaving its own PENDING). The current shop_domain
    # is excluded so the update_or_create below cleanly refreshes the row a
    # legitimate re-attempt expects to see.
    from datetime import timedelta

    from django.utils import timezone as tz

    swept = (
        ShopifyStore.objects.filter(
            company=company,
            status=ShopifyStore.Status.PENDING,
            updated_at__lt=tz.now() - timedelta(hours=1),
        )
        .exclude(shop_domain=shop_domain)
        .delete()
    )
    swept_count = swept[0] if isinstance(swept, tuple) else 0

    # B3 (2026-06-04): never downgrade an ACTIVE store to PENDING when the
    # merchant re-clicks Connect on the same shop_domain. A re-auth (Shopify
    # scope-grant flow, recovery from a glitchy install) is legitimate and
    # must rotate the nonce, but the existing access_token + ACTIVE status
    # must keep working until the new OAuth callback succeeds. If callback
    # never fires we still serve API requests with the old token instead of
    # silently disconnecting the merchant for hours.
    existing = ShopifyStore.objects.filter(company=company, shop_domain=shop_domain).first()
    if existing and existing.status == ShopifyStore.Status.ACTIVE:
        existing.oauth_nonce = nonce
        existing.save(update_fields=["oauth_nonce", "updated_at"])
        existing_status_before = "ACTIVE"
    else:
        existing_status_before = existing.status if existing else "NONE"
        ShopifyStore.objects.update_or_create(
            company=company,
            shop_domain=shop_domain,
            defaults={
                "oauth_nonce": nonce,
                "status": ShopifyStore.Status.PENDING,
            },
        )

    logger.info(
        "shopify.install_url_generated company=%s shop=%s prior_status=%s swept_pending=%d",
        getattr(company, "id", None),
        shop_domain,
        existing_status_before,
        swept_count,
    )

    redirect_uri = f"{SHOPIFY_APP_URL}/api/shopify/callback/"
    url = (
        f"https://{shop_domain}/admin/oauth/authorize"
        f"?client_id={SHOPIFY_API_KEY}"
        f"&scope={SHOPIFY_SCOPES}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state_in_url}"
    )

    return {"url": url, "nonce": nonce}


@transaction.atomic
def complete_oauth(company, shop_domain: str, code: str, nonce: str) -> CommandResult:
    """
    Exchange the OAuth code for an expiring offline access token (A122).

    Sends `expiring=1` in the request body so Shopify returns a refreshable
    offline token (access_token valid ~1h + refresh_token valid ~90d) instead
    of the legacy permanent non-expiring token. Permanent tokens are
    deprecated and will be rejected entirely by 2027-01-01.
    """
    logger.info(
        "shopify.oauth_callback_received company=%s shop=%s",
        getattr(company, "id", None),
        shop_domain,
    )

    try:
        store = ShopifyStore.objects.get(company=company, shop_domain=shop_domain)
    except ShopifyStore.DoesNotExist:
        logger.warning(
            "shopify.oauth_failed reason=no_store company=%s shop=%s",
            getattr(company, "id", None),
            shop_domain,
        )
        return CommandResult.fail(f"No pending store for {shop_domain}.")

    if store.oauth_nonce != nonce:
        logger.warning(
            "shopify.oauth_failed reason=nonce_mismatch company=%s shop=%s store_id=%s",
            getattr(company, "id", None),
            shop_domain,
            store.id,
        )
        return CommandResult.fail("OAuth state mismatch — possible CSRF attack.")

    # Exchange code for expiring access token (A122).
    token_url = f"https://{shop_domain}/admin/oauth/access_token"
    try:
        resp = requests.post(
            token_url,
            json={
                "client_id": SHOPIFY_API_KEY,
                "client_secret": SHOPIFY_API_SECRET,
                "code": code,
                "expiring": 1,
            },
            timeout=15,
        )
        resp.raise_for_status()
        token_data = resp.json()
    except requests.RequestException as e:
        logger.warning(
            "shopify.token_exchange_failed company=%s shop=%s error=%s",
            getattr(company, "id", None),
            shop_domain,
            e,
        )
        with command_writes_allowed():
            store.status = ShopifyStore.Status.ERROR
            store.error_message = str(e)
            store.save()
        return CommandResult.fail(f"Failed to exchange OAuth code: {e}")

    access_token = token_data.get("access_token", "")
    scopes = token_data.get("scope", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in")
    refresh_token_expires_in = token_data.get("refresh_token_expires_in")

    # A122: compute absolute expiry timestamps from the relative seconds
    # Shopify returns. If we didn't actually get an expiring token (e.g.,
    # Shopify ignored `expiring=1` for some reason), token_expires_at stays
    # NULL and the token is treated as legacy permanent.
    from datetime import timedelta

    from django.utils import timezone as tz

    now = tz.now()
    token_expires_at = now + timedelta(seconds=int(expires_in)) if expires_in else None
    refresh_token_expires_at = (
        now + timedelta(seconds=int(refresh_token_expires_in)) if refresh_token_expires_in else None
    )

    try:
        with command_writes_allowed():
            store.access_token = access_token
            store.refresh_token = refresh_token
            store.token_expires_at = token_expires_at
            store.refresh_token_expires_at = refresh_token_expires_at
            store.scopes = scopes
            store.status = ShopifyStore.Status.ACTIVE
            store.oauth_nonce = ""
            store.error_message = ""
            store.save()
    except IntegrityError:
        logger.warning(
            "shopify.store_active_failed reason=domain_taken company=%s shop=%s",
            getattr(company, "id", None),
            shop_domain,
        )
        return CommandResult.fail(
            "This Shopify store is already connected to another Nxentra company. "
            "Disconnect it from the other company first."
        )

    logger.info(
        "shopify.store_marked_active company=%s shop=%s store_id=%s expires_at=%s",
        getattr(company, "id", None),
        shop_domain,
        store.id,
        token_expires_at.isoformat() if token_expires_at else None,
    )

    # Auto-create a Shopify warehouse for inventory tracking
    _ensure_shopify_warehouse(store)

    # Auto-create Customer + PostingProfile for Sales Invoice routing
    _ensure_shopify_sales_setup(store)

    # Kick off the first data pull so the dashboard isn't empty
    _schedule_initial_sync(store)

    # A51 (2026-05-15): emit SHOPIFY_STORE_CONNECTED on successful OAuth.
    # Previously this event was emitted from register_webhooks (now removed
    # — webhooks are subscribed declaratively in shopify.app.toml). The
    # natural emission point is here, when the store transitions
    # PENDING → ACTIVE with a valid access token.
    from events.emitter import emit_event_no_actor

    emit_event_no_actor(
        company=company,
        event_type=EventTypes.SHOPIFY_STORE_CONNECTED,
        aggregate_type="ShopifyStore",
        aggregate_id=str(store.public_id),
        idempotency_key=f"shopify.store.connected:{store.public_id}",
        data=ShopifyStoreConnectedData(
            store_public_id=str(store.public_id),
            shop_domain=store.shop_domain,
            company_public_id=str(company.public_id),
            connected_by_email="",
        ),
    )

    return CommandResult.ok(data={"store": store})


# =============================================================================
# B6 (2026-06-05): Shopify-initiated install support
# =============================================================================


def verify_shopify_oauth_hmac(params) -> bool:
    """
    Verify the HMAC signature on a Shopify OAuth callback URL.

    Shopify signs the OAuth callback's query string the same way it signs
    the launch handshake — alphabetically sort all params except `hmac`,
    join as `k=v&k=v...`, HMAC-SHA256 with the client_secret, compare
    hex-encoded.

    For Nxentra-initiated installs we rely on the state nonce we created
    in get_install_url for CSRF protection. For Shopify-initiated
    installs no state nonce exists (the install originates from Shopify
    Partners or App Store), so HMAC verification is the only defense
    against forged callbacks. Strict checking required.
    """
    import hmac as hmac_lib

    hmac_param = params.get("hmac", "")
    if not hmac_param or not SHOPIFY_API_SECRET:
        return False

    sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "hmac")
    computed = hmac_lib.new(
        SHOPIFY_API_SECRET.encode("utf-8"),
        sorted_params.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac_lib.compare_digest(computed, hmac_param)


def _exchange_oauth_code(shop_domain: str, code: str) -> tuple[str | None, dict | None]:
    """
    POST to Shopify's token exchange endpoint and return (error, token_data).

    Returns (None, token_data) on success, (error_message, None) on failure.
    Shared between Nxentra-initiated and Shopify-initiated flows.
    """
    token_url = f"https://{shop_domain}/admin/oauth/access_token"
    try:
        resp = requests.post(
            token_url,
            json={
                "client_id": SHOPIFY_API_KEY,
                "client_secret": SHOPIFY_API_SECRET,
                "code": code,
                "expiring": 1,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return None, resp.json()
    except requests.RequestException as e:
        return str(e), None


def complete_oauth_shopify_initiated(shop_domain: str, code: str) -> CommandResult:
    """
    B6 Phase 1: handle a Shopify-initiated install callback.

    The merchant clicked Install from the App Store listing or Partners
    Dashboard test install. Shopify called our callback with `code +
    shop`, no pre-existing PENDING ShopifyStore row (we never created
    one via get_install_url) and possibly no Nxentra session at all.

    OAuth codes are single-use and short-lived (Shopify caps them at
    ~10 min), so we must exchange the code for tokens immediately.
    The tokens go into a PendingShopifyInstall record; the caller then
    redirects through login + select-company → finalize_shopify_install
    finishes the install for the chosen company.
    """
    from datetime import timedelta

    from django.utils import timezone as tz

    from .models import PendingShopifyInstall

    logger.info("shopify.oauth_callback_received_shopify_initiated shop=%s", shop_domain)

    error, token_data = _exchange_oauth_code(shop_domain, code)
    if error:
        logger.warning(
            "shopify.token_exchange_failed_shopify_initiated shop=%s error=%s",
            shop_domain,
            error,
        )
        return CommandResult.fail(f"Failed to exchange OAuth code: {error}")

    access_token = token_data.get("access_token", "")
    if not access_token:
        return CommandResult.fail("Shopify returned no access_token.")

    expires_in = token_data.get("expires_in")
    refresh_token_expires_in = token_data.get("refresh_token_expires_in")
    now = tz.now()
    token_expires_at = now + timedelta(seconds=int(expires_in)) if expires_in else None
    refresh_token_expires_at = (
        now + timedelta(seconds=int(refresh_token_expires_in)) if refresh_token_expires_in else None
    )

    with command_writes_allowed():
        # 30 minute TTL — the merchant has to log in + pick company
        # within this window. If not, the install record expires and
        # they'd need to redo the Shopify install (~3 clicks).
        pending = PendingShopifyInstall.objects.create(
            shop_domain=shop_domain,
            access_token=access_token,
            refresh_token=token_data.get("refresh_token", ""),
            token_expires_at=token_expires_at,
            refresh_token_expires_at=refresh_token_expires_at,
            scopes=token_data.get("scope", ""),
            expires_at=now + timedelta(minutes=30),
        )

    logger.info(
        "shopify.pending_install_created shop=%s pending_id=%s expires_at=%s",
        shop_domain,
        pending.public_id,
        pending.expires_at.isoformat(),
    )

    return CommandResult.ok(data={"pending": pending})


@transaction.atomic
def finalize_shopify_install(company, pending_public_id: str) -> CommandResult:
    """
    B6 Phase 2: associate a PendingShopifyInstall with a company.

    Called after the merchant logged into Nxentra and selected their
    active company. Moves the saved tokens onto a real ShopifyStore row
    and runs the standard post-install setup (warehouse, customer,
    posting profile, store-connected event).
    """
    from django.utils import timezone as tz

    from .models import PendingShopifyInstall

    try:
        pending = PendingShopifyInstall.objects.get(
            public_id=pending_public_id,
            consumed_at__isnull=True,
        )
    except PendingShopifyInstall.DoesNotExist:
        return CommandResult.fail("Install record not found or already consumed.")

    if pending.expires_at < tz.now():
        return CommandResult.fail("Install record expired — please reinstall the app from Shopify.")

    shop_domain = pending.shop_domain

    # Reuse an existing ShopifyStore row for this (company, shop_domain) if
    # one exists (e.g. the merchant previously disconnected and is reinstalling)
    # — same pattern as complete_oauth + the B3 no-downgrade guard.
    existing = ShopifyStore.objects.filter(company=company, shop_domain=shop_domain).first()
    if existing:
        store = existing
    else:
        store = None

    try:
        with command_writes_allowed():
            if store is None:
                store = ShopifyStore.objects.create(
                    company=company,
                    shop_domain=shop_domain,
                    access_token=pending.access_token,
                    refresh_token=pending.refresh_token,
                    token_expires_at=pending.token_expires_at,
                    refresh_token_expires_at=pending.refresh_token_expires_at,
                    scopes=pending.scopes,
                    status=ShopifyStore.Status.ACTIVE,
                )
            else:
                store.access_token = pending.access_token
                store.refresh_token = pending.refresh_token
                store.token_expires_at = pending.token_expires_at
                store.refresh_token_expires_at = pending.refresh_token_expires_at
                store.scopes = pending.scopes
                store.status = ShopifyStore.Status.ACTIVE
                store.oauth_nonce = ""
                store.error_message = ""
                store.save()
    except IntegrityError:
        logger.warning(
            "shopify.store_active_failed_finalize reason=domain_taken company=%s shop=%s",
            getattr(company, "id", None),
            shop_domain,
        )
        return CommandResult.fail(
            "This Shopify store is already connected to another Nxentra company. "
            "Disconnect it from the other company first."
        )

    # Mark pending consumed so it can't be reused.
    with command_writes_allowed():
        pending.consumed_at = tz.now()
        pending.consumed_by_company = company
        pending.save(update_fields=["consumed_at", "consumed_by_company"])

    logger.info(
        "shopify.store_marked_active_finalize company=%s shop=%s store_id=%s",
        getattr(company, "id", None),
        shop_domain,
        store.id,
    )

    # Same post-install setup as complete_oauth
    _ensure_shopify_warehouse(store)
    _ensure_shopify_sales_setup(store)
    _schedule_initial_sync(store)

    from events.emitter import emit_event_no_actor

    emit_event_no_actor(
        company=company,
        event_type=EventTypes.SHOPIFY_STORE_CONNECTED,
        aggregate_type="ShopifyStore",
        aggregate_id=str(store.public_id),
        idempotency_key=f"shopify.store.connected:{store.public_id}",
        data=ShopifyStoreConnectedData(
            store_public_id=str(store.public_id),
            shop_domain=store.shop_domain,
            company_public_id=str(company.public_id),
            connected_by_email="",
        ),
    )

    return CommandResult.ok(data={"store": store})


# =============================================================================
# B8 (2026-06-05): Token Exchange — embedded-app install flow
# =============================================================================
#
# Shopify's Token Exchange API replaces OAuth code-grant for apps using
# the modern App Bridge install pattern. The merchant installs the app
# silently (no OAuth redirect), and when they open it Shopify gives App
# Bridge a short-lived session_token JWT signed with our client_secret.
# We exchange that JWT here for an offline access_token + refresh_token,
# bypassing the entire OAuth dance.
#
# This is the path required for Shopify's Dev Dashboard "Install app"
# button (which bypasses OAuth — confirmed via empty nginx logs on
# 2026-06-05) and for any future managed-install distribution.


def verify_shopify_session_token(session_token: str) -> dict | None:
    """
    Verify and decode a Shopify session token JWT.

    Shopify signs session tokens with our client_secret using HS256.
    Expected claims (Shopify spec):
        iss   issuer — the shop URL, e.g. https://x.myshopify.com/admin
        dest  destination — the shop URL, e.g. https://x.myshopify.com
        aud   audience — our client_id
        exp   expiration timestamp
        nbf   not-before timestamp
        iat   issued-at timestamp
        sub   subject — the Shopify user id who launched the app
        jti   unique JWT id
        sid   session id

    Returns the decoded claims dict on success, None on any failure
    (bad signature, expired, missing/wrong audience, missing client_id
    in settings).
    """
    import jwt as pyjwt

    if not SHOPIFY_API_SECRET or not SHOPIFY_API_KEY:
        logger.warning("shopify.session_token_verify_failed reason=missing_secret_or_key")
        return None

    try:
        claims = pyjwt.decode(
            session_token,
            SHOPIFY_API_SECRET,
            algorithms=["HS256"],
            audience=SHOPIFY_API_KEY,
            options={"require": ["exp", "iat", "iss", "dest", "aud"]},
        )
    except pyjwt.ExpiredSignatureError:
        logger.warning("shopify.session_token_verify_failed reason=expired")
        return None
    except pyjwt.InvalidAudienceError:
        logger.warning("shopify.session_token_verify_failed reason=bad_audience")
        return None
    except pyjwt.InvalidTokenError as e:
        logger.warning("shopify.session_token_verify_failed reason=invalid error=%s", e)
        return None

    return claims


def _extract_shop_domain_from_claims(claims: dict) -> str | None:
    """Pull `<shop>.myshopify.com` out of a session token's iss/dest claim."""
    from urllib.parse import urlparse

    for key in ("dest", "iss"):
        value = claims.get(key, "")
        if not value:
            continue
        parsed = urlparse(value)
        netloc = parsed.netloc or parsed.path  # tolerant of malformed URLs
        if netloc.endswith(".myshopify.com"):
            return netloc
    return None


@transaction.atomic
def complete_oauth_token_exchange(
    company,
    session_token: str,
    expected_shop_domain: str = "",
) -> CommandResult:
    """
    Phase 1 of the embedded install — exchange a session token for an
    offline access token and persist a ShopifyStore for the merchant's
    company.

    The frontend (App Bridge) gives us the session_token. We:
      1. Verify the JWT signature + expiry + audience.
      2. Extract shop_domain from the JWT claims (verify against the
         expected one if the caller supplied a hint).
      3. POST to Shopify's /admin/oauth/access_token with
         grant_type=urn:ietf:params:oauth:grant-type:token-exchange,
         passing the session_token as the subject_token and asking for
         an offline-access-token in return.
      4. Persist the resulting tokens onto a ShopifyStore row for the
         caller's company (reusing the row if one already exists, same
         pattern as complete_oauth / finalize_shopify_install).
      5. Run the standard post-install setup (warehouse + sales setup
         + SHOPIFY_STORE_CONNECTED event).

    Same idempotency story as the OAuth-code path: if the merchant
    re-launches the app, we re-exchange and refresh tokens on the
    same row — no duplicate stores.
    """
    logger.info(
        "shopify.token_exchange_start company=%s expected_shop=%s",
        getattr(company, "id", None),
        expected_shop_domain or "(none)",
    )

    claims = verify_shopify_session_token(session_token)
    if not claims:
        return CommandResult.fail("Invalid or expired session token.")

    claim_shop_domain = _extract_shop_domain_from_claims(claims)
    if not claim_shop_domain:
        return CommandResult.fail("Session token has no recognizable shop domain.")

    if expected_shop_domain and expected_shop_domain != claim_shop_domain:
        logger.warning(
            "shopify.token_exchange_shop_mismatch claimed=%s expected=%s",
            claim_shop_domain,
            expected_shop_domain,
        )
        return CommandResult.fail("Session token shop_domain does not match request.")

    shop_domain = claim_shop_domain

    # B15 (2026-06-07): request an EXPIRING offline token, same as A122 did
    # for the OAuth code-grant path. Without `expiring=1`, Shopify returns a
    # legacy non-expiring `shpat_*` token that the Admin API now rejects
    # outright ("Non-expiring access tokens are no longer accepted for the
    # Admin API"). Diagnosed via direct API call against a fresh token-
    # exchanged store on 2026-06-07.
    token_url = f"https://{shop_domain}/admin/oauth/access_token"
    try:
        resp = requests.post(
            token_url,
            json={
                "client_id": SHOPIFY_API_KEY,
                "client_secret": SHOPIFY_API_SECRET,
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": session_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
                "requested_token_type": "urn:shopify:params:oauth:token-type:offline-access-token",
                "expiring": 1,
            },
            timeout=15,
        )
        resp.raise_for_status()
        token_data = resp.json()
    except requests.RequestException as e:
        logger.warning(
            "shopify.token_exchange_failed shop=%s error=%s",
            shop_domain,
            e,
        )
        return CommandResult.fail(f"Token exchange failed: {e}")

    access_token = token_data.get("access_token", "")
    if not access_token:
        return CommandResult.fail("Token exchange returned no access_token.")

    expires_in = token_data.get("expires_in")
    refresh_token_expires_in = token_data.get("refresh_token_expires_in")
    scopes = token_data.get("scope", "")
    refresh_token = token_data.get("refresh_token", "")

    from datetime import timedelta

    from django.utils import timezone as tz

    now = tz.now()
    token_expires_at = now + timedelta(seconds=int(expires_in)) if expires_in else None
    refresh_token_expires_at = (
        now + timedelta(seconds=int(refresh_token_expires_in)) if refresh_token_expires_in else None
    )

    existing = ShopifyStore.objects.filter(company=company, shop_domain=shop_domain).first()

    try:
        with command_writes_allowed():
            if existing:
                existing.access_token = access_token
                existing.refresh_token = refresh_token
                existing.token_expires_at = token_expires_at
                existing.refresh_token_expires_at = refresh_token_expires_at
                existing.scopes = scopes
                existing.status = ShopifyStore.Status.ACTIVE
                existing.oauth_nonce = ""
                existing.error_message = ""
                existing.save()
                store = existing
            else:
                store = ShopifyStore.objects.create(
                    company=company,
                    shop_domain=shop_domain,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    token_expires_at=token_expires_at,
                    refresh_token_expires_at=refresh_token_expires_at,
                    scopes=scopes,
                    status=ShopifyStore.Status.ACTIVE,
                )
    except IntegrityError:
        logger.warning(
            "shopify.token_exchange_active_failed reason=domain_taken company=%s shop=%s",
            getattr(company, "id", None),
            shop_domain,
        )
        return CommandResult.fail(
            "This Shopify store is already connected to another Nxentra company. "
            "Disconnect it from the other company first."
        )

    logger.info(
        "shopify.token_exchange_completed company=%s shop=%s store_id=%s expires_at=%s",
        getattr(company, "id", None),
        shop_domain,
        store.id,
        token_expires_at.isoformat() if token_expires_at else None,
    )

    _ensure_shopify_warehouse(store)
    _ensure_shopify_sales_setup(store)
    _schedule_initial_sync(store)

    from events.emitter import emit_event_no_actor

    emit_event_no_actor(
        company=company,
        event_type=EventTypes.SHOPIFY_STORE_CONNECTED,
        aggregate_type="ShopifyStore",
        aggregate_id=str(store.public_id),
        idempotency_key=f"shopify.store.connected:{store.public_id}",
        data=ShopifyStoreConnectedData(
            store_public_id=str(store.public_id),
            shop_domain=store.shop_domain,
            company_public_id=str(company.public_id),
            connected_by_email="",
        ),
    )

    return CommandResult.ok(data={"store": store})


# =============================================================================
# A122: Rotating offline tokens — refresh + valid-token helpers
# =============================================================================


def _refresh_shopify_token(store: ShopifyStore) -> bool:
    """
    Refresh the store's access_token using its refresh_token.

    Returns True on success. Returns False when:
      - The store has no refresh_token (legacy permanent token from before
        A122, or never completed OAuth)
      - The refresh_token itself has expired (>90d since last issue), in
        which case the merchant must re-authorize
      - Shopify rejects the refresh request for any reason

    Caller is responsible for surfacing a re-auth prompt to the merchant
    when this returns False on an ACTIVE store.
    """
    if not store.refresh_token:
        return False

    from django.utils import timezone as tz

    if store.refresh_token_expires_at and store.refresh_token_expires_at <= tz.now():
        logger.warning(
            "Shopify refresh_token expired for %s — merchant must re-OAuth",
            store.shop_domain,
        )
        return False

    token_url = f"https://{store.shop_domain}/admin/oauth/access_token"
    try:
        resp = requests.post(
            token_url,
            json={
                "client_id": SHOPIFY_API_KEY,
                "client_secret": SHOPIFY_API_SECRET,
                "refresh_token": store.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error(
            "Failed to refresh Shopify token for %s: %s",
            store.shop_domain,
            e,
        )
        return False

    from datetime import timedelta

    now = tz.now()
    access_token = data.get("access_token")
    if not access_token:
        logger.error(
            "Shopify refresh response missing access_token for %s",
            store.shop_domain,
        )
        return False

    new_refresh_token = data.get("refresh_token", store.refresh_token)
    expires_in = data.get("expires_in")
    refresh_token_expires_in = data.get("refresh_token_expires_in")

    with command_writes_allowed():
        store.access_token = access_token
        store.refresh_token = new_refresh_token
        if expires_in:
            store.token_expires_at = now + timedelta(seconds=int(expires_in))
        if refresh_token_expires_in:
            store.refresh_token_expires_at = now + timedelta(seconds=int(refresh_token_expires_in))
        store.save(
            update_fields=[
                "access_token",
                "refresh_token",
                "token_expires_at",
                "refresh_token_expires_at",
                "updated_at",
            ]
        )

    logger.info("Refreshed Shopify access_token for %s", store.shop_domain)
    return True


def _get_valid_access_token(store: ShopifyStore) -> str | None:
    """
    Return a valid Shopify access_token, refreshing on-the-fly if expired.

    Returns None when the token can't be made valid — caller should surface a
    "please reconnect this store" message to the merchant.

    Behavior:
      - Legacy stores (no token_expires_at): return access_token as-is. These
        permanent tokens still work until Shopify cuts them off entirely
        (deadline 2027-01-01).
      - A122 stores (token_expires_at set): refresh if expired or expiring
        within the next 60 seconds (buffer to avoid mid-call expiry).
    """
    if not store.access_token:
        return None

    if store.token_expires_at:
        from datetime import timedelta

        from django.utils import timezone as tz

        buffer = timedelta(seconds=60)
        if tz.now() + buffer >= store.token_expires_at:
            if not _refresh_shopify_token(store):
                return None

    return store.access_token


@transaction.atomic
def disconnect_store(actor: ActorContext, store_public_id: str = None) -> CommandResult:
    """Disconnect a Shopify store.

    If ``store_public_id`` is given, disconnects exactly that store. If it is
    omitted, the command auto-selects ONLY when the company has a single
    connected store; with two or more it refuses rather than guessing.

    A136 (sibling of A134): the previous bare
    ``.exclude(DISCONNECTED).first()`` fallback silently disconnected an
    arbitrary store for a multi-store merchant (the B8.5 multi-region
    incident), stranding the wrong store's order/refund/payout sync. The
    UI should pass the public_id of the store the merchant picked.
    """
    require(actor, "settings.edit")

    try:
        if store_public_id:
            store = ShopifyStore.objects.get(
                company=actor.company,
                public_id=store_public_id,
            )
        else:
            connected = list(
                ShopifyStore.objects.filter(company=actor.company).exclude(status=ShopifyStore.Status.DISCONNECTED)[:2]
            )
            if len(connected) > 1:
                return CommandResult.fail(
                    "Multiple connected Shopify stores — specify which one to disconnect (store_public_id is required)."
                )
            if not connected:
                raise ShopifyStore.DoesNotExist
            store = connected[0]
    except ShopifyStore.DoesNotExist:
        return CommandResult.fail("No connected store.")

    with command_writes_allowed():
        store.status = ShopifyStore.Status.DISCONNECTED
        store.access_token = ""
        # A47: also clear the rotating refresh token + expiries — otherwise a
        # disconnected store keeps a live shprt_* token that can re-mint access.
        store.refresh_token = ""
        store.token_expires_at = None
        store.refresh_token_expires_at = None
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

    # Idempotency: skip if a record already exists AND has been posted to
    # accounting (event emitted). A PENDING_CAPTURE metadata stub from
    # orders/create is upgraded below rather than blocking processing.
    existing_order = ShopifyOrder.objects.filter(
        company=store.company,
        shopify_order_id=shopify_order_id,
    ).first()
    if existing_order and existing_order.event_id:
        logger.info("Order %s already processed — skipping", shopify_order_id)
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
        order_fields = {
            "store": store,
            "shopify_order_number": str(payload.get("order_number", "")),
            "shopify_order_name": payload.get("name", ""),
            "total_price": total_price,
            "subtotal_price": subtotal_price,
            "total_tax": total_tax,
            "total_discounts": total_discounts,
            "currency": currency,
            "financial_status": payload.get("financial_status", ""),
            "gateway": _extract_gateway(payload),
            "shopify_created_at": order_date_str or datetime.now().isoformat(),
            "order_date": order_date,
            "raw_payload": payload,
            "status": ShopifyOrder.Status.RECEIVED,
        }
        if existing_order:
            # Upgrade a PENDING_CAPTURE stub from orders/create
            for field, value in order_fields.items():
                setattr(existing_order, field, value)
            existing_order.save()
            order = existing_order
        else:
            order = ShopifyOrder.objects.create(
                company=store.company,
                shopify_order_id=shopify_order_id,
                **order_fields,
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

        # Auto-create Item if no matching Item in Nxentra. Egyptian
        # merchants frequently sell items without SKUs (custom / one-off
        # products); the helper falls back to a synthetic code derived
        # from the Shopify variant_id when sku is empty (A9).
        _auto_create_item_from_line(store, sku, item)

    # Extract customer info. Shopify sends "customer": null when the order
    # has no customer attached (e.g. admin-created draft marked-as-paid),
    # so dict.get's default doesn't help — coerce explicitly.
    customer = payload.get("customer") or {}

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
def process_order_pending(store: ShopifyStore, payload: dict) -> CommandResult:
    """
    Process an orders/create webhook.

    Captures the order as metadata-only (no SalesInvoice, no JE). The order
    will be promoted by process_order_paid once Shopify marks it paid.

    If the order is already paid at creation time (e.g. Paymob/PayPal where
    payment clears before the webhook), route directly to process_order_paid.
    """
    shopify_order_id = payload.get("id")
    if not shopify_order_id:
        return CommandResult.fail("Missing order ID in payload.")

    financial_status = (payload.get("financial_status") or "").lower()
    if financial_status in ("paid", "authorized", "partially_paid"):
        return process_order_paid(store, payload)

    # Skip if we already have any record (idempotent — orders/create can fire twice)
    if ShopifyOrder.objects.filter(
        company=store.company,
        shopify_order_id=shopify_order_id,
    ).exists():
        return CommandResult.ok(data={"skipped": True})

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
            status=ShopifyOrder.Status.PENDING_CAPTURE,
        )

    return CommandResult.ok(data={"order": order, "captured_pending": True})


@transaction.atomic
def process_order_cancelled(store: ShopifyStore, payload: dict) -> CommandResult:
    """
    Process an orders/cancelled webhook.

    For pending orders never posted to accounting, mark the local record
    CANCELLED (no JE impact — nothing was booked).

    For already-posted orders, Shopify will also fire refunds/create if the
    cancellation included a refund; we let the existing refund handler do
    the accounting reversal. Here we just update the local status for audit.
    """
    shopify_order_id = payload.get("id")
    if not shopify_order_id:
        return CommandResult.fail("Missing order ID in payload.")

    order = ShopifyOrder.objects.filter(
        company=store.company,
        shopify_order_id=shopify_order_id,
    ).first()
    if not order:
        return CommandResult.ok(data={"skipped": True, "reason": "not_captured"})

    with command_writes_allowed():
        if order.status == ShopifyOrder.Status.PENDING_CAPTURE:
            order.status = ShopifyOrder.Status.CANCELLED
            order.save(update_fields=["status"])
            return CommandResult.ok(data={"order": order, "cancelled_pending": True})

        # Order was already posted — just note the cancellation on the raw payload.
        # The refund webhook (if any) handles the accounting reversal.
        order.raw_payload = {**(order.raw_payload or {}), "cancelled_at": payload.get("cancelled_at")}
        order.save(update_fields=["raw_payload"])
        logger.info(
            "Shopify order %s cancelled after posting — refund webhook will handle any reversal",
            shopify_order_id,
        )
        return CommandResult.ok(data={"order": order, "cancelled_after_post": True})


@transaction.atomic
def process_app_uninstalled(store: ShopifyStore, payload: dict) -> CommandResult:
    """Handle app/uninstalled webhook — mark store as disconnected."""
    with command_writes_allowed():
        store.status = ShopifyStore.Status.DISCONNECTED
        store.access_token = ""
        # A47: clear the rotating refresh token + expiries on uninstall too.
        store.refresh_token = ""
        store.token_expires_at = None
        store.refresh_token_expires_at = None
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
# GDPR Compliance Webhook Handlers (A44)
# =============================================================================
#
# Shopify-mandatory: customers/data_request, customers/redact, shop/redact.
# Every app — public or unlisted — must respond 200 to all three or Shopify
# disables the app silently.
#
# These handlers only write the audit row; the actual data work (customer
# data export, customer record deletion, shop data wipe) runs asynchronously
# and stays in PENDING status until implemented downstream. Shopify only
# requires the 200 ack on the webhook itself.
#
# Idempotency: dedupe on (topic, payload_signature). The webhook view passes
# the SHA-256 of the raw body, so a retry of an identical webhook trips the
# unique constraint and we return success without re-processing.


def _record_gdpr_request(
    topic: str,
    shop_domain: str,
    payload: dict,
    payload_signature: str,
    customer_id: int | None = None,
    customer_email: str = "",
) -> CommandResult:
    """Insert (or no-op on retry) a GdprRequest audit row."""
    from .models import GdprRequest

    shop_id = payload.get("shop_id")

    with command_writes_allowed():
        try:
            with transaction.atomic():
                req = GdprRequest.objects.create(
                    topic=topic,
                    shop_domain=shop_domain,
                    shop_id=shop_id,
                    customer_id=customer_id,
                    customer_email=customer_email or "",
                    payload=payload,
                    payload_signature=payload_signature,
                    status=GdprRequest.Status.PENDING,
                )
        except IntegrityError:
            # Shopify retry of an identical body — already audited.
            logger.info(
                "GDPR webhook retry deduped: topic=%s shop=%s",
                topic,
                shop_domain,
            )
            return CommandResult.ok(data={"deduped": True})

    return CommandResult.ok(data={"gdpr_request_id": req.id})


def process_customers_data_request(
    shop_domain: str,
    payload: dict,
    payload_signature: str,
) -> CommandResult:
    """
    Handle customers/data_request — merchant requested data of one of their customers.

    We have 30 days to deliver the data; for now we only audit the request.
    Actual export job is a future task.
    """
    customer = payload.get("customer") or {}
    return _record_gdpr_request(
        topic="customers/data_request",
        shop_domain=shop_domain,
        payload=payload,
        payload_signature=payload_signature,
        customer_id=customer.get("id"),
        customer_email=customer.get("email", ""),
    )


def process_customers_redact(
    shop_domain: str,
    payload: dict,
    payload_signature: str,
) -> CommandResult:
    """
    Handle customers/redact — merchant requested deletion of a customer's data.

    Audited only; actual deletion job is a future task.
    """
    customer = payload.get("customer") or {}
    return _record_gdpr_request(
        topic="customers/redact",
        shop_domain=shop_domain,
        payload=payload,
        payload_signature=payload_signature,
        customer_id=customer.get("id"),
        customer_email=customer.get("email", ""),
    )


def process_shop_redact(
    shop_domain: str,
    payload: dict,
    payload_signature: str,
) -> CommandResult:
    """
    Handle shop/redact — fires 48h after app uninstall; merchant data must be deleted.

    Audited only; actual wipe job is a future task. Note this webhook may arrive
    after the ShopifyStore record itself has been deleted, so we don't look one up.
    """
    return _record_gdpr_request(
        topic="shop/redact",
        shop_domain=shop_domain,
        payload=payload,
        payload_signature=payload_signature,
    )


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

    client = _admin_client(store)
    if not client:
        return CommandResult.fail("Token expired or revoked — please reconnect the store.")

    payouts_unavailable = CommandResult.ok(
        data={
            "created": 0,
            "skipped": 0,
            "status": "unavailable",
            "message": (
                "Shopify Payments isn't available on this store. "
                "Enable Shopify Payments in the store admin to start "
                "syncing payouts."
            ),
        }
    )

    try:
        payouts_data = client.list_payouts(status="paid", limit=50)
    except requests.RequestException as e:
        # Shopify denies this resource when the store hasn't enabled Shopify
        # Payments — the default state of every fresh dev store the App Store
        # reviewer creates. Treat that as "nothing to sync" rather than an
        # error so the reviewer doesn't see a red toast on first connect.
        denial = _shopify_access_denied(e)
        if denial:
            logger.info(
                "Skipping payout sync for %s: %s (likely Shopify Payments not enabled or scope not granted)",
                store.shop_domain,
                denial,
            )
            return payouts_unavailable
        logger.error("Failed to fetch payouts from Shopify: %s", e)
        return CommandResult.fail(f"Shopify API error: {e}")

    if payouts_data is None:
        # GraphQL exposes "no Shopify Payments" as shopifyPaymentsAccount: null
        # rather than an error — same outcome as the denied case above.
        logger.info(
            "Skipping payout sync for %s: no Shopify Payments account exposed",
            store.shop_domain,
        )
        return payouts_unavailable

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
    client = _admin_client(store)
    if not client:
        return CommandResult.fail("Token expired or revoked — please reconnect the store.")

    # Skip if transactions already fetched
    if payout.transactions.exists():
        return CommandResult.ok(data={"skipped": True, "reason": "Transactions already fetched."})

    try:
        transactions = client.list_payout_transactions(payout.shopify_payout_id, limit=250)
    except requests.RequestException as e:
        logger.error("Failed to fetch payout transactions: %s", e)
        return CommandResult.fail(f"Shopify API error: {e}")

    if transactions is None:
        return CommandResult.fail("Shopify Payments isn't available on this store.")

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
        sku = str(li.get("sku") or "").strip()
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
                with projection_writes_allowed():
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

    client = _admin_client(store)
    if not client:
        # Can't reach Shopify with a valid token — an empty token still
        # exercises the API call so we hit the fallback-warehouse branch.
        logger.warning(
            "Skipping location sync for %s: no valid access token",
            store.shop_domain,
        )
        client = ShopifyAdminClient(store.shop_domain, "")

    locations = []
    try:
        locations = client.list_locations()
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

        # Ensure at least one default warehouse exists (must stay inside
        # command_writes_allowed — Warehouse is a projection-owned model).
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

    # Skip customer/profile creation if already set up — but still re-run
    # the idempotent provider bootstrap: it's the canonical "make provider
    # state correct" function, and stores configured before a change to
    # _SHOPIFY_DEFAULT_PROVIDERS (e.g. `bogus`, 2026-06-12) or before the
    # needs_review healing would otherwise never receive it.
    if store.default_customer_id and store.default_posting_profile_id:
        mapping = ModuleAccountMapping.get_mapping(company, "shopify_connector")
        clearing_account = mapping.get("SHOPIFY_CLEARING") if mapping else None
        if clearing_account:
            with command_writes_allowed(), projection_writes_allowed():
                _bootstrap_shopify_settlement_providers(
                    company,
                    clearing_account,
                    store.default_posting_profile,
                )
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
                "currency": company.default_currency,
                "payment_terms_days": 0,
                "notes": f"Auto-created for Shopify store {store.shop_domain}",
                "status": Customer.Status.ACTIVE,
            },
        )

        # 3. Create or get PostingProfile (using Clearing as control account).
        # A78: usage=GATEWAY so it's hidden from manual-entry dropdowns and
        # the command-layer guard rejects accidental manual use.
        profile, profile_created = PostingProfile.objects.get_or_create(
            company=company,
            code=f"SHOPIFY-{store_label.upper()[:10]}",
            defaults={
                "name": f"Shopify: {store_label}",
                "name_ar": f"شوبيفاي: {store_label}",
                "profile_type": PostingProfile.ProfileType.CUSTOMER,
                "usage": PostingProfile.Usage.GATEWAY,
                "control_account": clearing_account,
                "is_active": True,
                "description": (
                    f"Auto-created for Shopify store {store.shop_domain}. "
                    f"Uses Shopify Clearing as control account (funds held by Shopify until payout)."
                ),
            },
        )
        if not profile_created and profile.usage != PostingProfile.Usage.GATEWAY:
            # Pre-A78 row from before usage existed; promote it.
            profile.usage = PostingProfile.Usage.GATEWAY
            profile.save(update_fields=["usage", "updated_at"])

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

    # 5. Bootstrap per-provider SettlementProvider rows. Each row gets its
    #    own PostingProfile, all initially anchored on the same
    #    SHOPIFY_CLEARING account. The merchant can later edit any one
    #    profile's control_account to point at a distinct sub-account
    #    (e.g. Paymob Clearing 11501) — at which point Paymob orders begin
    #    routing there, and historical postings stay where they were.
    _bootstrap_shopify_settlement_providers(company, clearing_account, profile)


# Default settlement-provider codes seen in Shopify payloads for MENA +
# global merchants. Keep small and obvious — the lazy-create path handles
# anything else. Note: `cash_on_delivery` is preserved as INACTIVE for
# historical compatibility (A2 created it). A12 routes COD orders via
# ShopifyStore.default_cod_settlement_provider (Bosta / DHL / Aramex /
# Mylerz) instead of looking up by the raw "cash_on_delivery" gateway
# string. Bosta is the suggested default for Egyptian merchants but is
# not auto-selected for the store — explicit configuration only.
_SHOPIFY_DEFAULT_PROVIDERS = (
    ("paymob", "Paymob", "gateway", True),
    ("paypal", "PayPal", "gateway", True),
    ("shopify_payments", "Shopify Payments", "gateway", True),
    ("manual", "Manual", "manual", True),
    ("bank_transfer", "Bank Transfer", "bank_transfer", True),
    ("bosta", "Bosta", "courier", True),
    ("unknown", "Unknown / Default", "manual", True),
    # Shopify's dev-store test gateway. Every App Store reviewer order (and
    # every merchant trial on a dev store) comes through it — without a
    # default it lazy-creates with needs_review=True and the reconciliation
    # page shows an unexplained "Review" badge on the reviewer's screen.
    ("bogus", "Bogus Gateway (Shopify test)", "gateway", True),
    # Transitional: A2 created cash_on_delivery as a provider; A12 routes
    # COD orders via ShopifyStore.default_cod_settlement_provider instead.
    # Kept inactive so it doesn't pollute reconciliation but historical
    # JEs (already posted before A12) still resolve their dimension tag.
    ("cash_on_delivery", "Cash on Delivery (deprecated)", "manual", False),
)


def _provider_profile_code(normalized_code: str) -> str:
    """Build a deterministic PostingProfile.code for a settlement provider.

    PostingProfile.code is max 20 chars and unique per company. Use a
    short prefix so provider profiles don't collide with the store-level
    default profile (`SHOPIFY-<store_label>`).
    """
    return f"PG-{normalized_code.upper()}"[:20]


def _bootstrap_shopify_settlement_providers(company, clearing_account, fallback_profile):
    """Create per-provider PostingProfile + SettlementProvider rows.

    Idempotent — uses get_or_create on both. Initially every provider points
    at the same `clearing_account` (via its dedicated PostingProfile);
    later edits to any one PostingProfile.control_account split a provider
    onto its own clearing sub-account.

    A12: also creates the SETTLEMENT_PROVIDER AnalysisDimension + one
    AnalysisDimensionValue per provider, and populates
    SettlementProvider.dimension_value. The reconciliation engine pivots
    JE lines on (clearing_account, dimension_value).

    For SettlementProvider rows that already existed from A2 without the
    dimension_value FK populated, this function backfills the FK on
    re-run so the bootstrap is the canonical "make this state correct"
    function.

    `fallback_profile` is reserved for the lazy-create path in projections
    (unknown gateway codes); we don't reference it here, but pass it
    through for symmetry / future use.
    """
    from accounting.models import AccountDimensionRule
    from accounting.settlement_provider import (
        SettlementProvider,
        ensure_settlement_provider_dimension,
        ensure_settlement_provider_dimension_value,
        normalize_gateway_code,
    )
    from sales.models import PostingProfile

    # 1. Ensure the SETTLEMENT_PROVIDER AnalysisDimension exists.
    dimension = ensure_settlement_provider_dimension(company)

    # 1b. Require the SETTLEMENT_PROVIDER dimension on the clearing account.
    # A12 follow-up: any JE line touching this clearing account must carry
    # the dimension tag so reconciliation queries are complete. Manual JEs
    # to clearing without the tag get rejected by post_journal_entry's
    # validate_line_dimensions check. Idempotent via update_or_create.
    AccountDimensionRule.objects.update_or_create(
        company=company,
        account=clearing_account,
        dimension=dimension,
        defaults={"rule_type": AccountDimensionRule.RuleType.REQUIRED},
    )

    with command_writes_allowed(), projection_writes_allowed():
        for raw_code, display_name, provider_type, is_active in _SHOPIFY_DEFAULT_PROVIDERS:
            normalized = normalize_gateway_code(raw_code)
            profile_code = _provider_profile_code(normalized)

            # 2. PostingProfile per provider (anchored on clearing initially).
            # A78: usage=GATEWAY — hidden from manual dropdowns.
            provider_profile, provider_created = PostingProfile.objects.get_or_create(
                company=company,
                code=profile_code,
                defaults={
                    "name": f"Shopify Provider: {display_name}",
                    "name_ar": f"بوابة شوبيفاي: {display_name}",
                    "profile_type": PostingProfile.ProfileType.CUSTOMER,
                    "usage": PostingProfile.Usage.GATEWAY,
                    "control_account": clearing_account,
                    "is_active": True,
                    "description": (
                        f"Auto-created for {display_name} routing. Initially "
                        f"points at the same Shopify Clearing account; edit "
                        f"control_account here to split this provider onto "
                        f"its own clearing sub-account."
                    ),
                },
            )
            if not provider_created and provider_profile.usage != PostingProfile.Usage.GATEWAY:
                provider_profile.usage = PostingProfile.Usage.GATEWAY
                provider_profile.save(update_fields=["usage", "updated_at"])

            # 3. AnalysisDimensionValue for the reconciliation tag.
            dimension_value = ensure_settlement_provider_dimension_value(
                dimension=dimension,
                normalized_code=normalized,
                display_name=display_name,
            )

            # 4. SettlementProvider row, with dimension_value populated.
            provider, _ = SettlementProvider.objects.get_or_create(
                company=company,
                external_system="shopify",
                normalized_code=normalized,
                defaults={
                    "source_code": raw_code,
                    "display_name": display_name,
                    "provider_type": provider_type,
                    "posting_profile": provider_profile,
                    "dimension_value": dimension_value,
                    "is_active": is_active,
                    "needs_review": False,
                },
            )
            # Backfill on re-run: existing rows from A2 won't have
            # dimension_value populated; set it now. Also enforce the
            # is_active flag for cash_on_delivery (deprecated) so
            # re-running bootstrap on an A2-era company deactivates it.
            updates = []
            if provider.dimension_value_id != dimension_value.id:
                provider.dimension_value = dimension_value
                updates.append("dimension_value")
            if provider.is_active != is_active and normalized == "cash_on_delivery":
                provider.is_active = is_active
                updates.append("is_active")
            # A defaults-listed code is by definition known: clear the review
            # flag a lazy-create may have set before the code joined this
            # list (e.g. `bogus` rows created by reviewer test orders before
            # 2026-06-12).
            if provider.needs_review:
                provider.needs_review = False
                updates.append("needs_review")
            if updates:
                provider.save(update_fields=[*updates, "updated_at"])


def _convert_costs_to_functional(store, client, cost_map: dict) -> dict:
    """
    Convert a {inventory_item_id: cost} map from the Shopify store currency
    to the company's functional currency. Returns the map unchanged when no
    conversion is needed or no exchange rate is configured (logged).
    """
    if not cost_map:
        return cost_map

    company = store.company
    functional = getattr(company, "functional_currency", "") or company.default_currency
    if not functional:
        return cost_map

    store_currency = _get_shopify_store_currency(store, client)
    if not store_currency or store_currency == functional:
        return cost_map

    from datetime import date as date_type

    from accounting.models import ExchangeRate

    rate = ExchangeRate.get_rate(company, store_currency, functional, date_type.today())
    if not rate or rate <= 0:
        logger.warning(
            "No exchange rate for %s→%s — storing Shopify costs unconverted. "
            "Set up exchange rates to enable conversion.",
            store_currency,
            functional,
        )
        return cost_map

    return {iid: (cost * rate).quantize(Decimal("0.01")) for iid, cost in cost_map.items()}


def _fetch_variant_cost(store, variant_id, convert_to_currency: str = "") -> Decimal:
    """Fetch cost_per_item for a single variant from Shopify API.

    Shopify returns cost in the store's currency. If convert_to_currency
    is provided and differs from the store currency, the cost is converted
    using the current exchange rate.

    Args:
        store: ShopifyStore instance
        variant_id: Shopify variant ID
        convert_to_currency: Target currency (e.g., company's functional currency).
            If empty, returns cost in store currency as-is.

    Returns:
        Cost in target currency (or store currency if no conversion needed).
    """
    if not variant_id:
        return Decimal("0")

    client = _admin_client(store)
    if not client:
        return Decimal("0")

    try:
        # unitCost carries its own currency, so a single query replaces the
        # old variant -> inventory_item -> shop currency REST chain.
        cost_str, cost_currency = client.get_variant_unit_cost(variant_id)
        cost = Decimal(cost_str)

        if cost <= 0 or not convert_to_currency:
            return cost

        store_currency = cost_currency or _get_shopify_store_currency(store, client)

        if not store_currency or store_currency == convert_to_currency:
            return cost

        # Convert cost to company's functional currency
        from datetime import date as date_type

        from accounting.models import ExchangeRate

        rate = ExchangeRate.get_rate(
            store.company,
            store_currency,
            convert_to_currency,
            date_type.today(),
        )
        if rate and rate > 0:
            converted = (cost * rate).quantize(Decimal("0.01"))
            logger.info(
                "Converted item cost %s %s → %s %s (rate %s) for variant %s",
                store_currency,
                cost,
                convert_to_currency,
                converted,
                rate,
                variant_id,
            )
            return converted
        else:
            logger.warning(
                "No exchange rate for %s→%s — storing cost as-is (%s). Set up exchange rates to enable conversion.",
                store_currency,
                convert_to_currency,
                cost,
            )
            return cost

    except Exception as exc:
        logger.warning("Failed to fetch variant cost for %s: %s", variant_id, exc)
        return Decimal("0")


def _get_shopify_store_currency(store, client=None) -> str:
    """Fetch the store's currency from Shopify's shop endpoint.

    Caches the result on the store instance to avoid repeated API calls.
    """
    # Check if we already know the currency from a recent order
    from shopify_connector.models import ShopifyOrder

    recent_order = (
        ShopifyOrder.objects.filter(
            company=store.company,
            store=store,
            currency__gt="",
        )
        .values_list("currency", flat=True)
        .first()
    )
    if recent_order:
        return recent_order

    # Fall back to Shopify shop API
    if client is None:
        client = _admin_client(store)
        if not client:
            return ""
    try:
        return client.get_shop_currency()
    except Exception as exc:
        logger.warning("Failed to fetch store currency for %s: %s", store.shop_domain, exc)
        return ""


def _auto_create_item_from_line(store, sku: str, line_item: dict):
    """Auto-create a Nxentra Item from a Shopify order line item if no match exists.

    A9: when sku is empty (common for Egyptian merchants selling custom /
    one-off products), fall back to a synthetic code derived from the
    Shopify variant_id (or product_id). The variant_id check below is the
    durable identity — re-orders for the same variant find the existing
    mapping and skip, regardless of whether the SKU is set.
    """
    from sales.models import Item
    from shopify_connector.models import ShopifyProduct

    sku = (sku or "").strip()
    variant_id = line_item.get("variant_id")
    product_id = line_item.get("product_id")

    if sku:
        code = sku
    elif variant_id:
        code = f"SHOP-{variant_id}"
    elif product_id:
        code = f"SHOP-PROD-{product_id}"
    else:
        # No identifiable handle — nothing we can create deterministically.
        return

    # Skip if Item already exists for this code
    if Item.objects.filter(company=store.company, code=code).exists():
        return
    # Skip if ShopifyProduct mapping already exists for this variant.
    # variant_id is the per-company unique identity; sku-keyed lookup
    # would false-positive across multiple empty-SKU products.
    if variant_id and ShopifyProduct.objects.filter(company=store.company, shopify_variant_id=variant_id).exists():
        return
    if sku and ShopifyProduct.objects.filter(company=store.company, sku=sku).exists():
        return

    title = line_item.get("title", code)
    price = Decimal(str(line_item.get("price", "0")))

    # Resolve default accounts (Sales, Inventory, COGS, Purchase) so the
    # merchant gets a usable Item out of the box and so fulfillment can
    # generate COGS journal entries later. Driven by ModuleAccountMapping
    # which is seeded by _setup_shopify_accounts during onboarding.
    defaults = _resolve_default_item_accounts(store.company)

    # Fetch cost from Shopify's product data (cost_per_item on variant)
    # Convert to company's functional currency if different from store currency
    functional_currency = getattr(store.company, "functional_currency", "") or store.company.default_currency
    cost = _fetch_variant_cost(store, variant_id, convert_to_currency=functional_currency)

    # Convert selling price to functional currency if store currency differs
    store_currency = _get_shopify_store_currency(store)
    if store_currency and store_currency != functional_currency and price > 0:
        from datetime import date as date_type

        from accounting.models import ExchangeRate

        rate = ExchangeRate.get_rate(
            store.company,
            store_currency,
            functional_currency,
            date_type.today(),
        )
        if rate and rate > 0:
            price = (price * rate).quantize(Decimal("0.01"))

    try:
        with transaction.atomic():
            with command_writes_allowed():
                item = Item.objects.create(
                    company=store.company,
                    code=code,
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
                code,
                title,
                cost,
                defaults.get("inventory"),
                defaults.get("cogs"),
            )
    except Exception as exc:
        logger.warning("Failed to auto-create Item %s: %s", code, exc)


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

    client = _admin_client(store)
    if not client:
        return CommandResult.fail("Token expired or revoked — please reconnect the store.")

    # Ensure Shopify warehouse exists
    _ensure_shopify_warehouse(store)

    company = store.company

    # Resolve default accounts from module mappings first, then fallback to params
    default_accounts = _resolve_default_item_accounts(company)
    inv_account = _resolve_account(company, inventory_account_id) or default_accounts.get("inventory")
    cogs_account = _resolve_account(company, cogs_account_id) or default_accounts.get("cogs")
    sales_account = default_accounts.get("sales")
    purchase_account = default_accounts.get("purchase")

    created = 0
    linked = 0
    updated = 0
    skipped = 0
    errors = []

    pages = client.iter_product_pages()

    while True:
        try:
            page = next(pages)
        except StopIteration:
            break
        except requests.RequestException as e:
            # A denial here means the access token wasn't granted
            # `read_products` at install time, or the store hides products
            # for some other policy reason. Either way, the merchant needs
            # to reinstall / reauthorize — not retry — so surface a friendly
            # message instead of a red "Failed to sync" toast. App Store
            # reviewers hit this on fresh dev stores.
            denial = _shopify_access_denied(e)
            reason = _shopify_denial_reason(e)
            if denial:
                if reason == "non_expiring_token":
                    # B15: Shopify rejected our legacy non-expiring access
                    # token. New token-exchanges (B15) return expiring
                    # tokens, but pre-B15 connected stores still have the
                    # old format and need a re-OAuth to upgrade.
                    msg = (
                        "Your Shopify connection uses a deprecated token "
                        "format that Shopify no longer accepts. Disconnect "
                        "and reconnect this store to upgrade to the current "
                        "token format, then try again."
                    )
                    log_hint = "non-expiring token deprecated by Shopify"
                else:
                    # Default: scope-missing message (covers fresh App Store
                    # installs that genuinely don't have read_products).
                    msg = (
                        "Shopify didn't grant read access to products on "
                        "this store. Disconnect and reconnect to re-grant "
                        "the read_products scope, then try again."
                    )
                    log_hint = "likely read_products scope not granted on this install"
                logger.info(
                    "Skipping product sync for %s: %s (%s)",
                    store.shop_domain,
                    denial,
                    log_hint,
                )
                return CommandResult.ok(
                    data={
                        "created": 0,
                        "linked": 0,
                        "updated": 0,
                        "skipped": 0,
                        "errors": [],
                        "status": "unavailable",
                        "reason": reason or "access_denied",
                        "message": msg,
                    }
                )
            logger.error("Shopify products API error: %s", e)
            return CommandResult.fail(f"Shopify API error: {e}")

        products, raw_cost_map = page

        # unitCost arrives in the same GraphQL page — no separate
        # inventory-items call anymore. Shopify denominates it in the STORE
        # currency; convert to the company's functional currency like the
        # per-variant path (_fetch_variant_cost) does, otherwise default_cost
        # — and the COGS bookings derived from it — are misstated whenever
        # the store currency differs from the books.
        cost_map = {iid: Decimal(cost) for iid, cost in raw_cost_map.items()}
        cost_map = _convert_costs_to_functional(store, client, cost_map)

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

                    # Backfill missing GL accounts / cost on the linked Item —
                    # only fills blanks, never overwrites a merchant's edits.
                    # Heals items created by older webhook code that didn't
                    # set account defaults.
                    if mapping.item:
                        _update_item_defaults(
                            mapping.item,
                            cost,
                            inv_account,
                            cogs_account,
                            sales_account,
                            purchase_account,
                            price,
                            image_url,
                        )

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
                    # Update existing item with cost + accounts + price + image if missing
                    _update_item_defaults(
                        item, cost, inv_account, cogs_account, sales_account, purchase_account, price, image_url
                    )
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

    # Sync-UX (2026-06-04): refresh last_sync_at so the settings page
    # "Last Sync" widget tracks the actual successful pull. Without this,
    # clicking Sync Products forever leaves "Last Sync: Never" — the
    # exact reviewer-broken signal we surfaced post-FX-fix on Shopify_R.
    from django.utils import timezone as tz

    with command_writes_allowed():
        store.last_sync_at = tz.now()
        store.save(update_fields=["last_sync_at"])

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
            # Snapshot the price we last synced from Shopify BEFORE overwriting
            # it, so we can tell whether the merchant has since taken ownership
            # of the Item's price (A127 principle: never clobber a manual price).
            previous_shopify_price = mapping.shopify_price

            # Update snapshot
            mapping.title = product_title
            mapping.variant_title = variant_title
            mapping.sku = sku
            mapping.shopify_price = price
            mapping.shopify_inventory_item_id = variant.get("inventory_item_id")
            mapping.raw_data = variant
            mapping.save()

            # Update auto-created Items. `auto_created` only means WE created the
            # Item — not that its price is still ours. A merchant can edit an
            # auto-created item's price in Nxentra, and pushing Shopify's price
            # here unconditionally silently reverted that on every products/update.
            # Only sync the price while the Item still matches the last price we
            # synced (i.e. the merchant hasn't overridden it).
            if mapping.auto_created and mapping.item:
                with command_writes_allowed():
                    item = mapping.item
                    item.name = f"{product_title} - {variant_title}" if variant_title else product_title
                    update_fields = ["name", "updated_at"]
                    merchant_owns_price = (
                        previous_shopify_price is not None and item.default_unit_price != previous_shopify_price
                    )
                    if not merchant_owns_price:
                        item.default_unit_price = price
                        update_fields.append("default_unit_price")
                    item.save(update_fields=update_fields)

            updated += 1
        elif store.product_sync_enabled:
            # Auto-create mapping for new variants. Resolve account defaults
            # the same way sync_products does: per-store overrides first, then
            # the company's shopify_connector module account mapping. The
            # previous version passed 7 positional args to the 10-arg
            # _create_item_from_variant — a guaranteed TypeError whenever a
            # products/create webhook raced ahead of the first product sync.
            defaults = _resolve_default_item_accounts(company)
            inv_account = _resolve_account(company, store.default_inventory_account_id) or defaults.get("inventory")
            cogs_account = _resolve_account(company, store.default_cogs_account_id) or defaults.get("cogs")
            sales_account = defaults.get("sales")
            purchase_account = defaults.get("purchase")

            item = Item.objects.filter(company=company, code=sku).first()
            auto_created = False

            if not item:
                item = _create_item_from_variant(
                    company,
                    sku,
                    product_title,
                    variant_title,
                    price,
                    Decimal("0"),
                    inv_account,
                    cogs_account,
                    sales_account,
                    purchase_account,
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


def _update_item_defaults(
    item, cost, inv_account, cogs_account, sales_account, purchase_account, price=Decimal("0"), image_url=""
):
    """Update an existing Item with missing defaults from Shopify.

    Every assignment is gated on the target field being blank, so this only
    ever HEALS items that were created before a value was captured — it never
    overwrites something the merchant set by hand.
    """
    updates = []
    if cost > 0 and not item.default_cost:
        item.default_cost = cost
        updates.append("default_cost")
    # Backfill the selling price only when it's still blank (e.g. an item that
    # was order-line auto-created with a 0 price, then later seen by a product
    # sync that carries the variant price). Never clobber a manual price.
    if price > 0 and not item.default_unit_price:
        item.default_unit_price = price
        updates.append("default_unit_price")
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

    # Backfill the product image too — a separate save because image.save()
    # writes the file, not a plain field. Only when the item has no image yet,
    # so we never re-download or clobber a photo the merchant uploaded. Heals
    # items first created via a webhook or an order line — paths that don't pull
    # the image (only the full product sync does).
    if image_url and not item.image:
        _download_item_image(item, image_url)


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


def _resolve_default_item_accounts(company):
    """Resolve default GL accounts for newly auto-created Items.

    All four accounts come from the company's shopify_connector
    ModuleAccountMapping, which is seeded by accounts.commands._setup_shopify_accounts
    during onboarding. The merchant can override any of them per-item later
    by editing the Item — these are just sensible defaults so the books
    don't end up incomplete the moment a Shopify webhook arrives.

    purchase_account defaults to the inventory account: for stocked
    inventory items, a purchase debits inventory (asset), so the same
    account is the conventional default. The user can repoint it to a
    dedicated purchase clearing account later if they want.
    """
    from accounting.mappings import ModuleAccountMapping

    result = {"sales": None, "purchase": None, "inventory": None, "cogs": None}

    mapping = ModuleAccountMapping.get_mapping(company, "shopify_connector")
    if not mapping:
        return result

    result["sales"] = mapping.get("SALES_REVENUE")
    result["inventory"] = mapping.get("INVENTORY")
    result["cogs"] = mapping.get("COGS")
    # Default purchase account to inventory for stocked items.
    result["purchase"] = result["inventory"]

    return result
