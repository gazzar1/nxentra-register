# Nxentra Pilot GA Lite Scorecard (Connect)

Version: 1.0
Last Updated: 2026-03-25
Scope: Design-partner launch (not full enterprise GA)
Review Cadence: Weekly

## Purpose
- Keep launch discipline high without overloading a small team.
- Focus on the minimum set of gates that protect financial trust and operator confidence.

## Status Legend
- PASS: Exit criteria met with evidence
- FAIL: Criteria not met
- BLOCKED: External dependency or unresolved prerequisite

---

## Gate A: Test Stability (Must Pass)

- [ ] Core ingestion and reconciliation API tests are green in CI
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] No unresolved redirect/env test harness issue on critical APIs
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Critical path suite stable for 7 consecutive days
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

Exit rule:
- Gate A is PASS only if all 3 items pass.

---

## Gate B: Shopify Reconciliation Depth (Must Pass)

- [ ] Core Shopify payout reconciliation flow works end-to-end
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Exception queue lifecycle works (open/escalate/resolve/reopen)
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Fee/refund/adjustment edge cases covered by tests and runbook
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

Exit rule:
- Gate B is PASS only if all 3 items pass.

---

## Gate C: One Clean Pilot Month-End Close (Must Pass)

- [ ] One real pilot close completed on scoped data set
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] No unexplained financial delta after reconciliation and posting
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Rebuild/replay reproduces the same accounting outcome
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

Exit rule:
- Gate C is PASS only if all 3 items pass.

---

## Gate D: Backup/Restore Confidence (Must Pass)

- [ ] Backup job documented and runnable by operator
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Restore drill executed in non-prod and verified
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:
- [ ] Recovery time and data integrity are acceptable for pilot SLA
  - Owner:
  - Target Date:
  - Status:
  - Evidence:
  - Blockers:

Exit rule:
- Gate D is PASS only if all 3 items pass.

---

## Pilot Launch GO / NO-GO

GO only if:
- [ ] Gate A PASS
- [ ] Gate B PASS
- [ ] Gate C PASS
- [ ] Gate D PASS
- [ ] Zero open P0/P1 defects in scoped pilot flows

Automatic NO-GO if any are true:
- [ ] Core API behavior unstable in pilot scope
- [ ] Unresolved financial delta in pilot close
- [ ] Restore drill failed or data integrity unverified
- [ ] Daily reconciliation still requires engineering intervention

Decision: `GO` / `NO-GO` / `HOLD`
Decision Date:
Decision Owner:
Notes:

---

## Weekly Review Template

Week Of:
Program Lead:

1. Gate A Status: PASS / FAIL / BLOCKED
2. Gate B Status: PASS / FAIL / BLOCKED
3. Gate C Status: PASS / FAIL / BLOCKED
4. Gate D Status: PASS / FAIL / BLOCKED
5. Top 3 Risks:
   -
   -
   -
6. Actions This Week:
   -
   -
   -
7. Decision: ADVANCE / HOLD / DE-SCOPE

