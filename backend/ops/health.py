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
from typing import Any

from django.conf import settings
from django.db import connections
from django.http import JsonResponse
from django.views import View

logger = logging.getLogger(__name__)

# =============================================================================
# A5-PR2b: adapter-registered counters of OPEN rejected source evidence —
# authenticated provider payloads durably rejected at ingress (no event, so no
# ProjectionFailureLog; adapter-owned tables, so the core cannot import them —
# the adapter registers how to count, the PR #129 register_known_order_lookup
# inversion: adapter depends on core, never the reverse). Each counter returns
# the adapter's count of open (unacknowledged, unsuperseded) evidence rows and
# is folded into the combined /_health/alerts exception pool.
#
# A5-PR1a adds a sibling registry of SOURCE-HEALTH condition counters (e.g. a
# pilot provider store whose token was revoked, or whose scheduled sync has gone
# stale) — each registered condition becomes a top-level integer field of the
# /_health/alerts body and any nonzero count makes the endpoint unhealthy.
# =============================================================================

_REJECTED_EVIDENCE_COUNTERS: dict[str, Any] = {}

_SOURCE_HEALTH_COUNTERS: dict[str, Any] = {}

# The fixed core fields of the /_health/alerts body. A registered source-health
# condition becomes a top-level field of that body, so it may never shadow one.
_ALERT_CORE_FIELDS = frozenset(
    {
        "status",
        "unresolved_failures",
        "unresolved_import_rejects",
        "open_rejected_evidence",
        "rejected_evidence_by_source",
        "total_lag",
        "paused_consumers",
        "errored_consumers",
        "stale_consumers",
        "missing_consumers",
        "alert_counter_errors",
        "thresholds",
    }
)


def _register_alert_counter(registry: dict, kind: str, name: str, counter) -> None:
    """A5-PR1a: registration is append-only per name — silently replacing an
    alert counter would drop a whole family from the /_health/alerts pool and
    read as healthy (the same bypass-door class the projection apply-validator
    registry refuses to allow). A repeat registration of the SAME callback
    (identical ``__module__`` + ``__qualname__`` — what a re-run
    ``AppConfig.ready`` produces, since it re-defines the closure at the same
    source location) is an idempotent no-op; a DIFFERENT callback under an
    already-registered name raises."""
    existing = registry.get(name)
    if existing is not None:
        same_callback = getattr(existing, "__module__", None) == getattr(counter, "__module__", None) and getattr(
            existing, "__qualname__", None
        ) == getattr(counter, "__qualname__", None)
        if not same_callback:
            raise RuntimeError(
                f"Conflicting {kind} counter registration for {name!r}: "
                f"{getattr(existing, '__module__', '?')}.{getattr(existing, '__qualname__', '?')} "
                f"is already registered; refusing to silently replace it with "
                f"{getattr(counter, '__module__', '?')}.{getattr(counter, '__qualname__', '?')}."
            )
    registry[name] = counter


def register_rejected_evidence_counter(name: str, counter) -> None:
    """Register an adapter's open-rejected-evidence counter.
    ``counter`` is a zero-arg callable returning an int; it runs inside the
    alert computation's rls_bypass (a cross-company ops read, like the sibling
    ImportRejectedRow count). Duplicate names refuse loudly unless the repeat
    is the same callback re-registered by a re-run app-ready."""
    _register_alert_counter(_REJECTED_EVIDENCE_COUNTERS, "rejected-evidence", name, counter)


def register_source_health_counter(condition: str, counter) -> None:
    """A5-PR1a: register an adapter's source-health condition counter.

    ``condition`` is the adapter-namespaced field name the count is published
    under in the /_health/alerts body (adapter-namespaced, e.g. ``<source>_reauth_required``); any
    nonzero count makes the endpoint unhealthy. ``counter`` is a zero-arg
    callable returning an aggregate int — no row data, no tenant identity —
    and runs inside the alert computation's rls_bypass. Duplicate conditions
    refuse loudly unless the repeat is the same callback (re-run app-ready)."""
    if condition in _ALERT_CORE_FIELDS:
        raise RuntimeError(
            f"Source-health condition {condition!r} would shadow a core /_health/alerts field — pick an adapter-namespaced name."
        )
    _register_alert_counter(_SOURCE_HEALTH_COUNTERS, "source-health", condition, counter)


class HealthCheck:
    """Health check implementation."""

    @staticmethod
    def check_database(alias: str = "default") -> dict[str, Any]:
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
    def check_all_databases() -> dict[str, Any]:
        """Check all configured databases."""
        results = {}
        all_healthy = True

        for alias in settings.DATABASES:
            result = HealthCheck.check_database(alias)
            results[alias] = result
            if result["status"] != "healthy":
                all_healthy = False

        return {
            "status": "healthy" if all_healthy else "degraded",
            "databases": results,
        }

    @staticmethod
    def check_redis() -> dict[str, Any]:
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
    def check_tenant_directory() -> dict[str, Any]:
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
    def check_projection_lag() -> dict[str, Any]:
        """Check projection consumer lag."""
        try:
            from accounts.rls import rls_bypass
            from events.models import BusinessEvent, EventBookmark

            with rls_bypass():
                bookmarks = EventBookmark.objects.select_related("company", "last_event").all()

                total_lag = 0
                consumers = []

                for bookmark in bookmarks:
                    total_events = BusinessEvent.objects.filter(company=bookmark.company).count()

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
                        consumers.append(
                            {
                                "consumer": bookmark.consumer_name,
                                "company": bookmark.company.slug,
                                "lag": lag,
                                "errors": bookmark.error_count,
                                "paused": bookmark.is_paused,
                            }
                        )

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
    def check_reconciliation() -> dict[str, Any]:
        """
        Check AR/AP reconciliation status across all active companies.

        Note: Capped to first 100 companies to keep health check latency
        bounded. For full coverage, use the reconciliation_check management
        command (python manage.py reconciliation_check --strict).
        """
        try:
            from accounts.models import Company
            from accounts.rls import rls_bypass

            with rls_bypass():
                companies = list(Company.objects.filter(is_active=True)[:100])

            if not companies:
                return {"status": "skipped", "reason": "No active companies"}

            from accounting.commands import validate_subledger_tieout

            imbalances = []
            for company in companies:
                try:
                    with rls_bypass():
                        valid, errors = validate_subledger_tieout(company)
                    if not valid:
                        imbalances.append(
                            {
                                "company": company.slug,
                                "errors": errors[:3],
                            }
                        )
                except Exception:
                    pass  # Skip companies that fail (e.g., no accounts)

            if not imbalances:
                return {
                    "status": "healthy",
                    "companies_checked": len(companies),
                }
            else:
                return {
                    "status": "degraded",
                    "companies_checked": len(companies),
                    "imbalances": imbalances[:10],
                }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @staticmethod
    def get_full_health() -> dict[str, Any]:
        """Get comprehensive health report."""
        checks = {
            "databases": HealthCheck.check_all_databases(),
            "redis": HealthCheck.check_redis(),
            "tenant_directory": HealthCheck.check_tenant_directory(),
            "projection_lag": HealthCheck.check_projection_lag(),
            "reconciliation": HealthCheck.check_reconciliation(),
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
            return JsonResponse(
                {
                    "status": "ready",
                    "database": db_check,
                }
            )
        else:
            return JsonResponse(
                {
                    "status": "not_ready",
                    "database": db_check,
                },
                status=503,
            )


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


def _alert_staleness_seconds() -> int:
    """ALERT_PROJECTION_STALENESS_SECONDS — Django setting first, environment
    fallback (settings.py deliberately untouched to hold this PR to its agreed
    file envelope; an env var reaches it either way). Default 21600s (6h):
    ABOVE the 4h poller-backfill window that can legitimately hold a deferred
    refund event pending, far BELOW the indefinite blindness of the count-only
    lag threshold when a small backlog sits unprocessed under dead workers."""
    import os

    from django.conf import settings

    value = getattr(settings, "ALERT_PROJECTION_STALENESS_SECONDS", None)
    if value is None:
        value = os.getenv("ALERT_PROJECTION_STALENESS_SECONDS", "21600")
    return int(value)


def compute_alert_state() -> dict:
    """A163: the ONE alert condition an external uptime pinger watches.

    Unhealthy when any financial/projection failure needs a human:
    - unresolved ProjectionFailureLog rows, ImportRejectedRow rows, and
      adapter-registered open rejected source evidence (combined pool
      > ALERT_UNRESOLVED_FAILURES_MAX)
    - relevance-aware projection lag (> ALERT_PROJECTION_LAG_THRESHOLD;
      the pre-A135 coarse whole-stream count reported phantom lag and
      would page on healthy systems)
    - any paused or erroring projection bookmark
    - A5-PR1a: any STALE consumer — relevant work is pending and its oldest
      pending event is older than ALERT_PROJECTION_STALENESS_SECONDS (the
      count threshold alone let a small-but-dead backlog read healthy
      indefinitely); a legitimately idle consumer (no pending work) never
      pages on age alone
    - A5-PR1a: any MISSING consumer — a registered projection/company pair
      with relevant events but NO bookmark row (a consumer that never drained
      was invisible to the bookmark iteration entirely)
    - A5-PR1a: any registered alert counter that raised (its count is
      unknown — that must page, never read as zero), and any nonzero
      adapter-registered source-health condition (e.g. a pilot provider store
      needing reauth or gone stale)

    Runs in the WEB process — the Celery worker being dead is exactly the
    failure class this must catch, so it cannot live in Celery. Pure
    reads; aggregate-only output (the /_health/ prefix is auth-exempt).
    """
    from django.conf import settings
    from django.utils import timezone

    from accounting.models import ImportRejectedRow
    from accounts.rls import rls_bypass
    from events.models import BusinessEvent, EventBookmark
    from projections.base import projection_registry
    from projections.models import ProjectionFailureLog

    max_failures = int(getattr(settings, "ALERT_UNRESOLVED_FAILURES_MAX", 0))
    lag_threshold = int(getattr(settings, "ALERT_PROJECTION_LAG_THRESHOLD", 50))
    staleness_seconds = _alert_staleness_seconds()

    counter_errors = 0
    with rls_bypass():
        unresolved = ProjectionFailureLog.objects.filter(resolved=False).count()
        # A5-PR3: a dropped settlement/bank source row is a real financial
        # exception with no event (so no ProjectionFailureLog) — page on it too.
        unresolved_rejects = ImportRejectedRow.objects.filter(resolved=False).count()
        # A5-PR2b: rejected provider source evidence (adapter-registered
        # counters — e.g. a malformed provider order payload durably rejected at
        # ingress) is the same class of unresolved financial exception.
        # A5-PR1a: a counter that raises has an UNKNOWN count — that pages via
        # alert_counter_errors (and the structured 503 body survives), it never
        # silently reads as zero and never escapes as an uncontrolled 500.
        rejected_evidence_by_source: dict[str, int] = {}
        for name, counter in sorted(_REJECTED_EVIDENCE_COUNTERS.items()):
            try:
                rejected_evidence_by_source[name] = int(counter())
            except Exception:
                logger.exception("alert: rejected-evidence counter %r failed — paging via alert_counter_errors", name)
                counter_errors += 1
        open_rejected_evidence = sum(rejected_evidence_by_source.values())

        # A5-PR1a: adapter-registered source-health conditions (each becomes a
        # top-level integer field; nonzero = unhealthy). A failed counter's
        # field is OMITTED — its value is unknown and must never be fabricated
        # as 0 — while alert_counter_errors makes the state unhealthy.
        source_health: dict[str, int] = {}
        for condition, counter in sorted(_SOURCE_HEALTH_COUNTERS.items()):
            try:
                source_health[condition] = int(counter())
            except Exception:
                logger.exception("alert: source-health counter %r failed — paging via alert_counter_errors", condition)
                counter_errors += 1

        now = timezone.now()
        total_lag = 0
        paused = 0
        errored = 0
        stale = 0
        seen_pairs: set[tuple[str, int]] = set()
        # A5-PR1a: iterate EVERY company-scoped bookmark. The old unordered
        # [:500] slice made the scanned subset database-arbitrary above 500
        # rows, so a paused/erroring bookmark past the cap silently vanished
        # from all three consumer counters — a false all-clear.
        # company__isnull guard: EventBookmark.company is nullable and a
        # global bookmark would AttributeError the naive metrics path.
        bookmarks = EventBookmark.objects.filter(company__isnull=False).select_related("last_event").iterator()
        for bookmark in bookmarks:
            seen_pairs.add((bookmark.consumer_name, bookmark.company_id))
            if bookmark.is_paused:
                paused += 1
            if bookmark.error_count > 0:
                errored += 1
            projection = projection_registry.get(bookmark.consumer_name)
            if projection is None:
                continue
            pending = BusinessEvent.objects.filter(
                company_id=bookmark.company_id,
                event_type__in=projection.consumes,
            )
            if bookmark.last_event_id:
                pending = pending.filter(company_sequence__gt=bookmark.last_event.company_sequence)
            lag = pending.count()
            total_lag += lag
            # A5-PR1a: age-aware staleness. Stale only when relevant work is
            # PENDING and its oldest pending event has aged past the threshold
            # (recorded_at = server receipt time — occurred_at is emitter-
            # suppliable and could be backdated). An idle consumer with no
            # pending work can never page on age alone.
            if lag > 0 and staleness_seconds >= 0:
                oldest = pending.order_by("company_sequence").values_list("recorded_at", flat=True).first()
                if oldest is not None and (now - oldest).total_seconds() > staleness_seconds:
                    stale += 1

        # A5-PR1a: the expected consumer set is the projection REGISTRY × the
        # companies that hold relevant events — never just the existing
        # bookmark rows. A registered projection that has never drained for a
        # company has no bookmark, so its unprocessed financial events read as
        # zero lag above; count the pair as MISSING instead.
        missing = 0
        for projection in projection_registry.all():
            consumes = list(projection.consumes or [])
            if not consumes:
                continue
            company_ids = (
                BusinessEvent.objects.filter(event_type__in=consumes).values_list("company_id", flat=True).distinct()
            )
            for company_id in company_ids:
                if company_id is None:
                    continue
                if (projection.name, company_id) not in seen_pairs:
                    missing += 1

    healthy = (
        # Combined pool vs the threshold (Codex P2): projection failures, import
        # rejects and rejected source evidence are all unresolved financial
        # exceptions — a max of N must bound their TOTAL, not each independently.
        (unresolved + unresolved_rejects + open_rejected_evidence) <= max_failures
        and total_lag <= lag_threshold
        and paused == 0
        and errored == 0
        and stale == 0
        and missing == 0
        and counter_errors == 0
        and all(count == 0 for count in source_health.values())
    )
    state = {
        "status": "healthy" if healthy else "unhealthy",
        "unresolved_failures": unresolved,
        "unresolved_import_rejects": unresolved_rejects,
        "open_rejected_evidence": open_rejected_evidence,
        "rejected_evidence_by_source": rejected_evidence_by_source,
        "total_lag": total_lag,
        "paused_consumers": paused,
        "errored_consumers": errored,
        "stale_consumers": stale,
        "missing_consumers": missing,
        "alert_counter_errors": counter_errors,
        "thresholds": {
            "unresolved_failures_max": max_failures,
            "projection_lag_threshold": lag_threshold,
            "projection_staleness_seconds": staleness_seconds,
        },
    }
    # Registered source-health conditions publish as top-level integer fields
    # (names are validated against _ALERT_CORE_FIELDS at registration).
    state.update(source_health)
    return state


class AlertHealthView(View):
    """A163: GET /_health/alerts — the endpoint an EXTERNAL uptime pinger
    (UptimeRobot/BetterStack) polls so a projection failure or stalled
    stream demonstrably reaches a human. 200 healthy / 503 unhealthy.

    Deliberately separate from /_health/ready: readiness feeds load
    balancers, and pulling a web process out of rotation because a
    projection lags would make an accounting problem into an outage.
    """

    def get(self, request):
        state = compute_alert_state()
        return JsonResponse(state, status=200 if state["status"] == "healthy" else 503)
