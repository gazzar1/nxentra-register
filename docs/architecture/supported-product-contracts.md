# Supported Product Contracts

Rule 5 of the [architecture constitution](architecture-constitution.md):
supported capability combinations are explicit, versioned contracts — not
scattered feature booleans. This document describes each contract **exactly as
currently implemented**. There is one constrained contract today.

`Company.pilot_profile = NONE` means no constrained profile is selected — the
company runs the standard shared-product behavior. It does **not** certify the
company as production-ready; standard readiness is gated by A3/A5/G1/G2.

---

## ISOLATED_SHADOW_LEDGER_V1

A supervised money-movement proof for one Egyptian Shopify merchant: Shopify
orders/refunds → Paymob/Bosta settlement CSVs → canonical bank CSV → shadow
ledger, operated by the founder. Not statutory books.

Implemented by A4 (PR #107, merged 2026-07-27 at `1e12250`). Enforcement is
**runtime gates at the deepest shared boundaries**
([backend/accounts/pilot_policy.py](../../backend/accounts/pilot_policy.py)) —
interactive paths raise `PilotScopeBlocked` (HTTP 403); scheduled/webhook paths
return a structured `skipped_pilot_scope` result with **no mutation and no
retry**. The preflight
([backend/accounts/pilot_preflight.py](../../backend/accounts/pilot_preflight.py))
is an exhaustive read-only proof + drift detector, never the enforcement.
Unknown profile values fail closed (every gated capability blocked).

Each blocked runtime **mutation** additionally admits under the Company
**admission lock**, serialized against the `activate_pilot_profile` cutover: the
capability is decided on the freshly locked Company profile and the decision is
held through the mutation's commit, so exactly one ordering with activation can
occur — the mutation commits first and activation judges its durable state, or
activation commits first and the mutation observes the active profile and
refuses / skips with zero side effects. A small set of multi-transaction /
deployment-wide paths (second-company creation, projection rebuild, the
scheduled/batched Stripe-payout and settlement-CSV imports) are **tracked
design-deferred residuals** that stay on their fail-closed point-in-time gate;
see [docs/status/constrained_pilot_status.md](../status/constrained_pilot_status.md).
No global deadlock-freedom is claimed.

### Deployment boundary

- one isolated deployment/database (no other `Company` row — active **or
  inactive** — may exist at activation; `not_isolated_rows`);
- one merchant company (an existing pilot row, even deactivated, blocks
  deployment-wide signup and company creation via `deployment_has_pilot()`);
- one active OWNER membership (all add/reactivate paths gated:
  invitations, `create_user_with_membership`, `accept_invitation`, admin);
- one active Shopify store, **proven EGP** (durable
  `ShopifyStore.shop_currency` or read-only probe; unknown fails go-live);
- EGP only (`require_pilot_currency` / `skip_pilot_currency` at every
  ingestion boundary, before any event or row);
- January fiscal-year start, 12/13-period structure (currency + fiscal
  configuration frozen: `Capability.CURRENCY_FISCAL_CHANGE`);
- `Company.is_active` frozen (pre_save guard + admin hardening + CI
  architecture rule against signal-bypassing mutations).

### Included capabilities

- Shopify order and refund accounting (webhook + poller);
- Paymob and/or Bosta settlement CSV import (`PAYMENT_SETTLEMENT_RECEIVED`);
- canonical bank CSV import (A17 dedup);
- provider clearing accounts (posting-profile routed);
- expected bank deposit (EBD) lifecycle;
- founder-reviewed supported bank matching (manual match/confirm);
- shadow-ledger reports and exception review.

### Excluded capabilities (each is a runtime gate, not UI hiding)

| Exclusion | Gate |
|---|---|
| Stripe | `Capability.STRIPE` — connect command + platform webhook blocked before side effects |
| Shopify Payments payout accounting | `Capability.SHOPIFY_PAYOUT_ACCOUNTING` — scheduled sync skips; interactive views 403 |
| Disputes / chargebacks | `Capability.SHOPIFY_DISPUTES` — `process_dispute` skips (no row, no event) |
| Inventory / COGS / FIFO | `Capability.INVENTORY` (Option B) — items forced NON_STOCK; no inventory/COGS module mappings; no cost fetch, no FX cost conversion |
| Purchasing / accounts payable | `Capability.PURCHASING_ACCOUNTING` — purchase documents (bills, orders, goods receipts, credit notes), purchase-originated journals, and the vendor-payment / AP-allocation workflow. Three enforcement layers: module admission (`ModuleEnabled` raises before the enablement lookup, and both enable doors — `CompanyModulesView.put` + onboarding Step 4 — refuse enabling with locked full-payload validation before any write), the `purchases` command boundary (a **serialized** `requires_capability` gate on every command — the Company admission row is locked, the profile re-read fresh, and the lock held through the mutation's commit, so admission and `activate_pilot_profile` share one serializable ordering), and `record_vendor_payment` (same serialized gate; stable 403 `pilot_scope_blocked` at its route). Preflight drift detection runs on durable canonical evidence — surviving documents, immutable purchase/AP events, vendor payment allocations — not only document rows. Manual journals — including vendor-tagged lines — remain governed by the ordinary manual-journal rules |
| Foreign currency | `require_pilot_currency` at settlement/bank/Shopify ingestion; EGP-only proven at go-live |
| Multiple users | `Capability.ADD_MEMBER` on every membership path |
| Legacy banking module | `Capability.LEGACY_BANKING` |
| Unsafe automatic bank matching | `Capability.UNSAFE_BANK_MATCH` (manual matching retained) |
| Rebuild / replay | `Capability.PROJECTION_REBUILD` at the single shared choke point |
| Second company / signup | `deployment_has_pilot()` deployment-wide block |

### Activation and go-live

- Activation **only** through `python manage.py activate_pilot_profile
  --company <id> --yes` — transactional (`select_for_update`), refuses on any
  forbidden state, never repairs data.
- Activation writes durable audit evidence: a `PilotProfileActivation` row in
  the **same transaction** as the profile write (inside
  `command_writes_allowed()`).
- Go-live requires `python manage.py pilot_preflight --company <id> --phase
  go-live` to pass with zero violations — including the full agreed workflow:
  proven-EGP store, exact sole-store ↔ sole-OWNER ↔ active-user binding
  (A1 `ShopifyUserBinding`), postable clearing/EBD mappings, ≥1 active
  Paymob/Bosta provider routed to an active posting profile, postable
  Cash/Bank GL account, and a clean forbidden-state sweep (one violation code
  per condition, each with a seeded acceptance test).
- **A1 and A4 still require live G1 operational proof** (fresh-company E2E in
  the real Shopify iframe with control totals + failure injection).
- **A3, A5, G1 and G2 remain open.**

### Acceptance tests

[backend/tests/test_a4_pilot_gates.py](../../backend/tests/test_a4_pilot_gates.py)
(~133 tests: every gate at unit + HTTP route + webhook level, Option B and
no-FX proofs, activation audit, one seeded test per preflight violation code,
admin bypass closures, and the purchasing/AP exclusion — every command blocked
with zero side effects, both enable doors incl. mixed-payload atomicity,
stale-enabled-row route 403s, per-leg durable-evidence drift detection) plus
the architecture rules in
[backend/tests/test_architecture_rules.py](../../backend/tests/test_architecture_rules.py)
(including the purchasing-command gate-marker ratchet and the optional-module
module-enablement-disposition ratchet).

### Graduation requirements

A broader contract (more users, more currencies, payouts, inventory, …) is a
**new versioned contract**, never an edit-in-place. It requires:

- explicit included/excluded capability lists;
- an accepted ADR;
- runtime gates for everything still excluded;
- activation + go-live preflight for the new boundary;
- acceptance tests at the same standard;
- one isolated proof deployment before wider activation.
