# Engineering Protocol

Established: 2026-05-25
Status: Canonical. Read this before fixing or creating anything in Nxentra.

This document defines the engineering practices that protect Nxentra's architecture from drift, regression, and silent failure. It is descriptive (codifies what the codebase already does well) and prescriptive (locks in what the 2026-05-25 A78 post-mortem revealed was missing).

Read first:
- [finance_event_first_policy.md](finance_event_first_policy.md) — the accounting laws (event-first, projections, idempotency)
- [architecture-map.md](architecture-map.md) — the command → event → projection pipeline
- [core-assurance-baseline.md](core-assurance-baseline.md) — the 38 invariant tests and what they prove

---

## 1. Layer discipline

Code goes in one of six layers, with strict directional dependencies:

```
View (HTTP boundary)
  └─→ Command (business rules + validation)
        └─→ Event Emission (immutable log)
              └─→ Projection (derives read models)
                    └─→ Read Model (queryable view)
                          └─→ Read-only API (for views)

Policy (validation logic) — called by Commands and Views
```

Rules:

1. **Views never write directly to read models.** Views call commands. Commands emit events. Projections write read models. If a view needs to "fix" a read model, the answer is a new command + event + projection update — not a direct UPDATE.

2. **Commands don't query projections to make business decisions.** A command is allowed to read input from the request, look up domain objects, validate against policies, and emit events. It is NOT allowed to read from `AccountBalance` to decide what to post — that creates an order-of-application dependency that breaks under replay.

3. **Projections don't call commands.** Projections derive read state from events. If you find yourself wanting a projection to "trigger" something, emit a new event from the upstream command instead.

4. **Policies are pure validators.** No side effects, no event emission, no projection writes. Pure functions of (input, current state) → (allow | deny + reason). See `accounting/policies.py` for the pattern.

5. **The events module owns the schema.** Every event type has a Pydantic payload in `events/types.py`. Changing the schema is a versioning event — add a new schema, don't mutate the old one (existing events must replay).

---

## 2. When you write a new guard / validator

The 2026-05-25 A78 regression: a guard was added to `create_sales_invoice` at line 723 without the `not auto_created` bypass that the parallel guard at line 1716 already had. Result: 3 weeks of broken Shopify projection nobody noticed.

When you add a guard:

1. **Grep all call sites of the same pattern.** Before committing, run `grep -rn '<the rule you just added>' .` and check every match. Apply consistently in one commit.

2. **Decide the `auto_created` semantics explicitly.** Most user-input validators should be `if not auto_created and <condition>: ...`. Platform integrations pass `auto_created=True` for a reason: they've already validated upstream with stricter rules.

3. **Document the rule's ticket reference inline.** Use `# A78:` or `# A23:` style prefix in the comment. Future grep finds every place the rule applies.

4. **Write a regression test.** Same commit. The test asserts the guard fires for the wrong caller AND doesn't fire for the right caller.

What the A78 incident proves: a one-line guard added without the sweep + test pattern can silently break the entire Shopify pipeline for weeks. The 30 seconds it takes to grep is non-negotiable.

---

## 3. When you change a projection

Projections are the most subtle part of the system. They run async, off the request path, and silent failures don't bubble up to users.

Rules:

1. **Read [projection-idempotency.md](projection-idempotency.md) first.** Re-read every time. The `_apply_line()` skip-guard pattern that bit four projections in March 2026 is easy to reintroduce.

2. **Idempotency is at `process_pending()`, not in handlers.** `ProjectionAppliedEvent` dedupes per (company, projection, event). Handlers process every line of every event they consume. Never write `if x.last_event_id == event.id: return` in a handler.

3. **No silent no-ops** — see [finance_event_first_policy.md §8](finance_event_first_policy.md). Use `raise` for invalid state, `defer` for race conditions, emit `*.failed` events for permanent errors. Never `logger.warning + return`.

4. **Wrap writes in `projection_writes_allowed()` context.** Read models like `JournalEntry` enforce this with `ProjectionWriteGuard`. Outside the context, writes fail loudly — that's a feature.

5. **Add the projection to `core-assurance-baseline.md` test coverage.** Every new projection needs at least one test in `test_truth_invariants.py` or `test_control_invariants.py` proving its math is right and its replay is idempotent.

6. **Watch `projection_health` after deploying.** Lag should converge. If a projection stays N events behind, it's silently failing OR the workload genuinely shifted; investigate before assuming the latter.

---

## 4. When you write a seed / flush / replay command

Lessons from `seed_test_csv_pack` (A81 in post-listing queue):

1. **`--flush` must cascade through projected state.** If your seed creates events that produce SalesInvoices, JEs, BankStatements, etc., then `--flush` must delete all of those too. Otherwise re-runs hit `IntegrityError` on the second pass.

2. **Test the flush + reseed cycle in CI.** A test that runs `seed --flush`, asserts clean state, then runs `seed --flush` again and asserts it succeeds with no DB errors. Would have caught A81 immediately.

3. **Document the order of operations in the command docstring.** Future operators (you in 6 months) need to know what state the command leaves the system in.

4. **Use `rls_bypass()` + `command_writes_allowed()` + `projection_writes_allowed()` contexts explicitly.** Seed commands span multiple contexts; declare them. Don't rely on the default behavior of any one of those decorators.

5. **Print what changed.** Every seed/flush should `stdout.write` counts of records created/deleted/skipped. Silent success is indistinguishable from silent failure.

---

## 5. When you fix a bug

1. **Find the root cause before changing code.** If the symptom is "projection didn't create records", the root cause is one of: missing guard bypass, schema mismatch, missing mapping, race condition, etc. Don't add a workaround that masks the real issue.

2. **One bug = one commit.** Don't bundle "fix A78" with "also clean up unrelated whitespace." Reviewers can't tell what the fix actually was. Future bisect can't isolate the change.

3. **Commit message format:**
   ```
   {module}: {one-line what changed}

   Why: {what broke, how it manifested}

   Mirror the pattern at {file:line} where {related rule} is already
   applied correctly. {Reference incident ID if relevant.}

   Symptom that surfaced this: {observable behavior}

   Co-Authored-By: ...
   ```
   See commit `540650d` (the A78 fix) for the template.

4. **Add a regression test in the same commit.** Same PR, same review cycle. "I'll add tests later" means "I won't."

5. **Reference the incident ID in code comments.** `# A78: ...` so future grep finds every place the lesson applies. Self-documenting code archaeology.

6. **Update the relevant canonical doc if the rule changed.** If you learn a new pattern, write it down. Tribal knowledge in one developer's head is technical debt with compound interest.

---

## 6. When you add a connector (Shopify, Stripe, WooCommerce, etc.)

See [finance_event_first_policy.md §9](finance_event_first_policy.md) for the accounting contract. Engineering-side requirements:

1. **One projection per connector** (`{platform}_accounting`). Don't extend existing projections.

2. **Use the `_for_platform` command wrappers** (`create_and_post_invoice_for_platform`, `create_and_post_credit_note_for_platform`). They handle `auto_created=True`, idempotency by source key, and system actor context.

3. **Webhook signature verification is the first thing the view does.** Reject unauthenticated requests with 401 before parsing the body. See `shopify_connector/views.py:144-153`.

4. **End-to-end test** that emits a fake external webhook and asserts the projection produces the expected SalesInvoice + JournalEntry. This test catches the entire A78 regression class.

5. **Operator-visible health surface.** The connector needs a page (`/integrations/{name}`) showing connection status, last sync time, error count, recent failed events. Silent failures destroy trust; visible failures are recoverable.

6. **Document the rate limits and the catch-up strategy.** Every connector has an external API with limits. Document them. Document what happens when the limit is exceeded (retry with backoff? defer? alert?).

---

## 7. CI and test requirements

From [core-assurance-baseline.md](core-assurance-baseline.md):

1. **The 38 invariant tests are mandatory CI gates.** Any failure blocks merge. No exceptions for "I'm in a hurry."

2. **Runtime invariants run against Postgres in CI** (not SQLite). Concurrency, RLS, and isolation behavior differ between engines.

3. **Add to the suite when you add to the architecture.** New projection → new invariant test. New connector → new end-to-end test. New currency-handling code → new FX test.

4. **The "what tests DO NOT prove" section in core-assurance-baseline is itself a backlog.** Pick one off it per quarter and close the gap.

---

## 8. Comment and documentation standards

What the codebase already does well — keep doing this:

1. **Multi-line docstrings on commands and projection handlers** explaining WHY, not just what. See `_handle_order_paid` in `shopify_connector/projections.py` for the template.

2. **Incident-tagged comments**: `# A78: ...`, `# A23: ...`, `# A51 (2026-05-15): ...`. These let future grep find every place a lesson applies.

3. **"What this prevents" sections** in policy docs (this one, finance_event_first_policy.md). Make the consequences of breaking the rule concrete.

4. **Pointer comments to related code** — `# See line 1716 for the parallel pattern` — these are gold during reviews.

What to stop doing:

1. **No "TODO" without a ticket.** A bare TODO is a wish that never gets prioritized. Either file the ticket and reference it (`# TODO(A82): ...`) or fix it now.

2. **No commented-out code in commits.** Use git for that. Future maintainers (you in 3 months) shouldn't have to wonder if a comment block was a deliberate choice or a forgotten experiment.

3. **No "should" comments without explaining why** ("this should use X" → why? when? at what cost?).

---

## 9. Engineering meta-rules

1. **Memory beats velocity.** A bug fixed once is worth more than five features shipped fast. The 2026-05-25 day spent debugging A78 cost less than the trust loss would have if a paying merchant hit it.

2. **The rules are not the limit; they're the floor.** If you can write code that's safer, clearer, or more testable than the rules require — do it. The rules exist for the worst case; aspire higher.

3. **When in doubt, ask the existing code.** This codebase has 18 months of accumulated decisions encoded in patterns. Search for similar features before designing new ones from scratch.

4. **Refuse to ship the silent failure.** If you can't tell when something breaks, you have not finished the feature. Add the alert, the test, the operator-visible counter, the failed-event emission. Then ship.

5. **The rules apply to AI-assisted code too.** When Claude (or any AI assistant) writes a change, the same protocol applies: grep for related patterns, write the test, follow the layer discipline. AI doesn't get a pass on engineering practice.

---

## 10. Adoption

This document is canonical from 2026-05-25 forward. Existing code that breaks these rules is a debt — file it, prioritize it, but don't propagate.

When this document gains new rules (driven by incidents or evolution), they apply to:
- All new code from the date of the addition
- Existing code touched in any commit after the date

Versioning the rules themselves: a `git log docs/engineering_protocol.md` is the change history. No formal versioning needed.
