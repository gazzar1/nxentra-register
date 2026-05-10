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

### A24. Bank statement frontend column-mapper UI — **STILL PENDING** (~1-2d, frontend)
Backend supports `date_column / description_column / amount_column / reference_column / debit_column / credit_column / date_format` configs on the bank-import endpoint. Frontend hard-codes defaults and provides no mapping UI. **2026-05-04 dry-run worked around this** by using the test pack's Nxentra-format CSV (matches hardcoded defaults). Real merchants will hit "Parsed 0 lines from CSV" the moment they upload their actual bank export. Fix: expose a 2-step import flow on `/accounting/bank-reconciliation/import`: (1) upload + parse-headers preview shows detected columns; (2) merchant maps columns to logical fields with smart pre-fill from filename/column-name heuristics; persist mapping per (company, bank_account). Ship the unified `<CsvMappingDialog>` component bank-rec is the highest-friction surface; settlement-import comes next.

### A25. Manual-match picker filter — surface settlement EBD lines as candidates — **BACKEND DONE** (commit `cc343a6`, 2026-05-03); **frontend wiring still pending**
Backend shipped: new `get_match_candidates_for_bank_line` helper + new endpoint `GET /api/accounting/bank-statements/lines/<pk>/candidates/`. Returns the union of un-reconciled bank-account lines AND un-reconciled EBD lines from `source_module='payment_settlement'` JEs, sorted by amount-proximity then date-proximity. Excludes REVERSED clearance JEs (paired with A19). 6 new tests. Verified on Aljazeera8: helper returns BST-701 EBD line (DR 2,050) as the closest candidate for BNK-003 (1,850), plus BST-702 / BST-703 / MAY01-C in proximity order. **Frontend picker still calls the legacy `/bank-reconciliation/unreconciled/` endpoint** so the BNK-003 → A16 Resolve flow is unreachable from the UI. Frontend swap should land alongside A24.

### A26. Settlement-without-original-order rejection or warning — **BACKEND DONE** (commit `6347db1`, 2026-05-03); **frontend badge still pending**
Backend shipped: `import_settlement_csv` looks up every referenced `order_id` against `ShopifyOrder` for the company. Per-batch result carries `unknown_order_ids: list[str]` — empty for healthy batches, non-empty when one or more rows reference orders the system has never seen. JE still posts (path b — forgive but flag) so merchants with incomplete Shopify history aren't blocked. 3 new tests. Verified on Aljazeera8: PAYMOB-MAY01-C (order 9999) and BST-703 (order 8888) both flagged in `unknown_order_ids`; JEs `JE-35-000013` and `JE-35-000017` posted as expected. **Frontend doesn't surface the badge yet**, so the merchant has no UI signal — file under A35 / new follow-up to add a "Needs Review" badge to the import-result tiles.

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

**Merchant-readiness exit criteria** (revised 2026-05-10 after code verification + A44 ship):
- **A44: DONE 2026-05-10** — code shipped + tested. **Manual operator step still pending:** wire the three webhook URLs into Partners Dashboard → App setup → Compliance webhooks.
- **A46: DONE** — already implemented; verified above.
- **A45 (remaining): tier-1** — must ship **before** the first paying merchant invite. Privacy page exists but Partners Dashboard config + support email are still unset. **Remaining tier-1 effort: ~2h.**
- **A47 + A48 (remaining) + A49: tier-2** — ship in the first 1-2 weeks **after** first paid signup. None block install; all are pre-emptive hardening before the second/third merchant. **Tier-2 effort: ~0.5d + ~1h + ~0.5d ≈ 1.25 days.**
- **App Store listing** (Built for Shopify checklist, public discoverability): deferred until 5-10 happy paying merchants and product polish reaches submission-grade. Months out, not weeks. Distinct from public OAuth distribution (already enabled).

---

## What to do right now, today

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
