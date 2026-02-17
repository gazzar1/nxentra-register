# Go / No-Go Review Checklist

**Version:** 1.0
**Date:** February 2026
**Meeting Lead:** PM
**Attendees:** BE-Lead, FE-Lead, QA-Lead, DevOps, PM

---

## Instructions

Before the release decision meeting, each owner fills in their section.
A **GO** decision requires:
- Zero P0/P1 defects open
- All mandatory items marked PASS
- All owners sign off

If any mandatory item is FAIL, the decision is **NO-GO** unless the team agrees on a documented mitigation.

---

## 1. Code Quality & Testing

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 1.1 | All unit tests pass (`pytest tests/ -v`) | BE-Lead | | |
| 1.2 | All e2e tests pass on Postgres (`pytest tests/e2e/ -v`) | BE-Lead | | |
| 1.3 | Frontend builds without errors (`npm run build`) | FE-Lead | | |
| 1.4 | CI pipeline green on main branch | DevOps | | |
| 1.5 | No P0/P1 defects open | QA-Lead | | |
| 1.6 | Code review completed on all merge commits | BE-Lead | | |

## 2. UAT Sign-off

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 2.1 | UAT checklist Sections A-H all PASS | QA-Lead | | |
| 2.2 | Year-end close workflow validated end-to-end | QA-Lead | | |
| 2.3 | Reopen + reclose workflow validated | QA-Lead | | |
| 2.4 | Operational document period controls validated | QA-Lead | | |
| 2.5 | UAT sign-off document signed by all roles | PM | | |

## 3. Data Integrity

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 3.1 | Reconciliation check passes (`python manage.py reconciliation_check --strict`) | BE-Lead | | |
| 3.2 | AR subledger ties out to GL control | BE-Lead | | |
| 3.3 | AP subledger ties out to GL control | BE-Lead | | |
| 3.4 | Event stream integrity verified (no gaps) | BE-Lead | | |
| 3.5 | Projection lag < threshold | DevOps | | |

## 4. Infrastructure & Operations

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 4.1 | Production database migrated (`manage.py migrate`) | DevOps | | |
| 4.2 | Backup/restore drill passed | DevOps | | |
| 4.3 | Health endpoints responding (`/_health/live`, `ready`, `full`) | DevOps | | |
| 4.4 | Metrics endpoint responding (`/_metrics`) | DevOps | | |
| 4.5 | Log aggregation confirmed (structured JSON in pipeline) | DevOps | | |
| 4.6 | SSL/TLS certificates valid and not expiring within 30 days | DevOps | | |

## 5. Security

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 5.1 | Security check script passed (`scripts/security-check.sh`) | DevOps | | |
| 5.2 | No hardcoded secrets in codebase | DevOps | | |
| 5.3 | Dependency vulnerability scan clean (pip-audit, npm audit) | DevOps | | |
| 5.4 | Django deployment checks pass (`manage.py check --deploy`) | DevOps | | |
| 5.5 | RLS policies active on all tenant tables (Postgres) | BE-Lead | | |
| 5.6 | CORS restricted to allowed origins | DevOps | | |
| 5.7 | Rate limiting configured and tested | BE-Lead | | |

## 6. Monitoring & Alerting

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 6.1 | Reconciliation monitoring scheduled (daily) | DevOps | | |
| 6.2 | Projection lag alerting configured | DevOps | | |
| 6.3 | Error rate alerting configured | DevOps | | |
| 6.4 | Disk space / DB size alerting configured | DevOps | | |

## 7. Rollback Plan

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 7.1 | Pre-release database backup taken | DevOps | | |
| 7.2 | Rollback procedure documented in runbook | BE-Lead | | |
| 7.3 | Previous version artifact available for rollback | DevOps | | |
| 7.4 | Rollback tested in staging | DevOps | | |

## 8. Release Smoke Test

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 8.1 | RC smoke test passed (`scripts/rc-smoke-test.sh`) | QA-Lead | | |
| 8.2 | Login flow works end-to-end | QA-Lead | | |
| 8.3 | Journal entry create/post/reverse works | QA-Lead | | |
| 8.4 | Period close/open works | QA-Lead | | |
| 8.5 | Receipts and payments work in open period | QA-Lead | | |

---

## Decision

| Decision | Date | Justification |
|----------|------|---------------|
| GO / NO-GO | | |

### Open Items (if NO-GO)

| # | Item | Owner | Target Date |
|---|------|-------|-------------|
| | | | |

---

## Sign-off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| BE-Lead | | | |
| FE-Lead | | | |
| QA-Lead | | | |
| DevOps | | | |
| PM | | | |

**Final authority:** PM makes the GO/NO-GO call based on team input.
