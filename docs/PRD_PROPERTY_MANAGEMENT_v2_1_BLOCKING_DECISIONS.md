# Nxentra Property Module PRD v2.1 - Blocking Decisions Checklist

**Date:** March 6, 2026  
**Scope:** Must be resolved before Sprint 1 implementation starts  
**Source PRD:** `docs/PRD_PROPERTY_MANAGEMENT_v2.md`

---

## How to Use

1. Each item below must have one selected option.
2. Record owner and target date.
3. Implementation cannot start until all items are `APPROVED`.
4. Any deferred item must include explicit mitigation and deadline.

Status values:
- `APPROVED`
- `DEFERRED`
- `BLOCKED`

---

## D1. Unallocated Cash Accounting Policy

**Problem:** `rent.payment_received` and `rent.payment_allocated` are separated. Accounting behavior for unallocated receipts is not finalized.

Options:
1. **Force full allocation at receipt time (Recommended for Phase 1)**
2. Allow partial/unallocated receipts and post to `unapplied_cash_account`
3. Record receipt operationally only and post accounting only on allocation

Selected option: `________________`  
Status: `________________`  
Owner: `________________`  
Target date: `________________`

Acceptance criteria:
1. PRD Section 10 updated with exact posting sequence.
2. API behavior documented for partial allocation attempts.
3. Tests added for unallocated/partially allocated edge cases.

---

## D2. Deterministic Idempotency Keys

**Problem:** Batch commands (`post_rent_dues`, `detect_overdue`) can duplicate effects without strict key design.

Options:
1. **Deterministic per schedule line + transition date keys (Recommended)**
2. Batch-run-level keys only
3. No explicit key policy (not acceptable)

Selected option: `________________`  
Status: `________________`  
Owner: `________________`  
Target date: `________________`

Acceptance criteria:
1. Key format documented for every write command.
2. Duplicate retry tests prove no double posting.
3. Projection replay leaves balances unchanged.

---

## D3. Projection Recursion / Double-Write Guard

**Problem:** Property accounting projection creating journal entries can recursively trigger accounting-related projections/events.

Options:
1. **Create JE through command path with explicit source marker + skip rules (Recommended)**
2. Direct model writes from projection (not acceptable)
3. Separate async handoff queue without skip semantics

Selected option: `________________`  
Status: `________________`  
Owner: `________________`  
Target date: `________________`

Acceptance criteria:
1. Recursion prevention mechanism documented.
2. Integration test proves single JE per triggering event.
3. Replay/incremental equivalence still passes.

---

## D4. Rent Schedule Generation Semantics

**Problem:** Ambiguity in month-end behavior and period boundary rules will create report and billing disputes.

Options:
1. **Fixed algorithm with explicit month-end carry + inclusive/exclusive boundaries (Recommended)**
2. Manual override-heavy behavior
3. Per-lease custom algorithm

Selected option: `________________`  
Status: `________________`  
Owner: `________________`  
Target date: `________________`

Acceptance criteria:
1. Rules documented for:
   - month-end starts (e.g., Jan 31)
   - leap year handling
   - start/end date inclusivity
   - due date resolution for `specific_day`
2. Golden test cases added.
3. UAT sample outputs approved by Product + QA.

---

## D5. Payment Void Edge Cases

**Problem:** Voiding after partial allocation or cross-period allocation can break accounting truth if not deterministic.

Options:
1. **Full reversal using original allocation map + reversing JE in same effective period policy (Recommended)**
2. Disallow void after any allocation
3. Allow void with manual correction entry flow

Selected option: `________________`  
Status: `________________`  
Owner: `________________`  
Target date: `________________`

Acceptance criteria:
1. Void behavior defined for:
   - partially allocated payments
   - multi-line allocations
   - allocations spanning fiscal boundaries
2. Idempotent void retry test added.
3. Trial balance remains consistent after void/replay.

---

## D6. Lease Overlap Concurrency Strategy

**Problem:** Command-layer overlap checks can race under concurrent activation.

Options:
1. **Transaction + row locking strategy in activation command (Recommended for Phase 1)**
2. Postgres exclusion constraint with date range field
3. Best-effort command check only (not acceptable)

Selected option: `________________`  
Status: `________________`  
Owner: `________________`  
Target date: `________________`

Acceptance criteria:
1. Concurrency test proves only one overlapping lease can activate.
2. Clear user-facing error for rejected concurrent activation.
3. No deadlock regression under parallel activation attempts.

---

## D7. Dimension Cardinality and Lifecycle

**Problem:** Auto-created property/unit dimension values can explode in count and become hard to maintain.

Options:
1. **Property and Unit dimensions with controlled code format + soft deactivation (Recommended)**
2. Property-only dimensions
3. Manual dimension management only

Selected option: `________________`  
Status: `________________`  
Owner: `________________`  
Target date: `________________`

Acceptance criteria:
1. Naming convention finalized (e.g., `PROP:{property_code}`, `UNIT:{property_code}:{unit_code}`).
2. Archival/deactivation behavior documented.
3. Reports still resolve historical dimension references after deactivation.

---

## D8. Tenant Timezone Rule for Daily Tasks

**Problem:** Due/overdue and expiry jobs require timezone-consistent daily boundaries.

Options:
1. **Per-company timezone evaluated in scheduled jobs (Recommended)**
2. Global UTC day boundary only
3. App server local timezone

Selected option: `________________`  
Status: `________________`  
Owner: `________________`  
Target date: `________________`

Acceptance criteria:
1. Timezone source field is defined and documented.
2. Job behavior around midnight has automated tests.
3. UAT confirms expected due/overdue date behavior.

---

## D9. Whole-Property vs Unit Lease Conflict Rule

**Problem:** The PRD allows `unit_id = null` for whole-property lease, but coexistence rules with unit-level leases are unspecified.

Options:
1. **Mutual exclusivity enforced (Recommended)**
2. Allow coexistence with proportional logic
3. Manual warning only

Selected option: `________________`  
Status: `________________`  
Owner: `________________`  
Target date: `________________`

Acceptance criteria:
1. Validation rule documented in lease commands.
2. Conflict scenarios covered in tests.
3. Error messaging is explicit in API/UI.

---

## D10. Migration and Backfill Plan for Existing Companies

**Problem:** Existing Nxentra tenants need a safe path to enable the module without data inconsistency.

Options:
1. **Feature flag + bootstrap command + account mapping prerequisite (Recommended)**
2. Auto-enable for all tenants after deploy
3. Manual DB changes per tenant

Selected option: `________________`  
Status: `________________`  
Owner: `________________`  
Target date: `________________`

Acceptance criteria:
1. Bootstrap command documented and tested.
2. Backfill creates required defaults without financial side effects.
3. Rollback plan exists for partial bootstrap failures.

---

## Sign-Off

Product: `________________`  
Tech Lead: `________________`  
Backend Lead: `________________`  
Frontend Lead: `________________`  
QA Lead: `________________`  
Date: `________________`

**Implementation Gate:** Sprint 1 starts only when all D1-D10 are `APPROVED` or have written mitigation accepted by Product + Tech Lead.
