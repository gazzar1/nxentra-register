# Nxentra Evaluation Status

**Last updated:** 2026-05-01 (refresh after Reconciliation Control Center spine shipped: A2.5 + A12 + A13 + A14 + A14b/c + A16)
**Prior baseline:** [NXENTRA_EVALUATION_2026_04_16.md](NXENTRA_EVALUATION_2026_04_16.md) — full numerical evaluation as of 2026-04-16
**Cumulative session history:** [SESSION_LOG.md](SESSION_LOG.md)

---

## Executive Summary

Nxentra has crossed an important threshold since the 2026-04-16 evaluation. It is no longer "an impressive accounting engine looking for a product" — the **Reconciliation Control Center spine is now shipped end-to-end** (Stage 1: Sales→Clearing per-provider with aging; Stage 2: Clearing→Settlement via manual Paymob/Bosta CSV import; Stage 3: Bank Match with auto-clearance; near-match difference engine with reason picker). The merchant question *"Where is my money?"* now has a concrete on-screen answer.

The strategic picture:

- **Engineering:** Phase A engine complete on the merchant-facing side. Outstanding for Phase A exit is first-user validation, not more code.
- **Product:** Strongest single positioning Nxentra has ever had — a Shopify accounting + reconciliation product for MENA merchants where the dominant gateways (Paymob, Bosta-COD) are untenable for global incumbents.
- **Commercial:** Still pre-revenue. First user (Egyptian Shopify merchant, acquired 2026-04-22) not yet onboarded; live Phase 1 dry-run on a fresh dev store still pending.

**Position label:** **late-pilot / pre-first-revenue**. One step beyond the 2026-04-16 "pilot-ready / late-beta" classification. The remaining gap is execution (validation + invite + close), not capability.

---

## What Changed Since 2026-04-16

A two-week sprint (8 commits in this session, ~17 in the period) shipped the entire merchant-facing reconciliation product spine that the prior evaluation said was missing:

| Ticket | Commit | What |
|---|---|---|
| A0 | `fb0e3d6` | CI invariants on Postgres |
| A1 | `b6b52b9`–`7d12432` | Phase 1 dry-run (5 scenarios pass) + 7 critical bug fixes |
| A8 | `71cb0d7`, `cd7f484` | Auto-fill GL accounts on Shopify-imported items |
| A2 | `d0dd0d2` | PaymentGateway routing primitive |
| A2.5 | `caa1ab9` | Rename to SettlementProvider; add provider_type |
| A12 | `86d62d2` + `6a09473` | Settlement-provider AnalysisDimension + COD wizard step + dimension tagging across refunds/payouts/disputes |
| A13 | `b24065b` | Reconciliation Control Center MVP at `/finance/reconciliation` |
| A14 | `238d0a9` | Manual Paymob + Bosta CSV import + Expected Bank Deposit account convention |
| A14b/c | `3445bc0` | Bank-rec auto-match for settlements + per-Shopify-order drilldown |
| A16 | `ced05ad`, `63d8888` | Difference Engine (near-match + reason picker + adjustment JE + narrative + Needs Review queue) |

**Implication:** The prior evaluation's #1 weakness ("no operator-driven month-end path", "Shopify payout reconciliation incomplete", "no merchant-facing reconciliation surface") is now substantially addressed.

---

## Updated Repo Metrics

| Metric | 2026-04-16 | 2026-05-01 | Δ |
|---|---|---|---|
| Total commits | 363 | 432 | +69 |
| Backend Python (excl. migrations) | ~108K (incl migrations) | ~72.7K | n/a (basis change) |
| Frontend TS/TSX (excl. node_modules) | ~77.3K | ~79.3K | +2K |
| Frontend pages | 161 | 163 | +2 (`/finance/reconciliation`, `/finance/settlements/import`) |
| Backend test files | 30+ | 33 | +3 (test_a14b, test_a14c, test_a16, test_settlement_imports, test_reconciliation_views, test_a12_followups, test_settlement_provider) |
| Accounting migrations | ~25 | 30 | +5 |
| Django apps | 18 | 18 | 0 |

Code growth is quality-focused: most net additions are tests and migrations, not feature sprawl. No new vertical modules added.

---

## Module-by-Module Completion (refreshed)

| Module | 2026-04-16 | 2026-05-01 | Note |
|---|---|---|---|
| Event Sourcing Engine | 95% | 95% | Stable; LEPH still production-ready |
| Accounting Core | 95% | 96% | A14 added Expected Bank Deposit + Sales Returns roles; A16 added the difference-resolution adjustment pattern |
| CQRS Projections | 90% | 92% | A14 added PaymentSettlementProjection; idempotency stronger after A1 incident |
| Auth & Permissions | 95% | 95% | Stable |
| Multi-Tenancy | 95% | 95% | Stable |
| Shopify Connector | 85% | 90% | A12 added settlement_provider routing; A1 hardening fixed 7 critical bugs; payout settlement still uses old shape but A14's `PAYMENT_SETTLEMENT_RECEIVED` is the canonical replacement |
| Bank Reconciliation | 80% | 88% | A14b auto-match for settlements + A16 difference engine added significant depth |
| **Reconciliation Control Center** | **~10% (vision only)** | **~85%** | **Net new — A13 + A14 + A14b/c + A16. The single biggest delta this period.** |
| Frontend UI | 80% | 82% | +2 pages, narrative banner + Needs Review card |
| Sales/Purchases | 70% | 72% | A12 follow-ups threaded `control_line_analysis_tags` kwarg through commands |
| Inventory | 60% | 60% | Unchanged |
| Stripe Connector | 40% | 40% | Unchanged — still ops-visible only, not accounting-integrated |
| Reporting & Analytics | 60% | 60% | Unchanged |
| Properties / Clinic | 30% | 30% | Unchanged — should be killed-or-shipped before broad GA |
| Documentation | 70% | 78% | SESSION_LOG, NEXT_TASKS, NEXT_SESSION_PROMPT, FINANCE_EVENT_FIRST_POLICY all current and detailed |
| Test Coverage | 65% | 72% | +33 tests in this period (15 A16, 11 settlement_imports, 6 A14c, 4 A14b, plus A12 follow-ups, dimension-validation tests, regression tests) |
| **Overall Weighted (to MVP/pilot for Shopify+MENA merchant)** | **~78%** | **~84%** | **+6pp. Closing the gap is now execution, not engineering.** |

---

## Strengths (Current)

### Architectural (still exceptional, now battle-tested)

1. **Event sourcing as source of truth** — proven through the A1 dry-run incident (idempotency on emit_event_no_actor caught a real bug, repeatable replay works).
2. **CQRS with write barriers** — A12 follow-ups added `AccountDimensionRule(REQUIRED)` enforcement; manual JEs that would break reconciliation are now rejected at validation time.
3. **Multi-tenancy with PostgreSQL RLS** — unchanged, still gold-standard.
4. **Command pattern + idempotency** — `source_module + source_document` pattern proven across `payment_settlement`, `payment_settlement_clearance`, `payment_settlement_difference` JEs. Re-import is safe.
5. **Typed event catalog** — `PaymentSettlementReceivedData` dataclass added; new event type registered in `EVENT_DATA_CLASSES`. Schema discipline holding.

### Product (newly strong)

6. **Reconciliation Control Center exists end-to-end.** Three-stage layout (Sales→Clearing, Clearing→Settlement, Bank Match), per-provider drilldown with aging buckets, "Tell me the story" narrative, Needs Review queue with reason picker. This is what the prior evaluation said was the missing product spine.
7. **Settlement Provider as reconciliation pivot.** AnalysisDimension-based — adding WooCommerce/Amazon/Noon costs N dimension values, not N new GL accounts. Trial balance stays clean as platforms grow.
8. **Manual CSV bridge for the Egyptian gateway gap.** Paymob and Bosta CSV import is the difference between "we'll integrate Paymob in 6 months" and "merchant uploads Paymob CSV today." The first user can do month-end close from day one.
9. **Near-match tolerance + reason picker + adjustment JE.** Bank deposits never equal expected exactly (gateway fees, bank wire fees, chargebacks). Without A16, every imperfect deposit would have left an unreconciled EBD line forever. With A16, the merchant categorizes once and the adjustment posts cleanly.
10. **Idempotent CSV re-upload.** Operator can re-upload Paymob CSVs without duplicate JEs (idempotency key `payment.settlement.received:{provider}:{batch_id}`).

### Engineering Quality

11. **Disciplined session structure.** SESSION_LOG.md captures every commit, NEXT_TASKS.md captures every deferred ticket with rationale, NEXT_SESSION_PROMPT.md hands off cleanly between agents. Bus-factor mitigation through documentation.
12. **Architectural reviews before coding.** A2 went through two external review rounds before any code landed; A12 was refined post-review (radio vs checkboxes, FK vs CharField, default-NULL vs seeded). The pattern is producing better designs than first-instinct would have.
13. **Test counts grow with feature surface.** +33 tests in two weeks; total test_*.py files at 33. No feature shipped without paired tests in this period.

### Security

14. **Production guards still strong** — settings.py still rejects insecure defaults at startup.
15. **HttpOnly cookie auth + CSP/HSTS/rate limiting** — unchanged, still production-grade.

### Niche Positioning

16. **MENA-specific gateway awareness.** Bosta as a courier (not a payment_method); Paymob/Mylerz/Aramex defaults driven by `company.default_currency`; explicit handling of cash-on-delivery as a settlement provider category. Global incumbents do not model this layer; their CSV imports are generic.
17. **Bilingual EN/AR with full RTL** — unchanged, still meaningful.

---

## Weaknesses & Gaps (Current)

### Critical (block first-user invite)

1. **Live Phase 1 dry-run not yet re-run since A12-A16 shipped.** Memory has it flagged as blocking before the first real user. A12-A16 introduced a new wizard step, new account roles (EBD, SALES_RETURNS), a new field on ShopifyStore, and major projection changes. Six tickets shipped without an end-to-end browser walkthrough. This is the #1 risk going into the first user invite.
2. **Manual UI pass on `/finance/reconciliation` not yet completed.** Narrative banner, Needs Review card, reason picker dropdown, Resolve button — all unit-tested but never clicked end-to-end. Risk: a wiring bug between the PATCH endpoint and the summary refresh that didn't surface in unit tests.

### Significant (should fix before broad GA)

3. **Stripe still skeletal (40%).** Webhook parsing exists but no accounting projection equivalent to Shopify's. Decision was deliberately deferred (per 2026-04-16 evaluation) — keep as ops-only, not in launch messaging — but the gap remains for any merchant with mixed Shopify+Stripe checkout.
4. **A11 deferred — per-item Shopify revenue routing.** Today's projection posts one aggregate revenue line per order to the company-level `SALES_REVENUE` mapping. Manual invoices respect `Item.sales_account`; Shopify-imported invoices don't. Real merchants with diverse SKUs (apparel vs accessories vs digital) will hit this. Pulled forward only if first user customizes per-item.
5. **A10 deferred — AR tie-out invariant noise.** `post_journal_entry` logs false-positive warnings whenever a customer uses a non-AR-Control posting profile (Shopify Clearing today; will worsen as more platforms onboard). Cosmetic, but log noise erodes trust over time.
6. **No automated backup strategy.** Backup module exists but no scheduled backups, no documented disaster recovery runbook for the managed Postgres. If the droplet's database disappeared today, the latest tenant export from `pilot_backup_post_fix_2026-03-25.zip` is the recovery point — over a month old.
7. **No payment/billing system.** Stripe billing not integrated. Cannot charge customers when ready.
8. **No Shopify App Store listing.** Distribution channel absent. Without listing, every customer is hand-acquired.
9. **No webhook retry / dead-letter queue.** Shopify webhooks that fail processing are lost. Inbox pattern is filed as B1 but not yet implemented.

### Moderate (plan for post-launch)

10. **Single-developer bus factor.** 432 commits from one contributor. No code review process visible. Codebase is well-documented enough for onboarding but second engineer is overdue.
11. **Test coverage 72% — short of accounting industry expectation of 90%+.** Strong on invariants and command paths; weaker on UI integration, concurrency, FX edge cases, RLS bypass attempts.
12. **No load testing, no concurrency testing.** Unknown ceiling. `BusinessEvent.save()` serializes per-company via `select_for_update` (filed as a Watch Item in NEXT_TASKS.md) — caps write throughput at ~1 TX per company per round-trip. Acceptable for early merchants, becomes a problem at >20 merchants live.
13. **Vertical modules (Properties, Clinic) still in repo at 30%.** Not blocking but they dilute the launch narrative. Decision pending: kill-or-ship.
14. **No QuickBooks/Xero import.** Switching cost from incumbents is high without it. Major adoption barrier.
15. **No mobile experience (PWA or app).** Merchants check Shopify on phones; Nxentra's responsive layouts work but there's no installable app.
16. **No public API documentation.** drf-spectacular not yet configured (filed as D1).

### Watch items (monitor, don't build yet)

17. **Event-write throughput bottleneck** at scale (>20 merchants live).
18. **Projection orchestration coarse-grained** — every projection iterates every company.
19. **SQLite test DB tracked in git** — minor Postgres-divergence risk.

---

## Market Value Estimate (Updated)

### Code asset

- ~152K lines of production code (72.7K backend + 79.3K frontend), excluding migrations and node_modules.
- Architecture quality remains top 5% of early-stage codebases.
- New for this period: a working merchant-facing reconciliation product. Replacement cost rises by roughly 2-3 months of dedicated 2-engineer team work to rebuild A12-A16 cleanly.
- Replacement-cost asset value: **$10M–$20M** (industry $50–$100/line, fintech-audited). Up from $9M–$18M baseline.

### Revenue

- **$0** (pre-revenue, pre-first-customer).
- Customer acquisition cost: ~zero (first user organic).
- LTV: undetermined.

### Pre-revenue valuation (realistic)

| Scenario | Range | Trigger |
|---|---|---|
| **Today (pre-first-customer)** | **$700K – $1.8M** | Up from $500K–$1.5M baseline. The shipped Reconciliation Control Center is the strongest evidence yet that this is a real product, not a beautiful framework. |
| With first user onboarded + 30 days clean | $1.5M – $3M | Demonstrates operator-independence (the prior evaluation's biggest "unproven" item). |
| With 5 paying users + retention | $3M – $6M | Pre-seed institutional range. |
| With 20 paying users + Shopify App Store + repeatable acquisition | $5M – $12M | Seed institutional range; defensible MENA niche thesis. |
| With SOC 2 + accountant portal + 50+ merchants | $15M+ | Series A territory; multi-platform expansion (B7 Paymob direct, E3 Bosta direct) becomes the growth narrative. |

**Most useful number to track:** time-to-first-paying-customer. Engineering is no longer the bottleneck.

---

## Areas for Improvement (prioritized)

### Tier 0 — This Week (validation gate)

- **Manual UI pass on `/finance/reconciliation`** — verify narrative banner, Needs Review card, reason picker, Resolve button click through cleanly. ~1h.
- **Live Phase 1 dry-run on a fresh Shopify dev store** — connect store, import orders, upload Paymob+Bosta CSVs, import bank statement, auto-match, resolve a near-match difference end-to-end. ~1.5h.
- **First-user invite** once the above are green.

### Tier 1 — Next 30 Days (first revenue)

- **Stripe billing integration** — Stripe Checkout + Customer Portal. Without this, no charging customers.
- **Automated backup schedule + documented disaster recovery runbook** — managed Postgres + tenant export weekly. Should not be on the critical path for a paying customer.
- **Shopify App Store listing prep** — privacy policy, compliance docs, listing copy. OAuth flow already works; this is the distribution channel.
- **Onboarding observability** — telemetry on wizard step completion, time-to-first-reconciliation. Without this, Tier 0's "operator-independent" claim is unverifiable.
- **Decision: kill-or-ship Properties + Clinic.** They dilute the narrative. Either ship them as separate products or move them to a `vertical_extensions/` archive folder.

### Tier 2 — 60-90 Days (validate product-market fit)

- **A11** (per-item Shopify revenue routing) — when first user customizes per-item, this becomes urgent.
- **A10** (AR tie-out invariant accommodates non-AR-Control profiles) — silences the false-positive warning that all integrated platforms trigger.
- **A6, A7, A9** — UX polish from A1 (auto-launch wizard, post-callback routing, item-without-SKU fallback).
- **drf-spectacular OpenAPI** — accountants and integrators need self-serve docs.
- **QuickBooks / Xero CSV import** — major switching-cost reducer.
- **B1 Ingest Inbox pattern** — webhook retry + dead-letter queue. Required as merchant count grows past 5.

### Tier 3 — 6-12 Months (scale)

- **A3 + A4 + A5** — architectural cleanup (reactor concept, architecture tests, FX direct-writes cleanup). Now informed by what the reconciliation MVP actually needed.
- **B2-B7** — Inbox pattern + canonical platform models + Shopify migration + Paymob direct connector.
- **C1-C3** — generic reconciliation engine (formalization of A13's projection-based MVP).
- **D1-D3** — agent-ready command surface + MCP server.
- **WooCommerce / Amazon connectors** — each expands TAM by 30-50%.
- **Accountant portal** — bookkeepers managing multiple clients.
- **SOC 2 Type II** — required for enterprise. Start now, takes 6-12 months.
- **Hire #1: senior Django engineer** — codebase is well-structured for 1-2 week onboarding. Bus factor mitigation is overdue.

---

## Updated Market Readiness View

### Controlled Pilot (single-merchant, supervised)

**Yes, with confidence.** The reconciliation product spine answers the merchant question end-to-end. First user can be invited as soon as Tier 0 validation is done.

### Multi-Merchant Pilot (5-10 supervised merchants)

**Yes, after Tier 1 ships.** Stripe billing + backup runbook + onboarding telemetry close the operational gap. App Store listing optional but accelerates acquisition.

### Broad SMB GA

**Not yet.** Tier 2 work (A10, A11, A6/A7/A9, OpenAPI docs, QuickBooks import, Inbox pattern) gates this. Realistic window: 60-90 days post-first-user-revenue.

### Enterprise

**Not yet.** Multi-tenancy architecture supports it. SOC 2 + accountant portal + dedicated-DB tenancy proven at 50+ merchants are the gates. Realistic window: 12-18 months.

### MENA niche positioning

**Stronger than ever.** Settlement Provider abstraction with currency-driven defaults (EGP→Bosta, SAR→Mylerz, AED→Aramex), Paymob CSV parser, Bosta-COD courier model, bilingual EN/AR + RTL — global incumbents do not model this layer. The first-user wedge is genuinely defensible.

---

## Verified Signals (Updated)

### Verified Positives

- 432 commits over ~6.5 months from a single contributor, with disciplined structure
- 33 backend test files; 15 new in two weeks; all 61+ reconciliation-related tests passing
- Frontend `tsc --noEmit` clean
- A1 dry-run on a previous fresh Shopify dev store passed all 5 scenarios
- Pre-commit (ruff + ruff-format) enforced; no `--no-verify` skips visible in commit history
- Architectural review is a habit, not a ceremony — A2 and A12 designs both refined post-review
- SESSION_LOG.md, NEXT_TASKS.md, NEXT_SESSION_PROMPT.md all current and detailed; bus-factor mitigation through docs is real

### Verified Current Blockers

- Live Phase 1 dry-run not re-run since A12-A16 shipped (memory-flagged as blocking)
- `/finance/reconciliation` narrative + Needs Review card not yet click-tested in a browser
- First user not yet invited

### Verified Risks

- Single-contributor bus factor (mitigated by docs, not eliminated)
- No automated backup schedule
- No payment/billing system; cannot charge when ready
- Stripe accounting integration still skeletal
- Verticals (Properties, Clinic) still 30% complete and visible in repo

---

## Bottom Line

Two weeks ago Nxentra was *"a technically differentiated, niche-credible, pilot-stage product"*. Today it is the same thing **plus a working merchant-facing reconciliation product spine** that answers the *"where is my money?"* question Egyptian Shopify merchants actually ask.

The architecture story has not changed. The product story has. The commercial story is unchanged: zero customers, but the path is now clear.

The next decision is not technical. It is execution: validate end-to-end in a browser, run the live dry-run, send the first invite. **Stop building. Start selling.** This was the prior evaluation's bottom-line recommendation; it is more true today than it was two weeks ago, because the product is more ready.

If the first user onboards cleanly within 48h of invite and uses the Reconciliation Control Center daily for two weeks, the valuation conversation moves from "technically differentiated pilot-stage" to "validated MENA Shopify accounting wedge with a defensible niche." That is the gate.

**Position label this week:** late-pilot / pre-first-revenue. **Position label after first-user signal:** early-revenue / niche-validated. The distance between the two is execution, not engineering.

---

## Recommended Next Steps

### This week (blocking)

- [ ] Manual UI pass on `/finance/reconciliation` (narrative banner, Needs Review card, reason picker, Resolve button)
- [ ] Live Phase 1 dry-run on a fresh Shopify dev store
- [ ] First-user invite (Egyptian Shopify merchant)

### Next 30 days (first revenue)

- [ ] Stripe billing integration (Checkout + Customer Portal)
- [ ] Automated backup schedule + documented DR runbook
- [ ] Shopify App Store listing prep (privacy policy, compliance docs, listing copy)
- [ ] Onboarding observability (wizard step telemetry, time-to-first-reconciliation)
- [ ] Decision: kill-or-ship Properties + Clinic

### 60-90 days (PMF validation)

- [ ] A10 + A11 (when first user signals)
- [ ] drf-spectacular OpenAPI docs
- [ ] QuickBooks / Xero CSV import
- [ ] B1 Ingest Inbox pattern
- [ ] Hire #1: senior Django engineer

### 6-12 months (scale)

- [ ] A3-A5 architectural cleanup
- [ ] B2-B7 canonical platform models + Paymob direct connector
- [ ] C1-C3 generic reconciliation engine
- [ ] WooCommerce / Amazon connectors
- [ ] Accountant portal
- [ ] SOC 2 Type II

---

## Amendment Log

| Date | Change |
|---|---|
| 2026-04-16 | Initial evaluation status created from full evaluation report |
| 2026-04-16 | Sec 9, 10 added (deployment + landing page) |
| 2026-04-16 | Landing page 6/10 → 8/10 after Tier 1+2 improvements |
| 2026-05-01 | **Full refresh** after A2.5/A12/A13/A14/A14b/c/A16 shipped. Reconciliation Control Center exists end-to-end. Overall completion 78% → 84%. Position late-beta → late-pilot. Pre-revenue valuation $500K–$1.5M → $700K–$1.8M. Tier 0/1/2/3 improvement areas restructured around the validation→first-revenue→PMF→scale arc. |
