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
| **A3** — one central posted-JE invariant at emit + apply | **Open — emit payload integrity enforced/in review (A3-PR2); A3-PR2b + A3-PR3 remain open** | Canonical invariant core + read-only corpus scanner merged (A3-PR1). A3-PR2 (PR #113, in review) enforces the canonical invariant at every JOURNAL_ENTRY_POSTED emit boundary (10 emit paths + external ingest) via `prepare_posted_journal_for_emit()` — exact prepared-payload emission, emit-only strict two-decimal representation, authoritative memo flags, owning-operation rollback; the ±0.05 receipt/payment acceptance band is removed. **A3-PR2b (open, blocking): concurrent account-state serialization** — emit-time active/postable validation is snapshot-based, not serialized against concurrent account mutations (TOCTOU); requires lock-order discovery across sequence/account/aggregate/projection locks before any locking design. A3-PR2b must finish BEFORE A3-PR3, before deployment of the fresh pilot database, and before real merchant data enters Nxentra. **A3-PR3 apply/replay enforcement remains blocked** (production corpus scan or explicit compatibility decision, plus A3-PR2b). A3 is NOT complete. |
| **A5** — durable visible state for included money paths | **Open** | Not started. |
| **G1** — current-head fresh-company E2E (control totals + failure injection) | **Open** | The live operational proof for A1/A4. |
| **G2** — isolated-DB restore drill | **Open** | |

**Deployment reminder for A4:** the profile is set only via
`python manage.py activate_pilot_profile --company <id> --yes` (it refuses on any
forbidden state and never repairs data). Run
`python manage.py pilot_preflight --company <id> --phase go-live` before G1 and as
a drift check after each sync/import.

## Open architectural decisions

- **A3-PR2b — serialize account-state validation with posted-event emission (blocking).** Emit-time account facts are validated on a snapshot; account rows are not serialized against BusinessEvent insertion (reachable through external ingest and internal emitters). Required before A3-PR3, before the fresh pilot database deployment, before real merchant data, and before A3 is marked complete. Scope recorded in PR #113 (discovery: one canonical lock order across company-sequence, account-row, aggregate and projection-write locks; PostgreSQL concurrency tests; no partial locks beforehand).
- **Precision Foundation (future, pre-multi-currency).** The current two-decimal JournalLine schema and `Decimal("0.01")`/ROUND_HALF_EVEN posting quantum are the CONSTRAINED-PILOT (EGP-only) contract, not the permanent global policy. Before foreign-currency input is re-enabled, a non-EGP merchant is onboarded, multi-currency ships, or GA: decide ISO 4217 minor units, currency-aware quantization, money-field capacity/scale, quantity/unit-cost/exchange-rate/tax precision, rounding modes and accounts, calculation-vs-posting precision, display precision, and migration from the (18,2) schema. The controlled fresh-database reset may be the lowest-cost schema-widening point.
