# ADR-0003: Enforce the Nxentra architecture constitution through documentation, CI fitness functions, PR contracts and runtime policies

- **Status:** Accepted — acceptance becomes effective when PR #109 is merged
  into `main`. From that merge, the constitution and its exception/ratchet
  rules are binding on `main`; before the merge, these files are proposed
  changes on the `chore/architecture-governance` feature branch.
- **Date:** 2026-07-28
- **Decision owner:** founder/eng
- **Related issue/PR:** governance PR `chore/architecture-governance`; evidence base: [2026-07-18 current-state audit](../audits/2026-07-18-nxentra-current-state-audit.md) (pinned to `bfb09fa` — historical evidence, not current-HEAD status), A4 review cycles on PR #107
- **Architecture rule affected:** establishes Rules 1–6 of the [architecture constitution](../architecture/architecture-constitution.md)

## Context

The 2026-07-18 audit and the A4 review cycles surfaced a repeated pattern: the
architecture's intent (event-first finance, provider adapters outside the core,
single writers, pure projections) was real but **bypassable** — direct writes
existed beside event paths, provider conditions leaked into core views,
invariants were re-implemented locally, and review had to re-litigate the same
architectural questions on every PR. The A4 cycle also proved the countermove
works: explicit capability contracts + runtime gates + exhaustive preflight +
adversarial review produced enforcement that survived (including a CI-only
defect — a weakly-referenced signal receiver — that documentation alone would
never have caught).

The audit is used here as **rationale and evidence of prior architectural
problems**. It is pinned to `bfb09fa` and must not be read as current-HEAD
status. Current status: A1 code-complete (operational proof pending G1);
A2 complete; **A4 complete at PR #107 (`1e12250`)**; A3 open; A5 open; G1
open; G2 open — per the authoritative
[constrained-pilot status tracker](../status/constrained_pilot_status.md).

## Decision

Adopt a four-layer governance baseline, each layer honest about what it can
and cannot enforce:

1. **Documentation defines intent.** The
   [architecture constitution](../architecture/architecture-constitution.md)
   (six rules + exception/ratchet policy), the
   [canonical money spine](../architecture/canonical-money-spine.md)
   (evidence-based writers/readers/legacy map), and the
   [supported product contracts](../architecture/supported-product-contracts.md)
   (ISOLATED_SHADOW_LEDGER_V1 as implemented).
2. **Architecture tests prevent selected code violations.**
   [backend/tests/test_architecture_rules.py](../../backend/tests/test_architecture_rules.py)
   (AST rules: views must not enter projection write contexts; projections
   must not emit events; guarded-field writes; no signal-bypassing
   `Company.is_active` mutation) — with allowlists that may shrink but not
   grow without an ADR.
3. **The PR contract forces architectural reasoning.**
   [.github/pull_request_template.md](../../.github/pull_request_template.md)
   plus the deterministic checker
   [scripts/check_pr_architecture_contract.py](../../scripts/check_pr_architecture_contract.py)
   run by the `PR Architecture Contract` workflow on every pull request. The
   checker verifies structure and substance mechanically (headings present,
   answers non-placeholder, attestations checked, exactly one product-contract
   selection, ADR linked where required). It does not attempt natural-language
   judgment.
4. **Runtime policies enforce product contracts.** A4's capability gates are
   the model; A3 (central posted-JE invariant) and A5 (durable terminal
   outcomes) extend runtime enforcement and **remain open**.

## Alternatives considered

- **Documentation only** — rejected: the audit showed intent without
  enforcement decays (that was the starting state).
- **Big-bang enforcement (block merges on everything now)** — rejected:
  current debt is real; a gate that fails on pre-existing state teaches people
  to bypass gates. Ratchet instead.
- **NL/LLM judgment of PR bodies** — rejected: non-deterministic, unauditable;
  the checker stays mechanical and narrow.
- **Immediate repo-wide import-linter dependency rules (Rule 2)** — deferred:
  worth doing, but requires an allowlist inventory first; introduced
  incrementally under the ratchet policy.

## Consequences

- Every PR pays a small, explicit reasoning cost; unreviewable "drive-by"
  architectural changes get caught at the template/checker layer.
- The money spine gives reviewers a single place to check "who is allowed to
  write this fact" instead of re-deriving it.
- Known debt is now *named* (dual webhook stacks, legacy `bank_connector`,
  duplicated JE validity checks, dead `ShopifyStore` inv/COGS defaults,
  dual exception models) — which makes silently re-introducing it harder.
- **This baseline does not claim to eliminate current architecture debt.** It
  freezes the boundary and ratchets.

## Financial and operational invariants

Unchanged by this ADR (governance-only). The invariants the governance
protects are those already enforced: A4 capability gates + preflight, the
architecture rules, the settlement equation guard, A17 dedup idempotency.
Central posted-JE validity remains **A3 (open)**; durable terminal financial
outcomes remain **A5 (open)**.

## CI fitness functions

- Existing: the architecture-rule tests (blocking in the Lint & Type Check
  job), canonical-spine mypy, the full test suites.
- New: the `PR Architecture Contract` workflow
  ([.github/workflows/pr-architecture-contract.yml](../../.github/workflows/pr-architecture-contract.yml))
  running the deterministic body checker, with pure tests in
  [backend/tests/test_pr_architecture_contract.py](../../backend/tests/test_pr_architecture_contract.py).
- **Honest status: the new workflow is initially advisory.** It runs and fails
  loudly on every PR, but it does not block merges until a later GitHub
  ruleset / branch-protection step marks it as required. The existing `main`
  Quality Gate is not modified by this PR. Rules 1–4 enforcement remains
  partial and is strengthened incrementally through A3 and A5; Rule 5 is
  materially enforced for ISOLATED_SHADOW_LEDGER_V1 by A4; Rule 6 begins
  through the PR evidence contract.

## Exception scope

None granted. This ADR grants no rule exceptions; it defines how exceptions
are granted (accepted ADR + named symbols + tests + owner + removal trigger;
allowlists ratchet down only).

**Temporary bootstrap limitation — RESOLVED in design, pending operational
proof (not a permanent architecture exception): trusted-checker execution.**
History and current state:

- PR #109 introduced the checker in **advisory** mode; its original
  `pull_request` workflow executed `scripts/check_pr_architecture_contract.py`
  from the PR checkout — a PR could therefore modify the checker to pass its
  own body (the accepted P2 finding);
- the follow-up governance-hardening PR (`chore/trusted-pr-architecture-check`)
  replaces that design: the workflow now runs on `pull_request_target` (its
  definition loads from the protected base branch), checks out **only** the
  exact protected base commit (`github.event.pull_request.base.sha`) into
  `trusted-base/`, executes the checker from that trusted checkout, keeps
  read-only permissions and no secrets, and passes the PR body strictly as
  data via the `PR_BODY` environment variable — static contract tests in
  `backend/tests/test_pr_architecture_workflow_contract.py` pin these
  properties;
- because a `pull_request_target` workflow is loaded from the base branch,
  the new definition **cannot prove itself on the PR that introduces it** —
  it has not yet operated from `main`. The check therefore still must not
  become a required status check until the merged workflow is observed
  working on a subsequent real pull request;
- branch protection remains intentionally deferred.

*Removal trigger:* Remove this limitation before `PR Architecture Contract`
becomes required in any GitHub ruleset or branch-protection rule — i.e. only
after the trusted workflow has been proven on a real PR post-merge.

**Operational proof.** The trusted-base workflow design was merged in PR #110
(`main` commit `c660321`). Operational proof is pending on this pull request
(`docs/prove-trusted-pr-architecture-check`) until its GitHub Actions run
completes: the proof criterion is that `PR Architecture Contract` runs from
the base revision — `pull_request_target` definition loaded from `main`,
checker executed from the `trusted-base/` checkout of
`github.event.pull_request.base.sha` — and successfully validates this pull
request's body. The check remains advisory until this proof passes and branch
protection is separately configured.

**Known governance debt recorded here:** the ADR directory contains a
numbering collision — two `0002-*` files
([0002-canonical-payments-stripe-adapter.md](0002-canonical-payments-stripe-adapter.md)
and [0002-prc-payout-readmodel-cutover.md](0002-prc-payout-readmodel-cutover.md)).
Both are left unchanged (renames would break links). Numbering continues from
0003; the next ADR is 0004.

## Migration and rollback

Migration: none (documentation + additive CI). Rollback: delete the workflow
file to silence the check; the documents remain as reference. No application
behavior depends on any file in this ADR.

## Removal trigger

Not applicable to the baseline itself. Individual transitional statements
inside it carry their own triggers: the "advisory" status of the PR check ends
when a branch-protection/ruleset step (explicitly deferred from this PR) marks
`PR Architecture Contract` as required — which, per the bootstrap limitation
in *Exception scope*, is only permitted after the trusted-checker follow-up PR
is merged and proven; the Rule 3/Rule 4 partial-enforcement notes end when
A3/A5 land and their fitness functions replace the prose.
