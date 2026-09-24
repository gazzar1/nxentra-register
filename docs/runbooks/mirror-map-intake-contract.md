# Mirror map — intake-contract rules in `docs/runbooks/fresh-isolated-pilot.md`

Maintained beside the runbook it indexes (from the revision that merged PR #153). It does not amend the runbook; it is the checklist a runbook change must walk. Section labels are the runbook's own; line numbers are omitted on purpose.

## How to use it

Before changing any rule below, open every mirror listed for it and change all of them in one commit, or
state in the PR why a mirror deliberately differs. The owner statement is the one other sections must
defer to; a mirror may restate for the operator's convenience but must not add or drop a condition.
Section labels are the runbook's own (§ letter › step). Line numbers are omitted on purpose; they drift.

## Rules

| Rule | Owner (single statement) | Mirrors that restate it | Notes |
|---|---|---|---|
| `INITIAL_TASKS_QUEUED = 1 + K` (one initial task per successful embedded `token-exchange/` call after J0; first in queue order = release execution) | §I definition block (after I12) + §I12 queue baseline | I13 (pre-declaration, ids in queue order, re-sign on a new launch); I14 (require exactly 1 + K consumed; K re-executions rule); I15 (replacement task is not an initial task); §K (all 1 + K results in four rows); §O ("any unexplained initial task"); §Q 10 (`PRE_GO_INITIAL_TASK_IDS`), §Q 11 (GO record declares 1 + K), §Q 12, §Q 15, §Q 17 | 7 sections. The K re-executions rule (per-window reconciliation) lives in I14; §Q 12 mirrors it in merchant form. |
| `TASK_RECEIVED_AT` = TaskResult `date_started` (django-celery-results ≥ 2.6.0, migration 0012) | §I definition block | §E3 (log-level / dependency mirror); §G1e (version + migration proof, STOP); I13 (ordered against it at I14); I14 (capture; export before §I16); I15 (`REPLACEMENT_EXECUTION_STARTED_AT` twin); §K (`INITIAL_SYNC_STARTED_AT`/`TASK_RECEIVED_AT` row); §O (ordering abort); §Q 12 (post-execution controls) | 9 sections. The dependency floor is pinned by `tests/test_intake_contract_dependencies.py`. |
| Seven-day rule (`created_at_max − created_at_min` = exactly 7 days) — binds `initial_store_sync` executions only; explicit-window executions reconcile to their declared window | §I definition block ("ONE rule — the same rule applied to each execution's own values") | I4 (hold rationale); I13 (windows are future values); I14 (STOP list, scoped); I15 (replacement window is NOT seven days); §O (seven-day abort, scoped to the release execution's A52 line); §Q 12, §Q 17 | 7 sections. Round 5–6 scoped every mirror; keep the scoping phrase identical. |
| `AUTHORIZED_PARENT_ORDER_SET` = union over the 1 + K executions of each execution's own A_k ∪ B_k (per-execution windows) | §I definition block | I14 (bullet: reconcile from ids in per-execution rows; record fields `CANDIDATE_ORDER_COUNT_A/_B` per execution + union; STOP "counts across each execution's A_k, B_k and their union"); I15 (replacement set is outside it by construction — bound = LAST execution's `INITIAL_SYNC_STARTED_AT`); §K (set A / set B per execution rows; union row); §O (closure re-execution outside the set); §Q 12 (record A_k/B_k per execution; reconcile the union), §Q 17 | 6 sections. Round 12 made every consumer per-execution; a new consumer must be per-execution too. |
| Ordering rule: sign-off `< TASK_RECEIVED_AT ≤ INITIAL_SYNC_STARTED_AT` (release execution); each K re-execution: same rule against its own `date_started`; replacement: `I15_REPLACEMENT_SIGNOFF_TIMESTAMP < REPLACEMENT_EXECUTION_STARTED_AT` | §I definition block | I14 (release + K form); I15 (replacement form + STOP); §O (both aborts); §Q 12 (`GO_TIMESTAMP` form) | 5 sections. |
| Replacement path terms (`REPLACEMENT_ORDER_SET` — no cancelled order, every member paid at creation; `REPLACEMENT_WINDOW` = host-clock [T0, T1] in `YYYY-MM-DDTHH:MM:SS+00:00`; `REPLACEMENT_EXECUTION_STARTED_AT`) | §I definition block | I15 (addendum contents, evidence contract, STOP line); §K (complete-results row); §O (replacement bullet) | 4 sections. §Q has NO replacement path by design (merchant lapse = recorded gap). The §I15 STOP line and the §O bullet must stay identical. |
| Evidence-bearing form of a re-execution = worker task `shopify.sync_store_orders` (full 12-field orders-leg result in its TaskResult row; enqueue via `manage.py shell -c` with the app's own `.delay(...)`; read form after the A52 done line); CLI `resync_shopify_orders` prints four counters and is NOT evidence | §I closure rule (A-leg re-execution paragraph, after I12) | §G1d (A52 timestamps copied verbatim); §I5 (controlled first synthetic proof grant); I14 (closure re-executions recorded with TaskResult row); I15 (replacement uses the same form); §K (complete-results row: worker-task form) ; §Q 17 (merchant use only after a recorded synthetic proof) | 6 sections. Any new re-execution kind must name this form. |
| TaskResult rows expire 24 h after beat starts (`celery.backend_cleanup`, 04:00 UTC, DatabaseScheduler, no `result_expires` override) → export every evidence row (all 1 + K, closure, replacement) into `preflight/` BEFORE §I16 / §Q 15 | I14 (export sentence) | I15 (cross-reference); I16 (precondition); §K (source column = the exports); §O (beat bullet); §P (directory map); §Q 15 | 7 sections. |
| Worker form `celery … worker -l INFO --concurrency 1` (founder decision D12) | §G1d worker row | §G1e (proof of the running value; STOP) | 2 sections; also the reason the runbook can identify an execution's additions by local `created_at` (I14 / §Q 12). |
| `INTAKE_CONTRACT_VERSION` = the §B runbook revision SHA; definition block textually identical between rehearsed and merchant revisions | §I definition block | §B1; I13 (sign-off names it); I14 (reconcile); I15 (addendum names it); §K; §O; §Q 3, §Q 11, §Q 17 | 8 sections. Any textual change to the block = new contract version = new I13/I14 before a merchant GO. |
| Closure re-execution that books outside `AUTHORIZED_PARENT_ORDER_SET` = STOP; recorded as an explained second execution (worker-task form) | §I closure rule | I14 (STOP list; "any other re-execution used for §I closure"); I15 (replacement is NOT a closure re-execution); §K (rows name closure re-executions); §O; §Q 12, §Q 17 | 6 sections. |
| I5 rule: an initial-sync enqueue-recovery command must be proven before it may appear (none does); the re-execution task has a CONTROLLED FIRST SYNTHETIC PROOF; merchant use only after that record exists | §I5 | §I closure rule (restatement + exception); §Q 17 (merchant gating) | 3 sections. |
| Retry clock: Shopify retries a held delivery 8 times over ~4 h; unblock within ~4 h of the first held synthetic order; lapsed = recorded gap (synthetic path may restart at order creation under the block, with the replacement execution) | I15 (opening paragraph) | §F3 (retry facts); I13 (planned unblock time); §Q 14 (merchant mirror; no replacement) | 4 sections. |
| A52 line transcription: copy the two window timestamps verbatim (never the shop domain / store id; never re-render) | §I definition block (transcription rule) | §E3; §G1d; I14; §K; §O (domain copied = abort); §Q 12 | 6 sections. |

## Sections that concentrate mirrors (read them together on every change)

§I definition block (after I12) → I13 → I14 → I15 → §K → §O → §Q 11/12/14/15/17. A rule that touches
the merchant path appears in §Q at least once; a rule that touches evidence appears in §K and §O.

## Proposed post-G1 restructure (Phase 3, closure PR — not now)

Split the runbook into a synthetic-rehearsal document and a merchant-cutover document; state each rule once
in a "Rules" section and have every step cite it by identifier; this map becomes the index.
