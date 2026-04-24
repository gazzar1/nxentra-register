# Next Tasks

Strategic roadmap drafted 2026-04-25 following an architectural audit of Nxentra against the long-term vision of a cutting-edge event-sourced CQRS accounting truth engine with first-class API/MCP/AI surface and universal reconciliation.

Foundation assessment: **B+ / A-**. Hard parts (event sourcing, CQRS, write barriers, RLS, invariant tests) are genuinely strong. Gaps are on the surface layer: canonical models across connectors, generic reconciliation, agent-ready command surface, and extensibility tooling.

The plan below is the path from "good Shopify accounting app" to "defensible accounting truth engine for commerce." Estimated 3-4 months focused work for Phases A-D; Phase E is ongoing.

---

## Phase A — First-user unblock (this week)

Ship these before the first real user (acquired 2026-04-22) starts imports.

### A1. Phase 1 dry-run on fresh Shopify dev store — **0.5d**
Validate end-to-end with actual Shopify webhooks:
- Paid order books invoice correctly
- Pending COD order lands as `PENDING_CAPTURE`, no JE
- Pending → paid transition upgrades stub and books invoice
- Cancel pending order flips to `CANCELLED`, no JE
- Historical import via onboarding wizard imports paid orders

Exit: first user can connect without fear of books being wrong.

### A2. PaymentGateway mapping (tactical slice) — **1d**
Single table `PaymentGateway(source_code, clearing_account_id, display_name)`. Invoice posting reads it to route orders from different payment methods (Paymob, PayPal, Manual/COD) to per-gateway clearing accounts instead of a single bucket.

This is a small sliver of the Phase B canonical work — build it now because the alternative is re-posting every invoice later.

### A3. Extract 3 projection-emits-event cases to commands — **~3d total**
Close the "documented exceptions" policy hole before more verticals cite the precedent:
- `clinic/projections.py:320` (rent.due_posted → JE)
- `shopify_connector/projections.py:1043` (payout settlement)
- `projections/property.py:671` (property-specific event)

Tighten `FINANCE_EVENT_FIRST_POLICY.md` to "zero exceptions" afterward.

---

## Phase B — Canonical platform models (4-6 weeks)

**The lynchpin refactor.** Every feature above this layer becomes generic once done. Every new connector becomes a one-week job instead of two.

### B1. Design + ADR — **2-3d**
Canonical models: `PlatformOrder`, `PlatformPayment`, `PlatformRefund`, `PlatformSettlement`, `PlatformDispute`. Attribution via `source_type`, `source_id`, `raw_payload` JSONB. Write decision record.

Depends: Phase A complete (first user stable).

### B2. Build canonical models + migrations — **3-5d**
New app `commerce` (or extend `platform_connectors`). ORM, RLS, write barriers, indices, unit tests.

### B3. Migrate Shopify to canonical models — **2 weeks**
Rewrite `process_order_paid/pending/cancelled`, `process_refund` commands to target canonical models. Projections consume canonical events. Use **shadow-write pattern**: write both old `ShopifyOrder` and new canonical rows for 1 week, then cutover + drop old tables. All Shopify tests updated.

**Critical risk.** Plan a 2-hour off-peak cutover window with rollback script ready.

### B4. Migrate Stripe to canonical models — **3-5d**
Thin (Stripe connector is skeletal today).

### B5. Build Paymob connector on canonical models — **1 week**
Proof the pattern works with a real new integration. Webhook verification, canonical mapping, sandbox testing.

**Exit criteria for Phase B:** adding Paymob required touching fewer than 5 files outside its own folder. That's the test of "not bitter."

---

## Phase C — Generic reconciliation engine (2-3 weeks, after B)

Can parallel with Phase D.

### C1. Design reconciliation contracts — **2-3d**
`ReconciliationSource`, `Matcher` interfaces. `ReconciliationRun` model. Proposed-JE generator.

### C2. Engine core — **1 week**
Runner, three built-in matchers (exact, amount+date, fuzzy-confidence), unmatched report, proposed-JE creation via commands.

### C3. Three-way UI: Bank ↔ GL ↔ Platform(s) — **1 week**
Single React view, all sides side-by-side, filter / match / unmatch / bulk actions.

**Exit criteria:** from the UI, reconcile Bank CSV + Shopify payouts + Stripe payouts + Paymob payouts in a single view without any reconciliation code being platform-specific.

---

## Phase D — Agent-ready command surface (2-3 weeks, can parallel C)

The goal isn't MCP itself — it's making commands self-describing. Once they are, every surface (OpenAPI, MCP, CLI, debug) becomes trivial.

### D1. OpenAPI via drf-spectacular — **2-3d**
Install, configure, annotate endpoints, expose at `/api/schema/` and `/api/docs/`. Unblocks external integrators.

### D2. Declarative command schemas — **1-1.5 weeks**
Pydantic or dataclass schemas for every command input + output. Pre-command validator. Permission declarations. Side-effect declarations. Command registry with reflection endpoint `/api/commands/`.

### D3. MCP server wrapping command registry — **3-5d**
Safety envelope: dry-run mode, permission checks, allowlist. Read-only operations first, write operations opt-in.

**Exit criteria:** an LLM agent can discover available commands, preview their effect, and execute them — all schema-validated, all audit-logged.

---

## Phase E — Proliferation (ongoing, after A-D)

| # | Ticket | Estimate |
|---|---|---|
| E1 | Connector scaffolder (`manage.py new_connector`) | 2-3d |
| E2 | Connector contract doc + vertical module guide | 2d |
| E3 | Bosta connector (CSV first, API later) | 2d CSV / 1wk API |
| E4 | Inventory Opening Balance step in onboarding wizard (pull stock + costs from Shopify, post OB JE) | 3-5d |
| E5 | Platform Settlements page under Finance (unified payouts / disputes / fees UI) | 1 week |
| E6 | Concurrent-write / deadlock tests | 3-5d |
| E7 | Cross-source reconciliation edge-case tests | 3-5d |
| E8 | Restock handler via StockLedger (move `_handle_refund_restock` out of projection into command) | 2-3d |

---

## Critical path and parallelism

```
A1 ─┐
A2 ─┼──► B1 ──► B2 ──► B3 ──► B4 ──► B5 ──► C1 ─► C2 ─► C3
A3 ─┘                                                 ║
                                                      ╚═► E1, E2, E3

                                    D1 ─► D2 ─► D3   (can start at B2, runs in parallel)
```

**Longest pole:** B3 (Shopify-to-canonical migration). Everything downstream waits on this.

---

## Decision points (revisit before starting Phase B)

1. **Paymob timing.** If the first user needs Paymob within 2-3 months, consider a throwaway Paymob integration in Phase A that gets rewritten in B5.
2. **Phase C vs D ordering.** Investor/demo pressure → flip to D first. Operational correctness pressure → keep current order.
3. **Shadow-write vs clean cutover for B3.** Shadow-write is safer but doubles write load briefly. Clean cutover is faster but riskier.

---

## What to do right now, today

Nothing from Phases B-E yet. Finish Phase A (dry-run + first-user support + payment gateway mapping), get 2-3 weeks of real production feedback, *then* start Phase B informed by one real user's actual pain points rather than hypothetical future connectors.

Premature refactoring is worse than refactoring informed by production reality.

---

## References

- Session history and context: [SESSION_LOG.md](SESSION_LOG.md)
- Event sourcing policy: [FINANCE_EVENT_FIRST_POLICY.md](FINANCE_EVENT_FIRST_POLICY.md)
- Engineering protocol: [ENGINEERING_PROTOCOL.md](ENGINEERING_PROTOCOL.md)
- Data ownership: [SHOPIFY_DATA_OWNERSHIP.md](SHOPIFY_DATA_OWNERSHIP.md)
- Architecture map: [NXENTRA_SYSTEM_MAP.md](NXENTRA_SYSTEM_MAP.md)
