# Next session — paste this prompt

Continue Nxentra. Last session (2026-04-30 → 2026-05-01) shipped the entire merchant-facing reconciliation product spine plus a critical bank-CSV idempotency fix and the first-user-invite asset kit:

- **A2.5** — rename PaymentGateway → SettlementProvider (`caa1ab9`)
- **A12** — settlement-provider dimension layer + COD wizard step (`86d62d2` + follow-ups `6a09473`)
- **A13** — Reconciliation Control Center MVP at `/finance/reconciliation` (`b24065b`)
- **A14** — manual settlement CSV import + Expected Bank Deposit convention (`238d0a9`)
- **A14b/A14c** — bank-rec auto-match for settlements + per-Shopify-order drilldown (`3445bc0`)
- **A16** — Reconciliation Difference Engine + "Tell me the story" narrative + Needs Review queue (`ced05ad`, `63d8888` hotfix)
- **A17** — bank statement CSV idempotent re-import (line-level SHA-256 dedup_hash, partial-overlap re-upload safe) (`faf5b52`)
- **Docs + invite kit** — SESSION_LOG, NEXT_TASKS, EVALUATION_STATUS refresh (78% → 84%, $500K-$1.5M → $700K-$1.8M), bilingual onboarding welcome doc at [docs/onboarding/welcome.md](docs/onboarding/welcome.md) (`b63a192`, `8ffd8b2`)

Total: 18 commits, ~1,800 LOC + tests, position upgraded from late-beta to **late-pilot / pre-first-revenue**. Phase A engine complete on the merchant-facing side.

Read [SESSION_LOG.md](SESSION_LOG.md) "Session: April 30 - May 1, 2026" entry, [NEXT_TASKS.md](NEXT_TASKS.md) "What to do right now, today", and [EVALUATION_STATUS.md](EVALUATION_STATUS.md) for the full strategic position.

---

## Goal of this session

**Validate end-to-end in a browser, then send the first-user invite.** No new feature work until the existing product is proven to actually work against a real Shopify dev store. The engine has been tested unit-level (72+ passing tests across A14b/A14c/A16/A17/reconciliation_views/settlement_imports) but never clicked through end-to-end since A2.5 went live. Seven tickets shipped without that browser validation.

The strategic line for this session: **product spine before features, validation before invite, signal before refactor. Stop building. Start selling.**

---

## Step 0 — Prerequisite assets (~45 min, blocks Step 3 only)

These are the four assets the user needs in hand before sending the first-user invite. Three are non-engineering tasks the user does themselves; one is a tiny frontend follow-up the agent can do.

### Non-engineering (user owns these — verify they're done before Step 3 fires)

- [ ] **Calendly / Google Calendar appointment link** with 4 open 45-minute slots over the next 5 days, in Cairo time (UTC+2). Free Calendly tier is fine.
- [ ] **WhatsApp Business number** set up — separate from personal. Real number to substitute into the welcome doc placeholder `+20-xxx-xxx-xxxx` and into the invite message.
- [ ] **Sign-up flow tested in incognito** at app.nxentra.com. Walk: register → verify email → onboarding wizard → connect Shopify dev store → import one order → see it on `/finance/reconciliation`. Anything that breaks → file a ticket and fix before Step 3.

### Engineering (small frontend follow-up to A17, ~15 min)

- [ ] Surface `lines_skipped_duplicate` in the bank-statement upload toast. The backend now returns `{ lines_created, lines_skipped_duplicate }` from `POST /api/accounting/bank-statements/`. Find the success toast in the bank-rec upload page (search for `lines_created` in `frontend/`). Update the message from `"Imported X transactions"` to:
  - `"Imported X transactions"` if `lines_skipped_duplicate === 0`
  - `"Imported X transactions, skipped Y duplicates"` if `lines_skipped_duplicate > 0`

  Also: same treatment on the Paymob/Bosta settlement upload page if it doesn't already show the `deduplicated: true` count from the import response. Quick win — the merchant uploading overlapping CSVs needs to see immediately that nothing was double-posted.

---

## Step 1 — Manual UI pass on `/finance/reconciliation` (~1h)

Start the frontend dev server and the backend, log into a test company, and walk the new screen.

Use one of the existing companies on dev or seed a fresh one via:

```bash
cd backend
python manage.py seed_shopify_demo  # or whichever seed creates the dev fixtures
```

To get a `MATCHED_WITH_DIFFERENCE` bank line into the queue, the easiest path:

1. Onboard a Shopify store (or use Aljazeera5 if it has data).
2. Upload a Paymob CSV at `/finance/settlements/import` with one batch summing to `net=1455.00`.
3. Import a bank statement (manually or via CSV) at `/bank-reconciliation/` with one line at `1450.00` and a description containing the batch ID — this trips the 2% tolerance (expected 1455, received 1450, difference 5).
4. Run auto-match on the statement → bank line should land as `MATCHED_WITH_DIFFERENCE`.

Now visit `/finance/reconciliation` and verify:

- [ ] **Narrative banner** renders at the top in a primary-tinted card. Sentence makes sense given current state. Reads "Shopify says X. After fees Y... Bank shows V. Unexplained: U bank deposits matched within tolerance..."
- [ ] **Needs Review card** (amber-tinted) renders above the totals tiles. Shows the bank line with: Date, Provider (paymob), Batch ID, Expected (1455.00), Received (1450.00), Difference (5.00, "Short paid"), Reason picker dropdown, Notes input, Resolve button.
- [ ] Reason picker dropdown lists six reasons (Extra fee / Bank charge / Chargeback / Write-off / Rounding / Other) — does NOT include "Needs review".
- [ ] Pick "Extra gateway/courier fee", add a note, click Resolve. Toast confirms. The row leaves the queue. Summary refreshes.
- [ ] Stage 3 card now shows the row in `matched_lines` count, not in `Needs review` tile.
- [ ] Open the journal entries page → there's a new posted JE with `source_module='payment_settlement_difference'`, lines DR `52000 Payment Processing Fees` 5.00 / CR `11600 Expected Bank Deposit` 5.00.
- [ ] Original Paymob settlement JE's EBD line is now reconciled (drained: 1455 = 1450 clearance + 5 adjustment).
- [ ] Refresh the page — narrative banner sentence updates to reflect zero unresolved differences.

**Edge case to verify:** over-paid case. Repeat with a bank line at `1460.00` (5 over). Pick "Rounding / FX". Adjustment JE should be DR EBD 5.00 / CR Payment Processing Fees 5.00 (reversed direction).

**A17 verification (also part of Step 1):** while you have the bank import open, re-upload the *same* statement. Toast should now read "skipped X duplicates" and `BankStatementLine` count for that account should not double. If it does, A17 has a regression and is the priority before anything else.

Document any visual glitches, broken refreshes, race conditions, or wiring bugs as new tickets in NEXT_TASKS.md (file as A16-followups or A17-followups). Fix only what blocks the merchant — defer cosmetic polish.

---

## Step 2 — Live Phase 1 dry-run on a fresh Shopify dev store (~1.5h)

Memory has this flagged as blocking before the first real user, last attempted before A12-A17 shipped. Walk the merchant journey from connect to reconciled:

1. Create a fresh Shopify dev store (or reuse `nxentra-test-code.myshopify.com` with cleared data).
2. Sign up a fresh Nxentra company. Walk the onboarding wizard end-to-end. Verify:
   - Fiscal Year step still works.
   - Shopify Connect step authorizes.
   - **NEW: COD courier step renders.** Currency-driven default suggestion (EGP→Bosta) is pre-selected. "Other" lazy-creates a SettlementProvider. Sets `ShopifyStore.default_cod_settlement_provider`.
   - Wizard advances past Shopify Setup (A7 was a known issue — confirm whether A12's COD step inadvertently fixed or left it).
3. Place 3-5 test orders on the dev store with mixed payment methods (paymob / cash_on_delivery / shopify_payments). Trigger the webhooks.
4. Visit `/finance/reconciliation`:
   - Stage 1 should show per-provider clearing balances with aging (all 0-7d initially).
   - Settlement provider dimension tags should be visible by drilling into a JE.
   - Click a provider → drilldown opens, "Orders" tab shows the orders with status `expected`.
5. Upload Paymob CSV for the prepaid orders + Bosta CSV for the COD orders at `/finance/settlements/import`.
6. Refresh `/finance/reconciliation`:
   - Stage 2 should show settled count + total.
   - Drilldown "Orders" tab status should advance to `settled` for matched orders.
   - Bosta CSV with `status=returned` rows should generate Sales Returns hits.
7. Import a bank statement covering the payout deposits (most batches should match exactly, seed at least one near-match for A16).
8. Run auto-match. Verify clearance JEs post for matched batches; near-match goes into Needs Review.
9. Resolve the near-match. Verify EBD drains.
10. Final state: drilldown "Orders" shows `banked` for the resolved orders; Stage 3 has zero unmatched for the seeded statement.

**A17 dry-run check:** in step 7, re-upload the bank statement a second time with a wider date range that overlaps. Verify lines do not double, toast shows skipped count, Stage 3 reconciliation counts stay clean.

Take screenshots at each milestone. Anything that breaks → ticket in NEXT_TASKS.md.

---

## Step 3 — Send the first-user invite

If Steps 1 + 2 are clean (or only cosmetic findings), and Step 0's three non-engineering assets are in hand, **send the invite to the Egyptian Shopify merchant acquired 2026-04-22**.

The invite text (EN + AR) lives in this conversation's earlier turns — copy from there or have the user paste it. The welcome doc to link from the invite is at [docs/onboarding/welcome.md](docs/onboarding/welcome.md) — host it on Notion or as a Google Doc and put the public link in the invite, *or* leave it as a markdown file the merchant can read after sign-up.

If the user balks at sending the invite (this has happened — see [EVALUATION_STATUS.md](EVALUATION_STATUS.md) Weakness #1: *"You build when you should sell"*), name it explicitly. Don't accept "let me ship one more thing first" without challenge. The single biggest predictor of whether Nxentra becomes a business in 2026 is whether the first invite goes out in May. Everything else is downstream.

If real bugs surface in Step 1 or Step 2, fix them first (file as A16/A17 follow-ups), commit, push, redo Steps 1-2. Do not invite until the dry-run is green.

---

## Step 4 (only if 1-3 done early, with bandwidth) — pick up the next deferred ticket

In priority order:

- **A11** — Shopify JE per-item account routing. ~2-3d. Real merchants want HEAD-001 hitting "Headphones Revenue" 41001 even when the order is imported from Shopify. Today the projection posts one aggregate revenue line to the company's `SALES_REVENUE` mapping. **Pull forward only if first user has diverse SKUs and customizes per-item.** Otherwise wait for signal.
- **A6** — Onboarding wizard auto-launch on first dashboard visit. ~1d. UX polish.
- **A7** — Wizard step routing after Shopify connect callback. ~1d. UX bug. Verify in Step 2 above whether A12's wizard step inadvertently fixed this.
- **A10** — AR tie-out invariant accommodates non-AR-Control posting profiles. ~2-3d. Currently logs false-positive warnings on every Shopify clearing flow; not blocking but noisy.

Do **not** start A3-A5 (architectural cleanup) until first-merchant signal validates the framing. Do **not** start Phase B until all of Phase A is green.

---

## Higher-priority Tier-1 work after first user is live (next 30 days)

These came out of the EVALUATION_STATUS refresh and are the path from "first user" to "first revenue":

- **Stripe billing integration** (~3-5d) — Stripe Checkout + Customer Portal. Cannot charge customers without it. Build when first user crosses 60-90 day free trial mark, or when a second prospect asks to pay.
- **Automated backup schedule + DR runbook** (~2-3d) — managed Postgres + tenant export weekly. Latest tenant export is `pilot_backup_post_fix_2026-03-25.zip`, over a month old. Should not be on the critical path for a paying customer. The DR runbook is a 1-2 page document covering: backup inventory, restore steps, RPO/RTO commitments, single-tenant restore via event replay, last-tested date.
- **Shopify App Store listing prep** (~1 week) — privacy policy, compliance docs, listing copy. OAuth flow already works; this is the distribution channel.
- **Onboarding observability** (~2d) — telemetry on wizard step completion, time-to-first-reconciliation. Without this, "operator-independent" is unverifiable.
- **Decision: kill-or-ship Properties + Clinic.** They dilute the launch narrative. Either ship them as separate products or move them to a `vertical_extensions/` archive folder.

These belong in NEXT_TASKS.md proper if not already filed. The agent should propose filing them and the user confirms.

---

## Working notes for the agent

- Memory at `C:\Users\gezzo\.claude\projects\c--Users-gezzo-nxentra-app-nxentra-register\memory\` carries the user profile and recent project state. Read MEMORY.md early.
- The user is on Windows with bash via Git for Windows. Use Unix shell syntax in Bash tool calls.
- Test command: `cd backend && python -m pytest tests/<file>.py --tb=short` (don't use `-x` if surveying broadly; use it when iterating on a single failure).
- Frontend typecheck: `cd frontend && npx tsc --noEmit; echo "EXIT=$?"`.
- Pre-commit hooks (ruff + ruff-format) often reformat files; if commit fails on hook reformat, re-stage the modified files and retry.
- Default to small commits per ticket with the `Co-Authored-By: Claude Opus 4.7 (1M context)` trailer (HEREDOC pattern in CLAUDE.md).
- Push to origin/main only when the user confirms — they push after each commit individually.
- For UI changes, the user runs the dev server in the browser and verifies; agent does not have UI access.
- The user has been documented (per EVALUATION_STATUS Weakness #1) as having a tendency to defer browser-validation/sales work for "next session." If they redirect away from Steps 1-3, name it. They explicitly asked for that pushback in the developer self-evaluation conversation.

---

## What "done" looks like for this session

Either:

(a) **Steps 0-3 green: first invite sent.** Brief SESSION_LOG entry covering "Step 0 frontend toast shipped, dry-run found N issues, all fixed, first user invited on YYYY-MM-DD" + commit hash list. **This is the strongly preferred outcome.**

(b) **Bugs found, ticketed, fixed, dry-run re-run green, first invite sent same session** — same outcome as (a) with detour.

(c) **A deep issue surfaces** (webhook delivery flake, projection lag, dimension validation false-positive) — file as a new ticket, fix at root cause, push the invite to next session. Acceptable but requires explicit "OK to delay" from the user.

(d) **Engineering succeeds but invite slips.** *Not* an acceptable outcome — name it, don't ship around it. The codebase has been ahead of the customer for 6 months; closing that gap is the work.

---

## After the first user is live — what changes

- The **first 90 days are paid in feedback, not money.** Bi-weekly 10-15 min calls, WhatsApp support during Cairo business hours. See [docs/onboarding/welcome.md](docs/onboarding/welcome.md) for the contract.
- **Hard cap at 5 design-partner merchants** in the first month. Don't post in Shopify Egypt Facebook / LinkedIn until the first 3 are stable. See concentric-rings outreach plan in this conversation's earlier turns.
- **Hire #1 trigger: customer #3 signs.** Mid-senior Django engineer. Codebase documented well enough for 1-2 week onboarding. ~$60-90K MENA, $120-150K remote-EE. Affordable from $5K MRR.
- **Build billing when:** first user crosses 60-day mark and you can see usage patterns, OR a second prospect asks to pay, OR Shopify App Store submission requires it (App Store mandates billing on day 1).

The path from here is execution, not engineering. The agent should resist "let me also build X" temptations and keep redirecting to validation + invite + customer conversations.
