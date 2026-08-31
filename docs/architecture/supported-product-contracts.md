# Supported Product Contracts

Rule 5 of the [architecture constitution](architecture-constitution.md):
supported capability combinations are explicit, versioned contracts — not
scattered feature booleans. This document describes each contract **exactly as
currently implemented**. There is one constrained contract today.

`Company.pilot_profile = NONE` means no constrained profile is selected — the
company runs the standard shared-product behavior. It does **not** certify the
company as production-ready; readiness is still gated by the pilot chain (A3
and A5 code-complete but not deployed; G1/G2 open — see
[the live tracker](../status/constrained_pilot_status.md)).

---

## ISOLATED_SHADOW_LEDGER_V1

A supervised money-movement proof for one Egyptian Shopify merchant: Shopify
orders/refunds → Paymob/Bosta settlement CSVs → canonical bank CSV → shadow
ledger, operated by the founder. Not statutory books.

Implemented by A4 (PR #107, merged 2026-07-27 at `1e12250`; reopened
2026-08-10 and RE-CLOSED through PRs #118–#123, then again by PR #137
(`3e962c5`), which closed the matched-line exclusion admission gap surfaced
during the A5 closure review — see the tracker's A4 row).
Enforcement is
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
- supervised manual journals as **traced pilot adjustments** — **EGP-only at
  header AND line level**, and (A5-PR4a) source-traced at post. The manual
  HTTP surface routes exclusively through the manual-journal process
  boundary (`accounting.commands.*_manual_journal_entry`): each wrapper owns
  `serialized_company_admission` (held through the shared command's whole
  commit, one serializable ordering with `activate_pilot_profile`) and the
  shared command runs the canonical `require_pilot_journal_currency`
  validator at its currency-resolution points — header, every line,
  `amount_currency` foreign legs, and any non-1 exchange rate all reject
  BEFORE any event/sequence/row (blank keeps its book-at-home-currency
  meaning). **Before activation, EGP setup/opening journals may be posted.
  After activation, manual journals are supported only as supervised pilot
  adjustments: every post requires a server-validated same-company source
  reference and a nonblank reason. Source provenance is server-stamped
  (`source_module="pilot_adjustment"` + one typed
  `source_document="<kind>:<id>"` from the closed resolver registry in
  `accounting.pilot_adjustments`; the raw fields are never
  request-writable); supported automated Shopify, Paymob/Bosta settlement
  and reconciliation processes are unaffected (no sentinel, no gate). Draft
  cleanup remains available**, drafts stay freely editable before posting,
  and a manual reversal requires its own reason while inheriting the
  original's source reference. The scratchpad commit — the one non-wrapper
  free-authoring door — refuses under the active pilot. The
  `PilotProfileActivation` row is the durable pre/post-activation cutoff,
  enforced as drift by `pilot_activation_audit_missing` /
  `untraceable_manual_posted_journal` / `invalid_pilot_adjustment_reference`
  / `pilot_adjustment_source_company_mismatch`. Manual sales invoices,
  credit notes, customer receipts, EDIM financial commit and FX revaluation
  remain UNSUPPORTED and are not addressed by this boundary;
- shadow-ledger reports and exception review.

### Excluded capabilities (each is a runtime gate, not UI hiding)

| Exclusion | Gate |
|---|---|
| Stripe | `Capability.STRIPE` — connect command + platform webhook blocked before side effects |
| Shopify Payments payout accounting | `Capability.SHOPIFY_PAYOUT_ACCOUNTING` — scheduled sync skips; interactive views 403 |
| Disputes / chargebacks | `Capability.SHOPIFY_DISPUTES` — `process_dispute` skips (no row, no event) |
| Inventory / COGS / FIFO | `Capability.INVENTORY` (Option B) — items forced NON_STOCK; no inventory/COGS module mappings; no cost fetch, no FX cost conversion |
| Purchasing / accounts payable | `Capability.PURCHASING_ACCOUNTING` — purchase documents (bills, orders, goods receipts, credit notes), purchase-originated journals, and the vendor-payment / AP-allocation workflow. Three enforcement layers: module admission (`ModuleEnabled` raises before the enablement lookup, and both enable doors — `CompanyModulesView.put` + onboarding Step 4 — refuse enabling with locked full-payload validation before any write), the `purchases` command boundary (a **serialized** `requires_capability` gate on every command — the Company admission row is locked, the profile re-read fresh, and the lock held through the mutation's commit, so admission and `activate_pilot_profile` share one serializable ordering), and `record_vendor_payment` (same serialized gate; stable 403 `pilot_scope_blocked` at its route). Preflight drift detection runs on durable canonical evidence — surviving documents, immutable purchase/AP events, vendor payment allocations — not only document rows. Manual journals — including vendor-tagged lines — remain governed by the manual-journal rules in "Included capabilities" above (EGP-only at header and line; after activation, post-able only as traced pilot adjustments per A5-PR4a) |
| Foreign currency | `require_pilot_currency` at settlement/bank/Shopify ingestion; `require_pilot_journal_currency` (header + every line + `amount_currency` legs + non-1 rates) at the serialized manual-journal process boundary; EGP-only proven at go-live and by the `non_egp_journal_line_data` / `fx_line_residue` / `fx_header_rate_residue` drift codes |
| Multiple users | `Capability.ADD_MEMBER` on every membership path |
| Legacy banking module | `Capability.LEGACY_BANKING` |
| Unsafe bank-match actions | `Capability.UNSAFE_BANK_MATCH` — blocked: automatic matching, unmatching, statement deletion requiring unmatch, and (PR #137) match-destructive exclusion. Retained: manual matching, and exclusion of a never-matched nuisance row |
| Rebuild / replay | `Capability.PROJECTION_REBUILD` at the single shared choke point |
| Second company / signup | `deployment_has_pilot()` deployment-wide block |
| In-app backup **restore** | `Capability.BACKUP_RESTORE` — a restore overwrites the company's books, configuration and event stream in one transaction; blocked at the canonical boundary `backups.importer.restore_company` under the Company admission lock (early HTTP 403 + CLI refusal; break-glass flags do not bypass it). Backup **export/download stay available**. **This is not G2** — G2 recovery is a separate **isolated-database** restore drill, not this in-app route |

### Activation and go-live

- Activation **only** through `python manage.py activate_pilot_profile
  --company <id> --yes` — transactional (`select_for_update`), refuses on any
  forbidden state, never repairs data.
- Activation writes durable audit evidence: a `PilotProfileActivation` row in
  the **same transaction** as the profile write (inside
  `command_writes_allowed()`).
- `Company.pilot_profile` is **activation-owned**: `activate_pilot_profile` is
  its sole production writer (pinned by an architecture ratchet). It is **never**
  set from a backup restore, and the Company/User read-model projections apply
  **only** their producing command's whitelisted fields — an event naming
  `pilot_profile` (or any other non-owned field) is a visible projection failure,
  never an arbitrary `setattr`.
- Go-live requires `python manage.py pilot_preflight --company <id> --phase
  go-live` to pass with zero violations — including the full agreed workflow:
  proven-EGP store, exact sole-store ↔ sole-OWNER ↔ active-user binding
  (A1 `ShopifyUserBinding`), postable clearing/EBD mappings, ≥1 active
  Paymob/Bosta provider routed to an active posting profile, postable
  Cash/Bank GL account, and a clean forbidden-state sweep (one violation code
  per condition, each with a seeded acceptance test).
- **A1 and A4 still require live G1 operational proof** (fresh-company E2E in
  the real Shopify iframe with control totals + failure injection).
- **A3 is COMPLETE** (A3-PR3, PR #125) and **A5 is COMPLETE** for this
  contract (PRs #127–#135, closed by the
  [2026-08-30 final closure review](../audits/2026-08-30-a5-final-closure-review.md));
  neither is deployed. **G1 and G2 remain open** — merchant data remains
  blocked until both close (see the
  [live tracker](../status/constrained_pilot_status.md)).

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
