# Canonical Money Spine

Evidence-based map of every important financial fact: where it comes from, what
its canonical form is, who is allowed to write it, who derives from it, what
legacy/competing representations still exist, and what gap remains. Paths,
classes, functions, and event names below are taken from current `main`
(post-A4, PR #107). Where the repository still has hybrid or competing sources,
this document says so explicitly — Rule 1 requires naming legacy paths, not
pretending they are gone.

Product-contract status refers to [`ISOLATED_SHADOW_LEDGER_V1`](supported-product-contracts.md)
(the only constrained contract). "In scope" / "out of scope" is enforced at
runtime by [`backend/accounts/pilot_policy.py`](../../backend/accounts/pilot_policy.py)
and verified by [`backend/accounts/pilot_preflight.py`](../../backend/accounts/pilot_preflight.py).

---

## 1. Shopify order

| Aspect | Current state |
|---|---|
| Raw/source evidence | Shopify `orders/create` / `orders/paid` webhooks → `ShopifyWebhookView.post` ([backend/shopify_connector/views.py](../../backend/shopify_connector/views.py)); 4-hour poller catch-up via the sync commands. Raw payload stored on the `ShopifyOrder` row. |
| Canonical identity / event | `shopify_order_id` unique per company; event `EventTypes.SHOPIFY_ORDER_PAID` (`"shopify.order_paid"`, [backend/events/types.py](../../backend/events/types.py)) with deterministic idempotency key. |
| Canonical model / output | `ShopifyOrder` ([backend/shopify_connector/models.py](../../backend/shopify_connector/models.py)) + the sales journal entry posted by the projection. |
| Authoritative writer | `process_order_pending` / `process_order_paid` ([backend/shopify_connector/commands.py](../../backend/shopify_connector/commands.py)) create the row and emit the event; `ShopifyAccountingHandler` ([backend/shopify_connector/projections.py](../../backend/shopify_connector/projections.py)) posts the JE (DR Shopify Clearing / CR revenue, tax, shipping via `ModuleAccountMapping` roles). |
| Derived readers | Reconciliation stage views, dashboards, reports; provider clearing balances. |
| Legacy / competing | A parallel generic path exists: `PlatformWebhookView` ([backend/platform_connectors/views.py](../../backend/platform_connectors/views.py)) can map `orders/paid → PLATFORM_ORDER_PAID` for the `shopify` slug, but production Shopify webhooks use `/api/shopify/webhooks/` — the generic route is registered yet unused for Shopify. Two potential interpretations of the same fact exist in code; only the legacy-named one is live. |
| Contract status | **In scope** (EGP only — non-EGP orders are structurally skipped before any row/event via `skip_pilot_currency` in `process_order_paid` / `process_order_pending`). |
| Known gap | Consolidating the dual webhook stacks is future work; posted-JE validity is not yet centrally enforced (A3). |

## 2. Shopify refund

| Aspect | Current state |
|---|---|
| Raw/source evidence | Shopify `refunds/create` webhook → `ShopifyWebhookView.post`; refund poller catch-up. |
| Canonical identity / event | `shopify_refund_id`; event `EventTypes.SHOPIFY_REFUND_CREATED` (`"shopify.refund_created"`). |
| Canonical model / output | `ShopifyRefund` row + reversal JE posted by `ShopifyAccountingHandler`. |
| Authoritative writer | `process_refund` ([backend/shopify_connector/commands.py](../../backend/shopify_connector/commands.py)); order-not-found races return `retryable` so Shopify redelivers (no silent loss). |
| Derived readers | Reconciliation, reports. |
| Legacy / competing | Same dual-stack note as orders (generic `refund_created` mapping exists but is unused for Shopify). |
| Contract status | **In scope** (EGP only, via the parent order's currency). |
| Known gap | Same as orders. |

## 3. Paymob / Bosta provider settlement

| Aspect | Current state |
|---|---|
| Raw/source evidence | Founder-uploaded settlement CSV (Paymob statement / Bosta COD report). Parsers `parse_paymob_csv` / `parse_bosta_csv` ([backend/accounting/settlement_imports.py](../../backend/accounting/settlement_imports.py)). |
| Canonical identity / event | Idempotency key `payment.settlement.received:{provider}:{batch_id}`; event `EventTypes.PAYMENT_SETTLEMENT_RECEIVED` (`"payment.settlement_received"`) — the provider-agnostic settlement spine. |
| Canonical model / output | The settlement JE that drains the provider clearing account: DR Expected Bank Deposit + fees (+ sales returns for failed COD) / CR provider clearing, routed by `SettlementProvider.posting_profile` ([backend/accounting/settlement_provider.py](../../backend/accounting/settlement_provider.py)). |
| Authoritative writer | `import_settlement_csv` emits the events; `PaymentSettlementProjection` ([backend/accounting/payment_settlement_projection.py](../../backend/accounting/payment_settlement_projection.py)) posts the JE. **Downstream accounting is a separate layer by design** — the event carries amounts + currency; FX interpretation happens in the JE-building layer ([backend/platform_connectors/je_builder.py](../../backend/platform_connectors/je_builder.py), `ExchangeRate.get_rate`), not in the parser. |
| Derived readers | Reconciliation stage 2/3, provider clearing balances, exception queue. |
| Legacy / competing | None for CSV settlements. (Stripe's pull-based settlement uses the same event per [ADR-0002](../adr/0002-canonical-payments-stripe-adapter.md) but is **out of contract scope**.) |
| Contract status | **In scope** (EGP only — `require_pilot_currency` rejects any foreign batch before the first event is emitted, so no partial import). |
| Known gap | Settlement-equation and JE validity checks are not yet a single central implementation (A3). |

## 4. Expected bank deposit (EBD)

| Aspect | Current state |
|---|---|
| Raw/source evidence | Derived — created as the debit leg of settlement JEs (and Stripe payout JEs outside the pilot). |
| Canonical identity / event | The JE line on the account mapped to `ModuleAccountMapping` role `EXPECTED_BANK_DEPOSIT` (module `shopify_connector`, seeded by `_setup_shopify_accounts` in [backend/accounts/commands.py](../../backend/accounts/commands.py)). |
| Canonical model / output | Open EBD journal lines = money remitted by a provider but not yet seen on a bank statement. |
| Authoritative writer | `PaymentSettlementProjection` (settlement JEs). Clearance (crediting EBD) happens only through the reconciliation match path. |
| Derived readers | Bank-matching candidate generation, reconciliation stage 3, aging views. |
| Legacy / competing | None. |
| Contract status | **In scope**; go-live preflight requires the mapping to exist and be postable (`missing_supported_mapping` in `pilot_preflight`). |
| Known gap | "Open vs cleared" is inferred from JE state + match links; a durable terminal-state model for money paths is **A5 (open)**. |

## 5. Canonical bank statement line

| Aspect | Current state |
|---|---|
| Raw/source evidence | Founder-uploaded bank CSV → `BankStatementCSVImportView` / `BankStatementListCreateView` ([backend/accounting/bank_views.py](../../backend/accounting/bank_views.py)). |
| Canonical identity / event | **No ingestion event.** Identity is the A17 `dedup_hash` per (company, account) on `BankStatementLine`. |
| Canonical model / output | `BankStatement` + `BankStatementLine` ([backend/accounting/models.py](../../backend/accounting/models.py)). |
| Authoritative writer | `import_bank_statement` ([backend/accounting/bank_reconciliation.py](../../backend/accounting/bank_reconciliation.py)) — **a documented Rule 1 hybrid**: it directly creates rows under `command_writes_allowed()` rather than emitting an event; dedup makes re-upload idempotent. Downstream match state on these lines is event-sourced (see §6). |
| Derived readers | Reconciliation matching, bank-rec UI, close checklist. |
| Legacy / competing | **Legacy `bank_connector` app still coexists**: `bank_connector.BankAccount` / `bank_connector.BankStatement` / `BankTransaction` / `ReconciliationException` ([backend/bank_connector/models.py](../../backend/bank_connector/models.py)) are a separate, older representation. The legacy matcher UI was retired (A166) and the pilot blocks the module (`Capability.LEGACY_BANKING`); the preflight flags any legacy rows (`legacy_bank_data`). Disposition: frozen legacy, removal unscheduled. |
| Contract status | **In scope** (EGP only — `require_pilot_currency` rejects foreign statements before any row is written; a postable Cash/Bank GL account is a go-live requirement). |
| Known gap | Raw ingestion is not event-first (accepted hybrid, per the precedent documented in the module itself); statement currency is not validated against an account currency (GL `Account` has no currency field). |

## 6. Reconciliation match

| Aspect | Current state |
|---|---|
| Raw/source evidence | Founder action (manual match / accept proposal) or the auto-matcher over statement lines + candidate JE lines. |
| Canonical identity / event | Six-event vocabulary `reconciliation.match_proposed / confirmed / rejected / unmatched`, `difference_resolved`, `exception_raised/resolved` ([backend/events/types.py](../../backend/events/types.py), payload classes in [backend/reconciliation/event_types.py](../../backend/reconciliation/event_types.py)); deterministic link identity per [ADR-0001](../adr/0001-reconciliation-link.md). |
| Canonical model / output | `ReconciliationLink` ([backend/reconciliation/models.py](../../backend/reconciliation/models.py)) + match state stamped on `BankStatementLine`. |
| Authoritative writer | Commands `auto_match_statement` / `manual_match` / `unmatch_line` ([backend/reconciliation/commands.py](../../backend/reconciliation/commands.py)) emit events; `ReconciliationProjection` ([backend/reconciliation/projections.py](../../backend/reconciliation/projections.py)) is the **sole writer of match state** (legacy direct mutation was decommissioned in A86.7b; guarded-field architecture tests hold the line). |
| Derived readers | Reconciliation page, exception queue, close checklist, F16 tie-outs. |
| Legacy / competing | Two exception representations still exist (rich legacy `bank_connector.ReconciliationException` vs. the needs-review queue over statement-line differences) — a known, documented split from ADR-0001. `source_document` string joins persist in older views. |
| Contract status | **Manual matching in scope; unsafe auto-match/unmatch blocked** (`Capability.UNSAFE_BANK_MATCH` gates `auto_match_statement` and unmatch paths for the pilot). |
| Known gap | Match/clearance JE validity centralization is A3; exception-model unification is future work. |

## 7. Posted journal entry

| Aspect | Current state |
|---|---|
| Raw/source evidence | Every upstream fact above, plus manual JEs and module documents (invoices, bills, receipts, payments). |
| Canonical identity / event | `EventTypes.JOURNAL_ENTRY_CREATED` / `JOURNAL_ENTRY_POSTED` (`"journal_entry.created"` / `"journal_entry.posted"`); entry number via `CompanySequence`. |
| Canonical model / output | `JournalEntry` + `JournalLine` ([backend/accounting/models.py](../../backend/accounting/models.py)) — the ledger truth all reports derive from. |
| Authoritative writer | The accounting command layer ([backend/accounting/commands.py](../../backend/accounting/commands.py)) and the JE-posting projections (Shopify accounting handler, payment-settlement projection). |
| Derived readers | Account/period/dimension balances ([backend/projections/](../../backend/projections/)), all reports, drill-downs. |
| Legacy / competing | None at the model level; the **validity checks are duplicated across layers** (emitter payload validation, model-level postability checks, period gates in `accounting/policies.py` / `accounting/validation.py`) rather than one canonical implementation. |
| Contract status | In scope (shadow-ledger reports are the pilot's output). Rebuild/replay is **blocked** for pilot companies (`Capability.PROJECTION_REBUILD` gate in [backend/projections/base.py](../../backend/projections/base.py)). |
| Known gap | **A3 (open): one central posted-JE invariant at emit + apply.** A3-PR1 introduced the canonical invariant core ([backend/accounting/journal_invariant.py](../../backend/accounting/journal_invariant.py), 14 stable violation codes) and the read-only corpus scanner (`audit_posted_journal_corpus`) — but **emit and apply enforcement remain pending** (A3-PR2/PR3), so Rule 3 for JE validity is still not runtime-enforced. |

---

## Out-of-contract facts (documented so their gates are auditable)

| Fact | State |
|---|---|
| Shopify Payments payout | `sync_payouts` ([backend/shopify_connector/commands.py](../../backend/shopify_connector/commands.py)) is the sole `SHOPIFY_PAYOUT_SETTLED` emitter; **structurally skipped** for pilot companies; interactive payout views return 403. Payout/dispute data presence fails preflight (`payout_data`). |
| Disputes / chargebacks | **More than one historical path exists**: the live legacy route (`ShopifyWebhookView` → `process_dispute`) and the registered-but-unused generic `platform_connectors` dispute mapping. A4 disables dispute processing for the pilot (`Capability.SHOPIFY_DISPUTES` skip at the deepest shared boundary) — the dual stack itself remains as documented debt. |
| Stripe | Canonical adapter per [ADR-0002](../adr/0002-canonical-payments-stripe-adapter.md); fully out of pilot scope (connect command gate + webhook block before side effects). |
| Inventory / COGS | Option B: item creation forced NON_STOCK; INVENTORY/COGS module mappings never seeded for pilots; `ShopifyStore.default_inventory_account` / `default_cogs_account` are **never written by any code path — legacy/dead configuration**, flagged by preflight if ever populated. |
| Foreign currency | Rejected at every ingestion boundary before events (`require_pilot_currency` / `skip_pilot_currency`); `ExchangeRate.get_rate` short-circuits same-currency, so no FX branch executes for EGP-only data. |
