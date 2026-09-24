# Mirror map — projection drain and consumption rules (code side)

Maintained with the code it indexes. Owner module for the drain: `backend/projections/runtime.py` (PR "one projection runtime"). This map lists where each rule is stated or enforced so a change to a rule touches every mirror in one commit.

## How to use it

Before changing any rule below, open its owner and every place listed under "also stated / enforced";
change them together or say in the PR why one deliberately differs. Ratchets are the tests that fail
when a mirror drifts; they are listed so a change knows which test it will trip.

| Rule | Owner (code) | Also stated / enforced | Notes |
|---|---|---|---|
| A projection never observes its own in-flight pass (nested `process_pending` for the same (projection, company) returns 0) | `projections/base.py` — `_PASSES_IN_FLIGHT` ContextVar, `_pass_in_flight`, the early return in `process_pending` | Docstring of `process_pending`; `tests/test_projection_pass_reentrancy.py`; `tests/test_i14_same_pass_order_refund.py`; arch ratchet `test_process_pending_has_one_in_flight_guard_ahead_of_the_bookmark_and_dispatch`; runbook §I (one descriptive paragraph); NEXT_TASKS A175 | Context-local only; no cross-worker lease (F-K, Phase 3 ADR). |
| Exactly one registry-walk drain in the codebase; every synchronous command drain is `command_drain` | `projections/runtime.py` — `drain_company`, `command_drain` | The five alias imports (`accounting/commands.py`, `accounts/commands.py`, `edim/commands.py`, `properties/commands.py`, `properties/tasks.py`); `events/emitter.py` fallback; the two Shopify seed commands; Rule 20 (`test_process_pending_call_sites_are_frozen_per_file`, `test_no_registry_walk_drain_outside_the_allowlisted_files`, `test_projection_runtime_is_a_walker_not_a_transaction_or_rls_owner`); `tests/test_projection_drain_characterisation.py` | Single-projection drains that are NOT walks stay outside: reconciliation (fresh instance, ungated), admin process endpoint, payments backfill, tenant replay — each pinned to one call. |
| The command-layer drain is gated on `settings.PROJECTIONS_SYNC` read at call time; production asserts the flag True at boot | `projections/runtime.py` — `command_drain` | Boot assertion (A162) in settings/app config; README "Projections" line; runbook §G/§I posture; characterisation T3 | The emitter fallback and the reconciliation drain are UNGATED by design (T7, T10). |
| Drain order = live registry registration order; never cached or sorted | `projections/base.py` — `ProjectionRegistry.all()` (singleton via `__new__`) | `drain_company` (walks `all()` each call); characterisation T1/T11 | The Celery task's include list is caller-ordered (T9); do not "fix" either into the other. |
| A drain owns no transaction, savepoint, on_commit, RLS or tenant context; the caller's context is the contract | `projections/runtime.py` docstring + Rule 20 walker check | Characterisation T5; command decorators (`@transaction.atomic`, `requires_capability`) at the call sites; `_run_reconciliation_projection_sync` docstring | Per-event savepoints live inside `process_pending`. |
| Handler errors are fail-soft inside `process_pending` (mark_error, stop, evidence); framework faults propagate out of the drain | `projections/base.py` — `process_pending` error branches; `ProjectionTerminalSkip` consume door | Characterisation T4; arch ratchet `test_terminal_skip_has_a_single_consume_door_repo_wide`; A5-PR1b tests | Rule 17 pins evidence/consume atomicity. |
| No production module dispatches a projection handler directly | `projections/base.py` — `process_pending` is the only caller of `handle()` | Arch ratchet `test_no_production_module_dispatches_a_projection_handler_directly`; Rule 20 walker check (`handle` identifier forbidden in the runtime) | |
| Rebuild is the one canonical destructive path (A4 `PROJECTION_REBUILD`-gated) | `projections/base.py` — `rebuild()` | `projections/tasks.py` rebuild tasks; `rebuild_projection` management command; A154 | Not a drain; Rule 20 pins `base.py` to one `process_pending` call (the drain-to-zero loop inside `rebuild`). |
| Posted journals go through the canonical emit boundary; emit restores RLS context (PR #145) | `accounting/posted_journal_boundary.py` — `emit_posted_journal`; `accounting/journal_invariant.py` — `prepare_posted_journal_for_emit`; `accounts/rls.py` — `rls_scope` (snapshots and restores the RLS context: the one restore rule, entered by `events/emitter.py` around every emit) | Architecture constitution rule 5; arch ratchets on posted emitters | `events/emitter.py` is the generic event door and the caller of the restore rule, not the owner of the posted-journal boundary; its projection fallback is a separate concern from emission. |

## Deferred decisions that will edit these rows (Phase 3, each with an ADR)

- A175: outermost-only drain / projection tiering (changes rule 2's owner semantics).
- F-K: cross-worker projection lease (adds a rule to row 1).
- Routing the Celery task and the operator CLI through `drain_company` (changes rows 2 and 4).
