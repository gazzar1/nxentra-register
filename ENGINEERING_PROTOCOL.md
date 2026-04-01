# Nxentra Engineering Protocol

This document defines the minimum engineering rules for changing Nxentra safely.

Nxentra is not a simple CRUD app. It contains:

- event-sourced financial workflows
- CQRS projections
- tenant routing and RLS isolation
- accounting invariants
- external connector ingestion
- operational recovery paths

Because of that, "quick fixes" can create silent data corruption, projection drift, tenant leakage, or unreproducible month-end results.

This protocol exists to prevent that.

## 1. Non-Negotiable Invariants

These rules are mandatory.

### 1.1 Finance Data Must Be Event-First

- Any finance-impacting state change must flow through the command layer and emit events.
- Journal entries, allocations, reconciliations, subledger changes, and period/year close actions must never be "fixed" by direct model edits.
- If a change affects balances, reports, auditability, replay, or close results, it must be represented in the event stream.

### 1.2 No Direct Production Fixes Through Projection Writes

- `projection_writes_allowed()` exists only for projection processing, not for production data corrections.
- Never use projection writes to patch accounting state, balances, or reporting discrepancies.
- If projection state is wrong, correct the underlying event/command path and then rebuild/replay projections as needed.

### 1.3 Tenant Isolation Must Remain Explicit

- Any code touching tenant data must state how tenant context is resolved.
- No endpoint, worker, command, or task may access tenant models without a valid company context unless it is explicitly documented as system-only.
- No "temporary bypass" of routing or RLS is acceptable without a documented incident path and explicit approval.

### 1.4 Read Models Are Derived, Not Canonical

- Projections, balances, aging tables, and summary tables are derived state.
- If canonical data and projections disagree, fix canonical flow first, then rebuild projections.
- Never treat a read model as the source of truth.

### 1.5 Auditability Beats Convenience

- Every correction must leave an explainable audit trail.
- If a human cannot reconstruct what changed and why, the fix is not acceptable.
- If a fix cannot be replayed or explained at month-end, it is incomplete.

## 2. Allowed Change Patterns

These are the preferred ways to modify the system.

### 2.1 Business Logic Changes

- Update the command layer first.
- Emit or evolve the event shape deliberately.
- Update projections only for derived behavior.
- Add or update tests for command, event emission, and projection outcome.

### 2.2 Bug Fixes in Financial Flows

Every fix must answer:

- What invariant was broken?
- What canonical source was wrong?
- Does this require a new event, a correction event, or a replay?
- What reports or balances could have been affected?
- What test now prevents recurrence?

### 2.3 Schema Changes

- State whether the change affects canonical models, projections, or both.
- State whether replay/backfill is required.
- State whether existing events remain backward-compatible.
- Prefer additive changes over destructive ones.

### 2.4 Connector Changes

- Treat ingestion as hostile input.
- Preserve idempotency.
- Document ordering assumptions, retry behavior, and failure visibility.
- Any skipped or partial posting must surface visibly to operators.

## 3. Forbidden Shortcuts

These are not allowed unless there is a production incident and the action is documented, approved, and followed by remediation.

- Direct DB edits for finance data
- `manage.py shell` fixes for accounting state
- direct `.save()` patches that bypass command/event flow
- using `projection_writes_allowed()` for data corrections
- silent backfills without audit notes
- changing event payload shape without considering replay compatibility
- deploying projection code that cannot handle old event shapes
- tenant-scoped queries executed without explicit tenant context
- shipping a bug fix without adding the regression test when a test is feasible

## 4. Required Tests By Change Type

Minimum test expectations:

### 4.1 Accounting / Ledger Changes

- command-level test
- event emission test
- projection/balance test
- reversal/correction path test if applicable

### 4.2 Tenant / Auth / Permissions Changes

- tenant isolation test
- no-tenant allowlist behavior test if relevant
- permission boundary test

### 4.3 Connector / Ingestion Changes

- idempotency test
- duplicate delivery test
- malformed payload or partial payload test
- operator-visible failure test

### 4.4 Projection Changes

- idempotency test
- replay/rebuild test where feasible
- drift prevention test if the change fixes a prior mismatch

### 4.5 Report / Query Changes

- correctness test against a known dataset
- tenant boundary test so aggregations do not leak cross-company data
- period boundary test so the wrong period does not bleed into results

## 5. Change Review Template

Every non-trivial change should be described in this format:

### Summary

- what changed
- why it changed

### Invariants

- which invariant is protected
- whether any invariant is newly introduced

### Impacted Areas

- commands
- events
- projections
- tenant routing / RLS
- reports / balances
- connectors

### Risk

- highest-risk failure mode
- whether replay/backfill is required
- whether operator workflow changes

### Tests

- tests added
- tests updated
- gaps intentionally left open

## 6. Release Checklist

Before merging finance-impacting work:

- canonical flow is correct
- event emission path is explicit
- projections reflect derived behavior only
- tests cover the regression
- tenant implications were reviewed
- operator-visible failure paths remain visible
- docs/runbook updated if workflow changed

Before deploying finance-impacting work:

- migrations reviewed
- replay/rebuild need assessed
- existing events remain backward-compatible with new projection code
- month-end/reporting impact assessed
- rollback path stated
- no manual shell steps required for normal operation

## 7. Incident Protocol

If data is already wrong in a live environment:

1. Stop making ad hoc edits.
2. Identify whether canonical data or projection data is wrong.
3. Document the affected tenant, period, reports, and user-visible symptoms.
4. If incorrect user-visible financial data was exposed or relied on, notify the affected tenant with what was wrong, when it was wrong, what was corrected, and any required follow-up.
5. Prefer a corrective command/event path over direct mutation.
6. If emergency intervention is unavoidable, record:
   - exact action taken
   - why normal path was insufficient
   - affected models/tables
   - required replay/rebuild steps
   - reconciliation and tie-out steps after the intervention
7. Add a regression test before closing the incident.

## 8. Vibe Coding Rules For Nxentra

If using AI-assisted or vibe coding, these extra rules apply:

- Never ask for "just fix it" on finance code without naming the invariant.
- Never accept a patch that changes commands, events, or projections without reading the full path.
- Never merge code that touches commands, events, projections, or tenant-scoped queries without tracing the full path from request entry to canonical write to read-model effect.
- Always ask: "What is the source of truth here?"
- Always ask: "Could this create silent misstatement or tenant leakage?"
- If the answer is "maybe", stop and inspect before editing.

## 9. Default Decision Rule

When speed and correctness conflict:

- choose correctness for anything affecting money, tenant isolation, audit trails, reconciliation, or period close
- choose speed only for isolated UI polish or non-canonical read-only behavior

Nxentra can tolerate slower feature delivery.
It cannot tolerate silent financial corruption.
