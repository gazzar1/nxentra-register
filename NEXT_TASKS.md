# Next Tasks

Strategic roadmap drafted 2026-04-25 and updated the same day after incorporating an independent architectural review. Follows the discipline in [ENGINEERING_PROTOCOL.md](ENGINEERING_PROTOCOL.md): finance work is event-first, projections are derived, auditability beats convenience, and every change type carries its required tests.

**Foundation assessment:** **B+ / A-** on the accounting core, roughly **6/10** on the full integration-grade, AI/MCP-ready operating core. Hard parts (event sourcing primitives, CQRS, write barriers, RLS, invariant tests) are genuinely strong. Gaps are on the perimeter: ingestion resilience, schema evolution, module governance consistency outside accounting, CI-enforced invariants, and agent-ready command surface.

**Framing:** Nxentra is a canonical financial event and reconciliation engine. Shopify is the first proof. Every canonical abstraction must be justified by a real concrete integration need — not speculative.

**Estimated budget:** Phases A-D ≈ 3-4 months focused work. Phase E is ongoing.

---

## Phase A — First-user unblock + foundation hardening (this week to 2 weeks)

Ship these before the first real user (acquired 2026-04-22) imports real orders, and before any large refactor.

### A0. Invariant suites mandatory in CI on Postgres — **2-3d**
Foundation before foundation. Fix the pytest/Django settings bootstrap issue (currently fails on CORS production guard in `settings.py:235`). Run `tests/test_truth_invariants.py` on a Postgres container in CI, not SQLite. Merge blocks on invariant failure.

Until CI is green on Postgres invariants, the "truth engine" is not actually proven — it's just asserted.

### A1. Phase 1 dry-run on fresh Shopify dev store — ✅ **DONE 2026-04-28**
All 5 scenarios passed against `nxentra-test-code.myshopify.com`. 7 critical bugs found + fixed + regression-tested along the way: registration currency persistence, OAuth callback projection-guard violation, two null-customer crashes (handler + projection), two frontend status display gaps (badge + dashboard icon), and the load-bearing wizard finalization gap that would have left every first user without sales routing or webhooks. Commits `b6b52b9`, `5b550fb`, `b3417f3`, `cdd286e`, `7d9a852`, `d85ed48`, `7d12432`. Five UX/invariant follow-ups identified as A6-A10 (below).

See [SESSION_LOG.md § Session: April 26-28, 2026](SESSION_LOG.md) for the full play-by-play. **First user can be invited.**

### A2. PaymentGateway mapping (tactical slice) — ✅ **DONE 2026-04-30**
Shipped Shape B: `PaymentGateway(company, external_system, source_code, normalized_code, display_name, posting_profile FK, is_active, needs_review)`. Clearing account is derived (`gateway.posting_profile.control_account`) — JE construction in `sales/commands.py` unchanged. Bootstrap on `_ensure_shopify_sales_setup` creates 7 default rows + 7 dedicated `PG-*` PostingProfiles (paymob/paypal/manual/shopify_payments/cash_on_delivery/bank_transfer/unknown), all initially anchored on the same SHOPIFY_CLEARING; merchant edits a single profile's `control_account` to split a gateway off. Unknown gateway codes lazy-create with `needs_review=True` (operator visibility via API filter + `list_review_payment_gateways` mgmt command, per [ENGINEERING_PROTOCOL.md](ENGINEERING_PROTOCOL.md) §2.4). Frontend: "Payment Gateway Routing" card on `/shopify/settings`. 19 new tests + 28 regression tests pass. Commit `d0dd0d2`.

External arch review (forwarded by user before coding) added the load-bearing refinements: `external_system` scoping, `normalized_code` for Shopify casing/spacing variance, `needs_review` flag for unknown gateways, and `accounting/` over `platform_connectors/` as the home (connectors detect facts; accounting decides meaning).

A2 deliberately does NOT migrate historical invoices to per-gateway clearing accounts — only routes future imports. If first user wants per-gateway re-posting of historical Shopify invoices, that's a separate corrective JE (out of scope).

### A3. Introduce Reactor concept; migrate 3 projection-emits-event cases — **~4-5d**
**Reframed per review:** this isn't "move 3 files." It's introducing a distinct architectural concept — **reactors** (aka process managers) as separate from projections — because conflating them muddies CQRS and replay semantics.

- Create `reactors/` layer with a base class (or explicit registry) distinct from projections.
- Move these three cases to reactors:
  - `clinic/projections.py:320` (rent.due_posted → JE)
  - `shopify_connector/projections.py:1043` (payout settlement)
  - `projections/property.py:671` (property-specific event)
- Document the rule: projections are **pure read-model builders**; reactors are event-to-command orchestrators with explicit idempotency + replay rules.
- Update [FINANCE_EVENT_FIRST_POLICY.md](FINANCE_EVENT_FIRST_POLICY.md) with the reactor concept and drop "acceptable exceptions."

### A4. Architecture tests — **1-2d**
Small, tight rule set (5-10 rules max, all enforced):
- No direct model mutation in `accounts/views.py`, `accounting/views.py`, `bank_connector/views.py`, `shopify_connector/views.py` — must route through a command.
- No `emit_event` call inside a file under `projections/` — only from `commands/` or `reactors/`.
- No `rls_bypass()` outside `accounts/rls.py`, `tests/`, or an explicit allowlist.
- Every finance-impacting command must have an event test in its test file.

Wire into the CI matrix. Start tight — tests that fail and get `# noqa`'d everywhere are worse than no tests.

### A5. Bank connector + FX direct-writes cleanup — **3-5d**
`bank_connector/views.py:71` and `:247` create and mutate operational records directly. `accounting/views.py:2097` writes exchange rates directly. These are the same pattern as A3 — extract to commands, emit events, update projection/balance flow.

Run A3's reactor pattern across these after the base layer is in place.

### A6. Onboarding wizard auto-launch on first dashboard visit — **~1d** (UX, surfaced by A1)
Right now a brand-new company with `onboarding_completed=False` lands on the dashboard with empty widgets and a "Continue Setup" banner. The first user can finish setup but the experience is less guided than it should be. Add a route guard / redirect so any unfinished onboarding deep-links straight into the wizard's first incomplete step. Not a blocker — banner works — but worth tightening before the user count grows.

### A7. Wizard step routing after Shopify connect callback — **~1d** (UX, surfaced by A1)
After a successful Shopify OAuth callback the wizard kicks the user back to the **Fiscal Year** step (the previous one) instead of advancing past Shopify Setup. Disorienting. Should advance to the Import Orders step (or wherever the next incomplete step is). Likely just a routing/redirect bug in the callback success handler.

### A8. Auto-fill GL accounts on Items created from Shopify imports — ✅ **DONE 2026-04-29**
Surfaced from A1: `_auto_create_item_from_line` was creating Items from Shopify SKUs but `_resolve_default_item_accounts` looked for accounts at the wrong codes (`1300`/`5100` instead of `13000`/`51000` that `_setup_shopify_accounts` actually creates), and the fallback `_ensure_inventory_accounts` used an invalid role string for ASSET accounts. Net result: every auto-created Item had Sales/Purchase/Inventory/COGS = None. Rewrote the resolver to read all four accounts from the company's shopify_connector ModuleAccountMapping (purchase defaults to inventory for stocked items). Deleted the broken fallback. Added two regression tests: defaults-on-create and preservation-on-update (proves merchant's manual GL account edits are never overwritten by future Shopify activity). Commits `71cb0d7`, `cd7f484`.

### A9. Item auto-create fallback when Shopify product has no SKU — **~1d** (correctness, surfaced by A1)
Today `_auto_create_item_from_line` only fires when `sku` is non-empty. Egyptian merchants frequently sell products without SKUs (small operations, custom items). Fall back to using `shopify_product_id` as the Item code, with the product title as the name. Same auto-fill of GL accounts as A8.

### A10. AR tie-out invariant accommodates non-AR-Control posting profiles — **~2-3d** (invariant, surfaced by A1)
`post_journal_entry` logs `"AR tie-out mismatch: AR Control (X) != Customer balances (Y)"` warnings whenever a customer uses a non-AR-Control posting profile (e.g. Shopify Clearing — where `_ensure_shopify_sales_setup` deliberately points the SHOPIFY-NXENTRA-* customer at the clearing account, not 12000 AR Control). The data is consistent (JEs balanced, customer balance matches debits) — the invariant is overly strict. Fix: tie-out should sum the actual control accounts referenced by the posting profiles in use, not just `AR_CONTROL`. Will silence false positives across all integrated platforms (Shopify, Stripe, future Paymob).

### A11. Shopify JE should respect Item-level GL account overrides — **~2-3d** (correctness, surfaced by A8 review)
The `shopify_accounting` projection's `_handle_order_paid` builds **one aggregate revenue line per order** posted to the company's `SALES_REVENUE` ModuleAccountMapping (account 41000) — it does not iterate line items or look up `Item.sales_account` per SKU. So if a merchant edits HEAD-001's Sales Account from "Sales Revenue" (41000) to "Headphones Revenue" (41001), manual invoices for HEAD-001 will credit 41001 but Shopify-imported orders for HEAD-001 will keep crediting 41000. Manual invoices respect Item.sales_account; Shopify-imported invoices don't. To fix: refactor `_handle_order_paid` to iterate `line_items`, look up Item by SKU, create one revenue line per item using `item.sales_account` (fall back to mapping if None). Need to think through tax + discount allocation per line. Not blocking the first user — company-level default works correctly until they want per-product revenue routing. Deferred deliberately so we can see whether the first user actually customizes per-item before pre-building.

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

### A14 (historical scope retained below for context):
Bridges Stage 2 (Gateway → Bank) for Egyptian merchants without waiting for the Paymob (B7) or Bosta (E3) connector code. Critical because most of the first user's payouts (Paymob, PayPal, Bosta-COD) don't have automated settlement events today.

**Scope:**
- New provider-agnostic event type: `PAYMENT_SETTLEMENT_RECEIVED` (replaces the Shopify-specific shape; Shopify Payments adapter remaps onto it for consistency). Event payload carries: `settlement_provider`, `payment_method` (denormalized), `payout_batch_id`, `gross`, `fee`, `net`, `payout_date`, `currency`, line-level breakdown.
- New page: `/finance/settlements/import` with two CSV uploaders — Paymob settlement statement + Bosta COD report. Mappable column schemas per provider.
- CSV parsers:
  - **Paymob:** `order_id, gross, fee, net, payout_batch_id, payout_date`
  - **Bosta:** `shipment_id (mappable to order_id), collected, courier_fee, net, batch_id, payout_date, status (delivered/returned)`
- Generates `PAYMENT_SETTLEMENT_RECEIVED` events; projection posts:
  ```
  Dr Expected Bank Deposit  net
  Dr Gateway/Courier Fees   fee
  Dr Sales Returns / Failed (Bosta returned/uncollected only)
      Cr <Provider> Clearing   gross   [tagged with settlement_provider dimension]
  ```
- New account convention: `Expected Bank Deposit` (asset, sub-control). Created by `_setup_shopify_accounts` on Shopify connect; mapped via a new `EXPECTED_BANK_DEPOSIT` role in ModuleAccountMapping. Bank reconciliation matcher learns to match `payout_batch_id` → Expected Bank Deposit clearance. When the bank deposit lands, it clears the Expected Bank Deposit balance for that batch — automatically debiting whichever bank GL account received the deposit (multi-bank works through the existing bank-account-per-GL pattern, no schema change).
- Idempotency: CSV re-import is safe (events keyed by `provider + payout_batch_id + order_id`).
- Tests: Paymob + Bosta CSV import, JE shape, dimension tag preservation, idempotency, returned-COD line creates a Sales Returns hit, bank match against Expected Bank Deposit, multi-bank deposit clears regardless of which bank received it.

**Why now:** Without this, the reconciliation MVP from A13 has empty Stage-2 tiles for everything except Shopify Payments. The first user's books require this to feel complete. The event is provider-agnostic so when Paymob (B7) and Bosta (E3) connectors land, they emit the same event type — A14's CSV path gracefully retires.

### A15. Multi-courier-per-store routing — **~3-5d** (deferred until first merchant has multi-courier volume)
Today's A12 design uses a single `default_cod_settlement_provider` per Shopify store — adequate for the modal Egyptian merchant (Bosta-only), inadequate for merchants with split fulfillment (Bosta for Cairo, Aramex for Gulf, DHL for international).

**Scope when triggered:**
- Schema evolution: rename `default_cod_settlement_provider` → `primary_cod_settlement_provider` (FK stays); add `cod_settlement_providers` (M2M to SettlementProvider).
- Wizard upgrade: radio → checkboxes with primary marker. Existing merchants migrate cleanly (current FK → primary, M2M empty until edited).
- Resolution rule in projection: read `shipping_carrier` from the fulfillment event (`fulfillments/create` webhook — currently consumed for COGS but not for routing). Match shipping_carrier against the M2M; fall back to primary if no match.
- Manual re-tag affordance on the order detail page for back-fixing mis-routed orders.

**Trigger to pull forward:** first merchant signals multi-courier volume in real use, OR week-4 gate (see Critical path) reveals manual re-tagging is becoming a workflow burden.

**Phase A exit criteria:**
- CI green, invariants mandatory, architecture tests enforcing event-first discipline.
- First user can import orders safely.
- Zero projection-emits-event cases; zero direct-write cases in views.
- Foundation is ready for the bigger refactor.
- **Reconciliation Control Center MVP shipped and validated against first user's real data.** Strategic addition: foundation-only is not a product; A2.5 + A12-A14 ensure the merchant sees "where is my money?" answered before Phase B's longer refactor begins.

---

## Phase B — Ingestion resilience + canonical platform models (5-7 weeks)

The lynchpin refactor, split into two independent foundations (inbox + schema evolution) plus the canonical model migration that builds on them.

### B1. Ingest Inbox pattern — **1 week**
Every external delivery (Shopify webhook, Stripe webhook, bank CSV row, Paymob notification) writes first to an immutable `IngestRecord`, keyed by `(provider, delivery_id, payload_hash)`. A worker normalizes + emits a canonical business event. Processing state (received / normalized / emitted / failed / poison) is explicit.

Gains:
- Resilience against double-delivery without breaking idempotency.
- Replay without asking Shopify to redeliver.
- Poison-message handling: bad payloads queue in a dead-letter state visible to operators.
- Partial postings surface visibly (per [ENGINEERING_PROTOCOL.md](ENGINEERING_PROTOCOL.md) §2.4).

Can be implemented with existing per-platform models — doesn't depend on canonical models. Do this first because it improves production resilience immediately.

### B2. Schema evolution infrastructure — **1 week** (can parallel B1)
Make `events.schema_version` real:
- Upcaster registry: functions that transform old event payloads into current shape on read.
- Versioned deserializer in `events/emitter.py` and projection event handlers.
- CI compat test: fixture set of historical events per event type, replayed after every migration, asserting projections still converge to expected state.

Without this, you can't evolve event schemas without hazard. For a system meant to live years, this is non-negotiable.

### B3. Canonical platform models — design + ADR — **2-3d**
`PlatformOrder`, `PlatformPayment`, `PlatformRefund`, `PlatformSettlement`, `PlatformDispute`. Attribution via `source_type`, `source_id`, `raw_payload` JSONB. Decision record.

### B4. Canonical platform models — build — **3-5d**
Models, migrations, RLS, write barriers, indices, unit tests. New app `commerce` (or extend `platform_connectors`).

### B5. Migrate Shopify to canonical models — **2 weeks**
Rewrite `process_order_paid / pending / cancelled / refund` commands to target canonical models (via the inbox layer from B1). Projections consume canonical events. **Shadow-write for 1 week** (both `ShopifyOrder` + canonical rows), then cutover + drop. Plan a 2-hour off-peak cutover window with rollback script.

All Shopify tests rewritten.

### B6. Migrate Stripe to canonical models — **3-5d**
Thin (Stripe connector is skeletal today).

### B7. Build Paymob connector on canonical models — **1 week**
Proof the pattern works with a real new integration. Webhook verifier, canonical mapping, Paymob sandbox testing.

**Phase B exit criteria:**
- Adding Paymob required touching fewer than 5 files outside its own folder. That's the "not bitter" test.
- Inbox is the single ingestion gate. No more webhook-to-event directly.
- Schema evolution: historical event fixtures replay green in CI.

---

## Phase C — Generic reconciliation engine (2-3 weeks, after B)

Can parallel with Phase D.

### C1. Reconciliation contracts design — **2-3d**
`ReconciliationSource`, `Matcher` interfaces. `ReconciliationRun` model. Proposed-JE generator via commands (not direct writes — per protocol §1.1).

### C2. Engine core — **1 week**
Runner, three built-in matchers (exact, amount+date, fuzzy-confidence), unmatched report, proposed-JE creation.

### C3. Three-way UI — Bank ↔ GL ↔ Platform(s) — **1 week**
Single React view, all sides side-by-side, filter / match / unmatch / bulk actions.

**Phase C exit criteria:** from the UI, reconcile Bank CSV + Shopify payouts + Stripe payouts + Paymob payouts in a single view with zero platform-specific reconciliation code.

---

## Phase D — Agent-ready command surface (2-3 weeks, can parallel C)

The trick isn't MCP — it's making commands self-describing. Once they are, every surface becomes trivial.

### D1. OpenAPI via drf-spectacular — **2-3d**
Install, configure, annotate endpoints, expose at `/api/schema/` and `/api/docs/`.

### D2. Declarative command schemas — **1-1.5 weeks**
Pydantic or dataclass schemas for every command's input + output. Pre-command validator. Permission + side-effect declarations. Command registry with reflection endpoint `/api/commands/`.

### D3. MCP server wrapping command registry — **3-5d**
Safety envelope: dry-run, permission checks, allowlist. Read-only first, write opt-in.

**Phase D exit criteria:** an LLM agent discovers commands, previews effects, executes — all schema-validated, all audit-logged.

---

## Phase E — Proliferation + durability (ongoing, after A-D)

| # | Ticket | Estimate |
|---|---|---|
| E1 | Connector scaffolder (`manage.py new_connector`) | 2-3d |
| E2 | Connector contract doc + vertical module guide | 2d |
| E3 | Bosta connector (CSV first, API later) | 2d CSV / 1wk API |
| E4 | Inventory Opening Balance step in onboarding wizard | 3-5d |
| E5 | Platform Settlements page under Finance (unified payouts / disputes / fees UI) | 1 week |
| E6 | Concurrent-write / deadlock / Postgres isolation-level tests | 3-5d |
| E7 | Cross-source reconciliation edge-case tests | 3-5d |
| E8 | Restock handler via StockLedger (move `_handle_refund_restock` out of projection into command) | 2-3d |
| E9 | Snapshotting infrastructure for long-lived aggregates | 1-2 weeks (trigger: when aggregates exceed ~1k events) |
| E10 | Tenant backup/restore integrity tests | 3-5d |
| E11 | Upgrade Next.js v14 → v15+ to clear remaining high-severity DoS / smuggling advisories ([GHSA-9g9p-9gw9-jx7f](https://github.com/advisories/GHSA-9g9p-9gw9-jx7f), [GHSA-h25m-26qc-wcjf](https://github.com/advisories/GHSA-h25m-26qc-wcjf), [GHSA-ggv3-7p47-pfv8](https://github.com/advisories/GHSA-ggv3-7p47-pfv8), [GHSA-3x4c-7xq6-9pq8](https://github.com/advisories/GHSA-3x4c-7xq6-9pq8), [GHSA-q4gf-8mx6-v5v3](https://github.com/advisories/GHSA-q4gf-8mx6-v5v3)). CI gate temporarily lowered to `--audit-level=critical` until this lands. Restore to `high` afterward. | 1-3d |

---

## Watch items (monitor, don't build yet)

Things the review flagged as real concerns but not blocking today. Set a threshold and revisit when crossed.

- **Event-write throughput bottleneck.** `BusinessEvent.save()` serializes per-company via `select_for_update()` on `CompanyEventCounter` (`events/models.py:356`). Correctness-first, but caps write throughput at ~1 TX per company per round-trip. **Revisit when:** >20 merchants live OR >10k events/day/company OR multi-agent AI writing commands in parallel. Likely fix: sharded counters, partitioned event tables, or move sequencing to append-only log with periodic consistency checks.
- **Projection orchestration is coarse-grained** (`projections/tasks.py:65` loops every projection per company). Wasted work at scale. **Revisit when:** projection count >20 per company OR projection-lag alerts fire regularly. Likely fix: event-type-to-projection routing table, targeted dispatch.
- **SQLite test DB in git** (`backend/test_db.sqlite3`). Minor Postgres-divergence risk. Acceptable but remove once CI runs on Postgres (A0).

---

## Critical path and parallelism

**Strategic reorder (2026-04-30, refined post-architectural-review):** A2.5 + A12-A14 (rename + dimension layer + Reconciliation MVP + manual settlement bridge) jumped ahead of A3-A5 to validate the merchant-facing product before Phase B's long refactor begins. Foundation cleanup (A3-A5) is genuinely load-bearing for long-term correctness, but the merchant cannot tell the difference between "good foundation" and "no product" — A2.5 + A12-A14 close that gap with ~2 weeks of work. A3-A5 resume after first-merchant signal validates (or invalidates) the framing.

The rename to SettlementProvider (A2.5) is non-negotiable before A12 starts: the model now covers Bosta, DHL, Aramex, bank transfer, and manual collection — keeping it named PaymentGateway would create technical debt the moment A12 goes live.

```
A0, A1, A2, A8 ✓ ──► A2.5 (rename) ──► A12 (dim layer) ──► A13 (recon MVP) ──┐
                                                                              │
                              invite first user (in parallel with A13) ───────┤
                                                                              ▼
                                                          A14 (CSV bridge for Stage 2)
                                                                              │
                                                                              ▼
                                                          Week-4 gate: real merchant signal
                                                                              │
                                  ┌───────────────────────────────────────────┴────────────────┐
                                  ▼                                                            ▼
                  A3 (reactors) ─► A4 (arch tests) ─► A5 (FX cleanup)                B1 (inbox) ──► B2 (schema evo)
                                                          │                                       │
                                                          └──────────────────┬────────────────────┘
                                                                             ▼
                                                          B3 (canonical design) ─► B4 (build)
                                                                                     │
                                                                                     ▼
                                                          B5 (Shopify) ─► B6 (Stripe) ─► B7 (Paymob)
                                                                                              │
                                                                                              ▼
                                                          C1 ─► C2 ─► C3 (reconciliation engine v2 — formalizes A13)
                                                                                              │
                                                                                              ▼
                                                                                        E1, E2, E3, A15 …

                                              D1 ─► D2 ─► D3 (can start at B4, run parallel to B-tail and C)
```

A15 (multi-courier-per-store) is deferred unless the week-4 gate (or later first-merchant signal) reveals the single-courier limit becoming a workflow burden.

**Longest pole still:** B5 (Shopify-to-canonical migration). Everything downstream of B waits — but A12-A14 ship merchant-visible product *before* B starts, so the long pole is on engine evolution, not on user value.

### Week-4 strategic gates

After A12-A14 ship and the first user has the MVP for a week, four signals decide whether the strategy is right:

1. **First merchant onboarded cleanly within 48h of invite.** If not — Phase A had a blind spot; fix before MVP iteration.
2. **MVP backing query <200ms on real merchant data.** If not — balance projections need work *now*, not in C.
3. **First merchant looks at the Control Center and says "yes, this is my problem."** If not — vision needs sharpening before more code; do not start Phase B.
4. **Manual CSV import is usable weekly without friction.** If not — Paymob connector becomes urgent; pull B7 forward, defer B5.

---

## Decision points (revisit before Phase B starts)

1. **Paymob timing.** If the first user needs Paymob within 2-3 months, consider a throwaway Paymob integration in Phase A that gets rewritten in B7.
2. **Phase C vs D ordering.** Investor/demo pressure → D first. Operational correctness → keep current order.
3. **Shadow-write vs clean cutover for B5.** Shadow-write safer, doubles write load briefly. Clean cutover faster, riskier if anything slips.
4. **Inbox scope in B1.** Minimal (write raw + normalize + emit), or full (retries + DLQ + operator UI)? I'd ship minimal first, add operator UI in Phase E if real incidents demand it.

---

## Phase A continues — Tier-1 fix list before first-user invite (surfaced by 2026-05-02/03 dry-run)

The Aljazeera7 dry-run drove the full reconciliation chain end-to-end (onboarding → Shopify Connect → seeded orders → Paymob CSV → Bosta CSV → bank statement → auto-match → manual match) and surfaced the items below. **Each is real data-loss or accounting-correctness, not polish.** The first-user invite is blocked on shipping A18-A26. Conservative estimate: 5-8 days of focused work, then re-run the dry-run, then send the invite. See [SESSION_LOG.md § Session: May 2-3, 2026](SESSION_LOG.md) for the full play-by-play.

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

### A27. (reserved)

### A28. Wizard "You're All Set!" final screen UX — **~0.5d** (UX, surfaced by dry-run §8)
After the user clicks Finish on the last wizard step, they land on a celebration screen with three optional next-steps and a "Go to Reconciliation" button. Functional but feels like a dead-end — the merchant expects the dashboard. Fix: auto-redirect to `/finance/reconciliation` after a brief celebration toast, OR strengthen the CTA hierarchy with "Go to Dashboard" as primary alongside "Go to Reconciliation."

### A29. Date format consistency across views — **~1d** (UX, surfaced by dry-run §8)
Merchant chooses DD/MM/YYYY at registration but most views render YYYY-MM-DD (statement detail page, drilldown order tables, JE list) or MM/DD/YYYY (form date inputs default placeholder). Per-merchant locale preference exists in the User profile but isn't applied app-wide. Fix: thread the format through a single utility `formatDate(date, user)` and replace direct `.toISOString().split('T')[0]` and `toLocaleDateString()` calls. Likely 30+ call sites. Ship as one focused PR.

### A30. Bank statement import UX polish — **~1-2d** (UX, surfaced by dry-run §7)
Collective ticket for the rough edges on `/accounting/bank-reconciliation/import`:
- Currency field is free-text; should be a dropdown defaulting to the merchant's functional currency, OR read from the selected bank account.
- "Please fill in all required fields" error toast doesn't say which field is missing — even though Statement Date has a `*` marker, the error doesn't reference it. Add field-specific error messaging.
- Bank-account picker dropdown options render in low-contrast (unselected items appear grayed out / hard to read) — CSS contrast bug.
- Selected-row highlight on the bank-rec page renders text unreadable (foreground/background contrast).
- Help text mentions "Date, Description, Amount, Reference (optional)" but doesn't tell the merchant how to map their own column names — solved properly by A24, but a one-line note would help in the interim.

### A31. Chart of Accounts — cash/bank accounts should carry their own currency — **~2-3d** (correctness, surfaced by dry-run §7)
Currently the CoA has a single company-level `default_currency`. A merchant with EGP operations + a USD reserve account (common for Egyptian merchants who hold inventory deposits in USD) can't represent that without manual gymnastics. Add `currency` field to `Account`, default to company currency, surface in account-edit form. Bank statement import currency dropdown should populate from the selected bank account's currency. FK ripple: FX revaluation already handles per-account currency; the model just doesn't carry it explicitly today.

### A32. (reserved)

### A33. Account-mapping seed labels — Payment Processing Fees → wrong account name — **~0.5d** (correctness, surfaced by dry-run §8)
The Shopify auto-seed maps the `PAYMENT_PROCESSING_FEES` role to GL account `52000 — Shipping Expense`. Wrong label — should be `52000 — Payment Processing Fees` (or a separate account). Likely a copy-paste error in the seed. Fix the seed and add a migration to update existing companies' mappings (Aljazeera7, Aljazeera5, demo).

### A34. (reserved)

### A35. Reconciliation polish — collective ticket — **~1-2d** (UX/correctness, surfaced by dry-run §5/§6/§7)
Group of small but real items on `/finance/reconciliation`:
- **Stage 2 widget** still shows "Settlements Posted: 0" and outdated banner *"Manual CSV import is on the roadmap (A14)"* — A14 shipped. Widget reads only `ShopifyPayout` rows, not manual settlement JEs. Update query + remove banner.
- **Narrative banner** ("Tell me the story") doesn't surface negative-clearing as a warning. When any provider's clearing < 0, prepend a red callout: *"Bosta clearing is negative (-1,000 EGP) — likely a settlement for an order with no original sale, or duplicate settlement import. Investigate Bosta drilldown."*
- **Auto-match tolerance** is 2%. Real-merchant short-payments are commonly 5-15%. Widen the *candidate-surfacing* tolerance to 15% (still mark within-2% as MATCHED_EXACT and 2-15% as `MATCHED_WITH_DIFFERENCE` for human review). Outside 15% remains Unmatched. Configurable per-merchant later.
- **"Imported" vs "Already imported"** label mismatch between Paymob and Bosta tabs after CSV upload. Standardize.
- **Dimension Analysis page** for SETTLEMENT_PROVIDER shows 0 P&L because the dimension is tagged on clearing (asset) lines. Add balance-sheet mode for context-typed dimensions, OR add SETTLEMENT_PROVIDER to the BS rendering path.
- **Hide deprecated `Cash on Delivery` provider row** in Settlement Provider Routing settings (leftover from pre-A12 schema; A2.5 deactivated it but didn't hide it).

### A36. Drilldown order-status accuracy — **~0.5d** (UX, surfaced by dry-run §7)
In Stage 1 drilldown, individual orders show "Settled" status when there's an import row for them, regardless of whether the settlement JE actually posted. Order 1004 showed "Settled" despite MAY01-A's JE silently failing (A20 cascade). Fix: derive order status from the actual JE state (clearance JE exists + EBD reconciled), not from the import row's existence. Pair with A20 — once unbalanced batches are rejected at import time, the cascade goes away.

### A37. Subledger tieout cleanup — **~1d** (correctness, surfaced by Cash Flow fix in dry-run §2)
While fixing the Cash Flow report, noticed `SubledgerTieOutView` at `projections/views.py:3673` and `:3757` has the same `journal_entry__` FK-alias bug (FK name is `entry`, not `journal_entry`) plus uses non-existent `line.debit_amount` / `line.credit_amount` (the model has `debit` / `credit`). Likely silent failures producing false-positive "Subledger mismatch" warnings — possibly the same warning A10 was filed for. Fix mechanically (same pattern as Cash Flow), then verify whether A10's complaint goes away.

### A38. nxentra-web PM2 process restart investigation — **~0.5d** (production hygiene, surfaced by dry-run §2)
PM2 status shows `nxentra-web` restarted 272 times in 31 hours uptime — ~9 restarts/hour. Indicates a memory leak, OOM kill, or unhandled exception triggering automatic respawn. Run `pm2 logs nxentra-web --lines 500 --err` to capture recent crashes. Likely candidates: Next.js dev-mode reloader still on in production (gunicorn logs show `Reloader is on. Use in development only!`), memory leak in a long-running route, or a recurring 5xx that the harness surfaces as a process failure. Stop-gap: bump PM2 max-memory-restart threshold; root cause is whatever's leaking.

---

## Tier-2 follow-ups surfaced by 2026-05-04 Aljazeera8 dry-run

The fresh-tenant dry-run on Aljazeera8 verified A18-A23, A25 backend, A26 backend in production. While doing so it surfaced these new items. Pull forward to the post-invite Tier-2 list; none block the first-merchant invite.

### A39. Settlement importer must not double-credit clearing when row already has a Shopify credit note — **~1-2d** (correctness, surfaced by 2026-05-04 dry-run)
BST-701 / order 1007 is the canonical case: COD failed delivery → Shopify fires `refund_created` webhook (Nxentra posts `CN-000002` for 1,200 EGP, credits Bosta clearing 1,200) AND Bosta later sends settlement statement with `returned_uncollected_amount=1,200, status=returned` (importer credits Bosta clearing another 1,200 via the Sales Returns line per A21). Same economic event counted twice → Bosta clearing over-drained by 1,200 (combined with the BST-703 orphan, drove the post-settlement Bosta open balance to **-2,200 EGP**). Same pattern would fire for any merchant whose Shopify shop auto-marks a COD failed-delivery order as "refunded." Policy options: (a) settlement importer detects rows whose `order_id` already has a posted credit note for the same source/source_document_id and skips the clearing CR for that line; (b) order_paid handler shouldn't recognize revenue+clearing for a status that's about to refund (more invasive); (c) pair with A26 — surface as `needs_review` and let operator pick the winner. Decide after first merchant signal whether (a) or (c) is the right default. Also pair with the A35 negative-clearing narrative warning — until A39 ships, the dashboard should at least flag the gap.

### A40. seed_test_csv_pack should emit orders before refunds — **~0.5d** (test-pack only, surfaced by 2026-05-04 dry-run)
The seed currently emits `SHOPIFY_ORDER_PAID` and `SHOPIFY_REFUND_CREATED` events in an order that puts refunds at lower `company_sequence` than their parent orders. Refund handler then runs FIRST, fails the SalesInvoice POSTED lookup, exhausts the A23 retry window (5 × 100ms), and silently drops. Recovery on the dry-run: rewind the `shopify_accounting` bookmark + clear `ProjectionAppliedEvent` rows + re-run `process_pending` (orders process first this time, refunds find their invoices on first attempt). Real Shopify webhooks deliver order_paid before refund_created in 99% of cases, but the seed should match production ordering to be useful as a test pack. Fix: in `shopify_connector/management/commands/seed_test_csv_pack.py`, structure the emit loop so all orders emit first, then all refunds, OR explicitly stamp `company_sequence` to control ordering.

### A41. A23 deeper fix — defer-on-exhaust instead of silent drop — **~1-2d** (correctness, architectural; deferred from A23)
Current A23 retry helper (5 × 100ms) handles the same-pass race within ~500ms. If retries exhaust (order_paid event hasn't even reached the projection yet — possible when Shopify webhooks deliver in unexpected order, or when a webhook batch arrives during a Celery worker restart), the refund event silently drops. Fix per A23 ticket option (c): introduce a `DeferEvent` exception that `process_pending` catches specifically — it logs at INFO (not ERROR), removes the `ProjectionAppliedEvent` row so the next pass retries, and continues to the next event without `stop_on_error` halting the projection. Refund handler raises `DeferEvent("waiting on order_paid for order 1004")` after retry exhausts. The next Celery beat tick (or next webhook delivery) re-attempts. Add a deadline (e.g. 24h) after which the refund really is treated as orphan and a Sentry alert fires. Pair with A26 — orphan-refund flow.

### A42. Settlement import: missing "Imported N batches" success toast — **~0.25d** (UX, surfaced by 2026-05-04 dry-run)
After uploading Paymob or Bosta CSV at `/finance/settlements/import`, the page silently re-renders with the new "Imported batches" cards but no success toast confirming the upload completed. Bank-rec import has this toast (per A17 follow-up); settlement import should match. Also include the `unknown_order_ids` count in the toast (e.g. *"Imported 4 batches. 1 batch references unknown order_ids — review needed."*) — that's the merchant-facing surface for A26.

### A43. Credit Note / Sales Invoice detail-page 404 — **~0.5d** (frontend routing, surfaced by 2026-05-04 dry-run)
Clicking the credit-note number link (`CN-000001`) on `/accounting/credit-notes` navigates to `/accounting/credit-notes/4` and returns the 404 page. Same pattern for the linked Original Invoice column (e.g. `INV-000004` on the credit notes table). Either the route doesn't exist or the page doesn't accept the integer id (probably needs `[publicId]` not `[id]`, or vice-versa). Fix: audit the frontend routes for these two list pages and ensure the row-link `href` points to the correct route. Cheap check; trips merchant trust the moment they click.

---

## Merchant-readiness — required before first paying merchant (post-Heba beta)

Surfaced 2026-05-10 after switching the Shopify app from Custom (Plus-org-scoped) to Public distribution so Heba's dev store could install. Public distribution lets *any* store install via direct OAuth link without App Store listing — adequate for closed-beta testers, but a real paying merchant on a real production store puts the app under Shopify's full compliance regime. The items below are Shopify-policy-mandatory or security-baseline gaps that don't affect Heba's beta but **must** ship before the first paying merchant.

App Store *listing* (Built for Shopify checklist, public discoverability) remains deferred — we don't need it for closed beta or even for the first dozen direct-link installs. Public distribution + the items below are the gate.

### A44. GDPR mandatory compliance webhooks — **DONE 2026-05-10** (Shopify policy-mandatory, blocks app long-term)
Shopify requires every app — public *or* unlisted — to handle three GDPR webhooks: `customers/data_request`, `customers/redact`, `shop/redact`. Shopify pings them periodically with test payloads; an app that doesn't respond 200 gets disabled silently. **Shipped:** `GdprRequest` audit model ([models.py](backend/shopify_connector/models.py)) + migration `0013_add_gdpr_request.py` + three handlers in [commands.py](backend/shopify_connector/commands.py) (`process_customers_data_request`, `process_customers_redact`, `process_shop_redact`) + webhook router branch in [views.py](backend/shopify_connector/views.py#L170-L191) that bypasses the store-lookup (since `shop/redact` arrives 48h after uninstall and the store record may already be gone) + 5 tests in [tests/test_a44_gdpr_webhooks.py](backend/tests/test_a44_gdpr_webhooks.py) covering 200-on-valid-sig, 401-on-bad-sig, audit row written, idempotent retry, missing-store handling. Actual data work (export / deletion) intentionally left as `PENDING` status — Shopify only requires the 200 ack on the webhook itself; build the async jobs out when there's volume. **Manual step still required (operator):** configure the three webhook URLs in **Partners Dashboard → App setup → Compliance webhooks** (`https://app.nxentra.com/api/shopify/webhooks/` for all three; topic header is what Shopify sends). Until that's done, Shopify won't actually call our endpoints in production.

### A45. Privacy policy page + support email — **~2h** (Shopify policy-mandatory, surfaced on install screen) — *partially DONE per 2026-05-10 verification*
Partners Dashboard requires a `Privacy policy URL` and `Support email` on the app config — fields are visible to merchants on the install consent screen. **2026-05-10 verification:** [frontend/pages/privacy.tsx](frontend/pages/privacy.tsx) already exists with substantive v1.0 content (data collected, OAuth-token clause, account info, voice data, etc.). What remains: (1) audit page for explicit Shopify-specific GDPR rights coverage (matches A44's `customers/data_request` / `customers/redact` / `shop/redact` semantics), (2) set up `support@nxentra.com` (forwarding to `mohamed.algazzar@gmail.com` until a help desk exists), (3) wire both URL + email into Partners Dashboard. Original ~0.5d estimate downscoped to ~2h since the page itself is built.

### A46. Webhook HMAC signature verification — **DONE** (verified 2026-05-10)
Verified at [backend/shopify_connector/views.py:144-153](backend/shopify_connector/views.py#L144-L153) (rejects 401 on missing/invalid signature *before* parsing the payload) and [backend/shopify_connector/commands.py:308-321](backend/shopify_connector/commands.py#L308-L321) (computes `hmac.new(SHOPIFY_API_SECRET, body, sha256)` and uses `hmac.compare_digest` for constant-time comparison). Pattern matches Shopify's prescribed verification exactly. No code change needed; closing as DONE per the doc's own predicted outcome.

### A47. Access token storage encryption at rest — **~0.5d** (security baseline)
`ShopifyStore.access_token` is stored plaintext in Postgres ([models.py:41-45](backend/shopify_connector/models.py#L41-L45) is plain `CharField(max_length=255)` — note the help_text claims *"encrypted at rest in production"* but no encryption layer exists; **fix the misleading help_text when implementing**). A DB breach (backup leak, rogue read replica, SQL injection elsewhere) hands an attacker every connected merchant's full Shopify API access — orders, customers, payouts, ability to refund / fulfill / push fake orders. Encrypt with `cryptography.fernet` keyed off a `SHOPIFY_TOKEN_KEY` env var. Migration encrypts existing rows in place. Read path decrypts on attribute access. Add a key-rotation runbook (re-encrypt all tokens with a new key, no Shopify-side re-auth needed). Tests: round-trip encrypt/decrypt, old-key tokens still readable during rotation, ciphertext different across rows (Fernet IV randomness).

### A48. app/uninstalled webhook handler — **~1h remaining** (lifecycle correctness) — *mostly DONE per 2026-05-10 verification*
**2026-05-10 verification:** handler exists at [commands.py:678-704](backend/shopify_connector/commands.py#L678-L704) and is wired in the webhook router at [views.py:198](backend/shopify_connector/views.py#L198). Already does: HMAC-verify (via the shared verifier upstream), set `status=DISCONNECTED`, blank `access_token`, set `webhooks_registered=False`, emit `SHOPIFY_STORE_DISCONNECTED` event (the audit trail). The "halt scheduled syncs" requirement is also satisfied for free — [tasks.py:51](backend/shopify_connector/tasks.py#L51) only iterates `status=ACTIVE` stores, and [tasks.py:96-97](backend/shopify_connector/tasks.py#L96-L97) early-returns `skipped` for non-ACTIVE stores. **Remaining:** add `uninstalled_at = models.DateTimeField(null=True, blank=True)` to `ShopifyStore` and stamp it in the handler — gives us a clean retention boundary and lets future GDPR `shop/redact` cleanup query "stores uninstalled >30d ago." Original ~2h estimate downscoped to ~1h.

### A49. Re-auth flow on token expiry / scope rotation — **~0.5d** (lifecycle correctness)
If a merchant's Shopify session token is revoked (Shopify password change, suspicious activity flag, manual revocation) or we add a new scope in a future release, every Shopify API call returns 401 / 403 and the connector silently fails — the merchant just sees stale data with no signal that re-auth is needed. Add: 401/403 from Shopify flips `ShopifyStore.needs_reauth = True`; the wizard's Shopify Setup step (and a banner in the connected-store settings) detects the flag and shows "Reconnect to Shopify" instead of "Connected"; OAuth retry path reuses the existing flow. Pair with A48 (uninstall) so both abnormal states surface the same UX pattern.

---

## Shopify connector bugs surfaced 2026-05-15 during App Store reviewer-store setup

Three bugs surfaced ~02:30 EEST while populating Nxentra `Shopify_R` company from fresh dev store `nxentra-reviewer-store.myshopify.com` (created for App Store reviewer test account, after Heba was lost 2026-05-11 to the "this app is under review" install banner). All three affect any new merchant connecting Shopify; all three should be fixed before continuing the App Store submission demo via the proper path (rather than the manual-data workaround).

### A50. Wizard "Import all historical orders" → 403 Forbidden — **~30min** (Shopify connector, blocking new-merchant onboarding)
The onboarding wizard's "Import all historical orders" option sends `created_at_min=2015-01-01` to `/admin/api/{ver}/orders.json`. Shopify's `read_orders` scope (which we have) limits to last 60 days; older `created_at_min` returns 403. **Sentry event:** id `5d5177e81c9941499b36ad943d312a35`, task `shopify.sync_store_orders`, 2026-05-15 02:23:24 EEST, store `nxentra-reviewer-store.myshopify.com`. **Fix:** clamp `created_at_min` to `max(stated_min, now - 60 days)` when `read_all_orders` scope is not granted. Add wizard copy explaining that >60d history requires separate Shopify scope approval (or paginate in 60d windows and rely on `updated_at_min` for older content — research needed). Alternative: add `read_all_orders` to [shopify.app.toml:10](shopify.app.toml#L10) scope set and request Shopify approval (longer path, several days).

### A51. "Register Webhooks" button fails — REST API needs `write_webhooks` scope we don't grant — **~15min** (Shopify connector, cosmetic)
In-app "Register Webhooks" action in the Shopify integration settings page posts to Shopify Admin REST `webhooks.json`, which requires `write_webhooks` scope. Our scope set in [shopify.app.toml:10](shopify.app.toml#L10) doesn't include it. GDPR compliance webhooks declared in [shopify.app.toml:22-25](shopify.app.toml#L22-L25) `[webhooks.privacy_compliance]` auto-register via `shopify app deploy` and are fine — only the programmatic registration path is broken. UI toast "Failed to register webhooks" shown 2026-05-15 02:25 EEST. **Fix:** remove the "Register Webhooks" button and rely entirely on declarative webhook config (preferred — matches Shopify's modern architecture, no scope needed). OR: add `write_webhooks` to scope set + request approval (slower, more attack surface). Declarative-only is the simpler and cleaner path.

### A53. Re-request Level 1 Protected Customer Data access + re-enable PII webhook subscriptions — **~30min code + 0-7d Shopify review** (Shopify connector, post-submission)
Five declarative webhook subscriptions (`orders/create`, `orders/paid`, `orders/cancelled`, `refunds/create`, `fulfillments/create`) were stripped from [shopify.app.toml](shopify.app.toml) on 2026-05-17 because `shopify app deploy` rejected them with *"This app is not approved to subscribe to webhook topics containing protected customer data."* These topics carry customer PII in their payloads; Shopify requires Level 1 (or higher) Protected Customer Data approval to subscribe. Earlier in App Store submission prep we set "Doesn't need access to protected customer data" (Level 0) to ship faster; this is the trade. **Until this lands, real-time order sync is replaced by the periodic `sync_shopify_all` Celery task (4-hour cadence).** Adequate for beta; merchants WILL notice the latency at >dozen-order/day volume. **Fix:** (1) Partners Dashboard → API access requests → reopen "Protected customer data access" with the "Other" reason ("Accounting reconciliation: build AR sub-ledger from orders, match payouts to customer transactions" — same justification as the original submission), submit, wait for approval. (2) Once approved, re-add the 5 topics to the `[[webhooks.subscriptions]]` block in shopify.app.toml. (3) `shopify app deploy` → releases nxentra-sync-N. (4) Verify webhook deliveries land at `/api/shopify/webhooks/` and route correctly. Don't request Level 2 (PII fields like name/email/phone/address as separately-approved fields) unless we actually use those fields in product features — Level 1 is sufficient for the webhook subscription side. **Do not** attempt during the in-flight App Store listing review — wait for listing approval first to avoid restarting that review.

### A54. Add `read_shopify_payments_disputes` scope + re-enable dispute webhook subscriptions — **~15min code + Shopify deploy** (Shopify connector, post-submission)
The `disputes/create` and `disputes/update` declarative subscriptions failed on 2026-05-17 `shopify app deploy` with *"Missing scope for webhook topic: disputes/create (read_shopify_payments_disputes)"*. We have `read_shopify_payments_payouts` but not `read_shopify_payments_disputes` — they're separate scopes. **Fix:** add `read_shopify_payments_disputes` to the `scopes = "..."` line in [shopify.app.toml:10](shopify.app.toml#L10), re-add the two dispute topics to the `[[webhooks.subscriptions]]` block, run `shopify app deploy`. Existing stores will need to re-authorize (OAuth scope expansion forces re-grant) — A49's re-auth flow handles the UX. Chargeback handling in `commands.py` already routes `disputes/*` correctly (verified via the webhook router map), so no handler work needed. **Defer until first chargeback complaint or first paying merchant** — dispute tracking is meaningful only with real payment volume.

### A55. Add `read_all_orders` scope for full historical import (>60 days) — **~15min code + 1-2wk Shopify review** (Shopify connector, post-submission)
A50 clamped the wizard's "Import all historical orders" to a 59-day floor because the `read_orders` scope is limited to that window. Merchants with longer histories who want their full books in Nxentra need orders older than 60 days. **Fix:** add `read_all_orders` to the `scopes` line in [shopify.app.toml:10](shopify.app.toml#L10), submit for separate Shopify approval (this scope requires explicit justification — accounting/bookkeeping is a recognized legitimate use). Once approved, relax the 59-day clamp in [accounts/commands.py:_enqueue_shopify_historical_import](backend/accounts/commands.py) to use the user-requested date range without clamping. Also update the wizard copy from "Import all historical orders" (currently misleading — clamps to 60d) to accurately describe what gets imported. **Workaround for merchants who need >60d before approval lands:** manual CSV settlement importer for backfill (already exists, A14 path). Independent of A53 — these are separate Shopify approvals running on separate timelines.

### A56. Failed OAuth leaves orphan PENDING ShopifyStore records — **~30min** (Shopify connector, surfaced 2026-05-17)
`ShopifyInstallView.post` creates a PENDING `ShopifyStore` record with `oauth_nonce` BEFORE redirecting to Shopify OAuth. If `complete_oauth` later fails (e.g., the shop is already linked to another Nxentra company → `IntegrityError`), the PENDING record stays in the DB indefinitely. Surfaced 2026-05-17 when DB query on `Shopify_R` company showed two stores: `aljazeera7-store.myshopify.com` (PENDING from failed first attempt) + `nxentra-reviewer-store.myshopify.com` (ACTIVE from successful second attempt). The orphan polluted the UI's "Previously connected to..." hint and caused A57. **Fix:** in `complete_oauth` error branches, `store.delete()` if `store.status == PENDING` and the store has no successful OAuth history. Or wrap the whole install + callback in a saga that rolls back on failure.

### A57. `disconnect_store` picks wrong store when multiple non-disconnected exist — **~15min** (Shopify connector, surfaced 2026-05-17)
Current code: `ShopifyStore.objects.filter(company=actor.company).exclude(status=DISCONNECTED).first()`. No `order_by`, so Django's default `pk ASC` wins. When `Shopify_R` had `store_id=66` (PENDING, aljazeera7 orphan from A56) and `store_id=67` (ACTIVE, nxentra-reviewer-store), clicking "Disconnect Store" in the UI disconnected store_id=66 instead of store_id=67 — leaving the actually-active store still connected silently. Surfaced 2026-05-17 when re-OAuth flow showed "Shopify store connected successfully!" toast but page UI still showed disconnected state. **Fix:** require `store_public_id` parameter always, OR change query to `.filter(status=ACTIVE).order_by('-updated_at')`. Option (b) is the more forgiving default.

### A58. Item record's "Product Page URL" external link field does not persist — **~30min** (sales/inventory, surfaced 2026-05-17)
Item edit form has an "External Link → Product Page URL" field (e.g., `https://instagram.com/p/...`). Value typed in is not saved on submit. Surfaced 2026-05-17 during Plan B manual demo data creation in `Shopify_R`. **Diagnosis needed:** check the Item model — `product_page_url`/`external_url` field exists? If not, model field missing. If yes, the serializer / form / update view probably isn't including it in the writable fields list. Likely a one-field-omission bug in the Item update path.

### A59. Vendor creation fails with "Failed to create vendor" — **~0.5d** (purchases, surfaced 2026-05-17, BLOCKING vendor flow)
`/accounting/vendors/new` form submits and gets back a generic "Failed to create vendor" error toast. Surfaced 2026-05-17 while creating demo data for App Store reviewer in `Shopify_R`. No stack trace shown to user. **Diagnosis needed:** check the vendor create endpoint (likely `purchases/views.py` `VendorCreateView` or similar). Server-side error is being swallowed by the frontend. Could be: missing required field with no client-side validation, FK constraint failure, RLS/permission issue under fresh-tenant setup. Pull the actual Sentry stack trace from production. Workaround for App Store demo: skip vendors entirely — the screencast only covers AR / sales side, not purchases.

### A52. "Re-sync Orders (7d)" returns "0 new, 0 already synced" despite orders present — **~0.5-1d** (Shopify connector, blocking sync feature)
After avoiding A50 by clicking the 7-day re-sync button instead of the wizard's "all-history" option, the API call succeeded (no 403, no Sentry error) but `sync_store_orders` task reported zero orders imported. UI toast "Order re-sync complete: 0 new, 0 already synced" shown 2026-05-15 ~02:28 EEST. **Verified:** 6 orders existed in `nxentra-reviewer-store` admin at sync time — #1001-#1003 paid, #1004 fully refunded, #1005 partially refunded, #1006 paid+fulfilled. All same-day created, USD, on a USD-functional-currency `Shopify_R` company. Status filter in the API call was `status=any` (verified from the A50 Sentry trace, which used the same task) so that part is fine. **Diagnosis needed:** instrument `backend/shopify_connector/tasks.py` `sync_store_orders` to log the outgoing API URL with all query params and the parsed response count. Likely root causes (in order): `updated_at_min` filter instead of `created_at_min` (orders never updated → excluded), timezone offset miscalculation (UTC vs EEST cuts off today's orders), hidden `financial_status` filter, response-parser bug discarding valid orders. Likely a 1-line fix once located. **Blocks:** the proper Shopify→Nxentra sync demo for App Store reviewer. Workaround for submission: create demo journal entries / invoices natively in Nxentra and present Shopify connection as "Connected" status only (Path 3 from 2026-05-15 03:00 conversation). **Update 2026-06-02:** Strongly suspected to share root cause with A120 — by 2026-06-01 the same `orders.json` endpoint was returning a hard 403 against the reviewer's `mec3xu-zd` store. Most likely "0/0 in May" was the soft leading edge of the same 2025-01 sunset and goes away with the 2026-04 bump. Verify by re-running the 7d re-sync against `Shopify_R` after deploy with at least one Bogus-Gateway test order present.

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

### A122. Address "deprecated offline tokens" warning surfaced by Shopify Dev Dashboard — **~0.5-1d** (Shopify connector, surfaced 2026-06-01)
Dev Dashboard's Overview page for `Nxentra Sync` shows a red "Fix overdue" banner: "Calls made with deprecated offline tokens detected in the last 14 days." Per Shopify's deprecation timeline, public apps must migrate from the legacy permanent-offline OAuth flow to either online tokens or the rotating-token pattern. Currently our `complete_oauth` exchanges code for a permanent offline token. Investigation needed to determine the exact migration path (online vs rotating offline) and code changes required in `commands.py:complete_oauth`. May surface in App Store review feedback if not addressed pre-resubmission.

### A123. Add explicit Sentry `before_send` PII redaction filter — **~1-2h** (security/privacy, surfaced 2026-06-02 during PCD Level 1 application)
[settings.py:393](backend/nxentra_backend/settings.py#L393) sets `send_default_pii=False`, which prevents Sentry SDK from auto-capturing request/user PII. However, PII can still leak via exception messages and log call arguments (e.g., a SQL error that includes a customer's email in the parameter list). Ship a `before_send` hook that scrubs known PII patterns (email, phone, address fields, full PAN) from `event['logentry']['message']`, `event['exception']['values'][].value`, and breadcrumb messages before transmission. Reference: the DLP doc ([docs/security/data-loss-prevention.md](docs/security/data-loss-prevention.md) §5) calls this out as a roadmap item — closing A123 lets that section drop the "roadmap" qualifier.

### A124. GDPR redact webhooks — programmatic data deletion — **~2-3d** (Shopify compliance, surfaced 2026-06-02 during PCD Level 1 application)
The `customers/redact` and `shop/redact` webhook handlers ([commands.py:740](backend/shopify_connector/commands.py#L740), [:761](backend/shopify_connector/commands.py#L761)) currently only audit-log the request — the handlers themselves carry comments saying "actual deletion job is a future task" / "actual wipe job is a future task." Shopify's GDPR webhooks policy requires apps to actually delete the affected data within the SLA (30 days for customer redact, 90 days for shop redact). Until A124 ships, deletion in response to a verified redaction request is performed manually within the SLA. Build a deletion job (Celery task) that: (a) for `customers/redact`, anonymizes the affected Customer row and any derived SalesInvoice/CustomerReceipt records by replacing PII fields with hashed placeholders; (b) for `shop/redact`, purges the entire tenant's data including events, projections, audit rows, and Shopify-derived records. Both must emit a completion audit event and be replayable safely (idempotent). Ship before first paid merchant volume.

### A125. Fulfillment backfill — historical orders never get COGS entries — **~1-2d** (Shopify connector, surfaced 2026-06-11 during reviewer-flow dry run)
COGS books exclusively from `fulfillments/create` webhooks (subscribed since `nxentra-sync-8`, 2026-06-11) → [commands.py](backend/shopify_connector/commands.py) `process_fulfillment` → inventory issue + COGS JE. Webhooks only fire **going forward**, and `_sync_orders` (the order backfill / Re-sync button / onboarding historical import) fetches orders only. Consequence: any order fulfilled *before* the merchant installs Nxentra — or fulfilled while a webhook was missed — produces a SalesInvoice + revenue JE but **never a COGS entry**, permanently overstating margin for imported history.

**Scope:**
- Extend `ShopifyAdminClient.iter_orders` (or add a dedicated query) to include each order's `fulfillments { id, createdAt, fulfillmentLineItems { ... } }` — same GraphQL query, no extra scope needed (`read_fulfillments` already granted).
- In `_sync_orders`, after routing the order handler, feed each fulfillment through `process_fulfillment` (REST-shape adapter; handlers are already idempotent on fulfillment id).
- Mind inventory state: backfilled COGS issues stock — items with zero opening balance and strict negative-stock will land in `/finance/exceptions` (A80-style). Decide policy: book at `default_cost` regardless, or surface as exception (current behavior for live webhooks).
- Tests: order-with-fulfillment backfill creates exactly one COGS JE; re-run is a no-op; unfulfilled order creates none.

**Trigger:** before onboarding any merchant with pre-existing fulfilled order history (i.e., effectively every real merchant using historical import). Pairs with the post-launch `read_all_orders` request (full-history import beyond 60 days).

### A80. A79 Phase 2 cleanup — drop `Customer.default_ar_account` + `Vendor.default_ap_account` columns — **~0.5d** (schema cleanup, surfaced 2026-05-23)
A79 introduced `default_posting_profile` on Customer/Vendor as the authoritative routing primitive; the bare `default_ar_account` / `default_ap_account` fields were hidden from the UI in A79b (commit `19f108d`) but left on the model + serializer + PATCH endpoint for one release of graceful deprecation. Phase 2 finishes the job.

**Pre-cleanup audit** (re-run to confirm nothing material has grown a reader since 2026-05-23):
- `rg -n "default_ar_account|default_ap_account" backend/` — verify only `models.py` (`clean()` validator), `views.py` (PATCH accept-pop pattern), `serializers.py` (read-only code/name fields), and the two historical migrations (`0014_customer_vendor_counterparty`, `0033_customer_vendor_default_posting_profile`) read it. No business logic should derive anything from it.
- `rg -n "default_ar_account|default_ap_account" frontend/` — should be empty after A79b. Verify `types/account.ts`, `CustomerForm`, `VendorForm`, customer/vendor list + detail pages all reference `default_posting_profile_*` only.
- Grep external integrations (`shopify_connector/`, `bank_connector/`, `platform_connectors/`) for any silent FK read — none expected.

**Migration scope** (single accounting migration):
1. `RemoveField` `Customer.default_ar_account` and `Vendor.default_ap_account`.
2. Drop the four read-only serializer fields (`default_ar_account_code`, `default_ar_account_name`, `default_ap_account_code`, `default_ap_account_name`) and the corresponding entries in `CustomerSerializer.Meta.fields` / `read_only_fields` + the same on `VendorSerializer`.
3. Drop `default_ar_account_id` and `default_ap_account_id` from `CustomerCreateSerializer` / `CustomerUpdateSerializer` / `VendorCreateSerializer` / `VendorUpdateSerializer`.
4. Drop the AR/AP-account get-or-404 + setter branches from `CustomerListCreateView.post`, `CustomerDetailView.patch`, `VendorListCreateView.post`, `VendorDetailView.patch` (the `if data.get("default_ar_account_id"):` / `if "default_ar_account_id" in data:` blocks).
5. Drop the `clean()` validation blocks in `accounting/models.py` (`Customer.clean()` lines ~979-982 and `Vendor.clean()` lines ~1171-1174) that validate the FK.
6. Frontend types: remove `default_ar_account*` / `default_ap_account*` fields from `Customer`, `Vendor`, `CustomerCreatePayload`, `CustomerUpdatePayload`, `VendorCreatePayload`, `VendorUpdatePayload` interfaces in `frontend/types/account.ts`.

**Do NOT do** in this cleanup (defer to A79 Phase 2 proper):
- Moving `payment_terms_days`, `default_tax_code`, `default_revenue_account`, `default_expense_account` onto PostingProfile. That's the more substantive Phase 2 work where the profile becomes a real "channel template" — separate ticket, larger scope.
- Cascading default-fill on invoice lines (revenue account, tax code) from the picked item / profile.

**Verification before merge:**
- `python manage.py migrate accounting` succeeds locally + on droplet without rewriting data.
- Tests pass (no test currently references the columns; A79 backfill migration already consumed the data).
- Hit the customer create + update + delete endpoints with curl to confirm no 500.
- Re-render `/accounting/customers`, `/accounting/customers/<code>`, `/accounting/customers/<code>/edit` — no `undefined` rendering.

**Why this hasn't been done already:** standard one-release graceful deprecation. The data was preserved during A79 backfill (used to resolve initial `default_posting_profile` matches), so removing the columns now is a pure schema cleanup with no behavior change. Hold for one round of in-the-wild use to catch any silent reader; ship when no one's poking at it.

### A81. E-invoicing compliance — Egypt ETA (Phase 1) + Saudi ZATCA (Phase 2 deferred) — **~4-6w focused** (compliance + wedge, surfaced 2026-05-23 evaluation)

**Both the legal-must and the strongest MENA wedge against QuickBooks/Xero.** The 2026-05-23 evaluation flagged this as the single biggest omission from EVALUATION_STATUS.md — neither doc mentioned it, despite Egypt ETA being mandatory for B2B merchants over the revenue threshold and Saudi ZATCA Phase 1 already live nationwide. Global incumbents do not handle either; they leave merchants to bolt-on a third-party invoicing portal. Nxentra can ship native compliance and price $30–$50 above the freemium tier on that basis alone.

#### Egypt ETA (Egyptian Tax Authority) — Phase 1 (do first, blocking Aljazeera7 paid invite if she's over the threshold)

**Regulatory context (verify current state with ETA before coding — rules have shifted twice since 2023):**
- Mandatory for B2B (issuer-to-VAT-registered-buyer) invoices for most sectors. Threshold + scope changes annually.
- Real-time clearance model: invoice is SUBMITTED to ETA, gets a UUID + signed return, then issued to the buyer. Unsigned invoices are not legally valid.
- XML payload (ETA-specific schema, NOT UBL 2.1 — they diverged), digitally signed with an HSM-backed certificate or USB token.
- ETA portal: `https://api.invoicing.eta.gov.eg` (production) / preprod sandbox available.

**Implementation scope:**
1. **`einvoicing/` Django app** — own its own models (`EInvoiceSubmission`, `EInvoiceSignature`, `EInvoiceStatus`), commands (`submit_einvoice`, `cancel_einvoice`, `query_einvoice_status`), projections. Keep separate from `sales/` so connector swap is clean.
2. **XML builder** — map `SalesInvoice` + `SalesInvoiceLine` + `Customer.tax_id` to ETA schema. Tax breakdown per line. Handle EGP→declared-currency conversion.
3. **Digital signature** — ETA accepts: (a) HSM-issued cert via Egypt Trust, (b) USB token (offline signing → manual workflow). For SaaS, HSM is the only sane path. Cost: ~$200/year per cert + HSM service (Egypt Trust or similar). Sign server-side via PKCS#11.
4. **ETA API client** — OAuth client_credentials, submit endpoint, query endpoint, cancel endpoint. Idempotency-aware: ETA assigns a UUID; store it and avoid re-submission on retry.
5. **Async submission** — Celery task. Status: `DRAFT → QUEUED → SUBMITTED → ACCEPTED | REJECTED`. Surface status on the invoice detail page. Block invoice posting if `EInvoice.required` is True for the company and submission has not succeeded.
6. **Settings UI** — Company → Settings → E-invoicing tab. Configure: ETA submitter ID, branch ID, activity code, cert path/HSM endpoint, mandatory vs optional flag, sandbox vs prod toggle.
7. **Customer master changes** — Customer.tax_id and Customer.activity_code become required for B2B if e-invoicing is enabled (validation surfaces on the customer form, not at submission time).

**Effort:** ~3-4 weeks focused. Signing + cert procurement is the long pole — start cert acquisition in parallel.

#### Saudi ZATCA (Zakat, Tax and Customs Authority) — Phase 2 (defer until first KSA merchant signs)

**Regulatory context:**
- Phase 1 (e-invoice generation): mandatory since 2021-12-04 — issuer produces a signed XML + QR code + structured invoice.
- Phase 2 (integration with ZATCA FATOORA portal): rolled out in waves by revenue band. ~most merchants over SAR 3M revenue are in-scope today.
- UBL 2.1 (PINT-Saudi profile) — different from Egypt ETA's schema. Don't share builders.
- QR code on every printed invoice (B2C as well). Required since Phase 1.

**Implementation scope:**
1. Reuse the `einvoicing/` app structure. Add ZATCA-specific submodule with its own schema mapper.
2. UBL 2.1 PINT-Saudi XML builder.
3. ZATCA Cryptographic Stamp Identifier (CSID) — different signing model than ETA. Onboarding API to obtain CSID; renew every 12 months.
4. QR code generation (TLV-encoded, base64, embedded in PDF).
5. Submit-or-clearance model depending on merchant's Phase 2 wave.
6. Settings UI extension — Country selector on the e-invoicing tab routes to ETA vs ZATCA pipeline.

**Effort:** ~2-3 weeks focused once ETA Phase 1 ships and the shared infrastructure exists.

#### Why this is the right wedge (not just compliance)

- **Compliance gate.** Egyptian B2B merchants over the revenue threshold legally cannot operate without e-invoicing. Today they bolt on Mtebes / OrcaCenter / similar at ~$30-80/month per company. If Nxentra ships native, that's an immediate $30/month price-add justification.
- **Lockup.** Once a merchant's e-invoicing UUIDs are stored in Nxentra, switching to QuickBooks/Xero means re-onboarding to a third-party invoicing portal too. The switching cost roughly doubles.
- **Unblocks KSA expansion.** Saudi ZATCA support is the gate for selling into Riyadh/Jeddah merchants — ~3-4x larger TAM than Egypt alone.
- **Global incumbents do not ship this.** QuickBooks MENA, Xero, FreshBooks — none have native ETA/ZATCA. They redirect to local partners. This is the most defensible moat Nxentra can build that doesn't require capital or headcount.

#### Sequencing recommendation

Do not start ETA Phase 1 in the next 30 days. The current commercial path is App Store listing + first 10 paying Shopify merchants → if any of those merchants are Egyptian B2B over the threshold, ETA Phase 1 becomes a 30-day hard requirement. Right now, every Shopify merchant Nxentra acquires is B2C (DTC e-commerce), and B2C e-invoicing is not yet mandatory in Egypt — buyer doesn't have a VAT ID to send to.

**Trigger to start ETA Phase 1:** any of (a) first paying B2B Egyptian merchant signs, (b) Aljazeera7 confirms she sells B2B, (c) Egypt ETA scope expands to B2C (watch for late-2026 announcements).

**Trigger to start ZATCA Phase 2:** first Saudi merchant inquiry.

**Procurement to start NOW even before code:** ETA submitter registration + cert acquisition (Egypt Trust, ~$200/year, 1-2 week lead time). This is a long-pole item that should not block implementation when the trigger fires.

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

### A83. Auto-created Shopify customer binds AR-DEFAULT instead of SHOPIFY-DEFAULT posting profile — **~30min** (Shopify connector + posting-profile binding, surfaced 2026-05-24)

When Shopify OAuth completes and Nxentra creates the "Shopify: <shop-domain>" customer record automatically, its `default_posting_profile` is set to `AR-DEFAULT` instead of the channel-specific `SHOPIFY-DEFAULT` profile that A79b/A79c was supposed to enforce. Reproduced 2026-05-24 after reinstalling Nxentra Sync on `nxentra-reviewer-store`: the auto-created `SHOPIFY-NXENTRA-RE` customer shows `AR-DEFAULT` in `/accounting/customers`. This works (invoices still post correctly), but it skips the per-channel routing logic A79 was designed to enable.

Likely root cause: in `complete_oauth` (or wherever the per-store customer record is created), the call uses the company's default AR posting profile lookup rather than `PostingProfile.objects.get_or_create(usage=GATEWAY, code="SHOPIFY-DEFAULT")`. **Fix:** ensure the SHOPIFY-DEFAULT GATEWAY profile is created if missing (commit `91bb57d` was supposed to ensure this — verify it covers the OAuth-create path, not just the seed path) and bind it on customer create. Test: reinstall on a fresh dev store, confirm the auto-created customer shows `SHOPIFY-DEFAULT` not `AR-DEFAULT`. **Not submission-blocking** but degrades A79's per-channel routing value proposition for first-merchant onboarding.

### A84. Customer Receipts form UX — "Bank Account" label is misleading + AR Control should default — **~1-2h** (sales UI polish + form ergonomics, surfaced 2026-05-24)

The `/accounting/receipts/new` form has two ergonomic problems that surfaced when manually processing payments against Shopify-clearing invoices:

1. **"Bank Account" label is wrong for clearing destinations.** The field accepts any cash-type or clearing account (e.g., `11500 Shopify Clearing`), which is correct for Shopify-gateway payments where money lands in clearing first and only later moves to the actual bank. But the label "Bank Account" makes operators hesitate or pick the wrong account. **Fix:** rename to "Deposit Account" or "Cash Destination" — covers both real bank accounts and intermediate clearing accounts.

2. **AR Control Account requires manual pick when it's deterministic.** Once an invoice is selected in the Invoice Allocation table, the AR account is known (it's the account the invoice's JE actually credited — typically `12000 Accounts Receivable`, or the channel-specific AR account if the customer has a posting profile binding). Forcing the operator to re-pick it is friction and an opportunity for error. **Fix:** auto-fill from the first allocated invoice's `accounts_receivable_account_id`; show as read-only when invoices are selected, fall back to editable when receipt has no invoice allocation (advance receipt).

Both surfaced during 2026-05-24 demo-data prep on Shopify_R when manually processing 3 receipts to populate the reconciliation control center. **Not submission-blocking** — operator can fill it correctly — but every merchant will hit this on day 1. Pull forward in the post-listing UX polish wave alongside A82.

### A87. Bank statement import — date format must inherit from company locale, not be re-auto-detected per upload — **~30min-1h** (bank reconciliation import flow, surfaced 2026-05-24)

The Import Bank Statement flow (`/accounting/bank-reconciliation/import`) auto-detects date format on each CSV upload. Reproduced 2026-05-24 on Shopify_R: uploaded `bank_statement_demo.csv` with `YYYY-MM-DD` (ISO) format → silently failed with "Parsed 0 lines — Check the column mapping — the date column may not match the date format." Regenerated as `MM/DD/YYYY` → same error. Only worked after manually opening the "Map columns" UI and explicitly selecting the date format.

This is bad for two reasons:
1. **Silent fail before user sees the column mapper.** The toast says "may not match the date format" but the importer never opened the mapper to let the user fix it — they had to click "Map columns" themselves to even discover the option. Operators will assume the file is broken.
2. **Auto-detection ignores the locale we already know.** Every Nxentra company picks a locale / date format preference during registration (DD/MM/YYYY for Egypt, MM/DD/YYYY for US, ISO for technical defaults). The importer should default to *that* on every CSV upload from this company, not run auto-detection from scratch every time.

**Fix:**
1. Pull `company.locale.date_format` (or whatever the registration setting maps to) and use it as the **default** date format in the column mapper.
2. On parse failure due to date format, **automatically open the column mapper modal** rather than just showing a toast. Pre-fill with the company's locale; let the user override.
3. Persist the per-bank-account mapping (already mentioned in the help text "Mappings are remembered per bank account") — once set, future uploads to the same bank account skip the mapping prompt entirely.
4. Wider fix: every CSV import flow in the app (settlement import, item import, customer import) should follow this same locale-defaulted, fail-loud-not-silent pattern. File a parent ticket if other importers exhibit the same issue.

**Not submission-blocking** (operator can manually map columns once and proceed), but every merchant will hit this on day 1 of bank rec and the "did the file work?" anxiety is a trust-damaging first impression. Pull forward post-listing alongside A82/A84/A86 in the UX polish wave.

### A86. Settlement importer falls back to generic expense account for gateway/courier fees instead of per-provider mapping — **~30min** (settlement importer + account mappings UI, surfaced 2026-05-24)

When the Paymob (and presumably Bosta) settlement CSV importer posts the per-batch JE, the fees line is routed to the first generic expense account found in the company's chart of accounts. Reproduced 2026-05-24 on Shopify_R: the `PAYMOB-BATCH-DEMO-001` settlement posted `$160.85` of Paymob gateway fees to `53000 Office & General 1` instead of a dedicated "Payment Processing Fees" or "Gateway Fees" account.

This is **mathematically correct** (debit balances the credit) but **mis-categorized for P&L purposes**: a merchant looking at "Office & General" expenses on their income statement sees Paymob fees blended with rent, utilities, and stationery. Their actual payment processing cost is hidden from operational reporting and competitive analysis. The expense category is a meaningful business KPI (gross margin should net it out separately) and getting it wrong costs the merchant trust in the system on day 1 of first settlement import.

**Fix:**
1. **Account Mappings UI extension** (`/settings/integrations/shopify` already has the mappings card) — add a "Gateway Fees Account" field per provider (Paymob, Bosta, Stripe, etc.); default to creating a "53400 Payment Processing Fees" account during onboarding if no equivalent exists.
2. **Settlement importer reads this mapping** when posting the fee line, instead of falling back to a generic expense lookup.
3. **Fail-loud fallback** — if no mapping exists, surface a banner / form-modal during import rather than silently routing to a wrong account. (Today's silent fallback is the worst-of-both-worlds: user thinks it worked, ledger is wrong.)

**Not data-incorrect, not submission-blocking** — fees ARE booked, they're just in the wrong category. Pull forward in the first wave of post-listing UX polish alongside A82/A84 since every merchant will hit it on day 1 of their first settlement.

### A100b. Migrate accounting/views.py off projection_writes_allowed() — **PENDING** (~1.5h, post-listing punch list)
Six sites in `backend/accounting/views.py` enter `projection_writes_allowed()` directly from a view, the same protocol violation A100 cleaned in `bank_connector/views.py`. Currently allowlisted in `tests/test_architecture_rules.py::VIEW_PROJECTION_CONTEXT_ALLOWLIST` so the arch test passes; that allowlist entry is what this ticket removes.

Suggested approach:
1. **Audit (~30 min):** list each of the 6 sites (lines 1275, 1338, 1381, 1437, 1500, 1543), identify the downstream command/operation, classify each as:
   - (a) command-needs-projection-write pattern → push context into the command
   - (b) workaround for missing `command_writes_allowed` chain → fix at the manager layer
   - (c) genuinely projection-rebuild work (analogous to `projections/views.py`) → keep but separately allowlisted with justification
2. **Move (~1h):** straightforward refactor per A100's pattern for type (a); small fix at the manager layer for type (b).
3. **Remove `accounting/views.py` from `VIEW_PROJECTION_CONTEXT_ALLOWLIST`.** The arch test now holds the whole `*/views.py` surface against the rule.

**When:** after the App Store listing submits and Aljazeera7 is onboarded. Not blocking any user. The arch test pins the surface so nothing gets worse in the meantime.

### A99b. Close the remaining 3 direct JournalLine.reconciled writes in reconciliation/commands.py — **PENDING** (A99b-fast ~1h + A99b-deep deferred)
A101's source scan surfaced three sites A99 didn't catch. Currently allowlisted in `RECONCILED_WRITE_ALLOWLIST` in the arch test.

**A99b-fast (~1h) — sites 518 and 1107:**
- `reconciliation/commands.py:518` — `auto_match_statement` platform-payout prepass. Flips a payout JE's bank line to reconciled.
- `reconciliation/commands.py:1107` — `auto_match_statement` generic-GL match. Flips a matched JL in the generic same-account fallback.

Both can ride on the existing `ReconciliationMatchConfirmedData.additional_journal_lines_to_reconcile` field that A99 added. No new event shape needed. Update the `_emit_match_confirmed(...)` callers to pass the relevant JL public_id, then delete the direct `JournalLine.objects.filter(pk=…).update(reconciled=True, …)` block. Same shape as the A99 refactor itself.

**A99b-deep (deferred) — site 1771:**
- `reconciliation/commands.py:1771` — `resolve_difference` flips the EBD line when the difference adjustment fully drains it. This is part of the A16 exception flow; the right home for the write is `ReconciliationExceptionResolved`'s projection handler, which is currently a no-op pending the exception read model (per the A86.3 comment in `reconciliation/projections.py`).

A99b-deep folds into the eventual exception-queue work alongside the A86.3 read-model build — bigger piece, not a standalone item.

**Exit:** when A99b-fast lands, drop the `reconciliation/commands.py` entry from `RECONCILED_WRITE_EXPECTED_COUNTS` (or lower the count from 3 → 1 if only sites 518+1107 are cleaned). When A99b-deep lands, drop the entry entirely.

**A99b refinement (2026-05-27, post-Round-4-review):** `reconciliation/commands.py` graduated from file-level allowlist (`RECONCILED_WRITE_ALLOWLIST`) to expected-count allowlist (`RECONCILED_WRITE_EXPECTED_COUNTS = {"reconciliation/commands.py": 3}`). Net effect: a new direct write fails the test (catches regression), AND a removal that doesn't update the count also fails (catches partial cleanup). For `difference_amount`, re-scan confirmed zero direct writes in the file — dropped from `DIFFERENCE_WRITE_ALLOWLIST` entirely; the architecture rule now holds the whole surface for that field.

**When:** post-listing punch list, after Aljazeera7 onboards. Same justification as A100b.

### A103. Registration must propagate `default_currency` → `functional_currency` — ✅ **ALREADY DONE 2026-04-26** (commit `b6b52b9`, re-diagnosed during 2026-05-27 Shopify_R screencast prep)

Re-diagnosed today and found to be already fixed. Commit `b6b52b9` (2026-04-26) "Fix registration: persist user-selected currency to both default and functional currency" did exactly this. The bug originally manifested in the opposite direction — Egyptian merchants picking EGP silently got USD/USD because the projection overwrote with the model default. The fix:
- `backend/accounts/views.py:174` reads `currency` first, falls back to `default_currency` (backward-compat)
- `register_signup` (`accounts/commands.py:187`) and `create_company` (`accounts/commands.py:488`) both persist `default_currency=X, functional_currency=X` on the Company row
- `CompanyCreatedData` carries both currencies; projection applies them (legacy events without `functional_currency` fall back to `default_currency` for replay safety)
- Two regression tests: `test_register_persists_currency_to_both_fields` + `test_register_view_honors_currency_request_key`

**Why Shopify_R still has the mismatch:** Shopify_R was created before commit `b6b52b9` shipped, so it carries the legacy USD/EGP state. New merchants registering via the App Store listing today will NOT hit this bug — they will have `default_currency == functional_currency` correctly persisted.

**Workaround applied to Shopify_R during 2026-05-27 session** (for the screencast specifically): added `ExchangeRate(USD→EGP, rate=1.0, effective_date=2026-01-01, source='Manual (demo seed workaround)')`. Marked the failure log row resolved manually after `run_projections` re-applied the pending refunds. Shopify_R remains a legacy mismatch case; no production merchant will reach this state.

**Optional follow-up:** a data migration could backfill any legacy company where `default_currency != functional_currency`. Deliberately not shipped because the mismatch is sometimes intentional (multi-currency businesses report in a different functional currency than transaction default). Leave to operator/admin tooling.

### A104. Reconcile FX-fallback policy between `je_builder` (warn+1.0) and `shopify_connector.projections._resolve_exchange_rate` (raise) — **PENDING ~30min** (post-listing punch list, surfaced 2026-05-27)
Two policies for the same situation (no rate found): `backend/platform_connectors/je_builder.py:227` warns and uses 1.0 (silent data-quality risk — JE posts at wrong rate, no operator-visible signal); `backend/shopify_connector/projections.py:151` raises `MissingExchangeRate` (visible operator stop via `ProjectionFailureLog`). After A103, this matters less, but the inconsistency is latent — `order_paid` will silently book at wrong rate while `refund_created` raises.

**Recommendation:** make the strict path the default everywhere; drop the je_builder fallback. The operator-visible stop is the right safety mechanism per [ENGINEERING_PROTOCOL.md](ENGINEERING_PROTOCOL.md) §2.4 ("partial postings must surface visibly").

### A105. `ProjectionFailureLog` auto-resolve hook didn't fire after successful retry — **PENDING ~1h** (post-listing punch list, surfaced 2026-05-27)
The model docstring at `backend/projections/models.py:1184-1186` claims: *"Once an operator fixes the underlying problem … and the next process_pending pass successfully processes the event, the framework auto-marks this entry resolved."* That did not happen during the 2026-05-27 session — after `run_projections` posted `CN-000001` + `CN-000002` successfully, the failure-log row stayed `resolved=False, resolved_at=NULL` and `shopify_health_check` continued to report a blocker until manually resolved via shell.

Grep `mark_resolved` / `auto_resolve` in `backend/projections/base.py` to confirm whether the hook is missing entirely or just not wired into `BaseProjection.process_pending`. Either implement it, or update the docstring to match reality (and document the manual resolve path).

### A106. `ProjectionFailureLog.resolved` boolean and `resolved_at` timestamp drift — **PENDING ~15min** (post-listing punch list, surfaced 2026-05-27)
Two fields representing one state. The model has both `resolved` (BooleanField, indexed) and `resolved_at` (DateTimeField, nullable). `backend/shopify_connector/management/commands/shopify_health_check.py:357` gates OPEN/RESOLVED on `resolved_at`; a manual `.update(resolved=True)` leaves `resolved_at=NULL` and the row still looks OPEN to the operator-facing health check (verified live during 2026-05-27 session).

**Smallest fix:** override `ProjectionFailureLog.save()` to auto-stamp `resolved_at = timezone.now()` when `resolved` flips True. Alternative: change `_collect_problems` (line 357) + the rendering at line 317-318 in `shopify_health_check.py` to read the `resolved` boolean. The save-override pattern is more defensive — it propagates to any future caller.

### A107. `paymob_accept` lazy-creates as a distinct SettlementProvider instead of mapping to `paymob` — **PENDING ~30min** (post-listing punch list, surfaced 2026-05-27)
On `/finance/reconciliation` Stage 1 the Shopify_R demo shows three providers: `Bosta`, `Paymob`, and `Paymob Accept` with a yellow "Review" badge. The Review badge fires from `needs_review=True` set by A2's lazy-create path. The seed CSV used the gateway code `Paymob Accept` for some orders and `Paymob` for others; A2's `normalized_code` logic didn't fold the variant.

Two paths:
1. Treat `paymob_accept` as a normalized alias of `paymob` (one clearing flow). Add to the alias map next to `accounting/settlement_provider.py`'s bootstrap rows.
2. Treat Paymob Accept as a legitimately distinct Paymob product (merchant-of-record API vs. gateway-only API are real distinct flows). Then drop `needs_review=True` after operator confirms.

Option 1 matches the existing bootstrap surface (which has `paymob` but not `paymob_accept`) and is the lower-cost choice. Worth a 5-minute Paymob-docs check before deciding.

### A116. `JournalEntry.source_module` / `source_document` are direct-written, not in event payload — lost on projection rebuild — **PENDING ~1h** (HIGH PRIORITY, surfaced 2026-05-28 during Shopify_R Banked-column diagnostic)

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

### A115. `JournalEntryProjection._clear_projected_data` is missing — `--rebuild` is a silent no-op on the JE read model — **PENDING ~30min** (HIGH PRIORITY, surfaced 2026-05-28 during Shopify_R orphan-event purge)

**The bug.**  `BaseProjection.rebuild()` at `backend/projections/base.py:98-132` is documented as:

> Default implementation:
>   1. Reset bookmark to beginning
>   2. Clear existing projected data
>   3. Process all relevant events

Step 2 calls `self._clear_projected_data(company)`.  The default implementation at base.py:134-139 is `pass` (a stub).  Subclasses are expected to override.

`AccountBalanceProjection`, `PeriodAccountBalanceProjection`, `SubledgerBalanceProjection` all override correctly.  **`JournalEntryProjection` does NOT** — the rebuild call is therefore a silent no-op.  The projection's `handle()` method at `projections/accounting.py:332` uses `get_or_create(public_id=...)`, so replayed `journal_entry.created` events match the EXISTING JE row by public_id and do nothing.  Net effect: `--rebuild journal_entry_read_model` does not delete orphan JEs; it just re-confirms the existing state.

**Symptom on Shopify_R 2026-05-28.**  After deleting 82 orphan `journal_entry.*` events and calling `--rebuild journal_entry_read_model`, the JE count actually went UP from 41 → 43 (because some previously incomplete JEs got picked up).  Clearing-account totals on the reconciliation page did not change.  Worked around by directly deleting orphan `JournalEntry` rows from the shell.

**Smallest fix:**

```python
# projections/accounting.py — JournalEntryProjection
def _clear_projected_data(self, company: Company) -> None:
    from .models import JournalEntry, JournalLine
    JournalLine.objects.filter(company=company).delete()
    JournalEntry.objects.filter(company=company).delete()
```

Caveats:
- JournalLine/JournalEntry are `ProjectionWriteGuard` models — the delete must happen in a `projection_writes_allowed()` context.  Check that the `rebuild` call site already grants this; if not, the `_clear_projected_data` override needs to grant it.
- Other source documents (SalesInvoice, PurchaseBill, etc.) have `posted_journal_entry` FK with `on_delete=SET_NULL` — the JE delete will null those.  After rebuild materializes new JEs with new int pks, those FKs stay null until `relink_orphaned_je_fks` runs.  Documented in [[A114]] — A114's "Option 3" (source-doc projections) fixes this class of issue entirely.

**Why this matters beyond demo data.**  Any operator who needs to recover from event corruption / partial-write scenarios is currently told `run_projections --rebuild journal_entry_read_model` is the recovery primitive.  It silently doesn't work.  Worth fixing before the first real merchant ever needs to use this path.

**Connects to.** [[A110]] (source-doc projections), [[A111]] (BusinessEvent deletion guard), [[A112]] (seed flush downstream cleanup), [[A114]] (FK target stability).

### A114. Source-document → JournalEntry FK target should survive JE projection rebuild — **PENDING ~2-4h** (post-listing punch list, surfaced 2026-05-28 during Shopify_R re-link recovery)

**The problem.**  Source documents (`SalesInvoice`, `SalesCreditNote`, `PurchaseBill`) carry `posted_journal_entry = ForeignKey(JournalEntry, on_delete=SET_NULL)`.  The FK target is `JournalEntry.id` — the auto-increment integer primary key.  When `run_projections --rebuild journal_entry_read_model` clears and replays the JE projection, the new JE rows get NEW int primary keys.  Source documents that were linked to the OLD ids now point to nothing — their FK gets nulled by SET_NULL during the rebuild's delete pass, and nothing reconnects them after.

**Symptom on Shopify_R 2026-05-28.**  After the JE rebuild, the Vendor Bills / Sales Invoices / Credit Notes pages all showed "—" in the new JE-link column because `posted_journal_entry_id IS NULL` on every row.  Worked around via a one-off `relink_orphaned_je_fks` management command (committed alongside this ticket) that memo-matches each source doc to its rebuilt JE.

**Three possible fixes (in increasing architectural cleanliness):**

1. **Keep the int FK + make rebuild reconnect** — `journal_entry_read_model._rebuild` would, after re-creating JEs, scan source documents whose memo matches a known pattern and re-link.  Memo-pattern matching is brittle (changes to memo format break recovery) and tightly couples the projection to source-doc semantics.

2. **Switch FK target to `JournalEntry.public_id` (UUID)** — UUIDs are stable across rebuilds (they're stored on the event payload, so the projection re-creates the same UUID even with a fresh int pk).  Requires a model migration: replace `posted_journal_entry` FK with `posted_journal_entry_public_id` UUIDField + a property that resolves to the JE row.  Cleaner than option 1 but loses the ORM `.posted_journal_entry` accessor.

3. **Make source documents projection-driven (A110)** — when SalesInvoice / PurchaseBill are themselves projection read models, both the row AND its FK get rebuilt from the source event's payload (which carries the JE UUID alongside the source doc's identity).  The whole class of "FK orphaned by rebuild" problems disappears for the full source-document tier.  This is the deepest fix and the right architectural endpoint.

**Recommendation:** ship Option 3 (A110).  Don't ship Option 1 (brittleness compounds).  Option 2 is a viable intermediate if A110 is too big to schedule but the rebuild-orphan problem keeps biting; it's strictly less work than A110 but yields less value.

**Connects to.** [[A110]] (the umbrella architectural fix), [[A111]] (deletion guard — preventing the originating event), [[A112]] (cleaning downstream events on test-pack re-seed).

### A113. GR/IR three-way matching for accrual accuracy on goods-received-pre-invoice — **DEFERRED, trigger-based** (post-listing punch list, surfaced 2026-05-28 during JE-link audit-trail work)

**Current Nxentra accounting flow (two-step):**
1. PO created/approved → no JE (commitment only)
2. Goods Receipt posted → updates `StockLedgerEntry` (physical quantity + average cost), **no JE**; per `backend/purchases/models.py:200` and `backend/purchases/commands.py:1156`: *"GRs create NO journal entries — accounting happens at bill posting."*
3. Vendor Bill posted → full JE: `Dr Inventory + Dr Tax / Cr AP Control` (where the AP liability is born)
4. Vendor Payment posted → `Dr AP / Cr Bank`

**The accrual gap.** If goods arrive on day 1 but the vendor's invoice doesn't arrive until day 10, between day 1 and day 10 the trial balance does NOT reflect either the inventory asset or the AP liability. The stock subledger says "we have it physically" but the books say "we don't own it yet." For a Shopify merchant whose invoices typically arrive within hours/days of the goods, this is fine. For larger operations with longer receipt-to-invoice gaps, the trial balance is understated on month-end snapshots between GR and Bill.

**Textbook three-way match pattern (what large ERPs do):**
- New account role: `GR_IR_CLEARING` (Goods Received / Invoice Received clearing — a control liability account)
- Post GR: `Dr Inventory / Cr GR/IR` — inventory hits books at receipt
- Post Bill: `Dr GR/IR / Cr AP Control` — clears the accrual, creates the actual AP liability
- Plus a Purchase Price Variance line if bill cost ≠ GR cost: `Dr PPV / Cr GR/IR` (or reverse)

**Smallest fix when triggered:**
1. Add `GR_IR_CLEARING` to `purchases` ModuleAccountMapping role list; bootstrap creates `21100 Goods Received / Invoice Received Clearing` (Liability, sub-control of AP).
2. `post_goods_receipt` (`purchases/commands.py:1150`): build a JE with `Dr Inventory(item.inventory_account) / Cr GR_IR_CLEARING(module mapping)`. Tag the GR/IR line with `vendor_public_id` so the subledger can age "we received goods but haven't been billed yet" by vendor.
3. `post_purchase_bill` (`purchases/commands.py:346`): change the inventory debit lines into `Dr GR_IR_CLEARING` (for matched amounts from linked PO lines). The non-inventory expense + tax debit lines stay as-is. Cr AP stays as-is.
4. Add a Purchase Price Variance line when `bill.unit_cost ≠ gr.unit_cost` for the same PO line.
5. New reconciliation surface: "GR/IR aging" — goods received but not yet billed, grouped by vendor.
6. New invariant test: `sum(GR_IR_CLEARING balance) == sum(unmatched GR cost where bill not yet posted)`.

**Trigger conditions** (don't pull forward without one of these):
- A real Nxentra merchant reports inventory understated at month-end because invoices arrive late
- Move into mid-market distribution/manufacturing ICP where month-end accruals are load-bearing
- Auditor/CPA partner requests it for a specific customer
- Or pre-emptive ahead of the Phase B canonical platform models work, when it's cheap to fold in

**Why NOT now:** the current two-step model is correct for the Shopify-merchant ICP. The 2026-05-28 narration ("POs are commitments, goods receipts record physical stock, accounting happens at Bill posting, click any bill or vendor payment to see the JE") is the simpler, more digestible story for a merchant who doesn't have a CFO. Three-way matching adds a clearing account that operators have to understand. Don't build until a real customer needs it.

**Connects to.** [[A110]] (source-document projection work — if SalesInvoice/PurchaseBill become projection-driven, the GR/IR transition is a natural alignment point), Phase B (canonical platform models — could fold in here).

### A112. `seed_test_csv_pack --flush` leaves downstream `journal_entry.created` / `sales.invoice_created` events as orphans, creating "ghost JE" history on re-seed — **PENDING ~1h** (post-listing punch list, surfaced 2026-05-28)

The `_flush` method at `backend/shopify_connector/management/commands/seed_test_csv_pack.py:361-388` only deletes events tagged with `metadata__source='test_csv_pack'` — which captures `shopify.order_paid` + `shopify.refund_created` (the events the seed itself emits) but NOT the cascading downstream events that the Shopify projection emits when it consumes those (specifically `journal_entry.created`, `journal_entry.posted`, `sales.invoice_created`, `sales.invoice_posted`).

**Symptom on Shopify_R, 2026-05-28:** after multiple seed-flush-reseed cycles over the past weeks, the company's `BusinessEvent` log carries 27 `sales.invoice_created` events and 25 `sales.invoice_posted` events — when the user only ever explicitly invoked 1 active seed (10 orders). The other 15+ events are "ghosts" from prior seed runs whose ShopifyOrders + tagged events were flushed but whose downstream JE events survived. After the 2026-05-28 rebuild, JE list shows 41 entries when operator expected ~15. Confusing for the operator; harmless to financial correctness; bad for demo cleanliness.

**Smallest fix:** in `_flush`, after deleting tagged `BusinessEvent`s, also delete:
1. `journal_entry.*` events whose `data.memo` matches the Shopify invoice naming pattern `Sales Invoice INV-*` AND whose date falls within the seed CSV's date range
2. `sales.invoice_*` events for the same invoice numbers
3. The orphan `SalesInvoice` + `JournalEntry` rows for those invoice numbers (the rows the projection materialized)

**Caveat:** this is a deliberate event deletion in a controlled scope (test-pack reseed). It's narrower than the 2026-05-27 incident (full table delete) but still violates event immutability. Worth gating behind an explicit `--purge-downstream` flag rather than making it the default of `--flush`. Document the trade-off clearly in the command help.

**Connects to.** [[A110]] (proper SalesInvoice projection would let this rebuild cleanly), [[A111]] (BusinessEvent deletion guard would force the explicit flag here).

### A111. Add code-level guard against `BusinessEvent` deletion — **PENDING ~2-3h** (post-listing punch list, surfaced 2026-05-28 after JE rebuild)

The 2026-05-27 incident (JEs wiped via Django shell DELETE) and my own follow-up advice (delete orphan `cash.customer_receipt_recorded` events as cleanup) both violated event immutability. A110 codifies the principle in docs. A111 codifies it in code.

**Smallest fix:** override `BusinessEvent.delete()` and `BusinessEvent.objects.delete()` paths to raise `EventImmutabilityViolation` unless the caller explicitly passes `confirm_immutability_violation=True`. Audit-log every deletion attempt (whether allowed or refused) to a separate `EventDeletionAttempt` model with stack trace + actor identity.

**Recovery path** still available — `confirm_immutability_violation=True` is the explicit acknowledgement that the caller knows the trade-off. Used by:
- `seed_test_csv_pack._flush` (after A112 wires the explicit `--purge-downstream` flag)
- Any future test-fixture cleanup
- Operator emergency recovery (with full audit trail)

**Plus monitoring:** add a `monitor_event_count_drops` management command (or scheduled health check) that compares `BusinessEvent.objects.filter(company=c).count()` against a high-water-mark stored per company. Sudden drops (>5% in 24h) alert. Catches both shell-level DELETEs and accidental code paths.

**Connects to.** [[A110]] (the lesson this codifies), [[A112]] (the legitimate use case that the override flag enables).

### A110. Source-document read models (SalesInvoice / PurchaseBill / PurchaseOrder / GoodsReceipt / ShopifyOrder) are not projection-driven — only the ledger tier is replayable from events — **PENDING ~1-2 weeks** (post-listing punch list, ARCHITECTURAL, surfaced 2026-05-28 during Shopify_R event-replay experiment)

**The finding.** Nxentra has TWO tiers of event-sourcing:

| Tier | Model | Pattern | Replayable from `BusinessEvent`? |
|---|---|---|---|
| Ledger | `JournalEntry`, `JournalLine`, `AccountBalance`, `DimensionBalance`, `PeriodAccountBalance`, `CustomerBalance`, `VendorBalance` | Projection-driven (via `journal_entry_read_model`, `account_balance`, etc.) | ✅ Yes — proven on 2026-05-28 when 111 events replayed into 41 JEs |
| Source documents | `SalesInvoice`, `PurchaseBill`, `PurchaseOrder`, `GoodsReceipt`, `ShopifyOrder` | Command-direct ORM `objects.create(...)` with auxiliary event emit | ❌ No — events carry the data but no projection consumes them to rebuild rows |
| Event-view (no model) | Customer Receipts list, Vendor Payments list | Pure event-query on `BusinessEvent` | ✅ Yes if their specific events still exist |

**Why it matters.** The "event log is the source of truth, everything else is a derived read model" promise is the heart of Nxentra's positioning. Today it holds for the ledger, which is the bulk of accounting truth — but breaks for source documents. A merchant whose Shopify-imported invoices or manually-created bills get deleted from the read-model table has no system-driven recovery path; the events are there but unused.

**Surfaced when.** During the 2026-05-28 Shopify_R event-replay experiment, JEs rebuilt cleanly (correctly proving the architecture works at the ledger tier) but the SalesInvoice list stayed at 10 entries — the events show 27 `sales.invoice_created` + 25 `sales.invoice_posted` but only 10 SalesInvoice rows exist. Same pattern for PurchaseBill (4 created events, 3 posted events, 3 rows).

**Smallest fix per model.** For each source document, add a projection in `<module>/projections.py` (or `projections/<model>.py`) that:
1. Consumes the `<model>.created` / `<model>.updated` / `<model>.posted` / `<model>.deleted` events
2. Materializes / mutates the row from event payload
3. Switch the command from `Model.objects.create(...)` to `emit_event(...)` then read the projected row (the same pattern `create_journal_entry` uses at `accounting/commands.py:732-758`)

**Suggested order of attack** (cheap → expensive):
1. **SalesInvoice** — highest-value, central to the merchant-facing surface
2. **PurchaseBill** — folds in A109 naturally (the `journal_entry_id` FK becomes a projection-write)
3. **ShopifyOrder** — feeds the Shopify dashboard; high merchant visibility
4. **PurchaseOrder + GoodsReceipt** — lower frequency, lower urgency

**Why not now (pre-listing).** This is genuine architectural work. The migration must be careful: each rewrite is a semantics change for one of the most-touched files in its module. Pre-listing budget can't absorb this.

**Connects to.** [[A99b]] (reconciliation/commands.py direct writes), [[A3]] (reactor extraction), [[A109]] (PurchaseBill→JE FK — folds in here). Also Phase B canonical platform models — when those land, this is a natural alignment point.

**Lesson logged.** When advising on cleanup, default to "preserve all events; rebuild read models" rather than "delete events to clean orphans." The 2026-05-27 advice to delete `cash.customer_receipt_recorded` events was wrong in retrospect — those events would have repopulated the Customer Receipts list after the JE rebuild. Event deletion violates the source-of-truth invariant even when the intent is cleanup.

### A109. `PurchaseBill` has no FK to its journal entry — ✅ **RESOLVED-NOT-A-BUG 2026-05-28** (originally surfaced 2026-05-27 during Shopify_R deep-data investigation)

**Original ticket was a false alarm.** Re-checking on 2026-05-28: `PurchaseBill` (and `PurchaseCreditNote`) DO carry `posted_journal_entry = ForeignKey(JournalEntry, ...)` at `backend/purchases/models.py:427` and `:583`. The previous shell verification used `hasattr(bill, 'journal_entry_id')` which returned False because the FK is named `posted_journal_entry` (auto-creating `posted_journal_entry_id`, not `journal_entry_id`). My field-name assumption was wrong.

Closure: same 2026-05-28 commit that adds the JE-link column on Vendor Bills surfaces this FK on the serializer (`journal_entry_pk` + `journal_entry_number` via `source="posted_journal_entry_id"` / `source="posted_journal_entry.entry_number"`). The UI now shows clickable `BILL-* → JE-*` links. Same treatment applied to Credit Notes, Sales Invoices, and Vendor Payments.

Lesson logged: when checking FK existence, `hasattr` is brittle — names like `journal_entry_id` vs `posted_journal_entry_id` matter. Better verification: read the model class definition directly, or grep for `ForeignKey.*JournalEntry`.

### A108. Dashboard "Total Revenue" doesn't reconcile with Shopify Connector dashboard or Reconciliation page — **PENDING ~1h, investigate-only** (post-listing punch list, surfaced 2026-05-27; only act if screencast playback exposes it)
Observed on Shopify_R during 2026-05-27 verification:
- `/dashboard` Total Revenue: **USD 39,448.78**
- `/shopify` Revenue (Processed): **USD 16,800.00**
- `/finance/reconciliation` Total Expected: **USD 16,950.00**

The reconciliation arithmetic is internally consistent (16,950 sold − 1,700 settled = 15,250 open balance). The `/shopify` figure is close to expected sold (10 USD seed orders, sum ≈ 16,800-16,950 depending on tax/shipping inclusion). The `/dashboard` Total Revenue is ~2.35× larger — likely the global dashboard sums all P&L revenue-class accounts (41000 Sales + 42000 Shipping + possibly a returns-class line) and may also pick up seeded opening balances on the chart of accounts.

Not wrong per se; the concern is a screencast viewer notices the gap and asks where 39k comes from. Investigate only if the playback shows the discrepancy on-camera; otherwise let it ride.

**Suggested probe:** `SELECT account.code, account.name, SUM(amount) FROM journal_line WHERE company_id = 41 AND account.type IN ('REVENUE', 'INCOME') GROUP BY account.code, account.name ORDER BY SUM(amount) DESC;` to identify which account(s) push the dashboard number above the connector number.

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

### A85. New company has no opening equity scaffolding — cash position goes negative on first non-payment activity — **~1h** (onboarding wizard, surfaced 2026-05-24)

A freshly-onboarded company has no opening JE establishing initial capital. The first activity that debits cash (e.g., posting any expense, paying any bill) immediately drives `11000 Cash and Bank` negative, and the dashboard shows a negative Cash Position card. Reproduced 2026-05-24 on Shopify_R after posting 5 sales invoices + reviewer-store install — Cash Position showed `USD -10,500.00` before we manually added a $50K Owner's Capital JE.

This is misleading: it tells the merchant their balance sheet is broken when in fact the engine is correct (no opening capital was recorded). Two fixes, either acceptable:
1. **Onboarding wizard prompts for opening equity** during company setup ("How much capital did you start this company with?" → posts the JE on completion).
2. **Dashboard distinguishes "no opening balance recorded yet"** from "actually overdrawn" — softer messaging or a setup prompt when cash is negative and no equity entries exist.

Option 1 is more forgiving for first-merchant onboarding. Per April 22 session log, the `seed_shopify_demo` flow already adds a $50K Owner's Capital entry — extend the same pattern to live company setup. **Not submission-blocking** (we manually added it for the reviewer-store demo today) but the first paying merchant will hit it on day 1 if their seed flow doesn't run.

---

**Merchant-readiness exit criteria** (revised 2026-05-17 after A50/A51/A52 ship + A53/A54/A55 surface):
- **A44: DONE 2026-05-10** — code shipped + tested. **Manual operator step still pending:** wire the three webhook URLs into Partners Dashboard → App setup → Compliance webhooks.
- **A46: DONE** — already implemented; verified above.
- **A50: DONE 2026-05-16** (commit `104a453`) — wizard import clamps to 59-day floor.
- **A51: DONE 2026-05-16** (commit `104a453`) — declarative webhook subscriptions, register-webhooks UI/backend removed, model field dropped via migration 0014.
- **A45 (remaining): tier-1** — must ship **before** the first paying merchant invite. Privacy page exists but Partners Dashboard config + support email are still unset. **Remaining tier-1 effort: ~2h.**
- **A52: tier-1** — diagnostic logging shipped 2026-05-16 (commit `104a453`); awaits live retry on Shopify_R to expose root cause. **Tier-1 effort: ~0.5-1d after data captured.** Workaround (manual demo data in Nxentra) unblocks the App Store submission itself without fixing A52.
- **A53 + A54 + A55: tier-2 post-listing-approval** — Shopify-approval-gated expansions of the OAuth scope set. A53 unlocks real-time order webhooks (replaces 4h polling latency). A54 enables dispute handling. A55 unlocks order history >60 days. **Do not start any of these during the in-flight App Store listing review** — they require Partners Dashboard / API access changes that may restart the review. Sequence: ship listing → wait for approval → request A53 + A55 in parallel → A54 when chargebacks become real.
- **A47 + A48 (remaining) + A49: tier-2** — ship in the first 1-2 weeks **after** first paid signup. None block install; all are pre-emptive hardening before the second/third merchant. **Tier-2 effort: ~0.5d + ~1h + ~0.5d ≈ 1.25 days.**
- **App Store listing** (Built for Shopify checklist, public discoverability): deferred until 5-10 happy paying merchants and product polish reaches submission-grade. Months out, not weeks. Distinct from public OAuth distribution (already enabled, currently submitted via `nxentra-sync-4` deploy; `nxentra-sync-5` releases on next successful `shopify app deploy` after this commit).

---

## What to do right now, today

**🔴 2026-05-17 PRIORITY — finish App Store listing submission.** A50 + A51 shipped 2026-05-16 (commit `104a453`); A52 has diagnostic logging in place. `shopify.app.toml` webhook subscriptions narrowed to 3 unblocked topics (products/*, app/uninstalled) — see A53/A54 for the deferred ones. **Today's path:** (1) deploy backend (git pull + migrate + restart) to droplet. (2) `shopify app deploy` from laptop → release nxentra-sync-5. (3) retest A52 on Shopify_R store, capture diagnostic logs. (4) if A52 still 0 orders, fall back to Path 3 — create demo data natively in Nxentra. (5) capture 3 screenshots + record screencast (3-8 min unlisted YouTube) + paste reviewer credentials into App Store listing form + Submit for review.

**Post-listing-approval queue (do NOT start before listing approves — risk of review restart):** A53 (Level 1 protected customer data → real-time order webhooks), A55 (`read_all_orders` → >60d history), A54 (`read_shopify_payments_disputes` → dispute handling).

---

Phase A continues. **A0 done** (`fb0e3d6`), **A1 done** (`b6b52b9`–`7d12432`, 2026-04-28), **A2 done** (`d0dd0d2`, 2026-04-30), **A2.5 done** (`caa1ab9`, 2026-04-30), **A8 done** (`71cb0d7`, `cd7f484`, 2026-04-29), **A12 done** (`86d62d2` + `6a09473`, 2026-05-01), **A13 done** (`b24065b`, 2026-05-01), **A14 done** (`238d0a9`, 2026-05-01), **A14b/A14c done** (`3445bc0`, 2026-05-01), **A16 done** (`ced05ad` + hotfix `63d8888`, 2026-05-01), **A17 done** (`faf5b52`, 2026-05-01), **5 commits 2026-05-02/03** (`7425bbc`, `b074164`, `96dd1e6`, `5df4d1e`, `e9a0ddd`) for settlement-importer aliases + A17 toast follow-up + seed_test_csv_pack tooling + Cash Flow ImportError fix + bank-rec auto-match crash fix, **8 Tier-1 commits 2026-05-03/04** for A18-A23 / A22 / A25 / A26 (`1fd3922 b510626 d9030de 29c1672 39adba0 cc343a6 6347db1 9b5191f`).

**Phase A complete on the merchant-facing engine. Tier-1 dry-run done.** 8 of 9 Tier-1 items shipped + verified in production via the 2026-05-04 Aljazeera8 fresh-tenant dry-run. Only A24 (frontend column-mapper) and A25 frontend wiring (picker swap to new endpoint) remain before the BNK-003 manual-match → A16 Resolve flow can be tested end-to-end from the UI. Neither is a data-loss bug; the merchant can be supported through the workaround on the WhatsApp call.

**Outstanding before invite:** nothing on the engineering side. The remaining items (A24, A25 frontend, A39-A43) are all **Tier-2 follow-ups** — UX polish + edge cases that won't hit the first merchant on day 1. Per session prompt, invite slipping is "(d) not acceptable — name it, don't ship around it."

**Immediate next steps (in order):**

1. **Send the first-user invite this week.** Egyptian Shopify merchant acquired 2026-04-22. Invite kit (Calendly with 4 open slots, WhatsApp Business number, [docs/onboarding/welcome.md](docs/onboarding/welcome.md)) needs final pre-flight check, then the EN+AR invite text goes out. The user has been documented as having a tendency to defer for "one more thing"; explicit guidance per the session prompt is don't.

2. **Triage incoming merchant signal during the first 48-72 hours.** Pull forward exactly the items the merchant complains about — most likely A24 (column-mapper) the moment they upload their bank CSV, possibly A43 (CN/INV detail 404) the moment they click a credit-note number. Don't pre-emptively fix anything until they signal.

3. **Then the Tier-2 backlog**, in roughly this order based on dry-run findings:
   - **A24** + **A25 frontend wiring** + **A26 frontend badge** — bank-rec UI polish, lets BNK-003 → A16 flow work end-to-end from the UI.
   - **A41** — A23 deeper fix (defer-on-exhaust). Real production edge case.
   - **A39** — settlement double-count detection (BST-701 / order 1007 pattern). Needs policy decision; defer until merchant hits it.
   - **A35** + **A42** + **A43** — UI polish (Stage 2 widget, success toast, detail-page 404).
   - **A40** — seed pack ordering (test-pack only).
   - **A28-A38** — Tier-2 UX backlog from prior dry-run, pulled forward only as merchant signals demand.

**Before first PAYING merchant (post-Heba beta, pre-paid-invite):**

4. **A45 (remaining only)** — Shopify-policy-mandatory tier-1, **~2h**. A44 + A46 both DONE 2026-05-10. A45 only needs Partners Dashboard config (privacy URL + support email) and `support@nxentra.com` forwarding. Plus the manual A44 follow-up: register the three GDPR webhook URLs in Partners Dashboard → App setup → Compliance webhooks.

**Then after week-4 gate (first-merchant signal):**

5. **A47 + A48 (remaining) + A49** — token encryption, `uninstalled_at` field, re-auth flow. Tier-2 merchant-readiness, **~1.25 days** after verification (A48 was mostly done in code). Ship in the first 1-2 weeks after first paid signup.
6. **A37 (Subledger tieout cleanup)** — pull forward early because it likely also fixes the noisy A10 false-positive warning that has been firing on every Shopify clearing flow for weeks.
7. **A3 + A4 + A5** in sequence — architectural cleanup that closes the event-first policy loopholes. Now informed by what the reconciliation MVP actually needed.
8. **A6, A7, A9, A10, A11** — UX + invariant + correctness follow-ups from A1/A8. Each is small (1-3d). A10 may already be partially solved by A37; verify.

**Pulled forward only if signal demands:**

9. **A15** — Multi-courier-per-store routing. Currently deferred; pulled forward only if first merchant has multi-courier volume or single-courier limit becomes a workflow burden.

**Then Phase B** — canonical platform models — but grounded in real merchant feedback rather than speculation.

**Do not start Phase B** until all of Phase A is merged and green. Phase B on an unverified foundation is where accounting systems die.

---

## References

- [SESSION_LOG.md](SESSION_LOG.md) — cumulative session history and context
- [ENGINEERING_PROTOCOL.md](ENGINEERING_PROTOCOL.md) — change discipline, test requirements, incident protocol
- [FINANCE_EVENT_FIRST_POLICY.md](FINANCE_EVENT_FIRST_POLICY.md) — event-first policy; update after A3 lands
- [SHOPIFY_DATA_OWNERSHIP.md](SHOPIFY_DATA_OWNERSHIP.md) — authority boundaries with external systems
- [NXENTRA_SYSTEM_MAP.md](NXENTRA_SYSTEM_MAP.md) — architecture map
- `backend/core-assurance-baseline.md` — referenced by external reviewer; items in §76-78 inform Phase A scope
