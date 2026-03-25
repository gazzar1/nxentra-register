# Nxentra GA Readiness Scorecard (Connect)

Version: 1.0  
Last Updated: 2026-03-24  
Target GA Decision Date: 2026-06-04  
Review Cadence: Weekly

## How To Use
- For each gate item, set `Status` to `PASS`, `FAIL`, or `BLOCKED`.
- Every `FAIL`/`BLOCKED` item must have a named owner and due date.
- GO decision is allowed only if all critical gates pass.

## Status Legend
- PASS: Exit criteria met with evidence
- FAIL: Criteria not met
- BLOCKED: Work cannot proceed due to dependency

---

## 1) Wedge 1: Ingestion/Integration Engine

### 1.1 Connector Reliability
- [ ] Shopify ingest success rate >= 99.5% (trailing 14 days)
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Webhook deduplication verified (no duplicate posting defects open)
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Retry/dead-letter flow tested end-to-end
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Zero open P0/P1 ingestion defects
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

### 1.2 Schema + Mapping Stability
- [ ] Canonical event schema versioned and documented
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Backward compatibility tests for payload versions pass
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Mapping coverage >= 95% for core entities
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

### 1.3 Data Integrity + Security
- [ ] Idempotency keys enforced across financial-impacting ingest paths
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Out-of-order event handling tested for top scenarios
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Tenant scoping validated for APIs and workers
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Secrets rotation tested and audited
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

---

## 2) Wedge 2: Reconciliation Engine (Shopify-first)

### 2.1 Matching Quality
- [ ] Auto-match rate meets agreed threshold
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Exception classification quality accepted by pilot finance owners
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Fees/taxes/payout adjustments handled in rule set
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

### 2.2 Exception Operations
- [ ] Exception queue lifecycle works (open/escalate/resolve/reopen)
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Median time-to-resolution under target (default < 48h)
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Critical exception SLA met (P0/P1 <= 24h)
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

### 2.3 Financial Trust + Close
- [ ] Reconciled journal outputs are reproducible
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Rebuild/replay checks match expected balances
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] At least 2 pilot month-end close cycles completed without unexplained deltas
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

---

## 3) Cross-Cutting Engineering

### 3.1 Test and Runtime Stability
- [ ] Core ingestion/reconciliation API tests are green and stable
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] CI critical-path suite passes consistently for 2 weeks
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] No unresolved test harness issues (redirect/env instability)
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

### 3.2 Event + Projection Health
- [ ] Projection lag SLO defined and met (example p95 < 60s)
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Event backlog alerting live and tested
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Event-first audit passes without critical violations
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

### 3.3 Release Discipline
- [ ] Zero open P0/P1 globally
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] P2 backlog triaged with owner and due date
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Rollback and incident runbook tested in staging
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

---

## 4) Product + UX (Operator-Facing)

### 4.1 Daily Workflow Fit
- [ ] Operator can complete daily recon loop without engineering help
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Top workflow friction points fixed (onboarding, triage, visibility)
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Alert noise reduced to acceptable signal-to-noise ratio
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

### 4.2 Pilot Proof
- [ ] 2-3 design partners using weekly
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Weekly feedback -> shipped fixes loop demonstrated
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Customer references approved for GTM use
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

---

## 5) Commercial + GTM Readiness

### 5.1 Packaging and Support
- [ ] Packaging finalized (shared vs dedicated DB tiers)
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Support model + SLA published
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Pricing metric validated with pilot value realization
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

### 5.2 Delivery Readiness
- [ ] Onboarding playbook documented and trialed
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Implementation checklist + migration template ready
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Security/compliance FAQ ready for prospects
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

---

## 6) GO / NO-GO Decision

### Critical GO Conditions (all required)
- [ ] Zero open P0/P1
- [ ] Core API and reconciliation test stability achieved
- [ ] Pilot references secured
- [ ] Onboarding/support processes documented and used
- [ ] Financial trust checks passed (replay/rebuild + close validation)

### Automatic NO-GO Triggers (any one blocks GA)
- [ ] Persistent API instability in core flows
- [ ] Uncontrolled projection lag/backlog
- [ ] Daily operations dependent on engineering
- [ ] Unresolved financial deltas in pilot close

Decision: `GO` / `NO-GO` / `HOLD`  
Decision Date:  
Decision Owner:  
Notes:

---

## 7) Weekly Operating Review (Template)

Week Of:  
Program Lead:  

1. Engineering Gate Status: PASS / FAIL / BLOCKED
2. Wedge 1 Gate Status: PASS / FAIL / BLOCKED
3. Wedge 2 Gate Status: PASS / FAIL / BLOCKED
4. Product/UX Gate Status: PASS / FAIL / BLOCKED
5. Commercial Gate Status: PASS / FAIL / BLOCKED
6. Top 3 Risks:
   -
   -
   -
7. Actions This Week:
   -
   -
   -
8. Decision: ADVANCE / HOLD / DE-SCOPE

---

## 8) Exit Criteria Snapshot (Target: 2026-06-04)
- [ ] Zero open P0/P1
- [ ] GO/NO-GO checklist fully signed
- [ ] Pilot reference customers confirmed
- [ ] Documented onboarding and support process in active use
