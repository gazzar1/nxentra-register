# shopify_connector/urls.py
from django.urls import path

from . import views

urlpatterns = [
    # OAuth flow
    path("install/", views.ShopifyInstallView.as_view(), name="shopify-install"),
    path("callback/", views.ShopifyCallbackView.as_view(), name="shopify-callback"),
    # B6 (2026-06-05): finalize a Shopify-initiated install after the
    # merchant has logged into Nxentra and selected a company. Called by
    # the /shopify/finalize-install Next.js page with the JWT in headers.
    path(
        "finalize-install/<uuid:pending_id>/",
        views.ShopifyFinalizeInstallView.as_view(),
        name="shopify-finalize-install",
    ),
    # A122 (2026-06-02): Shopify app launch handshake. Shopify sends merchants
    # to https://app.nxentra.com/?hmac=...&host=...&shop=...&session=... when
    # they click "Open app" from their Shopify admin. The frontend root page
    # 307-redirects launch traffic here; we verify HMAC and route to either
    # OAuth install (no store record) or the merchant's settings page
    # (already connected).
    path("launch/", views.ShopifyLaunchView.as_view(), name="shopify-launch"),
    # Webhook receiver (no auth — Shopify sends directly).
    # Both with and without trailing slash: Shopify's Partners Dashboard strips
    # the trailing slash off declarative subscription URIs during registration,
    # so deliveries hit /webhooks (no slash) and Django's APPEND_SLASH would
    # 301-redirect — Shopify rejects POST redirects as "Invalid webhook URL".
    path("webhooks", views.ShopifyWebhookView.as_view()),
    path("webhooks/", views.ShopifyWebhookView.as_view(), name="shopify-webhooks"),
    # Store management
    path("store/", views.ShopifyStoreView.as_view(), name="shopify-store"),
    path("disconnect/", views.ShopifyDisconnectView.as_view(), name="shopify-disconnect"),
    # Data views
    path("orders/", views.ShopifyOrdersView.as_view(), name="shopify-orders"),
    path("sync-payouts/", views.ShopifySyncPayoutsView.as_view(), name="shopify-sync-payouts"),
    path("sync-products/", views.ShopifySyncProductsView.as_view(), name="shopify-sync-products"),
    path("resync-orders/", views.ShopifyResyncOrdersView.as_view(), name="shopify-resync-orders"),
    # Account mapping
    path("account-mapping/", views.ShopifyAccountMappingView.as_view(), name="shopify-account-mapping"),
    # Payout verification (Layer 2 reconciliation)
    path("payouts/<int:payout_id>/verify/", views.ShopifyPayoutVerifyView.as_view(), name="shopify-payout-verify"),
    path(
        "payouts/<int:payout_id>/transactions/",
        views.ShopifyPayoutTransactionsView.as_view(),
        name="shopify-payout-transactions",
    ),
    # Monitoring
    path("clearing-balance/", views.ShopifyClearingBalanceView.as_view(), name="shopify-clearing-balance"),
    # Payout reconciliation
    path("payouts/", views.ShopifyPayoutsListView.as_view(), name="shopify-payouts-list"),
    path("reconciliation/", views.ShopifyReconciliationSummaryView.as_view(), name="shopify-reconciliation-summary"),
    path(
        "reconciliation/<int:payout_id>/",
        views.ShopifyPayoutReconciliationView.as_view(),
        name="shopify-payout-reconciliation",
    ),
]
