# projections/failure_log_views.py
"""
API views for ProjectionFailureLog (A80, 2026-05-25).

Backs the /finance/exceptions operator page. List unresolved failures,
view details, mark resolved with a note. Aggregated summary endpoint for
projection_health-style overview.

Permission model:
- list / detail / summary: any authenticated user with reports.view
  (so operators and accountants can see exceptions, not just admins)
- resolve: requires staff/superuser OR a specific permission
  (resolving a failure changes operator workflow state — restrict)

Tenant isolation: every endpoint filters on `actor.company`. No
cross-tenant leakage.

See:
- projections/models.py ProjectionFailureLog
- docs/finance_event_first_policy.md §8
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authz import require, resolve_actor
from projections.models import ProjectionFailureLog


def _serialize_failure(log: ProjectionFailureLog) -> dict:
    """Single-record shape returned by both list and detail views."""
    return {
        "id": log.id,
        "projection_name": log.projection_name,
        "event_id": str(log.event_id),
        "event_type": log.event_type,
        "category": log.category,
        "category_display": log.get_category_display(),
        "message": log.message,
        "fix_hint": log.fix_hint,
        "occurrence_count": log.occurrence_count,
        "first_seen_at": log.first_seen_at.isoformat() if log.first_seen_at else None,
        "last_seen_at": log.last_seen_at.isoformat() if log.last_seen_at else None,
        "resolved": log.resolved,
        "resolved_at": log.resolved_at.isoformat() if log.resolved_at else None,
        "resolved_by_id": log.resolved_by_id,
        "resolved_by_name": (
            getattr(log.resolved_by, "name", "") or getattr(log.resolved_by, "email", "")
            if log.resolved_by_id
            else None
        ),
        "resolution_note": log.resolution_note,
    }


class ProjectionFailureListView(APIView):
    """GET /api/reports/projection-failures/

    List failure log entries for the current company. Filterable by:
    - `resolved=true|false` (default: false — show only open failures)
    - `projection_name=shopify_accounting`
    - `category=MISSING_CONFIG|INVALID_DATA|DOWNSTREAM_FAILED|UNEXPECTED`
    - `event_type=shopify.order_paid`

    Pagination: `limit` (default 100, max 500), `offset` (default 0).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)

        qs = (
            ProjectionFailureLog.objects.filter(company=actor.company)
            .select_related("event", "resolved_by")
            .order_by("-last_seen_at")
        )

        resolved_param = request.query_params.get("resolved")
        if resolved_param == "true":
            qs = qs.filter(resolved=True)
        elif resolved_param == "false" or resolved_param is None:
            # Default: only open failures (the operator's actionable queue)
            qs = qs.filter(resolved=False)
        # resolved_param == "all" → no filter

        projection_name = request.query_params.get("projection_name")
        if projection_name:
            qs = qs.filter(projection_name=projection_name)

        category = request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)

        event_type = request.query_params.get("event_type")
        if event_type:
            qs = qs.filter(event_type=event_type)

        try:
            limit = min(int(request.query_params.get("limit", 100)), 500)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except (ValueError, TypeError):
            limit, offset = 100, 0

        total_count = qs.count()
        page = qs[offset : offset + limit]

        return Response(
            {
                "results": [_serialize_failure(log) for log in page],
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
            }
        )


class ProjectionFailureDetailView(APIView):
    """GET /api/reports/projection-failures/<id>/

    Single-failure detail. Includes the event's `data` payload so the
    operator can inspect what was being processed when it failed.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = resolve_actor(request)

        try:
            log = ProjectionFailureLog.objects.select_related("event", "resolved_by").get(pk=pk, company=actor.company)
        except ProjectionFailureLog.DoesNotExist:
            return Response(
                {"detail": "Projection failure not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        payload = _serialize_failure(log)
        # Add the event's payload data so operator can see what was
        # being processed when the handler failed.
        try:
            event_data = log.event.get_data() if log.event else {}
        except Exception:
            event_data = {}
        payload["event_data"] = event_data
        payload["event_aggregate_type"] = log.event.aggregate_type if log.event else ""
        payload["event_aggregate_id"] = log.event.aggregate_id if log.event else ""

        return Response(payload)


class ProjectionFailureResolveView(APIView):
    """POST /api/reports/projection-failures/<id>/resolve/

    Mark a failure as resolved. Optional body: `{"resolution_note": "..."}`.

    Note: if the same event fails again after this, the resolved flag
    auto-clears (per A80 dedup contract in models.py). This endpoint is
    for "I fixed the underlying problem and want to clear the operator
    queue."

    Permission (F22): company OWNER/ADMIN — or platform staff/superuser.
    The old is_staff-only gate meant a merchant OWNER could see their own
    stuck exceptions but never clear them, undercutting the truth-engine
    positioning. Junior roles (USER/VIEWER) stay excluded so the queue
    isn't cleared accidentally.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        if not (request.user.is_staff or request.user.is_superuser or actor.is_admin):
            return Response(
                {"detail": "Owner/admin access required to resolve projection failures."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            log = ProjectionFailureLog.objects.get(pk=pk, company=actor.company)
        except ProjectionFailureLog.DoesNotExist:
            return Response(
                {"detail": "Projection failure not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if log.resolved:
            return Response(
                {"detail": "Already resolved.", **_serialize_failure(log)},
                status=status.HTTP_200_OK,
            )

        note = (request.data.get("resolution_note") or "").strip()[:2000]
        # A3-PR3 (Codex round-15 P1): route through mark_resolved — the ONE
        # place that guarantees an operator resolution cannot reproduce the
        # framework's self-heal signature.
        log.mark_resolved(request.user, note=note)

        return Response(_serialize_failure(log))


class ProjectionFailureSummaryView(APIView):
    """GET /api/reports/projection-failures/summary/

    Aggregate counts for the projection_health dashboard:
    - Total unresolved
    - Unresolved per projection_name
    - Unresolved per category

    Cheap query (uses the idx_pfl_company_unresolved + idx_pfl_company_proj_resolved
    indexes from migration 0012).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Count

        actor = resolve_actor(request)

        unresolved = ProjectionFailureLog.objects.filter(company=actor.company, resolved=False)

        by_projection = list(unresolved.values("projection_name").annotate(count=Count("id")).order_by("-count"))
        by_category = list(unresolved.values("category").annotate(count=Count("id")).order_by("-count"))
        # Add the human-readable category label.
        category_labels = dict(ProjectionFailureLog.Category.choices)
        for row in by_category:
            row["category_display"] = category_labels.get(row["category"], row["category"])

        return Response(
            {
                "total_unresolved": unresolved.count(),
                "by_projection": by_projection,
                "by_category": by_category,
            }
        )


# =============================================================================
# A5-PR3: durable per-row import-reject records (settlement / bank CSV) — a
# sibling operator queue on /finance/exceptions for rows dropped at ingest with
# no event (so no ProjectionFailureLog can hold them).
# =============================================================================


def _serialize_reject(row) -> dict:
    return {
        "id": row.id,
        "public_id": str(row.public_id),
        "source_kind": row.source_kind,
        "provider_code": row.provider_code,
        "source_filename": row.source_filename,
        "import_batch_id": str(row.import_batch_id),
        "row_index": row.row_index,
        "reason_code": row.reason_code,
        "reason_message": row.reason_message,
        "status": row.status,
        "raw_row": row.raw_row,
        "occurrence_count": row.occurrence_count,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "resolved": row.resolved,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "resolved_by_id": row.resolved_by_id,
        "resolution_note": row.resolution_note,
    }


class ImportRejectedRowListView(APIView):
    """GET /api/reports/import-rejected-rows/

    List durable per-row import rejections for the current company (settlement +
    bank-CSV rows dropped at ingest). Filters: resolved (default false),
    source_kind, reason_code. Pagination: limit (default 100, max 500), offset.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounting.models import ImportRejectedRow

        actor = resolve_actor(request)
        # raw_row carries financial import evidence — gate on the same read
        # permission as other report/audit views (Codex P2).
        require(actor, "reports.view")
        qs = (
            ImportRejectedRow.objects.filter(company=actor.company)
            .select_related("resolved_by")
            .order_by("-last_seen_at")
        )

        resolved_param = request.query_params.get("resolved")
        if resolved_param == "true":
            qs = qs.filter(resolved=True)
        elif resolved_param == "false" or resolved_param is None:
            qs = qs.filter(resolved=False)

        source_kind = request.query_params.get("source_kind")
        if source_kind:
            qs = qs.filter(source_kind=source_kind)
        reason_code = request.query_params.get("reason_code")
        if reason_code:
            qs = qs.filter(reason_code=reason_code)

        try:
            limit = min(max(int(request.query_params.get("limit", 100)), 1), 500)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except (ValueError, TypeError):
            limit, offset = 100, 0

        total_count = qs.count()
        page = qs[offset : offset + limit]
        return Response(
            {
                "results": [_serialize_reject(r) for r in page],
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
            }
        )


class ImportRejectedRowResolveView(APIView):
    """POST /api/reports/import-rejected-rows/<id>/resolve/

    Operator ack (owner/admin) — a claim of handling (corrected via re-upload or a
    manual journal), NOT proof of reprocessing (rejected rows never replay).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from accounting.models import ImportRejectedRow

        actor = resolve_actor(request)
        # Same read gate as the list view — the response echoes raw_row financial
        # evidence, so an admin with reports.view revoked must not read it here
        # either (Codex round-2 P2).
        require(actor, "reports.view")
        if not (request.user.is_staff or request.user.is_superuser or actor.is_admin):
            return Response(
                {"detail": "Owner/admin access required to resolve import rejections."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            row = ImportRejectedRow.objects.get(pk=pk, company=actor.company)
        except ImportRejectedRow.DoesNotExist:
            return Response({"detail": "Import-rejected row not found."}, status=status.HTTP_404_NOT_FOUND)

        if row.resolved:
            return Response({"detail": "Already resolved.", **_serialize_reject(row)}, status=status.HTTP_200_OK)

        note = (request.data.get("resolution_note") or "").strip()[:2000]
        row.mark_resolved(request.user, note=note)
        return Response(_serialize_reject(row))
