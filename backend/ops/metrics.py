"""
Prometheus metrics endpoint.

Exposes application metrics in Prometheus format for scraping.

Metrics exposed:
- nxentra_events_total: Total events by type and company
- nxentra_projection_lag: Projection consumer lag
- nxentra_database_connections: Database connection pool stats
- nxentra_request_duration_seconds: HTTP request duration histogram
- nxentra_tenant_mode_info: Tenant isolation mode (shared/dedicated)
"""
import logging
import time
from functools import wraps

from django.conf import settings
from django.http import HttpResponse
from django.views import View
from django.db import connections

logger = logging.getLogger(__name__)

# Lazy import prometheus_client to avoid errors if not installed
_prometheus_available = None
_metrics_initialized = False

# Metric references (initialized lazily)
_events_total = None
_projection_lag = None
_tenant_mode = None
_request_duration = None
_active_requests = None
_database_pool = None


def _init_prometheus():
    """Initialize Prometheus metrics (lazy)."""
    global _prometheus_available, _metrics_initialized
    global _events_total, _projection_lag, _tenant_mode
    global _request_duration, _active_requests, _database_pool

    if _metrics_initialized:
        return _prometheus_available

    try:
        from prometheus_client import Counter, Gauge, Histogram, Info, REGISTRY

        # Event metrics
        _events_total = Gauge(
            "nxentra_events_total",
            "Total number of events",
            ["event_type", "company_slug"],
        )

        # Projection lag
        _projection_lag = Gauge(
            "nxentra_projection_lag",
            "Number of events pending processing",
            ["consumer", "company_slug"],
        )

        # Tenant mode
        _tenant_mode = Gauge(
            "nxentra_tenant_mode",
            "Tenant isolation mode (0=shared, 1=dedicated)",
            ["company_slug", "db_alias"],
        )

        # Request metrics
        _request_duration = Histogram(
            "nxentra_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "endpoint", "status"],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        )

        _active_requests = Gauge(
            "nxentra_active_requests",
            "Number of requests currently being processed",
        )

        # Database pool metrics
        _database_pool = Gauge(
            "nxentra_database_pool_size",
            "Database connection pool statistics",
            ["database", "stat"],
        )

        _prometheus_available = True
        _metrics_initialized = True
        logger.info("Prometheus metrics initialized")

    except ImportError:
        _prometheus_available = False
        _metrics_initialized = True
        logger.warning("prometheus_client not installed, metrics disabled")

    return _prometheus_available


def collect_metrics():
    """Collect current metrics values."""
    if not _init_prometheus():
        return

    try:
        from accounts.models import Company
        from accounts.rls import rls_bypass
        from events.models import BusinessEvent, EventBookmark
        from tenant.models import TenantDirectory

        with rls_bypass():
            # Event counts by type
            event_counts = (
                BusinessEvent.objects
                .values("event_type", "company__slug")
                .annotate(count=models.Count("id"))
            )
            for row in event_counts:
                _events_total.labels(
                    event_type=row["event_type"],
                    company_slug=row["company__slug"] or "unknown",
                ).set(row["count"])

            # Projection lag
            for bookmark in EventBookmark.objects.select_related("company", "last_event"):
                total = BusinessEvent.objects.filter(company=bookmark.company).count()
                if bookmark.last_event:
                    processed = BusinessEvent.objects.filter(
                        company=bookmark.company,
                        company_sequence__lte=bookmark.last_event.company_sequence,
                    ).count()
                else:
                    processed = 0
                lag = total - processed

                _projection_lag.labels(
                    consumer=bookmark.consumer_name,
                    company_slug=bookmark.company.slug,
                ).set(lag)

            # Tenant modes
            for tenant in TenantDirectory.objects.select_related("company"):
                is_dedicated = 1 if tenant.mode == TenantDirectory.IsolationMode.DEDICATED_DB else 0
                _tenant_mode.labels(
                    company_slug=tenant.company.slug,
                    db_alias=tenant.db_alias,
                ).set(is_dedicated)

    except Exception as e:
        logger.error(f"Error collecting metrics: {e}")


def get_prometheus_response():
    """Generate Prometheus metrics response."""
    if not _init_prometheus():
        return HttpResponse(
            "# Prometheus metrics not available (prometheus_client not installed)\n",
            content_type="text/plain",
        )

    try:
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

        # Collect current values
        collect_metrics()

        # Generate response
        output = generate_latest()
        return HttpResponse(output, content_type=CONTENT_TYPE_LATEST)

    except Exception as e:
        logger.error(f"Error generating metrics: {e}")
        return HttpResponse(
            f"# Error generating metrics: {e}\n",
            content_type="text/plain",
            status=500,
        )


class MetricsView(View):
    """
    Prometheus metrics endpoint.

    Exposes metrics in Prometheus format at /_metrics.
    Should be protected in production (internal network only).
    """

    def get(self, request):
        return get_prometheus_response()


def track_request_metrics(get_response):
    """
    Middleware to track request duration metrics.

    Add to MIDDLEWARE after SecurityMiddleware:
        "ops.metrics.track_request_metrics",
    """
    if not _init_prometheus():
        return get_response

    def middleware(request):
        if not _prometheus_available:
            return get_response(request)

        start = time.time()
        _active_requests.inc()

        try:
            response = get_response(request)
            return response
        finally:
            _active_requests.dec()
            duration = time.time() - start

            # Normalize endpoint for cardinality control
            endpoint = request.path
            # Strip IDs from common patterns
            import re
            endpoint = re.sub(r"/\d+/", "/{id}/", endpoint)
            endpoint = re.sub(r"/[0-9a-f-]{36}/", "/{uuid}/", endpoint)

            status = getattr(response, "status_code", 500) if "response" in dir() else 500
            status_class = f"{status // 100}xx"

            _request_duration.labels(
                method=request.method,
                endpoint=endpoint[:50],  # Truncate long paths
                status=status_class,
            ).observe(duration)

    return middleware


# Import models at module level for collect_metrics
from django.db import models
