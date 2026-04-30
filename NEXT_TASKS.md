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

### A2.5. Rename PaymentGateway → SettlementProvider — **~½d**
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

### A12. Settlement-provider dimension layer (structural retrofit on A2.5) — **~2d**
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

### A13. Reconciliation Control Center MVP — **~5d**
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

### A14. Manual settlement CSV import + Expected Bank Deposit convention — **~5-7d**
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

## What to do right now, today

Phase A continues. **A0 done** (`fb0e3d6`), **A1 done** (`b6b52b9`–`7d12432`, 2026-04-28), **A8 done** (`71cb0d7`, `cd7f484`, 2026-04-29), **A2 done** (`d0dd0d2`, 2026-04-30).

**Strategic pivot 2026-04-30:** the architectural cleanup (A3-A5) is deliberately deferred ~3 weeks while the merchant-facing reconciliation product gets built and validated. Foundation work resumes after first-merchant signal. See the "Critical path and parallelism" section above for the full sequence and the week-4 gates that test whether the strategy is right.

**Immediate next steps (in order):**
1. **Invite the first user** (Egyptian Shopify merchant, acquired 2026-04-22). A1's exit criterion is met; further pre-emptive work has diminishing returns vs real-world signal.
2. **A2.5** — Rename PaymentGateway → SettlementProvider. ~½d. Non-negotiable before A12 — the model now covers Bosta/DHL/Aramex/bank-transfer/manual; keeping the gateway name creates immediate technical debt.
3. **A12** — Settlement-provider dimension layer + COD wizard step. ~2d. Locks in the right topology before the chart of accounts grows. Done while waiting for first-user signal.
4. **A13** — Reconciliation Control Center MVP. ~5d. The merchant-visible product spine. Validates the "where is my money?" framing against the first user's real Shopify/Paymob/Bosta data.
5. **A14** — Manual settlement CSV import for Paymob + Bosta + Expected Bank Deposit account convention. ~5-7d. Closes Stage 2 (Gateway → Bank) for Egyptian merchants without waiting for B7/E3 connector code.

**Then after week-4 gate:**
6. **A3 + A4 + A5** in sequence — architectural cleanup that closes the event-first policy loopholes. Now informed by what the reconciliation MVP actually needs.
7. **A6, A7, A9, A10, A11** — UX + invariant + correctness follow-ups from A1/A8. Each is small (1-3d). Pick up between bigger Phase A work as time allows; **A10 and A11 land when first user signals they need them**.

**Pulled forward only if signal demands:**
8. **A15** — Multi-courier-per-store routing. Currently deferred; pulled forward only if first merchant has multi-courier volume or single-courier limit becomes a workflow burden.

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
