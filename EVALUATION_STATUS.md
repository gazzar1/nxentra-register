# Nxentra Evaluation Status

**Last updated:** 2026-05-23 (refresh after the post-2026-05-01 inventory-and-UX sprint: A50/A51/A52 App Store readiness, A74-A77 inventory depth, A78/A79 posting-profile UX overhaul, edit-page navigation pattern rolled across 8 pages)
**Prior baseline:** [NXENTRA_EVALUATION_2026_04_16.md](NXENTRA_EVALUATION_2026_04_16.md) — full numerical evaluation as of 2026-04-16
**Cumulative session history:** [SESSION_LOG.md](SESSION_LOG.md)

---

## Executive Summary

22 days after the 2026-05-01 refresh, the engineering picture is stronger but the commercial picture is unchanged. Posting profile UX (A78/A79) and inventory depth (A74-A77) both shipped cleanly. Eight edit pages picked up a shared prev/next + dirty-state-Save + unsaved-changes-guard pattern. The App Store submission tier-1 items (A50/A51) closed; A45 + A52 remain.

But **zero new paying users, zero MRR, no live App Store listing, no Stripe billing integration, no backup runbook.** The 2026-05-01 doc's central recommendation was *"Stop building. Start selling."* That recommendation was not followed. The work of the last 22 days was excellent **building**, not selling.

Two material corrections to the 2026-05-01 doc:

1. **A0 (CI invariants on Postgres) is not actually CI-gated.** The 2026-05-01 doc lists A0 as DONE via commit `fb0e3d6`. A fresh audit of `.github/workflows/ci.yml` shows the invariant tests are explicitly skipped in CI. The infrastructure landed; the gate did not. Foundation credibility is partial.

2. **E-invoicing compliance (Egypt ETA + Saudi ZATCA) was not mentioned anywhere in the 2026-05-01 doc.** This is both a legal must (Egypt ETA mandatory for B2B over threshold) and the strongest MENA wedge against QuickBooks/Xero. Now tracked as A81. Worth a separate Tier 1 line in the recommendations.

**Position label this week:** late-pilot / pre-first-revenue (unchanged from 2026-05-01). **Position label after first-user signal:** early-revenue / niche-validated. **The distance between the two has not shrunk in 22 days.**

---

## What Changed Since 2026-05-01

Roughly 50+ commits in 22 days. Heavy on technical depth, light on commercial movement.

### Shipped — engineering

| Tickets | What | Net effect |
|---|---|---|
| A50 + A51 | Wizard import clamps to 59-day floor; declarative webhook subscriptions; register-webhooks UI removed | App Store submission unblocked |
| A52 | Diagnostic logging on `sync_store_orders` | Awaits live retry to expose root cause |
| A74 | `Item.allow_negative_stock` per-item flag | Drop-ship / made-to-order items no longer blocked by stock checks while strict items stay strict |
| A75 | Line-level warehouse picker + "Avail: N · short M" availability hint on sales/bill forms | Multi-warehouse merchants can route lines per line |
| A77 (Phase 1/2/3) | Inventory transfers between warehouses (model + commands + frontend pages) | Stock movement closes the multi-warehouse story |
| A78 | `PostingProfile.usage` (MANUAL vs GATEWAY) — separates platform-owned profiles from manual-entry profiles; data migration auto-flips gateway profiles + ensures default MANUAL profiles per company | Eliminates the recurring "wrong posting profile" dimension-required errors that bit the Shopify_R demo |
| A78b/A78c | Post-time GATEWAY guard; DRAFT-invoice DELETE endpoint; surface real backend errors in list-page toasts | Stuck-draft cleanup path for pre-A78 invoices on now-GATEWAY profiles |
| A79 Phase 1 | `Customer.default_posting_profile` + `Vendor.default_posting_profile` FKs with backfill; invoice/bill forms auto-fill the posting profile when the counterparty is picked; dropdown hidden when company has only one MANUAL profile of that type | "Pick the same dropdown on every invoice" friction eliminated for the 90% common case |
| A79b | Hide bare `default_ar_account` / `default_ap_account` from Customer + Vendor UI (model fields kept for one-release graceful deprecation) | UI decluttered |
| A79 follow-up | Sales/0013 fix: catch `ShopifyStore.default_posting_profile` targets that A78's migration missed; re-ensure AR/AP-DEFAULT exist; rebind customers/vendors that landed on a now-GATEWAY profile | First real merchant data corrected |
| (UX) | Items list shows on-hand Qty column with N+1 fix; prev/next record navigation + dirty-state Save + unsaved-changes guard rolled across 8 edit pages (Items, Customers, Vendors, Sales Invoices, Purchase Bills, Tax Codes, Posting Profiles, Chart of Accounts) | Compounding UX polish |
| A80 (filed, not started) | Phase 2 cleanup — drop `default_ar/ap_account` columns | Schema cleanup, blocked by one-release graceful deprecation window |
| A81 (filed, not started) | E-invoicing compliance — Egypt ETA + Saudi ZATCA | Tracked. Trigger: first B2B Egyptian merchant |

### Not shipped — commercial

| Item | 2026-05-01 status | 2026-05-23 status |
|---|---|---|
| Stripe / Paymob billing integration | Not started | Not started |
| Automated backup schedule | Not started | Not started |
| Shopify App Store listing live | Submission in prep | Submission still in review |
| First paying user (Aljazeera7) onboarded | "Not yet" | Still unclear; not visibly active |
| MRR | $0 | $0 |
| Marketing site / pricing page | Not visible | Not visible |
| Help docs / onboarding flow | Not visible | Not visible |
| E-invoicing (Egypt ETA / Saudi ZATCA) | Not mentioned | Tracked as A81, not started |

---

## Updated Repo Metrics

| Metric | 2026-04-16 | 2026-05-01 | 2026-05-23 | Δ vs 2026-05-01 |
|---|---|---|---|---|
| Total commits | 363 | 432 | ~482 | +50 |
| Backend Python (excl. migrations) | ~108K (incl migrations) | ~72.7K | ~73-74K | ~+1K |
| Frontend TS/TSX (excl. node_modules) | ~77.3K | ~79.3K | ~80K | ~+0.7K |
| Frontend pages | 161 | 163 | 169 | +6 (inventory transfers list/detail/new, A79 forms; minor) |
| Backend test files | 30+ | 33 | 50 | +17 (per audit) |
| Backend test functions | ~250 (est) | ~290 (est) | 518 | +228 (per audit; includes legacy not counted before) |
| Accounting migrations | ~25 | 30 | 33 (sales 0013, accounting 0033, sales 0012) | +3 |
| Django apps | 18 | 18 | 18 | 0 |

Growth remains quality-focused: schema cleanups, UX primitives, reusable hooks/components, two reusable abstractions (`useUnsavedChangesGuard`, `RecordNavigator`).

---

## Module-by-Module Completion (refreshed)

| Module | 2026-04-16 | 2026-05-01 | 2026-05-23 | Note |
|---|---|---|---|---|
| Event Sourcing Engine | 95% | 95% | 95% | Stable |
| Accounting Core | 95% | 96% | 96% | Stable; no net changes this period |
| CQRS Projections | 90% | 92% | 92% | Stable; reactor formalization (A3) still pending |
| Auth & Permissions | 95% | 95% | 95% | Stable |
| Multi-Tenancy | 95% | 95% | 95% | Stable |
| Shopify Connector | 85% | 90% | 91% | A50/A51 closed; A52 diagnostic awaiting live data; A11 (per-item revenue) still deferred |
| Bank Reconciliation | 80% | 88% | 88% | Unchanged |
| Reconciliation Control Center | ~10% | ~85% | ~85% | Unchanged |
| Frontend UI | 80% | 82% | 86% | **+4pp** — edit-page UX rollout (prev/next + dirty-Save + unsaved-guard across 8 pages), inventory transfers UI, customer/vendor form polish |
| Sales/Purchases | 70% | 72% | 78% | **+6pp** — A78 posting profile usage flag + A79 customer/vendor default-profile binding + auto-fill = real architectural primitive plus daily-friction removal |
| Inventory | 60% | 60% | 75% | **+15pp** — A74 (allow_negative_stock), A75 (line warehouse + availability hint), A77 (warehouse transfers) shipped |
| Stripe Connector | 40% | 40% | 40% | Unchanged — still ops-visible only |
| Reporting & Analytics | 60% | 60% | 60% | Unchanged |
| Properties / Clinic | 30% | 30% | 30% | Unchanged — kill-or-ship decision still pending |
| Documentation | 70% | 78% | 80% | EVALUATION_STATUS refreshed, NEXT_TASKS adds A80 + A81 |
| **CI invariant gating (was implicit in Test Coverage)** | n/a | listed as DONE (incorrectly) | **~50%** | Audit confirms invariant tests exist + pass locally but `.github/workflows/ci.yml` still skips them. A0 is not truly closed. |
| Test Coverage | 65% | 72% | 76% | More test functions than the 2026-05-01 count suggested; still short of the 90%+ accounting industry expectation |
| **E-invoicing (Egypt ETA + Saudi ZATCA)** | not tracked | not tracked | **0%** | Added as a tracked dimension this refresh. Tier 1 priority once first B2B Egyptian merchant signs. |
| Billing infrastructure (Stripe Checkout + Paymob) | not tracked | not tracked | **0%** | Cannot charge customers. Same status as 2026-05-01. |
| **Overall Weighted (to MVP/pilot for Shopify+MENA merchant)** | ~78% | ~84% | **~87%** | **+3pp.** Engineering momentum is real. Commercial readiness has not improved. |

---

## Strengths (Current)

### Architectural (unchanged from 2026-05-01, deepened by recent work)

1. **Event sourcing as source of truth.** Proven through the A1 dry-run incident + the A78/A79 idempotent backfill migrations.
2. **CQRS with write barriers.** A78 added `usage`-aware guards at both the create and post commands; gateway profiles can't accidentally route a manual invoice.
3. **Multi-tenancy with PostgreSQL RLS.** Unchanged. Still gold-standard.
4. **Command pattern + idempotency.** Holds across A77 inventory transfers + A78/A79 customer-vendor backfills.
5. **Typed event catalog.** `PostingProfileCreatedData` gained a `usage` field; `InventoryTransferPosted`-shaped events added for A77.

### Product (newly strong)

6. **Reconciliation Control Center exists end-to-end.** Unchanged from 2026-05-01.
7. **Settlement Provider as reconciliation pivot.** Unchanged.
8. **Manual CSV bridge for the Egyptian gateway gap.** Unchanged.
9. **Near-match tolerance + reason picker + adjustment JE.** Unchanged.
10. **Idempotent CSV re-upload.** Unchanged.
11. **(NEW) Multi-warehouse inventory.** A77 inventory transfers + A75 line-level warehouse routing close the multi-location story. A real merchant with two warehouses can now move stock and post sales from the correct one.
12. **(NEW) Posting Profile = real routing primitive, not a noise field.** A78's MANUAL vs GATEWAY split + A79's Customer/Vendor default-binding eliminate the "pick the same dropdown on every invoice" friction that motivated the design. Posting profiles are now an honest abstraction instead of a daily annoyance.
13. **(NEW) Edit-page UX baseline.** Prev/next record navigation + arrow-key shortcuts + dirty-state Save + unsaved-changes confirm dialog applied uniformly across 8 edit pages via two reusable primitives (`useUnsavedChangesGuard`, `RecordNavigator`). Onboarding and daily-use friction both drop.

### Engineering Quality

14. **Disciplined session structure.** Unchanged — SESSION_LOG, NEXT_TASKS, NEXT_SESSION_PROMPT, EVALUATION_STATUS all current.
15. **Architectural reviews before coding.** Unchanged.
16. **Test counts grow with feature surface.** 518 test functions today, up from ~290 estimated on 2026-05-01.

### Security

17. **Production guards still strong.** Unchanged.
18. **HttpOnly cookie auth + CSP/HSTS/rate limiting.** Unchanged.

### Niche Positioning

19. **MENA-specific gateway awareness.** Unchanged.
20. **Bilingual EN/AR with full RTL.** Unchanged.

---

## Weaknesses & Gaps (Current)

### Critical (block first-user invite / first revenue)

1. **No paying customer yet.** Same as 2026-05-01. The 22 days of engineering work since did not move this metric. **This is the single most important number to change in the next 60 days.**
2. **No Stripe / Paymob billing integration.** Same as 2026-05-01. Cannot charge.
3. **Live Phase 1 dry-run not visibly re-run since A12-A16 + A77 + A78 + A79 shipped.** Even more new surface area has accumulated since the 2026-05-01 doc flagged this. Multiple major architectural changes (posting profile gateway flag, customer/vendor default binding, inventory transfers) without an end-to-end browser walkthrough.
4. **Shopify App Store listing still in submission review.** A50/A51 closed; A45 (Partners Dashboard config + support email) + A52 (re-sync diagnosis) outstanding.

### Significant (should fix before broad GA)

5. **(CORRECTED) A0 CI invariants NOT actually gated.** The 2026-05-01 doc listed this as DONE. Audit shows `.github/workflows/ci.yml:41-46` still skips invariant tests on Postgres in CI. Until invariants block merges, the "truth engine" is asserted but not proven in CI. Estimated effort to actually close: 2-3 days.
6. **No e-invoicing (Egypt ETA / Saudi ZATCA).** Tracked as A81. Compliance gate for B2B Egyptian merchants over threshold; native ZATCA support is the gate for Saudi market entry. Global incumbents do not ship this — strongest possible MENA wedge that doesn't require headcount or capital. ~4-6 weeks focused work for Egypt Phase 1; +2-3 weeks for Saudi Phase 2. Trigger: first B2B Egyptian merchant signs OR first Saudi merchant inquiry.
7. **A3 reactor formalization still pending.** Three projection-emits-event cases remain (clinic rent, shopify settlement, properties). Violates stated CQRS rule. 4-5 days estimated.
8. **A10 deferred — AR tie-out invariant noise.** Same as 2026-05-01.
9. **A11 deferred — per-item Shopify revenue routing.** Same as 2026-05-01.
10. **Stripe still skeletal (40%).** Same as 2026-05-01.
11. **No automated backup strategy.** Same as 2026-05-01.
12. **No webhook retry / dead-letter queue.** Same as 2026-05-01.
13. **`projections/views.py` is 6,609 LOC — single-file mega-aggregator.** Surfaced by 2026-05-23 audit. Not blocking but a maintainability cliff approaches.
14. **Bank connector still has direct writes in views.** A5 still pending.

### Moderate (plan for post-launch)

15. **Single-developer bus factor.** ~482 commits from one contributor. Same as 2026-05-01.
16. **Test coverage 76% — short of 90%+ accounting industry expectation.** Improved from 72% but UI integration + concurrency + FX edge cases still light.
17. **No load testing.** Unknown ceiling. `BusinessEvent.save()` serializes per-company via `select_for_update`. Caps write throughput at ~1 TX/company/round-trip. Becomes a problem at >20 merchants live.
18. **Vertical modules (Properties, Clinic) still in repo at 30%.** Kill-or-ship decision still pending. The 22 days of recent work did not pull these forward; the longer they sit half-finished, the more they dilute the brand.
19. **No QuickBooks/Xero CSV import.** Switching cost from incumbents remains high.
20. **No mobile experience (PWA or app).** Unchanged.
21. **No public API documentation (drf-spectacular).** Unchanged.
22. **TypeScript safety: 174 files with `any` / `@ts-ignore`.** Surfaced by 2026-05-23 audit. Not blocking; future refactoring risk.

### Watch items (monitor, don't build yet)

23. **Event-write throughput bottleneck at scale (>20 merchants live).** Same as 2026-05-01.
24. **Projection orchestration coarse-grained.** Same as 2026-05-01.
25. **SQLite test DB tracked in git.** Same as 2026-05-01.

---

## Market Value Estimate (Updated)

### Code asset

- ~152-155K lines of production code, excluding migrations and node_modules.
- Architecture quality remains top 5% of early-stage codebases.
- A77 inventory transfers + A78/A79 posting profile UX add meaningful engineering depth — roughly 2-3 weeks of equivalent 2-engineer team work.
- Replacement-cost asset value: **$10M-$20M** (industry $50-$100/line, fintech-audited). Unchanged from 2026-05-01 in order of magnitude — A77/A78/A79 add depth at the margin but not a step-change.

### Revenue

- **$0** (still pre-revenue, still pre-first-paying-customer 22 days later).
- Customer acquisition cost: ~zero (first user organic; no marketing spend).
- LTV: undetermined.

### Pre-revenue valuation (realistic, two-perspective view)

The 2026-05-01 doc used **replacement-cost methodology** and arrived at $700K-$1.8M. A buyer-perspective methodology (acqui-hire, asset sale, internal-tool sale to non-tech buyer) arrives at $150K-$400K. Both are defensible; they measure different things.

The most useful synthesis:

| Scenario | Realistic range | Trigger |
|---|---|---|
| **Today, asset sale only (code + IP, no founder)** | $50K - $200K | Engineer-hours × $60-$100/h, buyer discount 0.3-0.5x |
| **Today, acqui-hire (code + founder for 1-2 years)** | $150K - $500K | Buyer values architecture + niche; solo + 0 users = low premium |
| **Today, strategic acquirer (MENA player who values the niche)** | $400K - $1M | Wafeq, Foodics, Class 5 portfolio company |
| **Today, replacement-cost methodology (per 2026-05-01 doc)** | $700K - $1.8M | Defensible if you find a buyer who values the architecture as much as you do |
| **Honest weighted today** | **$300K - $800K** | Slightly below 2026-05-01's range. Engineering added value but pre-revenue valuations are dominated by traction signals that have not moved. |
| With first paying user onboarded + 30 days clean | $1M - $2.5M | Demonstrates operator-independence |
| With 5 paying users + retention curve | $2.5M - $6M | Pre-seed institutional range |
| With 20 paying users + App Store listing live + repeatable acquisition | $5M - $12M | Seed institutional range |
| With Egypt ETA shipped + first B2B merchant | $6M - $15M | E-invoicing wedge changes the multiple — compliance moat is rare among MENA SaaS |
| With Saudi ZATCA + 50+ merchants + SOC 2 in progress | $15M+ | Series A territory |

**Most useful number to track:** time-to-first-paying-customer. Engineering is no longer the bottleneck — and has not been for at least 22 days.

---

## Areas for Improvement (prioritized)

### Tier 0 — This Week (validation + commercial gate)

- **Manual UI pass on `/finance/reconciliation`** + on the new A78/A79 customer/vendor flows. ~2h.
- **Live Phase 1 dry-run on a fresh Shopify dev store** — connect store, import orders, upload Paymob+Bosta CSVs, import bank statement, auto-match, resolve a near-match difference end-to-end. ~1.5h.
- **(NEW) Close A45** — Partners Dashboard privacy URL + support email + Compliance webhook URLs registration. ~2h.
- **(NEW) Re-test A52** — Shopify_R store, capture diagnostic logs. If still 0 orders, fall back to Path 3 manual demo data.
- **Submit App Store listing for review** — three screenshots + screencast + reviewer credentials. ~3h.
- **First paying user invite** (Aljazeera7) once the above are green.

### Tier 1 — Next 30 Days (first revenue)

- **Stripe billing integration** — Checkout + Customer Portal. Pricing page on marketing site.
- **(NEW) Onboarding observability** — Mixpanel/PostHog. Wizard step completion, time-to-first-reconciliation, drop-off points.
- **(NEW) Help docs minimum viable** — 10 markdown articles + a Loom walkthrough. Hand-holding doesn't scale.
- **Automated backup schedule + documented disaster recovery runbook** — managed Postgres + tenant export weekly.
- **(CORRECTED) Actually close A0** — gate invariant tests in CI. Not just file-and-pass-locally.
- **Decision: kill-or-ship Properties + Clinic.** They have not moved in 7+ weeks. Either ship 20% more, or move to `vertical_extensions/` archive folder. Carrying half-modules costs roadmap focus.
- **A45 remaining + A52** if not closed in Tier 0.

### Tier 2 — 60-90 Days (validate PMF + start the compliance wedge)

- **Get to 10 active Shopify merchants.** DM 100+ Egyptian/Saudi Shopify store owners.
- **Instrument the funnel.** Where do merchants drop off in onboarding? Where do they spend the most time?
- **(NEW) A81 — Egypt ETA Phase 1 e-invoicing** if any of the 10 merchants is B2B over the revenue threshold. Start cert procurement in parallel even before code (Egypt Trust, ~$200/year, 1-2 week lead time). This is the compliance moat that justifies pricing $30-$50 above the freemium tier.
- **A11** (per-item Shopify revenue routing) — when first user customizes per-item.
- **A10** (AR tie-out invariant accommodates non-AR-Control profiles).
- **A6, A7, A9** — UX polish from A1 dry-run.
- **drf-spectacular OpenAPI** — accountants and integrators need self-serve docs.
- **QuickBooks / Xero CSV import** — major switching-cost reducer.
- **B1 Ingest Inbox pattern** — webhook retry + dead-letter queue. Required as merchant count grows past 5.

### Tier 3 — 6-12 Months (scale)

- **A81 — Saudi ZATCA Phase 2** — first Saudi merchant inquiry trigger. Doubles TAM.
- **A3 + A4 + A5** — architectural cleanup (reactor concept, architecture tests, FX direct-writes cleanup).
- **B2-B7** — Inbox pattern + canonical platform models + Shopify migration + Paymob direct connector.
- **C1-C3** — generic reconciliation engine.
- **D1-D3** — agent-ready command surface + MCP server.
- **WooCommerce / Amazon connectors** — each expands TAM by 30-50%.
- **Accountant portal** — bookkeepers managing multiple clients.
- **SOC 2 Type II** — required for enterprise. Start at 50 merchants, takes 6-12 months.
- **Hire #1: senior Django engineer.** Bus factor mitigation is overdue at $0 ARR; existential at $30K MRR.
- **(NEW) Fundraise seed round** ($500K-$1M) at $30K+ MRR. MENA investors: Sanabil, BECO, Class 5, Wamda. International: Y Combinator, Sequoia MENA scout program.

---

## Updated Market Readiness View

### Controlled Pilot (single-merchant, supervised)

**Yes, with confidence.** Same as 2026-05-01. The reconciliation product spine, posting profile UX, and inventory depth answer the merchant question end-to-end. First user can be invited as soon as Tier 0 validation is done.

### Multi-Merchant Pilot (5-10 supervised merchants)

**Yes, after Tier 1 ships.** Stripe billing + backup runbook + onboarding telemetry + help docs close the operational gap. App Store listing optional but accelerates acquisition.

### Broad SMB GA

**Not yet.** Tier 2 work (A10, A11, A6/A7/A9, OpenAPI docs, QuickBooks import, Inbox pattern, **Egypt ETA**) gates this. Realistic window: 90-120 days post-first-user-revenue.

### Enterprise

**Not yet.** Multi-tenancy architecture supports it. SOC 2 + accountant portal + dedicated-DB tenancy proven at 50+ merchants + e-invoicing native are the gates. Realistic window: 12-18 months.

### MENA niche positioning

**Stronger than ever, IF the e-invoicing wedge is shipped.** Settlement Provider abstraction with currency-driven defaults + Paymob CSV parser + Bosta-COD courier model + bilingual EN/AR + RTL — global incumbents do not model this layer. **Add native Egypt ETA + Saudi ZATCA on top of this and the moat doubles.** The first-user wedge is genuinely defensible; the compliance wedge would make it nearly impossible to displace.

---

## Verified Signals (Updated)

### Verified Positives

- ~482 commits over ~7 months from a single contributor, with disciplined structure
- 518 test functions across 50 test modules; A77/A78/A79 all shipped with paired tests
- Pre-commit (ruff + ruff-format) enforced; no `--no-verify` skips visible in commit history
- Architectural review is a habit — A78/A79 designs both refined post-discussion before code landed
- SESSION_LOG.md, NEXT_TASKS.md (now A0-A81), EVALUATION_STATUS.md, NEXT_SESSION_PROMPT.md all current and detailed; bus-factor mitigation through docs is real
- Reusable primitives extracted from feature work: `useUnsavedChangesGuard`, `RecordNavigator`, `LineAvailabilityHint`. Code quality is rising, not falling.

### Verified Current Blockers

- Live Phase 1 dry-run not re-run since A12-A16 + A77 + A78 + A79 shipped (gap is larger now than on 2026-05-01)
- `/finance/reconciliation` narrative + Needs Review card still not click-tested end-to-end in a browser
- A78/A79 customer/vendor flows not click-tested end-to-end in a browser
- First paying user not yet onboarded (Aljazeera7 acquired 2026-04-22, still unclear active status 2026-05-23)
- App Store listing still in submission review

### Verified Risks

- Single-contributor bus factor (mitigated by docs, not eliminated)
- No automated backup schedule
- No payment/billing system; cannot charge when ready
- Stripe accounting integration still skeletal
- Verticals (Properties, Clinic) still 30% complete and visible in repo
- **(NEW) A0 CI invariant gate was misreported as DONE on 2026-05-01.** Watch for similar reporting drift in future evaluations.
- **(NEW) Engineering velocity is healthy; commercial velocity is zero.** The 22-day delta proves engineering work alone does not move the commercial story.

---

## Bottom Line

Three weeks ago Nxentra was *"a technically differentiated, niche-credible, pilot-stage product with a working merchant-facing reconciliation product spine."* Today it is the same thing plus deeper inventory + better posting profile UX + a cleaner edit-page baseline.

The architecture story has improved at the margin. The product story has improved at the margin. **The commercial story has not changed at all.** Zero customers, $0 MRR, no live App Store listing, no Stripe billing, no help docs.

The 2026-05-01 doc's bottom-line recommendation was *"Stop building. Start selling."* The 22 days since have been excellent building. The recommendation is now restated more bluntly:

**Stop. Building. Start. Selling.**

The next 60 days should produce one number: **paying merchants.** Not new features, not architectural refactors, not UX polish. Paying merchants. The app is more than capable; the merchants are not yet there because the GTM machinery does not exist.

If by 2026-07-22 the answer to *"how many paying merchants do you have?"* is still zero, the technical strength of the platform becomes increasingly irrelevant to its valuation. Investors and acquirers buy traction; the codebase is a multiplier on traction, not a substitute for it.

If the answer is 10 paying merchants — even at $20/month each ($2.4K ARR) — the valuation conversation moves into a different bracket and the e-invoicing wedge (A81) becomes the right thing to build next, because there's a customer with a budget to pay for it.

The distance between today and that gate is execution. The same execution gap as on 2026-05-01. The same gap as on 2026-04-16. **The third evaluation in a row to say the same thing.**

**Position label this week:** late-pilot / pre-first-revenue (unchanged for 60+ days). **Position label after first 10 paying merchants:** validated-niche-wedge. The distance is commercial execution, not engineering, and it has been the distance for too long.

---

## Recommended Next Steps

### This week (blocking)

- [ ] Manual UI pass on `/finance/reconciliation` (narrative banner, Needs Review card, reason picker, Resolve button)
- [ ] Manual UI pass on A78/A79 customer/vendor flows
- [ ] Live Phase 1 dry-run on a fresh Shopify dev store
- [ ] Close A45 (Partners Dashboard + support email + compliance webhook URLs)
- [ ] Submit App Store listing for review (screenshots + screencast + reviewer credentials)
- [ ] First paying user invite (Aljazeera7 or new Egyptian Shopify merchant)

### Next 30 days (first revenue)

- [ ] Stripe billing integration (Checkout + Customer Portal + pricing page)
- [ ] Onboarding observability (PostHog or Mixpanel; wizard step telemetry, time-to-first-reconciliation)
- [ ] Help docs MVP (10 markdown articles + Loom walkthrough)
- [ ] Automated backup schedule + documented DR runbook
- [ ] **Actually close A0** — gate invariant tests in CI on Postgres
- [ ] Decision: kill-or-ship Properties + Clinic

### 60-90 days (PMF validation + compliance wedge starts)

- [ ] Get to 10 active Shopify merchants (DM 100+)
- [ ] **A81 — Egypt ETA Phase 1** if first B2B Egyptian merchant signs (start cert procurement in parallel)
- [ ] A10 + A11 (when first user signals)
- [ ] drf-spectacular OpenAPI docs
- [ ] QuickBooks / Xero CSV import
- [ ] B1 Ingest Inbox pattern
- [ ] Hire #1: senior Django engineer

### 6-12 months (scale + Series A path)

- [ ] **A81 — Saudi ZATCA Phase 2** at first Saudi merchant inquiry
- [ ] A3-A5 architectural cleanup
- [ ] B2-B7 canonical platform models + Paymob direct connector
- [ ] C1-C3 generic reconciliation engine
- [ ] WooCommerce / Amazon connectors
- [ ] Accountant portal
- [ ] SOC 2 Type II
- [ ] Fundraise seed at $30K+ MRR

---

## Amendment Log

| Date | Change |
|---|---|
| 2026-04-16 | Initial evaluation status created from full evaluation report |
| 2026-04-16 | Sec 9, 10 added (deployment + landing page) |
| 2026-04-16 | Landing page 6/10 → 8/10 after Tier 1+2 improvements |
| 2026-05-01 | **Full refresh** after A2.5/A12/A13/A14/A14b/c/A16 shipped. Reconciliation Control Center exists end-to-end. Overall completion 78% → 84%. Position late-beta → late-pilot. Pre-revenue valuation $500K-$1.5M → $700K-$1.8M. |
| 2026-05-23 | **Full refresh** after A50/A51/A52 + A74/A75/A77 inventory depth + A78/A79 posting profile UX + edit-page UX baseline rolled across 8 pages. Overall completion 84% → ~87%. Inventory 60% → 75%, Sales/Purchases 72% → 78%, Frontend 82% → 86%. **Two corrections to 2026-05-01:** (a) A0 CI invariant gate is NOT actually closed — infrastructure shipped but `.github/workflows/ci.yml` still skips invariant tests; (b) e-invoicing compliance (Egypt ETA / Saudi ZATCA) was omitted from the 2026-05-01 doc despite being both legal-must and the strongest MENA wedge — now tracked as A81 with sequencing recommendation. **Valuation:** weighted range adjusted to $300K-$800K to reconcile replacement-cost methodology with buyer-perspective methodology; both inputs to a band rather than a point estimate. **Bottom line restated more bluntly:** zero new paying merchants in 22 days; engineering velocity is healthy and commercial velocity is zero; the same execution gap has been the bottleneck for 60+ days. Position label late-pilot / pre-first-revenue unchanged. |
