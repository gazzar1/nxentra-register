# tests/test_backup_registry_completeness.py
"""
A161 — the backup registry must cover every company-scoped model.

Before this test existed, get_export_registry() silently omitted ~30
company-FK models (ReconciliationLink, PlatformSettlement/ProviderPayout,
JournalLineAnalysis, FifoLayer, credit notes, purchase orders, the
projection idempotency ledger, ...). Omission is a double hazard:
- the data is missing from every backup, AND
- _clear_company_data iterates the SAME registry, so on restore the
  omitted tables keep STALE rows pointing at deleted events/JEs.

Every concrete model with a Company FK must be either in the registry or
in the documented BACKUP_EXEMPT set — adding a new company-scoped model
without deciding fails CI.
"""

from django.apps import apps
from django.db import models

from backups.model_registry import BACKUP_EXEMPT, get_export_registry

THIRD_PARTY_APPS = {"admin", "auth", "contenttypes", "sessions", "staticfiles", "messages"}


def _has_company_fk(model_cls) -> bool:
    for field in model_cls._meta.get_fields():
        if isinstance(field, models.ForeignKey | models.OneToOneField):
            if field.related_model and field.related_model.__name__ == "Company":
                return True
    return False


def _company_scoped_models():
    for model_cls in apps.get_models():
        if model_cls._meta.abstract or model_cls._meta.proxy:
            continue
        if model_cls._meta.app_label in THIRD_PARTY_APPS:
            continue
        if _has_company_fk(model_cls):
            yield f"{model_cls._meta.app_label}.{model_cls.__name__}", model_cls


def test_every_company_scoped_model_is_registered_or_exempt():
    registry = get_export_registry()
    covered = set(registry.keys()) | BACKUP_EXEMPT

    missing = sorted(label for label, _ in _company_scoped_models() if label not in covered)
    assert not missing, (
        "Company-scoped models missing from the backup registry — add them to "
        "get_export_registry() in dependency order, or document the exemption "
        "in BACKUP_EXEMPT with a reason:\n  " + "\n  ".join(missing)
    )


def test_exempt_and_registry_are_disjoint():
    registry = get_export_registry()
    overlap = set(registry.keys()) & BACKUP_EXEMPT
    assert not overlap, f"Models both registered and exempt: {sorted(overlap)}"


def test_every_registry_label_resolves():
    """A renamed/deleted model must break this test, not the exporter."""
    for label, model_cls in get_export_registry().items():
        app_label, model_name = label.split(".")
        resolved = apps.get_model(app_label, model_name)
        assert resolved is model_cls, f"{label} does not resolve to the registered class"


def test_exempt_labels_resolve():
    for label in BACKUP_EXEMPT:
        app_label, model_name = label.split(".")
        assert apps.get_model(app_label, model_name) is not None, f"BACKUP_EXEMPT entry {label} does not resolve"
