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

### 4. Upload the three CSVs in this order

1. Paymob settlement → `Finance → Import Settlements`
2. Bosta COD report → `Finance → Import Settlements`
3. Bank statement → `Accounting → Bank Reconciliation → Import Statement`

Each upload walks you through a column-mapping step before parsing.

### 5. Open the Reconciliation dashboard

`Finance → Reconciliation`. Try to answer the merchant's question: *"did all my Shopify revenue make it to the bank?"* If you can answer it through the platform, the workflow works. If you can't, where you got stuck is the feedback I need.

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
