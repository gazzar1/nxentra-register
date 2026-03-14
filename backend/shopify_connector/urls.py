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

    # Account mapping
    path("account-mapping/", views.ShopifyAccountMappingView.as_view(), name="shopify-account-mapping"),
]
