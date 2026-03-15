# platform_connectors/urls.py
"""
URL configuration for the generic platform connector webhook endpoint.

All platform webhooks arrive at:
    POST /api/platforms/<slug>/webhooks/

The slug identifies the platform (e.g. "shopify", "stripe").
"""

from django.urls import path

from .views import PlatformWebhookView

app_name = "platform_connectors"

urlpatterns = [
    path(
        "<str:platform_slug>/webhooks/",
        PlatformWebhookView.as_view(),
        name="platform-webhook",
    ),
]
