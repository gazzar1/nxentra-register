# shopify_connector/views.py
"""
Shopify connector API views.

OAuth endpoints, webhook receiver, and store management.
"""

import json
import logging

from django.http import HttpResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authz import resolve_actor, require

from . import commands
from .models import ShopifyStore, ShopifyOrder, ShopifyRefund
from .serializers import ShopifyStoreSerializer, ShopifyOrderSerializer


logger = logging.getLogger(__name__)


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

        shop_domain = request.data.get("shop_domain", "").strip()
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

        result = commands.get_install_url(actor.company, shop_domain)
        return Response(result, status=status.HTTP_200_OK)


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
        state = request.query_params.get("state", "")

        if not code or not shop or not state:
            return Response(
                {"error": "Missing required OAuth parameters"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Find the store by shop domain and nonce
        try:
            store = ShopifyStore.objects.get(
                shop_domain=shop,
                oauth_nonce=state,
                status=ShopifyStore.Status.PENDING,
            )
        except ShopifyStore.DoesNotExist:
            return Response(
                {"error": "Invalid OAuth state or store not found"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = commands.complete_oauth(store.company, shop, code, state)

        if not result.success:
            # Redirect to frontend with error
            return HttpResponseRedirect(
                f"/shopify/settings?error={result.error}"
            )

        # Redirect to frontend settings page on success
        return HttpResponseRedirect("/shopify/settings?connected=true")


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

        # Find the store
        try:
            store = ShopifyStore.objects.get(
                shop_domain=shop_domain,
                status=ShopifyStore.Status.ACTIVE,
            )
        except ShopifyStore.DoesNotExist:
            # For app/uninstalled, try disconnected stores too
            if topic == "app/uninstalled":
                try:
                    store = ShopifyStore.objects.get(shop_domain=shop_domain)
                except ShopifyStore.DoesNotExist:
                    logger.warning("Unknown shop domain: %s", shop_domain)
                    return HttpResponse(status=200)  # Acknowledge anyway
            else:
                logger.warning("No active store for %s", shop_domain)
                return HttpResponse(status=200)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            logger.error("Invalid JSON in Shopify webhook body")
            return HttpResponse(status=400)

        # Route by topic
        handler = {
            "orders/paid": commands.process_order_paid,
            "refunds/create": commands.process_refund,
            "app/uninstalled": commands.process_app_uninstalled,
        }.get(topic)

        if handler:
            try:
                result = handler(store, payload)
                if not result.success:
                    logger.error(
                        "Shopify webhook %s failed for %s: %s",
                        topic, shop_domain, result.error,
                    )
            except Exception:
                logger.exception(
                    "Error processing Shopify webhook %s for %s",
                    topic, shop_domain,
                )
        else:
            logger.info("Unhandled Shopify webhook topic: %s", topic)

        # Always return 200 to acknowledge receipt
        return HttpResponse(status=200)


# =============================================================================
# Store Management API
# =============================================================================

class ShopifyStoreView(APIView):
    """
    GET /api/shopify/store/
    Returns the connected store details for the current company.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.view")

        try:
            store = ShopifyStore.objects.get(company=actor.company)
            return Response(ShopifyStoreSerializer(store).data)
        except ShopifyStore.DoesNotExist:
            return Response({"connected": False}, status=status.HTTP_200_OK)


class ShopifyRegisterWebhooksView(APIView):
    """
    POST /api/shopify/register-webhooks/
    Registers webhooks with Shopify for the connected store.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)

        try:
            store = ShopifyStore.objects.get(company=actor.company)
        except ShopifyStore.DoesNotExist:
            return Response(
                {"error": "No connected store"},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = commands.register_webhooks(actor, store.id)
        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "registered": result.data.get("registered", []),
            "errors": result.data.get("errors", []),
            "webhooks_registered": store.webhooks_registered,
        })


class ShopifyDisconnectView(APIView):
    """
    POST /api/shopify/disconnect/
    Disconnects the Shopify store.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        result = commands.disconnect_store(actor)
        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"status": "disconnected"})


class ShopifyOrdersView(APIView):
    """
    GET /api/shopify/orders/
    List Shopify orders for the current company.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.view")

        orders = ShopifyOrder.objects.filter(
            company=actor.company,
        ).order_by("-shopify_created_at")[:100]

        return Response(ShopifyOrderSerializer(orders, many=True).data)
