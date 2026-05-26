# reconciliation/views.py
"""Reconciliation API views.

A86.1 (2026-05-26): empty scaffold. URL prefix /api/reconciliation/
is registered but no endpoints land here yet — the existing surface
remains under /api/accounting/bank-statements/*. A86.8 migrates the
operator-facing endpoints (auto-match, preview, manual-match, unmatch,
exclude, resolve-difference, /needs-review, /period-overrides) here.

For A86.1, this module exists so:
- The bounded context's URL namespace can be resolved (test asserts this)
- Future routes have an obvious home
- The import-cleanliness gate is exercisable (the test imports views.py
  and proves no circular-import landmines exist between reconciliation
  and accounting/bank_connector)

No endpoints are exposed in A86.1 — `urlpatterns = []`.
"""
