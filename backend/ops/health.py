"""
Health check endpoints for operations monitoring.

Provides comprehensive health checks for:
- Database connectivity (all configured databases)
- Redis/Celery connectivity
- TenantDirectory consistency
- Projection lag monitoring

Endpoints:
- /_health/live    - Kubernetes liveness probe (is the process running?)
- /_health/ready   - Kubernetes readiness probe (can we serve traffic?)
- /_health/full    - Full health report (for debugging/dashboards)
"""
import logging
import time
from typing import Dict, Any, List

from django.conf import settings
from django.db import connections
from django.http import JsonResponse
from django.views import View

logger = logging.getLogger(__name__)


class HealthCheck:
    """Health check implementation."""

    @staticmethod
    def check_database(alias: str = "default") -> Dict[str, Any]:
        """Check database connectivity."""
        start = time.time()
        try:
            conn = connections[alias]
            conn.ensure_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            duration_ms = (time.time() - start) * 1000
            return {
                "status": "healthy",
                "alias": alias,
                "duration_ms": round(duration_ms, 2),
            }
        except Exception as e:
            duration_ms = (time.time() - start) * 1000
            return {
                "status": "unhealthy",
                "alias": alias,
                "error": str(e),
                "duration_ms": round(duration_ms, 2),
            }

    @staticmethod
    def check_all_databases() -> Dict[str, Any]:
        """Check all configured databases."""
        results = {}
        all_healthy = True

        for alias in settings.DATABASES.keys():
            result = HealthCheck.check_database(alias)
            results[alias] = result
            if result["status"] != "healthy":
                all_healthy = False

        return {
            "status": "healthy" if all_healthy else "degraded",
            "databases": results,
        }

    @staticmethod
    def check_redis() -> Dict[str, Any]:
        """Check Redis connectivity (if configured)."""
        redis_url = getattr(settings, "CELERY_BROKER_URL", None)
        if not redis_url:
            return {"status": "skipped", "reason": "Redis not configured"}

        start = time.time()
        try:
            import redis
            client = redis.from_url(redis_url)
            client.ping()
            duration_ms = (time.time() - start) * 1000
            return {
                "status": "healthy",
                "duration_ms": round(duration_ms, 2),
            }
        except ImportError:
            return {"status": "skipped", "reason": "redis package not installed"}
        except Exception as e:
            duration_ms = (time.time() - start) * 1000
            return {
                "status": "unhealthy",
                "error": str(e),
                "duration_ms": round(duration_ms, 2),
            }

    @staticmethod
    def check_tenant_directory() -> Dict[str, Any]:
        """Check TenantDirectory consistency."""
        try:
            from accounts.models import Company
            from accounts.rls import rls_bypass
            from tenant.models import TenantDirectory

            with rls_bypass():
                company_count = Company.objects.count()
                tenant_count = TenantDirectory.objects.count()

            if company_count == tenant_count:
                return {
                    "status": "healthy",
                    "companies": company_count,
                    "tenant_entries": tenant_count,
                }
            else:
                return {
                    "status": "unhealthy",
                    "companies": company_count,
                    "tenant_entries": tenant_count,
                    "missing": company_count - tenant_count,
                    "error": "TenantDirectory entries missing",
                }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }

    @staticmethod
    def check_projection_lag() -> Dict[str, Any]:
        """Check projection consumer lag."""
        try:
            from events.models import EventBookmark, BusinessEvent
            from accounts.rls import rls_bypass

            with rls_bypass():
                bookmarks = EventBookmark.objects.select_related("company", "last_event").all()

                total_lag = 0
                consumers = []

                for bookmark in bookmarks:
                    total_events = BusinessEvent.objects.filter(
                        company=bookmark.company
                    ).count()

                    if bookmark.last_event:
                        processed = BusinessEvent.objects.filter(
                            company=bookmark.company,
                            company_sequence__lte=bookmark.last_event.company_sequence,
                        ).count()
                    else:
                        processed = 0

                    lag = total_events - processed
                    total_lag += lag

                    if lag > 0 or bookmark.error_count > 0:
                        consumers.append({
                            "consumer": bookmark.consumer_name,
                            "company": bookmark.company.slug,
                            "lag": lag,
                            "errors": bookmark.error_count,
                            "paused": bookmark.is_paused,
                        })

            # Consider healthy if total lag is under threshold
            lag_threshold = getattr(settings, "PROJECTION_LAG_THRESHOLD", 1000)
            status = "healthy" if total_lag < lag_threshold else "degraded"

            return {
                "status": status,
                "total_lag": total_lag,
                "threshold": lag_threshold,
                "consumers_with_lag": consumers[:10],  # Limit to 10
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }

    @staticmethod
    def get_full_health() -> Dict[str, Any]:
        """Get comprehensive health report."""
        checks = {
            "databases": HealthCheck.check_all_databases(),
            "redis": HealthCheck.check_redis(),
            "tenant_directory": HealthCheck.check_tenant_directory(),
            "projection_lag": HealthCheck.check_projection_lag(),
        }

        # Determine overall status
        statuses = [c.get("status", "unknown") for c in checks.values()]
        if all(s == "healthy" or s == "skipped" for s in statuses):
            overall = "healthy"
        elif any(s == "unhealthy" for s in statuses):
            overall = "unhealthy"
        else:
            overall = "degraded"

        return {
            "status": overall,
            "checks": checks,
            "version": getattr(settings, "VERSION", "unknown"),
            "environment": "production" if not settings.DEBUG else "development",
        }


class LivenessView(View):
    """
    Kubernetes liveness probe.

    Returns 200 if the process is running.
    This should be very fast and not check external dependencies.
    """

    def get(self, request):
        return JsonResponse({"status": "alive"})


class ReadinessView(View):
    """
    Kubernetes readiness probe.

    Returns 200 if the service can handle traffic.
    Checks database connectivity.
    """

    def get(self, request):
        db_check = HealthCheck.check_database("default")

        if db_check["status"] == "healthy":
            return JsonResponse({
                "status": "ready",
                "database": db_check,
            })
        else:
            return JsonResponse({
                "status": "not_ready",
                "database": db_check,
            }, status=503)


class FullHealthView(View):
    """
    Full health check for debugging and dashboards.

    Returns comprehensive health information.
    Should be protected in production (internal network only).
    """

    def get(self, request):
        health = HealthCheck.get_full_health()

        status_code = 200 if health["status"] == "healthy" else 503
        return JsonResponse(health, status=status_code)
