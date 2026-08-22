# ADR-0004: Admit two classes of deliberately-unlocked A4 gate sites (operator-CLI entry refusals; the ECB auto-fetch deny-as-rate-miss)

- **Status:** Accepted — acceptance becomes effective when PR #123
  (`fix/a4-capability-dispositions`) is merged into `main`; before that merge
  this file is a proposed change on the PR branch.
- **Date:** 2026-08-20
- **Decision owner:** founder/eng (dispositions decided by the founder
  2026-08-20; recorded in the A4 row of
  [docs/status/constrained_pilot_status.md](../status/constrained_pilot_status.md))
- **Related issue/PR:** PR #123 (A4 Capability Dispositions); evidence base:
  the 2026-08-13 Pilot Process-Surface Completeness Assessment (C5/C9 clusters)
  and the 2026-08-15 Manual-Financial/EGP Boundary Assessment
- **Architecture rule affected:** the allowlist ratchet policy of
  [ADR-0003](0003-architecture-constitution-governance.md) /
  [AGENTS.md](../../AGENTS.md) ("allowlists in
  `backend/tests/test_architecture_rules.py` may shrink freely but never grow
  without an ADR") as it applies to architecture Rule 12's witnessed sets

## Context

Architecture Rule 12 requires every production call site of an A4 pilot-gate
function to either hold the Company admission lock or carry an explicit
classification in a witnessed set. PR #123 closes the final A4 residual
surfaces and, in doing so, introduces gate calls at seven sites that are
deliberately NOT admission-serialized:

1. **Six operator-CLI entry refusals** — the five seed/demo commands
   (`seed_demo_company`, `seed_shopify_demo`, `seed_test_csv_pack`,
   `seed_stripe_demo`, `seed_test_payout`) and `import_tenant_events` now call
   `require_no_pilot_deployment()` / `deployment_has_pilot()` at `handle()`
   entry and abort the WHOLE command on a pilot deployment. The decision is
   deployment-wide (does ANY pilot company row exist?), so there is no single
   Company row whose admission lock could serialize it — the same structural
   shape as the tracked Family-B residual (`register_signup`/`create_company`).
2. **`ExchangeRate._auto_fetch_rate`** — the ECB auto-fetch fallback inside
   `get_rate` now denies under a pilot profile and reads as an ordinary
   rate-miss. It is a LEAF classmethod reachable from callers already holding
   domain locks (journal posting, receipt recording, report views), so
   acquiring the Company admission lock inside it would invert the pinned
   Company-first lock order and re-create exactly the Counter→Company AB/BA
   forms PR #119 eliminated.

Without an ADR, admitting these sites to Rule 12's witnessed sets would grow
allowlists silently — the exact rot ADR-0003's ratchet policy exists to stop.

## Decision

Admit both classes as reviewed, deliberately-unlocked dispositions:

- A new witnessed set **`A4_OPERATOR_CLI_REFUSAL_SITES`** (initial membership:
  exactly the six `Command.handle` sites above) for operator-CLI ENTRY
  refusals: the gate refuses the whole command up front, point-in-time by
  design; the mutation that follows (only on non-pilot deployments) is trusted
  operator tooling. Drift backstop: the `seeded_event_residue`,
  `external_api_key_present` and related preflight checks make any slipped-in
  residue activation-blocking.
- One new member in **`A4_DESIGN_DEFERRED_MUTATING_SITES`**:
  `accounting/models.py::ExchangeRate._auto_fetch_rate`. The deny is
  point-in-time; the `exchange_rate_data` preflight check (ALL rate rows are
  pilot residue) is the load-bearing drift backstop, because a NONE-profile
  fetch racing activation can commit a rate row that the deny alone would not
  remove.

## Alternatives considered

- **Serialize the CLI refusals on a Company row** — rejected: the refusal is
  deployment-wide; locking one row proves nothing about a second company, and
  a deployment-wide lock primitive is the open Family-B design explicitly kept
  out of this PR by founder decision.
- **Serialize `_auto_fetch_rate` under `serialized_company_admission`** —
  rejected: lock-order inversion (admission lock acquired below domain locks),
  plus remote HTTP I/O adjacent to the lock; both violate the established A4
  lock doctrine.
- **Leave the sites ungated** — rejected: that is the pre-PR state the
  Process-Surface Assessment flagged (C5/C9 and the rate-fetch write path).
- **Classify the CLI sites in `A4_DESIGN_DEFERRED_MUTATING_SITES`** —
  rejected: that set documents *deferred serialization designs*; the CLI
  refusals are a *final* disposition (operator-trust with preflight backstop),
  and conflating the two would blur what "deferred" promises.

## Consequences

Easier: Rule 12 stays two-sided and honest — every unlocked gate call carries
exactly one reviewed classification, and a NEW unlocked CLI gate cannot land
without either serializing or amending this ADR's set. Harder/accepted debt:
the refusals and the auto-fetch deny are point-in-time; the accepted race
windows are documented above and each carries a named preflight drift
backstop.

## Financial and operational invariants

Protects the A4 boundary invariants: no seed/demo/import tooling writes below
the runtime gates on a pilot deployment; EGP-only pilot books acquire no
exchange-rate rows (canonical checks: `require_no_pilot_deployment` in
`accounts/pilot_policy.py`; the `is_supported(EXCHANGE_RATE_MAINTENANCE)` deny
in `ExchangeRate._auto_fetch_rate`; preflight codes `seeded_event_residue`,
`external_api_key_present`, `exchange_rate_data`).

## CI fitness functions

`backend/tests/test_architecture_rules.py`:
`test_every_gate_call_site_is_serialized_or_explicitly_classified` (Rule 12 —
membership, staleness, and disjointness of the witnessed sets, including
`A4_OPERATOR_CLI_REFUSAL_SITES`); the refusal behavior itself is pinned by
`backend/tests/test_a4_dispositions.py` (per-command CommandError tests, the
purge gate-before-delete test, and the auto-fetch no-network deny test).

## Exception scope

Exactly the seven sites named above, together with any sites added under
**Amendments** below. Any additional member of either set requires amending this
ADR (or a new one); membership may shrink freely.

## Amendments

### 2026-08-22 — add `backfill_platform_settlement_dims` to `A4_OPERATOR_CLI_REFUSAL_SITES`

The A5 Step-0 durable-outcome assessment found one further ungated operator CLI
of the same class:
`platform_connectors/management/commands/backfill_platform_settlement_dims.py`
(the A139 retro-tag of platform charge/refund clearing lines). Its `--apply`
mode emits `JOURNAL_LINE_ANALYSIS_SET` events **and** writes
`JournalLineAnalysis` rows across every company under `rls_bypass()`, below the
A4 runtime gates — the same "writes state below the gates" hazard as
`import_tenant_events`. Its report-only default is a pure read and is left
available (the `--dry-run`-equivalent carve-out `import_tenant_events` already
uses).

Disposition: identical to the six original operator-CLI refusals. The command's
`--apply` path calls the deployment-wide gate (`deployment_has_pilot()` →
`CommandError`) at `Command.handle` entry, and the site
`platform_connectors/management/commands/backfill_platform_settlement_dims.py::Command.handle`
is added to `A4_OPERATOR_CLI_REFUSAL_SITES`. Effective membership of that set is
now **seven**; total admitted unlocked sites across both witnessed sets, **eight**.
Behavior pinned by
`test_backfill_platform_settlement_dims_refuses_apply_on_pilot_deployment` (and
the report-only counterpart) in `backend/tests/test_a4_dispositions.py`.
