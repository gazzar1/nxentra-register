# accounting/settlement_import_views.py
"""
A14: API endpoints for the manual settlement-CSV import flow.

POST /api/accounting/settlements/import/  (multipart/form-data)
    file:     CSV upload
    provider: 'paymob' | 'bosta' (or any normalized SettlementProvider code)
    payment_method (optional): 'card' / 'cod' / 'bank_transfer' / ...

Returns a per-batch summary of what was emitted, plus deduplication info.
The caller polls /api/accounting/reconciliation/summary/ afterwards to see
the updated Stage 2 numbers.
"""

from __future__ import annotations

import logging

from rest_framework import status as http_status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authz import resolve_actor
from projections.tasks import process_company_projections

from .settlement_imports import (
    SettlementImportError,
    import_settlement_csv,
)

logger = logging.getLogger(__name__)


_SUPPORTED_PROVIDERS = ("paymob", "bosta")


class SettlementCSVImportView(APIView):
    """Upload a settlement CSV and emit one PAYMENT_SETTLEMENT_RECEIVED
    event per batch found. The PaymentSettlementProjection consumes the
    events and posts the JE that drains the provider's clearing balance."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=http_status.HTTP_400_BAD_REQUEST)

        upload = request.FILES.get("file")
        if not upload:
            return Response(
                {"detail": "Multipart field 'file' is required."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        provider = (request.data.get("provider") or "").strip().lower()
        if provider not in _SUPPORTED_PROVIDERS:
            return Response(
                {
                    "detail": (
                        f"provider must be one of {_SUPPORTED_PROVIDERS}. "
                        f"Other providers will be supported as their CSV "
                        f"formats are documented."
                    )
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        payment_method = (request.data.get("payment_method") or "").strip().lower() or None

        # 10 MB ceiling — Paymob/Bosta statements top out at a few hundred KB.
        if upload.size > 10 * 1024 * 1024:
            return Response(
                {"detail": "CSV file too large (10 MB max)."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        try:
            content = upload.read()
        except OSError as exc:
            return Response(
                {"detail": f"Failed to read upload: {exc}"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        try:
            emitted = import_settlement_csv(
                company=actor.company,
                provider_normalized_code=provider,
                file_content=content,
                source_filename=getattr(upload, "name", "upload.csv"),
                payment_method=payment_method or "",
                external_system="shopify",
            )
        except SettlementImportError as exc:
            return Response(
                {"detail": str(exc)},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Settlement CSV import failed")
            return Response(
                {"detail": f"Unexpected error: {exc}"},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Run projections inline (not via .delay) so the caller can
        # immediately fetch the updated reconciliation summary and see
        # Stage 2 populated. The Celery worker would also pick them up
        # eventually, but UX is much better when the merchant clicks Save
        # and the dashboard refreshes with the new numbers.
        try:
            process_company_projections.run(
                company_id=actor.company.id,
                projection_names=["payment_settlement"],
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("Inline projection run after CSV import failed; events still queued")

        return Response(
            {
                "provider": provider,
                "filename": getattr(upload, "name", ""),
                "batches": emitted,
                "batch_count": len(emitted),
            },
            status=http_status.HTTP_200_OK,
        )
