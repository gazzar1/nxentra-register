# Next session — paste this prompt

Continue Nxentra. Last session (2026-05-02 → 2026-05-03) ran the full Aljazeera7 end-to-end dry-run and surfaced a substantive Tier-1 fix list that **blocks the first-user invite**. The dry-run did exactly what the test pack was designed to do — caught real data-loss + accounting-correctness bugs at integration boundaries, not architectural ones. Five commits shipped this session:

- **`7425bbc`** — Paymob/Bosta CSV importer header aliases (3 additions; `gateway_fee`, `collected_amount`, `net_due`)
- **`b074164`** — A17 toast follow-up: surface skipped-duplicate count in bank import success message
- **`96dd1e6`** — `seed_test_csv_pack` management command + `test_data/` fixtures (5 CSVs covering EGP / Paymob / Bosta / refunds / returns / short-payment / alias-normalization / settlement-without-order / bank fees scenarios)
- **`5df4d1e`** — Fix Cash Flow report 500 error: wrong `JournalLine` model name + `entry__` FK alias (Sentry caught)
- **`e9a0ddd`** — Fix bank-rec auto-match crash: 8 sites bypass `JournalLine` read-model save guard via `.update()`; 57 tests still pass (Sentry caught)

Read [SESSION_LOG.md § Session: May 2-3, 2026](SESSION_LOG.md) and [NEXT_TASKS.md § Phase A continues — Tier-1 fix list](NEXT_TASKS.md) for the full play-by-play and ticket details.

---

## Goal of this session

**Ship the Tier-1 fix list (A18-A26), re-run the Aljazeera7 dry-run, send the first-user invite.**

The strategic line is unchanged from last session — **stop building, start selling** — but the validation step revealed real bugs that need fixing before any merchant sees this product. A failed first-user invite is much worse than a delayed one. Every Tier-1 ticket is unit-testable and bounded; aim for tests-first to lock the fix before any UI re-verification.

**Conservative budget: 5-8 days of focused engineering, then half a day re-running the dry-run, then send the invite.**

---

## Step 1 — Ship A18-A26 in priority order

Each ticket has full scope + reasoning in [NEXT_TASKS.md](NEXT_TASKS.md). Suggested order (by leverage / dependency):

1. **A19** — Bank-rec unmatch / exclude must reverse the clearance JE (~1d). Real merchants will hit this; orphan accounting is unrecoverable. Tests first: unmatch a previously-matched line, verify a contra clearance JE posts and bank account 11200 nets to zero.
2. **A20** — A14 refund-during-settlement: importer rejects unbalanced batch with clear error (~0.5-1d). MAY01-A scenario. Either parse `refund_or_chargeback` into `uncollected_amount` so math reconciles (preferred — correct accounting), or reject at parse time with merchant-visible error.
3. **A21** — A14 Bosta `returned_uncollected_amount` column reader (~0.5d). BST-701 scenario, 1,200 EGP currently silently dropped. Add field to `_BOSTA_HEADER_ALIASES`, route status=returned amounts to `uncollected_amount`. Test: BST-701 batch produces JE with separate Sales Returns line.
4. **A23** — Refund handler projection race (~1-2d, architectural). Wait-with-retry in handler if original invoice not yet POSTED + idempotency on credit-note creation. Test: emit order_paid + refund_created in same batch, verify credit note posts after retry.
5. **A22** — A14 settlement importer per-row provider routing (~1-2d). MAY01-B Paymob/Paymob Accept mixed batch. Group rows by per-order `gateway` normalization, post multi-line clearing drain.
6. **A25** — Manual-match picker filter surfaces settlement EBD lines (~0.5d). Currently the picker shows only orphan clearance JEs; merchant can't trigger A16 from UI. Fix the candidate query to include un-reconciled JournalLines on EBD account from `source_module='payment_settlement'` JEs sorted by amount-proximity.
7. **A24** — Bank statement frontend column-mapper UI (~1-2d). Backend already supports it; expose 2-step import flow with smart pre-fill + persist-mapping-per-(company, bank_account). Ship the unified `<CsvMappingDialog>` component the broader vision calls for.
8. **A18** — Frontend deploy hygiene + atomic-deploy script (~0.5d). Wrapped fail-fast script + 1-page runbook. Without this, every future deploy risks the same partial-state failure that broke prod for hours this session.
9. **A26** — Settlement-without-original-order rejection or warning (~0.5d). Validate referenced order_ids at import time; reject with merchant-visible error OR route to suspense account. Pair with A35 narrative banner (deferred to post-first-user, but its sister item).

Each ticket should land as its own commit on `main`. After each, **run the relevant test suites** (`tests/test_settlement_imports.py`, `tests/test_a14b_settlement_prepass.py`, `tests/test_a16_difference_engine.py`, `tests/test_a17_bank_statement_dedup.py`, `tests/test_reconciliation_views.py`) to confirm nothing regressed.

---

## Step 2 — Re-run the Aljazeera7 dry-run

After all of Step 1 is shipped + pushed + pulled on the droplet:

```bash
cd /var/www/nxentra_app
git pull origin main
pm2 restart nxentra-api nxentra-web

cd backend
# Optional: flush the previous test pack data and re-seed cleanly
python manage.py seed_test_csv_pack --company-slug aljazeera7 --flush
```

Then in the browser, end-to-end through `/finance/reconciliation`:

1. **Stage 1 verification.** Three providers (Paymob 8,050 / Paymob Accept 1,000 / Bosta 6,200), totals match expected after credit-note refunds. ✅ already worked last session.
2. **Upload Paymob CSV.** All 4 batches must post JEs cleanly — including MAY01-A after A20 (test: re-upload `test_data/paymob_settlements_test.csv`, expect 4 settlement JEs in `/accounting/journal-entries`, no Sentry errors).
3. **Upload Bosta CSV.** All 4 batches post + BST-701 includes a `DR Sales Returns 1,200` line in JE-34-XXX (test after A21).
4. **Upload bank statement (use the Nxentra-format CSV from last session OR the original after A24 ships).** Auto-match runs cleanly, no JournalLine save-guard crash (already fixed).
5. **Manually match BNK-003 (BST-701, 1,850).** Picker now shows BST-701's EBD line (2,050) as a candidate after A25. Pick it → MATCHED_WITH_DIFFERENCE → routes to A16 Needs Review queue.
6. **Resolve the difference.** Pick "Bank charge" reason, add a note, click Resolve. Adjustment JE posts (DR Bank Charges 200 / CR Bosta Clearing 200). BNK-003 leaves Needs Review queue.
7. **Try unmatching a line and re-matching.** After A19, the clearance JE reverses cleanly. Audit trail intact.

If anything fails: fix at root cause, commit, push, redo Step 2 from where it broke. Do not invite until all 7 sub-steps are green.

**Success criterion:** all 7 bank lines reach a final state (Matched / Matched with difference resolved / Excluded as known-non-match) AND Stage 1 / Stage 2 / Stage 3 numbers reconcile cleanly AND no Sentry errors fire during the run.

---

## Step 3 — Send the first-user invite

If Step 2 is green AND the user's pre-flight invite kit is ready (Calendly link with 4 open slots, WhatsApp Business number set up, sign-up flow tested in incognito), **send the invite to the Egyptian Shopify merchant acquired 2026-04-22**.

The invite text (EN + AR) was prepared two sessions ago — pull from the conversation history if needed. The welcome doc is at [docs/onboarding/welcome.md](docs/onboarding/welcome.md).

**The user has been documented as having a tendency to defer the invite for "one more thing"** (per [EVALUATION_STATUS.md](EVALUATION_STATUS.md) Weakness #1). If they balk, name it explicitly. The Tier-1 list this session was real; the *next* deferral isn't. The single biggest predictor of whether Nxentra becomes a business in 2026 is whether the first invite goes out in May. After Step 2 is green, the answer to "should we ship one more thing" is **no, send the invite.**

---

## Step 4 (only if Step 3 done with bandwidth) — Tier-2 UX cleanup

After the invite goes out, **don't start Phase B**. Instead, work through Tier-2 UX (A28-A36) and the deferred items (A37, A38) — informed by whatever the merchant complains about in the first 48-72 hours, not pre-emptively. Pull forward exactly the items the merchant signals; defer the rest.

Specifically — **A37 (Subledger tieout cleanup)** is worth scheduling early because it likely also fixes the noisy A10 false-positive warning that has been firing on every Shopify clearing flow for weeks.

---

## Working notes for the agent

- Memory at `C:\Users\gezzo\.claude\projects\c--Users-gezzo-nxentra-app-nxentra-register\memory\` carries the user profile and project state. Read MEMORY.md early.
- The user is on Windows with bash via Git for Windows. Use Unix shell syntax in Bash tool calls.
- Production is on a DigitalOcean droplet (`/var/www/nxentra_app`). The user SSHs in to deploy. Frontend is Next.js + PM2 (`nxentra-web`); backend is gunicorn + Django + Celery + PM2 (`nxentra-api`, `nxentra-celery`, `nxentra-celery-beat`). After A18 ships, deploys should follow the runbook.
- Aljazeera7 (slug `aljazeera7`, store `aljazeera7-store.myshopify.com`) is the dry-run merchant. Aljazeera5 is older test data. Don't confuse them.
- Test command: `cd backend && python -m pytest tests/<file>.py --tb=short` (drop `-x` when surveying broadly; use it when iterating on a single failure).
- Frontend typecheck: `cd frontend && npx tsc --noEmit; echo "EXIT=$?"`.
- Pre-commit hooks (ruff + ruff-format) often reformat files; if commit fails on hook reformat, re-stage the modified files and retry.
- Default to small commits per ticket with the `Co-Authored-By: Claude Opus 4.7 (1M context)` trailer (HEREDOC pattern in CLAUDE.md).
- Push to `origin/main` only when the user confirms — they push after each commit individually.
- For UI changes, the user runs the dev server in the browser and verifies; agent does not have UI access.
- The projection write-barrier (`JournalLine` save-guard) has a TESTING-mode bypass — meaning unit tests pass even when production code violates the guard. **When fixing model-write code paths, manually test in production-mode (or remove the bypass for a focused test run).** This is how A19's auto-match crash slipped through.

---

## What "done" looks like for this session

Either:

(a) **Tier-1 fix list shipped + dry-run re-run green + first invite sent.** Brief SESSION_LOG entry covering "A18-A26 shipped, dry-run re-run found N residual issues, all fixed, first user invited on YYYY-MM-DD" + commit hash list. **Strongly preferred outcome.**

(b) **Some of the Tier-1 list shipped, dry-run partially re-run, one or two more sessions needed.** Acceptable if the priority items (A19, A20, A21, A23) are green and the remainder are clearly scoped. The invite slips by 1-2 days.

(c) **A deeper architectural issue surfaces** (transaction-isolation rabbit hole on A23, projection-rebuild correctness, RLS interaction). File as a new ticket, fix at root cause, push the invite to the next session. Acceptable but requires explicit "OK to delay" from the user.

(d) **Engineering succeeds but invite slips for non-engineering reasons.** *Not* acceptable — name it, don't ship around it. The codebase has been ahead of the customer for 6 months; closing that gap is the work.

---

## After the first user is live — what changes

(unchanged from prior session prompt)

- The **first 90 days are paid in feedback, not money.** Bi-weekly 10-15 min calls, WhatsApp support during Cairo business hours. See [docs/onboarding/welcome.md](docs/onboarding/welcome.md) for the contract.
- **Hard cap at 5 design-partner merchants** in the first month. Don't post in Shopify Egypt Facebook / LinkedIn until the first 3 are stable.
- **Hire #1 trigger: customer #3 signs.** Mid-senior Django engineer. Codebase documented well enough for 1-2 week onboarding. ~$60-90K MENA, $120-150K remote-EE. Affordable from $5K MRR.
- **Build billing when:** first user crosses 60-day mark and you can see usage patterns, OR a second prospect asks to pay, OR Shopify App Store submission requires it.

The path from here is execution, not engineering. The agent should resist "let me also build X" temptations and keep redirecting to Tier-1 fixes + dry-run re-run + invite + customer conversations.
