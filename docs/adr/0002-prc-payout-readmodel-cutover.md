# ADR-0002 — PR-C: Stripe payout read-model cutover plan (PR-C0 grounding)

- **Status:** Grounding complete (PR-C0), cutover staged (PR-C1–C4 not yet started)
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
