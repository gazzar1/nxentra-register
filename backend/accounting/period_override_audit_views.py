# accounting/period_override_audit_views.py
"""
A85 chunk 3 (2026-05-26): read-only API for the PeriodOverrideAudit log.

Backs the /audit/period-overrides report. Operators / accountants /
auditors see every time someone overrode the date-derived posting period.
Filterable by source, user, and date range. Sorted newest first.

No write endpoint here — audit rows are created by the systems that
perform the override (settlement import, manual JE, etc., as those
wiring tasks ship in A85 chunk 3b and beyond). Append-only.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounting.models import PeriodOverrideAudit
from accounts.authz import require, resolve_actor


def _serialize_audit(row: PeriodOverrideAudit) -> dict:
    return {
        "id": row.id,
        "source": row.source,
        "source_display": row.get_source_display(),
        "source_document_ref": row.source_document_ref,
        "journal_entry_id": row.journal_entry_id,
        "user_id": row.user_id,
        "user_email": row.user_email_snapshot,
        "user_name": row.user_name_snapshot,
        "original": {
            "date": row.original_date.isoformat() if row.original_date else None,
            "period": row.original_period,
            "fiscal_year": row.original_fiscal_year,
        },
        "override": {
            "period": row.override_period,
            "fiscal_year": row.override_fiscal_year,
        },
        "reason": row.reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class PeriodOverrideAuditListView(APIView):
    """GET /api/accounting/period-overrides/

    Read-only list of PeriodOverrideAudit rows for the current company.
    Newest first.

    Query params:
      - source: filter by source (SETTLEMENT_IMPORT / BANK_IMPORT /
        MANUAL_JE / RECON_MATCH / OTHER)
      - user_id: filter by who performed the override
      - since: ISO date — only rows created on/after this date
      - until: ISO date — only rows created on/before this date
      - limit (default 100, max 500), offset (default 0)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        # Anyone with reports.view can SEE the audit log (transparency);
        # only users with accounting.je.override_period can CREATE entries
        # (enforced by the systems that write them, A85 chunk 3b+).
        require(actor, "reports.view")

        qs = (
            PeriodOverrideAudit.objects.filter(company=actor.company)
            .select_related("user", "journal_entry")
            .order_by("-created_at")
        )

        source = request.query_params.get("source")
        if source:
            qs = qs.filter(source=source)

        user_id = request.query_params.get("user_id")
        if user_id:
            try:
                qs = qs.filter(user_id=int(user_id))
            except (TypeError, ValueError):
                return Response(
                    {"error": "user_id must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        since = request.query_params.get("since")
        if since:
            qs = qs.filter(created_at__date__gte=since)

        until = request.query_params.get("until")
        if until:
            qs = qs.filter(created_at__date__lte=until)

        try:
            limit = min(int(request.query_params.get("limit", 100)), 500)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except (TypeError, ValueError):
            limit, offset = 100, 0

        total = qs.count()
        page = qs[offset : offset + limit]

        return Response(
            {
                "results": [_serialize_audit(r) for r in page],
                "total_count": total,
                "limit": limit,
                "offset": offset,
            }
        )
