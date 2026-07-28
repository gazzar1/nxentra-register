## Summary

<!-- What this PR changes, in 2–5 sentences. Name the modules touched. -->

## Why now?

<!-- Rule 6 evidence: the confirmed defect, pilot evidence, repeated founder
intervention, repeated change, customer requirement, measured problem, or
invariant that justifies this change now. "It seemed cleaner" is not evidence. -->

## Supported product contract

- [ ] No supported-contract change
- [ ] ISOLATED_SHADOW_LEDGER_V1
- [ ] New or changed product contract — ADR linked below

<!-- Check exactly one. "New or changed product contract" requires an ADR
reference in the "Allowlist or ADR" section below. -->

## Architecture contract

### Canonical financial fact

<!-- Which fact from docs/architecture/canonical-money-spine.md this PR
touches (order, refund, settlement, EBD, bank line, match, posted JE) — or a
specific statement that none is affected, e.g. "None — documentation-only
change; no runtime or financial path is affected." Bare N/A is rejected. -->

### Source of truth and writer impact

<!-- Does this PR add, move, or modify an authoritative writer? A second
writer for an existing fact requires an accepted ADR (Rule 1). -->

### Provider/core dependency

<!-- Does any core financial module gain a dependency on (or knowledge of) a
provider-specific module? Direction must stay adapter → core (Rule 2). -->

### Central invariant

<!-- Which canonical invariant implementation this change calls, changes, or
should have called. Duplicating an invariant locally is a violation (Rule 3). -->

### Projection/reactor boundary

<!-- If projections are touched: confirm they only derive state (no external
calls, no event emission, no queued work, no swallowed financial failures).
Orchestration belongs in an explicit reactor (Rule 4). -->

### Supported profile and runtime enforcement

<!-- Which product contract the change runs under and which runtime gate
(pilot_policy capability, preflight code) enforces the boundary — or why no
contract surface is affected (Rule 5). -->

### Evidence supporting the change

<!-- The concrete evidence behind any material refactor: failing test, defect
ID, pilot observation, measurement. Link it (Rule 6). -->

### Allowlist or ADR

<!-- Either "None — <specific explanation>" or the ADR reference covering any
allowlist expansion, new writer, dependency-direction exception, or product-
contract change. Allowlists may shrink freely; they may not grow without an
ADR (ratchet policy). -->

### End-to-end trace

<!-- How the affected money path remains traceable from source evidence to
financial outcome (event → model → JE → report), or a statement that no
runtime path changed. -->

## Verification

<!-- Commands run and their results: focused tests, full suite, lint/type
checks, migrations check, frontend build — whatever this change warrants. -->

## Architecture attestations

- [ ] ARCH: I identified the canonical financial fact or explained why none is affected
- [ ] ARCH: I did not create an undocumented second writer or source of truth
- [ ] ARCH: I preserved provider-to-core dependency direction or linked an ADR
- [ ] ARCH: I used the canonical invariant rather than duplicating it
- [ ] ARCH: I classified projections and orchestration correctly
- [ ] ARCH: I identified the supported product profile and runtime gate
- [ ] ARCH: I provided real evidence for any material refactor
- [ ] ARCH: I linked an ADR for every allowlist expansion or rule exception
- [ ] ARCH: I preserved the trace from source to financial outcome to evidence

## Risk and rollback

<!-- What could go wrong in production and how this change is reverted
(revert-commit, feature gate, migration rollback). -->

## Out of scope

<!-- What this PR deliberately does not do, so reviewers don't hunt for it. -->
