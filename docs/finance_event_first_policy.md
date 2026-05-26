# Finance Event-First Policy

Established: 2026-05-25
Status: Canonical. Code that violates these rules is broken, regardless of whether tests pass.

This document defines the rules every piece of accounting-touching code in Nxentra must follow. It is the contract between developers and the system's claim to be a "Financial Truth Engine." If you find existing code that breaks these rules, that's a bug — fix it or file a ticket; don't propagate the pattern.

Related documents:
- [architecture-map.md](architecture-map.md) — the command → event → projection pipeline
- [projection-idempotency.md](projection-idempotency.md) — why `_apply_line()` must never skip
- [core-assurance-baseline.md](core-assurance-baseline.md) — the 38 invariant tests that guard these rules

---

## 1. The core rule

**The `BusinessEvent` log is the source of truth. Everything else is a derived view.**

Every accounting movement starts as an emitted event. `SalesInvoice`, `JournalEntry`, `BankStatement`, `CustomerBalance`, `InventoryBalance` — all of these are **read models** projected from events. They are replayable, deletable, and regenerable. The event log is none of those things.

Consequences:
- If you need to change accounting state, **emit an event** — never UPDATE a read model directly from a view, command, or shell session
- If you need a new aggregate (e.g., "open AR by salesperson"), **add a projection** — never query across commands
- If a read model disagrees with the event log, **the read model is wrong** — replay it, don't patch it

### 1.1 Replay convergence — a load-bearing property

The above only holds if dropping every read-model row and re-running every projection from the event log reproduces the exact same state. This is **replay convergence**, and it is the property that distinguishes "we happen to write events" from "the event log is canonical."

Verticals are responsible for proving this with at least one test that:
1. Runs a representative lifecycle through commands.
2. Captures final read-model state.
3. Wipes the read-model rows + `ProjectionAppliedEvent` + `EventBookmark` for the projection.
4. Re-runs `process_pending()`.
5. Asserts the rebuilt state is identical.

Examples in tree:
- `tests/test_a86_7a_cutover.py::test_replay_convergence_full_lifecycle` — A86.7b makes BankStatementLine match state (`match_status`, `matched_journal_line`, `match_confidence`) canonically derived from the `ReconciliationMatch*` event stream; replay is therefore a guaranteed property of bank reconciliation.

If a new field on a read model cannot be reconstructed from events, it isn't a read model — it's hidden canonical state and is a P0 violation of this policy.

---

## 2. Event emission rules

Every event must:

1. **Carry an `idempotency_key`** — string, unique per (company, event_type, business_key). The event store enforces this with a unique constraint. Duplicate emissions are silently deduped.
   - Format convention: `{module}.{operation}:{business_key}` (e.g., `shopify.order.paid:1001`, `sales.invoice.posted:INV-000042`)
   - Without an idempotency key, retries create duplicates and the event log lies.

2. **Carry a `company_sequence`** — gap-free, monotonically increasing per company. Assigned by `emit_event()`. Never set manually.

3. **Pass payload-schema validation** — every event type has a `@dataclass` payload class extending `BaseEventData` in `events/types.py` (or in a vertical's `event_types.py` auto-discovered via `AppConfig.event_types_module`). Validation is mandatory at emission time via `validate_event_payload()`. Schema drift is the most common cause of silent projection failures. (Doc note: pre-A86.2 this section read "Pydantic schema" — the implementation has always used dataclasses; corrected 2026-05-26.)

4. **Use `system_actor_for_company()` for connector-driven emissions** — never reuse a user session for connector / scheduled / projection work. `ActorContext` is set explicitly at emission time so audit trails are correct.

5. **Be inside `command_writes_allowed()` context** — events emitted from outside a command context (e.g., direct shell, view middleware) are an architectural violation. Use the context or refuse to emit.

What you do NOT do:
- ❌ Emit `journal_entry.posted` directly from a view to "fix" something. Go through `post_journal_entry`.
- ❌ Emit a new event type without a payload schema in `events/types.py`.
- ❌ Mutate a `BusinessEvent` row after it's written. Events are immutable.

---

## 3. Read models and projection rules

Every projection must:

1. **Inherit `BaseProjection`** and register via `ProjectionRegistry`. Use the existing pattern in `projections/base.py`.

2. **Declare `consumes` explicitly** — list every `EventTypes.*` the projection handles. The framework uses this for routing and lag tracking.

3. **Use `process_pending()` for idempotency, not in-handler guards** — per [projection-idempotency.md](projection-idempotency.md), `ProjectionAppliedEvent` is the single idempotency mechanism. Never write `if balance.last_event_id == event.id: return` in handler code; that pattern silently drops multi-line events.

4. **Wrap writes in `projection_writes_allowed()` context** — read models like `JournalEntry` inherit `ProjectionWriteGuard` and raise `RuntimeError` outside the context. This is intentional — bypassing means you're writing to a derived view as if it were source.

5. **Defer rather than fail on missing dependency state** — if a refund handler arrives before the order_paid handler has produced the invoice, **defer the event** (the A23/A40 pattern). Don't fail; don't silently drop; defer with bounded retry budget.

6. **Never silently no-op on error.** See section 8.

What you do NOT do:
- ❌ Update `JournalEntry` from a view because "it's a simple field."
- ❌ Use `last_event_id` as a skip condition.
- ❌ Catch exceptions in the handler and `return` without raising or emitting a failure event.
- ❌ Filter events the projection should have consumed (the bookmark must always advance through the full event stream).

---

## 4. Idempotency contract

Three layers of idempotency, each at a different level:

| Layer | Mechanism | Enforced by |
|---|---|---|
| **Event emission** | `idempotency_key` unique constraint | `BusinessEvent.idempotency_key` UNIQUE INDEX |
| **Event consumption** | `ProjectionAppliedEvent (company + projection + event)` unique | `BaseProjection.process_pending()` |
| **Domain dedup** | `(company, source, source_document_id)` for platform-originated records | `SalesInvoice` UNIQUE INDEX + idempotency SELECT in `create_and_post_invoice_for_platform` |

Every platform connector must:
- Compute a stable `source_document_id` from the external system's primary key (Shopify order ID, Stripe charge ID, etc.)
- SELECT existing records by `(company, source, source_document_id)` before INSERT
- Return the existing record gracefully if found — don't INSERT and rely on the DB to error

The 2026-05-25 A78 incident exposed a gap: the SELECT in `create_and_post_invoice_for_platform` missed an existing row, then the INSERT hit the unique constraint and raised `IntegrityError`. **A82** in the post-listing queue addresses this — the SELECT and the INSERT must agree on the same uniqueness boundary.

---

## 5. RLS and multi-tenant rules

1. **RLS is on by default.** Every query is scoped to the current company by Postgres row-level security policies.

2. **`rls_bypass()` is opt-in and explicit.** Maintenance code, projections that span tenants for aggregate metrics, and the seed commands must declare `with rls_bypass():` — and explain why in a code comment.

3. **Tests must verify RLS isolation.** The core-assurance-baseline today bypasses RLS in tests; that's a documented gap. New connector code must include a test that proves company A's events never project into company B's read models.

4. **Multi-tenant data leakage is a P0.** A cross-tenant projection bug is the worst possible class of error for an accounting system. Treat every unscoped query as suspect.

---

## 6. Posting profile and module routing rules

The chart of accounts is **not** the routing layer. `PostingProfile` is.

1. **No hardcoded account selection in command or projection code.** Route through `PostingProfile`:
   - `Usage.MANUAL` — user picks during data entry, validated against profile_type
   - `Usage.GATEWAY` — owned by platform connectors, carries required dimension rules

2. **`auto_created=True` is the platform-integration bypass.** Validators that block GATEWAY profiles for manual users (the A78 family) MUST gate on `not auto_created and ...` — never unconditionally. Mirror the pattern at `sales/commands.py:1716`.

3. **Per-channel routing via `SettlementProvider`.** Each platform provider (Paymob, Bosta, Paymob Accept) has its own `SettlementProvider` with an attached `PostingProfile` and `AnalysisDimensionValue`. The reconciliation engine pivots on `(clearing_account, dimension_value)`.

4. **Unknown providers lazy-create with `needs_review=True`.** Don't fail the projection; flag for operator review.

---

## 7. Multi-currency rules

1. **Functional currency is per-company.** Determined at company creation, immutable afterward.

2. **Transactions carry both functional and foreign currency.** Every multi-currency entry records:
   - Transaction amount in foreign currency
   - Functional amount at the captured FX rate
   - The FX rate itself

3. **FX rates resolved at posting time, not query time.** Once posted, the JE is immutable. Re-querying historical data must not re-translate.

4. **`MissingExchangeRate` raises and notifies admins.** See `_handle_order_paid` in `shopify_connector/projections.py` — this is the one exception where the handler intentionally re-raises so an operator gets paged.

---

## 8. Failure modes — loud, not silent

This is the rule the 2026-05-25 A78 incident violated. **Never** swallow errors in projection handlers with `logger.warning(...); return`.

When a handler cannot produce its expected output:

| Failure type | Required response |
|---|---|
| **Missing config (mapping, posting profile, customer)** | Raise `InvalidProjectionStateError` so `projection_health` shows red and the operator gets paged. |
| **Downstream command returned `success=False`** | Re-raise as `ProjectionCommandFailedError` carrying the original error message. |
| **Race condition (dependency event not yet projected)** | Defer the event via the A23/A40 pattern, bounded retry budget. |
| **Schema mismatch (event payload doesn't match handler)** | Raise. This is a deploy ordering bug, must surface immediately. |
| **Permanent business error (e.g., refund > invoice total)** | Emit a `{module}.{operation}.failed` event that surfaces in `/finance/exceptions`. |

What is NEVER acceptable:
- ❌ `logger.warning(...); return` — the event is marked consumed, the read model is empty, the merchant sees nothing
- ❌ `try: ... except Exception: logger.error(...); return` — same problem
- ❌ "We'll add monitoring later" — silent failures in an accounting system destroy customer trust one merchant at a time

**Test:** for every projection handler, write a test that triggers each early-return path and asserts either an exception or a failure event was emitted.

---

## 9. Connector contracts

When adding a new platform connector (e.g., WooCommerce, Stripe, Amazon, BNPL providers):

1. **External event types use the `{platform}.{noun}_{verb}` convention.** Examples: `shopify.order_paid`, `stripe.charge_succeeded`. Internal accounting events stay generic (`journal_entry.posted`).

2. **Connector has its own projection.** Don't extend `AccountBalanceProjection` to handle Shopify-specific routing. Each connector gets a `{platform}_accounting` projection that translates external events into internal commands.

3. **The connector projection calls `create_and_post_invoice_for_platform` (or equivalent)** — not `create_sales_invoice` directly. The `_for_platform` wrapper exists to (a) supply `auto_created=True` so guards bypass correctly, (b) provide idempotency by source key, (c) skip COGS deferred to fulfillment.

4. **Every connector ships with an end-to-end test.** Seed an external event via the connector's seed command, run the projection, assert the SalesInvoice + JournalEntry exist with correct amounts and dimension tags. This is the test that would have caught A78 on commit.

5. **Webhook authentication is non-negotiable.** Every inbound webhook verifies HMAC/signature before doing anything else. See `shopify_connector/views.py:153` for the pattern.

6. **GDPR / data-erasure webhooks are mandatory for App Store distribution.** See `shopify_connector/views.py:170-191` for the three Shopify compliance topics.

---

## 10. What this policy prevents

Concrete incidents this policy, if followed from day one, would have prevented:

| Incident | Date | Rule that would have caught it |
|---|---|---|
| A23: Refund handler raced order_paid projection | 2026-05-02 | §3.5 (defer-don't-fail) — was applied retroactively, now canonical |
| A52: Shopify REST API returning 403 silently | 2026-05-15 | §8 (loud not silent) — A52 logs ERROR but doesn't surface in operator UI |
| A78: Shopify projection couldn't create invoices | 2026-05-25 | §6.2 (`auto_created` bypass) — pattern existed at line 1716, missed at line 723 |
| A78 hid for weeks | 2026-04 to 2026-05 | §9.4 (end-to-end test per connector) — no test asserted the projection produces records |

If you're about to write code that breaks one of these rules and you think you have a good reason — file a ticket explaining the reason and get it reviewed before committing. The rules exist because the alternative is a slow-bleed trust failure with paying merchants. Accounting software has no second chances on that front.
