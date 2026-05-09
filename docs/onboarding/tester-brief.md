# Nxentra — Tester Brief

Thanks for putting real time into this. Below is everything you need to start. ~5-min read. No commitment beyond playing with it for a few days and telling me what felt wrong.

---

## What Nxentra is, in two sentences

Nxentra is a reconciliation-first accounting platform built for Shopify merchants. The core product question it answers: **"Did all my Shopify revenue actually make it to my bank account, and if not, where is it sitting?"**

## What stage we're at

Pre-release. The reconciliation engine works end-to-end on real data, but the polish is uneven and there are known rough edges. You're testing it before the first paying merchant goes deep — that's why your feedback is valuable now.

---

## The scenario I'd love you to work through

Pretend you're an e-commerce merchant — or their accountant — at the end of a normal week. You need to answer one question:

> _"Did all this week's Shopify revenue make it to my bank? If something's missing, where is it sitting?"_

If you can answer that question through the platform, the workflow works. If you get stuck, **where you get stuck is exactly the feedback I need.**

You don't need to follow specific steps — explore however feels natural. Operations people read software differently than engineers, and that's the eye I'm asking for.

---

## How to start

### 1. Register at [app.nxentra.com](https://app.nxentra.com)

On the registration screen, pick these values so the seed data lines up with your tenant config:

| Field | Pick |
|---|---|
| Email | any (e.g. yours with `+heba` alias) |
| Full name | anything (e.g. `TestStore`) |
| Company database name | `TestStore` (or anything; lowercase, no spaces) |
| **Functional currency** | **EGP** |
| Interface language | English |

### 2. Walk through the onboarding wizard

The wizard auto-launches after registration. Pick these:

| Wizard step | What to pick |
|---|---|
| **Business Type** | **Shopify Merchant** (Recommended — auto-configures the retail chart of accounts, Shopify clearing, payment processing fees, etc.) |
| **Company Profile** → Fiscal Year Start Month | **January** |
| **Company Profile** → Date Format | **DD/MM/YYYY** |
| **Company Profile** → Thousands / Decimal | leave defaults (`,` and `.`) |
| **Fiscal Year & Periods** | leave defaults (year `2026`, `12 (Monthly)`, current period auto-detected) |
| **Shopify Setup** | type your Shopify dev store domain (e.g. `mystore.myshopify.com`) → click **Connect to Shopify** → complete OAuth on Shopify → you'll land back on Shopify Setup with a green "Store Connected" checkmark |
| **Import Orders** | **Start fresh — only sync new orders from today** (don't backfill any history; the seed data we'll add later replaces this) |
| **Ready** | click **Go to Dashboard** |

If you don't already have a Shopify dev store, create one for free at [partners.shopify.com](https://partners.shopify.com) — takes ~5 minutes. **Don't create test orders manually** — I'll seed them for you in the next step so they match the CSV pack.

### 3. Ping me on WhatsApp

Once your tenant + Shopify store are connected, message me. I'll:
- Preload 8–10 test Shopify orders (prepaid + COD + 1 refund)
- Send you a CSV pack with three files:
  - `paymob_settlement.csv` (gateway payout statement)
  - `bosta_cod.csv` (courier COD reconciliation report)
  - `bank_statement.csv` (matching bank deposits)

### 4. Upload the three CSVs in order — and watch what changes

Upload one CSV at a time, then **observe the cause-and-effect across multiple pages**. Each upload triggers a chain: settlement event → projection runs → JEs post → reconciliation dashboard updates. You'll learn most by following that chain end-to-end after each upload.

**Before any upload** — visit these pages and note the baseline:
- `Finance → Reconciliation` — Shopify clearing balances on the left, 0.00 settled
- `Accounting → Journal Entries` — about 12 entries (sales invoices + 2 credit notes from the seed)
- `Setup → Dimensions` — should already have `SETTLEMENT_PROVIDER` dimension with provider codes (Paymob, Bosta, etc.) auto-created during onboarding

**Upload 1: Paymob settlement**
- Go to `Finance → Import Settlements`
- Drop `paymob_settlement.csv` into the **Paymob** uploader (left side)
- Click **Import Paymob CSV**

What to check after:
- `Finance → Reconciliation` — Paymob's "Settled" column jumps; "Open Balance" drops. You may see a `Review` badge next to "Paymob Accept" — flag whether the badge is self-explanatory or confusing.
- `Accounting → Journal Entries` — 4 new JEs with memos like *"Settlement: Paymob batch PAYMOB-BATCH-..."*. Click one to see the JE structure (DR Expected Bank Deposit + DR Fees + CR Paymob Clearing).

**Upload 2: Bosta COD settlement**
- Same page (`Finance → Import Settlements`)
- Drop `bosta_cod.csv` into the **Bosta** uploader (right side)
- Click **Import Bosta CSV**

What to check after:
- `Finance → Reconciliation` — Bosta's settled jumps. **You'll likely see a red warning banner** ("Bosta clearing is negative") — that's intentional in the test data: one settlement line references an order that doesn't exist in the system. Tell me whether the warning makes sense or feels alarming.
- `Accounting → Journal Entries` — 4 new Bosta settlement JEs.

**Upload 3: Bank statement** — `Accounting → Bank Reconciliation → Import Statement`

Form fields:
- **Bank Account**: pick **`11000 — Cash and Bank`** (don't pick Shopify Clearing or Expected Bank Deposit — those are intermediate accounts; the deposit lands in your actual bank account)
- **Statement Date**: set to the latest date in the CSV (e.g. 2026-05-09)
- **Period Start / Period End**: span the dates in the CSV (e.g. 2026-04-30 to 2026-05-09)
- **Currency**: should default to EGP — confirm
- **Opening Balance / Closing Balance**: leave at 0

Then upload the file:

1. Choose `bank_statement.csv` from the file picker
2. Click the **"Map columns"** button (the import doesn't auto-start — this opens the column-mapper dialog)
3. The dialog shows what we auto-detected for each column (Date / Description / Amount / Reference / Debit / Credit) plus a date format. **Trust the auto-detection** — it sniffs the first row of your file. If you override the date format and the import fails ("Parsed 0 lines"), the error message will tell you to revisit the mapping.
4. Click **"Parse with these columns"**
5. Review the parsed-lines preview at the bottom — does it look right? (Date, description, amount, sign of amounts)
6. Click **"Import N lines"**

You'll land on the bank statement detail page with all the imported lines marked **Unmatched**. Don't click "Complete Reconciliation" yet — first:

7. Click the **"Auto-Match"** button at the top of the page. The system tries to match each bank deposit against the settlement JEs you posted earlier. Some will match cleanly (status: `Auto`), some will fall into a **Needs Review** queue (matched within tolerance but the bank received a different amount than expected — e.g. a short-payment), and some will stay **Unmatched** (bank fees, mystery transfers, etc.).

   What to check after Auto-Match:
   - `Accounting → Journal Entries` — new "Bank deposit clearance" JEs (DR Cash and Bank / CR Expected Bank Deposit). One JE per matched bank line.

8. Now go to `Finance → Reconciliation`. Stage 3 (Bank Match) shows the result. If anything is in **Needs Review** queue (top of page, above Stage 1), you'll see a row with the difference + a **"Pick a reason..."** dropdown. Pick the reason that fits ("Extra gateway/courier fee", "Bank charge", "Chargeback", "Rounding", etc.) and click **Resolve**.

   What to check after Resolve:
   - `Accounting → Journal Entries` — a new adjustment JE (memo: *"Reconciliation difference: batch ... — <reason>"*) drains the residual.
   - The narrative banner at the top updates — drops the "1 bank deposit matched within tolerance" sentence.

**Only after Auto-Match + Needs Review are clean** should you go back to the bank statement page and click "Complete Reconciliation" — that's the final closeout button that locks the period.

### 5. Open the Reconciliation dashboard one more time

`Finance → Reconciliation`. Now try to answer the merchant's question end-to-end:

> *"Did all my Shopify revenue make it to the bank? If anything's missing, where is it sitting?"*

The "Tell me the story" banner at the top of the page should give you a one-sentence answer. If it does — the workflow works. If you can't make sense of what it tells you, or you have to dig through three drilldowns to feel confident, that's exactly the feedback I need.

---

## Other useful screens

The 3 menu paths above are what the scenario uses. If you want to poke around:

- **Journal entries (audit trail of every transaction)** → `Accounting → Journal Entries`
- **Customer / vendor balances** → `Accounting → Customers` / `Vendors`
- **Chart of Accounts** → `Accounting → Chart of Accounts`
- **Shopify orders** → `Shopify → Orders` (or `Records`)

If something's not where you expect it to be, that's worth flagging — discoverability is part of the test.

---

## What to flag as you go

- **Rough edges** — anything broken, slow, ugly, or confusing
- **Missing flows** — "I expected X to be possible here and it wasn't"
- **Wrong language** — copy that doesn't match how merchants/accountants actually talk
- **Workflow mismatches** — "no real merchant would do it this way"
- **Trust issues** — anything that made you hesitate before clicking, or wonder if the number was right

## What NOT to worry about

- Pixel-perfect polish (will come later)
- Missing features that don't block the scenario above
- Performance on huge datasets
- Mobile layout (desktop-first for now)
- **`Records → Items` will be empty.** The seed creates orders + invoices but doesn't auto-populate the product catalog (real Shopify webhooks do; the seed bypasses that path). Don't read this as a bug; just skip the Items page for this test.

---

## How to give me the feedback

Whatever's easiest for you — pick one:

- **Comments on this doc** if I share it as a Google Doc (best for inline notes as you read)
- **A WhatsApp voice note** as you go ("just hit X and it was confusing because Y")
- **A short list** at the end ("3 things that surprised me, 5 things that felt missing")
- **The call** when you've formed an opinion — 15-20 min

No formal report. The rougher the notes, the better.

---

## Time expectation

Around 1–2 hours of testing spread over a few days. No deadline. No pressure to use it daily.

## How to reach me

- **WhatsApp:** [+20-115-638-4000]
- **Email:** mohamed.algazzar@gmail.com

For anything that blocks you (can't sign in, OAuth fails, page won't load) — WhatsApp, fastest. For everything else, just save it for the wrap-up.

---

## A note on data privacy

It's a test environment. Data lives in a managed PostgreSQL database with row-level isolation — no other Nxentra user can see your data. If you want me to wipe your tenant when you're done, I'll do it within 24h of your request.

---

Looking forward to your read.

— Mohamed Algazzar
Founder, Nxentra
