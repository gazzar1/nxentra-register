# Next session — paste this prompt

Continue Nxentra. Last session (2026-04-30 → 2026-05-01) shipped the entire merchant-facing reconciliation product spine: A2.5 rename, A12 dimension layer, A13 Reconciliation Control Center MVP, A14 manual settlement CSV import (Paymob + Bosta + Expected Bank Deposit convention), A14b bank-rec auto-match for settlements, A14c per-Shopify-order drilldown, A16 Reconciliation Difference Engine (near-match detection + reason picker + adjustment JE + "Tell me the story" narrative + Needs Review queue). Commits `caa1ab9` → `63d8888`. Phase A engine is now complete on the merchant-facing side.

Read [SESSION_LOG.md](SESSION_LOG.md) "Session: April 30 - May 1, 2026" entry for the full play-by-play, and [NEXT_TASKS.md](NEXT_TASKS.md) "What to do right now, today" for the strategic position.

## Goal of this session

**Validate the reconciliation product spine end-to-end before the first real user touches it.** No new feature work until the existing product is proven to actually work in a browser against a real Shopify dev store. The engine has been tested unit-level (61+ passing tests across A14b/A14c/A16/reconciliation_views/settlement_imports) but never clicked through end-to-end since A2.5 went live. Six tickets shipped without that validation.

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

Document any visual glitches, broken refreshes, race conditions, or wiring bugs as new tickets in NEXT_TASKS.md (file as A16-followups). Fix only what blocks the merchant — defer cosmetic polish.

## Step 2 — Live Phase 1 dry-run on a fresh Shopify dev store (~1.5h)

Memory has this flagged as blocking before the first real user, last attempted before A12-A16 shipped. Walk the merchant journey from connect to reconciled:

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

Take screenshots at each milestone. Anything that breaks → ticket in NEXT_TASKS.md.

## Step 3 — Decide on first-user invite

If Steps 1 + 2 are clean (or only cosmetic findings), **proceed with the first-user invite**. Egyptian Shopify merchant acquired 2026-04-22; A1's exit criterion has been met since 2026-04-28; A2.5/A12/A13/A14/A14b/A14c/A16 are additive merchant value beyond what was promised at acquisition.

If real bugs surface in Step 1 or Step 2, fix them first (file as A16-followups or A12-followups depending on origin), commit, push, redo Steps 1-2. Do not invite until the dry-run is green.

## Step 4 (only if 1-3 done early) — pick up the next deferred ticket

In priority order:

- **A11** — Shopify JE per-item account routing. ~2-3d. Real merchants want HEAD-001 hitting "Headphones Revenue" 41001 even when the order is imported from Shopify. Today the projection posts one aggregate revenue line to the company's `SALES_REVENUE` mapping. Filed in NEXT_TASKS.md "A11" section.
- **A6** — Onboarding wizard auto-launch on first dashboard visit. ~1d. UX polish.
- **A7** — Wizard step routing after Shopify connect callback. ~1d. UX bug.
- **A10** — AR tie-out invariant accommodates non-AR-Control posting profiles. ~2-3d. Currently logs false-positive warnings; not blocking but noisy.

Do **not** start A3-A5 (architectural cleanup) until first-merchant signal validates the framing. Do **not** start Phase B until all of Phase A is green.

## Working notes for the agent

- Memory at `C:\Users\gezzo\.claude\projects\c--Users-gezzo-nxentra-app-nxentra-register\memory\` carries the user profile and recent project state. Read MEMORY.md early.
- The user is on Windows with bash via Git for Windows. Use Unix shell syntax in Bash tool calls.
- Test command: `cd backend && python -m pytest tests/<file>.py --tb=short` (don't use `-x` if surveying broadly; use it when iterating on a single failure).
- Frontend typecheck: `cd frontend && npx tsc --noEmit; echo "EXIT=$?"`.
- Pre-commit hooks (ruff + ruff-format) often reformat files; if commit fails on hook reformat, re-stage the modified files and retry.
- Default to small commits per ticket with the `Co-Authored-By: Claude Opus 4.7 (1M context)` trailer (HEREDOC pattern in CLAUDE.md).
- Push to origin/main only when the user confirms — they push after each commit individually.
- For UI changes, the user runs the dev server in the browser and verifies; agent does not have UI access.

## What "done" looks like for this session

Either:

(a) Both Steps 1 and 2 green, first-user invite sent, brief SESSION_LOG entry covering "the dry-run found N issues, all fixed, first user invited" + commit hash list.

(b) Bugs found, ticketed, fixed, dry-run re-run green, first-user invite sent.

(c) If the dry-run reveals a deeper architectural issue (e.g. webhook delivery flake, projection lag, dimension validation false-positive), file it as a new ticket and discuss with the user before fixing — could push the invite by another session.

The strategic line: **product spine before features, validation before invite, signal before refactor.**
