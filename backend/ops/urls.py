"""
Operations endpoints.

These endpoints are for infrastructure monitoring and should be:
- Excluded from authentication middleware
- Protected at network level (internal only) in production
"""
from django.urls import path

from ops.health import LivenessView, ReadinessView, FullHealthView
from ops.metrics import MetricsView

urlpatterns = [
    # Kubernetes probes
    path("live", LivenessView.as_view(), name="health-live"),
    path("ready", ReadinessView.as_view(), name="health-ready"),
    path("full", FullHealthView.as_view(), name="health-full"),
]

# Metrics endpoint (separate path prefix in main urls.py)
metrics_patterns = [
    path("", MetricsView.as_view(), name="metrics"),
]
