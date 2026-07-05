# ADR-0002 — PR-C: Stripe payout read-model cutover plan (PR-C0 grounding)

- **Status:** C1–C3 shipped (canonical reads live behind `STRIPE_CANONICAL_PAYOUT_READS`); PR-D in flight (see the PR-D section below); C4a/C4b pending
- **Parent:** [ADR-0002 Canonical payment/settlement layer](0002-canonical-payments-stripe-adapter.md)
- **Builds on:** PR-A (`ProviderPayoutLine` + `PaymentsProjection`, dual-write) and PR-B (provider-agnostic lines + the Paymob/Bosta architecture gate), both shipped + deployed 2026-06-27.

## Why this doc exists

PR-C makes the canonical `ProviderPayoutLine` model the **source of truth** for Stripe payout reads and removes the legacy `StripePayout` / `StripePayoutTransaction` direct writes. That changes **live financial read paths**, so it is treated as a migration, not a feature. The safe pattern is **expand → prove parity → switch → remove** — never "remove + repoint + hope". PR-A/B were the *expand* phase (both paths write in parallel today, at zero read-risk). This doc is the grounding (PR-C0): the full consumer inventory, the canonical coverage gap, and the staged plan that the gap dictates.

**PR-C0 changes no behavior.** It ships only this doc + characterization tests (`backend/tests/test_s2c_payout_read_contracts.py`) that pin the current read contracts so any future divergence trips a test before it reaches a money screen.

## Consumer inventory (who reads the legacy models)

The blast radius is wider than the Stripe settings UI — the **bank-reconciliation match engine** reads the payout header.

| Consumer | File | Reads | Mutates |
|---|---|---|---|
| `StripePayoutsListView` | `stripe_connector/views.py` | header + txn counts | — |
| `StripeReconciliationSummaryView` | `stripe_connector/views.py` | header + txn aggregates | — |
| `StripePayoutReconciliationView` | `stripe_connector/views.py` | header + `reconcile_payout` | via reconcile (verified) |
| `StripePayoutVerifyView` | `stripe_connector/views.py` | txns | **`verified`, `local_charge`** |
| `reconcile_payout` / `reconciliation_summary` | `stripe_connector/reconciliation.py` | txns + `StripeCharge`/`Refund` | **`verified`, `local_charge`** |
| `_get_stripe_payouts` | `bank_connector/matching.py` | **header** (gross/fees/net/currency/date/`stripe_status`/`journal_entry_id`) | — |
| `_explain_stripe_payout` | `bank_connector/matching.py` | txns | — |
| `_create_payout_je` / `manual_match` / `auto_match_transactions` | `bank_connector/matching.py` | header | **`journal_entry_id`** |
| `detect_payout_discrepancies` | `bank_connector/exceptions.py` | header + reconcile | via reconcile |
| `StripePayoutAdmin` / `…TransactionAdmin` | `stripe_connector/admin.py` | header/txns | (admin) |
| Frontend | `frontend/services/stripe.service.ts`, `pages/stripe/{payouts,reconciliation,index}.tsx` | the view JSON above | — |

## Canonical coverage gap

`ProviderPayoutLine` is **lines only**. There is no canonical payout **header**, and the settlement event omits some header fields.

| Legacy field (consumer need) | Canonical source today | Status |
|---|---|---|
| line `amount/fee/net/source_id/kind` | `ProviderPayoutLine` | ✅ covered |
| line `uncollected`/`refund` | `ProviderPayoutLine.uncollected_amount` (PR-B) | ✅ covered |
| header `gross/fees/net/currency/payout_date` | `PAYMENT_SETTLEMENT_RECEIVED` top-level (+ line aggregate) | ✅ in event, ⚠️ no header row |
| `stripe_payout_id` | `event.payout_batch_id` | ✅ |
| **`stripe_status`** ("paid") | **not in event** — `_emit_settlement` drops `breakdown["status"]` | ❌ enrich emit (PR-C1) |
| **`account` → `account_name`** | not in event | ❌ resolve via connection or enrich emit (PR-C1) |
| **`journal_entry_id`** | JE built by `PaymentSettlementProjection`, no back-ref | ❌ resolve by `source_document` / write-back (PR-C1) |
| `stripe_balance_txn_id` | line_items has `order_id` only (bt id lost when `source` present) | ❌ add to line_items or accept order_id |
| **`verified` / `local_charge`** (match state) | none — mutated directly, no event | ❌ **PR-D** (variance → `ReconciliationException`) |
| `raw_payload` / `raw_data` | `ProviderRawObject` (provenance cache) | ✅ recoverable |
| `status` (local RECEIVED/PROCESSED) | none (app lifecycle flag) | ⚠️ projection defaults RECEIVED |

## Decisions the gap forces

1. **Build a projection-built `ProviderPayout` header read-model (PR-C1).** Event+line-aggregate cannot serve `account_name`, `stripe_status`, or `journal_entry_id` without joins/enrichment, and the bank-match engine needs a header row. This revisits the parent ADR's "ProviderPayout = the event (no header)" line — superseded here: a thin header read-model is warranted. It is **projection-owned** (same sole-writer + RLS pattern as `ProviderPayoutLine`).
2. **Enrich the emit before backfill (PR-C1).** Add `stripe_status` (and an account/connection reference) to `PAYMENT_SETTLEMENT_RECEIVED` so the header is **event-reproducible**. Until this lands, event-replay backfill cannot reach parity.
3. **Backfill by event replay, never by copying legacy rows.** `PaymentsProjection.rebuild()` over historical events. Copying legacy→canonical would mint rows the projection can't reproduce, silently re-breaking replay-safety. Parity = *canonical-from-events == legacy-from-direct-writes*; a divergence means the **event** is missing data → fix the emit, not the copy.
4. **`verified`/`local_charge` is PR-D, and it gates removal.** The line cache (`StripePayoutTransaction`) carries match state mutated with no event. It **cannot be removed until PR-D** routes verification through `ReconciliationException`/events. So "remove direct writes" splits: the header write can go after the header projection + parity; the line write only after PR-D. Verified=True rows must be snapshotted/migrated, never dropped.
5. **`reconcile_payout` compares lines to the header.** If the header stops being written before its canonical replacement is proven, reconcile breaks immediately. Keep the legacy header until the canonical header is parity-proven and reads are switched.

## Staged plan

| Stage | Scope | Risk |
|---|---|---|
| ✅ expand | PR-A/B dual-write (shipped) | none |
| ✅ **PR-C0** | this doc + characterization tests | none |
| PR-C1 | `ProviderPayout` header read-model + enrich emit (`stripe_status`, account ref); projection materializes header; reads UNCHANGED | low (additive) |
| PR-C2 | event-replay backfill (`rebuild`) + **real-droplet parity command** (per-payout diff: totals, settlement id, status, JE id, line counts) | none (read-only report) |
| PR-C3 | switch reads (views + bank-match `_get_stripe_payouts`/`_explain_stripe_payout`) to canonical, behind a flag/narrow route; legacy writes stay | low (instant rollback) |
| PR-C4a | remove legacy **header** direct write (after a clean deploy cycle on canonical reads) | contract |
| PR-D | `PROVIDER_PAYOUT_RECONCILED` + verification via `ReconciliationException` (unblocks line-cache removal) | — |
| PR-C4b | remove legacy **line** direct write (only after PR-D migrates `verified`) | contract |

## Parity gates (must pass before any read switch)

- Synthetic: the characterization tests in `test_s2c_payout_read_contracts.py` keep passing when reads are repointed.
- Real data: a management command run on the droplet reporting **zero** per-payout diffs between legacy and canonical for: header `gross/fees/net/currency/payout_date/stripe_status`, settlement id, `journal_entry_id`, line count, and per-line `gross/fee/net/uncollected`.

## PR-D design (grounded 2026-07-05, 8-mapper workflow + 3-lens adversarial verify)

**Event: `PROVIDER_PAYOUT_RECONCILED` (`provider_payout.reconciled`)** — a
provider-neutral, FULL-STATE snapshot of one payout's line match verdicts +
header outcome; replay is last-write-wins in `company_sequence` order. Dataclass
in `platform_connectors/event_types.py`; aggregate `ProviderPayout` /
`{provider}:{batch_id}`; idempotency key is per-emit uuid4 (deterministic keys
would lock the first outcome; content-hash keys would corrupt A→B→A replay).
Verdicts correlate to canonical lines by **`line_index`** (the settlement
event's frozen `line_items[]` position — replay-stable because the settlement
event is idempotency-locked at first emit). Legacy txns map to event lines by
`(source_id or stripe_balance_txn_id) == line_items[i].order_id` (exact:
normalize builds `order_id = bt.source or bt.id`; both sides exclude the
`type=="payout"` txn). Verdict fields avoid ALL validator-reserved names —
notably `kind` (enum-bound to `JournalEntry.Kind`; the draft design had this
and every emit would have 500'd — caught by the adversarial pass, pinned by
`test_s2g_payout_reconciled`).

**Variances are event-frozen**: header totals vs line sums of the SAME
settlement event — never the flag-selected header `reconcile_payout` used
(flag-dependent event content is poison) and never the mutable legacy header
(re-sync drift). Per-line `verified` mirrors the **persisted DB value** at
snapshot time, warts included (reconcile persists charge/refund matches only;
the verify endpoint also persists adjustment/payout) — so canonical verified
counts are byte-comparable to `_legacy_verified_counts` and the read switch is
parity-provable. Header `matched_count` mirrors reconcile's in-memory
semantics (auto-type lines count as matched). Unifying the two vocabularies is
post-C4b work.

**Emitters (dual-write, D1):** `reconcile_payout` (post-loop, source
`auto_reconcile`) and `StripePayoutVerifyView` (source `manual_verify`,
actor-stamped), both through `stripe_connector/reconciled_emit.py`'s
`maybe_emit_payout_reconciled` — emit-on-change (steady-state reconciles emit
nothing) and failure-isolated (an emit exception must never break the
read/verify path). Payouts with **no settlement event** (pre-PR-A history,
`seed_stripe_demo` rows) skip the emit: no canonical lines exist to stamp;
their verified state stays legacy-only and is **excluded from the D2 parity
gate** (reported under a named skip counter, never silently).
`manage.py stripe_reconciled_backfill --apply` seeds snapshots capturing
pre-PR-D verified state (one pass suffices — snapshots read current DB state).

**Exception producer (D1):** variance outcomes feed the existing
`ReconciliationException` queue by **direct write** via `_create_exception` —
the table is an operator table with four existing detector writers, not a
projection read-model (never wiped by rebuild), and its dormant event twin
(`RECONCILIATION_EXCEPTION_RAISED`, consumed as a no-op) is unfinished A86.8
work, out of PR-D scope. Dedup key = the scan detector's exact key
(`PAYOUT_DISCREPANCY`, `f"{platform}_payout"`, legacy payout pk) so
event-driven and scan production fold onto one open row; details are
byte-identical between the two producers; `outcome == "verified"`
auto-resolves open variance rows — but never ESCALATED ones (operator-parked,
matching `auto_resolve_matched`'s convention). The shared `_create_exception`
dedup-refresh keeps title/description/amount/details current and upgrades
severity **monotonically** (a shrinking-but-open anomaly keeps its peak
severity so it can't silently drop off a severity-sorted triage view).
Re-keying to the canonical pk is C4b (with `details.payout_batch_id` as the
bridge). Two deliberate behavior boundaries (from the adversarial review of
the diff): (1) viewing a payout detail page (which reconciles, which emits on
change) can now open a variance exception — previously only the on-demand
scan did; (2) the **backfill source is excluded from the exception feed** —
seeding months of event history must not flood the live queue with, or
re-open triaged, stale discrepancies; the bounded 30-day scan remains the
producer for history.

**D2 (projection + read switch):** migration adds match-state columns to
`ProviderPayoutLine` (`verified/match_kind/matched_ref/matched_ref_type/
provider_line_ref/verified_at`) + header outcome columns to `ProviderPayout`;
`PaymentsProjection` consumes the second event type (settlement handler's
`update_or_create` defaults must NOT include the new columns — a settlement
re-apply would zero verdicts); verified-count reads flip behind default-OFF
`STRIPE_CANONICAL_VERIFIED_READS`; `payments_canonical_backfill` gains
`verified_parity_ok/mismatch/skipped_no_event` counters — the flip gate is
`verified_parity_mismatch == 0` **among event-backed payouts**.

**D2 deploy runbook (ordering is load-bearing — C3 canonical reads are LIVE):**
1. `git pull` → `migrate platform_connectors` (0009) **BEFORE** restarting
   api/celery — the new model selects the new columns on every canonical
   query, so new code on an un-migrated DB 500s the payout pages.
2. Restart api/celery/celery-beat.
3. `payments_canonical_backfill --apply` — MANDATORY, not optional:
   reconciled events emitted before D2 registered the type sit **behind** the
   projection bookmark (`company_sequence__gt`; lag reads 0 for them) and are
   otherwise silently skipped. NB `--apply`'s rebuild DELETEs then replays the
   canonical rows per company — live C3 reads render empty/partial for a few
   seconds mid-rebuild; run in a quiet window or per `--company-id`.
4. Read the gate off the post-`--apply` report (report-only output annotates
   that un-replayed events read as false mismatches).
5. `verified_parity_mismatch == 0` → set `STRIPE_CANONICAL_VERIFIED_READS=True`
   in `.env` + restart. Rollback = flip back + restart (reads only).

**C4b checklist additions (from the blast-radius verify pass):**
`stripe_connector/admin.py` (list_display/list_filter on `verified`) and
`backups/model_registry.py` (StripePayoutTransaction registration) break at
column/table drop — retire them in the C4b PR alongside `reconcile_payout`'s
re-point to canonical lines, the verify endpoint's emit-only rewrite, and the
exception-reference re-key.
