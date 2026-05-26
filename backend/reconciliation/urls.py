# reconciliation/urls.py
"""Reconciliation URL configuration.

A86.1 (2026-05-26): scaffold only — empty urlpatterns. URL prefix
/api/reconciliation/ is wired in nxentra_backend/urls.py so the
namespace is reachable, but no endpoints are exposed yet.

Operator-facing routes land starting in A86.8 when the auto-match
preview / execute / manual-match / unmatch / exclude views are moved
out of accounting/bank_views.py.
"""

app_name = "reconciliation"

urlpatterns: list = []
