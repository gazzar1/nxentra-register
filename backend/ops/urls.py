"""
Operations endpoints.

These endpoints are for infrastructure monitoring and should be:
- Excluded from authentication middleware
- Protected at network level (internal only) in production
"""

from django.urls import path

from ops.health import AlertHealthView, FullHealthView, LivenessView, ReadinessView
from ops.metrics import MetricsView

urlpatterns = [
    # Kubernetes probes
    path("live", LivenessView.as_view(), name="health-live"),
    path("ready", ReadinessView.as_view(), name="health-ready"),
    path("full", FullHealthView.as_view(), name="health-full"),
    # A163: the endpoint an external uptime pinger watches — 503 when a
    # projection failure/lag/pause needs a human. Separate from `ready`
    # (which feeds load balancers and must stay DB-only).
    path("alerts", AlertHealthView.as_view(), name="health-alerts"),
]

# Metrics endpoint (separate path prefix in main urls.py)
metrics_patterns = [
    path("", MetricsView.as_view(), name="metrics"),
]
