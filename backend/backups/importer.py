# backups/importer.py
"""
Company data importer (restore from backup).

Reads a ZIP archive produced by exporter.py and restores all data
into the target company. Supports CLEAN_RESTORE mode only (Phase 1):
the target company must have no existing events.

Usage:
    from backups.importer import restore_company
    result = restore_company(company, zip_file)
"""
import hashlib
import io
import json
import logging
import zipfile
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db import models, transaction
from django.utils import timezone

from accounts.rls import rls_bypass

logger = logging.getLogger(__name__)

SUPPORTED_VERSIONS = {"1.0"}


class RestoreError(Exception):
    """Raised when restore encounters an unrecoverable problem."""
    pass


def restore_company(company, zip_file):
    """
    Restore company data from a backup ZIP archive.

    Args:
        company: Target Company instance (must be empty or will be cleared)
        zip_file: File-like object or bytes containing the ZIP archive

    Returns:
        dict with restore statistics

    Raises:
        RestoreError: If the archive is invalid or restore fails
    """
    from backups.model_registry import get_export_registry, EXCLUDED_FIELDS

    started_at = timezone.now()

    # Read ZIP
    if isinstance(zip_file, bytes):
        zip_file = io.BytesIO(zip_file)

    try:
        zf = zipfile.ZipFile(zip_file, "r")
    except zipfile.BadZipFile:
        raise RestoreError("Invalid backup file: not a valid ZIP archive.")

    # Read and validate manifest
    try:
        manifest_bytes = zf.read("manifest.json")
        manifest = json.loads(manifest_bytes)
    except (KeyError, json.JSONDecodeError) as e:
        raise RestoreError(f"Invalid backup file: cannot read manifest. {e}")

    version = manifest.get("format_version")
    if version not in SUPPORTED_VERSIONS:
        raise RestoreError(
            f"Unsupported backup version '{version}'. "
            f"Supported: {', '.join(SUPPORTED_VERSIONS)}"
        )

    registry = get_export_registry()
    stats = {"imported": {}, "skipped": {}, "cleared": 0, "errors": []}

    with rls_bypass():
        from projections.write_barrier import projection_writes_allowed, bootstrap_writes_allowed

        with bootstrap_writes_allowed(), projection_writes_allowed():
            # Phase 1: Clear existing company data (reverse dependency order)
            cleared = _clear_company_data(company, registry)
            stats["cleared"] = cleared

            # Phase 2: Import data in dependency order
            with transaction.atomic():
                # Build PK mapping: {label: {old_pk: new_pk}}
                pk_map = {}

                for label, model_cls in registry.items():
                    json_path = f"models/{label}.json"
                    if json_path not in zf.namelist():
                        stats["skipped"][label] = "not in backup"
                        continue

                    try:
                        data_bytes = zf.read(json_path)
                        records = json.loads(data_bytes)
                    except (json.JSONDecodeError, KeyError) as e:
                        stats["errors"].append(f"{label}: {e}")
                        continue

                    if not records:
                        stats["imported"][label] = 0
                        continue

                    excluded = EXCLUDED_FIELDS.get(label, [])
                    count = _import_model_records(
                        model_cls, company, records, pk_map, label, excluded
                    )
                    stats["imported"][label] = count
                    logger.info("Imported %d records for %s", count, label)

    zf.close()

    elapsed = (timezone.now() - started_at).total_seconds()
    stats["duration_seconds"] = round(elapsed, 2)
    stats["company"] = company.slug

    # Update company settings from manifest
    _update_company_settings(company, manifest.get("company", {}))

    return stats


def _clear_company_data(company, registry):
    """Delete all existing company data in reverse dependency order."""
    from events.models import EventPayload, BusinessEvent

    total_deleted = 0
    reversed_labels = list(reversed(registry.keys()))

    for label in reversed_labels:
        model_cls = registry[label]

        # EventPayload — skip deletion (content-addressed, may be shared)
        if model_cls is EventPayload:
            continue

        # Special handling for immutable models
        if model_cls is BusinessEvent:
            # Must use raw delete to bypass immutability guard
            count = BusinessEvent.objects.filter(company=company).count()
            if count > 0:
                BusinessEvent.objects.filter(company=company).delete()
                total_deleted += count
            continue

        qs = _get_company_qs_for_delete(model_cls, company)
        if qs is not None:
            count, _ = qs.delete()
            total_deleted += count

    return total_deleted


def _get_company_qs_for_delete(model_cls, company):
    """Get a queryset of model instances for a company (for deletion)."""
    for field in model_cls._meta.get_fields():
        if isinstance(field, (models.ForeignKey, models.OneToOneField)):
            if field.related_model and field.related_model.__name__ == "Company":
                return model_cls.objects.filter(**{field.name: company})
    return None


def _import_model_records(model_cls, company, records, pk_map, label, excluded_fields):
    """
    Import a list of serialized records into the database.

    Handles:
    - PK remapping (old PKs → new auto-generated PKs)
    - FK remapping using pk_map
    - Company field assignment
    - Skipping excluded fields
    """
    from events.models import EventPayload, BusinessEvent, CompanyEventCounter

    imported = 0
    old_to_new = {}

    for record in records:
        old_pk = record.get("id") or record.get("pk")

        # Prepare field values
        field_values = {}
        for field in model_cls._meta.concrete_fields:
            fname = field.name

            # Skip excluded fields
            if fname in (excluded_fields or []):
                continue

            if fname not in record:
                continue

            value = record[fname]

            # Company FK — point to target company
            if isinstance(field, (models.ForeignKey, models.OneToOneField)):
                if field.related_model and field.related_model.__name__ == "Company":
                    field_values[field.attname] = company.id
                    continue

                # Remap FK to already-imported records
                if value is not None:
                    related_label = _find_label_for_model(field.related_model, pk_map)
                    if related_label and related_label in pk_map:
                        remapped = pk_map[related_label].get(value)
                        if remapped is not None:
                            field_values[field.attname] = remapped
                            continue
                    # Self-referential FKs (e.g., caused_by_event)
                    if field.related_model == model_cls and label in pk_map:
                        remapped = pk_map[label].get(value)
                        if remapped is not None:
                            field_values[field.attname] = remapped
                            continue

                field_values[field.attname] = value
                continue

            # Type coercion
            value = _coerce_field_value(field, value)
            field_values[fname] = value

        # Handle special models
        if model_cls is EventPayload:
            # Content-addressed: skip if hash exists
            content_hash = field_values.get("content_hash")
            if content_hash:
                existing = EventPayload.objects.filter(content_hash=content_hash).first()
                if existing:
                    old_to_new[old_pk] = existing.pk
                    imported += 1
                    continue

            # Create new payload — bypass immutability by using raw create
            obj = EventPayload(
                content_hash=field_values.get("content_hash", ""),
                payload=field_values.get("payload", {}),
                size_bytes=field_values.get("size_bytes", 0),
                compression=field_values.get("compression", "none"),
            )
            models.Model.save(obj)
            old_to_new[old_pk] = obj.pk
            imported += 1
            continue

        if model_cls is BusinessEvent:
            # Bypass immutability — use raw insert
            # Don't let save() auto-assign sequences
            obj = BusinessEvent(**field_values)
            # Use Model.save to bypass BusinessEvent's custom save
            models.Model.save(obj)
            old_to_new[old_pk] = obj.pk
            imported += 1
            continue

        if model_cls is CompanyEventCounter:
            # OneToOneField — use update_or_create
            obj, _ = CompanyEventCounter.objects.update_or_create(
                company=company,
                defaults={"last_sequence": field_values.get("last_sequence", 0)},
            )
            old_to_new[old_pk] = obj.pk
            imported += 1
            continue

        # General model — let Django assign new PK
        # Remove old PK so Django auto-generates a new one
        pk_field_name = model_cls._meta.pk.name
        field_values.pop(pk_field_name, None)
        field_values.pop("id", None)

        try:
            obj = model_cls(**field_values)
            obj.save()
            old_to_new[old_pk] = obj.pk
            imported += 1
        except Exception as e:
            logger.warning("Failed to import %s record (pk=%s): %s", label, old_pk, e)

    pk_map[label] = old_to_new
    return imported


def _find_label_for_model(model_cls, pk_map):
    """Find the registry label for a given model class."""
    model_name = f"{model_cls._meta.app_label}.{model_cls.__name__}"
    if model_name in pk_map:
        return model_name
    # Try all keys
    for label in pk_map:
        parts = label.split(".")
        if len(parts) == 2 and parts[1] == model_cls.__name__:
            return label
    return None


def _coerce_field_value(field, value):
    """Coerce a JSON value to the correct Python type for a model field."""
    if value is None:
        return None

    if isinstance(field, models.DecimalField):
        return Decimal(str(value))
    elif isinstance(field, models.UUIDField):
        return UUID(str(value)) if value else None
    elif isinstance(field, models.DateTimeField):
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value
    elif isinstance(field, models.DateField):
        if isinstance(value, str):
            return date.fromisoformat(value)
        return value
    elif isinstance(field, models.BooleanField):
        return bool(value)
    elif isinstance(field, (models.IntegerField, models.BigIntegerField,
                            models.PositiveIntegerField, models.SmallIntegerField)):
        return int(value) if value is not None else None

    return value


def _update_company_settings(company, company_data):
    """Update company settings from backup manifest (non-destructive)."""
    if not company_data:
        return

    update_fields = []
    for attr in ("name", "name_ar", "default_currency", "functional_currency",
                 "fiscal_year_start_month"):
        if attr in company_data and company_data[attr]:
            setattr(company, attr, company_data[attr])
            update_fields.append(attr)

    if update_fields:
        from projections.write_guards import bootstrap_writes_allowed
        with bootstrap_writes_allowed():
            company.save(update_fields=update_fields)
