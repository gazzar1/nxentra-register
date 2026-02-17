# Year-End Close Runbook

## Overview

This runbook covers the year-end close workflow for Nxentra ERP.
It includes the standard operating procedure, rollback/reopen instructions,
and incident response guidance.

---

## 1. Pre-Close Checklist

Before initiating year-end close, verify the following:

| # | Check | How to Verify |
|---|-------|---------------|
| 1 | All transactions posted | No DRAFT or INCOMPLETE entries in the fiscal year |
| 2 | Bank reconciliation complete | Bank statement balances match GL cash accounts |
| 3 | AR/AP subledger tie-out | `GET /api/reports/reconciliation/` returns `balanced: true` |
| 4 | All normal periods (1-12) closed | `GET /api/reports/periods/` shows all CLOSED |
| 5 | Period 13 exists and is OPEN | P13 visible in period list with OPEN status |
| 6 | Projections up to date | `GET /api/reports/projection-status/` shows zero lag |
| 7 | Retained earnings account identified | EQUITY account code (e.g., `3100`) ready |

### Automated Readiness Check

```
GET /api/reports/fiscal-years/{year}/close-readiness/
```

Response:
```json
{
  "fiscal_year": 2026,
  "is_ready": true,
  "checks": [
    {"check": "Fiscal year not already closed", "passed": true, "detail": ""},
    {"check": "All normal periods (1-12) closed", "passed": true, "detail": ""},
    {"check": "Period 13 (adjustment) exists and is open", "passed": true, "detail": ""},
    {"check": "No draft or incomplete journal entries", "passed": true, "detail": ""},
    {"check": "Subledger tie-out balanced", "passed": true, "detail": ""},
    {"check": "All projections up to date (no lag)", "passed": true, "detail": ""}
  ]
}
```

All checks must pass before proceeding.

---

## 2. Year-End Close Procedure

### Step 1: Close All Normal Periods

For each period 1-12:
```
POST /api/reports/periods/{year}/{period}/close/
```

### Step 2: Run Readiness Check

```
GET /api/reports/fiscal-years/{year}/close-readiness/
```

Verify `is_ready: true`.

### Step 3: Execute Close

```
POST /api/reports/fiscal-years/{year}/close/
Body: {"retained_earnings_account_code": "3100"}
```

This will:
1. Generate CLOSING journal entries in Period 13 (zeroing revenue/expense to retained earnings)
2. Lock all 13 periods (CLOSED status)
3. Mark the fiscal year as CLOSED
4. Create next year's 13 periods (only Period 1 OPEN)

### Step 4: Verify

1. Check closing entries: `GET /api/reports/fiscal-years/{year}/closing-entries/`
2. Verify retained earnings balance is correct
3. Run reconciliation: `GET /api/reports/reconciliation/`
4. Verify next year's periods exist with Period 1 OPEN

---

## 3. Rollback / Reopen Procedure

If adjustments are needed after closing:

### Step 1: Reopen Fiscal Year

```
POST /api/reports/fiscal-years/{year}/reopen/
Body: {"reason": "Auditor requested adjustments to depreciation entries"}
```

This will:
1. Create reversal entries for the original closing entries
2. Reopen Period 13
3. Mark fiscal year as OPEN
4. Preserve full audit trail (original + reversal entries)

**Important:** The original closing entries are NEVER deleted. Reversal entries
are created as compensating entries to maintain audit integrity.

### Step 2: Make Adjustments

Post adjustment entries to Period 13:
- Use entry kind `ADJUSTMENT`
- Target period 13 explicitly

### Step 3: Reclose

Repeat the close procedure (Section 2).

---

## 4. Incident Response

### Scenario: Close fails mid-operation

**Symptoms:** Close API returns 500, fiscal year partially updated.

**Resolution:**
1. Check fiscal year status: `GET /api/reports/fiscal-years/{year}/close-readiness/`
2. If year shows CLOSED but no closing entries exist:
   - Reopen: `POST /api/reports/fiscal-years/{year}/reopen/`
   - Investigate the root cause in logs
   - Retry close
3. Check structured logs: filter by `fiscal_year.closed` or `fiscal_year.reopened`

### Scenario: Tie-out mismatch after close

**Symptoms:** `post_close_tieout.balanced` is `false` in close response.

**Resolution:**
1. Run reconciliation report: `GET /api/reports/reconciliation/`
2. Compare `gl_control_balance` vs `subledger_total` for AR and AP
3. Identify the mismatched entries
4. If needed, reopen the year and post correcting entries

### Scenario: User cannot post to next year

**Symptoms:** "No fiscal period defined for this date" error.

**Resolution:**
1. Verify next year periods exist: `GET /api/reports/periods/?fiscal_year={next_year}`
2. If missing, run configure: `POST /api/reports/periods/configure/`
3. If Period 1 is CLOSED, open it: `POST /api/reports/periods/{next_year}/1/open/`

### Scenario: Projection lag blocks close readiness

**Symptoms:** "All projections up to date" check fails.

**Resolution:**
1. Check projection status: `GET /api/reports/projection-status/`
2. Wait 30 seconds and retry readiness check
3. If lag persists, check for stuck projections in admin:
   `GET /api/reports/admin/projections/`
4. Rebuild stuck projection: `POST /api/reports/admin/projections/{name}/rebuild/`

---

## 5. Monitoring & Alerts

### Key Log Events

| Log Event | Logger | Level | Meaning |
|-----------|--------|-------|---------|
| `fiscal_year.close_readiness_checked` | `nxentra.accounting.commands` | INFO | Readiness check performed |
| `fiscal_year.closed` | `nxentra.accounting.commands` | INFO | Year-end close completed |
| `fiscal_year.reopened` | `nxentra.accounting.commands` | INFO | Fiscal year reopened |
| `operational_document.posting_denied` | `nxentra.accounting.policies` | WARNING | Receipt/payment blocked by period policy |
| `operational_document.p13_blocked` | `nxentra.accounting.policies` | WARNING | Operational doc blocked from P13 |

### Recommended Alerts

1. **fiscal_year.closed** - Notify finance team (Slack/email)
2. **fiscal_year.reopened** - Alert finance manager + audit committee
3. **operational_document.posting_denied** count > 10/hour - Possible misconfiguration
4. **Projection lag > 0 for > 5 minutes** - Infrastructure issue

---

## 6. Contacts

| Role | Responsibility |
|------|---------------|
| BE-Lead | Command layer fixes, closing entry logic |
| FE-Lead | UI issues in period management |
| DevOps | Projection infrastructure, CI/CD |
| Finance Manager | Business approval for close/reopen |
| Auditor | External verification of closing entries |
