# Fresh isolated pilot — executable runbook

**Status: PROCEDURE ONLY. This document has not been executed.** It defines
how to take a reviewed revision from a clean deployment target to a
supervised first-pilot environment and then prove G1 and G2. Writing it
closes nothing: **G1 and G2 remain OPEN and merchant data remains blocked**
until the live tracker
([constrained_pilot_status.md](../status/constrained_pilot_status.md))
records their closure with evidence.

Authorities: the [architecture constitution](../architecture/architecture-constitution.md),
the [supported product contracts](../architecture/supported-product-contracts.md),
the [live tracker](../status/constrained_pilot_status.md), and live code.
Where this runbook and live code disagree, the code is authoritative and this
runbook must be corrected before use.

---

## A. Scope and non-claims

This runbook applies only to **`ISOLATED_SHADOW_LEDGER_V1`**.

It prepares and proves one founder-operated deployment for one Egyptian
Shopify merchant, one active store, EGP-only accounting, Paymob/Bosta
settlement CSVs, canonical bank CSV, reconciliation, General Ledger, and
supervised traced manual adjustments.

It does **not** certify: statutory accounts; tax filing; inventory or COGS;
foreign currency; Stripe or Shopify Payments payout accounting; multiple
users; shared multi-merchant deployment; private beta; GA readiness.

Every phase below follows the pattern **Command / action → Expected result →
Evidence to retain → STOP if**. Evidence goes into the manifest structure in
§P — never into Git, never with secrets or merchant PII.

Sign-off model: the **founder/operator** makes every stop/go decision and
dates each phase sign-off field. No step may be marked complete without its
named evidence existing in the manifest.

---

## B. Phase 0 — Revision and evidence pin

- [ ] **B1. Pin the revision.**
  - Command / action: on the deployment working copy —
    `git rev-parse HEAD` and `git rev-parse HEAD^{tree}` and
    `git status --porcelain` (must be empty).
  - Expected result: HEAD is a commit on `main` that has a fully green CI
    run (all seven jobs including Quality Gate). Expected revision for the
    first execution: `c4896ff31b283d165a5180ca210cb981a93b3588`
    (tree `8104cf6824e473e92cc4c184c04525cd160ba95f`; main CI run
    33394909451, seven jobs green). A later revision may be used only when
    its own required CI is green and the operator records the new exact SHA.
  - Evidence to retain (`revision/`): commit SHA, tree SHA, CI run id and
    conclusion, deployment/image identifier, deployment timestamp, operator,
    database identifier (host/name only — no credentials), hosting region,
    and this runbook's revision.
  - STOP if: the working tree is dirty, the SHA is not on `main`, the branch
    is unreviewed, or the pinned revision's CI is not green. **An
    uncommitted local tree or an unreviewed branch must never be deployed.**

Sign-off: operator ______ date ______

---

## C. Phase 1 — Fresh isolated database proof

The pilot database must be **provably fresh** before the pilot company is
created. Do not substitute destructive cleanup for freshness: if unexpected
business history exists, STOP — recreate the deployment from a new empty
database, or explicitly choose the legacy-history path and audit it (§D).

- [ ] **C1. Migrate the empty database.**
  - Command / action: from `backend/`, with the production environment of
    §E already in place: `python manage.py migrate` then
    `python manage.py seed_permissions` and
    `python manage.py backfill_role_permissions`.
  - Expected result: migrations apply cleanly; `seed_permissions` and
    `backfill_role_permissions` complete (both are idempotent; with zero
    memberships the backfill reports 0 updated).
  - Evidence to retain (`environment/`): full migrate output.
  - STOP if: any migration fails or targets an unexpected database.

- [ ] **C2. Row-count proof (read-only).**
  - Command / action: from `backend/` (operator-only action; read-only):

    ```bash
    python manage.py shell -c "
    from accounts.rls import rls_bypass
    from accounts.models import Company, PilotProfileActivation
    from events.models import BusinessEvent, EventBookmark
    from accounting.models import JournalEntry, JournalLine, BankStatement, BankStatementLine, ImportRejectedRow
    from bank_connector.models import BankStatement as ConnectorBankStatement
    from shopify_connector.models import ShopifyStore, ShopifyRejectedEvidence
    from platform_connectors.models import PlatformSettlement, ProviderRawObject, ProviderPayout, ProviderPayoutLine
    from projections.models import ProjectionAppliedEvent, ProjectionFailureLog
    with rls_bypass():
        for m in (Company, PilotProfileActivation, BusinessEvent, EventBookmark,
                  JournalEntry, JournalLine, BankStatement, BankStatementLine,
                  ImportRejectedRow, ConnectorBankStatement, ShopifyStore,
                  ShopifyRejectedEvidence, PlatformSettlement, ProviderRawObject,
                  ProviderPayout, ProviderPayoutLine, ProjectionAppliedEvent,
                  ProjectionFailureLog):
            print(m._meta.label, m.objects.count())
    "
    ```

    The `rls_bypass()` wrapper is load-bearing: on PostgreSQL with forced
    row-level security, a plain shell query silently returns **0 rows**
    for RLS-covered tables regardless of content — an unwrapped count is
    not evidence of freshness.
  - Expected result: **every count is 0.** (On a freshly migrated empty
    database no migration or bootstrap creates rows in any listed model.
    The one later caveat: `EventBookmark` rows are created lazily the first
    time projections run — after C4, nonzero bookmarks alone do not imply
    merchant data; at THIS step, before any boot, the count must be 0.)
  - Evidence to retain (`environment/`): the full count output with
    timestamp and database identifier.
  - STOP if: any count is nonzero. Do not delete or reset the data
    casually — recreate from a new empty database, or explicitly take the
    audited legacy-history path (§D).

Sign-off: operator ______ date ______

---

## D. Conditional legacy corpus rule

1. **Fresh isolated database receiving no legacy BusinessEvents:**
   `audit_posted_journal_corpus` is **optional evidence, not a gate**.
2. **Any reuse, migration, restore, or replay of legacy event history:**
   run `python manage.py audit_posted_journal_corpus --strict --json`
   **before** that history is admitted; retain the full result
   (`preflight/`); STOP on every unexplained violation
   (`--strict` exits nonzero on any violation or unreadable payload).

Do not make the legacy-droplet corpus scan a prerequisite for a genuinely
fresh deployment.

---

## E. Phase 2 — Environment-safety check

Produce a recorded, **secret-free** environment-safety report
(`environment/`): variable **names** and presence/boolean state only —
never values for secrets.

- [ ] **E1. Settings-module identity.**
  - Command / action: confirm `DJANGO_SETTINGS_MODULE` is
    `nxentra_backend.settings` (or unset — WSGI/ASGI/Celery default to it
    and refuse any other value at startup).
  - Expected result: web (WSGI), worker, and beat all boot under
    `nxentra_backend.settings`.
  - STOP if: any process was started under another settings module.

- [ ] **E2. Fail-closed flag sweep.** Confirm ALL of the following are
  absent from the production environment (with `DEBUG` false, boot itself
  is the backstop: it refuses `PYTEST_CURRENT_TEST` on presence and the
  other four on truthy values — verify absence anyway):
  `PYTEST_CURRENT_TEST`, `DJANGO_TEST_MODE`, `TESTING`, `RLS_BYPASS`,
  `DISABLE_EVENT_VALIDATION`. Additionally confirm
  `ALLOW_ADMIN_EMERGENCY_WRITES` is absent or false (it is a deliberate
  emergency valve, not refused at boot — it must be OFF for the pilot).
  - STOP if: any flag is present/truthy.

- [ ] **E3. Required production values.** Confirm presence (names only):
  - `DEBUG` absent or false; `SECRET_KEY` set (boot refuses the
    `changeme` default); `FIELD_ENCRYPTION_KEY` set and valid (boot
    validates); `DATABASE_URL` pointing at the pilot database;
    `REDIS_URL`; `PROJECTIONS_SYNC=True` (boot refuses otherwise in
    production — A162); `ALLOWED_HOSTS` for `<DEPLOYMENT_HOST>`;
    `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` set to the real
    https origins (boot refuses wildcard/localhost values in production);
    `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`, `SHOPIFY_APP_URL`;
    `SENTRY_DSN`; email settings as chosen. Optional tunables with
    defaults: `ALERT_UNRESOLVED_FAILURES_MAX` (0),
    `ALERT_PROJECTION_LAG_THRESHOLD` (50),
    `ALERT_PROJECTION_STALENESS_SECONDS` (21600),
    `SHOPIFY_SOURCE_STALE_SECONDS` (28800).
  - STOP if: a required value is missing, or the process would start
    against the wrong database.

- [ ] **E4. Deploy check.**
  - Command / action: `python manage.py check --deploy --fail-level WARNING`
  - Expected result: exit 0, zero warnings (this is the same gate CI's
    Security & Deploy Check job enforces).
  - Evidence to retain: full output.
  - STOP if: any warning or error remains, or a secret value appears in
    captured evidence (redact and re-capture before proceeding).

Sign-off: operator ______ date ______

---

## F. Phase 3 — G1 pre-activation blockers

**Pilot activation and merchant data remain forbidden until all three are
dispositioned with evidence.** Writing them here does not complete them.

- [ ] **F1. ShopifyStore PENDING-sweep history protection.**
  - Required outcome: a store with canonical history cannot be deleted by
    an abandoned reconnect/PENDING cleanup.
  - Acceptable implementation: a has-history guard, or retaining/returning
    the store to DISCONNECTED instead of deleting canonical-history
    mirrors.
  - Evidence field (`preflight/`): fixing PR ______ and regression test ______.

- [ ] **F2. Health-endpoint publication restriction.**
  - Required outcome: the specifically approved aggregate
    `/_health/alerts` endpoint is reachable by the monitoring path (the
    documented pinger posture also watches `/_health/ready`);
    `/_health/full` and `/_metrics/` are blocked or internally restricted.
    All health endpoints are unauthenticated by design and MUST be
    network-protected — the repository ships no reverse-proxy
    configuration, so this restriction lives in the deployment's proxy/
    firewall and must be proven, not assumed.
  - Evidence field (`preflight/`): reverse-proxy/config test and external
    HTTP proof (from outside the host: `/_health/alerts` answers;
    `/_health/full` and `/_metrics/` are refused) ______.

- [ ] **F3. Shopify webhook-throttle decision.**
  - Required outcome: either evidence that the first merchant's realistic
    webhook burst plus retries fits safely within the configured policy,
    or a dedicated HMAC-protected Shopify webhook throttle posture.
  - Evidence field (`preflight/`): written decision, configuration, and
    burst/retry proof ______.

STOP if: any of F1–F3 lacks its evidence at the moment activation (§I) is
attempted.

Sign-off: operator ______ date ______

---

## G. Phase 4 — Deployment and service startup

The repository defines the process commands below but ships **no** backend
process-manager units (no systemd/pm2 config for the backend, no nginx
config, no backend deploy script). The chosen supervisor, proxy, and TLS
termination are deployment-specific: record what is used
(`environment/`), then verify each service against this table. Do not
introduce infrastructure this repository does not use.

- [ ] **G1a. Deploy the pinned revision** (checkout exactly the §B SHA on
  the host / build the image from it) and install backend requirements and
  frontend dependencies.
- [ ] **G1b. Database migration.**
  - Command / action: `python manage.py migrate` (then the C1 permission
    seeding if not already run).
  - Expected result: clean apply against the pilot database.
- [ ] **G1c. Frontend build.**
  - Command / action: from `frontend/`: `npm ci` then `npm run build`.
  - Expected result: build succeeds and `.next/BUILD_ID` exists.
- [ ] **G1d. Start services.** For every service record: expected process,
  expected version/SHA, startup command, health signal, log location,
  restart behavior.

  | Service | Startup command (verified) | Health signal |
  |---|---|---|
  | Web (Django) | `gunicorn nxentra_backend.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120` | `GET /_health/live` → 200; `GET /_health/ready` → 200 |
  | Worker | `celery -A nxentra_backend worker -l INFO` | worker log shows ready; `/_health/alerts` not reporting missing consumers after first drain |
  | Beat | `celery -A nxentra_backend beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler` | beat log ticking; scheduled tasks appear in worker log |
  | Broker | Redis reachable at `REDIS_URL` | `/_health/full` `redis` check |
  | Frontend | `npm run start` (Next.js, port 3000; the checked-in PM2 app name for the frontend is `nxentra-web`) | `GET /` on the frontend → 200 |
  | Reverse proxy + TLS | deployment-specific | https reaches frontend and API; §F2 restriction in force |

  Known, accepted quirk: `STATIC_ROOT` is not defined, so `collectstatic`
  cannot succeed (the backend Dockerfile deliberately ignores its failure);
  Django static assets are not part of this deployment's serving path.
- [ ] **G1e. Version proof.**
  - Command / action: verify every running service is executing the §B
    revision (image tag / deployed checkout SHA per service).
  - STOP if: **any service is running a different application revision.**
- [ ] **G1f. Boot health.**
  - Command / action: `curl` `/_health/live`, `/_health/ready`,
    `/_health/full` (internal path), and run
    `python manage.py alert_check`.
  - Expected result: live/ready 200; full reports `healthy` (503 with any
    failing check names otherwise); `alert_check` prints the aggregate
    state and exits 0.
  - Evidence to retain (`environment/`): the four outputs.
  - STOP if: full health or `alert_check` is unhealthy at boot on an empty
    database — that indicates a mis-set environment, not merchant data.

Sign-off: operator ______ date ______

---

## H. Phase 5 — Single-company bootstrap

Use the supported HTTP/frontend surfaces. Operator-only CLI actions are
labelled. Do not use Django admin or raw SQL for bootstrap.

- [ ] **H1. Create the operator account and the one company.**
  - Command / action: `POST /api/auth/register/` (frontend `/register`)
    with the founder-operator email, the merchant company name, and —
    **load-bearing** — currency **EGP**. `functional_currency` is set only
    at signup and has no later HTTP writer; preflight requires both
    `default_currency` and `functional_currency` to be EGP.
  - Expected result: company + OWNER membership created; email
    verification required before login (operator-only alternative:
    `python manage.py verify_user --email <OPERATOR_EMAIL>`). If
    `BETA_GATE_ENABLED` is set, complete the gate flow.
  - STOP if: the company was created with any currency other than EGP —
    recreate the company; do not attempt to edit currency afterward.
- [ ] **H2. Complete onboarding.**
  - Command / action: frontend `/onboarding/setup`
    (`POST /api/onboarding/setup/`), with: fiscal year starting
    **January** with **12 or 13 periods** (preflight enforces both), a
    chart-of-accounts template (`minimal` or `retail`), and
    `business_type = "shopify"` — this auto-provisions the GL accounts and
    the `shopify_connector` `SHOPIFY_CLEARING` (11500) and
    `EXPECTED_BANK_DEPOSIT` (11600) module-account mappings.
    The EBD mapping is provisioned **only** by this onboarding path — the
    account-mapping PUT endpoint does not carry that role.
  - Expected result: onboarding completes; accounts and both mappings
    exist. (The POST is idempotent and may be re-run.)
- [ ] **H3. Connect exactly one Shopify store.**
  - Command / action: from `/onboarding/setup` or `/shopify/settings`:
    `POST /api/shopify/install/` → complete OAuth for `<SHOP_DOMAIN>`.
  - Expected result: exactly one ACTIVE store; the OWNER's Shopify user
    binding exists (go-live preflight checks `store_count` and
    `binding_missing`).
- [ ] **H4. Prove the store currency is EGP.**
  - Command / action: run an initial **product sync** — that is the sync
    path that persists the durable `shop_currency` snapshot (an order sync
    alone does not persist it; the go-live preflight then falls back to a
    read-only live probe). The go-live preflight
    (`store_currency_unknown` / `store_currency_not_egp`) is the proof.
  - STOP if: preflight reports either store-currency violation.
- [ ] **H5. Settlement providers and posting profiles.**
  - Command / action: the Shopify setup bootstraps the provider rows and
    `PG-*` posting profiles; review at `/shopify/settings`
    (`PATCH /api/accounting/settlement-providers/<pk>/`) so that at least
    one of **paymob** / **bosta** is ACTIVE — and note the preflight
    checks **every** ACTIVE supported provider, so each provider left
    ACTIVE must route to an ACTIVE posting profile with a postable
    control account (preflight codes `provider_missing`,
    `provider_posting_profile`).
- [ ] **H6. Cash/Bank account.**
  - Command / action: confirm an ACTIVE, non-header LIQUIDITY account
    exists (template account `11000 Cash and Bank`, or create one at
    `/accounting/chart-of-accounts/new`). Preflight code:
    `bank_account_missing`.
- [ ] **H7. Single-company / single-owner proof.**
  - Command / action: re-run the §C2 count for `Company` (expect exactly
    1) and confirm in the UI there is exactly one active OWNER membership
    and no other members. (After activation, `deployment_has_pilot()`
    blocks all further signup/company creation deployment-wide.)
  - STOP if: more than one company or active membership exists.
- [ ] **H8. Excluded modules stay unavailable.**
  - Command / action: confirm purchases/clinic/properties are not enabled
    and their enable doors refuse under the pilot (spot-check one refusal
    after activation in §J).

Evidence to retain (`preflight/`): screenshots or API responses for each
step, redacting any merchant PII.

Sign-off: operator ______ date ______

---

## I. Phase 6 — Preflight and activation

Distinct stages: pre-activation configuration checks → profile activation →
go-live preflight → post-activation drift verification. For every run
record: exact command, company identifier, phase, exit code, complete
output, timestamp, operator (`preflight/`).

**Never repair or suppress a violation inside preflight.** Correct the cause
through its owning configuration or process, then rerun the full preflight.

- [ ] **I1. Setup-phase preflight (read-only).**
  - Command / action:
    `python manage.py pilot_preflight --company <PILOT_COMPANY_ID> --phase setup --json`
    (`<PILOT_COMPANY_ID>` is the numeric Company id.)
  - Expected result: `ok: true`, exit 0.
  - STOP if: any violation (exit 1) — fix the owning configuration, rerun.
- [ ] **I2. Activate the profile.**
  - Command / action:
    `python manage.py activate_pilot_profile --company <PILOT_COMPANY_ID> --yes`
  - Expected result:
    `Activated ISOLATED_SHADOW_LEDGER_V1 on company <PILOT_COMPANY_ID>.`
    exit 0; a `PilotProfileActivation` audit row (source `cli`) exists.
    (Without `--yes` a clean validation still exits 1 with
    "Re-run with --yes to activate." — that is the confirmation step, not
    an error.)
  - STOP if: `Refusing to activate: …` — violations are listed; nothing
    was modified; correct and return to I1.
- [ ] **I3. Go-live preflight.**
  - Command / action:
    `python manage.py pilot_preflight --company <PILOT_COMPANY_ID> --phase go-live --json`
  - Expected result: `ok: true`, exit 0 — this is the full agreed-workflow
    proof (EGP store, OWNER↔store binding, postable clearing/EBD mappings,
    active provider + posting profile, canonical bank account).
  - STOP if: any violation.
- [ ] **I4. Drift verification cadence.** Re-run I3 after every
  sync/import during the rehearsal (§J) and before every sign-off in this
  runbook. Any new violation is a STOP.

Sign-off: operator ______ date ______

---

## J. Phase 7 — G1 rehearsal matrix

One controlled, current-head rehearsal using synthetic or
merchant-approved test data. Where the supported workflow is user-facing,
run it through the real deployed application surfaces (frontend pages and
HTTP APIs), not test harnesses. Existing test fixtures and documented APIs
are references only. Record every scenario's evidence in
`failure-injection/` and `reconciliation/`.

For each row: run → verify the durable outcome and its operator surface →
verify `/_health/alerts` (or `python manage.py alert_check`) reflects it →
verify recovery where the contract heals.

- [ ] **J1. Shopify paid order:** successful order; exact
  redelivery/idempotent retry (no duplicate journal); mapping failure →
  visible failure → correction → retry heals exactly once.
- [ ] **J2. Shopify refund:** positive refund; zero-refund outcome where
  supported; malformed/negative refund → durable rejection
  (ShopifyRejectedEvidence, no row/event/journal); corrected redelivery
  supersedes and posts exactly once.
- [ ] **J3. Paymob/Bosta settlement** (frontend
  `/finance/settlements/import`): valid batch; malformed row → per-row
  REJECTED evidence while the clean subset posts; imbalance/quarantine
  (orphan order id → QUARANTINED review flag iff the journal committed);
  duplicate re-upload → no duplicate financial effect.
- [ ] **J4. Bank CSV** (frontend `/accounting/bank-reconciliation/import`;
  the commit must carry currency EGP): valid debit/credit rows; malformed
  row → durable reject; duplicate row → counted, not re-imported;
  non-EGP file → refused with zero rows persisted.
- [ ] **J5. Reconciliation:** permitted manual match; difference
  resolution (adjustment JE via the one correction path); permitted
  never-matched nuisance-row exclusion (durable EXCLUDED — the A5
  REJECTED-class outcome); proof that match-destructive exclusion and
  unmatch are refused (HTTP 403 `pilot_scope_blocked`).
- [ ] **J6. Pilot adjustment:** traced draft; post with typed source +
  10–180-char reason; inspect provenance on the journal detail page;
  reversal with its own reason; verify an untraceable manual post refuses
  with zero residue.
- [ ] **J7. Projection/visibility:** deliberate retryable failure →
  visible failure/exception row; `/_health/alerts` flips unhealthy (503);
  corrected retry/self-heal; the endpoint returns healthy **only after the
  evidence is actually resolved/healed** — never by deletion or timeout.

STOP if any scenario yields: a silent missing financial effect, a duplicate
financial effect, a false success, terminal evidence loss, a false
all-clear, or an unhealable corrected retry.

Sign-off: operator ______ date ______

---

## K. Phase 8 — Independent control pack

Nxentra's own totals are not the sole oracle. Build the pack from the
**source files and Shopify records directly**, then reconcile against the
deployed system (`source-controls/`). Record source-file SHA-256 hashes.

Minimum schema:

| Control | Source of truth |
|---|---|
| Shopify order/refund IDs, counts, gross totals | Shopify admin/exports |
| Settlement row count, gross, fee, net totals | the CSV files |
| Bank line count, debit total, credit total | the CSV files |
| Event counts by relevant type | system (read-only) |
| Posted-JE count; total debits == total credits | system reports |
| Provider-clearing balance | GL drilldown |
| Expected Bank Deposit balance | GL drilldown |
| Bank/cash balance | GL drilldown |
| Reconciliation counts and unmatched amount | reconciliation page |
| Rejected / quarantined / failed item counts | exceptions queue |
| Traced-adjustment count and value | journal list (pilot adjustments) |
| Trial-balance control total | trial balance report |
| Every unexplained variance | — must be zero — |

Every source row and amount must be accounted for as exactly one of:
**posted, matched, rejected, failed, quarantined, or intentionally excluded
under the documented nuisance-row mapping** (the A5 REJECTED-class outcome
in the [closure artifact](../audits/2026-08-30-a5-final-closure-review.md)).

STOP on any unexplained difference — **even when Nxentra reports healthy.**

Sign-off: operator ______ date ______

---

## L. Phase 9 — G1 human alert proof

Detection is proven (A5). G1 must prove **delivery to a human**. This
section provides evidence slots only — nothing here is claimed done.

- [ ] L1. The deployed monitor (external pinger) calls the approved
  endpoint(s) on schedule. Evidence: monitor configuration + probe log.
- [ ] L2. An injected condition produces a nonhealthy response
  (`/_health/alerts` → 503; `alert_check` exits nonzero).
- [ ] L3. A named human receives the alert. Evidence: the received
  notification with timestamp.
- [ ] L4. The alert is acknowledged; escalation timing recorded.
- [ ] L5. Resolution (through the real resolve surface) produces a new
  healthy signal.
- [ ] L6. No PII appears in the alert message or the aggregate endpoint
  body. Evidence: the captured payloads (`alerts/`).

Sign-off: operator ______ date ______

---

## M. Phase 10 — Backup capture for G2

At the conclusion of the successful G1 rehearsal:

- [ ] M1. **Freeze the final control pack** (§K). Take no new business
  writes between this snapshot and the backup — if any write occurs,
  repeat the control pack.
- [ ] M2. **Take the isolated-database backup** (the G2 input is the
  whole-database backup, not the in-app export):
  - Command / action: `scripts/backup-restore-drill.sh --backup-only`
    (pg_dump custom format of `DATABASE_URL`), and additionally the
    supported in-app export
    `python manage.py company_backup --company <PILOT_COMPANY_SLUG>` as
    secondary evidence.
  - Evidence to retain (`backup/`): timestamp, file size, SHA-256 hash of
    each artifact, application revision (§B), database/schema revision
    (latest applied migration per app).
- [ ] M3. Store the backup off-host in private encrypted storage. Never
  commit it to Git.

STOP if: the backup fails, or a write occurred after the control snapshot.

Sign-off: operator ______ date ______

---

## N. Phase 11 — G2 restore rehearsal (procedure only)

Documented here; **executed only as the separate G2 drill**. In-app restore
(`company_restore` / `POST /api/backups/restore/`) is blocked under the
active pilot by design and is **not** the G2 path; break-glass flags do not
bypass it. Because rebuild is unavailable as pilot recovery, the restore
result itself must reproduce the control pack.

- [ ] N1. Restore the **stored §M artifact itself** into a separate
  scratch database: verify the file's SHA-256 against the §M record, then
  `pg_restore` the custom-format dump into `<SCRATCH_DB_URL>` (schema
  reset then restore — the sequence `scripts/backup-restore-drill.sh`
  uses). Note the drill script's full-drill mode always takes a **new**
  dump at run time, so it cannot by itself restore the hash-recorded §M
  artifact — it is a rehearsal convenience, not the G2 restore step.
  After the restore, run
  `python manage.py check --deploy --fail-level WARNING` against the
  scratch database explicitly and require exit 0 (the drill script prints
  this check but does not fail on it).
- [ ] N2. Boot the application (same pinned revision) against the scratch
  database, isolated from production traffic and from Shopify webhooks.
- [ ] N3. Compare against the frozen control pack — all must match
  exactly: company/account configuration; BusinessEvent count and
  sequence; journal and line counts; total debits and credits;
  Shopify/provider/bank source totals; rejection/failure/quarantine
  counts; reconciliation totals; trial balance; traced-adjustment
  provenance; alert state; stored backup hash and restored revision.
- [ ] N4. Evidence to retain (`restore/`): the comparison table, restored
  revision, hashes.

STOP if: any control differs, or the restore requires manual database
repair of any kind.

Sign-off: operator ______ date ______

---

## O. Abort and rollback conditions (red box)

**Abort pilot activation or merchant-data intake immediately on any of:**

- wrong application revision on any service;
- nonfresh database when fresh mode was selected;
- multiple companies or active owners;
- unsafe environment/bypass flag present;
- non-EGP source admitted anywhere;
- any preflight violation;
- an unsupported capability reachable;
- worker/beat/monitor failure;
- unexplained source/control-total variance;
- silent missing financial effect;
- duplicate financial effect;
- exception or alert surface reporting false green;
- backup failure;
- restore mismatch;
- need for raw-SQL repair;
- any merchant reliance on unsupported reports.

The stop/go decision belongs to the **founder/operator**; every stop or go
is recorded with a dated sign-off in `signoff/`.

---

## P. Evidence directory / manifest

Keep evidence in a local/private manifest — **never** merchant data or
secrets in Git:

```
revision/           # §B pins
environment/        # §C, §E, §G reports (secret-free)
preflight/          # §F blockers, §H bootstrap, §I preflight outputs
source-controls/    # §K control pack + source-file hashes
failure-injection/  # §J scenario evidence
alerts/             # §L delivery proof
reconciliation/     # §J5, §K reconciliation evidence
backup/             # §M artifacts metadata (hashes, not the backup itself)
restore/            # §N comparison
signoff/            # dated stop/go decisions
```

Safe to attach to GitHub: revision pins, command outputs that are
secret-free and PII-free (preflight JSON, health JSON, count outputs).
Private encrypted storage only: backups, source CSVs, Shopify exports,
anything carrying merchant names/amounts/identifiers, monitor payloads
containing addresses or tokens.
