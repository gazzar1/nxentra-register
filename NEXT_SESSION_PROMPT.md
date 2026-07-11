# Next session — paste this prompt (written 2026-07-11; previous 2026-05-03 version preserved in git history)

> **Expiry rule:** if any P0 task below is already marked done in NEXT_TASKS.md or TASKS_DONE.md, skip it. If more than ~3 weeks have passed, re-verify every claim in Step 0 before writing code. The previous version of this file went 2 months stale and became actively dangerous — do not let this one do the same.

---

## Mission

Implement the **Safe-supervised-pilot exit gate** — the P0 section of [NEXT_TASKS.md](NEXT_TASKS.md) — so Nxentra is safe to put real merchant money through under supervision. This is the "before the first three merchants" engineering batch from the 2026-07-11 dual audit ([NXENTRA_AUDIT_2026_07_11.md](NXENTRA_AUDIT_2026_07_11.md) + [NXENTRA_CODEX_INDEPENDENT_AUDIT_2026_07_11.md](NXENTRA_CODEX_INDEPENDENT_AUDIT_2026_07_11.md)). Both audits independently confirmed every defect below; the disputed ones were re-verified line-by-line in the session that wrote this prompt.

**Scope discipline — do NOT:** touch P1/P3/deferred items (except where a P0 task explicitly absorbs one), implement billing (M1), refactor god-files, add features, archive clinic/properties, or start any integration. Every one of those is deliberately sequenced after this gate. If you find a new bug outside the gate, file it in NEXT_TASKS.md P1 and move on.

**Working rules:** finance work is event-first per [ENGINEERING_PROTOCOL.md](ENGINEERING_PROTOCOL.md) and [docs/finance_event_first_policy.md](docs/finance_event_first_policy.md) (the docs/ copy — the root copy is superseded). Every fix ships with a RED-proven regression test (write the failing test first, prove it fails for the audited reason, then fix). One branch + PR per task (or per small pair). After a squash-merge: `git pull --ff-only` — never `reset --hard` over uncommitted work. Frontend changes need `npm run build` on the droplet, not just a pm2 restart.

---

## Step 0 — Pre-flight (~30 min, do not skip)

1. `git status` — the tree may still carry uncommitted 2026-07-11 audit/roadmap files (`NEXT_TASKS.md`, `TASKS_DONE.md`, `SESSION_LOG.md`, `NEXT_SESSION_PROMPT.md`, `docs/archive/`, two audit reports). Review and **commit these first** on a `docs/audit-2026-07-11` branch so code work starts from a clean tree. Remember untracked files don't appear in `git diff` — use `git status`.
2. Baseline: run the backend suite the way CI does (`pytest tests/ accounting/tests/ events/tests/ accounts/tests/ --ignore=tests/e2e/` on SQLite) and note the result. Don't start on a red baseline.
3. Spot-verify 3 claims still hold before believing this prompt: (a) `postable_kinds` in `backend/accounting/commands.py` (~line 1110) still excludes REVERSAL; (b) `rebuild_projection.py:389-438` still deletes markers + bare-`handle()` replays; (c) `backend/backups/views.py` still has only `IsAuthenticated`. If any is already fixed, skip that task and tick its gate checkbox.

---

## Work order

Recommended sequence (dependencies + smallest-win-first). Full canonical task text lives in NEXT_TASKS.md P0 — read each entry there before starting it. Summary and traps below.

### 1. A164 — CI gaps (~0.5d) — FIRST, so every later regression test actually gates
Add `backend/reconciliation/tests/` to a CI pytest job (`.github/workflows/ci.yml:40` omits it — 26 collected tests currently run nowhere). Run `tests/test_aggregate_sequencing.py`'s Postgres-only concurrency class in the `backend-invariants` job. Optionally drop `-x` from the two pytest jobs.

### 2. A156 — `is_postable=True` FieldError sweep (~0.5d)
`is_postable` is a `@property` (`accounting/models.py:750-753`); `filter(is_postable=True)` raises FieldError at 7 sites: `accounting/commands.py:199`, `accounting/tasks.py:183,191`, `accounting/views.py:2650` (swallowed at :2577 — core mapping auto-init has never worked), `platform_connectors/je_builder.py:110`, `projections/views.py:5554,5563`. Replace with the correct pattern from `shopify_connector/projections.py:222-227` (`is_header=False, status=ACTIVE`). One test per fallback branch (FX-rounding-without-mapping, revaluation-without-FX-GAIN/LOSS, auto-init actually creating mappings).

### 3. A154 — one convergent drain-to-zero rebuild (~1.5-2d, CRITICAL)
Three failures: `rebuild_projection` cmd (`:389-438`) and `AdminProjectionRebuildView` (`projections/views.py:3036-3078`) delete `ProjectionAppliedEvent` + bookmark then bare-`handle()` replay → next `process_pending` doubles accumulators; `BaseProjection.rebuild()` runs a single `process_pending(limit=1000)` batch → silently partial >1,000 events; tenant replay passes unsupported `using=` (`tenant/management/commands/replay_projections.py:144-151`).
**Fix:** make `BaseProjection.rebuild()` loop process_pending until drained; route the CLI command and admin view through it (admin view must dispatch to Celery or hard-cap synchronous size — it currently blocks the request for a full replay); fix or explicitly disable tenant replay. **Includes A115:** add `JournalEntryProjection._clear_projected_data` (JE/JournalLine delete under `projection_writes_allowed()`; known limitation — source-doc FKs are SET_NULL, do NOT expand into A110, just document it).
**Tests:** >1,000-event rebuild converges; rebuild-then-`process_pending` changes **nothing** (assert balances byte-identical); the mgmt command and admin endpoint both go through the safe path; tenant command no longer TypeErrors.

### 4. A155 — reversal + void family (~3-4d, CRITICAL — the biggest task)
Three verified failure modes:
1. `reverse_journal_entry` omits `customer_public_id`/`vendor_public_id` on reversal lines (`accounting/commands.py:1411-1431`) — subledger never reverses, tie-out breaks, year close blocks (`:2298-2300`).
2. `void_sales_invoice` (`sales/commands.py:1634-1660`), `void_purchase_bill`, `void_purchase_credit_note` (`purchases/commands.py:665,1883` — **verify same pattern first**) create a `kind=REVERSAL` DRAFT then call `post_journal_entry`, whose `postable_kinds` (`accounting/commands.py:1110-1117`) excludes REVERSAL → the void **always fails** after creating the orphan DRAFT + events.
3. `void_credit_note` does `reversal_je = reverse_result.data` then `.public_id` — but `reverse_journal_entry` returns a dict (`accounting/commands.py:1483-1489`) → AttributeError (`sales/commands.py:2062-2085`).
**Fix:** one canonical counterparty-preserving reversal primitive (decide: allow posting REVERSAL kind through the command with an internal flag, or have voids use `reverse_journal_entry` — pick one, use it everywhere); standardize its return shape; failed voids must not strand DRAFT reversals (raise inside the atomic scope rather than return-fail, or clean up explicitly); add an orphan-DRAFT-reversal detector to System Health.
**Acceptance tests (all currently absent):** AR reversal + AP reversal preserve GL/subledger tie-out; each of the four voids completes and nets its document to zero; injected mid-void failure leaves no orphan DRAFT and no status change; `check_close_readiness` passes after each operation; `void_credit_note` round-trip restores invoice `amount_paid`.

### 5. A157 — fail-loud sweep (~1-2d)
Silent `logger + return` branches that consume financial events forever: `payment_settlement_projection.py:193-209` (zero-gross + imbalance — caused the real A20/MAY01-A loss); `platform_connectors/projections.py:97-104,143-148,222-225,274-279,352-359` (Stripe/all-platform JE path — bit production once already). Convert to `ProjectionStateError`/`ProjectionTerminalSkip` per the F27 pattern in the same file (`:61-114`). Property/clinic projections same pattern, lower priority. Test each branch → ProjectionFailureLog row + event NOT marked applied (or terminal-skipped visibly).

### 6. A158 — Stripe payout double-post guard (~1d)
Canonical pull never stamps `StripePayout.journal_entry_id` (`stripe_connector/sync.py:240-259`); legacy /banking matcher then posts a second JE (`bank_connector/matching.py:337-383,468-594`). **Minimal fix now:** make the legacy matcher skip any payout that has a canonical settlement (or stamp `journal_entry_id` at pull time). Full /banking retirement is A166 — do not do it in this task unless the owner has decided. Add the cross-engine test (pull-synced payout + /banking auto-match → exactly one JE).

### 7. A176 — balance sheet current-year earnings + "as of" semantics (~1-2d)
`BalanceSheetView` (`projections/views.py:700-765,811-862`) never folds unclosed P&L into equity (CURRENT_YEAR_EARNINGS role exists at `accounting/models.py:307`, unused), and period mode filters from `period_from` instead of accumulating through `period_to` (`:599-662`). Tests: mid-year BS balances with open P&L activity; selected-period = cumulative; before/after year-end close consistency.

### 8. A177 — JE command idempotency uses caller request identity (~1-2d)
`create_journal_entry` (`accounting/commands.py:655,698-762`): fresh aggregate UUID + content hash omitting period/source/dimensions/counterparties → a true retry returns the old event then fails the fresh-UUID lookup (false failure); distinct legitimate entries can collide (silent suppression). Accept a caller request ID / stable aggregate ID; on true retry return the original aggregate; reject same-key/different-payload. Tests: exact retry succeeds returning the original; different dimensions/counterparties/source-docs never collide.

### 9. A180 — atomic, event-reconstructible difference resolution (~1-1.5d)
`resolve_difference()` (`reconciliation/commands.py:1639-1645,1798-1847`): no outer transaction — posts the adjustment JE via separately-atomic commands, then direct-writes provenance + resolution state after. Wrap in one atomic scope; carry `difference_reason/notes/resolved_at/adjustment_entry` in the event payload (A116 pattern) so the projection writes them and rebuild reproduces resolved state (`reconciliation/projections.py:505-513` currently leaves bank-line state untouched). Absorbs the A99b site at `:1771`. **Scope fence:** difference-resolution only — no exception-queue read model. Tests: failure-injection between post and stamp → full rollback; wipe-and-rebuild reproduces identical resolved state; double-submit is idempotent.

### 10. A159 — refund backfill (~1-2d)
Webhook view 200s on `process_refund` failure (`shopify_connector/views.py:530-550`) and the 4h poller has no refund path (`tasks.py:137-178`) → missed refunds permanently overstate revenue. Add refunds to the catch-up sync (GraphQL order query can carry refund fields, or a dedicated refunds query; handlers are already idempotent on `shopify_refund_id`), and/or 5xx on retryable handler failure. Test: refund arriving before its order + a dropped webhook both recover via the poller.

### 11. A160 — backups authorization (~0.5d)
`backups/views.py:31-219`: list/export/download/**restore**/delete gated only by `IsAuthenticated`. Add `require()` permission gates (OWNER/admin; restore behind a sensitive permission). Denial tests for VIEWER and ordinary members on every endpoint.

### 12. A161 — fail-closed restore + backup verification (~2-4d code, + ops)
Importer skips missing/malformed model files and can commit partial restores (`backups/importer.py:97-131`); registry omits newer tables (`backups/model_registry.py`); no checksum/count verification. Make restore fail closed (verify export_hash, manifest counts, company/version; post-restore assert trial balance + subledger tie-out + event max-ID); registry completeness test. **Operator half (flag, don't fake):** verify DO managed-Postgres backups are actually enabled on the live cluster; move app ZIPs off `MEDIA_ROOT` to off-host storage; one timed destructive restore drill.

### 13. A162 — fail-safe production boot (~0.5d)
`settings.py:14` defaults DEBUG=True. Default it **False**; require explicit `DEBUG=True` in dev; assert `PROJECTIONS_SYNC=True` at prod boot (JE creation hard-depends on it, `accounting/commands.py:766-783`); fix `.env.example` drift (`DJANGO_ALLOWED_HOSTS`→`ALLOWED_HOSTS`, drop unread `POSTGRES_*`, add `SENTRY_DSN`/`REDIS_URL`/`PROJECTIONS_SYNC`); make test settings explicit rather than argv-sniffed (the `settings.TESTING` "test-in-argv" trigger). **Deploy trap:** before deploying, confirm the droplet `.env` explicitly sets `DEBUG=False` and dev machines set `DEBUG=True`, or local runserver breaks.

### 14. A163 — alerting that reaches a human (~1d + ops drill)
Prometheus stack is inert (placeholder Slack URL; rules on never-emitted metrics; middleware not installed). Ship the minimum instead: external uptime ping on `/_health/ready`; a **web-process** (not Celery — the worker being dead is the failure) check alerting on projection lag / unresolved ProjectionFailureLog; confirmed Sentry notification rules. Mark or delete the aspirational ops/ configs. **Ops:** force one projection failure and prove a named human gets pinged.

### 15. A124 — GDPR export + deletion jobs (~2-3d)
Handlers only write PENDING `GdprRequest` rows (`shopify_connector/commands.py:1531-1590`); the app is PUBLISHED with 30/90-day SLAs. Build idempotent jobs: `customers/data_request` → export; `customers/redact` → anonymize Customer + derived docs + `ShopifyOrder.raw_payload`; `shop/redact` → tenant purge. Stamp COMPLETED with evidence + audit event. **Owner decision needed:** immutable-event PII policy — recommended default: scrub every mutable store now and document a lawful-basis exception for the append-only ledger; crypto-shredding is a later design if legal review demands it.

---

## Per-task workflow

Branch → RED test proving the audited bug → fix → green → regression sweep of the touched area → PR (cite the task ID + audit finding) → merge → tick the gate checkbox in NEXT_TASKS.md → one-line closeout in TASKS_DONE.md (new format: `ID — date — shipped — outcome — commit/PR`).

## Definition of done for this batch

All engineering checkboxes in NEXT_TASKS.md's "Safe-supervised-pilot exit gate" ticked; the three **[ops]** checkboxes (GDPR end-to-end evidence, restore drill, alert drill) either done or handed to the operator as a named checklist with exact commands. Then deploy (backend: pull, migrate if any, restart api/celery/beat; frontend if touched: `npm run build` + pm2 restart), and update the gate section.

## Owner decisions to surface early (don't guess)

1. A158: guard the legacy matcher vs retire /banking now (guard is the default in this batch).
2. A155: raise-vs-cleanup semantics for failed voids (raise-inside-atomic recommended).
3. A124: immutable-event PII policy (documented lawful-basis exception recommended as v1).
4. M-track (not this session's code, but runs in parallel): the pilot price for M2 conversations.
