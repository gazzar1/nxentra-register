# ADR-0002 — Canonical payment/settlement layer, Stripe as the first reference adapter

- **Status:** Proposed (2026-06-22)
- **Builds on:** [ADR-0001 Reconciliation Link](0001-reconciliation-link.md); the shipped 3-stage reconciliation page; `PAYMENT_SETTLEMENT_RECEIVED` provider-agnostic settlement spine.
- **Supersedes for payment providers:** the "Phase B canonical platform models" sketch in [NEXT_TASKS.md](../../NEXT_TASKS.md) (B3/B4) — scoped down to payments + read-only, Shopify untouched.

## Context

Nxentra needs Stripe for one thing: **financial reconciliation and accounting truth.** So the integration is deep where Nxentra needs it (transactions → payout → bank → ledger) and shallow everywhere else (no charge/refund/subscription/Checkout creation). Stripe is the *first reference adapter*, not the architecture.

A grounding audit (15-agent workflow, 2026-06-22, all areas verified) found that **~80% of the proposed "canonical payment module" already exists** in Nxentra's event-first architecture. The danger is building a parallel directly-written `payments_core` that forks the reconciliation truth just hardened in ADR-0001. This ADR records the grounded design.

## Decision

Build the **Stripe adapter** + a **small canonical provider-payment layer inside the existing event/projection architecture** (not a parallel core) + an **explicit raw ingestion cache**. Reuse the engine for everything else. Stripe normalizes into the existing settlement event, so the reconciliation engine lights up without a rewrite, and **Shopify (load-bearing, App-Store-published) is never touched.**

> Wording matters (per review): **no new *parallel* core — but yes, a small canonical provider-payment layer *inside* the existing event/projection architecture.** "Stage 1/2/3 light up with zero new core" was true only of the reconciliation engine + Stage-2/3 JE flow; the provider-payment layer (transaction/payout-line identity, capabilities, fee derivation, sign/currency normalization, replay/idempotency, source→canonical validation) **is** real, if small, new core.

### Reuse map (brief object → Nxentra)

| Brief object | Nxentra target | Action |
|---|---|---|
| `ProviderPayout` | `PAYMENT_SETTLEMENT_RECEIVED` event → `PaymentSettlementProjection` | reuse as-is (emit the event; projection drains clearing + posts fees) |
| `BankMatch` | `reconciliation.ReconciliationLink` (durable, projection-owned, U5a legs) | reuse as-is (`confirmation_kind='platform_payout_reconcile'` already exists) |
| `ReconciliationCase` | `bank_connector.ReconciliationException` + 5 detectors | reuse-extend |
| `AccountMapping` | `ModuleAccountMapping` + `SettlementProvider`→PostingProfile | reuse as-is (after the module-key fix below) |
| `ExternalRawObject` | **NEW raw ingestion cache** (see "Raw data" — the event store is *not* enough) | build-new (thin, source-only) |
| `PaymentProviderConnection` | `stripe_connector.StripeAccount` | reuse-extend (auth-agnostic credential) |
| `PaymentProviderAdapter` | `platform_connectors.BasePlatformConnector` + `ConnectorRegistry` | reuse-extend (add the PULL half) |
| `ProviderPayoutLine` | `PaymentSettlementReceivedData.line_items[]` + NEW projection-built read-model | reuse-extend |
| `ProviderTransaction` | (nothing canonical; `StripePayoutTransaction` is dead/direct-written) | build-new (projection-built) |

### Genuinely new (the small canonical layer)

1. **Stripe restricted-key auth + a pull/backfill client** over Balance Transactions + Payouts (+ Payout Reconciliation report). The connector is webhook-push-only today (no SDK, no outbound reads); `payout.paid` alone lacks the fee/net split and webhooks aren't guaranteed — pull+backfill is the **primary** truth source.
2. **Canonical `ProviderTransaction` + `ProviderPayoutLine` read-models**, materialized by **one new sole-writer projection** mirroring `ReconciliationLink.save`'s `projection_writes_allowed()` guard + uuid5 identity. This *is* the already-identified "Stage-2 per-batch table" recon improvement (convergence).
3. **Three new event types**: `PROVIDER_PAYOUT_RECONCILED` (the brief's middle stage — "did the provider correctly aggregate charges/refunds/fees into this payout net?"), and **dispute-resolution / reserve / adjustment**.
4. **Raw ingestion cache** (see below).
5. Plumbing: an **adapter registry** (replacing the `if code=='paymob'/'bosta'` dispatch in `settlement_imports.py`), a **`_setup_platform_accounts` seed**, a **`capabilities`** property.

### Authorization — restricted key now, OAuth/Stripe App later, swap designed-in

The use case is reading a **merchant's own** Stripe account, not a platform onboarding sub-merchants — so the "Connect Onboarding / OAuth-not-recommended" caveat does **not** apply (verified against current Stripe docs).

- **Phase 1 (prove the engine, fastest/simplest):** restricted read-only API key (`Balance/Charges/Payouts/Disputes = Read`), entered by the merchant.
- **Public destination:** OAuth via a **Stripe App** when many merchants self-connect.
- **Design rule:** the connection is **auth-method-agnostic** — `StripeAccount.auth_type` (`restricted_key | oauth`) + a `credential_ref`; the pull client asks the connection for "a Stripe client" and never cares how it was authorized. The OAuth upgrade then touches only the connect/credential layer, not the engine. Restricted key buys zero less engine-proof and ships in days.
- **Encryption-at-rest is A47, and it is a HARD gate before Phase 1.** The codebase has *no* field encryption today (Shopify `access_token` + Stripe `webhook_secret` are plaintext; A122 rotated tokens but did not encrypt). The S0 connection ships the auth-agnostic *shape* with `credential_ref` plaintext **but empty** — nothing populates it until Phase 1's connect UI. **A47 (encrypt all provider credentials — Stripe `credential_ref` + Shopify token + webhook secrets — one key rollout) MUST land before Phase 1 ever writes a real key.** Encrypting only the new field while Shopify tokens stay plaintext would not reduce the DB-breach threat, so it is done once, consistently, deliberately.

### Raw data — an explicit raw cache IS warranted (reverses the first draft)

The brutal test ("for any payout, can you answer: exact objects, API version, fetched time, source channel, payload hash, and *replay normalization after a bug*?") was run against the event store. Result:

- ✅ payload hash (`BusinessEvent.payload_hash` + content-addressed `EventPayload`), normalized event, `external_source`/`external_id`, `occurred_at`.
- ❌ **API version, fetched-at, source-channel (api/webhook/report/csv), and the raw Stripe *input* to the normalizer.** The event stores the *output* of normalization. The existing `raw_payload` columns on `StripeCharge/Refund/Payout` are webhook-only, provenance-less, **and direct-write read-models that won't survive a rebuild.**

So the test **fails on "replay normalization after a bug."** Decision: build a **thin, explicitly-raw ingestion cache** — `(provider, object_type, external_id, api_version, fetched_at, source, payload_hash, payload_json)` — that the normalizer reads *from* (replay = re-run normalizer over raw), **replacing** the scattered `raw_payload` columns. It is raw/source-only, **not** a truth model. Principle: *raw data can be stored separately; truth cannot be duplicated separately.*

### Account mapping + JE patterns — and the module-key trap (Phase 0, financial-trust)

Reuse `ModuleAccountMapping` + `SettlementProvider`. JE patterns mostly already exist in `PlatformAccountingProjection` (sale, refund, payout-drain, clearance, dispute-created). New for Stripe: dispute **resolution** (won/lost/funds-withdrawn), **reserve**, **adjustment** (each a new role + event), and **realized-FX computation** (FX accounts exist in core; no platform JE builder computes FX today).

**The module-key split is a financial-trust bug, not cleanup.** For a single Stripe provider, the same logical mapping resolves **three different keys**:

| Site | Key for Stripe | File |
|---|---|---|
| Order/refund/dispute JEs (`PlatformAccountingProjection`) | `platform_stripe` | [platform_connectors/projections.py:94](../../backend/platform_connectors/projections.py#L94) |
| Settlement JEs (`PaymentSettlementProjection`) | `stripe_connector` (fallback) | [payment_settlement_projection.py:205](../../backend/accounting/payment_settlement_projection.py#L205) |
| Settlement EBD lookup (bank-clearance leg) | **hardcoded** `shopify_connector` | [bank_reconciliation.py:633](../../backend/accounting/bank_reconciliation.py#L633) |

Seed one, the others skip with only a warning log → **the books are silently incomplete.** Shopify is unaffected (its own `shopify_accounting` projection + `shopify_connector` key are separate); Paymob/Bosta ride `external_system='shopify'` → `shopify_connector` and must keep doing so. Decision: introduce **one canonical `module_key_for_provider(external_system/slug)`** helper, route all three sites through it (Stripe → `platform_stripe`; shopify/paymob/bosta → `shopify_connector`), and resolve the **shared-vs-per-provider EBD** question explicitly (today's hardcode assumes a shared/Shopify EBD account). Gate behind a **failing characterization test** before changing routing.

## The phased roadmap

> Do **Stage 2 before Stage 1** for Stripe — the payout breakdown is Stripe's value and Nxentra's weakest stage.

- **Phase 0 — Financial-trust hardening (not "cleanup", and comes first).** Unify the module key (3 sites + helper, test-gated); adapter registry; `capabilities` + `ParsedProviderTransaction`/`ParsedPayoutLine` DTOs; auth-agnostic `StripeAccount` credential; raw ingestion cache model; re-key webhook resolution off the Connect-account-id. *No real merchant connects Stripe until this lands.*
- **Phase 1 — Stripe read-only adapter.** `stripe` SDK + pull client (Payouts + Balance Transactions + Payout Reconciliation report) with a sync cursor; `_setup_platform_accounts` seed under the unified key + `SettlementProvider(stripe, GATEWAY)`; emit `PAYMENT_SETTLEMENT_RECEIVED` with **derived** fees (fix fees=0); self-serve connect UI; fold/demote direct writes.
- **Phase 2 — Stage-2 payout-line breakdown FIRST (the convergence).** Populate `line_items[]` from Balance Transactions; emit `PROVIDER_PAYOUT_RECONCILED`; new sole-writer `PaymentsProjection` materializing `ProviderTransaction`/`ProviderPayoutLine`; re-point `reconcile_payout` variance at `ReconciliationException` via the event path (remove direct `verified`-flag mutation); two-phase emit for `payout.reconciliation_completed` lag.
- **★ Architecture gate (cheap, early) — run existing Paymob/Bosta settlement events through the NEW canonical projection.** They already emit `PAYMENT_SETTLEMENT_RECEIVED` with `line_items[]`. If the canonical layer only works on Stripe's clean balance transactions and chokes on Bosta's uncollected/returned COD rows, the Stripe-shaped leak surfaces *immediately, while the abstraction is still soft.* A continuous test, not a final phase.
- **Phase 3 — Shopify↔Stripe order match + dispute/reserve/adjustment events.** Generalize A26/A39 dedup beyond `invoice__source='shopify'` *additively*; dispute-resolution/reserve/adjustment events + JE branches (events are sole owner — deprecate the command-layer `DISPUTE_WON` to avoid double-post); new roles + backfill; derive chargeback fee (fix hardcoded $15).
- **Phase 4 — Bank deposit match end-to-end.** Verify Stripe EBD → `ReconciliationLink` flows unchanged; negative payouts + per-currency clearing + realized-FX (reuse core `REALIZED_FX_GAIN/LOSS`); orphan-deposit exceptions.
- **Phase 5 — Second *pull* provider (Paymob API or PayPal).** Self-registers via the Phase-0 registry; only adapter + seed + capability declaration needed.

## Key decisions

1. **Auth:** restricted read key (encrypted) for Phase 1; OAuth/Stripe App for public; connection designed auth-agnostic so the swap is drop-in.
2. **No parallel `payments_core`:** every canonical object except transaction/line granularity already has an event-first home; build only the new read-model projection + adapter + 3 events + raw cache.
3. **Unify the module key before seeding** (the 3-site silent-fail) — test-gated, Shopify untouched.
4. **Events are the sole owner** of dispute/payout-verification state (deprecate the command-layer dispute path) — else double-post + non-replayable state.
5. **Raw cache is explicitly raw, not truth** — added because the event store keeps normalized output, not replayable raw input + provenance.

## Sequencing vs "defer Phase B"

Not in tension: the risky long pole (migrating *Shopify* onto canonical models) stays out of scope. Shopify keeps emitting its events through unchanged code; Stripe is a **new** adapter emitting the **same** provider-agnostic events into the **same** engine — convergence at the *event layer*, not by rewriting Shopify. The new read-models are populated **from Stripe (and the Paymob/Bosta gate) only**; backfilling Shopify into them *is* the deferred long pole. This plan validates the abstraction on a non-load-bearing provider first.

## Risks (financial-trust, not technical)

If fees are hardcoded to zero, Stage 2 lies. If disputes double-post, accounting lies. If direct writes bypass projections, replay lies. If sign conventions are wrong, gross/net reconciliation lies. If the module key splits, the books are silently incomplete. **A reconciliation product that lies about money dies — so Phase 0 is a correctness prerequisite, not optional.** Other risks: `payout.reconciliation_completed` lag (two-phase emit); Connect-account webhook resolution returns None for a restricted-key setup (re-key to the connection; pull is primary); cents/negative/multi-currency mapping into the `net+fees+uncollected==gross` balance guard (it *silently refuses* the JE on a sign error).
