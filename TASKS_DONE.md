# Completed Tasks

Archive of completed items moved from NEXT_TASKS.md. See NEXT_TASKS.md for pending work.

> **Format note (2026-07-11).** Existing entries below keep their full historical closeout text — they are the archive and the diligence evidence; do not compress them retroactively. **New closeouts from here on use one line each**: `ID — date — classification (shipped / superseded / refuted) — one-sentence outcome — commit/PR — link to detail if any`. Detail beyond one line goes in the PR description or an archive doc, not here.

## 2026-07-12 — Safe-supervised-pilot exit gate (P0 batch, dual audit 2026-07-11)

- A164 — 2026-07-11 — shipped — reconciliation/tests/ + Postgres concurrency class now run in CI; -x dropped; test_db.sqlite3 untracked — PR #60
- A156 — 2026-07-11 — shipped — is_postable FieldError fixed at 7 sites (+ revaluation ActorContext TypeError the FieldError masked); core-mapping auto-init works for the first time — PR #61
- A154 (+A115) — 2026-07-11 — shipped — one canonical drain-to-zero rebuild across CLI/HTTP/tenant paths; rebuild-then-process is byte-identical; JE read model clears on rebuild — PR #62
- A155 — 2026-07-11 — shipped — canonical counterparty-preserving reversal core; all four voids complete atomically (raise-inside-atomic, owner decision); orphan-DRAFT detector in System Health — PR #63
- A157 — 2026-07-11 — shipped — settlement imbalance/zero-gross + all platform mapping gaps now raise (TerminalSkip/StateError per F27); refunds branch had been a bare return — PR #64
- A158 — 2026-07-11 — shipped — legacy /banking matcher reuses the canonical Stripe settlement JE (guard-now owner decision); pending-window + event-less paths pinned — PR #65 (+#67 lint hotfix)
- A176 — 2026-07-11 — shipped — balance sheet folds current-year earnings into equity (both modes); period mode is true as-of cumulative — PR #66
- A177 — 2026-07-11 — shipped — JE idempotency via caller request_id (aggregate-scoped keys otherwise); 4 collision classes + false-failure eliminated; 5 dedupe-reliant callers migrated — PR #68
- A180 — 2026-07-11 — shipped — resolve_difference atomic + event-carried (ReconciliationDifferenceResolved); rebuild reproduces resolved state; A99b site absorbed (3→2) — PR #69 (+#71 pin hotfix)
- A159 — 2026-07-11 — shipped — refund backfill (per-order GraphQL query + updated_at catch-up + first-seen-refunded booking); webhook 503s on retryable failures — PR #70
- A160+A161 — 2026-07-12 — shipped — backups.* permissions (restore SENSITIVE even for OWNER) + fail-closed verified restore (hash/counts/identity pre-flight, in-transaction invariants) + 30 missing models registered — PR #72
- A162 — 2026-07-12 — shipped — DEBUG defaults False; TESTING explicit (argv backdoor removed); PROJECTIONS_SYNC asserted at boot; .env.example rewritten (contract-pinned) — PR #73
- A163 — 2026-07-12 — shipped — /_health/alerts (503 on failures/lag/pauses, web-process) + alert_check command; ops/ configs marked NOT WIRED; drill runbook in OPS_PLAYBOOK — PR #74
- A124 — 2026-07-12 — shipped — GDPR export/redact/shop-redact jobs with evidence + completion events; immutable-event lawful-basis exception (owner decision) pinned by test — PR #75
- Docs — 2026-07-11 — shipped — dual-audit reports + rebuilt NEXT_TASKS/NEXT_SESSION_PROMPT committed — PR #59

New defects found during the batch, filed as P1 (A181-A185): auto-reversal helper payload mismatch; branch protection + ruff pin (two red-CI merges hotfixed same-session); pre-A180 resolution backfill; dormant-vertical fail-loud; /_health/full slug leak.

## From: Phase A — First-user unblock + foundation hardening

### A1. Phase 1 dry-run on fresh Shopify dev store — ✅ **DONE 2026-04-28**
All 5 scenarios passed against `nxentra-test-code.myshopify.com`. 7 critical bugs found + fixed + regression-tested along the way: registration currency persistence, OAuth callback projection-guard violation, two null-customer crashes (handler + projection), two frontend status display gaps (badge + dashboard icon), and the load-bearing wizard finalization gap that would have left every first user without sales routing or webhooks. Commits `b6b52b9`, `5b550fb`, `b3417f3`, `cdd286e`, `7d9a852`, `d85ed48`, `7d12432`. Five UX/invariant follow-ups identified as A6-A10 (below).

See [SESSION_LOG.md § Session: April 26-28, 2026](SESSION_LOG.md) for the full play-by-play. **First user can be invited.**

### A2. PaymentGateway mapping (tactical slice) — ✅ **DONE 2026-04-30**
Shipped Shape B: `PaymentGateway(company, external_system, source_code, normalized_code, display_name, posting_profile FK, is_active, needs_review)`. Clearing account is derived (`gateway.posting_profile.control_account`) — JE construction in `sales/commands.py` unchanged. Bootstrap on `_ensure_shopify_sales_setup` creates 7 default rows + 7 dedicated `PG-*` PostingProfiles (paymob/paypal/manual/shopify_payments/cash_on_delivery/bank_transfer/unknown), all initially anchored on the same SHOPIFY_CLEARING; merchant edits a single profile's `control_account` to split a gateway off. Unknown gateway codes lazy-create with `needs_review=True` (operator visibility via API filter + `list_review_payment_gateways` mgmt command, per [ENGINEERING_PROTOCOL.md](ENGINEERING_PROTOCOL.md) §2.4). Frontend: "Payment Gateway Routing" card on `/shopify/settings`. 19 new tests + 28 regression tests pass. Commit `d0dd0d2`.

External arch review (forwarded by user before coding) added the load-bearing refinements: `external_system` scoping, `normalized_code` for Shopify casing/spacing variance, `needs_review` flag for unknown gateways, and `accounting/` over `platform_connectors/` as the home (connectors detect facts; accounting decides meaning).

A2 deliberately does NOT migrate historical invoices to per-gateway clearing accounts — only routes future imports. If first user wants per-gateway re-posting of historical Shopify invoices, that's a separate corrective JE (out of scope).

### A8. Auto-fill GL accounts on Items created from Shopify imports — ✅ **DONE 2026-04-29**
Surfaced from A1: `_auto_create_item_from_line` was creating Items from Shopify SKUs but `_resolve_default_item_accounts` looked for accounts at the wrong codes (`1300`/`5100` instead of `13000`/`51000` that `_setup_shopify_accounts` actually creates), and the fallback `_ensure_inventory_accounts` used an invalid role string for ASSET accounts. Net result: every auto-created Item had Sales/Purchase/Inventory/COGS = None. Rewrote the resolver to read all four accounts from the company's shopify_connector ModuleAccountMapping (purchase defaults to inventory for stocked items). Deleted the broken fallback. Added two regression tests: defaults-on-create and preservation-on-update (proves merchant's manual GL account edits are never overwritten by future Shopify activity). Commits `71cb0d7`, `cd7f484`.

### A2.5. Rename PaymentGateway → SettlementProvider — ✅ **DONE 2026-04-30**
Shipped commit `caa1ab9`. `accounting/payment_gateway.py` → `settlement_provider.py`; new `provider_type` field; URL `/api/accounting/settlement-providers/`; bootstrap rows now paymob/paypal/shopify_payments/bosta/bank_transfer/manual/unknown; `cash_on_delivery` deactivated as a transitional row. Aljazeera5 backfilled via `backfill_settlement_providers --cod-provider bosta`.

(historical scope retained below for context):
Pre-A12 cleanup driven by the architectural review: the model now needs to cover Bosta, DHL, Aramex, bank transfer, and manual collection — calling that a "PaymentGateway" actively misleads. Rename now, while the model is one day old, before any of the dimension layer or reconciliation work hardens around the wrong name.

**Reconciliation pivots on "who holds or remits the money," not "how the customer paid."** Bosta-COD and DHL-COD are different reconciliation cases because different parties hold money on different schedules. The right primary identity is the *settlement provider*. `payment_method` (cash_on_delivery, card, wallet, bank_transfer) is preserved as a denormalized fact for analytics, but is not the reconciliation pivot.

**Scope (pure refactor — no behavior change, no schema beyond rename + new field):**
- Django `RenameModel` migration: `accounting_paymentgateway` → `accounting_settlementprovider`. Rename indexes + unique constraint with the new prefix.
- Add `provider_type` CharField with TextChoices: `gateway`, `courier`, `bank_transfer`, `manual`, `marketplace`. Default rows: paymob/paypal/shopify_payments → gateway; bosta → courier; bank_transfer → bank_transfer; manual / unknown → manual.
- Files: `accounting/payment_gateway.py` → `settlement_provider.py`; `payment_gateway_views.py` → `settlement_provider_views.py`; `tests/test_payment_gateway.py` → `test_settlement_provider.py`.
- URL: `/api/accounting/payment-gateways/` → `/api/accounting/settlement-providers/`.
- Management commands: `list_review_payment_gateways` → `list_review_settlement_providers`; `backfill_payment_gateways` → `backfill_settlement_providers`. Backfill gains `--cod-provider <code>` flag for explicit existing-merchant config.
- Bootstrap rows: replace `cash_on_delivery` *as a provider* with `bosta` (provider_type=courier) — `cash_on_delivery` lives on as a payment_method fact, not a provider identity. Default seven becomes: paymob, paypal, shopify_payments, bosta, bank_transfer, manual, unknown.
- Frontend: `services/payment-gateways.service.ts` → `services/settlement-providers.service.ts`; card title "Payment Gateway Routing" → "Settlement Provider Routing"; per-row icon driven by `provider_type` (card / truck / bank / pencil icons).
- Tests + migration check pass; Aljazeera5 backfilled via `backfill_settlement_providers --cod-provider bosta`.

**Why this is half a day, not 2:** the model is 1 day old; no production data yet beyond Aljazeera5; 19 tests rename mechanically; the rename has no data-shape change. Cost is bounded; the confusion-tax of keeping the wrong name forever is not.

### A12. Settlement-provider dimension layer (structural retrofit on A2.5) — ✅ **DONE 2026-05-01**
Shipped commits `86d62d2` (core) + `6a09473` (follow-ups: refund/payout/dispute JEs tag clearing, AccountDimensionRule REQUIRED on clearing, dimension_validation UUID/string bug fix). Wizard COD step renders with currency-driven default suggestion; `ShopifyStore.default_cod_settlement_provider` FK; projection routes COD orders through it with lazy-create-on-NULL safety net; `_resolve_settlement_provider` and `_build_provider_tags` helpers; `sales/commands.py` and `platform_connectors/commands.py` thread `control_line_analysis_tags` / `clearing_line_analysis_tags` kwargs through.

(historical scope retained below for context):
Strategic decision driven by reconciliation-product framing (see [SESSION_LOG.md](SESSION_LOG.md) Session April 30): instead of merchants splitting `SHOPIFY_CLEARING` into seven sibling GL accounts (Paymob Clearing, PayPal Clearing, Bosta Clearing, …), keep one clearing account and use an `AnalysisDimension` to distinguish *settlement providers*. Trial balance stays clean; reconciliation queries pivot on `(account, dimension_value)`; adding WooCommerce/Amazon/Noon later costs N dimension values, not N new accounts.

**Scope:**
- New `settlement_provider` AnalysisDimension per company (created during onboarding alongside default cost centers).
- AnalysisDimensionValue rows seeded for the seven default providers (paymob, paypal, shopify_payments, bosta, bank_transfer, manual, unknown). Bootstrapped by `_ensure_shopify_sales_setup` + `backfill_settlement_providers`.
- New FK `SettlementProvider.dimension_value` (PROTECT, populated by bootstrap; lazy-create path for unknown gateways also creates a matching dimension value).
- New FK on ShopifyStore: `default_cod_settlement_provider` (FK to SettlementProvider, nullable, PROTECT, related_name="+"). Set during onboarding via the new wizard step (below); not auto-defaulted to bosta — explicit selection only.
- Onboarding wizard: new mini-step after Shopify Setup, before Import Orders. *"How do you collect Cash on Delivery?"* — single-select radio: Bosta / Aramex / Mylerz / DHL / Other (specify) / We don't use COD. Smart suggestion driven by `company.default_currency` (EGP→Bosta, SAR→Mylerz, AED→Aramex) — pre-selected but visible and confirmable, never hidden. "Other" opens a text field that lazy-creates a SettlementProvider with provider_type=courier, needs_review=False (the merchant explicitly named it). Helper text: *"Using more than one courier? Pick the one you use most. Multi-courier routing ships in A15."*
- Provider resolution in projection (`_handle_order_paid`):
  - Prepaid methods (paymob/paypal/shopify_payments): provider = method (1:1 lookup against SettlementProvider).
  - `cash_on_delivery`: provider = `store.default_cod_settlement_provider`. If NULL → lazy-create a `pending_cod_setup` row with `needs_review=True`, post via fallback profile.
  - `bank_transfer`, `manual`: provider = same string.
  - Unknown method: lazy-create with `needs_review=True`, `provider_type=manual`.
- `payment_method` preserved on the order/event payload as a denormalized fact (already on `ShopifyOrder.gateway` — keep it; future settlement events carry it forward). Reconciliation pivots on settlement_provider; analytics pivots on payment_method.
- Projection injects `settlement_provider.dimension_value` into the clearing JE line's `analysis_tags`. Refund / settlement / payout JEs that touch the clearing account also tag (preserves the cross-stage reconciliation chain).
- `is_required_on_posting=True` on the clearing account specifically (verify per-account requirement is supported via `AccountAnalysisDefault`; if not, scope at the dimension level + add a save-time validator on clearing-account JE lines).
- Migration: additive only. No backfill of historical JE lines (deliberately — those orders are already settled and rerunning the projection is out of scope).
- Tests: dimension created on bootstrap, JE line carries the tag, COD order with default_cod_settlement_provider=Bosta tags as bosta, COD order with NULL default lazy-creates pending_cod_setup, manual JE on clearing account without tag rejects, currency-based suggestion in wizard pre-selects correctly.

**Why now:** A2.5 just renamed the model; the seven `PG-*` PostingProfiles preserve the *splitting* affordance for power users; the dimension is what makes the *default* path queryable for reconciliation. Both modes coexist — split-by-account works alongside split-by-dimension; reconciliation engine groups by `(account, dimension_value)` either way.

### A13. Reconciliation Control Center MVP — ✅ **DONE 2026-05-01**
Shipped commit `b24065b`. New page at `/finance/reconciliation` with three-stage layout (Sales→Clearing, Clearing→Settlement, Bank Match), per-provider drilldown with aging buckets and JE-line history, top-line totals tiles. Backing endpoints `GET /api/accounting/reconciliation/summary/` and `GET /api/accounting/reconciliation/drilldown/?provider_id=`. Sidebar entry under Finance.

(historical scope retained below for context):
The merchant-visible product spine. New page at `/finance/reconciliation` answering one painful question: **where is my money?**

**Scope:**
- Three top-level sections, one per stage of the truth-matching chain:
  1. **Sales → Clearing.** Per-provider clearing balances, aging buckets (0-7d / 7-30d / 30+d), unsettled-orders count.
  2. **Clearing → Settlement.** Per-provider expected vs settled vs deposited deltas. Empty until A14 is in place; renders with a "no settlement data" state until then.
  3. **Bank Match.** Matched vs unmatched bank deposits. Surfaces the existing bank-rec data here so the merchant doesn't have to context-switch.
- Each tile clickable → drilldown table per provider: Order # | Date | Shopify Paid | Gateway Settled | Bank Received | Diff | Status.
- Status derivation at query time (no new aggregate yet — pure projection over JournalLine + dimension):
  - `matched` if `(account, dim_value)` balance has zeroed
  - `expected` if balance > 0 AND age ≤ 7d
  - `unsettled` if balance > 0 AND age > 7d
  - `short_paid` / `over_paid` once A14's settlement events flow in
- Backing API: `GET /api/finance/reconciliation/summary/` and `GET /api/finance/reconciliation/drilldown/?provider=…`.
- Frontend: card-based, color-coded aging (green/yellow/red), drilldown modal or sub-page. Tile icons follow `provider_type` (gateway / courier / bank).
- Top-nav entry: "Reconciliation" or "Money" — visible at app root, not buried.

**Non-goals (deliberately out of scope):**
- The full `ReconciliationCase` aggregate from the long-term vision. That comes in Phase C, after MVP signal validates the framing. Building it now is over-engineering.
- AI explanation / suggested resolution. Phase E territory.
- Cross-company / cross-provider analytics. Single-company view first.

**Why now:** Validates the core product hypothesis with the first user before Phase B's 5-7 week canonical refactor. The data is already there (clearing balances, dimension tags from A12, existing bank-rec); MVP is a query + a screen. ~5 days. Real merchant signal beats another sprint of architecture.

### A14. Manual settlement CSV import + Expected Bank Deposit convention — ✅ **DONE 2026-05-01**
Shipped commit `238d0a9`. New `PAYMENT_SETTLEMENT_RECEIVED` event + `PaymentSettlementReceivedData` dataclass; Paymob + Bosta CSV parsers in `accounting/settlement_imports.py`; `import_settlement_csv` with idempotency key `payment.settlement.received:{provider}:{batch_id}`; `/finance/settlements/import` page with side-by-side uploaders; new accounts `EXPECTED_BANK_DEPOSIT` (11600) and `SALES_RETURNS` (41200); `PaymentSettlementProjection` posts the four-line settlement JE with provider dimension tag on clearing. Surfaced and fixed a latent A8 bug: `INVENTORY` role string was wrong (should be `INVENTORY_VALUE`).

### A14b. Bank-rec auto-match for settlements — ✅ **DONE 2026-05-01**
Shipped commit `3445bc0`. New `_settlement_prepass_match` in `auto_match_statement` runs before the generic GL-level matcher. Matches bank lines to PaymentSettlement JEs via batch-id-in-description (high confidence) or amount + date proximity (fallback); posts a clearance JE `DR Bank / CR Expected Bank Deposit` for the actual bank amount; stamps `source_module='payment_settlement_clearance'`; marks the original settlement JE's EBD line reconciled.

### A14c. Per-Shopify-order drilldown — ✅ **DONE 2026-05-01**
Shipped commit `3445bc0`. New endpoint `GET /api/accounting/reconciliation/orders/?provider_id=`: per-order rows with Shopify Paid / Settled Batch / Settled Amount / Bank Received / Status (`expected` / `settled` / `banked`). Frontend: per-provider drilldown gains an "Orders" tab (merchant-friendly) alongside the "JE Lines" tab (auditor view).

### A16. Reconciliation Difference Engine — ✅ **DONE 2026-05-01**
Shipped commits `ced05ad` + `63d8888` (max_length hotfix). Detects near-match bank deposits (within 2% / capped at 500 currency units) and routes them to a Needs Review queue with a reason picker (extra fee / bank charge / chargeback / write-off / rounding / other). Selecting a reason posts the adjustment JE that drains the EBD residual.

- New `BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE` + `DifferenceReason` TextChoices + difference fields (migration 0028, max_length widen 0029).
- `_settlement_prepass_match` updated with tolerance-based detection; clearance JE posts for actual bank amount, EBD residual stays open until categorized.
- New `resolve_difference(actor, bank_line_id, reason, notes)` posts adjustment JE (DR reason / CR EBD short paid; reverse for over paid), stamps `source_module='payment_settlement_difference'`, drains EBD line.
- New endpoint `PATCH /api/accounting/bank-statements/lines/<pk>/difference/`.
- Reconciliation summary now returns `narrative` ("Tell me the story" sentence) and `needs_review` queue with available reason options per row.
- Frontend: narrative banner at top of `/finance/reconciliation`; Needs Review card with per-row reason dropdown + notes input + Resolve button; "Needs review" tile in Stage 3.
- 15 new tests covering tolerance math, near-match detection, outside-tolerance non-match, short/over paid adjustment direction, rejection paths, narrative content, queue inclusion/exclusion. All passing.

### A17. Bank statement CSV idempotent re-import — ✅ **DONE 2026-05-01**
Surfaced by user question "if the merchant uploads April 1-15 then April 1-30, do duplicates appear?". Verified the gap: Paymob/Bosta CSV import was already idempotent at batch level (`payment.settlement.received:{provider}:{batch_id}`), but `import_bank_statement` had no overlap detection — every upload created fresh `BankStatementLine` rows, so partial-overlap re-upload silently doubled the bank-line count for the overlapping period. No financial damage (the `journal_line.reconciled` flag still protected JE lines from double-matching) but Stage 3 reconciliation counts were polluted.

Shipped option 2 (line-level hash dedup): new `dedup_hash` field on `BankStatementLine` (SHA-256 of `line_date | amount | reference | description`), backfilled for existing rows in migration 0030, indexed on `(company, dedup_hash)` for fast lookup. `import_bank_statement` now scans existing hashes for the `(company, account)` pair before inserting and skips duplicates — both cross-import (April 1-15 then April 1-30) and intra-file (same row appearing twice in one upload). Response payload returns `lines_skipped_duplicate` so the frontend can surface "Skipped X duplicate transactions" to the merchant. Scope: `(company, account)` — same row on a different bank account (e.g. internal transfer) still imports.

11 new tests in `tests/test_a17_bank_statement_dedup.py` covering: hash determinism + field sensitivity + whitespace normalization, single-import baseline, intra-file collapse, full-overlap re-upload, partial-overlap re-upload (the merchant's actual scenario), cross-account isolation, cross-company isolation, legitimately-different-on-same-day handling, statement-row created even on full-dup re-upload. All passing.

## From: Phase A continues — Tier-1 fix list before first-user invite

### A18. Frontend deploy hygiene + atomic-deploy script — **DONE** (commit `9b5191f`, 2026-05-03)
Shipped: `scripts/deploy-frontend.sh` + `docs/runbook-frontend-deploy.md`. Fail-fast end-to-end with `set -euo pipefail`, refuses root, wipes `.next/` before build, verifies `BUILD_ID` exists post-build, health-checks `127.0.0.1:3000` with 30s timeout, then compares served `buildId` against disk and exits non-zero on mismatch. Used for the dry-run deploy itself.

### A19. Bank-rec unmatch / exclude must reverse the clearance JE — **DONE** (commit `1fd3922`, 2026-05-03)
Shipped: `_reverse_match_side_effects` helper + `_clear_match_state` helper in `bank_reconciliation.py`. `unmatch_line` and `exclude_line` now reverse any clearance JE created by the prior match (detected by `source_module='payment_settlement_clearance'`) via `commands.reverse_journal_entry`, plus reverse the A16 difference adjustment JE if one was posted, plus flip the original settlement JE's EBD line back to `reconciled=False`. Pre-existing JEs (platform payouts, manually-posted entries) are left untouched — only the reconciled flag flips. 6 new tests in `test_a19_bank_rec_unmatch_reversal.py`. Verified end-to-end on Aljazeera8: unmatch on BNK-001 (PAYMOB-BATCH-APR30-A 2,520) posted `JE-35-000025` reversal of `JE-35-000021`, summary correctly flipped to `Matched: 3/7`. **Known limitation:** re-running auto_match on the same bank line after unmatch hits a pre-existing event-idempotency wall (clearance JE content identical → `emit_event` deduplicates). Merchant re-match path is via the A25 manual-match picker.

### A20. A14 refund-during-settlement: importer routes refund_or_chargeback to uncollected — **DONE** (commit `b510626`, 2026-05-03)
Shipped: `refund_or_chargeback` (plus `refund_amount`, `chargeback`, `chargeback_amount` aliases) added to `_PAYMOB_HEADER_ALIASES`. Per-row refund amounts add to the batch's `uncollected_amount`, which the projection posts as a `DR Sales Returns` line. Math reconciles (`gross == net + fees + uncollected`), JE posts cleanly. Per-row line_items carry both `refund` and `status="refunded"` for audit. 5 new tests. Verified end-to-end on Aljazeera8: MAY01-A imported with `gross 800 / fees 24 / net 276 / uncollected 500`, posted `JE-35-000015`.

### A21. A14 Bosta `returned_uncollected_amount` column reader — **DONE** (commit `d9030de`, 2026-05-03)
Shipped: `returned_uncollected_amount` (plus `returned_amount`, `uncollected_amount`, `uncollected` aliases) added to `_BOSTA_HEADER_ALIASES`. For status=returned rows, prefer the dedicated column when populated; fall back to `collected` for legacy CSVs. Per-row line_items now carry both `uncollected` and `status`. 4 new tests. Verified end-to-end on Aljazeera8: BST-701 imported with `gross 3,400 / net 2,050 / uncollected 1,200`, posted `JE-35-000019` with the correct `DR Sales Returns 1,200` line.

### A22. A14 settlement importer per-row provider routing — **DONE** (commit `39adba0`, 2026-05-03)
Shipped: parser reads per-row `gateway` column and groups by normalized gateway via `normalize_gateway_code()`. When a batch spans more than one gateway, parser emits a `provider_breakdown` list. `PaymentSettlementReceivedData` carries the new optional field. `PaymentSettlementProjection` resolves a SettlementProvider per breakdown entry and posts one CR clearing line per provider, each tagged with its dimension value. DR EBD/Fees/Returns aggregate as before so bank-rec auto-match still matches one bank deposit against one settlement JE. Lazy-create policy unchanged. 4 new tests. Verified end-to-end on Aljazeera8: MAY01-B (3,000 Paymob + 1,000 Paymob Accept) drained Paymob clearing 7,600 / Paymob Accept clearing to **0** ✓.

### A23. Refund handler projection race — **DONE** (commit `29c1672`, 2026-05-03)
Shipped: `_find_posted_shopify_invoice` helper retries up to 5 times with 100ms sleeps before returning None; `_handle_refund_created` calls it instead of the inline query. Credit-note idempotency on `(source, source_document_id)` already existed at `create_and_post_credit_note_for_platform`; A23 added a regression test. 6 new tests. **Production verification surfaced a real edge case:** the seed_test_csv_pack emitted refund events with **lower** `company_sequence` than the order events, so refunds processed first and exhausted the 500ms retry window before order_paid handlers ran (recovery: rewound bookmark + re-processed; both credit notes posted CN-000001 / CN-000002). Filed two follow-ups: **A40** (seed pack emit-order bug — test-pack only) and **A41** (deeper A23 fix: defer-on-exhaust instead of silent drop — production edge case).

### A24. Bank statement frontend column-mapper UI — **DONE** (shipped before 2026-05-26)
Two-step import flow on `/accounting/bank-reconciliation/import`: (1) upload CSV → `parseCSVHeaders` previews detected columns, (2) operator maps columns via `<CsvMappingDialog>` → `parseCSV` returns the parsed lines, (3) preview table → confirm → `createStatement`. Mapping persists per bank account in `localStorage` (`nxentra:bank-import-mapping:<account_id>`). Audit-confirmed 2026-05-26 during A24+A25+A26 frontend pass; no further work required.

### A25. Manual-match picker filter — surface settlement EBD lines as candidates — **DONE 2026-05-26** (backend `cc343a6`, 2026-05-03; frontend this commit)
Backend (Codex/2026-05-03): `get_match_candidates_for_bank_line` helper + `GET /api/accounting/bank-statements/lines/<pk>/candidates/`. Returns union of same-account unreconciled lines AND un-reconciled EBD lines from `source_module='payment_settlement'` JEs, amount-proximity sorted, excluding REVERSED clearance JEs.

Frontend (this pass): added `MatchCandidate` type and `getMatchCandidates(bankLineId)` to `bank-reconciliation.service.ts`; swapped the manual-match panel in `frontend/pages/accounting/bank-reconciliation/[id].tsx` from `getUnreconciledLines(account_id, period_end)` to `getMatchCandidates(line_id)`. The picker now shows the candidate's account_code + name and tags EBD candidates with a small `EBD` chip so the operator can tell same-account vs settlement-side at a glance. BNK→A16-Resolve flow is now reachable from the UI.

### A26. Settlement-without-original-order rejection or warning — **DONE 2026-05-26** (backend `6347db1`, 2026-05-03; frontend this commit)
Backend (Codex/2026-05-03): `import_settlement_csv` cross-checks every `order_id` against `ShopifyOrder` per company; per-batch result carries `unknown_order_ids: list[str]` (also surfaced in the preview path).

Frontend (this pass): added `unknown_order_ids: string[]` to `SettlementImportBatch` in `settlement-imports.service.ts`. The `BatchResult` tile in `frontend/pages/finance/settlements/import.tsx` now renders a red "Needs review" badge alongside the "Imported"/"Already imported" badge when `unknown_order_ids` is non-empty, plus an inline panel listing the first 10 orphan IDs and a sentence explaining that the JE still posted but the orphaned portion will short-pay provider clearing until the missing orders are imported. The merchant now sees the signal on the same screen as the import result.

## From: Merchant-readiness — required before first paying merchant

### A44. GDPR mandatory compliance webhooks — **DONE 2026-05-10** (Shopify policy-mandatory, blocks app long-term)
Shopify requires every app — public *or* unlisted — to handle three GDPR webhooks: `customers/data_request`, `customers/redact`, `shop/redact`. Shopify pings them periodically with test payloads; an app that doesn't respond 200 gets disabled silently. **Shipped:** `GdprRequest` audit model ([models.py](backend/shopify_connector/models.py)) + migration `0013_add_gdpr_request.py` + three handlers in [commands.py](backend/shopify_connector/commands.py) (`process_customers_data_request`, `process_customers_redact`, `process_shop_redact`) + webhook router branch in [views.py](backend/shopify_connector/views.py#L170-L191) that bypasses the store-lookup (since `shop/redact` arrives 48h after uninstall and the store record may already be gone) + 5 tests in [tests/test_a44_gdpr_webhooks.py](backend/tests/test_a44_gdpr_webhooks.py) covering 200-on-valid-sig, 401-on-bad-sig, audit row written, idempotent retry, missing-store handling. Actual data work (export / deletion) intentionally left as `PENDING` status — Shopify only requires the 200 ack on the webhook itself; build the async jobs out when there's volume. **Manual step still required (operator):** configure the three webhook URLs in **Partners Dashboard → App setup → Compliance webhooks** (`https://app.nxentra.com/api/shopify/webhooks/` for all three; topic header is what Shopify sends). Until that's done, Shopify won't actually call our endpoints in production.

### A46. Webhook HMAC signature verification — **DONE** (verified 2026-05-10)
Verified at [backend/shopify_connector/views.py:144-153](backend/shopify_connector/views.py#L144-L153) (rejects 401 on missing/invalid signature *before* parsing the payload) and [backend/shopify_connector/commands.py:308-321](backend/shopify_connector/commands.py#L308-L321) (computes `hmac.new(SHOPIFY_API_SECRET, body, sha256)` and uses `hmac.compare_digest` for constant-time comparison). Pattern matches Shopify's prescribed verification exactly. No code change needed; closing as DONE per the doc's own predicted outcome.

## From: Shopify connector bugs surfaced 2026-05-15 during App Store reviewer-store setup

### A120. App Store rejection 2026-06-01 — REST API 2025-01 → 2026-04 + resilient sync handlers — **DONE 2026-06-02** (Shopify connector, App Store rejection ref 114779)
Shopify cut off REST API version `2025-01` between 2026-05-17 and 2026-06-01. Droplet logs confirmed all three endpoints (`products.json`, `orders.json`, `shopify_payments/payouts.json`) started returning 403 Forbidden against tokens with every required scope granted. Reviewer's screencast showed two red "Failed to sync" toasts and a misleading "Order re-sync complete: 0 new" success toast on the bare dev store `mec3xu-zd.myshopify.com`. Fix package: centralized `SHOPIFY_API_VERSION="2026-04"` constant + `_shopify_api_root()` helper, new `_shopify_access_denied()` classifier maps 401/402/403/404 → recoverable "unavailable" CommandResult, applied to `sync_products` / `sync_payouts` / `_sync_orders`. Frontend [settings.tsx](frontend/pages/shopify/settings.tsx) handlers now branch on `data.status` — neutral toast on "unavailable", destructive only on "error" or network failure. Also fixed [views.py:203](backend/shopify_connector/views.py#L203) `MultipleObjectsReturned` in `app/uninstalled` webhook fallback (`.get()` → `.filter().order_by("-created_at").first()`) — same shop_domain can exist across multiple companies when only the unique_active constraint applies. 8 regression tests in [test_a120_shopify_sync_403_resilience.py](backend/tests/test_a120_shopify_sync_403_resilience.py).

### A121. Migrate Shopify REST product reads → GraphQL Admin API — ✅ **DONE 2026-06-11, scope exceeded** (Shopify connector, follow-up to A120, surfaced 2026-06-02)
Shipped as the full GraphQL migration (commits `885dfbf`, `af4249a`, `0188ff5`, `bf7d9bb`, `718bf90`, `3533812`): ALL Admin API reads — products, variants, inventory costs, orders backfill, payouts, balance transactions, locations, shop currency — now go through the single `ShopifyAdminClient` in [graphql_client.py](backend/shopify_connector/graphql_client.py) (adapters return REST-shaped dicts). Live-validated 5/5 via `manage.py shopify_graphql_ping` against 2026-04. Required adding `read_shopify_payments_accounts` scope (GraphQL gates `shopifyPaymentsAccount` on it; REST only needed `_payouts`). Bonus: fixed the A52 zero-orders bug (REST silently dropped dev-store test orders; GraphQL returns them). Original scope text below kept for reference.
Shopify has been deprecating REST product endpoints for public apps since API 2024-04 — full removal is on a published timeline. `sync_products` (via `commands.py` `/products.json?limit=250`) and related variant/inventory item reads (`/variants/<id>.json`, `/inventory_items.json`) all read products via REST. After A120 these survive degradation gracefully (return "unavailable"), but the real fix is migrating to the Shopify Admin GraphQL API before REST products is fully removed and merchants with real catalogs lose product sync.

**Scope:**
- New `shopify_connector/graphql_client.py` wrapping `requests.post` against `/admin/api/<ver>/graphql.json` with the bulk-operation pattern for large catalogs (>250 products). Single rate-limit (cost) accounting.
- Replace product listing pagination loop in `sync_products` ([commands.py](backend/shopify_connector/commands.py)) with GraphQL `products` query + nested `variants(first: 100) { edges { node { ... inventoryItem { unitCost } } } }`. Eliminates the second-call `_fetch_inventory_item_costs` batch (cost comes inline in GraphQL).
- Replace single-variant fetch in `_fetch_variant_cost` with `productVariant(id:)` GraphQL query.
- Keep REST for orders / payouts / fulfillments for now — those endpoints are not on Shopify's REST sunset list yet.
- New `tests/test_a121_graphql_product_sync.py` covering: response-shape parity with REST output, bulk-operation handling for >250 products, retryable rate-limit errors (429-equivalent THROTTLED extension), error-shape parity (so `_shopify_access_denied` classifier from A120 still catches denied responses).

**Out of scope (separate ticket):**
- Migrating order webhooks to GraphQL subscriptions.
- Migrating settlement (`shopify_payments/payouts.json`) — Shopify Payments has its own REST timeline distinct from products.

**Trigger:** Ship before either (a) the first real merchant with >100 products connects, or (b) Shopify announces a specific products REST sunset date — whichever comes first.

### A125. Fulfillment backfill — historical orders never get COGS entries — ✅ **DONE 2026-06-19** (`1bad6c6`)
**✅ Shipped:** new cost-safe `ShopifyAdminClient.get_order_fulfillments` (dedicated per-order GraphQL, schema-verified against Shopify's reference) + `_backfill_order_fulfillments` wired into `_sync_orders` after each booked paid order (incl. already-booked → historical backfill). Best-effort by contract (a fulfillment failure can never roll back the committed order or break the batch) + idempotent via `process_fulfillment`. Tests: `test_a125_cogs_backfill.py`. *(Original scope below for context.)*

COGS books exclusively from `fulfillments/create` webhooks (subscribed since `nxentra-sync-8`, 2026-06-11) → [commands.py](backend/shopify_connector/commands.py) `process_fulfillment` → inventory issue + COGS JE. Webhooks only fire **going forward**, and `_sync_orders` (the order backfill / Re-sync button / onboarding historical import) fetches orders only. Consequence: any order fulfilled *before* the merchant installs Nxentra — or fulfilled while a webhook was missed — produces a SalesInvoice + revenue JE but **never a COGS entry**, permanently overstating margin for imported history.

**Scope:**
- Extend `ShopifyAdminClient.iter_orders` (or add a dedicated query) to include each order's `fulfillments { id, createdAt, fulfillmentLineItems { ... } }` — same GraphQL query, no extra scope needed (`read_fulfillments` already granted).
- In `_sync_orders`, after routing the order handler, feed each fulfillment through `process_fulfillment` (REST-shape adapter; handlers are already idempotent on fulfillment id).
- Mind inventory state: backfilled COGS issues stock — items with zero opening balance and strict negative-stock will land in `/finance/exceptions` (A80-style). Decide policy: book at `default_cost` regardless, or surface as exception (current behavior for live webhooks).
- Tests: order-with-fulfillment backfill creates exactly one COGS JE; re-run is a no-op; unfulfilled order creates none.

**Trigger:** before onboarding any merchant with pre-existing fulfilled order history (i.e., effectively every real merchant using historical import). Pairs with the post-launch `read_all_orders` request (full-history import beyond 60 days).

### A126. Historical order import beyond 60 days — `read_all_orders` — ✅ **DONE 2026-06-19** (`1bad6c6` + `shopify app deploy` → nxentra-sync-9)
**✅ Shipped:** `read_all_orders` added to all 3 scope sites (shopify.app.toml + settings.py SHOPIFY_SCOPES [the effective one] + commands.py); app deployed to Shopify (version nxentra-sync-9). Cap-lift is **scope-gated on `ShopifyStore.scopes`** — only a store that actually re-granted `read_all_orders` skips the 59-day clamp (so un-reconnected stores never 403); window split into monthly chunks (one idempotent task each), closed-period months skipped+logged (see A/C). Tests: `test_a126_historical_import.py`. **Activation pending: existing stores must reconnect** (managed install grants the scope from the declared config). Approval ticket 67968450 was already granted. *(Original scope below.)*

The `read_orders` scope only exposes the last 60 days of orders; that's why the onboarding historical import caps `created_at_min` at 59 days ([accounts/commands.py](backend/accounts/commands.py) `earliest_allowed`). Merchants switching accounting systems need their full fiscal year (or more).

**Scope:**
- Request the `read_all_orders` grant in Partner Dashboard → Nxentra Sync → API access → "Read all orders scope" → Request access (justification: accounting books require complete order history; mirrors the PCD reasons). Shopify reviews this separately — expect days-to-weeks.
- Once granted: add scope to `shopify.app.toml` + `SHOPIFY_SCOPES` (remember [settings.py](backend/nxentra_backend/settings.py) default shadows commands.py) + `shopify app deploy`; existing stores must reconnect.
- Lift the 59-day cap in `_enqueue_historical_import`; surface a "from date / full history" choice in the onboarding wizard and a Settings-page import flow. Large imports are long-running Celery jobs — chunk by month, report progress, stay idempotent.
- Depends on A125 (fulfillment backfill) — importing a year of orders without COGS makes the margin problem worse, not better.

**Trigger:** first merchant who asks to migrate a full fiscal year (i.e., any serious accounting migration).

### A127. Shopify-pulled Items: account defaults + selling price gaps (backend backfill + edit-page display bug) — ✅ **DONE 2026-06-20** (commit `6420226`)
Three distinct symptoms observed on Shopify_R, only partly fixed by `718bf90`:
1. **FIXED root cause** (`718bf90`): the `products/create` webhook auto-create branch passed 7 args to the 10-arg `_create_item_from_variant` (TypeError) and ignored the module account mapping — items created by that path got no accounts. Sync Products now backfills missing accounts/cost via `_update_item_defaults`.
2. **Still open — selling price**: `_update_item_defaults` backfills cost + the four GL accounts but NOT `default_unit_price`. Items created by older broken paths show Unit Price 0.00 (e.g. `Snowboard-Complete`, `Snowboard-Liquid` on Shopify_R). Add price backfill (only when current value is 0/empty — never clobber a merchant's manual price), and audit all three creation paths (sync, webhook, order-line auto-create) set `default_unit_price` from the variant price.
3. **Still open — frontend display bug**: [items/[id]/edit.tsx](frontend/pages/accounting/items/[id]/edit.tsx) shows Sales/Purchase/Inventory/COGS dropdowns as "None" even when the DB has them (verified 2026-06-11: item 55 `DEMO-MUG-001` has accounts 721/716/723 in DB; edit page displayed None; the Items LIST shows the correct Sales A/C). Form reads `item.sales_account?.toString()` against the serializer's int PK — likely the accounts options list loads after form init or a Select value-type mismatch (string vs number). A merchant who "fixes" the apparent None and saves could overwrite real accounts — worse than cosmetic.

### A128. CSV date-format sniffer can't disambiguate DD/MM vs MM/DD + import form uses locale-native date pickers — ✅ **DONE 2026-06-20** (commit `e799aae`)
Two related findings from screencast prep:
1. [CsvMappingDialog.tsx](frontend/components/common/CsvMappingDialog.tsx) `suggestMapping` sniffs `##/##/####` dates as `%d/%m/%Y` unconditionally. A US-format bank export (MM/DD) either errors on import (month > 12) or — worse — **silently transposes** day/month for the first 12 days of any month, putting wrong dates on bank lines. Fix: scan ALL sample rows for a value with first-component > 12 (proves DD/MM) or second-component > 12 (proves MM/DD); if every row is ambiguous, force the user to pick explicitly (no silent default). The dialog already has an editable date_format field — this is about the default + a confirmation nudge.
2. The bank-import form's Statement Date / Period fields use native `<input type="date">` (placeholder follows browser locale, e.g. mm/dd/yyyy) while the rest of the app uses the custom picker bound to `company.date_format` (e.g. JournalEntryForm). Data is safe (ISO submitted) but inconsistent UX the user noticed immediately. Swap to the shared picker.
Settlement imports (Paymob/Bosta) are stricter — ISO only via `fromisoformat` — consider the same explicit-format mapping there when a real provider exports non-ISO.

### A131. Shopify COGS path bypasses the item's negative-stock block — 🟡 **REVIEWED / DECIDED 2026-06-19 (no change)**
**🟡 Decision:** the Shopify fulfillment→COGS path *deliberately* forces `company.allow_negative_inventory = True` ([commands.py:2306-2320](backend/shopify_connector/commands.py)) — "merchants don't manage stock in Nxentra" — so COGS always posts for the (common) merchant who doesn't track inventory here. Blocking it would regress COGS for the majority. **Recommend leaving as-is**; the demo's −5 mug is fixed by the reseed (clean inventory), not by changing this guard. Could add a per-store "block negative" opt-in later for stock-tracking merchants if a real one asks. *(Original scope below.)*

On Shopify_R the Demo mug (`DEMO-MUG-001`) shows Quantity on Hand **−3** while the item is configured **"Allow Negative Stock: No (strict — block sales below stock)."** The Shopify fulfillment → COGS path posts COGS against inventory without enforcing the item's negative-stock guard, so stock goes negative despite the strict setting. Manual sales invoices respect the block; Shopify-imported COGS doesn't (parallels the A11 split where the Shopify and manual paths diverge). **Fix:** have the Shopify COGS booking honor `Item.allow_negative_stock` — for imported fulfillments, surface a visible "negative stock" exception rather than silently posting (a hard block on an already-shipped Shopify order is awkward; decide the intended behavior for imported fulfillments first). Correctness/visibility, not data-loss.

### A132. PRODUCTION: Shopify projection crashes on product title > 100 chars — DataError varchar(100) — ✅ **DONE 2026-06-19** (`4f446c8`, deployed + verified live)
**✅ Shipped:** dimension `name` capped to 100; dimension `code` (max 20) made collision-resistant via an idempotent uppercased-SHA1 hash suffix; routed all 11 dimension call sites through the helper. New `test_shopify_dimension_codes.py`. *(Original scope below.)*

`_resolve_dimensions` ([projections.py:445-454](backend/shopify_connector/projections.py)) tags the PRODUCT dimension via `_ensure_dimension_and_value(..., val_code=sku, val_name=title)`. `AnalysisDimensionValue.name` is `max_length=100` ([accounting/models.py:1888](backend/accounting/models.py)); real product titles routinely exceed 100 (e.g. "JBL Junior 470NC Blue Edition: Ultra-Immersive Wireless Noise-Cancelling Gaming & Media Headset for Young Gamers"). `get_or_create` → `save()` → `StringDataRightTruncation: value too long for character varying(100)` → the whole `shopify_accounting` event errors in `process_company_projections`, which can **stall the company's ordered projection stream** (books stop updating). Sentry `0a20824c`, 2026-06-18 16:08 EEST, store b74379.
**Immediate fix (safe, no migration):** cap the name in `_ensure_dimension_and_value` (`projections.py:285`): `"name": (val_name or "")[:100]`. Dedup key is `code`, so truncating the display name cannot cause collisions.
**Robustness follow-up:** widen `AnalysisDimensionValue.name` to 255 (match `AnalysisDimension.name`) so analytics keeps full titles, then cap `[:255]`. Audit the other dims in `_resolve_dimensions` (CATEGORY/VENDOR/CAMPAIGN/PROMOTION/CUST_SEGMENT) — several pass the same string as both `code` (max 20) and `name`; a value > 20 chars there hits a varchar(20) crash. Prefer widening/hashing `code` over truncating it (truncation risks collisions → wrong tagging).

### A133. PRODUCTION: Shopify fulfillment webhook 500s on null SKU — AttributeError NoneType.strip — ✅ **DONE 2026-06-19** (`4f446c8`, deployed + verified live)
**✅ Shipped:** `sku = str(li.get("sku") or "").strip()` (null SKU → existing no_sku branch); also hardened 3 identical `request.data.get(k,"").strip()` patterns in views.py. *(Original scope below.)*

`process_fulfillment` ([commands.py:1995](backend/shopify_connector/commands.py)) does `sku = li.get("sku", "").strip()`. When a fulfillment line item has `"sku": null` (key present, value None — common in real stores), `.get("sku", "")` returns None → `None.strip()` raises → the `fulfillments/create` webhook 500s → **no COGS booked** for that order, and Shopify retries the failing delivery. Sentry `5591687f`, 2026-06-18 16:22 EEST, store b74379.
**Fix (one line):** `sku = (li.get("sku") or "").strip()`. Null SKU then falls into the existing `if not sku:` no_sku branch (`commands.py:1998`) which records it as unmatched and continues — graceful, no crash.

Both PRODUCTION, both surfaced by the reviewer's real store data (long titles, null SKUs) — every real merchant will hit them. **Fix before "Make fully visible."**

### A134. Shopify projection hard-errors on stranded events when company has no ACTIVE store (or store missing default_customer/posting_profile) — ✅ **DONE 2026-06-19** (`1bad6c6`, deployed + verified live)
**✅ Shipped (all 5 sub-points + 2 sibling fixes):** new `_resolve_store_for_event` resolves by the event's **identifier** (`store_public_id` → `shop_domain`), **honored regardless of store status** (the identifier is truth; re-homing to a different active store is the A57 mis-attribution bug a background review caught as HIGH and is now fixed); `_ensure_store_setup` self-heals then raises only if STILL missing; gone/disconnected store → **bounded `DeferEvent`** (quiet) escalating to loud `ProjectionStateError` past 24h (no silent-forever-drop, no head-of-line stall); `shopify_health_check` flags every active store missing defaults. Refunds tag via `original_order.store`. Verified live: Shopify_R `projection_health` "All projections up to date" (order-1064 Sentry stall gone). Tests: `test_a134_store_resolution.py` (14). **Two siblings shipped alongside:** **A136** = `disconnect_store` no longer disconnects an arbitrary store for multi-store merchants (backend `1bad6c6` + frontend store-picker `f2512dc`); **C/A** = closed-period quarantine (`ProjectionTerminalSkip` framework primitive so a closed-period order advances instead of stalling) + import skips closed-period months — both `1bad6c6`, tests in `test_closed_period_quarantine.py`. *(Original scope below.)*

`_handle_order_paid` ([projections.py:742](backend/shopify_connector/projections.py)) raises `ProjectionStateError` "Shopify store missing Customer/PostingProfile" when `ShopifyStore.filter(company, status=ACTIVE).first()` is None OR the active store lacks `default_customer_id`/`default_posting_profile_id`. Surfaced on Shopify_R (Sentry `1133cfc0`, 2026-06-19 16:24 EEST) for b74379's **order 1064**: after the reviewer's b74379 store connected→disconnected and the demo store was reconnected/disconnected repeatedly during cast prep, Shopify_R has **no ACTIVE store** — so b74379's stranded order events re-error on every `process_all_projections` beat (recurring Sentry noise). `setup_shopify_module_routing --company-slug shopify-r` returns "No active Shopify stores found" — nothing to configure. The raise is correct by design (A80 loud-not-silent), but two upstream gaps: (1) OAuth connect can leave a store without default_customer/posting_profile when SHOPIFY_CLEARING didn't exist yet at callback time (ties [[A56]]/[[A57]]/[[A83]] store-record state); (2) events whose store later disconnects have **no resolution path** — they retry-and-error forever. **For Shopify_R now:** reseed (ties [[A130]]) or reconnect a store so the stranded events book. (`setup_shopify_module_routing` alone is a no-op here — confirmed "No active Shopify stores found"; it needs an ACTIVE store to configure.)

**General fix (ordered):**
1. **Resolve the store from the event payload** (`store_public_id` / `shop_domain`) instead of `ShopifyStore.filter(company, status=ACTIVE).first()`. The bare `.first()` (no ordering, no event match) silently picks the wrong row when a company has multiple/historical stores — same root flaw as [[A57]]. This is the highest-value change.
2. **Idempotent self-heal before raising:** if the resolved store lacks `default_customer`/`default_posting_profile`, call `_ensure_shopify_sales_setup(store)` once, refresh the row, and raise `ProjectionStateError` only if it's *still* missing. Keep loud-not-silent (A80) — never silently skip a financial event.
3. **Fail loud at connect/finalize** if `_ensure_shopify_sales_setup` can't create default_customer + default_posting_profile (e.g. `SHOPIFY_CLEARING` not yet mapped at OAuth-callback time) — so a store never reaches ACTIVE *without* its routing. This is the actual root cause (store went ACTIVE before setup completed).
4. For events whose store later **disconnects** with no active replacement, **defer** (don't re-error every `process_all_projections` beat) past a deadline, rather than emitting a Sentry alert on every tick.
5. Extend `shopify_health_check` (A98) to flag ACTIVE stores missing default_customer/posting_profile.

Prereq for the self-heal/setup path: the company's `shopify_connector` ModuleAccountMapping must carry `SHOPIFY_CLEARING`, `SALES_REVENUE`, `SALES_TAX_PAYABLE`, `CASH_BANK`, `PAYMENT_PROCESSING_FEES` — without `SHOPIFY_CLEARING` the PostingProfile can't be created. NOT a real-merchant blocker (clean single-connect never hits it) — demo-company artifact. (External Codex review 2026-06-19 contributed points 1–2 + 5; it mis-diagnosed the live instance as "active store missing defaults" when the actual branch was `not store` / no-active-store, so its `setup_shopify_module_routing` immediate fix no-op'd.)

### A135. Relevance-aware projection lag metric — `projection_health` reports phantom lag — ✅ **DONE 2026-06-19** (`1bad6c6`, deployed + verified live)
**✅ Shipped:** `get_projection_lag_metrics` now resolves each bookmark's projection via `projection_registry.get(consumer_name)` and counts only its `consumes` types within `(bookmark, latest]`; unknown/legacy consumers keep the coarse fallback; added a `relevance_aware` flag. Reporting-only. **Verified live:** server `projection_health` reads "All projections up to date" (was phantom hundreds-behind). Tests: `test_a135_relevance_aware_lag.py`. *(Original scope below — the "deeper cousin" routing-table perf win is still open, tracked as the coarse-orchestration Watch item.)*

`projection_health` / `get_projection_lag_metrics` report a projection as "N events behind" by counting **all** events after its bookmark, regardless of whether that projection handles those event types. Each projection's bookmark only advances when it processes a *relevant* event, so trailing events of other types inflate the lag indefinitely. Verified 2026-06-19: `projection_health` showed `gezzo` 9 behind on a dozen projections, but `run_projections --company gezzo` found **0 pending events** (none of the 9 were those projections' types). Droplet-wide this produced alarming phantom numbers (Shopify_R 487, demo 212, heba_dry 137, …) that **masked the one genuinely-stuck projection** — `shopify_accounting` on Shopify_R, blocked at order 1064 ([[A134]]).

**Why it matters:** the gauge cries wolf, so a real stall is indistinguishable from noise — and it blocks any trustworthy automated projection-lag alert (a constantly-firing alert gets muted, and the next real order-1064 slips through unseen).

**Fix (low-risk, reporting-only):** make `get_lag` / the lag metric count only events whose type is in the projection's handled-event-type set, within `(bookmark, latest]`. No processing-semantics change. After it, "behind" means real pending work and `projection_health` becomes alert-grade.

**Deeper cousin** (the existing *"projection orchestration is coarse-grained"* Watch item): advance each projection's bookmark past irrelevant events so the gap itself shrinks, plus an event-type→projection routing table so `process_all_projections` only runs projections with real work (kills the per-company × per-projection loop). The metric fix is the cheap high-value slice; the routing table is the bigger perf win.

**Priority:** low until automated projection monitoring is stood up (before real merchant volume) — then it's a prerequisite for a usable health signal.

## From: Post-listing engineering queue + governance / architectural review

### A82. Invoice list sort tie-breaker is inconsistent within the same posting date — ✅ **DONE 2026-05-28** (sales UI polish, surfaced 2026-05-24 during App Store demo data creation)

Shipped during 2026-05-28 screencast pre-flight. Two-file change:
1. `backend/nxentra_backend/pagination.py` — `paginate_queryset` now accepts a tuple/list for `default_ordering`; splats into `order_by(*ordering)` when so. Backward-compatible — string callers unchanged.
2. `backend/sales/views.py:436` (Sales Invoices list) — `default_ordering=("-invoice_date", "-invoice_number")`. Within the same date, invoices now sort by invoice number descending.
3. `backend/sales/views.py:942` (Credit Notes list) — same shape: `default_ordering=("-credit_note_date", "-credit_note_number")`.

Original scope below:

`/accounting/sales-invoices` sorts by `Date DESC` correctly, but the secondary sort within the same date is inconsistent. Reproduced 2026-05-24 on Shopify_R company with 5 demo invoices:
- 23/05/2026 entries: `INV-000007` shown above `INV-000006` (descending ✓)
- 22/05/2026 entries: `INV-000004` shown above `INV-000005` (ascending ✗)

Most likely cause: ordering ties broken by `posted_at` rather than `id` or `invoice_number`. INV-000005 was saved as DRAFT first and posted later than INV-000004, which inverts the visual order from what a user expects (highest invoice number on top within a date). **Fix:** secondary `ORDER BY id DESC` or `invoice_number DESC` in the list query so tie-break is monotonic with what the user sees. Single line in the queryset. Cosmetic — not data-correctness — but jarring once you notice. **Not submission-blocking.**

### A103. Registration must propagate `default_currency` → `functional_currency` — ✅ **ALREADY DONE 2026-04-26** (commit `b6b52b9`, re-diagnosed during 2026-05-27 Shopify_R screencast prep)

Re-diagnosed today and found to be already fixed. Commit `b6b52b9` (2026-04-26) "Fix registration: persist user-selected currency to both default and functional currency" did exactly this. The bug originally manifested in the opposite direction — Egyptian merchants picking EGP silently got USD/USD because the projection overwrote with the model default. The fix:
- `backend/accounts/views.py:174` reads `currency` first, falls back to `default_currency` (backward-compat)
- `register_signup` (`accounts/commands.py:187`) and `create_company` (`accounts/commands.py:488`) both persist `default_currency=X, functional_currency=X` on the Company row
- `CompanyCreatedData` carries both currencies; projection applies them (legacy events without `functional_currency` fall back to `default_currency` for replay safety)
- Two regression tests: `test_register_persists_currency_to_both_fields` + `test_register_view_honors_currency_request_key`

**Why Shopify_R still has the mismatch:** Shopify_R was created before commit `b6b52b9` shipped, so it carries the legacy USD/EGP state. New merchants registering via the App Store listing today will NOT hit this bug — they will have `default_currency == functional_currency` correctly persisted.

**Workaround applied to Shopify_R during 2026-05-27 session** (for the screencast specifically): added `ExchangeRate(USD→EGP, rate=1.0, effective_date=2026-01-01, source='Manual (demo seed workaround)')`. Marked the failure log row resolved manually after `run_projections` re-applied the pending refunds. Shopify_R remains a legacy mismatch case; no production merchant will reach this state.

**Optional follow-up:** a data migration could backfill any legacy company where `default_currency != functional_currency`. Deliberately not shipped because the mismatch is sometimes intentional (multi-currency businesses report in a different functional currency than transaction default). Leave to operator/admin tooling.

### A109. `PurchaseBill` has no FK to its journal entry — ✅ **RESOLVED-NOT-A-BUG 2026-05-28** (originally surfaced 2026-05-27 during Shopify_R deep-data investigation)

**Original ticket was a false alarm.** Re-checking on 2026-05-28: `PurchaseBill` (and `PurchaseCreditNote`) DO carry `posted_journal_entry = ForeignKey(JournalEntry, ...)` at `backend/purchases/models.py:427` and `:583`. The previous shell verification used `hasattr(bill, 'journal_entry_id')` which returned False because the FK is named `posted_journal_entry` (auto-creating `posted_journal_entry_id`, not `journal_entry_id`). My field-name assumption was wrong.

Closure: same 2026-05-28 commit that adds the JE-link column on Vendor Bills surfaces this FK on the serializer (`journal_entry_pk` + `journal_entry_number` via `source="posted_journal_entry_id"` / `source="posted_journal_entry.entry_number"`). The UI now shows clickable `BILL-* → JE-*` links. Same treatment applied to Credit Notes, Sales Invoices, and Vendor Payments.

Lesson logged: when checking FK existence, `hasattr` is brittle — names like `journal_entry_id` vs `posted_journal_entry_id` matter. Better verification: read the model class definition directly, or grep for `ForeignKey.*JournalEntry`.

### A102. GitHub Actions: make mypy spine + architecture tests blocking — **DONE 2026-05-26** (governance, surfaced by 2026-05-26 review #3)
Codex review #3 said *"CI still allows mypy to fail"* — turned out to be exactly right. `.github/workflows/ci.yml` already existed (Glob filters dotfiles by default, hid it from earlier audits during Track 2; I'd been operating under the wrong assumption that there was no CI at all). The full-codebase mypy step in `backend-lint` carried `continue-on-error: true` (line 206), making type-checking advisory.

A102 fixes that surgically:
- New step `mypy strict on canonical spine (blocking)` runs `python ../scripts/check-types.py` (the same script as the pre-push hook). 17 spine files MUST pass strict typing.
- New step `architecture rule tests (blocking)` runs the 5 AST-based tests from A101.
- The existing whole-codebase `mypy --config-file pyproject.toml .` step stays advisory under `continue-on-error: true` — gradual adoption on non-spine files is still the right posture, but the spine is now enforced.

Path note: the spine-mypy step uses `python ../scripts/check-types.py` because the `backend-lint` job's working-directory is `backend/`, and the wrapper script chdirs to backend/ internally before running mypy, so it works regardless of invocation cwd.

Other findings during A102:
- `security-check` job (line 248-251 of the workflow) was already running `manage.py makemigrations --check --dry-run` — so A88's pre-push migration check is duplicated by CI, not the sole gate. Belt-and-suspenders is the right answer here.
- The full E2E + invariants + Postgres test jobs already exist (`backend-invariants`, `backend-e2e`). The protocol-spine gates A102 adds are additive.

When this lands on main, the next push will exercise the new blocking steps in CI.

### A101. Executable architecture tests — **DONE 2026-05-26** (governance, surfaced by 2026-05-26 review #3)
Codex review #3 recommended making the architecture rules executable so a regression breaks the build. Shipped `backend/tests/test_architecture_rules.py` with 5 tests (4 rules + 1 meta):

1. **Rule 1** — `*/views.py` files must not call `projection_writes_allowed()`. Allowlist: `projections/views.py` (legitimate operator-triggered rebuild endpoint) + `accounting/views.py` (6 known sites tracked for A3 reactor cleanup). bank_connector cleaned via A100; this rule holds the line.
2. **Rule 2** — `*/projections.py` files must not call `emit_event*`. Allowlist: `shopify_connector/projections.py` + `clinic/projections.py` (the existing "projection vs reactor" blurs, pending A3 reactor extraction).
3. **Rule 3** — non-allowlisted files must not perform direct `JournalLine.reconciled = …` writes. Allowlist: `reconciliation/projections.py` (canonical writer), `accounting/models.py` (field def), `backfill_entry_numbers.py` (ops), `reconciliation/commands.py` (3 remaining sites at lines 518/1107/1771 deferred as A99b).
4. **Rule 4** — non-allowlisted files must not perform direct `BankStatementLine.difference_amount = …` writes. Allowlist: projection, model def, `reconciliation/commands.py` (A99b resolve_difference path).
5. **Meta** — each allowlist capped at 5 entries so the lists can't grow silently; new additions need a written justification.

Surfaced **three more direct `JL.reconciled` writes** in `reconciliation/commands.py` (lines 518, 1107, 1771) that A99 didn't catch — platform-payout prepass, generic-GL match, A16 resolve_difference. Logged as A99b on the post-listing punch list.

Verified: 5/5 architecture tests green. AST-based scans so renames are a one-line update.

### A100. Remove projection_writes_allowed() from bank_connector views — **DONE 2026-05-26** (governance, surfaced by 2026-05-26 review #3)
Codex review #3: `backend/bank_connector/views.py:568` (and `:611` in `ManualMatchView`) entered `projection_writes_allowed()` directly from a view. The engineering protocol forbids views from granting projection-write privileges — that should sit narrowly around the actual write inside a command/projection.

Root cause: `_create_payout_je` calls `platform_connectors.je_builder.build_journal_entry`, which uses `JournalEntry.objects.projection().create()` — a projection-chain write that requires `projection_writes_allowed()`. The view was the only path that had it open.

Fix: pushed the context entry from the two views into `bank_connector/matching.py:_reconcile_payout_je`, scoped to just the `_create_payout_je(...)` call. The views now only wrap with `transaction.atomic()`; they no longer grant projection-write privileges to anything they don't own. Documented in code that the eventual A3 reactor extraction replaces the in-line projection-chain write with a proper event-driven post, at which point the context manager goes away entirely.

Verified: 11/11 green across `tests/test_a86_6_bank_connector_emission.py` (7 incl. A89 capstone) + `tests/test_a99_reconciliation_event_first.py` (4).

### A99. Finish projection ownership of JournalLine.reconciled + A16 difference fields — **DONE 2026-05-26** (event-first hardening, surfaced by 2026-05-26 review #3)
Codex review #3 flagged `reconciliation/commands.py:660 + :1193 + :1302` as residual direct writes to `JournalLine.reconciled` and `BankStatementLine.difference_*` fields. The code self-admitted the gap in comments. A99 closes it.

Changes:
- **Event payload extended.** `ReconciliationMatchConfirmedData` gains `additional_journal_lines_to_reconcile: list` (for the settlement-prepass EBD line). `ReconciliationMatchUnmatchedData` gains `additional_journal_lines_to_unreconcile: list` (for the EBD line on reverse).
- **Projection extended.** `ReconciliationProjection._handle_match_confirmed` now also writes `bank_line.difference_amount`, `bank_line.difference_reason`, and flips `JournalLine.reconciled=True` for `matched_journal_line` + every `additional_journal_lines_to_reconcile`. `_handle_match_unmatched` clears `bank_line.difference_amount / difference_reason / difference_notes / difference_resolved_at / difference_adjustment_entry` and flips `JournalLine.reconciled=False` for `previously_matched_journal_line` + every `additional_journal_lines_to_unreconcile`.
- **Commands cleaned.** `auto_match_statement` no longer writes BSL.difference_* or JL.reconciled directly — passes `additional_journal_lines_to_reconcile=[ebd_line.public_id]` on exact match. `manual_match` no longer writes JL.reconciled directly. `_clear_match_state` is now a no-op (kept as a placeholder so the unmatch_line/exclude_line call shape is unchanged in this diff); both unmatch callers pass `additional_journal_lines_to_unreconcile=[settlement_ebd_line.public_id]` when present.
- **Latent bug fixed.** Pre-A99, `unmatch_line` cleared the BSL match fields but never reset `JournalLine.reconciled` on the previously-matched line — the JL carried a stale reconciled=True with no bank line pointing at it. Now both transitions flow through the projection so the invariant holds.

Tests:
- 4/4 A99 capstone tests in `tests/test_a99_reconciliation_event_first.py` — stub the projection, prove `manual_match` and `unmatch_line` produce no direct writes (matches the A89 pattern).
- 74/74 reconciliation regression sweep across `test_a86_3..._a86_7a`, `test_a19_bank_rec_unmatch_reversal`, `test_a16_difference_engine`, `test_a25_match_candidates` still green.
- mypy spine still clean.

**Deferred as A99b:** A16's `resolve_difference` command path still writes `difference_notes`, `difference_resolved_at`, `difference_adjustment_entry` directly. That path emits a `ReconciliationExceptionResolved` event whose projection handler is still a no-op (per the existing A86.3 comment). When the exception read model lands, fold those writes in.

### A98. shopify_accounting projection diagnostic — **DONE 2026-05-26** (operability, blocker for screencast)
Per `project_app_store_submission_paused.md`, on 2026-05-25 the screencast was blocked because `seed_test_csv_pack` created `ShopifyOrder` rows but the `shopify_accounting` projection produced no `SalesInvoice`/`JournalEntry` for them, and the cause was unclear.

Code-level reproduction: **the bug no longer exists in code.** All four E2E tests in `tests/test_shopify_pipeline_e2e.py` pass — including the exact `SHOPIFY_ORDER_PAID → SalesInvoice + posted JournalEntry` happy path that A78 originally broke. A80's loud-failure framework writes a `ProjectionFailureLog` row whenever a handler raises, which means the production failure on Shopify_R was either (a) an environmental gap (missing `ModuleAccountMapping` role, missing `store.default_customer`/`default_posting_profile`, order date in a CLOSED fiscal period) or (b) a `ProjectionFailureLog` row that nobody checked on `/finance/exceptions`.

Built `shopify_connector/management/commands/shopify_health_check.py` to surface every required piece of setup in one place:
- ACTIVE `ShopifyStore` + `default_customer` + `default_posting_profile`
- Required `ModuleAccountMapping` roles (`SALES_REVENUE`, `SHOPIFY_CLEARING`) + optional (`SHIPPING_REVENUE`, `SALES_TAX_PAYABLE`)
- Event queue counts (total / applied by `shopify_accounting` / pending) per shopify event type
- `ShopifyOrder` rows by status (RECEIVED vs PROCESSED)
- Fiscal-period coverage warnings for any RECEIVED orders outside an OPEN period
- Recent `ProjectionFailureLog` entries for `shopify_accounting`, last N days

Outputs human-readable text by default; `--json` for piping into monitoring.

Operator workflow on the droplet:
```
python manage.py shopify_health_check --company-slug shopify-r
```
prints a checklist with `[OK]` / `[FAIL]` / `[WARN]` markers and a "Found N blocker(s)" summary. Run it on Shopify_R, fix what it flags (likely missing posting profile or role mapping per the 2026-05-25 memory), re-run `seed_test_csv_pack --flush`, then record the screencast.

### A97. Mypy blocking on canonical spine — **DONE 2026-05-26** (governance, surfaced by 2026-05-26 architectural review)
Codex flagged that mypy was `continue-on-error: true` for finance-critical modules — type-checking was advisory only. There is no CI to host that flag (no `.github/workflows/`), so the practical "blocking" enforcement attaches to the existing pre-push pre-commit hook chain.

`backend/pyproject.toml` already declared strict per-module overrides (`check_untyped_defs = true`, `warn_return_any = true`) on the canonical spine. The work was to (a) clean up the spine files so they pass strictly, (b) wire a pre-push hook that enforces it.

Cleaned up 22 errors across 6 spine files:
- `events/types.py` — `to_dict` result dict needed `dict[str, Any]` annotation (2 errors)
- `events/models.py` — `cast(dict, ...)` for JSONField return paths + guard against `payload_ref is None` + Optional default on `event_types` (5 errors)
- `projections/base.py` — declared `_projections` at class level on `ProjectionRegistry` singleton + `cast(int, ...)` on QuerySet.count return (9 errors)
- `accounts/middleware.py` — annotated `_tenant_cache` and made `company_id` properly Optional (2 errors)
- `accounting/models.py` — moved `# type: ignore[misc]` from the unused override slot onto the actual `super()._clone()` call where django-stubs lacks the method + `# type: ignore[import-untyped]` for the `requests` import (2 errors)
- `accounting/behaviors.py` — `cast(dict, ...)` for `Account.VALID_ROLES_BY_TYPE.get(str_key)` where TextChoices keys vs str arg confused overload resolution (2 errors)

Result: 17 spine files pass mypy strict cleanly. New pre-push hook `mypy-spine` in `.pre-commit-config.yaml` runs `scripts/check-types.py` (cross-platform Python wrapper; `.sh` + `.ps1` shells delegate to it). `--follow-imports=silent` so transitively-imported files get type-inferred without their existing errors blocking the gate.

**Deferred as A98**: `backend/accounting/commands.py` (150 errors) and `backend/sales/commands.py` (85 errors). These are the noisiest spine files — both `commands.py` files that need a focused cleanup pass before they can join the gate. Splitting them out kept A97's scope honest while still locking down 17 critical files.

33/33 regression tests green across A86.6 + A87 + write_barrier + A90 after the source fixes. Hook verified: `mypy strict on canonical spine......Passed`.

### A95. Write barrier: threading.local → contextvars.ContextVar — **DONE 2026-05-26** (architecture, surfaced by 2026-05-26 architectural review)
Codex flagged `backend/projections/write_barrier.py:8` for using `threading.local()` to back the write-context stack. The doc surface says async-safe; the primitive was not. For plain sync Django the two are equivalent — but every future async surface (Channels consumer, async management command, AI/agent worker) would silently lose context across `await` boundaries, causing finance writes inside async tasks to either be spuriously blocked or sneak through one barrier and trip another mid-transaction.

Swapped to `contextvars.ContextVar[tuple[str, ...]]` with an empty-tuple default. The public API is identical (`current_write_context`, `write_context_allowed`, and the six `*_writes_allowed()` context managers), so all 50+ call sites are untouched. Stack is stored as an immutable tuple — mutating a shared list would leak across asyncio task boundaries because ContextVar wraps the value, not a fresh copy.

New regression suite at `tests/test_a90_write_barrier_contextvars.py` (12 tests):
- sync stack discipline (push/pop, nesting, exception cleanup, LIFO)
- `write_context_allowed` membership semantics
- `admin_emergency` setting gate
- thread isolation (two threads see separate stacks — floor we had under `threading.local`)
- **`asyncio.create_task` inherits the parent's stack** — the contract the swap was for
- **task mutations don't leak back to the parent** — isolation contract
- **concurrent tasks under the same parent context keep independent sub-stacks**
- ContextVar default is the empty tuple (pinned, because flipping it to `None` would TypeError on subscript)

Regression sweep: 55/55 green across A86.3–A86.7a + bank_connector + A87 + the new + the original `test_write_barrier.py`. Existing callers see no behavior change today; the architecture is now ready for an async finance surface without rewriting the barrier.

### A94. bank_connector reconciled-flag audit — **DONE 2026-05-26** (correctness, surfaced by 2026-05-26 architectural review)
Codex flagged `bank_connector/matching.py:288` as a residual direct mutation of `journal_line.reconciled` under `projection_writes_allowed()` context. **The finding is stale** — A86.7b (commit `5d73387`) already removed the direct flip; `_reconcile_payout_je` now emits a `ReconciliationMatchConfirmed` event with `confirmation_kind="platform_payout_reconcile"` and runs the projection synchronously, with the projection as the sole writer of `JournalLine.reconciled`.

Audit findings:
- **Zero** direct `.reconciled = True/False` writes anywhere in `backend/bank_connector/` (grep-verified).
- A86.6 has 6 tests pinning the emission + projection-write contract — all green.
- `BankTransaction.status` mutations remain direct, by design: `BankTransaction` is a connector-owned canonical model (no `ProjectionWriteManager`, no write-barrier check). Not a protocol violation.
- `payout_obj.journal_entry_id` mutation in `_create_payout_je` is also connector-owned canonical state. Not a violation.

Capstone test added (`test_a89_no_direct_journal_line_reconciled_write_in_matching_path`): stubs the projection to a no-op, runs `auto_match_transactions`, asserts (a) the canonical event WAS emitted, (b) `JournalLine.reconciled` stayed `False`. If anyone ever reintroduces a "just in case" direct flip, this test fails.

7/7 green at `tests/test_a86_6_bank_connector_emission.py`.

### A93. Migration health gate + RLS_BYPASS engine guard — **DONE 2026-05-26** (correctness/dev-loop, surfaced by 2026-05-26 architectural review)
Codex flagged a reported `duplicate column name: warehouse_id` SQLite migration failure. The original symptom did not reproduce, but reproducing the gate surfaced a different latent bug: `settings.py` unconditionally added a Postgres-only `OPTIONS["options"] = "-c app.rls_bypass=on"` to `DATABASES["default"]` whenever `RLS_BYPASS=True`. Any Django command run against a SQLite `DATABASE_URL` with that flag set crashed with `TypeError: 'options' is an invalid keyword argument for Connection()`. `pytest` only worked because `test_settings.py` overwrote `DATABASES` *after* the buggy mutation. Gated the block on `"postgresql" in ENGINE` so SQLite stays usable.

Gate now exists in two halves:

- **Fast (pre-push):** `.pre-commit-config.yaml` runs `python backend/manage.py makemigrations --check --dry-run` on every `git push`. Enable on a fresh clone with `pre-commit install --hook-type pre-push`.
- **Full (manual before schema work):** `scripts/check-migrations.sh` + `scripts/check-migrations.ps1` add a migrate-from-zero against a throw-away SQLite DB (~40s). Catches duplicate-column, missing-dependency, and bad-RunPython class bugs that `--check` does not.

Verified end-to-end: A87 backend test suite still 11/11 green after the settings fix. `check-migrations.ps1` reports `Migration health: GREEN.`

### A92. Plaintext password in sessionStorage during company-selection — **DONE 2026-05-26** (security, surfaced by 2026-05-26 architectural review)
Shipped pending-login-token flow. Previously `login.tsx` wrote `pendingPassword` + `pendingEmail` to `sessionStorage` so `select-company.tsx` could re-POST the credentials with the chosen `company_id`. Any XSS or browser extension could lift the password.

Replaced with a short-lived signed token: the backend mints a `pending_login_token` (5-minute TTL, `django.core.signing.dumps` salted with `nxentra.pending-login.v1`, payload = `{user_id, valid_company_ids}`) and returns it alongside the `choose_company` response. The browser stores only that token. The second `/auth/login/` call exchanges `{pending_login_token, company_id}` for JWTs without re-sending the password. Membership is re-checked at exchange time so a revocation between step 1 and 2 still blocks the login.

`email + password + company_id` continues to work for API clients and the existing e2e test — only the browser sessionStorage round-trip was the bug.

11 backend regression tests in `accounts/tests/test_pending_login_token.py` (token mint + shape, exchange happy path, expired token, tampered token, wrong company, revoked membership, missing user, missing company_id, wrong salt, max_age sanity). 3 new frontend regression tests in `tests/login-page.test.tsx` including a paranoid `for (key of sessionStorage) expect(value).not.toContain(password)` to pin the rule. 12/12 frontend + 11/11 backend green.

## From: NEXT_TASKS cleanup — verified-done items moved 2026-06-20

Batch-moved out of NEXT_TASKS.md after git-confirming each is fully shipped. Each entry carries a closeout line (what resolved it + commit) followed by the full original ticket text.

### A0. Invariant suites mandatory in CI on Postgres — ✅ **DONE** (commit `fb0e3d6`)
**✅ Resolved:** Postgres-backed invariant suites (`test_truth_invariants` / `test_runtime_invariants` / `test_control_invariants`) run in CI on a `postgres:16` service and block merge on failure — `backend-invariants` job in `.github/workflows/ci.yml`. *(Original ticket below.)*

Foundation before foundation. Fix the pytest/Django settings bootstrap issue (currently fails on CORS production guard in `settings.py:235`). Run `tests/test_truth_invariants.py` on a Postgres container in CI, not SQLite. Merge blocks on invariant failure.

Until CI is green on Postgres invariants, the "truth engine" is not actually proven — it's just asserted.

### A50. Wizard "Import all historical orders" → 403 Forbidden — ✅ **DONE 2026-05-16** (commit `104a453`)
**✅ Resolved:** wizard historical import clamps `created_at_min` to a 59-day floor when `read_all_orders` isn't granted, so the >60-day 403 no longer fires. Full >60-day history later unlocked by [[A55]]/A126's `read_all_orders` grant. *(Original ticket below.)*

The onboarding wizard's "Import all historical orders" option sends `created_at_min=2015-01-01` to `/admin/api/{ver}/orders.json`. Shopify's `read_orders` scope (which we have) limits to last 60 days; older `created_at_min` returns 403. **Sentry event:** id `5d5177e81c9941499b36ad943d312a35`, task `shopify.sync_store_orders`, 2026-05-15 02:23:24 EEST, store `nxentra-reviewer-store.myshopify.com`. **Fix:** clamp `created_at_min` to `max(stated_min, now - 60 days)` when `read_all_orders` scope is not granted. Add wizard copy explaining that >60d history requires separate Shopify scope approval (or paginate in 60d windows and rely on `updated_at_min` for older content — research needed). Alternative: add `read_all_orders` to [shopify.app.toml:10](shopify.app.toml#L10) scope set and request Shopify approval (longer path, several days).

### A51. "Register Webhooks" button fails — REST API needs `write_webhooks` scope we don't grant — ✅ **DONE 2026-05-16** (commit `104a453`)
**✅ Resolved:** dropped the programmatic register path for declarative webhook config (auto-registered via `shopify app deploy`); removed the UI button + backend route + model field (migration 0014). No `write_webhooks` scope needed. *(Original ticket below.)*

In-app "Register Webhooks" action in the Shopify integration settings page posts to Shopify Admin REST `webhooks.json`, which requires `write_webhooks` scope. Our scope set in [shopify.app.toml:10](shopify.app.toml#L10) doesn't include it. GDPR compliance webhooks declared in [shopify.app.toml:22-25](shopify.app.toml#L22-L25) `[webhooks.privacy_compliance]` auto-register via `shopify app deploy` and are fine — only the programmatic registration path is broken. UI toast "Failed to register webhooks" shown 2026-05-15 02:25 EEST. **Fix:** remove the "Register Webhooks" button and rely entirely on declarative webhook config (preferred — matches Shopify's modern architecture, no scope needed). OR: add `write_webhooks` to scope set + request approval (slower, more attack surface). Declarative-only is the simpler and cleaner path.

### A55. Add `read_all_orders` scope for full historical import (>60 days) — ✅ **DONE** (folded into [[A126]], `1bad6c6` + `shopify app deploy` → nxentra-sync-9)
**✅ Resolved:** `read_all_orders` added to all three scope sites (shopify.app.toml + settings.py `SHOPIFY_SCOPES` + commands.py) and deployed; the 59-day clamp lift is scope-gated on `ShopifyStore.scopes` (un-reconnected stores never 403). Same work shipped under A126. Activation still requires existing stores to reconnect (operational, not code). *(Original ticket below.)*

A50 clamped the wizard's "Import all historical orders" to a 59-day floor because the `read_orders` scope is limited to that window. Merchants with longer histories who want their full books in Nxentra need orders older than 60 days. **Fix:** add `read_all_orders` to the `scopes` line in [shopify.app.toml:10](shopify.app.toml#L10), submit for separate Shopify approval (this scope requires explicit justification — accounting/bookkeeping is a recognized legitimate use). Once approved, relax the 59-day clamp in [accounts/commands.py:_enqueue_shopify_historical_import](backend/accounts/commands.py) to use the user-requested date range without clamping. Also update the wizard copy from "Import all historical orders" (currently misleading — clamps to 60d) to accurately describe what gets imported. **Workaround for merchants who need >60d before approval lands:** manual CSV settlement importer for backfill (already exists, A14 path). Independent of A53 — these are separate Shopify approvals running on separate timelines.

### A57. `disconnect_store` picks wrong store when multiple non-disconnected exist — ✅ **DONE** (folded into [[A136]], `1bad6c6` + frontend `f2512dc`)
**✅ Resolved:** `disconnect_store` now requires an explicit `store_public_id` (signature `disconnect_store(actor, store_public_id)`), and the frontend ships a store-picker so a multi-store merchant disconnects the intended store — no more arbitrary `pk ASC` `.first()`. Shipped as A136 alongside the A134 store-resolution work. *(Original ticket below.)*

Current code: `ShopifyStore.objects.filter(company=actor.company).exclude(status=DISCONNECTED).first()`. No `order_by`, so Django's default `pk ASC` wins. When `Shopify_R` had `store_id=66` (PENDING, aljazeera7 orphan from A56) and `store_id=67` (ACTIVE, nxentra-reviewer-store), clicking "Disconnect Store" in the UI disconnected store_id=66 instead of store_id=67 — leaving the actually-active store still connected silently. Surfaced 2026-05-17 when re-OAuth flow showed "Shopify store connected successfully!" toast but page UI still showed disconnected state. **Fix:** require `store_public_id` parameter always, OR change query to `.filter(status=ACTIVE).order_by('-updated_at')`. Option (b) is the more forgiving default.

### A52. "Re-sync Orders (7d)" returns "0 new, 0 already synced" despite orders present — ✅ **DONE** (diagnostics `104a453`; root cause fixed by [[A121]] GraphQL migration)
**✅ Resolved:** zero-orders was the REST path silently dropping dev-store test orders (compounded by the 2025-01 API sunset → 403). The full GraphQL migration (A121, `885dfbf`…`3533812`) returns those orders correctly — A121's closeout explicitly notes "fixed the A52 zero-orders bug." Diagnostic logging shipped in `104a453`. *(Original ticket below.)*

After avoiding A50 by clicking the 7-day re-sync button instead of the wizard's "all-history" option, the API call succeeded (no 403, no Sentry error) but `sync_store_orders` task reported zero orders imported. UI toast "Order re-sync complete: 0 new, 0 already synced" shown 2026-05-15 ~02:28 EEST. **Verified:** 6 orders existed in `nxentra-reviewer-store` admin at sync time — #1001-#1003 paid, #1004 fully refunded, #1005 partially refunded, #1006 paid+fulfilled. All same-day created, USD, on a USD-functional-currency `Shopify_R` company. Status filter in the API call was `status=any` (verified from the A50 Sentry trace, which used the same task) so that part is fine. **Diagnosis needed:** instrument `backend/shopify_connector/tasks.py` `sync_store_orders` to log the outgoing API URL with all query params and the parsed response count. Likely root causes (in order): `updated_at_min` filter instead of `created_at_min` (orders never updated → excluded), timezone offset miscalculation (UTC vs EEST cuts off today's orders), hidden `financial_status` filter, response-parser bug discarding valid orders. Likely a 1-line fix once located. **Blocks:** the proper Shopify→Nxentra sync demo for App Store reviewer. Workaround for submission: create demo journal entries / invoices natively in Nxentra and present Shopify connection as "Connected" status only (Path 3 from 2026-05-15 03:00 conversation). **Update 2026-06-02:** Strongly suspected to share root cause with A120 — by 2026-06-01 the same `orders.json` endpoint was returning a hard 403 against the reviewer's `mec3xu-zd` store. Most likely "0/0 in May" was the soft leading edge of the same 2025-01 sunset and goes away with the 2026-04 bump. Verify by re-running the 7d re-sync against `Shopify_R` after deploy with at least one Bogus-Gateway test order present.

### A122. Address "deprecated offline tokens" warning surfaced by Shopify Dev Dashboard — ✅ **DONE 2026-06-02** (commit `493df61`, migration `0015_a122_rotating_tokens`)
**✅ Resolved:** migrated `complete_oauth` off permanent offline tokens to the rotating-offline-token pattern + Shopify launch handshake; verified live (a `shprt_` token was issued). Cleared the Dev Dashboard "deprecated offline tokens" banner. *(Original ticket below.)*

Dev Dashboard's Overview page for `Nxentra Sync` shows a red "Fix overdue" banner: "Calls made with deprecated offline tokens detected in the last 14 days." Per Shopify's deprecation timeline, public apps must migrate from the legacy permanent-offline OAuth flow to either online tokens or the rotating-token pattern. Currently our `complete_oauth` exchanges code for a permanent offline token. Investigation needed to determine the exact migration path (online vs rotating offline) and code changes required in `commands.py:complete_oauth`. May surface in App Store review feedback if not addressed pre-resubmission.

### A116. `JournalEntry.source_module` / `source_document` are direct-written, not in event payload — lost on projection rebuild — ✅ **DONE 2026-06-20** (reconciliation truth phase, PR #2 → main `ac6a36f`)
**✅ Resolved:** the stamps now ride in the event payload instead of a direct-write after materialization. `JournalEntryCreatedData` / `JournalEntryPostedData` gained optional `source_module` / `source_document`; `create_journal_entry` / `post_journal_entry` accept + emit them; the accounting projection materializes them on the row (set-if-nonempty on POSTED). The post-hoc `.update()` stamp is gone, so a JE rebuild no longer zeros the Banked column. Gated by `test_reconciliation_truth_gate.py`. See [[project_reconciliation_link_shipped_2026_06_20]]. *(Original ticket below.)*

**The bug.**  Settlement and bank-clearance journal entries carry two stamps used downstream to join the Stage 1 → Stage 3 chain:

- `source_module = 'payment_settlement'` (settlement JEs) or `'payment_settlement_clearance'` (clearance JEs)
- `source_document = batch_id` (links settlement to clearance via the shared Paymob/Bosta batch identifier)

These stamps are applied via a **direct ORM update AFTER the projection materializes the JE** — see `backend/reconciliation/commands.py:407` for the clearance side and the settlement command for the other:

```python
with command_writes_allowed():
    JournalEntry.objects.filter(pk=entry.pk).update(
        source_module="payment_settlement_clearance",
        source_document=settlement_entry.source_document or batch_id,
    )
```

The fields are NOT part of the `JOURNAL_ENTRY_CREATED` event payload (`JournalEntryCreatedData` in `events/types.py` doesn't include them).  So when `JournalEntryProjection.handle()` re-materializes a JE from events during a rebuild, it never sees the stamps.

**Symptom on Shopify_R 2026-05-28.**  After the orphan-JE purge and balance-projection rebuilds, the Banked column on the reconciliation page showed $0 for all providers.  Diagnosis: `JournalEntry.source_module = ''` and `source_document = ''` on both the settlement JE (JE-000018) and the bank clearance JE (JE-000019).  The `_banked_by_provider` helper filters by `source_module='payment_settlement'` / `'payment_settlement_clearance'` so found no rows.  Manually re-stamped via shell update from the memo's batch_id; Banked column now shows correct Paymob $5,688.03.

**Smallest fix.**  Extend `JournalEntryCreatedData` (and `JournalEntryPostedData` if needed) with optional `source_module` and `source_document` fields.  Have `create_journal_entry` and `post_journal_entry` accept these as kwargs and include them in the emitted event.  Have the projection's `handle()` set them on the row when materializing.  Drop the direct-write stamp step entirely — it becomes redundant.

**Why this matters.**  Settle / clearance / future audit pipelines all key off these stamps.  Today any operator who does a JE rebuild silently loses the Stage 2→3 join.  Same root cause as [[A115]] (rebuild semantics) and [[A114]] (FK stability across rebuilds) — fields outside the event payload don't survive rebuilds.

**Workaround in place for Shopify_R 2026-05-28:** shell update that re-derives batch_id from the JE memo via regex `r"batch\s+([\w\-]+)"`.  Persists until the next rebuild that clears JE rows.  Not committed to source — one-off.

### A129. Settlement clearance JE: not idempotent per batch, orphaned by statement deletion, inconsistent currency — ✅ **RESOLVED 2026-06-20** (reconciliation truth phase, PR #2 → main `ac6a36f`)
**✅ Resolved:** parts 1+2 shipped in the truth phase; part 3 investigated and closed as a non-issue. (1) `_plan_settlement_prepass_matches` skips candidates whose `source_doc` is already cleared (batch+provider-scoped, replay-safe). (2) `pre_delete` guard (`guard_bank_statement_delete` + `statement_delete_allowed()` contextvar) blocks deleting a statement with matched lines; `unmatch_and_delete_statement` reverses cleanly first. (3) The cross-currency "won't net to zero" path (A129c) is unreachable via the matcher (compares against the functional-converted EBD debit) → no code change. *(Original ticket below.)*

Live sequence on Shopify_R: a May bank statement was matched (clearance JE-000019 posted: Dr Bank / Cr Expected Bank Deposit), then the statement was deleted during cleanup — the clearance JE survived and the settlement's matched-state reset. Re-importing the same statement and auto-matching posted a SECOND identical clearance (JE-000048) → Bank QNB doubled to 11,376.06 and 11600 went negative. Fixed manually by reversing the orphan (JE-000049). Three product fixes:
1. **Idempotent clearance per settlement batch**: `_settlement_prepass_match` must detect an existing non-reversed clearance JE for the same settlement batch (deterministic idempotency key scoped to batch id, not uuid4 per emission) and link to it instead of posting again.
2. **Statement deletion must unmatch first**: deleting a BankStatement with matched lines should run the unmatch flow (reversing clearance side-effects per A19) or be blocked until lines are unmatched. Today it orphans JEs and resets match state silently.
3. **Clearance JE currency consistency**: May's path booked clearance as USD-foreign (settlement currency @ rate), the A85/A86 path books plain functional EGP — two JEs for the same settlement denominated differently (user-visible confusion in the JE list). Pick one convention (settlement currency as foreign + functional base, matching the sales-invoice JEs) and apply to the prepass path.
Related display nit: the statement page's "GL Balance" tile nets only unreconciled lines, which reads as 0.00 when an orphan+reversal pair cancels — label it ("Unreconciled GL movement") or compute the true account balance.

### A123. Sentry `before_send` PII redaction filter — ✅ **DONE + DEPLOYED + VERIFIED LIVE 2026-06-28** (PR #25 `e3b8fd9`)
Extended `backend/ops/sentry_scrub.py` (the pre-existing credential scrubber) to also redact **customer PII** from error telemetry — `send_default_pii=False` stops auto-captured PII, but it still leaks via exception messages, log args, breadcrumbs, and GET query strings. The existing `before_send` already walks the whole event, so PII redaction rides the same recursive pass: value patterns (email, phone — intl/Egyptian mobile+landline/NANP, Luhn-gated card numbers incl. numeric values, IBAN, Egyptian 14-digit national ID) + segment-anchored field-name patterns (email/phone/address/ssn/tax_id/card_number/iban/bank_account/…) + parsed `request.query_string` params. A 4-lens adversarial review caught a **ReDoS** in the email regex (a DoS vector since `before_send` runs inline — fixed by bounding the local part `{1,64}` → linear) + missing IBAN/bank coverage + phone over-redaction of signed decimals; Codex P1 (numeric PAN bypass) + P2 (separated mobiles) fixed; declined the IBAN-IGNORECASE P2 (would over-redact ~5.5% of hex trace IDs). Tests: `backend/tests/test_a123_pii_scrub.py` (24). `docs/security/data-loss-prevention.md` §5 drops the "roadmap" qualifier. All 7 CI green incl. Postgres E2E. Deployed (backend-only, no migration/build: pull + restart 3 procs) + verified live (`cust [redacted] card [redacted] phone [redacted]`). See [[session_2026_06_27_28_gate_a123]].

## 2026-07-11 audit cleanup — items moved out of NEXT_TASKS.md

Batch-moved during the full-repo audit. Full original ticket text for every item below is preserved in [docs/archive/NEXT_TASKS_pre-cleanup_2026-07-11.md](docs/archive/NEXT_TASKS_pre-cleanup_2026-07-11.md). Classifications were git/code-verified (evidence cited per item).

### Shipped or partially shipped classifications (verified in code/git)

- **A4 (implemented subset)** — A101/A102 shipped 5 blocking AST rules in CI. The original acceptance scope was not fully met: "every finance command has an event test" was not built, and the projection-emitter rule scans only files literally named `projections.py`, missing `projections/property.py`. The remaining boundary/scan outcome is active as A175.
- **A6** — onboarding auto-launch shipped in `frontend/pages/dashboard/index.tsx:46-60` with the new-owner redirect in `frontend/contexts/AuthContext.tsx:102-110` (git blame `d9e691e0`, 2026-05-04). The later F3 Finish-Setup CTA supplements this; it did not supersede an unbuilt A6.
- **A9** — no-SKU item auto-create fallback: `shopify_connector/commands.py:3057-3080` (explicit "A9:" docstring; SHOP-{variant_id} fallback).
- **A10** — policy tie-out now includes accounts referenced by active CUSTOMER/VENDOR posting profiles, not only RECEIVABLE/PAYABLE_CONTROL roles (`accounting/policies.py:912-987`; test `test_a10_tieout_includes_non_ar_control_posting_profile_accounts`; commit `49f2a63`). The newer F16 defect is different: settlements drain platform clearing without reducing the pseudo-customer subledger, so that economic-state mismatch remains active.
- **A28** — wizard final screen: `onboarding/setup.tsx:1449-1474` ("A28:" comment; Go to Dashboard primary CTA).
- **A29** — date-format utility: `hooks/useCompanyFormat.ts`, imported in 55 files.
- **A33** — PAYMENT_PROCESSING_FEES seed label: `accounts/commands.py:3662-3667`; Stripe fees got dedicated 53100 (#18 `721fff0`).
- **A37** — subledger tieout FK-alias fix: `projections/views.py:3703-3710` ("A37:" comment).
- **A39** — settlement double-credit vs Shopify credit note: `payment_settlement_projection.py:211-252` + `_detect_already_credited_lines`.
- **A40** — seed pack emits orders before refunds: `seed_test_csv_pack.py:99-111` ("A40:" comment).
- **A41** — defer-on-exhaust: `DeferEvent` in `projections/base.py:28` + bounded 24h defer in shopify projections (shipped with A134).
- **A42** — settlement import success toast: `finance/settlements/import.tsx:145-148`.
- **A43** — CN/invoice detail 404: detail pages exist and the original route/data fix shipped in `1d50ced`; later Run-1 coverage exercised the page as well.
- **A47** — credential encryption at rest: commits `942ad17`/`b82df03`/`0388a6e` (#5-#7), verified live. (The old NEXT_TASKS entry still claimed "no encryption layer exists" — false since late June.)
- **Next.js 14.2.35 security upgrade** — shipped in `bcd829e` and `d29091c`; it cleared the critical advisory present at the time and restored the Security gate. Active E11 is explicitly a later follow-on because 14.x subsequently became unsupported and the current dependency audit reports newer high/moderate findings—not a claim that this upgrade never happened.
- **A53** — PCD Level 1 + PII webhook subscriptions: `shopify.app.toml` carries orders/refunds/fulfillments topics with the 2026-06-11 PCD-completion comment.
- **A58 (partial, reopened)** — model/migration and edit control exist, but persistence never shipped: the submit payload, `ItemUpdateSerializer`, and `update_item` omit `external_url`. Returned to `NEXT_TASKS.md` for an end-to-end save test.
- **A104** — je_builder FX fallback unified to quarantine: FX sweep #33 (`67fd754`); the warn-and-post-at-1.0 path is gone.
- **A130** — demo order-number collision: code done (`f2512dc`); residual droplet reseed is now explicitly tracked in the M4 ops checklist.
- **A137** — Account Inquiry / GL drilldown: shipped + deployed (#26/#27).
- **A142** — settlement JE header rate stamp: commit `474e392` (#42); A147's shipped text references "the A142-stamped rate".
- **A150** — date-ranged payout totals: resolved by A152 PR1 windowed Stage-2 ledger (docs commit `b7a302d` "resolves A150"). The C4b legacy-endpoint deletion decision rides with the C4b ticket when that work resumes.
- **S0** — financial-trust hardening: full [S0] commit series (`1a1541b` module-key, `c37f663` parser registry, `d6fd9e7` capabilities+DTOs, `7dcf480` auth-agnostic StripeAccount, `ab1361a` raw cache "[S0 complete]").
- **S1** — Stripe read-only adapter: commits #8-#17; sandbox connected; later real-payout C3 gate passed (#38-#41).
- **S2 / S2-gate** — Stage-2 payout-line breakdown + Paymob/Bosta canonical-projection gate: ADR-0002 Phase-2 PR-A/B + PR-D (#47-#49); `test_s2b_payments_projection_gate.py`.
- **A139/A140/A141/A143/A144/A146/A147+A148/PR-D/A152/F27** — already marked ✅ in the old file with commits (#40-#58 series); prose moved here wholesale via the archive. Outstanding **operational** steps extracted to the M4 ops checklist (A139 droplet backfill, A140 webhook resend, A126 reconnect, A146 restamp, PR-D ordered deploy).
- **FX-sweep residue "wrong-currency stamps"** — done by A146 (`27dec2a`, functional-first at 3 sites); the 2026-07-01 bullet was never updated.

### Obsolete / superseded (closed without shipping — superseded by a different design)

- **A14 historical scope block, A35 bundle, A108, merchant-readiness exit-criteria, "What to do right now" blocks, critical-path diagram, week-4 gates, decision points** — April/May-era narrative; app published 2026-06-16. Unverified A30/A35 residue re-filed as a P3 re-triage ticket.
- **A3** (reactor extraction) — the old all-at-once design is retired, not completed. Dormant clinic/property emitters are governed by the A170 archive decision; the live Shopify emitter and filename-based architecture-rule blind spot remain active as A175.
- **A5** (bank connector + FX direct-write cleanup) — decomposed rather than silently dropped: remaining reconciliation writes are A99b; the duplicate bank engine is covered by A158/A166 retirement; FX posting paths were hardened by the FX sweep. No separate A5 epic remains.
- **B1** (universal ingest inbox design) — abandoned/narrowed, not fully delivered. ProviderRawObject + ProjectionFailureLog/DeferEvent cover parts of the outcome, but Shopify still acknowledges failed financial webhooks without a durable received/failed/poison record. Durable retry/backfill remains in A159; do not read this closeout as proof of a universal inbox.
- **B3/B4** (canonical platform models design/build) — superseded by ADR-0002 (canonical layer inside the existing event/projection architecture; ProviderPayout/Line shipped instead).
- **B5** (Shopify→canonical migration) — explicitly retired by the Phase S header ("out of scope").
- **B6/B7** (Stripe/Paymob on B4 models) — superseded by Phase S (S0-S2) and S5 respectively.
- **C1/C2/C3** (generic recon engine + three-way UI) — the intended product outcome was superseded by ADR-0001 ReconciliationLink + the A86 bounded context + `/finance/reconciliation` + A145/A148. Consolidation is incomplete while `/banking/reconciliation` and `/shopify/reconciliation` remain live; that residue is active under A158/A166/F4. (Note: "C3" also named the ADR-0002 payout read-switch — ID collision recorded.)
- **E3 (CSV half)** — Bosta CSV shipped via A14; API half stays deferred (S5-adjacent).
- **E5** — unified settlements page: superseded by A145 Stage-2 ProviderPayout ledger + PR-D3 per-line detail.
- **A27/A32/A34** — reserved-and-never-used ID placeholders; deleted.

### Task-ID collisions recorded (for future traceability)

A80 = deprecated AR/AP columns AND the shipped ProjectionFailureLog/operator queue (live column-removal ticket renamed **A80b**); A84 = receipts-form UX AND shipped posting-period defense (live UX ticket renamed **A84b**); A85 = opening-equity ticket AND JE-preview epic AND the F27 period_override reference (opening-equity re-filed as **A85b**); A86 = fee-mapping ticket AND the reconciliation bounded-context epic; A87 = bank-import locale/date work AND shipped pending-login/password-removal hardening (live import residue renamed **A87b**); A98 = shopify health check AND the deferred mypy cleanup; C3 = Phase-C UI AND the ADR-0002 payout read-switch.
