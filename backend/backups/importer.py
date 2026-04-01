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
    from backups.model_registry import EXCLUDED_FIELDS, get_export_registry

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
    # Build set of model classes in registry for FK target detection
    registry_models = set(registry.values())
    stats = {"imported": {}, "skipped": {}, "cleared": 0, "errors": []}

    with rls_bypass():
        from projections.write_barrier import bootstrap_writes_allowed, projection_writes_allowed

        with bootstrap_writes_allowed(), projection_writes_allowed():
            # Wrap BOTH clear and import in a single transaction so that
            # if import fails, the clear is rolled back and no data is lost.
            with transaction.atomic():
                # Phase 1: Clear existing company data (raw SQL, reverse order)
                cleared = _clear_company_data(company, registry)
                stats["cleared"] = cleared

                # Phase 2: Import data in dependency order
                pk_map = {}
                # Track deferred FK updates: list of (model_cls, obj_pk, field_attname, old_fk_value)
                deferred_fks = []

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
                    count, model_deferred = _import_model_records(
                        model_cls, company, records, pk_map, label, excluded,
                        registry_models,
                    )
                    deferred_fks.extend(model_deferred)
                    stats["imported"][label] = count
                    logger.info("Imported %d records for %s", count, label)

                # Phase 3: Fix up deferred nullable FKs (self-references, etc.)
                if deferred_fks:
                    _apply_deferred_fks(deferred_fks, pk_map)
                    logger.info("Applied %d deferred FK updates", len(deferred_fks))

    zf.close()

    elapsed = (timezone.now() - started_at).total_seconds()
    stats["duration_seconds"] = round(elapsed, 2)
    stats["company"] = company.slug

    # Update company settings from manifest
    _update_company_settings(company, manifest.get("company", {}))

    return stats


def _clear_company_data(company, registry):
    """
    Delete all existing company data using raw SQL.

    Triggers are already disabled by the caller, so FK constraints
    won't block deletion. Uses raw SQL to bypass any custom model
    delete() overrides (e.g., BusinessEvent immutability).
    """
    from django.db import connection

    from events.models import EventPayload

    total_deleted = 0

    # Delete in reverse registry order (children before parents)
    models_list = list(registry.items())
    models_list.reverse()

    with connection.cursor() as cursor:
        for _label, model_cls in models_list:
            # Skip EventPayload (content-addressed, shared across companies)
            if model_cls is EventPayload:
                continue

            # Find the company FK column name
            company_col = _get_company_column(model_cls)
            if not company_col:
                continue

            table = model_cls._meta.db_table
            cursor.execute(
                f'DELETE FROM "{table}" WHERE "{company_col}" = %s',
                [company.id],
            )
            count = cursor.rowcount
            if count > 0:
                total_deleted += count
                logger.info("Cleared %d rows from %s", count, table)

    return total_deleted


def _get_company_column(model_cls):
    """Get the database column name for the company FK."""
    for field in model_cls._meta.get_fields():
        if isinstance(field, (models.ForeignKey, models.OneToOneField)):
            if field.related_model and field.related_model.__name__ == "Company":
                return field.column
    return None


def _import_model_records(model_cls, company, records, pk_map, label, excluded_fields,
                          registry_models):
    """
    Import a list of serialized records into the database.

    Handles:
    - PK remapping (old PKs → new auto-generated PKs)
    - FK remapping using pk_map
    - Company field assignment
    - Skipping excluded fields
    - FKs to models outside the registry (e.g., User) are kept as-is
      if the target row exists, or set to NULL if nullable

    Returns:
        tuple: (count, deferred_fks) where deferred_fks is a list of
        (model_cls, new_pk, field_attname, old_fk_value, label) for FKs
        that couldn't be resolved yet (set to NULL temporarily).
    """
    from events.models import BusinessEvent, CompanyEventCounter, EventPayload

    imported = 0
    old_to_new = {}
    deferred_fks = []

    for record in records:
        old_pk = record.get("id") or record.get("pk")

        # Prepare field values
        field_values = {}
        record_deferred = []  # FKs to fix up later for this record

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

                # FK to a model outside the registry (e.g., User)?
                if value is not None and field.related_model not in registry_models:
                    # Keep raw value if target row exists, else NULL
                    if _row_exists(field.related_model, value):
                        field_values[field.attname] = value
                    elif field.null:
                        field_values[field.attname] = None
                    else:
                        field_values[field.attname] = value
                    continue

                # Remap FK to already-imported records
                if value is not None:
                    remapped = None
                    related_label = _find_label_for_model(field.related_model, pk_map)
                    if related_label and related_label in pk_map:
                        remapped = pk_map[related_label].get(value)
                    # Self-referential FKs (e.g., caused_by_event)
                    if remapped is None and field.related_model == model_cls and label in pk_map:
                        remapped = pk_map[label].get(value)

                    if remapped is not None:
                        field_values[field.attname] = remapped
                        continue
                    elif field.null:
                        # FK target not found yet — set to NULL now, fix later
                        field_values[field.attname] = None
                        record_deferred.append((field.attname, value, label))
                        continue
                    else:
                        # Non-nullable FK — pass raw value through
                        field_values[field.attname] = value
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
            obj = BusinessEvent(**field_values)
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
        pk_field_name = model_cls._meta.pk.name
        field_values.pop(pk_field_name, None)
        field_values.pop("id", None)

        # Use models.Model.save() to bypass custom save() overrides
        obj = model_cls(**field_values)
        models.Model.save(obj)
        old_to_new[old_pk] = obj.pk
        imported += 1

        # Track deferred FKs with the NEW pk
        for attname, old_fk_value, fk_label in record_deferred:
            deferred_fks.append((model_cls, obj.pk, attname, old_fk_value, fk_label))

    pk_map[label] = old_to_new
    return imported, deferred_fks


def _apply_deferred_fks(deferred_fks, pk_map):
    """
    Fix up nullable FK fields that were set to NULL during import
    because the target record hadn't been imported yet.

    Uses raw SQL UPDATE to bypass custom save() overrides.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        for model_cls, obj_pk, attname, old_fk_value, _fk_label in deferred_fks:
            # Find the new PK for the old FK value
            new_fk = None
            for _map_label, mapping in pk_map.items():
                if old_fk_value in mapping:
                    new_fk = mapping[old_fk_value]
                    break

            if new_fk is None:
                continue

            table = model_cls._meta.db_table
            pk_col = model_cls._meta.pk.column
            cursor.execute(
                f'UPDATE "{table}" SET "{attname}" = %s WHERE "{pk_col}" = %s',
                [new_fk, obj_pk],
            )


def _row_exists(model_cls, pk_value):
    """Check if a row with the given PK exists in the database."""
    try:
        return model_cls.objects.filter(pk=pk_value).exists()
    except Exception:
        return False


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
        if company_data.get(attr):
            setattr(company, attr, company_data[attr])
            update_fields.append(attr)

    if update_fields:
        from projections.write_barrier import bootstrap_writes_allowed
        with bootstrap_writes_allowed():
            company.save(update_fields=update_fields)
