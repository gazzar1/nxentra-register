# UAT Checklist: Year-End Close & Period Management

**Version:** 1.0
**Date:** February 2026
**Sign-off required from:** BE-Lead, QA-Lead, DevOps, PM

---

## Instructions

For each scenario, execute the steps described and mark PASS/FAIL.
A passing UAT requires zero P0/P1 defects open.

### Environment Setup

```bash
# Backend (from backend/ directory)
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver

# Frontend (from frontend/ directory)
npm install
npm run dev
```

### Running Automated Tests Locally

```bash
# Unit + integration tests (SQLite, fast)
cd backend
python -m pytest tests/ accounting/tests/ events/tests/ -v --tb=short

# E2E fiscal year lifecycle (SQLite)
python -m pytest tests/e2e/test_fiscal_year_lifecycle.py -v --tb=long

# E2E on Postgres (full fidelity, requires Postgres running)
TEST_DATABASE_URL=postgres://user:pass@localhost:5432/nxentra_test \
  python -m pytest tests/e2e/ -v --tb=long --create-db
```

### Manual Testing

Use the running app at `http://localhost:3000`. Log in as an OWNER user.
Navigate to **Settings > Fiscal Periods** for year-end workflows.
Use **Reports** menu for reconciliation and closing entries review.

---

## Section A: Journal Entry Lifecycle

| # | Scenario | Steps | Expected | Status |
|---|----------|-------|----------|--------|
| A1 | Create and post journal entry | Create JE with 2+ lines, complete, post | Entry status = POSTED, entry_number assigned, posted_at set | |
| A2 | Reverse posted entry | Reverse a POSTED entry | Reversal created with swapped debits/credits, original status = REVERSED | |
| A3 | Reverse uses original date | Post entry in Jan, reverse in Feb | Reversal's period = January (original entry's period), not February | |
| A4 | Cannot reverse non-POSTED | Try to reverse DRAFT entry | Error: "Only POSTED entries can be reversed" | |
| A5 | Cannot reverse non-NORMAL | Try to reverse CLOSING entry | Error: "Only NORMAL entries can be reversed" | |
| A6 | Unbalanced entry rejected | Complete entry with mismatched debits/credits | Error at complete step | |

## Section B: Period Management

| # | Scenario | Steps | Expected | Status |
|---|----------|-------|----------|--------|
| B1 | Close period | Close Period 1 | Period status = CLOSED | |
| B2 | Post blocked in closed period | Post JE dated in closed period | Error: "Fiscal period X is closed" | |
| B3 | Open period | Reopen a closed period | Period status = OPEN | |
| B4 | Open period blocked in closed FY | Close FY, then try opening Period 1 | Error: "Cannot open period in closed fiscal year" | |
| B5 | Configure periods with P13 | Configure 13 periods | 12 NORMAL + 1 ADJUSTMENT period created | |

## Section C: Operational Document Period Controls

| # | Scenario | Steps | Expected | Status |
|---|----------|-------|----------|--------|
| C1 | Receipt in open period | Record customer receipt dated in open period | Receipt created successfully | |
| C2 | Receipt in closed period | Close period, record receipt dated in that period | Error: period is closed | |
| C3 | Payment in open period | Record vendor payment dated in open period | Payment created successfully | |
| C4 | Payment in closed period | Close period, record payment dated in that period | Error: period is closed | |
| C5 | Receipt blocked in P13 | Record receipt with date matching P13 | Error: "Operational documents cannot be posted to the adjustment period" | |
| C6 | Receipt blocked in closed FY | Close FY, record receipt in that year | Error: period/year is closed | |

## Section D: Year-End Close Workflow

| # | Scenario | Steps | Expected | Status |
|---|----------|-------|----------|--------|
| D1 | Readiness check — not ready | Check readiness with open periods | `is_ready: false`, checks array shows failed items with detail | |
| D2 | Readiness check — ready | Close all 12 periods, check readiness | `is_ready: true`, all checks passed | |
| D3 | Readiness check — draft entries block | Leave draft JE, check readiness | `is_ready: false`, draft check fails | |
| D4 | Close fiscal year | Provide retained earnings code, close | Closing entries in P13, FY status CLOSED, net_income correct | |
| D5 | Net income calculation | Post $10k revenue, $3k expense, close | net_income = $7,000 | |
| D6 | Closing entries review | After close, GET closing-entries | Entries have `entry_public_id`, `kind: CLOSING`, `period: 13` | |
| D7 | Next year created | After close, check next year periods | 13 periods exist, only Period 1 is OPEN | |
| D8 | Post-close reconciliation | Check close response `post_close_tieout` | `balanced: true` | |
| D9 | Close idempotency | Close already-closed FY | Returns `already_closed: true` (no error) | |

## Section E: Year-End Reopen & Reclose

| # | Scenario | Steps | Expected | Status |
|---|----------|-------|----------|--------|
| E1 | Reopen fiscal year | Reopen closed FY with reason | FY status OPEN, reversal entries created, P13 reopened | |
| E2 | Reopen reason required | Reopen without reason | Error: "reason is required" | |
| E3 | Post adjustment after reopen | Reopen FY, post ADJUSTMENT entry to P13 | Entry posted successfully in P13 | |
| E4 | Reclose after reopen | Reopen, close periods, reclose FY | FY closed again, new closing entries created | |
| E5 | Audit trail preserved | After reopen+reclose, review all entries | Original close + reversal + new close all visible | |

## Section F: Reconciliation

| # | Scenario | Steps | Expected | Status |
|---|----------|-------|----------|--------|
| F1 | Reconciliation check | GET /api/reports/reconciliation/ | AR and AP sections with gl_control_balance, subledger_total, difference, balanced | |
| F2 | Clean reconciliation | No mismatches in AR/AP | `balanced: true` for both AR and AP | |

## Section G: Frontend UX

| # | Scenario | Steps | Expected | Status |
|---|----------|-------|----------|--------|
| G1 | Readiness display | Run readiness check in UI | Each check shown with pass/fail icon | |
| G2 | Failed check remediation | View failed readiness checks | Blue remediation hint text shown below each failed check | |
| G3 | Close button gated | View readiness when not ready | "Close Fiscal Year" button not shown | |
| G4 | Period 13 visual | View periods list | P13 shown separately as "Adjustment Period" | |
| G5 | Close/reopen flow | Complete close + reopen via UI | Toast messages confirm success, periods refresh | |

## Section H: Observability

| # | Scenario | Steps | Expected | Status |
|---|----------|-------|----------|--------|
| H1 | Close logged | Close FY, check logs | `fiscal_year.closed` log entry with company_id, fiscal_year, net_income | |
| H2 | Reopen logged | Reopen FY, check logs | `fiscal_year.reopened` log entry with reason | |
| H3 | Policy denial logged | Post receipt in closed period, check logs | `operational_document.posting_denied` warning logged | |

---

## Sign-off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| BE-Lead | | | |
| QA-Lead | | | |
| DevOps | | | |
| PM | | | |

**Criteria:** Zero P0/P1 defects open. All Section A-H scenarios PASS.
