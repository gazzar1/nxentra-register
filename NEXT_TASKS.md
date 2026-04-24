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

### A1. Phase 1 dry-run on fresh Shopify dev store — **0.5d**
Validate end-to-end with actual Shopify webhooks: paid order books invoice; pending COD lands as `PENDING_CAPTURE` with no JE; pending→paid upgrades stub + books invoice; cancel pending flips to `CANCELLED`; historical import via wizard works.

Exit: first user can connect without fear of corrupted books.

### A2. PaymentGateway mapping (tactical slice) — **1d**
Single table `PaymentGateway(source_code, clearing_account_id, display_name)`. Invoice posting reads it to route Paymob / PayPal / Manual (COD) to per-gateway clearing accounts.

Tactical precursor to Phase B canonical work — build now because the alternative is re-posting every invoice later.

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

Phase A in its entirety. Specifically:
- **A0 first** — invariant tests in CI on Postgres. Until this exists, no further refactor is safe.
- **A1 + A2** in parallel (small).
- **A3 + A4 + A5** in sequence after A0 lands.

**Do not start Phase B** until all of Phase A is merged and green. Phase B on an unverified foundation is where accounting systems die.

---

## References

- [SESSION_LOG.md](SESSION_LOG.md) — cumulative session history and context
- [ENGINEERING_PROTOCOL.md](ENGINEERING_PROTOCOL.md) — change discipline, test requirements, incident protocol
- [FINANCE_EVENT_FIRST_POLICY.md](FINANCE_EVENT_FIRST_POLICY.md) — event-first policy; update after A3 lands
- [SHOPIFY_DATA_OWNERSHIP.md](SHOPIFY_DATA_OWNERSHIP.md) — authority boundaries with external systems
- [NXENTRA_SYSTEM_MAP.md](NXENTRA_SYSTEM_MAP.md) — architecture map
- `backend/core-assurance-baseline.md` — referenced by external reviewer; items in §76-78 inform Phase A scope
