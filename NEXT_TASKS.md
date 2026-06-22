# Next Tasks

Strategic roadmap drafted 2026-04-25 and updated the same day after incorporating an independent architectural review. Follows the discipline in [ENGINEERING_PROTOCOL.md](ENGINEERING_PROTOCOL.md): finance work is event-first, projections are derived, auditability beats convenience, and every change type carries its required tests.

**Foundation assessment:** **B+ / A-** on the accounting core, roughly **6/10** on the full integration-grade, AI/MCP-ready operating core. Hard parts (event sourcing primitives, CQRS, write barriers, RLS, invariant tests) are genuinely strong. Gaps are on the perimeter: ingestion resilience, schema evolution, module governance consistency outside accounting, CI-enforced invariants, and agent-ready command surface.

**Framing:** Nxentra is a canonical financial event and reconciliation engine. Shopify is the first proof. Every canonical abstraction must be justified by a real concrete integration need — not speculative.

**Estimated budget:** Phases A-D ≈ 3-4 months focused work. Phase E is ongoing.

---

## Phase A — First-user unblock + foundation hardening (this week to 2 weeks)

Ship these before the first real user (acquired 2026-04-22) imports real orders, and before any large refactor.

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

### A9. Item auto-create fallback when Shopify product has no SKU — **~1d** (correctness, surfaced by A1)
Today `_auto_create_item_from_line` only fires when `sku` is non-empty. Egyptian merchants frequently sell products without SKUs (small operations, custom items). Fall back to using `shopify_product_id` as the Item code, with the product title as the name. Same auto-fill of GL accounts as A8.

### A10. AR tie-out invariant accommodates non-AR-Control posting profiles — **~2-3d** (invariant, surfaced by A1)
`post_journal_entry` logs `"AR tie-out mismatch: AR Control (X) != Customer balances (Y)"` warnings whenever a customer uses a non-AR-Control posting profile (e.g. Shopify Clearing — where `_ensure_shopify_sales_setup` deliberately points the SHOPIFY-NXENTRA-* customer at the clearing account, not 12000 AR Control). The data is consistent (JEs balanced, customer balance matches debits) — the invariant is overly strict. Fix: tie-out should sum the actual control accounts referenced by the posting profiles in use, not just `AR_CONTROL`. Will silence false positives across all integrated platforms (Shopify, Stripe, future Paymob).

### A11. Shopify JE should respect Item-level GL account overrides — **~2-3d** (correctness, surfaced by A8 review)
The `shopify_accounting` projection's `_handle_order_paid` builds **one aggregate revenue line per order** posted to the company's `SALES_REVENUE` ModuleAccountMapping (account 41000) — it does not iterate line items or look up `Item.sales_account` per SKU. So if a merchant edits HEAD-001's Sales Account from "Sales Revenue" (41000) to "Headphones Revenue" (41001), manual invoices for HEAD-001 will credit 41001 but Shopify-imported orders for HEAD-001 will keep crediting 41000. Manual invoices respect Item.sales_account; Shopify-imported invoices don't. To fix: refactor `_handle_order_paid` to iterate `line_items`, look up Item by SKU, create one revenue line per item using `item.sales_account` (fall back to mapping if None). Need to think through tax + discount allocation per line. Not blocking the first user — company-level default works correctly until they want per-product revenue routing. Deferred deliberately so we can see whether the first user actually customizes per-item before pre-building.

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

### A45. Privacy policy page + support email — **~2h** (Shopify policy-mandatory, surfaced on install screen) — *partially DONE per 2026-05-10 verification*
Partners Dashboard requires a `Privacy policy URL` and `Support email` on the app config — fields are visible to merchants on the install consent screen. **2026-05-10 verification:** [frontend/pages/privacy.tsx](frontend/pages/privacy.tsx) already exists with substantive v1.0 content (data collected, OAuth-token clause, account info, voice data, etc.). What remains: (1) audit page for explicit Shopify-specific GDPR rights coverage (matches A44's `customers/data_request` / `customers/redact` / `shop/redact` semantics), (2) set up `support@nxentra.com` (forwarding to `mohamed.algazzar@gmail.com` until a help desk exists), (3) wire both URL + email into Partners Dashboard. Original ~0.5d estimate downscoped to ~2h since the page itself is built.

### A47. Access token storage encryption at rest — **~0.5d** (security baseline)
`ShopifyStore.access_token` is stored plaintext in Postgres ([models.py:41-45](backend/shopify_connector/models.py#L41-L45) is plain `CharField(max_length=255)` — note the help_text claims *"encrypted at rest in production"* but no encryption layer exists; **fix the misleading help_text when implementing**). A DB breach (backup leak, rogue read replica, SQL injection elsewhere) hands an attacker every connected merchant's full Shopify API access — orders, customers, payouts, ability to refund / fulfill / push fake orders. Encrypt with `cryptography.fernet` keyed off a `SHOPIFY_TOKEN_KEY` env var. Migration encrypts existing rows in place. Read path decrypts on attribute access. Add a key-rotation runbook (re-encrypt all tokens with a new key, no Shopify-side re-auth needed). Tests: round-trip encrypt/decrypt, old-key tokens still readable during rotation, ciphertext different across rows (Fernet IV randomness).

### A48. app/uninstalled webhook handler — **~1h remaining** (lifecycle correctness) — *mostly DONE per 2026-05-10 verification*
**2026-05-10 verification:** handler exists at [commands.py:678-704](backend/shopify_connector/commands.py#L678-L704) and is wired in the webhook router at [views.py:198](backend/shopify_connector/views.py#L198). Already does: HMAC-verify (via the shared verifier upstream), set `status=DISCONNECTED`, blank `access_token`, set `webhooks_registered=False`, emit `SHOPIFY_STORE_DISCONNECTED` event (the audit trail). The "halt scheduled syncs" requirement is also satisfied for free — [tasks.py:51](backend/shopify_connector/tasks.py#L51) only iterates `status=ACTIVE` stores, and [tasks.py:96-97](backend/shopify_connector/tasks.py#L96-L97) early-returns `skipped` for non-ACTIVE stores. **Remaining:** add `uninstalled_at = models.DateTimeField(null=True, blank=True)` to `ShopifyStore` and stamp it in the handler — gives us a clean retention boundary and lets future GDPR `shop/redact` cleanup query "stores uninstalled >30d ago." Original ~2h estimate downscoped to ~1h.

### A49. Re-auth flow on token expiry / scope rotation — **~0.5d** (lifecycle correctness)
If a merchant's Shopify session token is revoked (Shopify password change, suspicious activity flag, manual revocation) or we add a new scope in a future release, every Shopify API call returns 401 / 403 and the connector silently fails — the merchant just sees stale data with no signal that re-auth is needed. Add: 401/403 from Shopify flips `ShopifyStore.needs_reauth = True`; the wizard's Shopify Setup step (and a banner in the connected-store settings) detects the flag and shows "Reconnect to Shopify" instead of "Connected"; OAuth retry path reuses the existing flow. Pair with A48 (uninstall) so both abnormal states surface the same UX pattern.

---

## Shopify connector bugs surfaced 2026-05-15 during App Store reviewer-store setup

Three bugs surfaced ~02:30 EEST while populating Nxentra `Shopify_R` company from fresh dev store `nxentra-reviewer-store.myshopify.com` (created for App Store reviewer test account, after Heba was lost 2026-05-11 to the "this app is under review" install banner). All three affect any new merchant connecting Shopify; all three should be fixed before continuing the App Store submission demo via the proper path (rather than the manual-data workaround).

### A53. Re-request Level 1 Protected Customer Data access + re-enable PII webhook subscriptions — **~30min code + 0-7d Shopify review** (Shopify connector, post-submission)
Five declarative webhook subscriptions (`orders/create`, `orders/paid`, `orders/cancelled`, `refunds/create`, `fulfillments/create`) were stripped from [shopify.app.toml](shopify.app.toml) on 2026-05-17 because `shopify app deploy` rejected them with *"This app is not approved to subscribe to webhook topics containing protected customer data."* These topics carry customer PII in their payloads; Shopify requires Level 1 (or higher) Protected Customer Data approval to subscribe. Earlier in App Store submission prep we set "Doesn't need access to protected customer data" (Level 0) to ship faster; this is the trade. **Until this lands, real-time order sync is replaced by the periodic `sync_shopify_all` Celery task (4-hour cadence).** Adequate for beta; merchants WILL notice the latency at >dozen-order/day volume. **Fix:** (1) Partners Dashboard → API access requests → reopen "Protected customer data access" with the "Other" reason ("Accounting reconciliation: build AR sub-ledger from orders, match payouts to customer transactions" — same justification as the original submission), submit, wait for approval. (2) Once approved, re-add the 5 topics to the `[[webhooks.subscriptions]]` block in shopify.app.toml. (3) `shopify app deploy` → releases nxentra-sync-N. (4) Verify webhook deliveries land at `/api/shopify/webhooks/` and route correctly. Don't request Level 2 (PII fields like name/email/phone/address as separately-approved fields) unless we actually use those fields in product features — Level 1 is sufficient for the webhook subscription side. **Do not** attempt during the in-flight App Store listing review — wait for listing approval first to avoid restarting that review.

### A54. Add `read_shopify_payments_disputes` scope + re-enable dispute webhook subscriptions — **~15min code + Shopify deploy** (Shopify connector, post-submission)
The `disputes/create` and `disputes/update` declarative subscriptions failed on 2026-05-17 `shopify app deploy` with *"Missing scope for webhook topic: disputes/create (read_shopify_payments_disputes)"*. We have `read_shopify_payments_payouts` but not `read_shopify_payments_disputes` — they're separate scopes. **Fix:** add `read_shopify_payments_disputes` to the `scopes = "..."` line in [shopify.app.toml:10](shopify.app.toml#L10), re-add the two dispute topics to the `[[webhooks.subscriptions]]` block, run `shopify app deploy`. Existing stores will need to re-authorize (OAuth scope expansion forces re-grant) — A49's re-auth flow handles the UX. Chargeback handling in `commands.py` already routes `disputes/*` correctly (verified via the webhook router map), so no handler work needed. **Defer until first chargeback complaint or first paying merchant** — dispute tracking is meaningful only with real payment volume.

### A56. Failed OAuth leaves orphan PENDING ShopifyStore records — **~30min** (Shopify connector, surfaced 2026-05-17)
`ShopifyInstallView.post` creates a PENDING `ShopifyStore` record with `oauth_nonce` BEFORE redirecting to Shopify OAuth. If `complete_oauth` later fails (e.g., the shop is already linked to another Nxentra company → `IntegrityError`), the PENDING record stays in the DB indefinitely. Surfaced 2026-05-17 when DB query on `Shopify_R` company showed two stores: `aljazeera7-store.myshopify.com` (PENDING from failed first attempt) + `nxentra-reviewer-store.myshopify.com` (ACTIVE from successful second attempt). The orphan polluted the UI's "Previously connected to..." hint and caused A57. **Fix:** in `complete_oauth` error branches, `store.delete()` if `store.status == PENDING` and the store has no successful OAuth history. Or wrap the whole install + callback in a saga that rolls back on failure.

### A58. Item record's "Product Page URL" external link field does not persist — **~30min** (sales/inventory, surfaced 2026-05-17)
Item edit form has an "External Link → Product Page URL" field (e.g., `https://instagram.com/p/...`). Value typed in is not saved on submit. Surfaced 2026-05-17 during Plan B manual demo data creation in `Shopify_R`. **Diagnosis needed:** check the Item model — `product_page_url`/`external_url` field exists? If not, model field missing. If yes, the serializer / form / update view probably isn't including it in the writable fields list. Likely a one-field-omission bug in the Item update path.

### A59. Vendor creation fails with "Failed to create vendor" — **~0.5d** (purchases, surfaced 2026-05-17, BLOCKING vendor flow)
`/accounting/vendors/new` form submits and gets back a generic "Failed to create vendor" error toast. Surfaced 2026-05-17 while creating demo data for App Store reviewer in `Shopify_R`. No stack trace shown to user. **Diagnosis needed:** check the vendor create endpoint (likely `purchases/views.py` `VendorCreateView` or similar). Server-side error is being swallowed by the frontend. Could be: missing required field with no client-side validation, FK constraint failure, RLS/permission issue under fresh-tenant setup. Pull the actual Sentry stack trace from production. Workaround for App Store demo: skip vendors entirely — the screencast only covers AR / sales side, not purchases.

### A123. Add explicit Sentry `before_send` PII redaction filter — **~1-2h** (security/privacy, surfaced 2026-06-02 during PCD Level 1 application)
[settings.py:393](backend/nxentra_backend/settings.py#L393) sets `send_default_pii=False`, which prevents Sentry SDK from auto-capturing request/user PII. However, PII can still leak via exception messages and log call arguments (e.g., a SQL error that includes a customer's email in the parameter list). Ship a `before_send` hook that scrubs known PII patterns (email, phone, address fields, full PAN) from `event['logentry']['message']`, `event['exception']['values'][].value`, and breadcrumb messages before transmission. Reference: the DLP doc ([docs/security/data-loss-prevention.md](docs/security/data-loss-prevention.md) §5) calls this out as a roadmap item — closing A123 lets that section drop the "roadmap" qualifier.

### A124. GDPR redact webhooks — programmatic data deletion — **~2-3d** (Shopify compliance, surfaced 2026-06-02 during PCD Level 1 application)
The `customers/redact` and `shop/redact` webhook handlers ([commands.py:740](backend/shopify_connector/commands.py#L740), [:761](backend/shopify_connector/commands.py#L761)) currently only audit-log the request — the handlers themselves carry comments saying "actual deletion job is a future task" / "actual wipe job is a future task." Shopify's GDPR webhooks policy requires apps to actually delete the affected data within the SLA (30 days for customer redact, 90 days for shop redact). Until A124 ships, deletion in response to a verified redaction request is performed manually within the SLA. Build a deletion job (Celery task) that: (a) for `customers/redact`, anonymizes the affected Customer row and any derived SalesInvoice/CustomerReceipt records by replacing PII fields with hashed placeholders; (b) for `shop/redact`, purges the entire tenant's data including events, projections, audit rows, and Shopify-derived records. Both must emit a completion audit event and be replayable safely (idempotent). Ship before first paid merchant volume.

### A130. Demo-data: Shopify order-number collision between seed and live orders — ✅ **CODE DONE 2026-06-19** (`f2512dc`); reseed pending
**✅ Shipped (code):** `seed_shopify_demo` order numbers rebased to #9001+ (`base_num = 9001`, was 1001) so a freshly-seeded company can't collide with a real store's #1001 sequence. **Reseed of the already-polluted Shopify_R still pending** — and note: a re-seed of an already-seeded company won't replace #1001 with #9001 because `--flush` leaves the derived `source="shopify"` SalesInvoices (idempotent on the unchanged `shopify_order_id`), so the books keep #1001. NEXT SESSION decision (user leaning **option B: fresh demo company**, which gets pristine #9001 data). See [[project_a134_a136_store_resolution]]. *(Original scope below.)*

`seed_shopify_demo.py` seeds orders numbered #1001–#1030 with synthetic Shopify IDs (`5000000000+`). The reviewer demo store's real Shopify sequence also starts at #1001, so live/cast orders now occupy #1001–#1004 (incl. cast orders #1003/#1004, $33, 2026-06-16) and coexist with seed rows reusing the same display numbers. Dedup is by `(company, shopify_order_id)` (`shopify_connector/models.py:311`, verify), so there is **no crash or double-booking** — but the Paymob reconciliation drill-down shows #1001/#1002 as EGP/April seed lines while Shopify → Orders shows #1001/#1002 as USD/June real orders (duplicate numbers, mismatched currency + date), visible if a provider row is expanded. Worked around for the 2026-06-16 App Store resubmission by removing the "expand provider to drill into orders" step from the reviewer test instructions. **Fix:** reseed order numbers at a non-colliding base (e.g. #9001+) well above any real store sequence, or warn/flag on duplicate display number within a company. Cosmetic only — no financial impact. Related to [[A129]] currency consistency.

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

### A108. Dashboard "Total Revenue" doesn't reconcile with Shopify Connector dashboard or Reconciliation page — **PENDING ~1h, investigate-only** (post-listing punch list, surfaced 2026-05-27; only act if screencast playback exposes it)
Observed on Shopify_R during 2026-05-27 verification:
- `/dashboard` Total Revenue: **USD 39,448.78**
- `/shopify` Revenue (Processed): **USD 16,800.00**
- `/finance/reconciliation` Total Expected: **USD 16,950.00**

The reconciliation arithmetic is internally consistent (16,950 sold − 1,700 settled = 15,250 open balance). The `/shopify` figure is close to expected sold (10 USD seed orders, sum ≈ 16,800-16,950 depending on tax/shipping inclusion). The `/dashboard` Total Revenue is ~2.35× larger — likely the global dashboard sums all P&L revenue-class accounts (41000 Sales + 42000 Shipping + possibly a returns-class line) and may also pick up seeded opening balances on the chart of accounts.

Not wrong per se; the concern is a screencast viewer notices the gap and asks where 39k comes from. Investigate only if the playback shows the discrepancy on-camera; otherwise let it ride.

**Suggested probe:** `SELECT account.code, account.name, SUM(amount) FROM journal_line WHERE company_id = 41 AND account.type IN ('REVENUE', 'INCOME') GROUP BY account.code, account.name ORDER BY SUM(amount) DESC;` to identify which account(s) push the dashboard number above the connector number.

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

### ✅ CURRENT STATE — 2026-06-19 (supersedes the 2026-05-17 block below)

**App Store: APPROVED + PUBLISHED** (ref 114779, app version **nxentra-sync-9**) — status "Limited visibility." HOLD "Make fully visible" until the demo is reviewer-clean (reseed) and landing-page pricing matches the listing (already reconciled to "Free to start").

**Post-launch sprint SHIPPED + DEPLOYED + verified live** (commits `4f446c8`, `1bad6c6`, `f2512dc`): **A132, A133, A134** (+ siblings **A136** disconnect guard, **C/A** closed-period quarantine), **A125** (COGS backfill), **A126** (read_all_orders + deployed to Shopify), **A130** (code), **A135** (lag metric). **A131** reviewed → leave as-is. No DB migrations across the sprint. Server pulled + restarted; `projection_health` reads "All projections up to date." See [[project_a134_a136_store_resolution]].

**Open / next session:**
1. **Shopify_R reseed** — user leaning **option B: fresh demo company** (`seed_shopify_demo --flush --company-slug <new>` → pristine #9001). Optional: build a `shopify_hard_reset` cmd to clean company 41 itself. Safety nets first (git tag + `pg_dump`).
2. **A126 activation** — existing stores must **reconnect** to grant `read_all_orders` (managed install); then verify a >60-day import.
3. **A136 frontend deploy** — `cd frontend && npm run build && pm2 restart nxentra-web` (not picked up by the api/celery restart).
4. **Not done this sprint** (still open): **A127** (item price + GL-account edit-page display bug), **A128** (CSV DD/MM sniffer + native date pickers), **A129** (settlement clearance idempotency), **A44/A45 compliance verify** (GDPR webhook URLs registered/responding + privacy URL + support email in Partners Dashboard).

---

**🔴 2026-05-17 PRIORITY — finish App Store listing submission.** *(SUPERSEDED — listing approved + published; kept for history.)* A50 + A51 shipped 2026-05-16 (commit `104a453`); A52 has diagnostic logging in place. `shopify.app.toml` webhook subscriptions narrowed to 3 unblocked topics (products/*, app/uninstalled) — see A53/A54 for the deferred ones. **Today's path:** (1) deploy backend (git pull + migrate + restart) to droplet. (2) `shopify app deploy` from laptop → release nxentra-sync-5. (3) retest A52 on Shopify_R store, capture diagnostic logs. (4) if A52 still 0 orders, fall back to Path 3 — create demo data natively in Nxentra. (5) capture 3 screenshots + record screencast (3-8 min unlisted YouTube) + paste reviewer credentials into App Store listing form + Submit for review.

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

## Phase S — Stripe integration via a canonical payment/settlement layer (2026-06-22)

Full design: [docs/adr/0002-canonical-payments-stripe-adapter.md](docs/adr/0002-canonical-payments-stripe-adapter.md). Stripe is the **first reference adapter** for a small canonical provider-payment layer **inside** the existing event/projection architecture (NOT a parallel `payments_core`). It rides **alongside** Shopify (which stays untouched — the risky Shopify→canonical migration, old B5, is explicitly out of scope) by emitting the same provider-agnostic `PAYMENT_SETTLEMENT_RECEIVED` events the reconciliation engine already consumes. Grounding: ~80% of the "canonical module" already exists (`ReconciliationLink`=BankMatch, `ReconciliationException`=ReconciliationCase, `PlatformSettlement`/`PAYMENT_SETTLEMENT_RECEIVED`=ProviderPayout, `ModuleAccountMapping`+`SettlementProvider`=AccountMapping).

### S0. Financial-trust hardening — **~1w** (BLOCKING; no real merchant connects Stripe until this lands)
These are **financial-correctness** bugs, not cleanup (fees=0 → Stage 2 lies; module-key split → books silently incomplete; double-post → accounting lies; direct writes → replay lies; sign errors → reconciliation lies).
- **Module-key unification (test-gated).** One canonical `module_key_for_provider()` routing all 3 sites: order/refund/dispute JEs ([platform_connectors/projections.py:94](backend/platform_connectors/projections.py#L94) `platform_stripe`), settlement JEs ([payment_settlement_projection.py:205](backend/accounting/payment_settlement_projection.py#L205) `stripe_connector` fallback), settlement EBD lookup ([bank_reconciliation.py:633](backend/accounting/bank_reconciliation.py#L633) **hardcoded `shopify_connector`**). Stripe → `platform_stripe`; shopify/paymob/bosta stay `shopify_connector`. Resolve shared-vs-per-provider EBD explicitly. Gate behind a failing characterization test FIRST.
- Adapter registry replacing the `if code=='paymob'/'bosta'` dispatch ([settlement_imports.py:435](backend/accounting/settlement_imports.py#L435)).
- `capabilities` property on `BasePlatformConnector` + `ParsedProviderTransaction`/`ParsedPayoutLine` DTOs in `platform_connectors/canonical.py`.
- Auth-agnostic `StripeAccount`: `auth_type` (restricted_key|oauth) + encrypted `credential_ref`; re-key webhook resolution off the Connect-account-id.
- **Raw ingestion cache** model `(provider, object_type, external_id, api_version, fetched_at, source, payload_hash, payload_json)` — explicitly raw/source-only (the event store keeps normalized output, not replayable raw input). Replaces the scattered `raw_payload` columns.

### S1. Stripe read-only adapter — **~2w**
`stripe` SDK + pull client (Payouts + Balance Transactions + Payout Reconciliation report) with sync cursor in `StripeAccount.last_sync_at`; `_setup_platform_accounts` seed under the unified key + `SettlementProvider(stripe, GATEWAY)`; emit `PAYMENT_SETTLEMENT_RECEIVED` with **derived** fees (fix fees=0 at [connector.py:209](backend/stripe_connector/connector.py#L209)); self-serve connect UI (replace "Contact your administrator"); fold/demote direct `store_charge/store_refund/store_payout` writes.

### S2. Stage-2 payout-line breakdown FIRST (the convergence with the recon Stage-2 table) — **~2w**
Populate `line_items[]` from Balance Transactions filtered by `payout=po_…`; emit `PROVIDER_PAYOUT_RECONCILED`; NEW sole-writer `PaymentsProjection` (uuid5 + `projection_writes_allowed()` guard) materializing `ProviderTransaction`/`ProviderPayoutLine`; re-point `reconcile_payout` variance at `ReconciliationException` via the event path (remove the direct `verified`-flag mutation); two-phase emit for `payout.reconciliation_completed` lag.

### S2-gate. Architecture test — Paymob/Bosta through the canonical projection — **~2d** (early, cheap)
They already emit `PAYMENT_SETTLEMENT_RECEIVED` with `line_items[]`. Run them through the new `PaymentsProjection` and confirm the read-models populate for messy CSV/COD data (uncollected, returns, multi-gateway). If it only works on Stripe's clean balance transactions, the Stripe-shaped leak surfaces while the abstraction is still soft. A continuous test, not a final phase.

### S3. Shopify↔Stripe order match + dispute/reserve/adjustment events — **~2w**
Generalize A26/A39 dedup beyond `invoice__source='shopify'` *additively*; dispute-resolution (won/lost/funds-withdrawn) + reserve + adjustment event types + `PlatformAccountingProjection` branches (**events are sole owner** — deprecate command-layer `DISPUTE_WON` to avoid double-post); NEW `PROVIDER_RESERVE`/`PROVIDER_ADJUSTMENT` roles + seed + `check_required_roles` + backfill for connected companies; derive chargeback fee (fix hardcoded $15 at [connector.py:227](backend/stripe_connector/connector.py#L227)).

### S4. Bank deposit match end-to-end — **~1w**
Verify Stripe EBD → `ReconciliationLink` flows unchanged ([matching.py:212](backend/reconciliation/matching.py#L212)); negative payouts (net debit) + per-currency clearing + realized-FX computation (reuse core `REALIZED_FX_GAIN/LOSS`); orphan Stripe-deposit exceptions on `/finance/exceptions`.

### S5. Second *pull* provider (Paymob API or PayPal) — **~1.5w**
Self-registers via the S0 registry; confirm only adapter + seed + capability declaration needed — no new events/projections/recon code. Document the adapter onboarding checklist.

---

## References

- [SESSION_LOG.md](SESSION_LOG.md) — cumulative session history and context
- [ENGINEERING_PROTOCOL.md](ENGINEERING_PROTOCOL.md) — change discipline, test requirements, incident protocol
- [FINANCE_EVENT_FIRST_POLICY.md](FINANCE_EVENT_FIRST_POLICY.md) — event-first policy; update after A3 lands
- [SHOPIFY_DATA_OWNERSHIP.md](SHOPIFY_DATA_OWNERSHIP.md) — authority boundaries with external systems
- [NXENTRA_SYSTEM_MAP.md](NXENTRA_SYSTEM_MAP.md) — architecture map
- `backend/core-assurance-baseline.md` — referenced by external reviewer; items in §76-78 inform Phase A scope
