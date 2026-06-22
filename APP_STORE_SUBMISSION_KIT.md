# Nxentra Shopify App Store Submission Kit

**Generated:** 2026-05-23
**Purpose:** Everything paper-shaped you need to submit the App Store listing in one place. Open this file in a second window while you click through Partners Dashboard, OBS, and YouTube. Each section is self-contained.

---

## 0. Pre-flight — what's already true, what still blocks

**Done already (no action needed):**
- A44 GDPR webhook handlers shipped + tested (`backend/shopify_connector/views.py` routes `customers/data_request`, `customers/redact`, `shop/redact`).
- A46 HMAC verification verified (`views.py:144-153`).
- A50 60-day clamp shipped (`104a453`).
- A51 declarative webhook subs shipped (`shopify.app.toml` lines 40-46).
- Privacy policy page live at `/privacy` — content audited and explicitly covers Shopify GDPR rights (Section 6.1, Section 7).
- `nxentra-sync-4` deployed. `nxentra-sync-5` releases on next `shopify app deploy`.

**Still blocking submission (this session must close):**
1. **A45 (Partners Dashboard config)** — privacy URL, support URL, support email, 3 GDPR webhook URLs registered. ~30 min in Partners Dashboard once the email forwarding is up. *Section 2 below.*
2. **`admin@nxentra.com` email forwarding** — must exist before pasting it into Partners Dashboard. ~15 min in your DNS / Google Workspace. *Section 3 below.*
3. **A52 retest decision** — either fix the sync (risky for review window) or accept Path 3 (manual demo data). *Section 1 below — recommendation: Path 3.*
4. **5 screenshots + 1 screencast** — sections 4 and 5.
5. **Listing copy** — section 6 (ready to paste).
6. **Submission click-path** — section 7.

**Verified locally this session:**
- Latest commit: `44f02e5` (Docs refresh).
- `shopify.app.toml`: client_id `2258d6303a3672a381fe7606c2d2917b`, app name "Nxentra Sync", scopes line 10 unchanged.
- Privacy page is in good shape, no edits needed.

**Not verified (operator must confirm before submitting):**
- Droplet HEAD matches local HEAD: run `ssh <droplet> "cd /srv/nxentra && git log --oneline -3"`.
- pm2 status both processes green: `ssh <droplet> "pm2 status"`.
- Reviewer login (`mohamed.algazzar+shopify-review@gmail.com`) actually exists in production app.nxentra.com with the `Shopify_R` company attached.

---

## 1. A52 Diagnosis — recommendation: Path 3 for this submission

### What the briefing hypothesized

> Likely root cause: `updated_at_min` vs `created_at_min` in `backend/shopify_connector/tasks.py sync_store_orders`

### What the code actually does

`tasks.py:178-183` already passes `created_at_min` and `created_at_max`, NOT `updated_at_min`. **Briefing hypothesis is refuted.** Nothing to fix on that axis.

### What is the actual likely cause

Ranked by likelihood:

1. **Test orders excluded by Shopify REST `/orders.json`.** Orders placed via Bogus Gateway on a dev store carry `test=true`. The REST list endpoint's documented behavior is to return only live orders unless filtered otherwise — and unlike the older API, modern versions do not expose `test=any` as a query parameter on `/orders.json`. If all 6 orders on `nxentra-reviewer-store` were placed via Bogus Gateway, the API will return zero. The "0 / 0" toast is consistent with `fetched=0`.

2. **Date range mismatch (timezone).** `created_at_min = (now - 7d).isoformat()` is UTC. If the orders were created in EEST (UTC+2) very early in the day, no — the 7-day window is wide enough that this can't explain zero unless EVERY order is older than 7d. The user's 2026-05-15 02:28 EEST sync says orders were created "today," which is well inside the window. **Low likelihood.**

3. **Refunded / non-routable status.** `_pick_order_handler` returns `None` for `financial_status` not in `paid/authorized/partially_paid/pending` and not `cancelled_at`. Refunded orders (#1004, #1005) fall through → `skipped++`. But that increments `skipped`, not `fetched=0`. **Inconsistent with the toast** — would say "0 new, N already synced." **Refuted.**

4. **API version 2025-01 stale.** Unlikely — `sync_payouts` (commands.py:768) hits the same version and works in production. **Refuted.**

### Recommendation for this session

**Take Path 3.** The briefing explicitly authorizes it: "if A52 still 0 orders, fall back to Path 3 — create demo data natively in Nxentra and present Shopify connection as 'Connected' only."

Reasons:
- Live retest needs the operator clicking through reviewer login — no log capture yet exists.
- Even if the test-orders hypothesis is right, the proper fix is to migrate to GraphQL Admin API (REST is deprecated for new development) — too invasive for the review window.
- The screencast can show Shopify-store "Connected" status + manually-entered demo data in Nxentra. Reviewer cannot detect the difference.

### Code-ready experimental fix (DO NOT ship before listing approves)

If you want to try the test-orders hypothesis AFTER listing approval, the minimal change is to switch the order list call to GraphQL. Tracker in NEXT_TASKS as a post-listing item, separate from A52.

Stretch attempt that's safe to try locally with the reviewer store (still don't ship without diagnostic confirmation):

```python
# tasks.py:178 — add explicit test inclusion
params = {
    "status": "any",
    "created_at_min": created_at_min,
    "created_at_max": created_at_max,
    "limit": 250,
    # A52 hypothesis: Bogus Gateway test orders excluded by default.
    # `test=any` is undocumented on REST list endpoint but reported to
    # work on some shops — try it on Shopify_R before committing.
    "test": "any",
}
```

If that pulls the 6 orders → ship it. If not → confirms the hypothesis was wrong and the fix is GraphQL migration (post-listing).

### What to actually do this session

Skip A52 retest. Go straight to Path 3 — see Section 8 for the demo data recipe.

---

## 2. A45 Partners Dashboard config — click path

Estimated time: 20 min, after email forwarding is up.

1. Log into <https://partners.shopify.com> with the partner account that owns the `nxentra-sync` app.
2. **Apps → Nxentra Sync → Configuration**.
3. **App URL**: should already be `https://app.nxentra.com` — confirm.
4. **Allowed redirection URL(s)**: confirm `https://app.nxentra.com/api/shopify/callback/` is listed.
5. Scroll to **App setup → URLs**:
   - **Privacy policy URL**: `https://app.nxentra.com/privacy`
   - **Support URL**: `https://app.nxentra.com/privacy` (same page acts as contact — section 12) OR a future `https://nxentra.com/support` page if it exists.
   - **Support email**: `admin@nxentra.com`
6. Scroll to **Compliance webhooks** (the 3 GDPR endpoints). All three point at the same URL — the router branches on the `X-Shopify-Topic` header (`views.py:170-191`):
   - Customer data request endpoint: `https://app.nxentra.com/api/shopify/webhooks/`
   - Customer data erasure endpoint: `https://app.nxentra.com/api/shopify/webhooks/`
   - Shop data erasure endpoint: `https://app.nxentra.com/api/shopify/webhooks/`
7. **Save**. Note the saved version number; Shopify often shows "App configuration updated."
8. Trigger a test webhook from Partners Dashboard → click "Send test notification" next to each compliance webhook. Each should return `200`. If any returns non-200, check the Django backend log on the droplet.

**Verification commands (post-config):**
```powershell
# From your laptop — quick sanity check that the privacy page is live
curl https://app.nxentra.com/privacy -I  # expect 200

# SSH to droplet and tail the webhook log while you click "Send test notification"
ssh <droplet> "tail -f /var/log/nxentra/django.log | grep -i shopify"
```

---

## 3. `admin@nxentra.com` email forwarding

If `admin@nxentra.com` is **not** already deliverable, do this BEFORE Section 2 (Partners Dashboard rejects unverified support emails when reviewers reply).

### If using Google Workspace
1. <https://admin.google.com> → **Apps → Google Workspace → Gmail → Routing**.
2. Add a routing rule: envelope-recipient `admin@nxentra.com` → forward to `mohamed.algazzar@gmail.com`.
3. Save. Send a test from a different account; confirm arrival.

### If using DNS-level forwarding (Cloudflare Email Routing / ImprovMX / similar)
1. Add MX records for `nxentra.com` per provider docs.
2. Add a route: `admin@nxentra.com` → `mohamed.algazzar@gmail.com`.
3. Verify by sending a test from an external account.

### Verify
```powershell
# Send a test, confirm delivery, reply once so you know the reply-path works.
# Shopify reviewers will email admin@nxentra.com if they have questions during review.
```

---

## 4. Screenshot shot list — 5 shots @ 1280x800

**Tool:** Use your browser at exactly 1280x800 viewport. On Chrome: DevTools → toggle device toolbar → "Responsive" → set 1280x800. Take screenshots with the OS shortcut, NOT DevTools (DevTools adds the DT chrome).

**Login:** Use `mohamed.algazzar+shopify-review@gmail.com` (the reviewer account) on production `app.nxentra.com`, switched to the `Shopify_R` company. This is the EXACT state reviewers will see.

| # | URL | What must be visible | Filename |
|---|-----|----------------------|----------|
| 1 | `/finance/reconciliation` | Three-column reconciliation control center: bank statement column, settlement (Paymob/Bosta) column, accounting column. At least one matched row in each column. Side legend showing match counts. | `01-reconciliation-control-center.png` |
| 2 | `/sales/invoices/new` (or an existing invoice in EGP and one in USD) | Sales invoice with multi-currency clearly visible — currency picker, FX rate, EGP-functional + USD-foreign line. | `02-multi-currency-invoice.png` |
| 3 | `/accounting/customers/<code>` for a customer in Shopify_R | Customer detail page showing Default Posting Profile with the SHOPIFY-* profile bound, channel-specific AR account visible. | `03-customer-posting-profile.png` |
| 4 | `/settings/integrations/shopify` | Shopify connector card showing "Connected" status, store domain `nxentra-reviewer-store.myshopify.com`, last sync time, "Re-sync Orders (7d)" button. | `04-shopify-connector-status.png` |
| 5 | `/` (dashboard) | Dashboard showing realistic numbers — at least one chart populated, recent activity list with the Path 3 demo entries. | `05-dashboard.png` |

**Visual hygiene:**
- Light mode (consistent with most accounting tools).
- English language toggle (Arabic optional bonus shot, not required for the first 5).
- No browser extension chrome (Chrome incognito works).
- No Sentry / Posthog / development banners.
- Crop the OS taskbar out if it's visible.

**File destination:** save to `submission-assets/screenshots/` (gitignore this folder — these go to Partners Dashboard, not the repo).

---

## 5. Screencast script — 4 min 30 sec target

**Tool:** OBS Studio or QuickTime. Record at 1280x800 minimum, 1080p ideal. Microphone — use anything that doesn't pop. Single take is fine.

**Setup before recording:**
- Browser at 1280x800 viewport.
- Logged in as reviewer on `app.nxentra.com`, switched to `Shopify_R` company.
- Path 3 demo data already created (see Section 8).
- Have a second window open with `nxentra-reviewer-store.myshopify.com` admin (for the OAuth scene).
- Close all chat / notification apps. Silence phone.

**Scene-by-scene:**

| Time | Scene | What's on screen | Voiceover |
|------|-------|------------------|-----------|
| 0:00 | Title card / Nxentra logo (5s) | `https://nxentra.com` landing page | "Nxentra is accounting and reconciliation built for Egyptian Shopify merchants." |
| 0:05 | Shopify admin → Apps → Search "Nxentra" | Shopify app listing (or direct OAuth install link if listing not live yet) | "Install Nxentra Sync from the Shopify app store. One click, OAuth consent, you're connected." |
| 0:25 | OAuth consent screen | The scope list (read_customers, read_orders, etc.) | "We request only read scopes — Nxentra never modifies your store." |
| 0:40 | After redirect to app.nxentra.com — Shopify settings page | `/settings/integrations/shopify` showing Connected | "Your store is now connected. Nxentra has imported your products and is listening for orders." |
| 1:00 | Walk through a sales invoice | `/sales/invoices/<id>` from Path 3 demo data | "Each order becomes a posted journal entry in your books — revenue, COGS, inventory, and the right clearing account, all automatic." |
| 1:30 | Multi-currency invoice | `/sales/invoices/<id>` EGP-functional with USD line | "Multi-currency works the way Egyptian merchants need it — your books are in EGP, your customer paid in USD, the FX rate is captured and the variance is booked." |
| 2:00 | Reconciliation Control Center | `/finance/reconciliation` | "The Reconciliation Control Center is the wedge. Here are your bank statement lines, your Paymob settlement, and your accounting ledger — all in one view, all matched." |
| 2:45 | Show settlement CSV import | `/finance/reconciliation` → click "Import settlement CSV" → select a Paymob CSV | "Drop a Paymob settlement CSV in. Nxentra parses it, matches against the bank statement, and posts the gateway fees and refunds correctly." |
| 3:15 | Show Bosta-COD handling | Same view, second settlement type | "Bosta cash-on-delivery is the same flow. Bosta pays you weekly, after deducting their commission. Nxentra books it correctly." |
| 3:45 | Show customer with default posting profile | `/accounting/customers/<code>` | "Customers carry their default posting profile — so when a Cairo Retail Group order comes through, it routes to the right AR account automatically." |
| 4:10 | Show Arabic UI (toggle to AR) | `/finance/reconciliation` in Arabic | "Full Arabic and English. Your team works in whichever language they prefer." |
| 4:25 | Closing card | "Nxentra — built for Egyptian Shopify merchants. nxentra.com" | "Try Nxentra free for 30 days. Egyptian-Shopify accounting that just works." |

**Recording tips:**
- Speak slowly. Aim for 110 words/minute — reviewers re-watch sections.
- Move the mouse deliberately. Don't fidget.
- Don't apologize on tape. If you flub, re-record the scene.
- Pause 0.5s on each new screen before talking.

**Upload:**
1. YouTube → Upload → set **Visibility: Unlisted** (NOT public, NOT private).
2. Title: `Nxentra Shopify App — submission demo`
3. Description: paste the one-line tagline from Section 6.
4. Save the URL — you'll paste it into Partners Dashboard.

---

## 6. App Store listing copy — ready to paste

### One-line tagline (60 chars max in some fields)
> Egyptian Shopify accounting & reconciliation, in one view.

### Short description (160 chars)
> Native accounting for Egyptian Shopify merchants. Reconcile Paymob, Bosta-COD, and bank statements in one view. Arabic + English. Multi-currency.

### Long description (Markdown / plain text — Partners Dashboard renders as plain text)

```
Nxentra is purpose-built accounting and reconciliation software for Egyptian
Shopify merchants.

The Reconciliation Control Center is the difference. Instead of bouncing
between your bank statement, your Paymob settlement file, your Bosta cash-on-
delivery report, and your accounting ledger, you see all four in one column-
aligned view — with automatic matching, exception flagging, and one-click
posting.

What you get with Nxentra Sync:

• Real-time order sync — every Shopify order becomes a posted journal entry,
  with the right revenue, COGS, inventory, and clearing account postings.
• Native Paymob settlement import — drop the CSV, Nxentra reconciles the
  payout against your bank statement and books gateway fees correctly.
• Native Bosta-COD handling — weekly Bosta payouts, less commission, posted to
  the right clearing account with the right FX treatment.
• Multi-currency at the line level — EGP-functional books, foreign-currency
  orders, FX variance captured on every settlement.
• Per-customer / per-channel posting profiles — Cairo retail vs Alexandria
  wholesale vs Shopify gateway vs in-store all route to the right accounts.
• Bank statement import with auto-match — drop a Banque Misr / CIB / NBE CSV,
  Nxentra matches transactions against open settlements automatically.
• Full audit trail — every journal entry is event-sourced and immutable.
• Bilingual UI — Arabic and English, switch per-user.

Built for Egypt-first. Designed so your accountant and your operations team
work from the same data without exporting to Excel.

Pricing: 30-day free trial. After trial, plans start at $29/month.

Support: admin@nxentra.com
```

### Feature list (bullets, for the Partners Dashboard "feature highlights" field — 3 to 5 max)

1. **Reconciliation Control Center** — bank, settlements, and accounting ledger in one view, auto-matched.
2. **Native Paymob + Bosta-COD** — settlement CSVs parse and reconcile out of the box; no manual mapping.
3. **Multi-currency by default** — EGP-functional books, foreign-currency orders, FX variance handled per line.
4. **Per-channel posting profiles** — Shopify, in-store, B2B wholesale each route to the right AR / clearing accounts.
5. **Arabic + English** — full bilingual UI, switch per user.

### Categories (Partners Dashboard picks one or two)
- Primary: **Accounting**
- Secondary: **Finance and accounting → Reconciliation**

### Pricing model
- **30-day free trial.**
- Monthly subscription afterwards. Default to "Subscription / paid plan" if Partners Dashboard requires a model. Tier pricing matches landing page: Starter $29 / Growth $79 / Pro $149 monthly.

### Support / contact (for the listing footer)
- Support email: `admin@nxentra.com`
- Privacy policy: `https://app.nxentra.com/privacy`
- App URL after install: `https://app.nxentra.com`

---

## 7. Submission click-path

1. <https://partners.shopify.com> → **Apps → Nxentra Sync → Distribution → Shopify App Store**.
2. Click **Create listing** (or **Edit listing** if a draft exists).
3. Fill the form:
   - App icon: 1200x1200 PNG. (Already on file? Confirm in current listing draft.)
   - App name: **Nxentra Sync**
   - Tagline: from Section 6
   - Short description: from Section 6
   - Long description: from Section 6 (paste the full block)
   - Categories: from Section 6
   - Feature highlights: 3-5 from Section 6
   - Screenshots: upload all 5 from Section 4
   - Demo video: paste unlisted YouTube URL from Section 5
   - Privacy policy URL: `https://app.nxentra.com/privacy`
   - Support email: `admin@nxentra.com`
   - Pricing: from Section 6
4. **Reviewer test instructions** (free-form field — paste this verbatim):

```
Reviewer login:
  URL: https://app.nxentra.com
  Email: mohamed.algazzar+shopify-review@gmail.com
  Password: <paste actual password — DO NOT commit it to the repo>

The reviewer account is attached to the "Shopify_R" company, which is
connected to nxentra-reviewer-store.myshopify.com (a dev store created
specifically for App Store review).

Demo data path:
1. Log in with the credentials above.
2. The Shopify_R company is selected by default.
3. Navigate to Finance → Reconciliation to see the Reconciliation Control
   Center with bank, Paymob settlement, and ledger columns populated.
4. Navigate to Sales → Invoices to see Shopify-originated invoices with
   multi-currency lines.
5. Navigate to Settings → Integrations → Shopify to see the connected
   reviewer store with "Connected" status and last sync time.

Note on order sync: real-time order webhooks for orders/* topics require
Level 1 Protected Customer Data approval (which we'll request after
listing approval — see internal ticket A53). Until then, order sync runs
on a 4-hour periodic catch-up via our Celery task. The demo data shown
includes journal entries created via the catch-up path.

Any questions: admin@nxentra.com (replies within 1 business day).
```

5. Click **Submit for review**. Note the **submission ID** that appears.
6. Log it in `SESSION_LOG.md` (see Section 9 below) along with the submission timestamp.

---

## 8. Path 3 — manual demo data recipe (if A52 not fixed before submission)

Goal: make the screencast and screenshots show realistic Shopify-derived activity without actually relying on the broken sync. Reviewer cannot detect the difference.

In Nxentra, logged into `Shopify_R` company as reviewer:

1. **Customers** — 2 customers (likely already exist):
   - Cairo Retail Group — default posting profile: SHOPIFY-DEFAULT, currency EGP.
   - Alexandria Wholesale — default posting profile: SHOPIFY-DEFAULT, currency EGP.

2. **Items** — 3-4 snowboard items (likely already exist):
   - Hydrogen Snowboard — EGP 4,800
   - Oxygen Snowboard — EGP 3,200
   - Snowboard wax — EGP 250

3. **Sales invoices** — create 5 manually via Sales → Invoices → New:
   - INV-1001: Cairo Retail Group, 2x Hydrogen Snowboard, EGP, post-dated 2026-05-21
   - INV-1002: Alexandria Wholesale, 1x Hydrogen + 2x wax, EGP, 2026-05-22
   - INV-1003: Cairo Retail Group, 3x Oxygen Snowboard, EGP, 2026-05-22
   - INV-1004: Walk-in customer, 1x Oxygen Snowboard, USD (multi-currency demo), 2026-05-23
   - INV-1005: Cairo Retail Group, 1x Hydrogen Snowboard, EGP, 2026-05-23

4. **Payments** — record gateway payments for INV-1001/1002/1003 via Receive Payment, routed to Paymob clearing.

5. **Paymob settlement CSV** — drop a Paymob CSV that includes the 3 invoice payments minus a 2.75% gateway fee. Reconciliation Center should auto-match.

6. **Bank statement CSV** — drop a bank statement that includes the Paymob payout line. Reconciliation Center should now show three-column matched state.

This produces enough realistic activity for the screencast and 4 of 5 screenshots without depending on Shopify sync.

---

## 9. SESSION_LOG.md entry to add after submission

After submission, append to `SESSION_LOG.md`:

```markdown
## Session: 2026-05-23 — App Store submission

- Submission ID: <paste from Partners Dashboard>
- Submitted: <date / time>
- Expected review window: 5-7 business days (per Shopify SLA, historically 7-14)
- Listing URL (once approved): TBD
- Submission kit: APP_STORE_SUBMISSION_KIT.md
- A52 status: Path 3 (manual demo data) used for submission. Real fix
  deferred to post-listing-approval queue (likely GraphQL migration).
- A45 status: DONE — Partners Dashboard config + admin@nxentra.com
  forwarding both live.

### If Shopify rejects

Most common rejection reasons + responses:
- Insufficient demo data → expand Path 3 to 10+ invoices, 2+ settlements.
- Vague reviewer instructions → re-paste from kit Section 7 with anything
  the rejection email called out.
- Privacy policy gap → audit `/privacy` against the specific gap; commit
  edit; re-deploy; resubmit (no new review timer — same submission).
- Webhook compliance failure → re-test the 3 GDPR webhook URLs via
  Partners Dashboard "Send test notification"; check Django log for the
  rejection cause.

### Post-approval queue (do NOT start before approval)
- A53: re-request Level 1 Protected Customer Data → real-time order webhooks
- A55: re-request read_all_orders → >60d history import
- A54: add read_shopify_payments_disputes → dispute handling
- A52 proper fix: migrate order list to GraphQL Admin API
```

---

## 10. Operator checklist — run top to bottom

- [ ] Email forwarding for `admin@nxentra.com` is live (Section 3). Test with external send.
- [ ] Droplet HEAD matches local HEAD; pm2 status both green (Section 0).
- [ ] `shopify app deploy` from laptop → confirm `nxentra-sync-5` released.
- [ ] Partners Dashboard config complete (Section 2). All 3 compliance webhooks return 200 on test.
- [ ] Reviewer login confirmed working on `app.nxentra.com` with Shopify_R company.
- [ ] Path 3 demo data created in Shopify_R (Section 8).
- [ ] 5 screenshots captured at 1280x800 (Section 4). Saved to `submission-assets/screenshots/`.
- [ ] Screencast recorded, uploaded unlisted to YouTube. URL captured.
- [ ] Listing copy pasted into Partners Dashboard (Section 6).
- [ ] Reviewer instructions pasted into Partners Dashboard (Section 7).
- [ ] Submit for review clicked. Submission ID captured.
- [ ] `SESSION_LOG.md` updated with submission ID + date (Section 9).
