# GA Readiness Scorecard

Last updated: 2026-03-25

## Gate Status

| Gate | Description | Status | Date |
|------|------------|--------|------|
| A | CI stability (all tests green 7 days) | PASS | 2026-03-18 |
| B | Shopify reconciliation depth | PASS | 2026-03-25 |
| C | Pilot month-end close | PENDING | — |
| D | Operational proof (backup/restore) | PASS | 2026-03-15 |

## P0/P1 Completed

| Item | Commit | Description |
|------|--------|-------------|
| Dispute-won reversal | c071212 | JE reversal when merchant wins chargeback |
| FX rate fail-fast | c071212 | MissingExchangeRate exception + admin notification |
| Multi-refund matching | 44e479f | Match refund txns to closest refund by amount |
| Negative payout tests | 44e479f | Reconciliation handles negative net payouts |
| Transaction variance API | 44e479f | variance/matched/matched_to in payout txn endpoint |
| Pilot readiness command | b6bf5ef | 8-point month-end close validation |

## P2 Deferred — Guardrails

### P2-1: Transaction Re-sync

- **Risk**: If a webhook is missed, the payout transaction won't match a local order.
- **Silent misstatement?** NO — shows as "unmatched" in reconciliation report and pilot_readiness check #4.
- **Temporary runbook**: Operator runs `sync-payouts` + `verify` from Shopify dashboard before month-end close. Unmatched transactions appear in reconciliation summary.
- **Owner**: TBD
- **Target**: Before GA scale-out

### P2-2: Unmatched SKU Zero-Cost COGS

- **Risk**: Fulfillment with unrecognized SKU creates no COGS entry.
- **Silent misstatement?** NO — fulfillment status is set to `ERROR` or `PARTIAL` with `unmatched_skus` list. No JE is created for unmatched SKUs (conservative: understates COGS rather than fabricating).
- **Temporary runbook**: Operator checks `ShopifyFulfillment` records with status `ERROR`/`PARTIAL` weekly. Manually create COGS JE if needed, or link SKU via `sync-products`.
- **Owner**: TBD
- **Target**: Before GA scale-out

### P2-3: Account Mapping Validation Warnings

- **Risk**: Optional account roles (TAX, SHIPPING, DISCOUNTS, CHARGEBACK) not mapped could cause JE lines to be skipped.
- **Silent misstatement?** NO — pilot_readiness check #2 warns on missing optional mappings. Projection logs error when mapping is missing and creates INCOMPLETE entry + admin notification.
- **Temporary runbook**: Before pilot, ensure all 8 account roles are mapped via Shopify > Account Mapping page. pilot_readiness check #2 catches gaps.
- **Owner**: TBD
- **Target**: Before GA scale-out

### P2 Confirmation

All three P2 items produce **visible exceptions** (reconciliation warnings, ERROR status, INCOMPLETE entries, admin notifications). None can create silent misstatement. Each has an operator workaround documented above.

P2 items will be completed before broader GA scale-out, triggered only if pilot data shows they actively hurt close accuracy or operator time.

## Pilot Execution Checklist

Run against real company data for one full month (e.g., March 2026):

### Pre-flight
- [ ] Shopify store connected and webhooks registered
- [ ] All 8 account roles mapped (Settings > Shopify > Account Mapping)
- [ ] Exchange rates configured for any non-functional currencies
- [ ] At least one full month of order/payout data synced

### Pilot Close
- [ ] Run `python manage.py run_projections --company <slug>` (clear any lag)
- [ ] Run `python manage.py pilot_readiness --company <slug> --year 2026 --month 3 --json`
- [ ] All 8 checks PASS (warnings acceptable, failures must be resolved)
- [ ] Review trial balance in UI: debits == credits
- [ ] Review Shopify reconciliation in UI: match rate >= 95%
- [ ] Close period 3 via Periods page

### Backup/Restore Drill
- [ ] Run `python manage.py company_backup --company <slug> --out pre_close_backup.zip`
- [ ] Verify backup file is valid ZIP with expected record counts
- [ ] (Optional) Restore to test environment: `python manage.py company_restore --file pre_close_backup.zip`

### Post-Close
- [ ] Save pilot_readiness JSON output as evidence
- [ ] Note any P2 items that caused manual intervention
- [ ] Update Gate C status to PASS with date

## Deploy Checklist

```bash
cd /var/www/nxentra_app
git pull
python manage.py migrate
pm2 restart all
# NEVER touch .env
```
