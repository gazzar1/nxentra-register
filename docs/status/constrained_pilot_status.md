# Constrained-pilot gate — current status

Small living tracker for the `ISOLATED_SHADOW_LEDGER_V1` gate (A1–A5 + G1–G2)
defined in the two-contract readiness model
([docs/audits/2026-07-18-nxentra-current-state-audit.md](../audits/2026-07-18-nxentra-current-state-audit.md),
§21.2). This file records progress only; it does not restate or revise the audit.

Sequence: **A1+A2 → A4 → A3 → A5 → G1 → G2**.

| Item | Status | Notes |
|---|---|---|
| **A1** — cookie-JWT CSRF, four-mode auth matrix, explicit Shopify user binding | **Code-complete** at `8720887` (PR #106) | Operationally open until the live Shopify-iframe **G1** proof (App Bridge session tokens, `not_bound → link → login`, third-party cookies disabled). |
| **A2** — fail-closed production boot on unsafe test/bypass flags | **Complete** at `8720887` | Module-identity exemption; WSGI/ASGI/Celery assert the production settings module. |
| **A4** — constrained-pilot feature gates + Option B inventory invariant | **Complete** at `1e12250` (PR #107, merged 2026-07-27) | Central `accounts.pilot_policy` runtime gates (per-platform webhook map, disputes, interactive-payout 403 vs scheduled skip, EGP-only ingestion, single-user/currency/fiscal freeze, `is_active` freeze + CI architecture rule + admin hardening); `activate_pilot_profile` (transactional, audited, fail-closed) + exhaustive `pilot_preflight` commands — go-live enforces the FULL agreed workflow (proven-EGP store, exact OWNER↔store binding, postable clearing/EBD mappings, active Paymob/Bosta provider + posting profile, canonical bank account). Option B forces NON_STOCK with no inventory/COGS mappings, no residual balances, and no FX cost fetch. Live G1 still required before real data. |
| **A3** — one central posted-JE invariant at emit + apply | **Open** | Not started; do not begin in the A4 branch. |
| **A5** — durable visible state for included money paths | **Open** | Not started. |
| **G1** — current-head fresh-company E2E (control totals + failure injection) | **Open** | The live operational proof for A1/A4. |
| **G2** — isolated-DB restore drill | **Open** | |

**Deployment reminder for A4:** the profile is set only via
`python manage.py activate_pilot_profile --company <id> --yes` (it refuses on any
forbidden state and never repairs data). Run
`python manage.py pilot_preflight --company <id> --phase go-live` before G1 and as
a drift check after each sync/import.
