# shopify_connector/views.py
"""
Shopify connector API views.

OAuth endpoints, webhook receiver, and store management.
"""

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
            return HttpResponseRedirect(f"/shopify/settings?error={result.error}")

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
            except Exception:
                logger.exception(
                    "Error processing Shopify webhook %s for %s",
                    topic,
                    shop_domain,
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

        stores = ShopifyStore.objects.filter(company=actor.company)
        if not stores.exists():
            return Response({"connected": False, "stores": []}, status=status.HTTP_200_OK)
        return Response(
            {
                "connected": True,
                "stores": ShopifyStoreSerializer(stores, many=True).data,
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


class ShopifyRegisterWebhooksView(APIView):
    """
    POST /api/shopify/register-webhooks/
    Registers webhooks with Shopify for the connected store.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)

        store_id = request.data.get("store_id") or request.query_params.get("store_id")
        try:
            if store_id:
                store = ShopifyStore.objects.get(
                    company=actor.company,
                    public_id=store_id,
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

        return Response(
            {
                "registered": result.data.get("registered", []),
                "errors": result.data.get("errors", []),
                "webhooks_registered": store.webhooks_registered,
            }
        )


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


class ShopifySyncPayoutsView(APIView):
    """
    POST /api/shopify/sync-payouts/
    Fetches recent payouts from Shopify Payments and creates settlement events.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.edit")

        try:
            store = ShopifyStore.objects.get(
                company=actor.company,
                status=ShopifyStore.Status.ACTIVE,
            )
        except ShopifyStore.DoesNotExist:
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

        try:
            store = ShopifyStore.objects.get(
                company=actor.company,
                status=ShopifyStore.Status.ACTIVE,
            )
        except ShopifyStore.DoesNotExist:
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

        try:
            store = ShopifyStore.objects.get(
                company=actor.company,
                status=ShopifyStore.Status.ACTIVE,
            )
        except ShopifyStore.DoesNotExist:
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
        actor = resolve_actor(request)
        require(actor, "settings.view")

        orders = ShopifyOrder.objects.filter(
            company=actor.company,
        ).order_by("-shopify_created_at")[:100]

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

        try:
            store = ShopifyStore.objects.get(company=actor.company, status="ACTIVE")
        except ShopifyStore.DoesNotExist:
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
