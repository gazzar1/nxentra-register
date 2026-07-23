# shopify_connector/views.py
"""
Shopify connector API views.

OAuth endpoints, webhook receiver, and store management.
"""

import hashlib
import json
import logging

from django.http import HttpResponse, HttpResponseRedirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounting.mappings import ModuleAccountMapping
from accounting.models import Account
from accounts.authz import require, resolve_actor
from projections.write_barrier import command_writes_allowed

from . import commands
from .models import ShopifyOrder, ShopifyPayout, ShopifyStore
from .projections import MODULE_NAME
from .serializers import ShopifyOrderSerializer, ShopifyStoreSerializer

logger = logging.getLogger(__name__)


def _shopify_admin_app_url(shop_domain: str) -> str:
    """
    B17 (2026-06-07): post-OAuth landing URL inside Shopify admin.

    For an embedded app, the canonical "open the app" location is the
    Shopify admin's apps panel for the merchant's store, not standalone
    app.nxentra.com. Redirecting there after OAuth puts the merchant back
    inside the iframe (or opens it for the first time), where our
    embedded launch flow takes over: session-login mints a fresh Nxentra
    JWT and renders /shopify/settings — no manual Nxentra login needed.

    Without this redirect, the merchant who initiated OAuth from inside
    the iframe (Disconnect → Connect) was dropped at standalone
    app.nxentra.com/login, lost their iframe context, and had to log in
    again and manually navigate back to Shopify admin. Discovered in the
    B13 live test on 2026-06-07.

    URL format follows Shopify's documented admin app deep link:
        https://admin.shopify.com/store/<subdomain>/apps/<client_id>
    """
    from django.conf import settings as django_settings

    subdomain = shop_domain.replace(".myshopify.com", "").strip()
    api_key = getattr(django_settings, "SHOPIFY_API_KEY", "") or SHOPIFY_API_KEY_PLACEHOLDER
    return f"https://admin.shopify.com/store/{subdomain}/apps/{api_key}"


# B17: fallback constant only used if Django settings somehow lack
# SHOPIFY_API_KEY (which would already break OAuth — defensive only).
SHOPIFY_API_KEY_PLACEHOLDER = ""


def _get_active_store_for_actor(actor) -> ShopifyStore | None:
    """
    Return the actor's company's currently-connected ShopifyStore, or None.

    A company can legitimately have multiple ACTIVE stores at once (e.g. a
    multi-region merchant with one store per region). The sync endpoints
    used to call `.get(company=..., status=ACTIVE)` which crashed with
    MultipleObjectsReturned in that case (B8.5 live test 2026-06-07).

    For the single-store-button endpoints (Sync Products / Sync Payouts /
    Re-sync Orders / Verify Payout) we resolve ambiguity by picking the
    most recently updated ACTIVE store — that's the one whose token was
    just refreshed via the embedded launch (or via the most recent OAuth
    callback), so it's the merchant's "current" store for this session.

    A future hardening should let the UI pass a `store_public_id` to
    target a specific store, but the freshest-active heuristic is enough
    to unblock the App Store reviewer flow today.
    """
    return (
        ShopifyStore.objects.filter(
            company=actor.company,
            status=ShopifyStore.Status.ACTIVE,
        )
        .order_by("-updated_at")
        .first()
    )


# =============================================================================
# OAuth Flow
# =============================================================================


class ShopifyInstallView(APIView):
    """
    POST /api/shopify/install/
    Body: {"shop_domain": "my-store.myshopify.com"}

    Returns the Shopify OAuth authorization URL for the merchant to visit.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.edit")

        shop_domain = (request.data.get("shop_domain") or "").strip()
        if not shop_domain:
            return Response(
                {"error": "shop_domain is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Normalize domain — strip protocol, trailing slashes
        shop_domain = shop_domain.removeprefix("https://").removeprefix("http://")
        shop_domain = shop_domain.rstrip("/")
        if not shop_domain.endswith(".myshopify.com"):
            shop_domain = f"{shop_domain}.myshopify.com"

        # B17.2 (2026-06-07): the frontend tells us whether the OAuth
        # flow was initiated from inside the Shopify admin iframe so the
        # callback can route the success redirect appropriately.
        # Standalone-started flows stay on app.nxentra.com; embedded-
        # started flows return to admin.shopify.com.
        embedded = bool(request.data.get("embedded", False))

        result = commands.get_install_url(actor.company, shop_domain, embedded=embedded)
        return Response(result, status=status.HTTP_200_OK)


class ShopifyLaunchView(APIView):
    """
    GET /api/shopify/launch/?hmac=...&host=...&shop=...&session=...&timestamp=...

    A122 (2026-06-02): Handles the Shopify "Open app" handshake.

    When a merchant clicks the app icon from their Shopify admin (or Shopify
    auto-launches the app to verify it's reachable), Shopify GETs our
    `application_url` with a signed query-string. This view:

      1. Verifies the HMAC against the rest of the query-string using our
         shared client_secret. Rejects on mismatch.
      2. Decodes the `host` parameter (base64 of `admin.shopify.com/store/<shop>`)
         or falls back to the `shop` parameter to identify the shop.
      3. Looks up an ACTIVE ShopifyStore for that shop_domain. If found,
         redirects to the in-app Shopify settings page. If not found,
         redirects to a "install / connect this store" page that prompts the
         merchant to sign in to (or create) their Nxentra account, after
         which the install flow runs.

    Without this endpoint, Shopify treats our `application_url` as
    "application_cant_be_loaded_misconfigured" because the bare marketing
    page at `/` doesn't know what to do with launch parameters.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        import base64
        import hmac as hmac_lib

        from django.conf import settings as django_settings

        params = request.query_params
        hmac_header = params.get("hmac", "")
        host = params.get("host", "")
        shop = params.get("shop", "")

        if not hmac_header:
            return Response(
                {"error": "Missing hmac parameter"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Build the canonical message: every query-string param sorted by
        # key, except hmac itself, joined as key=value&... per Shopify spec.
        sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "hmac")
        client_secret = getattr(django_settings, "SHOPIFY_API_SECRET", "")
        computed = hmac_lib.new(
            client_secret.encode("utf-8"),
            sorted_params.encode("utf-8"),
            "sha256",
        ).hexdigest()
        if not hmac_lib.compare_digest(computed, hmac_header):
            logger.warning("Shopify launch HMAC verification failed (shop=%s)", shop or host)
            return Response(
                {"error": "Invalid HMAC"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve shop domain — prefer explicit `shop`, fall back to decoding
        # `host` (base64-url of `admin.shopify.com/store/<shop_slug>`).
        shop_domain = shop
        if not shop_domain and host:
            try:
                # Shopify uses base64-URL without padding; pad to 4-char boundary
                padded = host + "=" * (-len(host) % 4)
                decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
                # decoded is e.g. "admin.shopify.com/store/whmwtc-pc"
                if "/store/" in decoded:
                    slug = decoded.rsplit("/store/", 1)[1]
                    shop_domain = f"{slug}.myshopify.com"
            except Exception:
                logger.exception("Failed to decode Shopify host parameter: %s", host)

        if not shop_domain:
            return Response(
                {"error": "Could not determine shop domain from launch params"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not shop_domain.endswith(".myshopify.com"):
            shop_domain = f"{shop_domain}.myshopify.com"

        # Both connected and not-yet-connected cases route to /shopify/settings,
        # which already handles "show connected store" vs "show connect form".
        # The shop hint lets the connect form pre-fill when no store row
        # exists for this domain. The standard frontend auth guard prompts
        # for login if the merchant isn't authenticated in this browser.
        return HttpResponseRedirect(f"/shopify/settings?shop={shop_domain}&launched=true")


class ShopifyCallbackView(APIView):
    """
    GET /api/shopify/callback/?code=...&shop=...&state=...

    Shopify redirects here after the merchant authorizes the app.
    Exchanges the code for an access token, then redirects to the frontend.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        code = request.query_params.get("code", "")
        shop = request.query_params.get("shop", "")
        raw_state = request.query_params.get("state", "")

        if not code or not shop:
            return Response(
                {"error": "Missing required OAuth parameters"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # B17.2 (2026-06-07): if the install was kicked off from inside
        # the Shopify admin iframe, get_install_url appended an
        # ".embedded" suffix to the OAuth state. Strip it before lookup
        # (the stored ShopifyStore.oauth_nonce only holds the bare
        # nonce) and remember the intent for the success redirect.
        return_to_embedded = raw_state.endswith(commands.EMBEDDED_STATE_SUFFIX)
        state = raw_state[: -len(commands.EMBEDDED_STATE_SUFFIX)] if return_to_embedded else raw_state

        # Try to match a PENDING ShopifyStore row created by the
        # Nxentra-initiated install path (merchant pasted shop domain
        # into /shopify/settings → Connect → get_install_url created the
        # row + nonce). When matched, we have a company context and run
        # the original flow.
        store = None
        if state:
            try:
                store = ShopifyStore.objects.get(
                    shop_domain=shop,
                    oauth_nonce=state,
                    status=ShopifyStore.Status.PENDING,
                )
            except ShopifyStore.DoesNotExist:
                pass

        if store is not None:
            result = commands.complete_oauth(store.company, shop, code, state)
            if not result.success:
                # A7: error path also branches on onboarding state so the
                # merchant lands back where they started (wizard or
                # standalone settings). Errors stay on standalone Nxentra
                # so the merchant can see the error message — embedded
                # iframe would just bounce them back here on next launch
                # without context.
                if not store.company.onboarding_completed:
                    return HttpResponseRedirect(f"/onboarding/setup?shopify_error={result.error}")
                return HttpResponseRedirect(f"/shopify/settings?error={result.error}")
            # A7: onboarding-incomplete companies are mid-wizard at the
            # Shopify step — keep them on the wizard, not bouncing into
            # Shopify admin.
            if not store.company.onboarding_completed:
                return HttpResponseRedirect("/onboarding/setup?shopify_connected=true")
            # B17.2 (2026-06-07): post-onboarding success lands the
            # merchant back where they STARTED the OAuth flow:
            #   - Iframe-started (embedded suffix on state) → Shopify
            #     admin embedded app URL. The iframe re-opens our app
            #     at the connected state with embedded.tsx running the
            #     session-login + token-exchange handshake.
            #   - Standalone-started (no suffix) → app.nxentra.com/
            #     shopify/settings. The merchant stays inside Nxentra
            #     without being teleported into Shopify admin.
            if return_to_embedded:
                return HttpResponseRedirect(_shopify_admin_app_url(shop))
            return HttpResponseRedirect("/shopify/settings?connected=true")

        # B6 (2026-06-05): Shopify-initiated install fallback.
        # Reviewer / merchant installed via App Store or Partner Dashboard
        # — no PENDING row exists for us to bind to a company. We exchange
        # the OAuth code immediately (it's single-use + short-lived),
        # stash the tokens, and bounce through login + select-company so
        # the merchant can choose where the store lands.
        if not commands.verify_shopify_oauth_hmac(request.query_params):
            logger.warning(
                "shopify.oauth_callback_hmac_failed shop=%s state=%r",
                shop,
                state[:8] if state else "",
            )
            return Response({"error": "Invalid HMAC"}, status=status.HTTP_400_BAD_REQUEST)

        result = commands.complete_oauth_shopify_initiated(shop, code)
        if not result.success:
            return HttpResponseRedirect(f"/shopify/settings?error={result.error}")

        pending = result.data["pending"]

        finalize_path = f"/shopify/finalize-install?handle={pending.public_id}"
        # /shopify/finalize-install is a Next.js page protected by the
        # frontend auth guard; unauthenticated users get bounced through
        # /login?next=... → /select-company → finalize-install page,
        # which calls the backend finalize endpoint with the JWT.
        return HttpResponseRedirect(finalize_path)


class ShopifyTokenExchangeView(APIView):
    """
    POST /api/shopify/token-exchange/
    Body: {"session_token": "<jwt>", "shop_domain": "<optional hint>"}

    B8 (2026-06-05): Token Exchange — the embedded install path.

    Called by the App Bridge frontend after Shopify silently installs
    the app. The session token (JWT signed by Shopify with our
    client_secret) is exchanged server-side for an offline access
    token, and the resulting tokens are persisted on a ShopifyStore
    row for the authenticated merchant's company.

    Replaces the OAuth code-grant dance for installs that bypass
    /api/shopify/callback/ (Shopify Dev Dashboard "Install app", App
    Store managed-install flow). The Nxentra-initiated Connect form
    still uses OAuth; this is the second path.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.edit")

        session_token = (request.data.get("session_token") or "").strip()
        if not session_token:
            return Response(
                {"error": "session_token is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Optional shop_domain hint — when provided, the command
        # verifies it matches the JWT's claim to catch frontend bugs
        # early. The JWT remains the source of truth either way.
        shop_hint = (request.data.get("shop_domain") or "").strip()

        result = commands.complete_oauth_token_exchange(
            actor.company,
            session_token,
            expected_shop_domain=shop_hint,
        )
        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        store = result.data["store"]

        # A1: bind the Shopify user (session token `sub`) to the acting
        # membership — the ceremony where the backend holds BOTH an
        # authenticated Nxentra membership and a valid session token. Subsequent
        # embedded per-request auth resolves through this binding. Non-fatal to
        # the connection itself: a conflict (a different Shopify user already
        # bound) is surfaced but does not undo the store connection.
        bound = False
        bind_error = None
        shopify_sub = result.data.get("shopify_sub")
        if shopify_sub:
            from .user_binding import BindingError, bind_shopify_user

            try:
                bind_shopify_user(
                    store=store,
                    shopify_sub=shopify_sub,
                    membership=actor.membership,
                    actor_user=actor.user,
                )
                bound = True
            except BindingError as exc:
                bind_error = str(exc)
                logger.warning(
                    "shopify.token_exchange_bind_failed shop=%s error=%s",
                    store.shop_domain,
                    exc,
                )

        return Response(
            {
                "status": "connected",
                "shop_domain": store.shop_domain,
                "store_public_id": str(store.public_id),
                "shopify_user_bound": bound,
                "bind_error": bind_error,
            }
        )


class ShopifyLinkingNonceView(APIView):
    """
    POST /api/shopify/linking-nonce/

    A1: a standalone (cookie-authenticated) OWNER/ADMIN mints a single-use,
    short-lived nonce to link a Shopify user to their membership. The embedded
    app then redeems it (with a session token) at /redeem-linking-nonce/. This
    is the split-context ceremony that establishes the FIRST binding without
    depending on third-party cookies inside the iframe.

    Body (optional): {"store_public_id": "<uuid>"} — defaults to the company's
    single ACTIVE store.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.edit")

        from .user_binding import BindingError, create_linking_nonce

        store_public_id = (request.data.get("store_public_id") or "").strip()
        store_qs = ShopifyStore.objects.filter(company=actor.company, status=ShopifyStore.Status.ACTIVE)
        if store_public_id:
            store_qs = store_qs.filter(public_id=store_public_id)
        store = store_qs.order_by("-updated_at").first()
        if store is None:
            return Response(
                {"error": "No active Shopify store found for this company."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            nonce = create_linking_nonce(store=store, membership=actor.membership, actor_user=actor.user)
        except BindingError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"nonce": nonce, "shop_domain": store.shop_domain, "expires_in_seconds": 600})


class ShopifyRedeemLinkingNonceView(APIView):
    """
    POST /api/shopify/redeem-linking-nonce/

    A1: the embedded app redeems a linking nonce with a valid Shopify session
    token. No prior Nxentra authentication is required (the nonce + the signed
    session token are the credentials). Binds the token's `sub` to the nonce
    creator's membership after verifying the token, shop, nonce validity and
    single-use. Public: authorization is proven by the nonce + session token.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        from .user_binding import BindingError, redeem_linking_nonce

        nonce = (request.data.get("nonce") or "").strip()
        session_token = (request.data.get("session_token") or "").strip()
        if not nonce or not session_token:
            return Response(
                {"error": "nonce and session_token are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            redeem_linking_nonce(nonce_value=nonce, session_token=session_token)
        except BindingError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"status": "linked"})


class ShopifyFinalizeInstallView(APIView):
    """
    POST /api/shopify/finalize-install/<pending_id>/

    B6 Phase 2: associate a PendingShopifyInstall with the
    authenticated merchant's active company. Called by the
    /shopify/finalize-install Next.js page after the merchant has
    logged in and selected a company.

    GET would expose the action to CSRF — keep this POST-only.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pending_id):
        actor = resolve_actor(request)
        require(actor, "settings.edit")

        result = commands.finalize_shopify_install(actor.company, str(pending_id))
        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        store = result.data["store"]
        return Response(
            {
                "status": "connected",
                "shop_domain": store.shop_domain,
                "store_public_id": str(store.public_id),
            }
        )


# =============================================================================
# Webhook Receiver
# =============================================================================


@method_decorator(csrf_exempt, name="dispatch")
class ShopifyWebhookView(APIView):
    """
    POST /api/shopify/webhooks/

    Receives all Shopify webhooks. Verifies HMAC, routes by topic.
    No authentication (Shopify sends these directly).
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        # Verify HMAC signature
        hmac_header = request.META.get("HTTP_X_SHOPIFY_HMAC_SHA256", "")
        if not hmac_header:
            logger.warning("Shopify webhook missing HMAC header")
            return HttpResponse(status=401)

        body = request.body
        if not commands.verify_webhook_hmac(body, hmac_header):
            logger.warning("Shopify webhook HMAC verification failed")
            return HttpResponse(status=401)

        # Parse topic and shop domain
        topic = request.META.get("HTTP_X_SHOPIFY_TOPIC", "")
        shop_domain = request.META.get("HTTP_X_SHOPIFY_SHOP_DOMAIN", "")

        if not topic or not shop_domain:
            logger.warning("Shopify webhook missing topic or shop domain headers")
            return HttpResponse(status=400)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            logger.error("Invalid JSON in Shopify webhook body")
            return HttpResponse(status=400)

        # GDPR mandatory compliance webhooks (A44) — route before the store
        # lookup. shop/redact fires 48h after uninstall and may arrive when
        # no ShopifyStore record exists; the customers/* topics may arrive
        # for shops we never connected to. Audit-log and 200.
        gdpr_handler = {
            "customers/data_request": commands.process_customers_data_request,
            "customers/redact": commands.process_customers_redact,
            "shop/redact": commands.process_shop_redact,
        }.get(topic)
        if gdpr_handler:
            payload_signature = hashlib.sha256(body).hexdigest()
            try:
                result = gdpr_handler(shop_domain, payload, payload_signature)
                # A124: enqueue the export/redaction job right away (fast
                # path); the shopify.process_gdpr_requests beat task is the
                # durable catch-up for dropped enqueues. Enqueue failure
                # must not break the mandated 200 ack.
                request_id = (result.data or {}).get("gdpr_request_id") if result and result.success else None
                if request_id:
                    try:
                        from .tasks import process_gdpr_request_task

                        process_gdpr_request_task.delay(request_id)
                    except Exception:
                        logger.exception("Could not enqueue GDPR job for request %s", request_id)
            except Exception:
                logger.exception(
                    "Error processing Shopify GDPR webhook %s for %s",
                    topic,
                    shop_domain,
                )
                # Still 200 — Shopify only requires the ack; the audit row
                # captures whatever we managed to write.
            return HttpResponse(status=200)

        # Find the store
        try:
            store = ShopifyStore.objects.get(
                shop_domain=shop_domain,
                status=ShopifyStore.Status.ACTIVE,
            )
        except ShopifyStore.DoesNotExist:
            # For app/uninstalled, try disconnected stores too. Multiple
            # companies can share the same shop_domain when the store has
            # been connected and disconnected from different test companies
            # — the unique_active constraint only forbids more than one
            # ACTIVE row. Use .filter().first() so we don't blow up with
            # MultipleObjectsReturned (A120, surfaced via Sentry
            # faa8b00779d04db2a3aed3bbbb366198 from our own diagnostic shell
            # but the same path is reachable from a real app/uninstalled
            # webhook).
            if topic == "app/uninstalled":
                store = ShopifyStore.objects.filter(shop_domain=shop_domain).order_by("-created_at").first()
                if not store:
                    logger.warning("Unknown shop domain: %s", shop_domain)
                    return HttpResponse(status=200)  # Acknowledge anyway
            else:
                logger.warning("No active store for %s", shop_domain)
                return HttpResponse(status=200)

        # Route by topic
        handler = {
            "orders/create": commands.process_order_pending,
            "orders/paid": commands.process_order_paid,
            "orders/cancelled": commands.process_order_cancelled,
            "refunds/create": commands.process_refund,
            "fulfillments/create": commands.process_fulfillment,
            "disputes/create": commands.process_dispute,
            "disputes/update": commands.process_dispute,
            "products/create": commands.process_product_webhook,
            "products/update": commands.process_product_webhook,
            "app/uninstalled": commands.process_app_uninstalled,
        }.get(topic)

        if handler:
            try:
                result = handler(store, payload)
                if not result.success:
                    logger.error(
                        "Shopify webhook %s failed for %s: %s",
                        topic,
                        shop_domain,
                        result.error,
                    )
                    # A159: transient failures (e.g. a refund racing its
                    # order) must NOT be acked — a 2xx makes Shopify never
                    # redeliver and the data is lost until the poller runs.
                    # 503 → Shopify retries with backoff for ~48h. Permanent
                    # validation failures stay 200 so we don't burn the
                    # subscription's consecutive-failure budget.
                    if result.data and result.data.get("retryable"):
                        return HttpResponse(status=503)
            except Exception:
                logger.exception(
                    "Error processing Shopify webhook %s for %s",
                    topic,
                    shop_domain,
                )
                # A159: unexpected exceptions are retryable by definition —
                # let Shopify redeliver instead of silently consuming.
                return HttpResponse(status=500)
        else:
            logger.info("Unhandled Shopify webhook topic: %s", topic)

        # Acknowledge receipt (success, benign skip, or permanent failure).
        return HttpResponse(status=200)


# =============================================================================
# Store Management API
# =============================================================================


class ShopifyStoreView(APIView):
    """
    GET /api/shopify/store/
    Returns connected store(s) for the current company.
    Supports ?store_id=<public_id> for a specific store.

    PATCH /api/shopify/store/
    Updates mutable store config:
    - default_cod_settlement_provider: SettlementProvider id (or null to unset)
      The store's default COD courier (Bosta / DHL / Aramex / Mylerz / ...).
      Drives JE tagging for orders with gateway='cash_on_delivery'.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.view")

        store_id = request.query_params.get("store_id")
        if store_id:
            try:
                store = ShopifyStore.objects.get(
                    company=actor.company,
                    public_id=store_id,
                )
                return Response(ShopifyStoreSerializer(store).data)
            except ShopifyStore.DoesNotExist:
                return Response(
                    {"error": "Store not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

        # B1+B4 (2026-06-04): contract — `connected` is true iff at least one
        # ACTIVE row exists. `stores` holds live rows (ACTIVE/ERROR) the
        # frontend treats as connection candidates; `inactive_stores` holds
        # DISCONNECTED rows kept only so the settings page can render the
        # "previously connected to <shop>" hint. PENDING rows are never
        # exposed.
        #
        # The earlier shape returned `{connected: true, stores: [all-minus-
        # PENDING ranked]}` and let the frontend re-sort. That hid a latent
        # bug: a company with only DISCONNECTED rows reported
        # `connected: true`, lying to any consumer that didn't itself check
        # per-row status. That's the App Store reviewer's failure mode — the
        # API claimed connection, the frontend (correctly) said no.
        live_qs = (
            ShopifyStore.objects.filter(company=actor.company)
            .filter(status__in=[ShopifyStore.Status.ACTIVE, ShopifyStore.Status.ERROR])
            # ACTIVE before ERROR — DESC ordering on the literal string puts
            # 'ACTIVE' (A) before 'ERROR' (E) alphabetically reversed; explicit
            # case keeps it intentional even if the labels ever change.
            .order_by("-updated_at")
        )
        inactive_qs = ShopifyStore.objects.filter(
            company=actor.company,
            status=ShopifyStore.Status.DISCONNECTED,
        ).order_by("-updated_at")

        live = sorted(
            live_qs,
            key=lambda s: (
                0 if s.status == ShopifyStore.Status.ACTIVE else 1,
                -(s.updated_at.timestamp() if s.updated_at else 0),
            ),
        )
        inactive = list(inactive_qs)
        connected = any(s.status == ShopifyStore.Status.ACTIVE for s in live)

        logger.info(
            "shopify.store_api company=%s active=%d error=%d disconnected=%d connected=%s",
            getattr(actor.company, "id", None),
            sum(1 for s in live if s.status == ShopifyStore.Status.ACTIVE),
            sum(1 for s in live if s.status == ShopifyStore.Status.ERROR),
            len(inactive),
            connected,
        )

        return Response(
            {
                "connected": connected,
                "stores": ShopifyStoreSerializer(live, many=True).data,
                "inactive_stores": ShopifyStoreSerializer(inactive, many=True).data,
            }
        )

    def patch(self, request):
        from accounting.settlement_provider import SettlementProvider
        from projections.write_barrier import command_writes_allowed

        actor = resolve_actor(request)
        require(actor, "settings.update")

        store_id = request.data.get("store_id") or request.query_params.get("store_id")
        try:
            if store_id:
                store = ShopifyStore.objects.get(
                    company=actor.company,
                    public_id=store_id,
                )
            else:
                store = (
                    ShopifyStore.objects.filter(company=actor.company)
                    .exclude(status=ShopifyStore.Status.DISCONNECTED)
                    .first()
                )
                if not store:
                    raise ShopifyStore.DoesNotExist
        except ShopifyStore.DoesNotExist:
            return Response({"error": "Store not found"}, status=status.HTTP_404_NOT_FOUND)

        update_fields: list[str] = []

        # Only one mutable field today: default_cod_settlement_provider.
        if "default_cod_settlement_provider" in request.data:
            value = request.data.get("default_cod_settlement_provider")
            if value in (None, ""):
                store.default_cod_settlement_provider = None
            else:
                try:
                    provider = SettlementProvider.objects.get(
                        company=actor.company,
                        pk=int(value),
                        provider_type=SettlementProvider.ProviderType.COURIER,
                        is_active=True,
                    )
                except (ValueError, SettlementProvider.DoesNotExist):
                    return Response(
                        {
                            "error": (
                                "default_cod_settlement_provider must be the id of an "
                                "active SettlementProvider with provider_type='courier'."
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                store.default_cod_settlement_provider = provider
            update_fields.append("default_cod_settlement_provider")

        if not update_fields:
            return Response({"error": "No mutable fields supplied."}, status=status.HTTP_400_BAD_REQUEST)

        with command_writes_allowed():
            store.save(update_fields=[*update_fields, "updated_at"])

        return Response(ShopifyStoreSerializer(store).data, status=status.HTTP_200_OK)


class ShopifyDisconnectView(APIView):
    """
    POST /api/shopify/disconnect/
    Disconnects the Shopify store.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        # A136: pass the merchant-selected store through so a multi-store
        # tenant disconnects the intended store, not an arbitrary one. Absent
        # (single-store UI) the command auto-selects the sole connected store.
        store_public_id = request.data.get("store_public_id") or None
        result = commands.disconnect_store(actor, store_public_id=store_public_id)
        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"status": "disconnected"})


class ShopifySyncPayoutsView(APIView):
    """
    POST /api/shopify/sync-payouts/
    Fetches recent payouts from Shopify Payments and creates settlement events.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.edit")

        store = _get_active_store_for_actor(actor)
        if store is None:
            return Response(
                {"error": "No active Shopify store"},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = commands.sync_payouts(store)
        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "created": result.data.get("created", 0),
                "skipped": result.data.get("skipped", 0),
            }
        )


class ShopifySyncProductsView(APIView):
    """
    POST /api/shopify/sync-products/
    Pull products from Shopify and create/link Nxentra Items.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.edit")

        store = _get_active_store_for_actor(actor)
        if store is None:
            return Response(
                {"error": "No active Shopify store"},
                status=status.HTTP_404_NOT_FOUND,
            )

        inventory_account_id = request.data.get("inventory_account_id")
        cogs_account_id = request.data.get("cogs_account_id")

        result = commands.sync_products(
            store,
            inventory_account_id=inventory_account_id,
            cogs_account_id=cogs_account_id,
        )
        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(result.data)


class ShopifyResyncOrdersView(APIView):
    """
    POST /api/shopify/resync-orders/
    Re-sync missed orders by polling the Shopify Orders API.
    Catches webhooks that were missed due to downtime.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.edit")

        store = _get_active_store_for_actor(actor)
        if store is None:
            return Response(
                {"error": "No active Shopify store"},
                status=status.HTTP_404_NOT_FOUND,
            )

        from datetime import timedelta

        from django.utils import timezone as tz

        from .tasks import _sync_orders

        days = int(request.data.get("days", 7))
        now = tz.now()
        created_at_min = (now - timedelta(days=days)).isoformat()
        created_at_max = now.isoformat()

        result = _sync_orders(store, created_at_min, created_at_max)
        return Response(result)


class ShopifyOrdersView(APIView):
    """
    GET /api/shopify/orders/
    List Shopify orders for the current company.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from decimal import Decimal

        from django.db.models import DecimalField, Sum
        from django.db.models.functions import Coalesce

        actor = resolve_actor(request)
        require(actor, "settings.view")

        # F11: annotate each order's total refunded (Σ of its Shopify refunds)
        # so the dashboard can surface returns/refunds against gross revenue —
        # one aggregate query, no per-row N+1.
        orders = (
            ShopifyOrder.objects.filter(company=actor.company)
            .annotate(
                total_refunded=Coalesce(
                    Sum("refunds__amount"),
                    Decimal("0"),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            )
            .order_by("-shopify_created_at", "-shopify_order_id")[:100]
        )

        return Response(ShopifyOrderSerializer(orders, many=True).data)


# =============================================================================
# Account Mapping
# =============================================================================

ACCOUNT_ROLES = [
    "SALES_REVENUE",
    "SHOPIFY_CLEARING",
    "SALES_TAX_PAYABLE",
    "SHIPPING_REVENUE",
    "SALES_DISCOUNTS",
    "CASH_BANK",
    "PAYMENT_PROCESSING_FEES",
    "CHARGEBACK_EXPENSE",
]


class ShopifyAccountMappingView(APIView):
    """
    GET /api/shopify/account-mapping/
    PUT /api/shopify/account-mapping/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.view")

        mapping = ModuleAccountMapping.get_mapping(actor.company, MODULE_NAME)
        result = []
        for role in ACCOUNT_ROLES:
            account = mapping.get(role)
            result.append(
                {
                    "role": role,
                    "account_id": account.id if account else None,
                    "account_code": account.code if account else "",
                    "account_name": account.name if account else "",
                }
            )
        return Response(result)

    def put(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.edit")

        mappings = request.data
        if not isinstance(mappings, list):
            return Response(
                {"detail": "Expected a list of role mappings."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with command_writes_allowed():
            for item in mappings:
                role = item.get("role")
                account_id = item.get("account_id")

                if role not in ACCOUNT_ROLES:
                    continue

                account = None
                if account_id:
                    try:
                        account = Account.objects.get(
                            company=actor.company,
                            pk=account_id,
                        )
                    except Account.DoesNotExist:
                        return Response(
                            {"detail": f"Account {account_id} not found."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                ModuleAccountMapping.objects.update_or_create(
                    company=actor.company,
                    module=MODULE_NAME,
                    role=role,
                    defaults={"account": account},
                )

        return Response({"detail": "Account mappings updated."})


class ShopifyPayoutVerifyView(APIView):
    """POST: Fetch and verify transactions for a payout."""

    permission_classes = [IsAuthenticated]

    def post(self, request, payout_id):
        actor = resolve_actor(request)
        require(actor, "reports.view")

        store = _get_active_store_for_actor(actor)
        if store is None:
            return Response(
                {"detail": "No active Shopify store."},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = commands.verify_payout(store, payout_id)
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(result.data)


class ShopifyPayoutTransactionsView(APIView):
    """GET: List transactions for a payout."""

    permission_classes = [IsAuthenticated]

    def get(self, request, payout_id):
        actor = resolve_actor(request)
        require(actor, "reports.view")

        try:
            payout = ShopifyPayout.objects.get(
                company=actor.company,
                shopify_payout_id=payout_id,
            )
        except ShopifyPayout.DoesNotExist:
            return Response(
                {"detail": "Payout not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        transactions = payout.transactions.all().order_by("-processed_at")
        # Compute variance for each transaction
        from .reconciliation import _match_transaction

        data = []
        for t in transactions:
            match = _match_transaction(actor.company, t)
            data.append(
                {
                    "id": t.id,
                    "shopify_transaction_id": t.shopify_transaction_id,
                    "transaction_type": t.transaction_type,
                    "amount": str(t.amount),
                    "fee": str(t.fee),
                    "net": str(t.net),
                    "currency": t.currency,
                    "source_order_id": t.source_order_id,
                    "source_type": t.source_type,
                    "verified": t.verified,
                    "local_order_name": (t.local_order.shopify_order_name if t.local_order else None),
                    "matched": match.matched,
                    "matched_to": match.matched_to,
                    "variance": str(match.variance),
                    "processed_at": t.processed_at.isoformat() if t.processed_at else None,
                }
            )
        return Response(
            {
                "payout_id": payout.shopify_payout_id,
                "payout_net": str(payout.net_amount),
                "payout_fees": str(payout.fees),
                "payout_gross": str(payout.gross_amount),
                "transactions": data,
                "count": len(data),
            }
        )


class ShopifyClearingBalanceView(APIView):
    """GET: Return current Shopify Clearing account balance for the company."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "reports.view")

        from .management.commands.check_clearing_balance import compute_clearing_balance

        data = compute_clearing_balance(actor.company)

        if data is None:
            return Response(
                {"detail": "No SHOPIFY_CLEARING account mapped."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(data)


# =============================================================================
# Payout Reconciliation
# =============================================================================


class ShopifyReconciliationSummaryView(APIView):
    """
    GET /api/shopify/reconciliation/
    Returns payout reconciliation summary for a date range.

    Query params:
        date_from (required): YYYY-MM-DD
        date_to (required): YYYY-MM-DD
        store_id: optional store public_id to filter
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from datetime import date as date_type

        from .reconciliation import reconciliation_summary, summary_to_dict

        actor = resolve_actor(request)
        require(actor, "reports.view")

        date_from_str = request.query_params.get("date_from")
        date_to_str = request.query_params.get("date_to")

        if not date_from_str or not date_to_str:
            return Response(
                {"detail": "date_from and date_to query params required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            date_from = date_type.fromisoformat(date_from_str)
            date_to = date_type.fromisoformat(date_to_str)
        except ValueError:
            return Response(
                {"detail": "Invalid date format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        store = None
        store_id = request.query_params.get("store_id")
        if store_id:
            store = ShopifyStore.objects.filter(
                company=actor.company,
                public_id=store_id,
            ).first()

        summary = reconciliation_summary(actor.company, date_from, date_to, store)
        return Response(summary_to_dict(summary))


class ShopifyPayoutReconciliationView(APIView):
    """
    GET /api/shopify/reconciliation/<payout_id>/
    Returns detailed reconciliation for a single payout.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, payout_id):
        from .models import ShopifyPayout
        from .reconciliation import payout_recon_to_dict, reconcile_payout

        actor = resolve_actor(request)
        require(actor, "reports.view")

        try:
            payout = ShopifyPayout.objects.get(
                company=actor.company,
                shopify_payout_id=payout_id,
            )
        except ShopifyPayout.DoesNotExist:
            return Response(
                {"detail": "Payout not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        recon = reconcile_payout(actor.company, payout)
        return Response(payout_recon_to_dict(recon))


class ShopifyPayoutsListView(APIView):
    """
    GET /api/shopify/payouts/
    List payouts with reconciliation status.

    Query params:
        page: page number (default 1)
        status: filter by recon status (verified|discrepancy|unverified|no_transactions)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Count, Q

        from .reconciliation import reconcile_payout

        actor = resolve_actor(request)
        require(actor, "reports.view")

        payouts = (
            ShopifyPayout.objects.filter(
                company=actor.company,
            )
            .select_related("store")
            .order_by("-payout_date")
        )

        page = int(request.query_params.get("page", 1))
        page_size = 25
        offset = (page - 1) * page_size
        total = payouts.count()
        payouts_page = payouts[offset : offset + page_size]

        results = []
        for payout in payouts_page:
            has_transactions = payout.transactions.exists()
            txn_stats = payout.transactions.aggregate(
                total=Count("id"),
                verified=Count("id", filter=Q(verified=True)),
            )

            if not has_transactions:
                recon_status = "no_transactions"
            else:
                recon = reconcile_payout(actor.company, payout)
                recon_status = recon.status

            results.append(
                {
                    "shopify_payout_id": payout.shopify_payout_id,
                    "payout_date": str(payout.payout_date),
                    "gross_amount": str(payout.gross_amount),
                    "fees": str(payout.fees),
                    "net_amount": str(payout.net_amount),
                    "currency": payout.currency,
                    "shopify_status": payout.shopify_status,
                    "store_domain": payout.store.shop_domain,
                    "reconciliation_status": recon_status,
                    "transactions_total": txn_stats["total"],
                    "transactions_verified": txn_stats["verified"],
                    "journal_entry_id": str(payout.journal_entry_id) if payout.journal_entry_id else None,
                }
            )

        # Filter by recon status if requested
        status_filter = request.query_params.get("status")
        if status_filter:
            results = [r for r in results if r["reconciliation_status"] == status_filter]

        return Response(
            {
                "results": results,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )
