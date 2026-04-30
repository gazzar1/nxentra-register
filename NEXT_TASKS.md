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

**Phase A exit criteria:**
- CI green, invariants mandatory, architecture tests enforcing event-first discipline.
- First user can import orders safely.
- Zero projection-emits-event cases; zero direct-write cases in views.
- Foundation is ready for the bigger refactor.

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

```
A0 (CI/invariants) ─┐
A1 (dry-run)        ─┤
A2 (PaymentGateway) ─┼─► A3 (reactors) ─► A5 (bank/FX cleanup) ─┐
A4 (arch tests)     ─┘                                          │
                                                                 ▼
                    B1 (inbox)    ──────────────────┐
                    B2 (schema evo, parallel B1) ───┤
                                                    ▼
                    B3 (canonical design) ─► B4 (build) ─► B5 (Shopify) ─► B6 (Stripe) ─► B7 (Paymob)
                                                                                              │
                                                                                              ▼
                                                                       C1 ─► C2 ─► C3 (reconciliation UI)
                                                                                              │
                                                                                              ▼
                                                                                        E1, E2, E3 …

                    D1 ─► D2 ─► D3 (can start at B4, run parallel to B-tail and C)
```

**Longest pole:** B5 (Shopify-to-canonical migration). Everything downstream waits.

---

## Decision points (revisit before Phase B starts)

1. **Paymob timing.** If the first user needs Paymob within 2-3 months, consider a throwaway Paymob integration in Phase A that gets rewritten in B7.
2. **Phase C vs D ordering.** Investor/demo pressure → D first. Operational correctness → keep current order.
3. **Shadow-write vs clean cutover for B5.** Shadow-write safer, doubles write load briefly. Clean cutover faster, riskier if anything slips.
4. **Inbox scope in B1.** Minimal (write raw + normalize + emit), or full (retries + DLQ + operator UI)? I'd ship minimal first, add operator UI in Phase E if real incidents demand it.

---

## What to do right now, today

Phase A continues. **A0 done** (`fb0e3d6`), **A1 done** (`b6b52b9`–`7d12432`, 2026-04-28), **A8 done** (`71cb0d7`, `cd7f484`, 2026-04-29), **A2 done** (`d0dd0d2`, 2026-04-30). Remaining:
- **A3 + A4 + A5** in sequence — the architectural cleanup that closes the event-first policy loopholes.
- **A6, A7, A9, A10, A11** — UX + invariant + correctness follow-ups from A1/A8. Each is small (1-3d). Pick up between bigger Phase A work as time allows; **A10 and A11 land when first user signals they need them**.

**Do not start Phase B** until all of Phase A is merged and green. Phase B on an unverified foundation is where accounting systems die.

---

## References

- [SESSION_LOG.md](SESSION_LOG.md) — cumulative session history and context
- [ENGINEERING_PROTOCOL.md](ENGINEERING_PROTOCOL.md) — change discipline, test requirements, incident protocol
- [FINANCE_EVENT_FIRST_POLICY.md](FINANCE_EVENT_FIRST_POLICY.md) — event-first policy; update after A3 lands
- [SHOPIFY_DATA_OWNERSHIP.md](SHOPIFY_DATA_OWNERSHIP.md) — authority boundaries with external systems
- [NXENTRA_SYSTEM_MAP.md](NXENTRA_SYSTEM_MAP.md) — architecture map
- `backend/core-assurance-baseline.md` — referenced by external reviewer; items in §76-78 inform Phase A scope
