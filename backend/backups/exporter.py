# backups/exporter.py
"""
Company data exporter.

Produces a ZIP archive containing:
- manifest.json — metadata, version, model counts
- events.json — the full BusinessEvent stream (source of truth)
- models/<label>.json — write models and read model snapshots

Usage:
    from backups.exporter import export_company
    zip_bytes, metadata = export_company(company)
"""

import hashlib
import io
import json
import logging
import zipfile
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db import models
from django.utils import timezone

from accounts.rls import rls_bypass

logger = logging.getLogger(__name__)

FORMAT_VERSION = "1.0"


class BackupEncoder(json.JSONEncoder):
    """JSON encoder for backup data — handles Decimal, datetime, date, UUID."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, UUID):
            return str(obj)
        return super().default(obj)


def _serialize_instance(instance, excluded_fields=None):
    """Serialize a model instance to a dict of field values."""
    excluded = set(excluded_fields or [])
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in excluded:
            data[field.name] = None
            continue

        value = field.value_from_object(instance)

        # Convert special types for JSON
        if isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, datetime | date):
            value = value.isoformat() if value else None
        elif isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, bytes):
            value = value.hex()

        data[field.name] = value

    return data


def export_company(company):
    """
    Export all company data to a ZIP archive.

    Returns:
        tuple: (zip_bytes: bytes, metadata: dict)
    """
    from backups.model_registry import EXCLUDED_FIELDS, get_export_registry

    started_at = timezone.now()
    registry = get_export_registry()

    manifest = {
        "format_version": FORMAT_VERSION,
        "exported_at": timezone.now().isoformat(),
        "company": {
            "id": company.id,
            "public_id": str(company.public_id),
            "slug": company.slug,
            "name": company.name,
            "name_ar": getattr(company, "name_ar", ""),
            "default_currency": company.default_currency,
            "functional_currency": getattr(company, "functional_currency", "USD"),
            "fiscal_year_start_month": company.fiscal_year_start_month,
        },
        "model_counts": {},
        "event_count": 0,
        "total_records": 0,
    }

    buf = io.BytesIO()
    hasher = hashlib.sha256()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf, rls_bypass():
        total_records = 0

        for label, model_cls in registry.items():
            excluded = EXCLUDED_FIELDS.get(label, [])

            # Build queryset filtered to company
            qs = _get_company_queryset(model_cls, company)
            if qs is None:
                continue

            # Order by PK for deterministic export
            qs = qs.order_by("pk")

            records = []
            for instance in qs.iterator(chunk_size=1000):
                record = _serialize_instance(instance, excluded)
                records.append(record)

            if not records:
                manifest["model_counts"][label] = 0
                continue

            # Write to ZIP as models/<label>.json
            json_bytes = json.dumps(records, cls=BackupEncoder, ensure_ascii=False).encode("utf-8")
            zf.writestr(f"models/{label}.json", json_bytes)

            # Update hash
            hasher.update(json_bytes)

            count = len(records)
            manifest["model_counts"][label] = count
            total_records += count

            if label == "events.BusinessEvent":
                manifest["event_count"] = count

            logger.info("Exported %d records for %s", count, label)

        manifest["total_records"] = total_records
        manifest["export_hash"] = hasher.hexdigest()

        # Write manifest
        manifest_bytes = json.dumps(manifest, cls=BackupEncoder, indent=2, ensure_ascii=False).encode("utf-8")
        zf.writestr("manifest.json", manifest_bytes)

    zip_bytes = buf.getvalue()
    elapsed = (timezone.now() - started_at).total_seconds()

    metadata = {
        "file_size_bytes": len(zip_bytes),
        "file_checksum": hashlib.sha256(zip_bytes).hexdigest(),
        "event_count": manifest["event_count"],
        "model_counts": manifest["model_counts"],
        "total_records": total_records,
        "duration_seconds": round(elapsed, 2),
        "export_hash": manifest["export_hash"],
    }

    return zip_bytes, metadata


def _get_company_queryset(model_cls, company):
    """
    Return a queryset of model instances scoped to company.

    Handles ForeignKey and OneToOneField to Company.
    Special case for EventPayload (no company FK — export those
    referenced by the company's events).
    """
    from events.models import EventPayload

    # EventPayload has no company FK — export those referenced by company events
    if model_cls is EventPayload:
        from events.models import BusinessEvent

        payload_ids = (
            BusinessEvent.objects.filter(company=company, payload_ref__isnull=False)
            .values_list("payload_ref_id", flat=True)
            .distinct()
        )
        return EventPayload.objects.filter(id__in=payload_ids)

    # Check for company field
    company_field = None
    for field in model_cls._meta.get_fields():
        if isinstance(field, models.ForeignKey | models.OneToOneField):
            if field.related_model and field.related_model.__name__ == "Company":
                company_field = field.name
                break

    if not company_field:
        logger.warning("Model %s has no company field, skipping", model_cls.__name__)
        return None

    return model_cls.objects.filter(**{company_field: company})
