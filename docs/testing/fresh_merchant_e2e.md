# Fresh-Merchant E2E Test Script (release gate)

**Purpose:** tick-through validation of the complete merchant funnel on a **fresh Nxentra company + new Shopify development store with no demo seeds**. This is the gate for the App Store "Make fully visible" flip and the reusable pre-release regression script.

**Grounding:** every expected result below was mapped from code (8-surface grounding workflow, 2026-07-05, head `4487edf`). Items marked **[verify]** are expectations the code reading could not fully pin — treat a mismatch there as "investigate", not automatically a bug.

**Protocol:** one step at a time; do not advance until the current step's Expected column is confirmed or the deviation is logged. Log deviations b74379-style: P1 money-wrong / data-loss · P2 blocks the funnel · P3 cosmetic/confusing · P4 note. Fix P1/P2 same-session (adversarial review on financial paths), ledger the rest.

**Abort criterion:** any async wait that exceeds 2× its stated window → screenshot `/settings/system-health`, stop the step, record it. (Testers without droplet SSH have no in-app way to force a projection sweep — that fact itself is a finding if it bites.)

---

## Environment preconditions (confirm once before Run 1)

| # | Check | Expected |
|---|-------|----------|
| E1 | Target host | Production droplet `app.nxentra.com`, main = `4487edf` or later |
| E2 | Celery worker + beat running (`pm2 status` on droplet) | nxentra-api / nxentra-celery / nxentra-celery-beat all online |
| E3 | Projection catch-up beat | `process_all_projections` scheduled (droplet observed ~10 min; all "wait for sweep" steps below assume ≤10 min) |
| E4 | Stripe flags | `STRIPE_CANONICAL_PAYOUT_READS=True`, `STRIPE_CANONICAL_VERIFIED_READS=True` (both live since 2026-07-04/05) |
| E5 | `BETA_GATE_ENABLED` not set/False on droplet | signup auto-approves after email verification; if a yellow "Pending Approval" screen appears at step 4, this flag is on — approve via admin and log it |
| E6 | SMTP configured on droplet | verification emails actually arrive (console backend is dev-only) |
| E7 | Registration throttle awareness | repeated signups from one IP can 429 ("Too many registration attempts") — space out re-runs |

---

## Part 0 — Prep (Shopify side, done before touching Nxentra)

| # | Action | Expected |
|---|--------|----------|
| P1 | Partners/Dev dashboard → Create store: type **Dev**, plan **Basic**, "Generate test data" **unchecked**, feature preview **unchecked** | Store created, empty (no products/orders/customers) |
| P2 | Settings → General: business country **Egypt**; **Store currency = EGP**; Time zone **Cairo (GMT+02/03)** | All three saved before any sale (currency locks after first sale) |
| P3 | Settings → Payments → activate **(for testing) Bogus Gateway**; under Manual payment methods create a **custom** method named exactly **`Cash on Delivery`** — do **NOT** use Shopify's preset "Cash on Delivery (COD)" | Both available at checkout. The name matters: the COD-courier projection branch matches the normalized gateway string `cash_on_delivery` **exactly**; the preset's "(COD)" suffix normalizes to `cash_on_delivery_cod`, which silently bypasses Bosta routing (verified `shopify_connector/projections.py:1112-1123`, `tests/test_settlement_provider.py:41`) |
| P4 | Settings → Shipping: ensure a **free shipping** rate covers Egypt. Settings → Taxes: confirm **no tax** is collected for Egypt | Keeps order totals exactly = item prices (deterministic numbers below) |
| P5 | Online Store → Preferences: note the storefront password (dev stores are password-protected) | Can open the storefront for checkout |
| P6 | Do **not** install any app yet; do not create products/orders yet | Store stays untouched until step 15 |

Run 1 store used in this script: `gazzar-store-yxfymewq.myshopify.com` (substitute yours).

---

## Part 1 — RUN 1: modal Egyptian merchant (EGP store on EGP books)

### A. Signup & email verification

| # | Action | Expected |
|---|--------|----------|
| 1 | Open `https://app.nxentra.com/register` in a fresh browser profile | Heading **"Get started"**; fields: Email, Full name, Phone, Password, **Company database name** (max 10 chars, single word), **Functional currency** select (default USD), Interface language, ToS checkbox; button **"Launch Workspace"** |
| 2 | Negative checks: try a database name with a space / >10 chars; short password; unchecked ToS | Inline client-side errors: "Use a single word with no spaces" / "Maximum 10 characters" / "Password must be at least 8 characters" / ToS error. No API call fires |
| 3 | Fill valid values. **Functional currency = EGP — this is the one unrecoverable choice in the whole flow** (never shown again; wrong = restart with a new signup). Email: use a `+e2e1` Gmail alias. Click Launch Workspace | Redirect to `/verify-email?...&sent=true`: **"Check Your Email"** + resend link (60s cooldown). Backend: company created with default=functional=EGP, 8 system CoA accounts seeded (11000/12000/21000/32000/33000/49000/59000/59500) |
| 4 | Negative check: try logging in at `/login` **before** clicking the email link | Silent redirect back to `/verify-email?email=…` (403 `email_not_verified` under the hood; no toast on the login form) |
| 5 | Open the verification email link | Spinner → **"Email Verified!"** + "Continue to Login". Re-clicking the used link later shows "Verification Failed" — that's the one-time token, **not** a bug |
| 6 | Log in | No company chooser (single membership). Because onboarding is incomplete + role OWNER you are pushed **straight into `/onboarding/setup`** — you do not see the dashboard first |

### B. Onboarding wizard

| # | Action | Expected |
|---|--------|----------|
| 7 | Observe wizard step 1 | Progress pills: **Business Type · Company Profile · Fiscal Year · Shopify Setup · Import Orders · Ready**. "Shopify Merchant" card pre-selected with "Recommended" badge |
| 8 | Keep Shopify Merchant → Next. Step 2 Company Profile | Fields: Data entry fields (English only default — Arabic inputs hidden per A138), **Company Name** (prefilled with the short DB name — set the real display name), **Fiscal Year Start Month**, number/date formatting with live preview. **No currency field anywhere in the wizard** (locked from signup). **Leave Fiscal Year Start Month = January** — changing it past the current month turns later steps into period blockers |
| 9 | Next. Step 3 Fiscal Year | FY defaults to current year, 12 (Monthly) periods, current period preselected. Keep defaults → 12 OPEN monthly periods Jan–Dec will be created at Finish |
| 10 | **A6 symptom check** (optional, 2 min): click "Complete later", visit `/dashboard` | Banner **"Complete your setup"** + "Continue Setup" button. In a **new browser tab/session** the first `/dashboard` visit auto-bounces you into the wizard (per-session sessionStorage guard). If it loops on every refresh **within one session** → regression, log P2 |
| 11 | Continue Setup → advance back to Step 4 **Shopify Setup**. **STOP — do not use the wizard's Connect form.** Leave this tab open on this step | Card "Connect Your Shopify Store" with Store Domain input + footer "You can also skip this and connect later from Settings → Integrations." We install via the App Store instead (the real funnel) — next section |

### C. App Store install (the real funnel) + link + finish wizard

| # | Action | Expected |
|---|--------|----------|
| 12 | In the browser that's logged into the **dev store's** Shopify admin, open **`https://apps.shopify.com/nxentra-sync`** and click **Install** | Listing loads (app is live, limited visibility = direct URL works). Shopify shows the consent screen: **read-only scopes only** — read_customers, read_discounts, read_fulfillments, read_inventory, read_locations, read_orders, read_all_orders, read_products, read_returns, read_shopify_payments_accounts, read_shopify_payments_payouts. No write scopes |
| 13 | Approve the install. Watch where you land — **two valid branches** (2026-07-06 run: Branch A fired; consent renders as three grouped access categories — customer / staff / store data — not a raw scope list): | **Branch A (embedded launch):** app iframe opens inside Shopify admin → "Connecting Shopify" card → amber alert **"No Nxentra account is connected to \<shop\> yet."** + button "Open Nxentra" → login inside the iframe. **⚠ F3 (P2, found live 2026-07-06): if the company is MID-ONBOARDING, this branch dead-ends** — login lands on `/shopify/settings`, which ModuleGuard blocks ("Module Not Enabled … Contact your administrator" — shown to the owner mid-signup) because `shopify_connector` only gets enabled at Finish Setup. Recovery: connect from the wizard's own Shopify Setup step (its OAuth callback handles onboarding-incomplete correctly). For a company that HAS finished onboarding, the branch proceeds: `/shopify/settings` → type domain → Connect → OAuth (instant) → connected. **Branch B (finalize page):** browser redirected to `/shopify/finalize-install?handle=…` → (login if asked) → card "Completing Shopify Connection" → "Connected to \<shop\>." |
| 14 | After connect, on `/shopify/settings` | Toast **"Shopify store connected successfully!" / "Initial sync started — orders, products and payouts will appear shortly."** Connected Store card: domain in mono, Status **Active** (green dot), Last Sync **"Never"** (no polling — it updates only after the first sync completes AND you refresh) |
| 15 | Return to the wizard tab (still on Shopify Setup), refresh the page and navigate back to the Shopify Setup step | Green **"Store Connected"** card with your domain (verified: the wizard fetches active stores on mount, so out-of-band linking is detected). **A7 symptom check:** if the wizard instead dumps you back on **Fiscal Year**, that's the A7 regression — log P3 and manually advance |
| 16 | Next → Step 5 **Import Orders**: pick **"Start fresh — only sync new orders from today"** (store is empty) → **Finish Setup** | Wizard advances to **"You're All Set!"** Backend (atomic): FY2026 + 12 OPEN periods; retail CoA (13000 Inventory, 41000 Sales Revenue, 51000 COGS, 53000 Payment Processing Fees, 57000 Office & General, …); Shopify GL accounts **11500 Shopify Clearing, 11600 Expected Bank Deposit, 41100 Sales Discounts, 41200 Sales Returns / Failed Delivery, 52100 Chargeback Expense** + module account mappings; modules enabled: shopify_connector, sales, purchases, inventory. **Note: bank_connector and stripe_connector are deliberately force-disabled** — re-enabled later at steps 40/45+ |
| 17 | Click "Go to Dashboard" | Dashboard loads with **no** "Complete your setup" banner. Cards all EGP 0.00; Reconciliation widget shows "—" / "No bank statements imported yet"; Recent Activity "No recent activity". Revisiting `/onboarding/setup` now redirects to `/dashboard` |
| 18 | `/shopify/settings` → **Cash on Delivery Courier** card: pick **Bosta** → Save. Then read the Settlement Provider Routing card | COD Courier saved. Routing card lists bootstrapped providers — paymob, paypal, shopify_payments, manual, bank_transfer, bosta, unknown, **Bogus Gateway (Shopify test)** — all active, none "Needs review", all posting to 11500 Shopify Clearing. **Set COD courier BEFORE any COD order arrives** or those orders get a lazy `pending_cod_setup` provider flagged for review |
| 19 | Negative-degradation check: click **Sync Payouts** | Neutral (not red) toast: **"Shopify Payments isn't available on this store. Enable Shopify Payments in the store admin to start syncing payouts."** This is the A120 regression guard — a red "Failed to sync" toast here is an instant P1 |

### D. Products & items

Create in Shopify admin (Products → Add product), all with **Track quantity OFF**:

| Product | SKU | Price | Cost per item |
|---|---|---|---|
| Classic Tee — Black | `TEE-BLK` | 500.00 | 200.00 |
| Coffee Mug | `MUG-01` | 250.00 | 100.00 |
| Custom Bracelet | *(no SKU)* | 150.00 | *(blank)* |

| # | Action | Expected |
|---|--------|----------|
| 20 | Create the 3 products, then `/shopify/settings` → **Sync Products** | Toast **"Product sync complete: 2 created, 0 linked, 0 updated"** — the no-SKU Bracelet is **skipped by design** (counted in skipped, no Item). Costs are already EGP (store=books currency): no FX involved |
| 21 | `/accounting/items` | Exactly 2 rows: `TEE-BLK` / `MUG-01`, names from product titles, Type **Inventory**, Sales A/C 41000, Unit Price 500.00 / 250.00, **Qty 0** (stock ledger empty — correct), Status Active |
| 22 | **A127 check:** open TEE-BLK's edit page; wait for load; click nothing; then change Unit Price to 501 and Save; reopen | Single spinner holds the whole form until item+accounts+tax codes all load — account selects show real values, never a transient "None". Save disabled until dirty ("No changes to save"). After saving price-only, **all GL account fields survive**. Set price back to 500 |

### E. Orders → ledger

Place these storefront orders (password from P5), paying with **Bogus Gateway, card number `1`**, any test customer/address in Egypt, free shipping. Do them **one at a time**, verifying each before the next. After each order: wait ~30–60 s, then `/shopify/orders` → **Refresh** (no live polling — this is expected behavior, not a bug).

| # | Action | Expected |
|---|--------|----------|
| 23 | **O1 = #1001: 2 × Classic Tee = 1,000.00 EGP**, pay with card `1` | Row #1001 appears: Financial Status `paid`, Sync Status grey **Received** → green **Processed** (seconds, Refresh again if needed), Journal Entry link **JE-000001**. Open it: POSTED, memo "Sales Invoice INV-000001", lines **DR 11500 Shopify Clearing 1,000.00 / CR 41000 Sales Revenue 1,000.00**, clearing line tagged SETTLEMENT_PROVIDER = Bogus Gateway (Shopify test), CHANNEL=SHOPIFY dimension present. `/accounting/sales-invoices` shows INV-000001 referencing #1001 |
| 24 | **O2 = #1002: 1 × Coffee Mug = 250.00**, pay card `1` | INV-000002 + JE: DR 11500 250 / CR 41000 250 |
| 25 | In Shopify admin **fulfill #1002** | fulfillments/create webhook → **COGS JE books synchronously**: memo "Shopify COGS: #1002 (Fulfillment …)", **DR 51000 COGS 100.00 / CR 13000 Inventory 100.00** (weighted-average cost from the synced 100.00; negative stock force-allowed). If cost had been blank at sync time this JE would be silently absent — that's the known cost-before-sync rule |
| 26 | **O3 = #1003: 1 × Classic Tee + 1 × Custom Bracelet = 650.00**, pay card `1` (**A9 no-SKU probe**) | Order books normally: INV-000003 + JE DR 11500 650 / CR 41000 650 — **no crash on the null-SKU line** (b74379 class). An Item is auto-created at order time with synthetic code **`SHOP-<variant_id>`**, name "Custom Bracelet" — check `/accounting/items` now has 3 rows. PRODUCT dimension simply omitted for the no-SKU line |
| 27 | Fulfill #1003 | COGS JE for the Tee line only (**DR 51000 200 / CR 13000 200**). The Bracelet line produces **no COGS** (unmatched `no_sku`) and no visible error anywhere — known gap, log as P4 evidence for A9's ledger entry |
| 28 | **O4 = #1004: 1 × Classic Tee = 500.00**, pay card `1`. Then in Shopify admin: **Refund → custom amount 100.00, no restock** | INV-000004 + JE (500). Then refunds/create → **CN-000001** against INV-000004, JE memo "Credit Note CN-000001 (ref: INV-000004)": **DR 41000 100 / CR 11500 100**, clearing line tagged with the order's provider (Bogus) |
| 29 | **O5 = #1005: 1 × Coffee Mug = 250.00**, pay card `1` → **fulfill it** → then **full refund 250.00 WITH "Restock items" checked** | INV-000005 + JE (250); COGS JE (100); then **CN-000002** + JE (DR 41000 250 / CR 11500 250) **plus a second restock JE** "Shopify restock: Order #1005 (Refund …)": **DR 13000 100 / CR 51000 100** |
| 30 | **O6 = #1006: 1 × Coffee Mug = 250.00, pay with Cash on Delivery** at checkout (do NOT pay). Refresh `/shopify/orders` | Row #1006 with Sync Status **"Pending Capture"** and **no** Journal Entry link — COD orders are a metadata stub until paid. No invoice exists yet |
| 31 | In Shopify admin, open #1006 → **Collect payment → Mark as paid** ("courier collected") | orders/paid arrives → INV-000006 + JE DR 11500 250 / CR 41000 250. Clearing line tagged **SETTLEMENT_PROVIDER = Bosta** — but only because P3 named the method exactly `Cash on Delivery` (exact-match branch). **If instead a new "Needs review" `cash_on_delivery_cod` row appears in Settlement Provider Routing**, the order used a differently-named method — that's the known exact-name gap (log P2/P3: COD courier routing silently not applied), not a Bosta-setting failure. Record the order payload's `payment_gateway_names` value either way |
| 32 | `/accounting/journal-entries` — walk the tabs | Default tab **Posted**. **Reversed** tab: empty for now. All JEs from above present. Open JE-000001 → chip shows no "Foreign" pill (EGP=EGP). Drafts tab empty |
| 33 | `/reports/trial-balance` | Footer **"Balanced"** (green). Sanity: 41000 credits 2,900.00 vs 350.00 refund debits (net 2,550.00); 11500 net debit **2,550.00** at this point (2,900 sold − 350 refunded, nothing settled yet); 51000 net debit **300.00** (COGS 100 + 200 + 100, minus 100 restock); 13000 net credit **300.00** (negative stock — expected, no opening inventory). Record actuals if they differ and reconcile before continuing |
| 34 | `/dashboard` | **Total Revenue = EGP 2,550.00** (2,900 sold − 350 refunded). Recent Activity lists the JEs. Reconciliation widget still "—" (bank-only — correct) |

### F. Reconciliation page — first read (before settlements)

| # | Action | Expected |
|---|--------|----------|
| 35 | Finance → **Reconciliation** (`/finance/reconciliation`) | Header "Reconciliation / **Where is my money?** — across Shopify, gateways, couriers, and the bank". Narrative reads exactly: **"Bogus Gateway (Shopify test) + Bosta say 2,900.00 EGP sold. 350.00 has been refunded to customers — no settlements imported yet; 2,550.00 is still expected from providers."** (with refunds > 0 the refund clause carries the "no settlements imported yet" text; channel order is bogus before bosta — dimension-code sort). **Money Bridge**: Sold 2,900.00; segments Settled 0 / Refunded 350.00 / Still expected 2,550.00; "Reached the bank: 0.00". **"What to do next"** amber panel: Bogus Gateway row "2,300.00 open" with hint "Awaiting payout sync", Bosta row "250.00 open" with **"Import Bosta settlement"** link. Stage 1 table: Bogus (Expected 2,650, Refunded 350, Open 2,300), Bosta (Expected 250, Open 250). Stage 2: tiles 0 / 0.00, no rows, prompt rows for the owed providers. Stage 3: tiles 0/0/0 |
| 36 | Expand the Bogus Stage-1 row → Orders tab → click **Trace** on #1001 | Money trace 3-step list: "1 · Sale" populated, "2 · Settlement — not settled yet", "3 · Bank — not banked yet" |

### G. Settlement CSV import (Paymob + Bosta)

Build two CSVs from the templates in Appendix A. **Critical:** the `order_id` column must contain each order's **Shopify internal numeric ID** (the long number in the admin order URL, e.g. `.../orders/6046628905119`) — **not** `#1001`. Display numbers will import fine but flag every batch "Needs review / order IDs not found" (that mis-keying is the A26 class).

- `gaz_paymob_001.csv`: batch **GAZ-PMB-001**, date = today, rows: O1 gross 1000 fee 30 net 970; O3 gross 650 fee 20 net 630. Totals: gross 1,650 / fees 50 / net 1,600. currency EGP.
- `gaz_bosta_001.csv`: settlement_id **GAZ-BST-001**, date = today, one row: O6 collected 250, courier_fee 20, net_due 230, status `settled`.

| # | Action | Expected |
|---|--------|----------|
| 37 | Finance → **Import Settlements** → Paymob card → choose `gaz_paymob_001.csv` → **Import Paymob CSV** | **JEPreviewModal** opens (nothing posted yet): "Review paymob settlement import", "**1 batch, 1 journal entry will be created**" (modal pluralizes; only the Post button uses the fixed "Post 1 JEs"), Gross 1,650.00 / Fees 50.00 / Net 1,600.00, period badge "July 2026 (1 JE)" OPEN, batch row **"Will post"** with **no orphan warning** (real internal IDs). Click **Post 1 JEs** → toast "Imported 1 batch(es) from paymob." JE posts **immediately** (this projection runs inline): DR **11600 Expected Bank Deposit 1,600** / DR **53000 Fees 50** / CR **11500 Clearing 1,650** (tagged PAYMOB), memo "Settlement: Paymob batch GAZ-PMB-001 …" |
| 38 | Same for Bosta card with `gaz_bosta_001.csv` | Preview 1 batch → Post → JE: DR 11600 **230** / DR 53000 **20** / CR 11500 **250** (tagged BOSTA) |
| 39 | Idempotency: re-upload `gaz_paymob_001.csv` | Preview shows the batch with a **"Duplicate"** badge, "0 journal entries will be created", **Post button disabled**. No double-posting possible |
| 40 | Back to `/finance/reconciliation` → Refresh. Read Stage 2 and the narrative | Tiles **immediately**: "Settlements posted **2**", "Net to bank **1,830.00**". The payout **ledger rows appear only after the periodic payments-projection sweep (≤10 min)** — expanding too early can 404 ("Detail unavailable.") — wait and Refresh. Then: 2 rows — Paymob GAZ-PMB-001 (Gross 1,650 · Fees 50 · Net 1,600) and Bosta GAZ-BST-001 (250/20/230), both Status **"Posted"**, Entry linking the settlement JE. **Expected artifact (log P4, do not "fix"):** narrative now leads with red **"⚠ Paymob clearing is negative (−1,650.00 EGP)…"** — the dev store's card orders are tagged *Bogus*, so the Paymob settlement drained a bucket that was never funded. On a real merchant (real Paymob gateway) the buckets align; the warning firing here proves the negative-clearing detector works. Stage 1 now also shows a Paymob row with negative open balance. The **Bosta leg is the clean one**: Bosta row Expected 250 / Settled 250 / Open 0. No FX bridge anywhere (EGP on EGP — absence is the guard working) |

### H. Bank statement + auto-match + difference + complete

Prereq: Chart of Accounts → add **11201 "Bank — CIB"** (ASSET, active, non-header).
Build `gaz_bank_A.csv` (Appendix A): L1 credit **1,600.00** desc "Paymob payout GAZ-PMB-001" · L2 credit **205.00** desc "Bosta COD settlement GAZ-BST-001" (deliberately 25.00 short of the 230.00 net — inside the 15% tolerance) · L3 debit **15.00** "Bank service fee". All dated today.

| # | Action | Expected |
|---|--------|----------|
| 41 | Finance → **Bank Reconciliation** → Import Statement. Bank Account 11201, dates today/this month, Opening 0, **Closing 1,790.00**, Currency EGP (prefilled). Choose file → **Map columns** | Dialog auto-suggests: Date=transaction_date, Debit/Credit columns mapped, date format auto-detects **YYYY-MM-DD**. (The DD/MM force-pick only triggers on ambiguous NN/NN dates — A128 lives here, not on settlement import.) "Parse with these columns" → "Parsed 3 lines from CSV." → Import 3 Lines → toast "Imported 3 transactions.", statement page opens, **Matched: 0 / 3**, Difference red 1,790.00 |
| 42 | Click **Auto-Match** | Toast **"Auto-matched 2 of 3 lines."** (verified: the with-difference match counts). L1 → green **Auto** badge, Matched To = clearance JE ("Bank deposit clearance: settlement batch GAZ-PMB-001": DR 11201 1,600 / CR 11600 1,600). L2 → matched **with difference**: known display gap — on this page it still shows a red "Unmatched"-style badge but with a populated "Matched To" and no action icons (P3, known). L3 stays Unmatched. Status → In Progress |
| 43 | `/finance/reconciliation` → **Needs Review (1)** card | Row: provider bosta, batch GAZ-BST-001, Expected 230.00, Received 205.00, Difference 25.00 **SHORT PAID**. Pick reason **"Extra gateway/courier fee"**, note optional → **Resolve** → toast "Difference resolved for batch GAZ-BST-001." Backend posts adjustment JE: DR 53000 25 / CR 11600 25. Stage-2 Bosta row: "Needs attention" → **"Banked"** after resolve; Paymob row **"Banked"**. Known wart (P4, don't file): the matches-footer "need review" count can stay at 1 (ReconciliationLink stays NEEDS_REVIEW after resolution) while the queue is empty |
| 44 | Manual match the fee: create+post JE "Bank service fee" **DR 57000 Office & General 15 / CR 11201 15** dated today. Statement page → link icon on L3 | Candidate panel lists your JE's bank-credit line (−15.00); click Match → toast "Lines matched.", blue **Manual** badge |
| 45 | Statement header → **Complete Reconciliation** | Difference tile reads **0.00** → toast **"Statement reconciled successfully!"** Statement → Reconciled (green badge), all action buttons disappear. GL 11201 balance = 1,790.00 = closing. `/finance/reconciliation` Stage 3: 3 / 3 / 0, matches footer "Auto 2 · Manual 1". Dashboard Reconciliation widget now lights up (match % green ≥80) |
| 46 | (Optional) Statement B: one credit 300.00 "Unknown transfer", opening 1,790 closing 2,090 → import → **Exclude** the line (ban icon) → Complete | Toast **"Reconciled with difference of 300.00"** — completion is deliberately not blocked; the difference is recorded. Excluded lines count as cleared |

### I. Exception scan + system health + wrap

| # | Action | Expected |
|---|--------|----------|
| 47 | Settings → **Modules** → enable **Banking** (bank_connector). Then open `/banking/exceptions` **by direct URL** (not in sidebar) | Before enabling, the page shows "Module Not Enabled". After: "Exception Queue — Reconciliation exceptions requiring review", empty ("No exceptions found. All clear!") |
| 48 | Click **Scan Now** | Toast **"Scan complete — 0 new, 0 auto-resolved, 0 open"** — the queue stays empty and no "Exceptions to investigate" card appears. **This is pre-found finding F1 (P2), confirmed by code verification before the run:** the CLEARING_BALANCE detector filters `Account.role ∈ {SHOPIFY_CLEARING, STRIPE_CLEARING}`, but onboarding seeds 11500 (and Stripe's 11510) with `role=LIQUIDITY` — SHOPIFY_CLEARING exists only as a ModuleAccountMapping role — so this detector **can never fire on any real onboarded company** (`bank_connector/exceptions.py:513-518` vs `accounts/commands.py:3659`). With the 650.00 clearing residual it *should* have raised a MEDIUM exception. UNMATCHED_BANK_TX (reads only the bank_connector stack, ≥7d age) and UNMATCHED_PAYOUT (≥5d age) correctly cannot fire on a same-day run |
| 49 | ~~Resolve the exception~~ **N/A until F1 is fixed** (detector re-keyed to the mapping role). Once fixed+deployed, re-run the scan and expect: 1 new CLEARING_BALANCE, MEDIUM, 650.00; resolve it with a note → card disappears from `/finance/reconciliation` | — |
| 50 | Settings → **System Health** (Review tab → Control) | "Shopify Clearing Balance" check WARNS at 650.00 (expected pre-settlement), Trial Balance **Balanced**, Event Processing "All projections up to date", no pending entries. Screenshot for the run record |

**Run 1 final-state invariants** (all must hold): TB Balanced · Total Revenue 2,550.00 · 11500 clearing balance 650.00 · 11600 EBD balance 0.00 · 11201 bank 1,790.00 · Money Bridge: Sold 2,900 / Settled 1,900 / Refunded 350 / Still expected 650 / **Reached the bank: 1,830.00** (this figure = the settlement JEs' Expected-Bank-Deposit debits 1,600 + 230, **not** actual bank matches — it ignores clearance JEs, so it reads 1,830 even though only 1,805 physically landed and it doesn't move when the 25.00 difference is resolved).

---

## Part 2 — RUN 2: FX variant (USD store on EGP books + Stripe sandbox)

Fresh second company (email alias `+e2efx`), **functional currency EGP again**, new **US dev store** (currency USD, plan Basic, no test data, bogus gateway enabled, taxes/shipping zeroed as in P4). Wizard exactly as Run 1 steps 7–17. This store may also enable **Shopify Payments test mode** (US store) — do so if offered; note that **test-mode payouts never occur** (Shopify payout legs staying "Expected" is a documented dev-store limitation, not a bug).

| # | Action | Expected |
|---|--------|----------|
| 51 | Create product "FX Widget" SKU `FXW-01`, price **USD 100.00**, cost **USD 40.00**. **Do NOT add an exchange rate yet.** Place order **FX-O1 = 1 × FX Widget, USD 100, bogus card `1`** | Order row appears but stays **Received** with no JE. `/finance/exceptions` (Finance → Exceptions) shows an unresolved **DOWNSTREAM_FAILED** entry containing **"Missing USD→EGP exchange rate for \<today\>. Add the rate, then repost."** Nothing books at 1:1 — this is the FX-sweep guarantee. Note: this **stalls all later Shopify events for this company** until fixed (head-of-line, by design) |
| 52 | Settings → **Exchange Rates** → Add Rate: From USD, To EGP, Rate **48**, Effective **yesterday**, type Spot | Toast "Exchange rate saved." Within ~10 min (next projection pass) the stuck order **self-heals**: INV + JE post — JE currency USD, **DR 11500 Shopify Clearing 4,800.00 EGP** (amount_currency USD 100.00) / CR 41000 4,800.00, JE header stamped **1 USD = 48 EGP** (A142), detail page shows the blue **"Foreign"** chip. The exceptions entry stops recurring — mark it resolved |
| 53 | **Sync Products** now (after the rate exists) | Item FXW-01 cost = **1,920.00 EGP** (40 × 48). (Cost syncs use today's rate; blank-rate syncs store USD costs unconverted — that ordering is why the rate came first) |
| 54 | Place **FX-O2 = 1 × FX Widget USD 100** paid, fulfill it | INV+JE at 4,800 EGP as above, then COGS JE **DR 51000 1,920 / CR 13000 1,920** |
| 55 | Settings → Modules → enable **Stripe**. `/stripe/settings`: negative test first — paste an `sk_` key | Destructive toast: "That looks like a SECRET key (sk_…) … create a RESTRICTED key (rk_…) with Balance and Payouts set to Read." Input cleared |
| 56 | In the **Stripe SANDBOX** (`acct_1Tjfc8GWqh44OsSL` — **never the parent live account**): Developers → API keys → create restricted key, **Balance=Read + Payouts=Read only**. Paste `rk_test_…` → Connect | Toast "Stripe connected." / "Payouts will sync shortly." Connection card shows **Test** badge. CoA gains 11510 Stripe Clearing and **11610 Expected Bank Deposit — Stripe**; the PAYMENT_PROCESSING_FEES role maps to the **existing 53000** (the connect seed reuses any account named "Payment Processing Fees"; a new 53100 is minted only when no such-named account exists — not the case on a retail-CoA company). Account Mappings card shows 8 pre-selected roles |
| 57 | **Before any charge:** Stripe sandbox → Workbench → Webhooks → add endpoint `https://app.nxentra.com/api/platforms/stripe/webhooks/` with the 6 topics listed in the on-page "How to set up the webhook" box; copy `whsec_` → paste in "Webhook signing secret" → Save secret | Toast "Webhook signing secret saved."; badge flips to "Webhook secret configured". **Charges fired before this point are silently lost** (401/skipped) — resendable from Stripe → Events |
| 58 | Fire a test charge: sandbox Workbench / `stripe` CLI, **USD 200, source `tok_bypassPending`** (funds immediately available) | Webhook verified → `/stripe/charges` row: 200.00, **Fee 0.00 — by design** (real fees only arrive with a payout), status Processed. JE: **DR 11510 Stripe Clearing 9,600.00 EGP / CR 41000 9,600.00** (USD 200 @ 48), memo "Stripe order: …". `/stripe` tiles: Total Charges 1, Processing Fees "—" |
| 59 | Payout leg — **conditional, timebox 48 h**: set the sandbox payout schedule to **Daily automatic** (Settings → External payouts). **Never click the dashboard "Pay out" button** — manual payouts are unitemizable and skipped forever by design. Known risk: a non-activated sandbox may be unable to hold a test bank account and will then **never** pay out (this exact sandbox was C3-blocked before — do not fight it) | **If the automatic payout arrives:** payout.paid → debounced pull → one PAYMENT_SETTLEMENT_RECEIVED → settlement JE **DR 11610 net / DR \<mapped fee account — 53000 on this company\> real fees / CR 11510 gross**, JE in USD stamped @48; Stage-2 Stripe row with **"≈ \<net×48\> EGP"** under Net; expand → FX bridge line **"Statement amounts at the posted rate 1 USD = 48 EGP: ≈ gross … · fees … · net … EGP"**; click **"Verify against local records"** → toast "Verification run for payout po_…", outcome badge **"Reconciled clean"**, lines "✓ Matched ch_…". **If no payout by the timebox: skip to step 60's fallback — do not block the run** |
| 60 | **FX-bridge fallback (run regardless if 59 didn't complete):** build `fx_paymob_001.csv` — Paymob template, batch FX-PMB-001, currency **USD**, one row: FX-O2's real internal ID, gross 100, fee 3.20, net 96.80. Import it on the Run-2 company | Settlement JE posts in USD @48: DR 11600 4,646.40-equivalent lines (USD 96.80), DR 53000 (USD 3.20), CR 11500 (USD 100), header rate 48. After the sweep, Stage-2 row shows Gross/Fees/Net **in USD** with **"≈ 4,646.40 EGP"** on Net (tooltip "Posted at 1 USD = 48 EGP") and the expansion carries the **FX bridge** line. ≈-values may drift a cent from JE lines — reconstruction, not a bug |
| 61 | Bank the FX payout: statement CSV on the Run-2 company (account e.g. 11202), one credit **4,646.40** desc containing `FX-PMB-001` → import (currency **EGP**) → Auto-Match | Matches at confidence 100 (batch substring); clearance JE DR bank 4,646.40 / CR 11600. Stage-2 row → **Banked**, FX bridge still shown. Complete Reconciliation → 0.00 |
| 62 | **Parity gate (user runs on droplet, after the ≤10-min payments sweep):** `python manage.py payments_canonical_backfill` (report mode) | The company appears only if it has settlement events (step 60's import guarantees one). Its line is plain text: `events=… headers=… stripe_parity_ok=N verified_parity_ok=N …`; mismatches print as separate **red indented** `stripe parity:` / `verified parity:` / `reconstruct MISMATCH:` lines; the `[totals]` line is green only when all mismatch counters are 0. Run before the sweep and `header_missing` turns totals yellow — timing, rerun. **Any red parity line = P1** — except a verified-parity line accompanied by the command's own "report-only mode" NOTE (un-replayed reconciled event): rerun with `--apply` before judging. If step 59 produced a payout + Verify, expect `reconciled_events≥1` |

---

## Appendix A — CSV templates

Paymob (`gaz_paymob_001.csv`) — replace `<O1_ID>`/`<O3_ID>` with the **internal numeric IDs** from the admin order URLs:

```csv
settlement_id,settlement_date,payout_batch_id,gateway,order_id,gross_amount,gateway_fee,refund_or_chargeback_amount,net_amount,currency,status,notes
PMB-1,2026-07-05,GAZ-PMB-001,Paymob,<O1_ID>,1000,30,0,970,EGP,settled,O1 tee x2
PMB-1,2026-07-05,GAZ-PMB-001,Paymob,<O3_ID>,650,20,0,630,EGP,settled,O3 tee+bracelet
```

Bosta (`gaz_bosta_001.csv`):

```csv
settlement_id,settlement_date,courier,order_id,collected_amount,courier_fee,returned_uncollected_amount,net_due,currency,status,notes
GAZ-BST-001,2026-07-05,Bosta,<O6_ID>,250,20,0,230,EGP,settled,COD mug
```

Bank statement A (`gaz_bank_A.csv`):

```csv
bank_txn_id,transaction_date,description,reference,debit_amount,credit_amount,currency,bank_account,notes
BNK-001,2026-07-05,Paymob payout GAZ-PMB-001,GAZ-PMB-001,0,1600,EGP,CIB,full net
BNK-002,2026-07-05,Bosta COD settlement GAZ-BST-001,GAZ-BST-001,0,205,EGP,CIB,25 short - tolerance test
BNK-003,2026-07-05,Bank service fee,FEE-1,15,0,EGP,CIB,manual match test
```

Notes: settlement dates must be **ISO (YYYY-MM-DD)** — the DD/MM sniffer exists only on the bank import. The stock `test_data/*.csv` files reference order_ids 1001–1010 that only match after `seed_test_csv_pack` (not used in this run — fresh-funnel realism is the point).

## Appendix B — Expected artifacts & known-open ledger (log, don't file)

| Item | Class | Where it shows |
|---|---|---|
| **F1: CLEARING_BALANCE detector dead on all real companies** (filters `Account.role`, but seeds write `LIQUIDITY`; mapping-role key never checked) | **P2 — pre-found by code verification 2026-07-05** | step 48 scans 0/0/0 despite a 650.00 clearing residual; fix = re-key detector to ModuleAccountMapping roles |
| **F3: App-Store install mid-onboarding dead-ends at "Module Not Enabled"** — embedded no-connection → login lands on `/shopify/settings`, which ModuleGuard blocks until Finish Setup enables `shopify_connector`; copy says "contact your administrator" to the very owner who just signed up | **P2 — found live 2026-07-06 (step 13)** | fix candidates: embedded login should route onboarding-incomplete OWNERs to the wizard's Shopify step (mirror `views.py:287-291` callback behavior), or ModuleGuard should special-case incomplete onboarding |
| COD courier routing requires gateway to normalize to exactly `cash_on_delivery`; Shopify's preset "(COD)" name bypasses it | P2/P3 — probed at step 31 | Settlement Provider Routing grows a "Needs review" `cash_on_delivery_cod` row instead of tagging Bosta |
| Shopify Payments payouts never occur on dev stores | platform limitation | Shopify payout legs stay "Expected"; Sync Payouts says "isn't available on this store" |
| Bogus-gateway orders can't be settled by Paymob/Bosta CSVs | dev-store artifact | negative Paymob clearing + red narrative (step 40) — the detector working |
| **F9: no per-product revenue detail** — invoice posts ONE aggregate revenue line per order (`projections.py:919-946`); PRODUCT dimension tags the whole amount with the **first** line item's SKU (`projections.py:482-493`), so multi-product orders mis-attribute revenue (e.g. #1003: bracelet's 150 tagged as TEE-BLK). COGS side is already per-item — revenue-per-line is the missing half of per-product P&L | P3 enhancement — found live 2026-07-06 (step 26) | Totals correct; only product-level analytics affected. Fix = one invoice line per order line (own Item / Sales A/C / PRODUCT tag); needs design for discount+tax allocation and refund symmetry |
| **F10: Credit-note detail page leaks JE pk** — renders "JE #1690" instead of "JE-000007" (`credit-notes/[id].tsx:175` uses `posted_journal_entry_id`; `CreditNoteSerializer` at `sales/serializers.py:572` exposes only the pk while the LIST serializer has `journal_entry_number`). Same family as the PR #37 reversal-banner leak | P3 display — found live 2026-07-06 (step 29) | Fix: add `posted_journal_entry_number` to the detail serializer + render it. Sweep other detail pages for `#{...id}` renders while at it |
| **F11: Shopify dashboard has no refunds/returns visibility** — "Revenue (Processed)" tile shows gross sold (2,400) with no refund/discount breakdown; net truth (2,300) only visible on main dashboard/reconciliation. Also: refund CN auto-labels reason "Goods Returned" even for money-only refunds (no goods moved) | P3/P4 enhancement — user suggestion 2026-07-06 (step 29) | Candidate: add Refunded tile (or gross → net strip) to /shopify dashboard; derive CN reason from whether restock/line-items present |
| **F12: refund restock posts value but not quantity** — restock is a bare JE (`projections.py:1288` "kept as direct JE for now", `:1156` "until Phase 6"), while fulfillment issue uses `record_stock_issue` (`commands.py:2326`, StockLedgerEntry + InventoryBalance). Every restocked return: GL 13000 gets the value back, Qty on hand stays down → subledger diverges from GL by restock value; weighted-avg cost base wrong thereafter | **P2 — found live 2026-07-06 (step 32: mug −2 before AND after restock; GL −300 vs ledger −400)** | Fix: restock handler records a stock receipt (idempotent, same txn as JE) mirroring the issue path. Batch with F1 same-session, adversarial review |
| **F13: COD COGS/revenue timing mismatch** — fulfillment handler posts COGS with no financial_status gate (`commands.py:1950`ff) while COD revenue waits for Mark-as-paid → real COD sequence (ship → collect later) books cost and revenue in different periods when crossing month-end; refused parcels carry COGS with no sale until restock | P3 design — code-confirmed 2026-07-06 (step 33) | Candidate: defer COGS for unpaid COD orders until order_paid, or Goods-in-Transit account. Revenue-at-paid itself is CORRECT for COD (IFRS 15 control-at-door + Egyptian RTO rates) — do not "fix" that |
| **F14: no pending-COD exposure visibility** — between checkout and collection, outstanding COD only visible as "Pending Capture" in Shopify→Orders; no dashboard/recon tile totaling COD out for delivery | P4 enhancement — 2026-07-06 | Off-ledger operational tile ("COD awaiting collection: N orders / X EGP") on /shopify or reconciliation |
| **F15: "Tell me the story" narrative should be the money identity, not prose** — user (product owner) couldn't parse the banner during the run. Proposed: line 1 = equation "2,900 sold = 1,900 settled (1,830 to bank + 70 fees) + 350 refunded + 650 still expected"; line 2 (red, conditional) = anomaly alert (e.g. Paymob settled with no matching sales), since the net identity HIDES the +2,300/−1,650 split; tie "still expected" to aging for "missing" semantics | P3 UX — owner feedback 2026-07-07 (post step 45) | Rewrite the narrative builder to emit the identity + separate conditional alert line; keep totals from the same tiles so the equation always foots |
| **F16: System Health AR/AP tie-out false-positives on Shopify-mode companies** — compares posting-profile control (11500, drained by settlements) vs customer invoice balances (never drained; Shopify collects via clearing, not receipts): "AR Control 650 != Customer balances 2550, Difference −1900" = exactly the settled amount, forever | P3 — found live 2026-07-07 (step 50) | Exclude platform-clearing control accounts from the tie-out, or net settlement drains into the customer side for platform profiles |
| **F17: System Health clearing-balance sign flipped** — shows "Clearing balance: −650.00" for a +650 DR balance (check itself fires correctly — unlike F1's dead detector, this path works) | P4 — found live 2026-07-07 (step 50) | Fix sign convention in the health-check display |
| **F18: record_stock_receipt zero-crossing discards residual value** — when a receipt brings a negative balance exactly to 0, `stock_value = new_qty*avg` zeroes out any residual between issue cost and receipt cost (stays in GL + SLE cumulative, vanishes from InventoryBalance and future WAC math). Pre-existing arithmetic in `inventory/commands.py:399,410`; the F12 receipt newly routes every Shopify buy-1/return-1 (0→−1→0) through it. With the FX fix + JE anchoring the residual only arises from genuine cost-sync drift between sale and return | P3 — adversarial review 2026-07-07 | Fix in inventory commands (shared with purchase bills — own change): accumulate old_value+value_delta or post the crossing residual to a variance account |
| A6 | known-open UX | per-session wizard auto-bounce + banner (step 10) |
| A7 | fixed-in-code, watch for regression | wizard step after OAuth return (step 15) |
| A9 | known-open correctness | no-SKU: sync skips the product; order auto-creates `SHOP-<id>` item; **no COGS ever** (steps 20/26/27) |
| MATCHED_WITH_DIFFERENCE badge fallback | known P3 display gap | statement detail page (step 42) |
| ReconciliationLink stays NEEDS_REVIEW after resolve | known P4 wart | matches footer vs empty queue (step 43) |
| Charge Fee 0.00 / fees tile "—" until first payout | by design | /stripe (step 58) |
| Interface-language dropdown on /register is dropped server-side | known P4 | signup (step 1) |
| Re-clicking a used verification link says "Verification Failed" | by design | step 5 |

## Appendix C — Run log

For each step record: step #, PASS/FAIL, actual observed text/numbers when deviating, screenshot ref. Batch findings at the end of each Part: P1/P2 fixed same-session (adversarial review on financial-path diffs), P3/P4 → NEXT_TASKS.
