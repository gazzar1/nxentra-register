# reconciliation/tests/test_a86_1_scaffold.py
"""A86.1 (2026-05-26): reconciliation bounded-context scaffold tests.

Asserts the empty package is wired correctly so future chunks
(A86.2 events, A86.3 projection, ...) have a clean starting point.

Acceptance criteria for A86.1:
- The `reconciliation` Django app is installed and resolvable.
- All module files import cleanly (no circular-import landmines).
- The URL namespace `reconciliation` is reachable (even with empty
  urlpatterns).
- No new models / migrations are needed yet (A86.3 adds the shadow
  fields).
"""

from django.apps import apps
from django.conf import settings


def test_reconciliation_app_is_installed():
    """The app is in INSTALLED_APPS via its AppConfig."""
    assert "reconciliation" in [c.label for c in apps.get_app_configs()], (
        "reconciliation app config not registered. Did INSTALLED_APPS get the "
        "reconciliation.apps.ReconciliationConfig entry?"
    )

    cfg = apps.get_app_config("reconciliation")
    assert cfg.name == "reconciliation"
    assert cfg.verbose_name == "Reconciliation (bounded context)"


def test_all_scaffolded_modules_import_cleanly():
    """Every module in the scaffold is importable. Catches circular-import
    surprises now — before A86.2/A86.3 start adding cross-module deps."""
    # Star imports kept as direct module imports so an import-time error
    # (e.g., bad future-feature module reference) surfaces here.
    from reconciliation import (  # noqa: F401
        apps,
        commands,
        event_types,
        exceptions,
        matching,
        policies,
        projections,
        urls,
        views,
    )


def test_reconciliation_url_namespace_resolves():
    """The /api/reconciliation/ namespace is wired even though no
    endpoints are exposed yet. A86.8 starts adding routes here; we want
    this gate to fail loudly if someone deletes the include() in
    nxentra_backend/urls.py."""
    from django.urls import get_resolver

    resolver = get_resolver()
    namespaces = list(resolver.namespace_dict.keys())
    assert "reconciliation" in namespaces, (
        f"reconciliation URL namespace not registered. Found: {sorted(namespaces)}. "
        "Check that nxentra_backend/urls.py includes "
        "path('api/reconciliation/', include('reconciliation.urls'))."
    )


def test_reconciliation_settings_consistency():
    """Sanity check: the app's AppConfig name matches what's in
    INSTALLED_APPS so the verbose registration is consistent."""
    installed = [item.split(".apps.")[0] if ".apps." in item else item for item in settings.INSTALLED_APPS]
    assert "reconciliation" in installed, (
        "reconciliation not in INSTALLED_APPS. The app needs to be "
        "registered as 'reconciliation.apps.ReconciliationConfig'."
    )
