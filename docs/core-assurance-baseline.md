# Core Assurance Baseline

Established: 2026-03-06

This document defines the current state of Nxentra's accounting core assurance.
It is the reference point before any refactor, feature addition, or schema change.

## Invariant Test Suites

### Truth Invariants (`tests/test_truth_invariants.py`) — 11 tests

Prove that the accounting math and event replay logic are correct.

| Invariant | Tests | What it proves |
|---|---|---|
| ReplayEqualsIncremental | 1 | Rebuild from event store = incremental processing |
| DoubleApplyStability | 1 | Running `process_pending` twice is a no-op |
| ReversalFullyOffsets | 1 | Entry + exact reversal = zero balance |
| MultiLineSameAccount | 2 | Multiple lines to same account in one event are all applied |
| ExternalPayloadEquivalence | 2 | LEPH external storage produces identical results to inline |
| VerifyAllBalancesConsistency | 2 | `verify_all_balances()` matches projected state |
| TrialBalanceAlwaysBalanced | 2 | Trial balance is balanced after any operation sequence |

### Control Invariants (`tests/test_control_invariants.py`) — 11 tests

Prove that structural accounting controls enforce correctness at system boundaries.

| Invariant | Tests | What it proves |
|---|---|---|
| ClosedPeriodBlocksPosting | 3 | `can_post_to_period` rejects closed period/fiscal year |
| ClosedPeriodProjectionFreeze | 1 | Closing a period does not corrupt existing balances |
| FiscalYearCloseReopenPreservesTruth | 1 | Close + reopen cycle is lossless |
| SubledgerTieOut | 3 | AR/AP control account = sum of customer/vendor balances |
| MixedOperationReplayConsistency | 2 | Rebuild after mixed ops preserves all state |
| MixedPayloadCloseReopenReplay | 1 | Inline + external + reversals + close/reopen + rebuild all compatible |

### Runtime Invariants (`tests/test_runtime_invariants.py`) — 9 tests

Prove operational resilience under conditions that occur in production.

| Invariant | Tests | What it proves |
|---|---|---|
| ConcurrentPosting | 1* | Parallel writes produce correct totals, no gaps, balanced TB |
| ProjectionCrashRecovery | 2 | Partial process + restart = correct final state |
| OutOfOrderReplay | 1 | Rebuild in sequence order produces correct state |
| ProjectionLagReporting | 2 | Lag tracks unprocessed events accurately; pause works |
| IdempotentEventEmission | 3 | Duplicate idempotency keys don't create duplicates or corrupt state |

*Skipped on SQLite. Requires Postgres (CI).

### LEPH Tests — 7 tests

| Suite | Tests | What it proves |
|---|---|---|
| `test_leph_e2e_projection.py` | 2 | External payload flows through full projection pipeline |
| `test_leph_safety.py` | 5 | External storage roundtrip, hash integrity, deduplication |

**Total: 38 tests**

## What These Tests Prove

- Replay from event store always equals incremental processing
- Projections are idempotent (double-run is a no-op)
- Reversals net to zero
- Multiple lines to the same account in one event are all applied
- LEPH external storage does not change results
- Trial balance is always balanced
- Closed periods block posting
- Fiscal year close/reopen is lossless
- AR/AP subledger totals tie to control accounts
- Crash recovery produces correct final state
- Projection lag is reported accurately
- Duplicate event emission is rejected
- `company_sequence` is gap-free

## What These Tests Do NOT Prove

1. **Real concurrency under Postgres** — The concurrent posting test is skipped on SQLite. It must run in CI with Postgres to be meaningful.
2. **Network-level idempotency** — Tests cover event-layer dedup, not HTTP retries or API gateway dedup.
3. **Schema migration safety** — No test replays historical events after a migration. Adding/removing fields on event payloads could break projections silently.
4. **Multi-tenant isolation** — RLS is bypassed in tests. No test verifies that company A's events never leak into company B's projections.
5. **Permission edge cases** — Tests use owner/admin fixtures. No test verifies that a user with partial permissions cannot corrupt accounting state.
6. **UI-induced bad state** — Tests emit events directly. No test verifies that the frontend cannot construct an invalid journal entry that passes API validation.
7. **Projection ordering dependencies** — AccountBalance and SubledgerBalance are processed independently. No test verifies they remain consistent if one fails while the other succeeds.
8. **Clock skew / timezone edge cases** — All tests use server time. No test verifies behavior when `posted_at` and fiscal period boundaries interact across timezones.
9. **Large-scale performance** — LEPH tests use 300 accounts. Production could have thousands of lines per event. No load/stress tests exist.
10. **Backup/restore integrity** — No test verifies that a database restore + projection rebuild produces identical state.

## Architecture References

- [Projection Idempotency Architecture](projection-idempotency.md) — Why `_apply_line()` must never skip lines
- `projections/base.py` — `BaseProjection.process_pending()` with `ProjectionAppliedEvent` dedup
- `events/models.py` — `EventBookmark` with `company_sequence`-based cursor
- `accounting/policies.py` — `can_post_to_period()`, `validate_subledger_tieout()`

## Projection Files Covered

| Projection | File | Guard bug fixed |
|---|---|---|
| AccountBalanceProjection | `projections/account_balance.py` | Yes (March 2026) |
| SubledgerBalanceProjection | `projections/subledger_balance.py` | Yes (March 2026) |
| PeriodAccountBalanceProjection | `projections/period_balance.py` | Yes (March 2026) |
| InventoryBalanceProjection | `projections/inventory_balance.py` | Yes (March 2026) |

## CI Requirements

These test suites MUST be mandatory CI gates. A failure in any invariant test blocks merge.

Required CI configuration:
- Run all invariant suites on every PR
- Run `test_runtime_invariants.py` with `TEST_DATABASE_URL` set to Postgres
- Run LEPH tests (`test_leph_e2e_projection.py`, `test_leph_safety.py`) on every PR
- Fail the pipeline if any invariant test fails

## Phase 1 Completion Criteria

Phase 1 ("Accounting Core") is complete when:

- [ ] All 38 invariant tests green on Postgres in CI
- [ ] CI pipeline configured with mandatory gates
- [ ] This baseline document reviewed and accepted
- [ ] Known limitations (section above) acknowledged and prioritized
- [ ] No new feature work starts before this is locked
