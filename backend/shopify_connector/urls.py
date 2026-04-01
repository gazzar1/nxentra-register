# shopify_connector/urls.py
from django.urls import path

from . import views

urlpatterns = [
    # OAuth flow
    path("install/", views.ShopifyInstallView.as_view(), name="shopify-install"),
    path("callback/", views.ShopifyCallbackView.as_view(), name="shopify-callback"),

    # Webhook receiver (no auth — Shopify sends directly)
    path("webhooks/", views.ShopifyWebhookView.as_view(), name="shopify-webhooks"),

    # Store management
    path("store/", views.ShopifyStoreView.as_view(), name="shopify-store"),
    path("register-webhooks/", views.ShopifyRegisterWebhooksView.as_view(), name="shopify-register-webhooks"),
    path("disconnect/", views.ShopifyDisconnectView.as_view(), name="shopify-disconnect"),

    # Data views
    path("orders/", views.ShopifyOrdersView.as_view(), name="shopify-orders"),
    path("sync-payouts/", views.ShopifySyncPayoutsView.as_view(), name="shopify-sync-payouts"),
    path("sync-products/", views.ShopifySyncProductsView.as_view(), name="shopify-sync-products"),

    # Account mapping
    path("account-mapping/", views.ShopifyAccountMappingView.as_view(), name="shopify-account-mapping"),

    # Payout verification (Layer 2 reconciliation)
    path("payouts/<int:payout_id>/verify/", views.ShopifyPayoutVerifyView.as_view(), name="shopify-payout-verify"),
    path("payouts/<int:payout_id>/transactions/", views.ShopifyPayoutTransactionsView.as_view(), name="shopify-payout-transactions"),

    # Monitoring
    path("clearing-balance/", views.ShopifyClearingBalanceView.as_view(), name="shopify-clearing-balance"),

    # Payout reconciliation
    path("payouts/", views.ShopifyPayoutsListView.as_view(), name="shopify-payouts-list"),
    path("reconciliation/", views.ShopifyReconciliationSummaryView.as_view(), name="shopify-reconciliation-summary"),
    path("reconciliation/<int:payout_id>/", views.ShopifyPayoutReconciliationView.as_view(), name="shopify-payout-reconciliation"),
]
