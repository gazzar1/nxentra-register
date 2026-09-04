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

This runbook operates **two distinct environments**:

1. **The G1/G2 rehearsal environment** — one isolated
   deployment/database; one Shopify **development/test** store; synthetic
   company identity; synthetic product catalog; synthetic orders/refunds;
   synthetic settlement and bank CSVs; **no real merchant identifiers,
   catalog, customers, orders, refunds, payouts, or financial records.**
2. **The real merchant environment** — created **only after G1 and G2
   close**; a **new empty isolated database**; the **exact G1/G2-tested
   revision and immutable deployment artifact**. A later application
   revision is eligible only after **both G1 and G2 are repeated and
   closed on that exact later revision** — green CI is necessary but does
   not transfer the earlier operational and restore proofs to changed
   application code or deployment artifacts. **No data, backup, or
   BusinessEvent history is copied from the rehearsal database.**

The G1/G2 rehearsal database is **never promoted** into the real merchant
accounting database. It contains synthetic financial history and must not
be converted by disconnecting the development store and connecting the real
store. The synthetic G1 backup is **G2 evidence only** and must not be
restored into the real merchant database. The rehearsal database may be
privately archived or destroyed after G2, subject to the retained-evidence
policy (§P).

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
    run (all seven jobs including Quality Gate). **Minimum
    application-code baseline:**
    `cd8bc9df12407cbcab473f9f6c2a1f2336967ae5`
    (code tree `c32efc3013de914e302dbc871e044a4601d63b15`; green main CI
    run 33684519108, seven jobs) — the merge of the PR #140
    cancelled-order refund-recovery correction, which contains the
    PR #139 refund-completeness correction (`3fc79de`). **No earlier
    commit lacking PR #140 is eligible** for G1/G2: the previously
    named `3fc79de` (PR #139 without PR #140) and `c4896ff3` (neither)
    revisions predate the cancelled-order correction and may no longer
    be used — no verdict transfers from them. Because this runbook
    document itself merges after that baseline, the exact revision
    selected at execution must be a `main` commit containing PR #139
    AND PR #140 AND this merged runbook, with its own green required
    CI; the operator records that exact selected SHA, and the runbook
    document revision is recorded separately from the deployed
    application revision. A later reviewed `main` revision with green
    required CI may be **selected here, before a new rehearsal begins**
    — that selected revision and its immutable artifact then become the
    subject of this runbook's G1 and G2, and the operator records the
    new exact SHA. This selection rule never transfers a PRIOR G1/G2
    verdict forward — green CI is a selection precondition, not an
    operational proof: the merchant cutover (§Q) must deploy the exact
    revision pack that passed the gates.
  - Evidence to retain (`revision/`): commit SHA, tree SHA, CI run id and
    conclusion, deployment/image identifier, the frontend build origin
    and bundle digest (`FRONTEND_BUILD_ORIGIN` /
    `FRONTEND_BUNDLE_DIGEST` — recorded when §G1c builds; the API
    origin is compiled into the frontend bundle, §E3), deployment
    timestamp, operator,
    database identifier (host/name only — no credentials), hosting region,
    and this runbook's revision (the SHA that the §I definition names
    `INTAKE_CONTRACT_VERSION` — the "version <n>" every intake
    authorization cites).
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
    time projections run — after the §G service startup, nonzero bookmarks
    alone do not imply merchant data; at THIS step, before any boot, the
    count must be 0.)
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
    `SHOPIFY_SOURCE_STALE_SECONDS` (28800); `LOG_LEVEL` absent or
    `INFO` (never stricter — the §I intake-contract capture reads the
    worker's INFO-level `[A52] _sync_orders start` line and the Celery
    task lines).
  - **Frontend BUILD-time variables** — these are compiled into the
    browser bundle by `npm run build`, not read at runtime, so they
    must be present and recorded BEFORE the §G1c build runs (names and
    non-secret values):
    - `NEXT_PUBLIC_API_URL` — REQUIRED: this deployment's production
      https API base URL INCLUDING the `/api` path (the form
      `https://<DEPLOYMENT_HOST>/api` — a bare origin without the
      path would pass a presence check yet route every API call to
      the wrong path). With it absent the build still SUCCEEDS and
      the bundle silently targets the compiled-in default
      `http://localhost:8000/api` (`frontend/lib/api-client.ts`;
      several modules bake the same fallback; the settings pages
      strip the `/api` suffix from this value to build media URLs,
      so the configured value must carry it). Record the exact value
      used — it is this deployment's `FRONTEND_BUILD_ORIGIN`, which
      §G1c verifies in the built artifact and §N5/§Q bind the
      frontend artifact rule to.
    - `NEXT_PUBLIC_ENABLE_EXCHANGED_TOKEN_FALLBACK` — record its
      presence/boolean posture (absent = disabled).
    - `NEXT_PUBLIC_SHOPIFY_API_KEY` — record which value the build
      used: with it absent, `frontend/pages/_document.tsx` falls back
      to the published app's hardcoded public client id (a public
      identifier, not a secret).
    - `NEXT_PUBLIC_SENTRY_DSN` — optional; enables the frontend
      Sentry build path (record presence only, never the value).
  - STOP if: a required value is missing; the process would start
    against the wrong database; or `NEXT_PUBLIC_API_URL` is absent,
    non-https, missing its `/api` path, or not this deployment's real
    API base URL at the moment the frontend build runs.

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

- [ ] **F1. ShopifyStore PENDING-sweep history protection — FIX ON
  RECORD.**
  - Required outcome: a store with canonical history cannot be deleted by
    an abandoned reconnect/PENDING cleanup.
  - Fix on record: **PR #141** (merge
    `ee003d57f3f304c16f4b917baeff3db407ee674c`). All three
    stale-PENDING deletion doors (the per-company install-URL sweep,
    the OAuth domain-taken branch, and the periodic cleanup task) share
    one disposition: a stale PENDING store WITH canonical history (any
    dependent order/payout/dispute/product/binding/rejected-evidence
    row, or a once-ACTIVE marker) is returned to DISCONNECTED with
    credentials cleared instead of being deleted; a store without
    history is deleted as before; each candidate is re-read and
    disposed under its own row lock. Regression tests:
    `backend/tests/test_g1_f1_store_sweep_history_guard.py` and the
    PostgreSQL two-connection proofs in
    `backend/tests/e2e/test_g1_f1_store_sweep_serialization.py`.
  - Evidence field (`preflight/`): verification that the executed §B
    revision contains PR #141 (`git merge-base --is-ancestor
    ee003d57f3f304c16f4b917baeff3db407ee674c <EXECUTED_SHA>` exits 0)
    ______.

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

**Evidence scope — per deployment, not per revision.** F1 is a code
property and travels with the revision: it is proven once by its fixing
PR and regression test, and every later environment only confirms the
executed revision contains that PR. **F2 and F3 do not transfer between
environments:** F2 lives in each deployment's proxy/firewall, and F3
depends on the specific merchant's expected webhook burst and retry
volume. Synthetic-rehearsal evidence for F2/F3 therefore proves nothing
about the real merchant deployment — §Q step 3 requires fresh F2/F3
evidence on the merchant host, for that merchant, before the merchant
company is activated.

STOP if: any of F1–F3 lacks its evidence at the moment activation (§I) is
attempted — in the rehearsal environment for G1, and again, freshly, in
the merchant environment for §Q.

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
- [ ] **G1c. Frontend build — with the recorded build origin, verified
  in the built artifact.**
  - Command / action: from `frontend/`: `npm ci` then, with the §E3
    frontend build-time variables in place (`NEXT_PUBLIC_API_URL` =
    this deployment's production https API base URL including the
    `/api` path), `npm run build`.
    Then verify the ARTIFACT, not the build process: the served client
    bundle (`.next/static/`) must contain the EXACT recorded
    `FRONTEND_BUILD_ORIGIN` value and
    must NOT contain `localhost:8000` (e.g.
    `grep -rl "<FRONTEND_BUILD_ORIGIN>" .next/static/` finds matches;
    `grep -rl "localhost:8000" .next/static/` finds none — a correct
    production build constant-folds the unset-variable fallback away,
    so any surviving `localhost:8000` means the origin was NOT baked
    correctly; never explain it away as dead code). Record the two
    grep results, `FRONTEND_BUILD_ORIGIN`, and a bundle digest
    (`FRONTEND_BUNDLE_DIGEST` — e.g. a SHA-256 over the `.next`
    client build output together with `.next/BUILD_ID`).
  - Expected result: build succeeds; `.next/BUILD_ID` exists; the
    production API base URL is baked into the client bundle; the
    localhost default is absent. A `GET /` returning 200 is NOT
    evidence of the bundle's API origin — the page serves regardless
    of which origin is compiled in.
  - STOP if: the built client bundle contains `localhost:8000`, or its
    compiled-in API base URL is anything other than the exact recorded
    `FRONTEND_BUILD_ORIGIN`. (This rule is about the API base URL
    only: third-party origins — the Sentry ingest host when
    `NEXT_PUBLIC_SENTRY_DSN` is set, CDN and font hosts — legitimately
    appear in the bundle and are not violations.)
- [ ] **G1d. Start services.** For every service record: expected process,
  expected version/SHA, startup command, health signal, log location,
  restart behavior.
  Beat's schedule lives in the database (`django_celery_beat`
  DatabaseScheduler) and the repository registers no periodic task in
  code — record every periodic task registered for this deployment
  (name, task, schedule; at minimum `shopify.sync_all_stores` and
  `shopify.cleanup_stale_installs` if used) in `environment/`, because
  a restored backup carries those rows (§N2).

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

## H. Phase 5 — Base company bootstrap (before activation)

Use the supported HTTP surfaces. Operator-only CLI actions are labelled.
Do not use Django admin or raw SQL for bootstrap.

**Order is load-bearing.** Shopify provisioning, store connect, and product
sync happen ONLY AFTER activation (§I): under profile NONE,
`complete_onboarding(business_type="shopify")` creates the
`shopify_connector` INVENTORY and COGS module mappings (inventory is a
supported capability on NONE), and a pre-activation product sync can create
INVENTORY items — activation preflight then deterministically refuses with
`module_inv_cogs_mapping` / `inventory_items`. Under the active pilot the
same provisioning withholds those mappings.

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
- [ ] **H2. Base onboarding pass — no Shopify provisioning.**
  - Command / action: authenticated `POST /api/onboarding/setup/` with an
    explicit payload containing: `company_name`; `company_name_ar` (if
    used); `fiscal_year_start_month = 1`; `thousand_separator`;
    `decimal_separator`; `decimal_places`; `date_format`;
    `enable_arabic_fields`; `fiscal_year`; `num_periods` = 12 or 13;
    `current_period`; `coa_template = <BASE_COA_TEMPLATE>` (`minimal` or
    `retail`); `import_mode = "skip"`. **Explicitly omit** `business_type`
    (or submit it as `""`), `modules`, and `import_from_date`. Do not
    connect a Shopify store, do not run product sync, do not import
    orders. **Record `<BASE_COA_TEMPLATE>` and the
    `enable_arabic_fields` boolean** — the pilot-aware pass 2 (I3) must
    repeat both exactly.
  - Expected result: fiscal structure exists (`num_periods` NORMAL
    periods plus one framework ADJUSTMENT period; preflight requires the
    January start and 12/13 periods); the chosen chart of accounts
    exists; `onboarding_completed` is true; **no** `ShopifyStore` exists;
    **no** `Item` with `item_type=INVENTORY` exists; **no**
    `shopify_connector` mapping with role INVENTORY or COGS exists; no
    unsupported optional module is enabled.
  - Evidence to retain (`preflight/`): the response plus the recorded
    template/Arabic choices.
  - STOP if: any expected-absent object exists, or fiscal configuration
    deviates from the January / 12-or-13-period requirement.

Evidence to retain (`preflight/`): screenshots or API responses for each
step, redacting any merchant PII.

Sign-off: operator ______ date ______

---

## I. Phase 6 — Activation, pilot-aware Shopify provisioning, go-live

One chronological sequence — do not jump backward into §H: activation-aware
validation-only check → activation → pilot-aware Shopify provisioning →
**controlled Shopify intake hold** → standalone OAuth store connection
(deliberately unbound; initial sync queued but unable to execute) → the
I6/J0 `not_bound` → link → authenticated ceremony (which CREATES the
canonical binding) → configuration and refusal proofs → the first go-live
preflight that can legitimately pass (read-only currency probe) →
pre-release no-ingestion proof → intake authorization → **staged release:
worker only (the initial sync owns the first product sync) → webhooks →
beat LAST** → drift-verification cadence. For every run record: exact command, company
identifier, phase, exit code, complete output, timestamp, operator
(`preflight/`).

**Never repair or suppress a violation inside preflight.** Correct the cause
through its owning configuration or process, then rerun the full preflight.

The standalone `pilot_preflight` command runs **without** activation
awareness, so before activation it always reports `profile_not_enabled` —
it is NOT usable as the pre-activation check and must not be worked around.
The activation command's validation-only mode is the sanctioned
pre-activation check:

- [ ] **I1. Activation-aware validation-only check — no persistent
  mutation.**
  - Command / action:
    `python manage.py activate_pilot_profile --company <PILOT_COMPANY_ID>`
    (`<PILOT_COMPANY_ID>` is the numeric Company id.) **Intentionally omit
    `--yes`.**
  - Expected result: exit code **1**, with output containing
    `Validation passed. Re-run with --yes to activate.`
    This nonzero exit is the command's deliberate confirmation barrier,
    not a failed validation. No pilot-profile change or
    `PilotProfileActivation` row is written.
  - Evidence to retain (`preflight/`): complete stdout/stderr, exit code,
    company identifier, timestamp, operator.
  - STOP if: the output says `Refusing to activate`; any violation is
    listed; the command exits 0 because the company is already active
    unexpectedly; the expected validation-passed message is absent; or any
    persistent state changes.
- [ ] **I2. Activate the profile.**
  - Command / action:
    `python manage.py activate_pilot_profile --company <PILOT_COMPANY_ID> --yes`
  - Expected result:
    `Activated ISOLATED_SHADOW_LEDGER_V1 on company <PILOT_COMPANY_ID>.`
    exit 0; a `PilotProfileActivation` audit row (source `cli`) exists.
  - **Warning:** activation at I2 means the constrained capability profile
    is active. It does NOT authorize merchant financial data. Go-live
    preflight, G1, and G2 remain incomplete.
  - STOP if: `Refusing to activate: …` — violations are listed; nothing
    was modified; correct and return to I1.
- [ ] **I3. Pilot-aware Shopify provisioning pass.**
  - Command / action: an authenticated **direct API request** to
    `POST /api/onboarding/setup/`. **Do not use the ordinary frontend
    Shopify wizard** for this pass — its submission sends
    `fiscal_year_start_month`, `fiscal_year`, the full module list with
    `purchases=true` and `inventory=true`, and `business_type=shopify`,
    which the active pilot refuses. Exact payload:

    ```json
    {
      "business_type": "shopify",
      "coa_template": "<BASE_COA_TEMPLATE>",
      "enable_arabic_fields": <SAME_BOOLEAN_AS_H2>,
      "modules": [
        {"key": "sales", "is_enabled": true},
        {"key": "shopify_connector", "is_enabled": true}
      ],
      "import_mode": "skip"
    }
    ```

    Do **not** send: `fiscal_year_start_month`, `fiscal_year`,
    `num_periods`, `current_period`, `import_from_date`, or any other
    module (`purchases`, `inventory`, `clinic`, `properties`,
    `stripe_connector`, `bank_connector`).
  - Serializer-default warning (why the payload is exactly this):
    `fiscal_year_start_month` and `fiscal_year` default to **0** when
    omitted, so this pass requests no fiscal mutation (the
    `CURRENCY_FISCAL_CHANGE` gate fires only on a start month in 1–12 or
    `fiscal_year > 0`); the module payload is validated by
    `require_module_enable_allowed` on the locked Company, and neither
    `sales` nor `shopify_connector` is module-enablement-blocked;
    `coa_template` defaults to `"minimal"` and `enable_arabic_fields`
    defaults to `false` **and is always persisted** — both MUST be
    repeated with the exact H2 values or the defaults silently overwrite
    them.
  - Expected result: `business_type` becomes `shopify`; `sales` and
    `shopify_connector` are enabled; the Shopify GL accounts are
    provisioned; the `SHOPIFY_CLEARING` (11500) and
    `EXPECTED_BANK_DEPOSIT` (11600) mappings exist (the EBD mapping is
    provisioned **only** by this onboarding path — the account-mapping
    PUT endpoint does not carry that role); `shopify_connector`
    INVENTORY and COGS mappings do **not** exist; no INVENTORY `Item`
    exists; no historical import is queued; fiscal configuration,
    `coa_template`, and `enable_arabic_fields` are byte-for-byte
    unchanged from H2. (The exact H2 → I1 → I2 → I3 sequence was executed
    successfully against a fresh throwaway database; the PII-free summary
    is recorded in the PR.)
  - STOP if: the request is refused with `CURRENCY_FISCAL_CHANGE`; any
    unsupported module is enabled; any INVENTORY/COGS module mapping
    exists; fiscal settings change; COA-template metadata changes;
    Arabic-field configuration changes; or an import task is queued.
- [ ] **I4. Establish the CONTROLLED SHOPIFY INTAKE HOLD.**
  - Why: `complete_oauth()` automatically schedules `initial_store_sync`
    the moment the store becomes ACTIVE, and as soon as a worker
    consumes it that task pulls the full initial-intake contract
    (defined before I13): orders selected by the execution-time
    seven-day `created_at` window (cancelled captured-money orders
    included; never-captured cancellations dispositioned with no
    financial effect), and refund candidates selected by the
    execution-time seven-day order-`updated_at` window — each refund
    candidate, cancelled or not, booked with its complete parent order
    and complete refund history regardless of age — plus products and
    payouts; declarative Shopify
    webhooks can also begin delivering immediately after connection.
    Store connection is therefore NOT inert — ingestion must be held
    until deliberately released. (`import_mode="skip"` suppresses only
    the onboarding historical-import request; it does NOT suppress the
    OAuth-triggered initial sync or its refund catch-up leg.)
  - Command / action, in order: (1) keep the web process available for
    exactly: Shopify OAuth install/callback, embedded session login,
    linking-nonce creation/redemption, authenticated configuration
    reads/writes, and preflight; (2) block the Shopify
    financial/source-ingestion routes at the reverse proxy — at minimum
    `/api/shopify/webhooks` and `/api/shopify/webhooks/` — with a
    retryable non-success response (e.g. 503; never a discarding 200),
    and prevent accidental calls to interactive sync/resync endpoints
    during the hold; (3) stop Celery beat and prove it stopped;
    (4) inspect active/reserved/scheduled worker tasks, drain to empty
    and accounted-for, then stop the worker; (5) keep Redis/the broker
    AVAILABLE so the OAuth-triggered initial sync can be queued;
    (6) record an empty (or fully accounted-for) task-queue baseline.
  - Evidence to retain (`preflight/`): proxy rule proof, beat/worker
    stop proofs, queue baseline, timestamps, operator.
  - STOP if: a worker can consume tasks; beat can enqueue the periodic
    catch-up; a Shopify webhook can reach Django; an interactive sync
    endpoint remains usable; or the pre-OAuth task queue contains
    unexplained work.
- [ ] **I5. Connect exactly one SYNTHETIC development store —
  deliberately UNBOUND.**
  - Command / action: initiate the connection through the **top-level
    standalone Nxentra OAuth path**: `/shopify/settings` →
    `POST /api/shopify/install/` → complete OAuth for
    `<SYNTHETIC_DEV_SHOP_DOMAIN>`. Do **not** use the embedded
    token-exchange installation path for this step — it automatically
    creates a `ShopifyUserBinding` when it holds a Shopify `sub`, which
    would make the required unbound-state proof impossible. The store
    MUST be a non-production Shopify development/test store containing
    only synthetic catalog and transaction data — no copied real
    customer/order/refund data — while still exercising real OAuth,
    App Bridge/session-token behavior, signed webhook delivery, and API
    synchronization.
  - Expected result: exactly one ACTIVE synthetic Shopify development
    store exists. **No active `ShopifyUserBinding` exists yet** for the
    synthetic Shopify user and store — this is deliberate: the
    immediately following I6/J0 ceremony must first prove the
    fail-closed `not_bound` state and then create the canonical binding.
    (The standalone OAuth path creates/activates the store but never a
    binding; only the embedded token-exchange path and the linking-nonce
    redemption create bindings.) The OAuth-triggered
    `initial_store_sync` enqueue is recorded in the private application
    log ("Queued initial Shopify sync for <shop>"); the worker and beat
    remain stopped and webhook ingress remains blocked, so **the task is
    queued but cannot execute** and no automatic sync has run.
    **Enqueue-failure disposition:** OAuth success is NOT proof the task
    was queued — the helper swallows broker failures. If the log shows
    "Could not queue initial Shopify sync" or the enqueue result is
    uncertain: STOP before any release, keep worker/beat/webhooks
    blocked, repair the broker condition; do not rely silently on the
    periodic catch-up and do not blindly enqueue a second task while the
    first task's existence is uncertain. Any manual enqueue recovery
    command must first be verified against live code, must return a
    recorded task id, and must be proven on the synthetic rehearsal
    before it may appear in this runbook.
  - STOP if: any active `ShopifyUserBinding` already exists before J0;
    the connect path automatically authenticates the embedded user; the
    store was connected through a path that bypasses the intended
    unbound state; more than one ACTIVE store exists; the domain is the
    first merchant's live store; the store contains real merchant
    catalog, customers, orders, or financial history; or data was copied
    from the merchant merely to make the test realistic.
- [ ] **I6 / J0. A1 live Shopify embedded-authentication proof —
  independently signed; executed HERE, before product sync and before
  go-live preflight.** This is the named J0 criterion of the G1 matrix
  (§J), placed at its only executable point in the chronology: the store
  is ACTIVE and no binding exists yet, so the fail-closed `not_bound`
  state is genuinely observable, and this ceremony is what CREATES the
  canonical binding the go-live preflight requires. Uses only the
  synthetic development store, the synthetic founder/operator identity,
  and the rehearsal environment. The endpoints below are the live code's
  canonical A1 surfaces: `POST /api/auth/shopify-session-login/`
  (embedded session login; 403 `not_bound` for an unbound Shopify user),
  `POST /api/shopify/linking-nonce/` (authenticated standalone
  OWNER/ADMIN with `settings.edit` mints a single-use nonce,
  `expires_in_seconds: 600`), and
  `POST /api/shopify/redeem-linking-nonce/` (public by design — the
  nonce plus the signed App Bridge session token ARE the credentials;
  success `{"status": "linked"}`). Prove, in order:
  1. **Browser posture** — clean browser profile; third-party cookies
     disabled/blocked; record browser name, exact version, OS, and the
     cookie setting; remove any pre-existing standalone Nxentra
     authentication state from the embedded context. The G1 sign-off
     applies only to the recorded posture; repeat for any additional
     browser the first pilot will support.
  2. **Unbound embedded launch** — launch Nxentra from the synthetic
     store's Shopify Admin app surface inside the embedded iframe;
     obtain and use an App Bridge session token; with no active binding
     for that exact Shopify user/store pair, reach the explicit 403
     `not_bound` state; prove the system does not silently select the
     first OWNER/ADMIN and does not authenticate via a third-party
     Nxentra cookie.
  3. **Standalone owner link initiation** — in a top-level Nxentra
     context, authenticate the exact founder OWNER and create the
     single-use linking nonce through the canonical path. Do not record
     or attach the nonce value itself; retain only the redacted status,
     timestamp, company/store identity proof, and operator identity.
  4. **Embedded redemption** — back in the iframe, redeem the nonce with
     the valid session token; the token's Shopify shop must match the
     synthetic store; verify the resulting active `ShopifyUserBinding`
     points to the exact synthetic store, the exact Shopify `sub`, the
     exact active OWNER membership, and the same company — no
     first-owner or cross-company fallback.
  5. **Single-use proof** — attempt to redeem the same nonce again;
     require the loud "nonce already used" refusal; no second binding or
     mutation may be created.
  6. **Bound session login** — reload/relaunch from Shopify Admin;
     obtain a fresh session token; the session-login path now resolves
     the exact active binding and reaches the intended Nxentra company
     without third-party cookies or a standalone-cookie fallback; the
     displayed actor/company identity matches the bound membership.
  7. **STOP if:** the initial embedded launch skips `not_bound`; an
     OWNER is selected merely because one exists; third-party cookies
     are required; no App Bridge session token is used; the nonce can
     bind a different company, store, user, or membership; nonce replay
     succeeds; post-binding session login fails; the embedded actor
     resolves to the wrong company; or any real merchant store or
     identity is used.

  **J0 evidence** (all raw authentication evidence PRIVATE): retain
  privately the browser/version/OS + cookie posture; timestamped
  screenshots of the initial embedded launch, `not_bound`, successful
  link, and successful post-link embedded login; a redacted network
  sequence (session-token request present; not-bound result; nonce
  creation status; nonce redemption status; successful session login);
  proof of the binding's same-company/store/membership relationship; the
  nonce-replay refusal; operator sign-off. **Never retain or upload
  raw:** App Bridge session tokens, linking nonce values, cookies,
  Authorization headers, Shopify access tokens, raw HAR files, customer
  or merchant data. Any GitHub evidence is a manually sanitized summary
  containing only: browser/version, third-party-cookie posture, the
  `not_bound → linked → authenticated` state transition, endpoint/result
  categories, final PASS/FAIL, timestamps.

  Sign-off (I6/J0 alone): operator ______ date ______
- [ ] **I7. Settlement providers and posting profiles.**
  - Command / action: the Shopify setup bootstraps the provider rows and
    `PG-*` posting profiles; review at `/shopify/settings`
    (`PATCH /api/accounting/settlement-providers/<pk>/`) so that at least
    one of **paymob** / **bosta** is ACTIVE — and note the preflight
    checks **every** ACTIVE supported provider, so each provider left
    ACTIVE must route to an ACTIVE posting profile with a postable
    control account (preflight codes `provider_missing`,
    `provider_posting_profile`).
- [ ] **I8. Cash/Bank account.**
  - Command / action: confirm an ACTIVE, non-header LIQUIDITY account
    exists (template account `11000 Cash and Bank`, or create one at
    `/accounting/chart-of-accounts/new`). Preflight code:
    `bank_account_missing`.
- [ ] **I9. Single-company / single-owner proof.**
  - Command / action: re-run the §C2 count for `Company` (expect exactly
    1) and confirm in the UI there is exactly one active OWNER membership
    and no other members. (After activation, `deployment_has_pilot()`
    blocks all further signup/company creation deployment-wide.)
  - STOP if: more than one company or active membership exists.
- [ ] **I10. Excluded-capability refusal checks.**
  - Command / action: confirm purchases/clinic/properties are not enabled
    and their enable doors refuse under the active pilot (spot-check one
    refusal; the rehearsal in §J exercises more).
- [ ] **I11. First binding-dependent go-live preflight.**
  - Command / action:
    `python manage.py pilot_preflight --company <PILOT_COMPANY_ID> --phase go-live --json`
    Because no product sync has run yet under the intake hold, the
    store-currency check legitimately uses the preflight's read-only
    live probe (no durable `shop_currency` snapshot exists until the
    initial sync's product leg runs at I14).
  - Expected result: `ok: true`, exit 0 — this is the full agreed-workflow
    proof (EGP store, OWNER↔store binding, postable clearing/EBD mappings,
    active provider + posting profile, canonical bank account).
  - STOP if: any violation.
- [ ] **I12. Pre-release no-ingestion proof.** Read-only proof that no
  automatic sync executed during the hold: no `ShopifyOrder` /
  `ShopifyRefund` rows; no synchronized Shopify product `Item`; no
  `ShopifyPayout` / provider-payout financial state; no sync-created
  `ProviderRawObject`; no order/refund/payout financial `BusinessEvent`;
  no Shopify-ingestion `JournalEntry`; no sync-caused `last_sync_at`
  update. Name the EXPECTED bootstrap state explicitly (do not assert a
  blanket zero-event claim): the `ShopifyStore` row, the post-J0
  `ShopifyUserBinding`, the Shopify warehouse/Customer/PostingProfile
  setup records, the module-account mappings, the non-financial
  `SHOPIFY_STORE_CONNECTED` event, and account/provider configuration.

**Initial-intake contract — reusable definition** (used by I13, I14, the
§K controls, and §Q; every window is computed at EXECUTION time, when
the worker starts the task — never at OAuth time):

```
INITIAL_SYNC_STARTED_AT = the execution-time `now` the initial_store_sync
                          task computes when its store sync begins
                          (`_sync_store`: `now = tz.now()`) — the value
                          the code uses as BOTH window ends. It is
                          observable ONLY as the `created_at_max` value
                          of the worker's private INFO log line
                          `[A52] _sync_orders start … created_at_min=…
                          created_at_max=…`; it is NOT in the task
                          result, and it is NOT the Celery task
                          received/started timestamp, which precedes it
                          by the task's store lookup, tenant-context
                          entry and store refetch (seconds).

TASK_RECEIVED_AT        = the task's start timestamp from its durable
                          `django_celery_results` TaskResult row, keyed
                          by task id (the result backend is django-db
                          and STARTED tracking is on, so the row's
                          date_created is the STARTED time and
                          date_done the finish), corroborated by the
                          worker's received/started log lines where
                          present (recorded separately; used only for
                          ordering)

ORDER_CREATED_WINDOW =
    [INITIAL_SYNC_STARTED_AT − 7 days, INITIAL_SYNC_STARTED_AT]
    = the [created_at_min, created_at_max] pair of that log line

REFUND_CANDIDATE_UPDATED_WINDOW =
    [INITIAL_SYNC_STARTED_AT − 7 days, INITIAL_SYNC_STARTED_AT]
    = the SAME pair by construction (`_sync_store` passes one
      min_date/max_date to both legs; the refund leg logs no window
      of its own)

A = eligible Shopify orders whose created_at is in ORDER_CREATED_WINDOW

B = orders currently refunded or partially_refunded whose Shopify
    order.updated_at is in REFUND_CANDIDATE_UPDATED_WINDOW

AUTHORIZED_PARENT_ORDER_SET = A union B, deduplicated by Shopify order id

INTAKE_CONTRACT_VERSION = the runbook document revision SHA recorded in
                          §B (`revision/`, "this runbook's revision";
                          repeated for the merchant database by §Q
                          step 3). The definition text at that revision
                          IS the intake contract; the GO record's
                          "version <n>" and the step-12 reconciliation
                          cite this SHA. The executed application
                          revision is recorded separately (the §B
                          selected SHA; for §Q the §N5
                          GATE_TESTED_COMMIT_SHA), which §B requires to
                          contain PR #139 and PR #140.
```

When transcribing the `[A52] _sync_orders start` line into the record,
copy ONLY the two timestamps — the line also carries the shop domain
and store id, which must not enter any evidence that may be attached.
The line is emitted at INFO on the application logger, so the worker's
effective log level must be INFO (the `-l INFO` startup command in §G
and a `LOG_LEVEL` environment value no stricter than INFO, recorded in
the §E report). The reconciliation that these fields must satisfy is
ONE rule, stated identically wherever it is applied:
`created_at_max − created_at_min` = exactly 7 days
(`INITIAL_LOOKBACK_DAYS`); authorization sign-off timestamp
(`GO_TIMESTAMP` in §Q, `I13_SIGNOFF_TIMESTAMP` in the rehearsal)
`< TASK_RECEIVED_AT ≤ INITIAL_SYNC_STARTED_AT` — the inequality failing
is a STOP; the last two are normally seconds apart, and a larger gap is
not a STOP by itself but must be explained in the record; an absent
A52 start line is itself a STOP.

If the runbook revision recorded for the merchant run (§Q step 3)
differs from the revision rehearsed at G1, the §I definition block at
the two revisions must be textually identical — record both SHAs and
the diff result; a changed definition is a new contract that has not
been rehearsed: STOP and re-run I13/I14 under it before any merchant
GO.

(The live B selection is the refund catch-up's search — the
`updated_at` window AND `financial_status:refunded OR
financial_status:partially_refunded`. A populated `cancelled_at` is
NOT a selection or exclusion criterion and is NOT a no-financial-effect
predicate: every B candidate is captured money by selection, so a
**cancelled refunded candidate — of any age, inside or outside A — is
recovered exactly like an open one**, through the canonical writers
only: the parent via the idempotent `process_order_paid`; cancellation
provenance via the canonical `process_order_cancelled` posted-order
branch, which stamps `cancelled_at` on the stored raw payload; its
complete refund history via the PR #139 pagination and the idempotent
`process_refund`. Since PR #140 (`cd8bc9d`) the refund leg skips NO
cancelled candidate; the earlier blanket skip — which dropped both the
parent and its refunds while the leg still reported `ok` — does not
exist in any eligible revision. In the A leg, a first-seen cancelled
order whose money was captured (financial status `paid`,
`partially_paid`, `refunded`, or `partially_refunded`) likewise books
through the paid writer and receives the provenance stamp — webhook
parity: captured revenue stays booked until a refund reverses it —
while a cancelled order whose money was never captured (`authorized`,
`voided`, `pending`, `expired`, unknown) routes to the cancellation
writer as an explicit, counted no-financial-effect disposition.)

For every B candidate, the catch-up may: book the full parent order if
it is absent locally, **even when that order was created months or
years before the seven-day window**; fetch and process the complete
refund history returned by Shopify for that order; and process refunds
whose individual `created_at` values fall outside the seven-day window.
The seven-day period is a candidate-SELECTION window — it is NOT a
guarantee that every imported order or refund source timestamp is at
most seven days old.

A and B may overlap. A parent order appearing in both is counted and
posted only once. Existing idempotency (`process_order_paid` /
`process_refund`) remains load-bearing.

**Per-leg result fields required for reconciliation** (PR #140,
`cd8bc9d`; PII-free counters read from the retained task result):

- Refund catch-up leg (`refunds`) — on its `ok` shape and on its
  complete-history-failure shape: `status`, `scanned`,
  `refunds_created`, `errors`, `fetch_failures`, `pilot_scope_skipped`,
  `cancelled_financial_candidates`, `cancelled_financial_processed`,
  `cancelled_processing_errors` (the failure shape adds `error`). Any
  other shape — token missing/revoked, or an exception caught by the
  task wrapper — carries only `status`/`error` and NO counters, and is
  equally a STOP.
- Order-created leg (`orders`) — on its `ok` shape: `status`,
  `fetched`, `created`, `skipped`, `errors`, `cogs_fulfillments`,
  `refunds_backfilled`, `pilot_scope_skipped`,
  `cancelled_financial_candidates`, `cancelled_financial_processed`,
  `cancelled_no_effect_skipped`, `cancelled_processing_errors`. Its
  mid-fetch `partial`/`error` shape carries `fetched`/`created`/
  `skipped`/`errors`/`error` plus the five pilot/cancelled counters;
  its `unavailable` shape (read scope denied before any order was
  fetched) carries only zeroed `fetched`/`created`/`skipped`/`errors`
  and a message — no pilot/cancelled counters. Like the refund leg, it
  also has bare shapes with NO counters — token missing/revoked before
  any fetch, or an exception caught by the task wrapper — carrying only
  `status`/`error`. An orders-leg `status = "error"` is the
  counter-bearing mid-fetch shape only when the five pilot/cancelled
  counters are present; with or without them it is equally a STOP.

Counter semantics (both legs unless stated): `cancelled_financial_candidates`
counts every candidate with a populated `cancelled_at` that is routed
to the paid writer (in the B leg that is every cancelled candidate —
all are captured money by selection); `cancelled_financial_processed`
counts those dispositioned end-to-end without error (parent booked or
already present, provenance stamped, complete refund history
processed); `cancelled_processing_errors` is PER ERROR ENTRY, not per
candidate — a parent-booking failure, a provenance-stamp failure, a
refund-history fetch failure, each individual refund processing
failure, an id-less payload carrying `cancelled_at` (either leg; in
the A leg that entry has no matching `cancelled_financial_candidates`
increment), and — A leg only — a never-captured cancellation whose
cancellation writer failed or raised (likewise not a candidate) add
one each, so a single candidate can contribute several and the A-leg
counter can exceed its candidates; `cancelled_no_effect_skipped` (A
leg only) counts never-captured cancellations that the cancellation
writer dispositioned SUCCESSFULLY — a never-captured cancellation whose
writer failed lands in `errors`/`cancelled_processing_errors` instead
and must be dispositioned there, never silently; `pilot_scope_skipped`
counts every order — cancelled or not, from any handler — that a
canonical writer dispositioned OUT of the pilot's scope with the
structured `SKIPPED_PILOT_SCOPE` answer (the A4 EGP-only admission: no
row, no event, no journal, no retry); such an order leaves its leg
BEFORE the provenance stamp and before any backfill (both the
fulfillment and refund backfills in the A leg; the single refund
backfill in the B leg) and is counted in none of `created`, `skipped`,
or `errors`.

**Checkable inequality, per leg:**
`cancelled_financial_candidates − cancelled_financial_processed ≤
cancelled_processing_errors + pilot_scope_skipped`
(the pilot bucket also counts open candidates, the error counter is
per entry, and in the A leg it also counts id-less and never-captured
cancellation errors that are not candidates — which is why this is an
inequality). On a correctly scoped EGP store `pilot_scope_skipped`
MUST be 0 — the synthetic rehearsal store and the real merchant store
are both EGP by precondition, so a nonzero bucket is a scope finding to
STOP on, not a tolerance.

**The ONLY legitimate outcomes in which a B candidate yields no local
parent order or no refund evidence** — every other gap is unexplained
and a STOP:

1. **Pilot-dispositioned** — the paid writer answered
   `SKIPPED_PILOT_SCOPE` (a non-EGP order under the active profile):
   no row, no event, no journal, counted once in `pilot_scope_skipped`
   — explicit and counted, never silent. Expected count on an EGP
   store: 0.
2. **Counted loud failure** — the parent could not be booked
   (`errors` +1, plus `cancelled_processing_errors` +1 when cancelled;
   the leg moves to the next candidate without stamping or backfilling
   that one), or the complete refund history could not be fetched
   (`fetch_failures` +1, `errors` +1; the leg's `status` becomes
   `"error"` and `error` names the number of affected candidates; the
   parent STAYS COMMITTED and books NO REFUNDS), or an individual
   refund failed to process (`errors` +1 per refund). Each is visible
   in the counters and in the private worker log, must be
   dispositioned, and — this is the load-bearing part — **must be
   CLOSED before any merchant-facing checkpoint**: the parent, its
   provenance stamp where `cancelled_at` is populated, and its complete
   refund history must be present in the database. A counted loud
   error is a legitimate INTERMEDIATE state, never a terminal one. A
   TRANSIENT failure (database, network, lock, a refund that raced its
   parent) is closed by a recorded, explained re-execution that
   RE-SELECTS the candidate; a successful re-execution is idempotent
   and duplicates no `ShopifyOrder`, `ShopifyRefund`, `BusinessEvent`,
   or `JournalEntry`. There is NO explicit-window refund-leg re-run in
   the code: `initial_store_sync` never auto-retries and every
   re-execution of it, and every periodic `sync_shopify_all` pass
   (48-hour lookback, only once beat runs), recomputes both windows
   from its OWN start time, so a B candidate is re-selected only while
   its `updated_at` still falls inside that later window — and this
   runbook names no enqueue door for a second `initial_store_sync`
   (the I5 enqueue-recovery rule applies: any such command must be
   verified against live code, return a recorded task id, and be
   proven on the synthetic rehearsal before it may appear here). The
   A-leg re-execution that DOES take an explicit `created_at` window
   is `python manage.py resync_shopify_orders --company <slug>
   --from <ISO> --to <ISO>` (or the worker task
   `shopify.sync_store_orders` with explicit `created_at_min` /
   `created_at_max`); with any window the order leg routes a cancelled
   or refunded order through the paid writer, the stamp and the refund
   backfill, so it can close a candidate whose creation date the
   operator names. It books EVERY order created in the named window,
   so the window MUST be the narrowest that re-selects the candidate,
   the re-execution is recorded as an explained second execution (its
   own task id or CLI invocation, its own `[A52] _sync_orders start`
   line, counters and inequality, in the §K pack), and any order it
   books outside AUTHORIZED_PARENT_ORDER_SET is an intake-contract
   variance and a STOP. The interactive resync endpoint is NOT a
   closure path: it accepts only a `days` lookback ending at its own
   request time, runs in-request as the merchant session, and stays
   blocked under the intake hold. A candidate that no re-execution can
   re-select is a STOP, never a signed disposition. A never-captured
   cancellation error (A leg) is CLOSED when a recorded re-execution
   re-selects the order and the cancellation writer disposes it
   successfully (it then appears in that run's
   `cancelled_no_effect_skipped`) with no financial row, event, or
   journal for the order — that is the no-financial-effect closure. A
   PERMANENT structural rejection of the parent-order or refund payload
   is different in kind: it is counted in the same `errors` (and
   `cancelled_processing_errors`) bucket but is durable
   `ShopifyRejectedEvidence` on the exceptions queue (no row, no event,
   no journal); a re-execution re-sights the same evidence row when the
   payload is identical (occurrence count bumps, the queue item
   reopens) or opens a further row when the still-malformed payload
   changed — either way it closes nothing; only a corrected payload
   (supersession) does, and until then it is a STOP.

A cancelled B candidate is NOT a third category: it is processed
(`cancelled_financial_processed`), pilot-dispositioned
(`pilot_scope_skipped`), or errored (`cancelled_processing_errors`),
and the inequality above binds those three outcomes together.

**Stamp verification is a result/log check, not a row check.** The
paid writer stores the WHOLE poller payload as the order's raw payload,
and the poller payload already carries `cancelled_at`, so a
poller-booked cancelled parent shows `cancelled_at` on its stored raw
payload even if the canonical `process_order_cancelled` stamp then
FAILED (the stamp rewrites the same key with the same value). The
stamp is therefore verified from the task result and the private
worker log — no stamp-failure entry in `cancelled_processing_errors`
and no "Cancellation provenance stamp failed" warning for that order —
with the raw-payload field as corroboration only; for a parent that
already existed locally (booked earlier by a webhook) the stamp is what
adds the value.

**Leg-status truth — a worker-log/result check, not a claim of
safety:** among the refund leg's counter-bearing shapes, `status`
flips to `"error"` ONLY when `fetch_failures > 0` (its bare
token-missing / caught-exception shapes are separate `"error"` shapes
with no counters). A parent-booking failure, a provenance-stamp
failure, or a refund processing failure leaves the leg at
`status = "ok"` while incrementing `errors` (and
`cancelled_processing_errors` for a cancelled candidate) — so
`status = "ok"` is NOT evidence of zero errors; `errors` and
`cancelled_processing_errors` must be read directly. When the legs
run, the top-level `initial_store_sync` result's `status` is set to
`"ok"` after all five legs regardless of any leg's outcome (a leg that
raises is recorded as that leg's own `{"status": "error", ...}`), so it
can mask any leg error and proves nothing — evaluate every leg's own
result individually: its `status` where present (the `deferred_cogs`
success shape is `{"booked": n}` with no `status` key; the `payouts`
and `products` success shapes are the commands' own data) and its
counters. The task can also return BEFORE any leg runs, with NO leg
keys at all: `status = "skipped"` with reason "Store not active" or
"tenant not writable (migrating / read-only / suspended)", or
`status = "error"` with "Store not found". Each of those returns
without raising, so the queued task is CONSUMED without executing the
authorized intake; the task can also RAISE before any leg (the tenant
lookup or the tenant-context store refetch failing) and, having no
auto-retry, is consumed as a Celery FAILURE with a traceback and no
result dict at all. Either is the same STOP: do not re-enqueue until
the cause is explained and the pre-release no-ingestion proof is
re-established.

**Refund-completeness fix on record (PR #139):** merge
`3fc79de4a4371ea3e45ff09eded369a44aa6c747`; reviewed head
`a3f1f44bcaffec78760982d5afadf0a99a5d4e10`; main CI 33571139879 —
success. `Order.refunds` is queried directly as its exact uncapped
2026-04 list shape; `Refund.transactions` and `Refund.refundLineItems`
are cursor-paginated to exhaustion; incomplete pages fail loudly
through `ShopifyGraphQLIncomplete`; the refund leg reports structured
non-success (`status`, `fetch_failures`) and a fetch failure books NO
REFUNDS for that order — a parent order booked earlier in the same
pass stays committed (a newly booked parent with zero refunds after a
fetch failure is an EXPECTED intermediate state, not an unexplained
variance), and the idempotent retry completes the refund history with
no duplicate financial effect. Refund evidence is complete or the
refund leg fails loudly — no partial refund page may masquerade as
complete.

**Cancelled-order refund-recovery fix on record (PR #140):** merge
`cd8bc9df12407cbcab473f9f6c2a1f2336967ae5`; reviewed head
`a963556d4b6bceeed796811ab0d10742566ed34b`; main CI 33684519108 —
success. Production change confined to
`backend/shopify_connector/tasks.py` (tests:
`backend/tests/test_a5_cancelled_refund_recovery.py`). `cancelled_at`
is no longer a no-financial-effect predicate in either sync leg; the
refund leg's blanket cancelled skip is removed; the per-leg
pilot/cancelled counters defined above are added to both legs'
results; a pilot-dispositioned order leaves its leg before the
provenance stamp and before any backfill (both A-leg backfills; the B
leg's single refund backfill — the previous code mis-counted the A-leg
pilot skip as `created`). A re-execution that re-selects a candidate
is idempotent — no duplicate `ShopifyOrder`, `ShopifyRefund`,
`BusinessEvent`, or `JournalEntry` (see the closure rule above for
what a re-execution can and cannot re-select). No migration, model,
event-schema, A3, or A4 change.

**Mechanical side effects of the authorized intake** (expected and
named here so they are never read as "unexplained" rows): for every
booked paid order the initial task also fetches the order's
fulfillments and records `ShopifyFulfillment` source rows — under the
pilot's NON_STOCK-only catalog these carry no COGS lines and may
legitimately carry status ERROR "No SKUs matched inventory items",
an expected and explained non-financial state; the task's
deferred-COGS sweep leg also runs and must report 0 booked under the
profile. The task result's top-level `status` field is unconditionally
`"ok"` when the legs run (a no-leg-keys `skipped`/`error` return, or a
raised Celery FAILURE, is the consumed-without-executing STOP) and
proves nothing — evaluate each leg's own result (`orders`,
`payouts`, `products`, `refunds`, `deferred_cogs`) individually, per
the leg-status truth above (status where the shape carries one, and
counters).

**Nested-collection caps — CURRENT-CODE limitation and the
merchant-shape precondition it forces (removal condition: a merged,
reviewed pagination fix named in §B).** The GraphQL order queries both
sync legs use (`iter_orders` for set A, `iter_refunded_orders` for
set B) select each order's own line items as `lineItems(first: 50)`
(`LINE_ITEMS_PER_ORDER`) with NO pagination and NO `pageInfo` — an
order with more than 50 line items yields exactly the first 50 with no
warning, no counter, and no error anywhere. The product sync
(`iter_product_pages`) selects `variants(first: 60)`
(`VARIANTS_PER_PRODUCT`) with no pagination; it does detect overflow
and logs the private worker WARNING `has more than 60 variants — extra
variants not synced`, then continues — a logged truncation, never a
failure or counter. Money-truth: neither cap changes any invoice or
journal amount — the Shopify invoice is built from the order-level
totals (`subtotal` / `total_price` / `total_tax` / shipping) that the
order queries fetch separately from the line items, never by summing
line items, and dimension tagging reads only the FIRST line item. What
truncates is EVIDENCE and CATALOG state: the stored order payload's
`line_items` list (the order event's evidence — including a B
candidate's booked parent) is capped at 50 on every GraphQL sync path
(webhook payloads carry the complete list — the cap is poller-path
only); NON_STOCK item auto-provisioning sees only the first 50 lines'
SKUs; the product mirror and NON_STOCK catalog carry at most 60
variants per product. Refund completeness is NOT affected — the
PR #139 refund reads paginate to exhaustion, and this cap never
touches them. The same class exists in the per-order fulfillment
query (`FULFILLMENTS_PER_ORDER = 10`, `FULFILLMENT_LINE_ITEMS = 50`),
whose output is non-financial under the pilot (the mechanical side
effects above). Until a reviewed pagination fix — draining `lineItems`
and `variants` to exhaustion with the PR #139 fail-loud pattern — is
merged and contained in the executed §B revision, the pilot carries a
**MERCHANT-SHAPE PRECONDITION**: no order in the intake may have more
than 50 line items and no product more than 60 variants, proven from
the Shopify export (per-order line-item counts, per-product variant
counts — the line-item cap emits NO signal of its own, so the export
comparison is the only line-item control; the variant cap's worker
warning is corroboration only), verified by the I14 / §K completeness
controls and recorded in the I13 and §Q authorizations. The
precondition is ONGOING, not intake-only: the caps live in every
GraphQL sync path for as long as the executed revision runs —
including the periodic catch-up and product sync once beat restarts
(I16) — so a post-GO order booked by the poller (e.g. a missed
webhook) or a post-GO product change syncs under the same caps.
While they are in force: the §Q GO record (and the I13 twin) carries
the shape constraint as a STANDING commitment for the pilot's
operating period, acknowledged by the signer; every later
control-pack build and phase sign-off repeats the §K export
comparison over the period since the last check; the worker log is
monitored for the variant-truncation warning; and a breach detected
at any point is a STOP at that point. Once the
fixing PR is merged and the executed revision contains it, this
precondition and its controls become obsolete and must be removed
from this runbook by a follow-up correction naming that PR.

- [ ] **I13. Synthetic-rehearsal intake authorization.** A dated
  sign-off authorizing release of the queued initial sync for the
  SYNTHETIC store (the rehearsal twin of the §Q GO decision). The
  synthetic sign-off explicitly authorizes, per the intake-contract
  definition above:
  1. set A — synthetic orders created during the seven-day
     `ORDER_CREATED_WINDOW`;
  2. set B — synthetic refunded/partially-refunded orders whose
     order.`updated_at` falls in the seven-day
     `REFUND_CANDIDATE_UPDATED_WINDOW`, regardless of parent-order age;
  3. for every B candidate — cancelled or not — the complete parent
     order, the cancellation provenance stamp where `cancelled_at` is
     populated, and the complete refund history as returned by
     Shopify, regardless of individual refund dates;
  4. the complete synthetic product catalog;
  5. execution of the payout sync leg, with payout accounting expected
     to remain blocked/skipped under `ISOLATED_SHADOW_LEDGER_V1`.
  The sign-off must acknowledge: an imported parent order or refund may
  have a Shopify source date older than seven days even though the
  change that selected the parent order occurred inside the
  refund-candidate `updated_at` window; and a cancelled refunded
  candidate is recovered and booked (parent, provenance stamp, complete
  refunds), never skipped. While the §I nested-collection caps are in
  force, the sign-off must also record the merchant-shape precondition
  for the SYNTHETIC store: no order in its catalog/history has more
  than 50 line items and no product has more than 60 variants, proven
  from the store's Shopify export (per-order line-item counts,
  per-product variant counts) and dated — the rehearsal twin of the §Q
  step-11 `MERCHANT_SHAPE_PRECONDITION_VERIFIED` record; without that
  record this sign-off may not be given. This sign-off authorizes the contract at
  `INTAKE_CONTRACT_VERSION` = <the §B runbook revision SHA> (the
  rehearsal twin of the GO record's "version <n>"). Like the §Q GO
  record, it authorizes the FUTURE execution-relative contract: the
  worker is stopped at signing, so `INITIAL_SYNC_STARTED_AT` and the
  effective window boundaries do not exist yet and no VALUE for them
  may be recorded here — they are captured at I14 and reconciled to
  this authorization there.
  Sign-off: operator ______ `I13_SIGNOFF_TIMESTAMP` (UTC, to the
  second — the rehearsal twin of `GO_TIMESTAMP`, ordered against
  `TASK_RECEIVED_AT` at I14) ______
- [ ] **I14. Controlled initial-sync release — worker only. Verify the
  authorized intake CONTRACT, not a simple window.** Start the Celery
  worker ONLY; keep beat stopped and webhooks blocked. Observe the
  queued `shopify.initial_store_sync` task: record its task id (from
  the worker receive/start log and its durable `django_celery_results`
  TaskResult row), its start/finish timestamps, and its complete result
  (privately); require exactly one initial task consumed on release —
  any later re-execution used for §I closure is a separately recorded,
  explained execution with its own task id or CLI invocation, A52 line
  and counters, and more than one UNEXPLAINED initial task is a STOP.
  Record, from the worker's private `[A52] _sync_orders start …
  created_at_min=… created_at_max=…` INFO line (transcribe ONLY the two
  timestamps — the line also carries the shop domain and store id; the
  windows are NOT in the task result):
  `INITIAL_SYNC_STARTED_AT` = `created_at_max`,
  `ORDER_CREATED_WINDOW_START` = `created_at_min`,
  `ORDER_CREATED_WINDOW_END` = `created_at_max`,
  `REFUND_CANDIDATE_UPDATED_WINDOW_START` / `_END` = the same pair
  (the refund leg is passed the identical values and logs no window of
  its own); and, separately, `TASK_RECEIVED_AT` from the TaskResult
  row's start time, corroborated by the Celery received/started log
  line. This is the FIRST point at which these values exist (they never
  appear in the I13 sign-off). Reconcile them to the I13 authorization
  — `INTAKE_CONTRACT_VERSION` and `INITIAL_LOOKBACK_DAYS = 7` — by the
  §I rule: `created_at_max − created_at_min` = exactly 7 days;
  `I13_SIGNOFF_TIMESTAMP < TASK_RECEIVED_AT ≤ INITIAL_SYNC_STARTED_AT`
  (the inequality failing is a STOP; the last two are normally seconds
  apart — a larger gap is not a STOP by itself but must be explained in
  the record); an absent A52 start line is a STOP. A task result with
  NO leg keys (`skipped` "Store not active" / "tenant not writable", or
  `error` "Store not found"), or a raised Celery FAILURE with no result
  dict, means the task was consumed without executing the intake — STOP
  (§I leg-status truth). Require the task result to show, PER LEG (the
  top-level `status` is unconditionally `"ok"` when the legs run and
  proves nothing — §I definition):
  - the order-created leg completed on its `ok` shape with its full
    counter set recorded (`fetched`, `created`, `skipped`, `errors`,
    `cogs_fulfillments`, `refunds_backfilled`, `pilot_scope_skipped`,
    `cancelled_financial_candidates`, `cancelled_financial_processed`,
    `cancelled_no_effect_skipped`, `cancelled_processing_errors`), OR
    failed loudly — on its mid-fetch `partial`/`error` shape (which
    carries only `fetched`/`created`/`skipped`/`errors`/`error` plus the
    five pilot/cancelled counters, never `cogs_fulfillments` or
    `refunds_backfilled`), on its `unavailable` shape (zeroed
    `fetched`/`created`/`skipped`/`errors` and a message, no
    pilot/cancelled counters), or on a bare `status`/`error` shape —
    every failure shape is a STOP;
  - the refund catch-up leg completed with `status = "ok"` and
    `fetch_failures = 0`, with its full counter set recorded
    (`scanned`, `refunds_created`, `errors`, `fetch_failures`,
    `pilot_scope_skipped`, `cancelled_financial_candidates`,
    `cancelled_financial_processed`, `cancelled_processing_errors`) —
    those fields are guaranteed only on the `ok` and
    complete-history-failure shapes; any other error shape carries
    only `status`/`error` and is equally a STOP. A fetch failure on the
    release execution (`status = "error"`, `fetch_failures > 0`) is a
    STOP at that moment; it is CLOSED only per the §I closure rule — a
    recorded re-execution whose refund leg is `ok` with
    `fetch_failures = 0` and the affected candidate's complete refund
    history present — with BOTH results retained in the §K pack, and
    the latest accounted execution's refund leg must be `ok` with
    `fetch_failures = 0`;
  - `status = "ok"` on the refund leg is NOT read as zero errors (it
    flips only on fetch failures — §I leg-status truth): `errors` and
    `cancelled_processing_errors` are read directly and every entry is
    zero or individually explained, corrected, and CLOSED per the §I
    closure rule (a re-selecting re-execution that leaves the gap open
    closes nothing);
  - `pilot_scope_skipped = 0` in BOTH legs (the synthetic store is EGP
    by precondition — a nonzero bucket is a scope finding);
  - the per-leg inequality holds in BOTH legs:
    `cancelled_financial_candidates − cancelled_financial_processed ≤
    cancelled_processing_errors + pilot_scope_skipped`.
  Then verify against the database and the §K control pack:
  - every B candidate — cancelled or not — has a local parent order
    and complete refund evidence (the task result cannot show these),
    and — where Shopify shows `cancelled_at` — the cancellation
    provenance stamp, verified per the §I definition from the task
    result and worker log (no stamp-failure entry in
    `cancelled_processing_errors`, no "Cancellation provenance stamp
    failed" warning for that order; the raw-payload `cancelled_at`
    field is corroboration only, because the poller-booked parent
    already carries it); no partial refund list or refund-connection
    page was accepted — proven by the refund leg's `status = "ok"` with
    `fetch_failures = 0` (order-page partial responses are separately
    visible only as the private worker-log warning "Shopify GraphQL
    partial response" and must be inspected if present). The ONLY B
    candidates permitted to lack a parent or evidence at this
    checkpoint are those accounted for by the §I definition's two
    legitimate outcomes: `pilot_scope_skipped` (expected 0 here), or a
    counted loud error (`cancelled_processing_errors` /
    `fetch_failures` / `errors`) that has been dispositioned AND whose
    recorded, explained re-execution has CLOSED the gap — parent,
    stamp, and complete refunds now present; a gap that is only
    counted, dispositioned, or retried without closure, a candidate no
    re-execution can re-select, or a permanent malformed-payload
    rejection is NOT accounted for and remains a STOP;
  - every first-seen cancelled captured-money order in A is booked
    and stamped (`cancelled_financial_processed`), and every
    never-captured cancellation in A is counted in
    `cancelled_no_effect_skipped` with no financial effect — or, if its
    cancellation writer failed, appears in
    `errors`/`cancelled_processing_errors` and is dispositioned and
    closed there (§I no-financial-effect closure), never silently;
  - the A union B parent-order set is deduplicated by Shopify order
    id — reconcile the union from order ids in the §K control pack,
    NEVER by summing leg counters: an overlapping A/B order
    legitimately appears in both legs' counters (`fetched` and
    `scanned`, and — when cancelled — in both legs'
    `cancelled_financial_candidates`);
  - the §I nested-collection completeness controls hold: for every
    intake order, the per-order line-item count from the Shopify
    export is at most 50 AND equals the stored order evidence's
    `line_items` count (the stored list is unfiltered, so equality is
    exact below the cap; the cap emits no signal of its own — this
    export comparison is the only line-item control); for every
    product in the export, the per-product variant count from the
    export is at most 60; and the worker log carries no
    `more than 60 variants` truncation warning (corroboration). The
    variant control is deliberately EXPORT-side only: the sync
    legitimately skips SKU-less variants (no mirror row, no Item) and
    the NON_STOCK catalog collapses shared SKUs, so a mirror or
    catalog count below the export count is NOT truncation evidence
    and no equality is demanded of it;
  - all source and financial effects reconcile to the authorized
    intake contract — not merely to source timestamps within seven
    days.
  Record: `MAX_ORDER_LINE_ITEM_COUNT` and `MAX_PRODUCT_VARIANT_COUNT`
  (from the Shopify export, with the export capture date — both must
  respect the §I caps while they are in force),
  `OLDEST_IMPORTED_PARENT_ORDER_CREATED_AT`,
  `OLDEST_IMPORTED_REFUND_CREATED_AT`,
  `NEWEST_IMPORTED_REFUND_CREATED_AT`, `CANDIDATE_ORDER_COUNT_A`,
  `CANDIDATE_ORDER_COUNT_B`, `CANDIDATE_ORDER_UNION_COUNT`,
  `COMPLETE_REFUND_COUNT`, `REFUND_FETCH_FAILURES`, and the per-leg
  counters `PILOT_SCOPE_SKIPPED_A` / `_B` (must be 0),
  `CANCELLED_FINANCIAL_CANDIDATES_A` / `_B`,
  `CANCELLED_FINANCIAL_PROCESSED_A` / `_B`,
  `CANCELLED_NO_EFFECT_SKIPPED_A`, `CANCELLED_PROCESSING_ERRORS_A` /
  `_B`. An older parent-order date or refund date is NOT a violation
  when the order was legitimately selected through B; a cancelled
  refunded candidate's booked parent and refunds are NOT a violation —
  they are the required outcome. Then verify: synthetic products
  synchronized and every product NON_STOCK with zero inventory/COGS
  account links and zero inventory ledger/FIFO residue; the durable
  `shop_currency` snapshot now exists and is EGP; synthetic
  orders/refunds carry truthful outcomes with no duplicate journal;
  the payout leg's result is recorded and payout ACCOUNTING remains
  blocked/skipped under the constrained profile; the fulfillment
  backfill's `ShopifyFulfillment` rows appear only in their expected
  non-financial state and the `deferred_cogs` leg reports 0 booked
  (the §I definition's mechanical side effects); no unexplained source
  or financial row appears. Rerun the go-live preflight and
  `/_health/alerts`.
  STOP if: `fetch_failures` is nonzero on the latest accounted
  execution, or on any earlier execution whose affected candidate's
  complete refund history is still not present; the refund leg reports
  error or incomplete evidence on that same basis; a candidate order's
  full refund history cannot be assembled; a B candidate lacks a local
  parent, its provenance stamp, or complete refund evidence and is not
  accounted for by `pilot_scope_skipped` or a counted loud error that
  has been CLOSED (a retry that leaves the gap open, a candidate no
  re-execution can re-select, or a permanent malformed-payload
  rejection is not accounted for); `pilot_scope_skipped` is nonzero in
  either leg; `cancelled_processing_errors` or `errors` is nonzero and
  not individually dispositioned and closed; the per-leg inequality
  fails; a cancelled B candidate is classified as harmless, skipped,
  or outside the required parent/evidence set; the refund leg's
  `status = "ok"` is read as proof of zero errors; the task result has
  no leg keys or the task raised before any leg; the A52 start line is
  absent, or the recorded windows do not match its
  `created_at_min`/`created_at_max`, or
  `created_at_max − created_at_min ≠ 7 days`, or
  `I13_SIGNOFF_TIMESTAMP < TASK_RECEIVED_AT ≤ INITIAL_SYNC_STARTED_AT`
  fails; a closure re-execution booked an order outside
  AUTHORIZED_PARENT_ORDER_SET; counts across A, B, and their union
  cannot be reconciled; an older imported parent/refund is omitted
  merely to preserve a seven-day timestamp narrative; or — while the
  §I nested-collection caps are in force — the export shows any order
  with more than 50 line items or any product with more than 60
  variants, a per-order line-item count differs between the export
  and the stored order evidence, or the
  worker log carries the variant-truncation warning.
- [ ] **I15. Webhook release and retry reconciliation.** Unblock the
  Shopify webhook route while beat remains stopped. Allow queued
  Shopify retries to arrive; verify idempotency — duplicate deliveries
  create no duplicate financial effects; account for the webhook
  backlog.
- [ ] **I16. Beat restart LAST + drift cadence.** Once the initial task
  and webhook retries are reconciled and health is stable, start Celery
  beat. Rerun the go-live preflight and `/_health/alerts`. From here on,
  rerun the go-live preflight after every subsequent sync/import or
  supported configuration action and before every later phase sign-off.
  If any post-J0 operation causes `binding_missing` or another
  violation: STOP, investigate the state change — do not recreate or
  bypass the binding casually.

Sign-off: operator ______ date ______

---

## J. Phase 7 — G1 rehearsal matrix

One controlled, current-head rehearsal using **synthetic data generated
solely for the rehearsal environment** — merchant approval does not
convert real merchant data into non-merchant data. The Shopify test cases
must run through the real deployed Shopify integration using the synthetic
development store, not merely direct fake command calls; settlement and
bank files must also be synthetic and contain no real merchant identifiers
or amounts. Where the supported workflow is user-facing,
run it through the real deployed application surfaces (frontend pages and
HTTP APIs), not test harnesses. Existing test fixtures and documented APIs
are references only. Record every scenario's evidence in
`failure-injection/` and `reconciliation/`.

**J0 was executed and independently signed at I6, before go-live
preflight.** It is a G1 prerequisite but is not repeated here, because
repeating its initial unbound state would require destroying or revoking
the binding that I11 correctly requires. Do not remove the binding to
replay J0. A second-browser J0 proof, if required for the pilot's
supported browser posture, must use a controlled fresh Shopify
user/binding setup or another fresh rehearsal environment — it must not
destructively alter the already signed primary proof without restarting
the affected proof sequence.

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

**G1 cannot close unless I6/J0 and J1–J7 all pass.** The financial J1–J7
matrix alone is insufficient — the tracker's A1 row remains operationally
open until J0 evidence exists. This documentation PR marks neither A1 nor
G1 complete.

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
| Initial-intake set A: order count, ids, totals (`created_at` window) | Shopify admin/exports |
| Initial-intake set B: refund-candidate order count and ids (order-`updated_at` window) | Shopify admin/exports |
| A union B order count after deduplication + A/B overlap count | Shopify admin/exports vs system |
| Complete refund count and totals per B candidate | Shopify admin/exports vs system |
| Per-order line-item count for every intake order (≤ 50 while the §I nested-collection caps are in force, and equal to the stored order evidence's `line_items` count — the cap emits no signal of its own, so this export comparison is the only line-item control) | Shopify admin/exports vs stored order evidence (read-only) |
| Per-product variant count for every product in the export (≤ 60 while the §I caps are in force — an EXPORT-side control; the private worker `more than 60 variants` warning must be absent, as corroboration. Deliberately NOT an equality against the product mirror / NON_STOCK catalog: the sync legitimately skips SKU-less variants and collapses shared SKUs, so a lower system count is not truncation evidence) | Shopify admin/exports + worker log (private) |
| Oldest and newest imported parent-order dates | system (read-only) vs Shopify |
| Oldest and newest imported refund dates | system (read-only) vs Shopify |
| Refund fetch-failure count (must be 0 on the latest accounted execution; any earlier execution's fetch failure recorded with its §I closure) | `initial_store_sync` task result(s) — the release execution AND every recorded closure re-execution |
| Cancelled B candidates: count and ids; for each — local parent present, complete refund count/totals, and the cancellation provenance stamp verified per §I (no stamp-failure entry in `cancelled_processing_errors` and no "Cancellation provenance stamp failed" warning for that order; raw-payload `cancelled_at` corroboration only) | Shopify admin/exports vs system (read-only) + task result / worker log (private) |
| Cancelled A orders: captured-money (booked + stamped) vs never-captured (no financial effect) split | Shopify admin/exports vs `cancelled_financial_processed` (booked + stamped) / `cancelled_no_effect_skipped` (never captured, writer succeeded); any `cancelled_financial_candidates` − `cancelled_financial_processed` gap must be accounted for by `cancelled_processing_errors` + `pilot_scope_skipped` per the §I inequality (row below) |
| Per-leg pilot/cancelled counters (`pilot_scope_skipped`, `cancelled_financial_candidates`, `cancelled_financial_processed`, `cancelled_no_effect_skipped` (orders leg only), `cancelled_processing_errors`) and the inequality check per leg | `initial_store_sync` task result |
| `pilot_scope_skipped` per leg (must be 0 on the EGP store) | `initial_store_sync` task result |
| `INITIAL_SYNC_STARTED_AT`, effective window boundaries (timestamps only), `TASK_RECEIVED_AT`, `INTAKE_CONTRACT_VERSION` | worker log `[A52] _sync_orders start` line + Celery task log (private) vs the §B runbook revision |
| The complete `initial_store_sync` task result, plus the complete result of every recorded closure re-execution (each with its own task id or CLI invocation, A52 line, counters, and inequality) | worker log / task result / TaskResult rows (private) |
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
| Per-source-row financial-effect classification and linked JE/value | source files vs system |
| Per-source-row durable review/reconciliation state(s) | source files vs system |
| Shopify embedded-authentication configuration | binding state (private) |
| Trial-balance control total | trial balance report |
| Every unexplained variance | — must be zero — |

Every source row and amount must record **both** of two orthogonal
dimensions — they are **not mutually exclusive**:

1. **Financial effect:** `POSTED_EXACTLY_ONCE` (with the journal
   identifier and amount) or `NO_FINANCIAL_EFFECT`.
2. **Durable review / reconciliation evidence** — record every applicable
   state: `CLEAR / NONE`, `MATCHED`, `REJECTED`, `FAILED`, `QUARANTINED`,
   `INTENTIONALLY_EXCLUDED` under the documented nuisance-row mapping (the
   A5 REJECTED-class outcome in the
   [closure artifact](../audits/2026-08-30-a5-final-closure-review.md)),
   or `HEALED / SUPERSEDED` where historical failure evidence remains
   relevant.

For the **Shopify embedded-authentication configuration** control, record
privately: active `ShopifyUserBinding` count; store identifier; company
identifier; membership identifier; active/revoked status; a one-way hash
of the Shopify `sub` (for comparison); linking proof timestamp; browser
posture tested. Never put a raw Shopify `sub`, nonce, token, email, or
store domain into public evidence.

The required `ORPHAN_ORDER_ID` settlement representation is
**`POSTED_EXACTLY_ONCE` + `QUARANTINED`**: the committed journal is the
financial effect; the quarantine flag is the operator-review state.
Financial totals count the amount once, from the financial-effect
dimension; exception/review totals independently count the quarantine
evidence. Never double-count a value merely because it carries two
truthful classifications.

The initial-intake controls follow the intake-contract definition (§I,
before I13): **financial totals must count an overlapping A/B parent
order exactly once**; review/evidence totals may independently count
rejection, failure, or quarantine records without duplicating the
financial amount. An old parent-order or refund date visible in these
controls is not a variance when the order was legitimately selected
through set B. A cancelled refunded B candidate is recorded exactly
like an open one: its parent carries `POSTED_EXACTLY_ONCE` (with the
journal identifier), each of its refunds carries its own financial
effect, and the cancellation provenance stamp is review/evidence
metadata — never a reason to omit the row, and never a financial effect
of its own. A never-captured cancelled A order is `NO_FINANCIAL_EFFECT`
and must reconcile to `cancelled_no_effect_skipped` — or, where its
cancellation writer failed, to an `errors`/`cancelled_processing_errors`
entry that is dispositioned and closed (§I no-financial-effect
closure). Every B candidate lacking a parent or
refund evidence must reconcile to exactly one of the §I definition's
two legitimate outcomes (`pilot_scope_skipped` — expected 0 — or a
counted loud error that has been CLOSED before the checkpoint); any
other absence is a variance. While the §I nested-collection caps are
in force, the per-order line-item and per-product variant rows above
are repeated at every later control-pack build and phase sign-off,
over the period since the last check (§I ongoing rule).

For every **system** control in this table, record alongside its
value the exact read-model function or query and parameters that
reproduce it from the operator shell under `rls_bypass` (the §M4
second capture) — the G2 restore comparison (§N3) recomputes every
control that way, because the restore environment runs no HTTP
surface.

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

## M. Phase 10 — Enforced quiescence and backup capture for G2

At the conclusion of the successful G1 rehearsal, the control pack and the
backup must share **one enforced application control point** — "taking no
new writes" is enforced and proven, never promised. Required order:

- [ ] **M1. Block external write ingress.**
  - Command / action: put the deployment into recorded maintenance mode
    at the reverse proxy: reject Shopify webhook requests and every
    public mutation route with a **retryable non-success** response
    (e.g. 503) — never return 200 while discarding a webhook. Permit
    only the explicitly needed authenticated read-only reporting paths
    until the control pack is finalized.
  - Evidence to retain (`backup/`): proxy rule/config, external HTTP
    proof, activation timestamp, operator.
  - STOP if: any mutation or webhook can still reach Django.
- [ ] **M2. Stop scheduled enqueueing.**
  - Command / action: stop Celery beat using the deployment-specific
    supervisor command recorded in §G; prove beat is stopped; record the
    last scheduled-task timestamp.
  - STOP if: beat can enqueue another task.
- [ ] **M3. Drain and stop workers.**
  - Command / action: inspect active, reserved, and scheduled Celery
    tasks; wait until all three are empty. Do not revoke a financial
    task merely to make the queue appear empty — investigate any stuck
    task. Stop the worker gracefully only after the drain is proven.
  - Evidence: active/reserved/scheduled inspection, worker shutdown
    output, queue state.
  - STOP if: any task is active, reserved, scheduled, or unaccounted
    for.
- [ ] **M4. Finalize the control pack.**
  - Command / action: with mutation ingress blocked and beat/worker
    stopped, generate the final §K source and system controls through
    only the approved read-only surfaces — AND capture every system
    control a second time from the operator shell under `rls_bypass`,
    using a named read-model function or query with recorded
    parameters (for example `AccountBalanceProjection().get_trial_balance(company)`
    for the trial balance, `build_account_drilldown(...)` for the GL
    drilldown balances, the reconciliation and exceptions-queue count
    queries, `python manage.py alert_check` for the alert state), and
    require the two captures of each control to agree. Record, per
    control, the exact function/query, parameters, and serialization
    used, so that §N3 can recompute it identically from the shell
    (the restore environment runs no HTTP surface). Record every
    source-file hash; serialize the final control manifest; compute
    and record its SHA-256; record the timestamp. After this point no
    source or system control may be edited, and no shell computation
    is ever adjusted post hoc to reach agreement.
- [ ] **M5. Stop the web process.**
  - Command / action: stop gunicorn after the final read-only controls
    are captured; prove no public application process can reach the
    database. Do not leave web running merely because the operator
    promises not to write.
- [ ] **M6. Prove database quiescence.**
  - Command / action: using an operator-only database session, retain a
    private result showing: no application-role client backend remains
    connected (except the explicitly named operator/backup session where
    applicable); no active or idle-in-transaction application
    transaction exists; no application writer process remains. Capture a
    BusinessEvent watermark for the pilot company — `event_count`,
    `max_event_id`, `max_company_sequence` — using `rls_bypass` or the
    proven operator context so RLS cannot make the watermark vacuously
    empty. Also record the final values/hash of the §K durable-control
    manifest. The BusinessEvent watermark is a **backstop, not the sole
    quiescence proof** — some source/write-model changes do not create a
    BusinessEvent.
- [ ] **M7. Verify the control point immediately before pg_dump.**
  - Command / action: re-run the application-session proof, the
    BusinessEvent watermark, and the durable-control manifest/hash
    comparison; require **exact equality** with M6.
  - STOP if anything changed: discard the frozen control pack, do not
    take the backup, identify the writer, and repeat from M1.
- [ ] **M8. Take the backup.**
  - Command / action: `scripts/backup-restore-drill.sh --backup-only`
    (pg_dump custom format of `DATABASE_URL`) against the quiesced
    database; additionally the supported in-app export
    `python manage.py company_backup --company <PILOT_COMPANY_SLUG>` as
    secondary evidence. Immediately after completion: re-read the
    BusinessEvent watermark and require equality with M6/M7; calculate
    the backup SHA-256.
  - Evidence to retain (`backup/`): backup size, timestamps, database
    identifier, application SHA (§B), schema/migration revision,
    control-manifest hash, event watermark.
  - STOP if: a watermark changed; an application database session
    appeared; the dump failed; the backup hash cannot be recorded; or
    any service restarted before the control point was sealed.
- [ ] **M9. Restart policy.** Do not restart web/worker/beat until the
  backup hash and control-point evidence are captured AND the operator
  signs the backup control point. A restart afterward creates later
  state but does not alter the frozen §M backup — record the restart
  time separately.
- [ ] **M10. Store the backup off-host** in private encrypted storage.
  Never commit it to Git.

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
- [ ] N2. Bring up ONLY the operator read path against the scratch
  database — **no application process at all**: no web process, no
  frontend, no beat, no worker, no shared broker. Every control N3
  compares is computed in the **operator shell under `rls_bypass`**
  (the §C2 / §M6 form) using the same named read-model functions and
  queries §M4 recorded for each control (§K); the alert comparison
  uses `python manage.py alert_check`, which runs the identical
  `compute_alert_state()` behind `/_health/alerts`, so the endpoint is
  not needed. This is deliberately the only posture: every HTTP
  surface of this application that authenticates is a writer, and a
  restore environment that mutated itself proves nothing.
  - **Why no web process:** `POST /api/auth/login/` (both its password
    step and its pending-token second step) and
    `POST /api/auth/shopify-session-login/` save `user.last_login`,
    emit a `user.logged_in` BusinessEvent for the restored company
    (moving `event_count` / `max_event_id` / `max_company_sequence` off
    the §M record), schedule projection processing (which, if the
    broker call fails, synchronously creates bookmark / applied-event
    rows), and write token rows; `POST /api/auth/refresh/` and
    `POST /api/auth/switch-company/` write token rows (rotation +
    blacklist); Django admin login writes a session row and
    `last_login` — and the interactive resync runs the order sync
    synchronously inside the web process with no worker at all. With
    no web process listening none of these doors exists. If a future
    drill ever needs an HTTP read, that method must be designed, proven
    write-free on a rehearsal (watermark equality PLUS unchanged token,
    session and `last_login` table state — the watermark cannot see
    non-event writes), and added to this runbook first; until then it
    is inadmissible.
  - **Why no beat or worker:** beat writes to the database on start
    regardless of content (schedule bookkeeping and its default
    entries), and the restored database carries whatever periodic-task
    rows the live deployment registered (§G1d records them) plus the
    ACTIVE store row, so beat + worker would enqueue and execute the
    scheduled Shopify catch-up against whatever store the restored row
    names (the synthetic store in the G2 drill; the real merchant store
    in any later merchant-environment drill) and mutate `last_sync_at`,
    source rows, events, journals, and bookmarks before the comparison.
    The STOP is on starting beat or a worker at all.
  - **Broker:** none is started for the restore environment; the
    scratch settings must name a `REDIS_URL` distinct from the live
    deployment's (never shared, never reachable from it), recorded by
    name, so that even an accidental enqueue can never land on a live
    queue.
  - **Required outbound control:** the restore host has no outbound
    network path to Shopify (proxy/firewall rule, proven and recorded).
    Optional additional control: a `FIELD_ENCRYPTION_KEY` distinct from
    the live deployment's. Understand its consequence before choosing
    it: the shell boots, but the field converter decrypts every
    selected encrypted column — a model-instance load OR a bare
    `.values()` / `.values_list()` of `ShopifyStore`,
    `PendingShopifyInstall` or `StripeAccount` (`access_token`,
    `refresh_token`, `webhook_secret`, `credential_ref`) raises a
    decrypt error (fail-loud — that is the control working). Under it,
    every store or binding field N3 needs (status, `needs_reauth`,
    `created_at`, `last_sync_at`, store/company/membership
    relationships) must be read with `.values(<named non-encrypted
    fields>)` / `.values_list(<named fields>)` / `.only()` / counts
    that exclude the encrypted columns; `alert_check` is count-only
    and unaffected. Restoring the live key to make a read work is a
    STOP. Record which controls are in force.
  - **Quiescence proof** (the §M6 form): capture #1 from the shell
    before any comparison — the BusinessEvent watermark
    (`event_count`, `max_event_id`, `max_company_sequence`) under
    `rls_bypass`, and proof that no application writer process and no
    application database session exists other than the explicitly
    named operator shell session taking the capture (record its
    backend identifier); run N3; capture #2 identically. Require both
    captures to equal the §M record exactly. A shell computation is
    never adjusted post hoc to reach equality — a difference is a
    difference.
  - STOP if: any application process (web, frontend, beat, worker) was
    started before N3 was recorded; any HTTP login, session-login,
    token-refresh, switch-company or Django-admin login was performed
    against the restored database; the broker is shared with, or
    reachable from, the live deployment; any outbound Shopify call is
    observed; any evidence row was resolved or any other non-event
    mutation made; a watermark capture differs from the §M record; or
    any restored source row, event, journal, bookmark, or
    `last_sync_at` changed between restore and comparison. A restore
    environment that mutated itself proves nothing — discard it and
    repeat N1.
- [ ] N3. Compare durable controls and evaluate derived alert conditions.
  The restored database must reproduce the **§M backup control point**:
  the §M control-manifest SHA-256 and every underlying durable control;
  the M6 BusinessEvent watermark — `event_count`, `max_event_id`,
  `max_company_sequence`. **Exact equality is required** for all
  persisted and financially material controls: company/account
  configuration; BusinessEvent count and sequence; journal and line
  counts; total debits and credits; Shopify/provider/bank source totals;
  durable rejected, failed, and quarantined evidence rows;
  reconciliation state and totals; trial balance; traced-adjustment
  provenance; persisted alert inputs (store status, `needs_reauth`,
  `created_at`, `last_sync_at`, bookmark state, paused/error state,
  unresolved evidence counts); the durable Shopify binding state —
  `ShopifyUserBinding` row count, store/company/membership relationships,
  active/revoked state, and the privately computed hash of the Shopify
  `sub`; stored backup hash, restored application revision, and
  schema/migration revision. A live Shopify iframe launch is NOT required
  inside the isolated G2 restore environment unless it is deliberately
  configured to receive Shopify traffic — the restore proof verifies the
  durable binding state; J0 carries the live browser/session-token proof.

  The pre-backup `/_health/alerts` JSON is **not** required to match
  byte-for-byte where fields are derived from the current clock. Record:
  control-pack timestamp; backup timestamp; restore evaluation timestamp;
  effective `SHOPIFY_SOURCE_STALE_SECONDS`; effective
  `ALERT_PROJECTION_STALENESS_SECONDS`. Recompute the expected
  restore-time values of `shopify_stale_sources`, `stale_consumers`, and
  any other clock-derived alert condition from the restored durable
  inputs and the restore-time clock; require the restore-time alert
  state — computed by `python manage.py alert_check` in the restore
  environment (the same `compute_alert_state()` behind
  `/_health/alerts`; no web process runs there, §N2) — to match that
  **age-aware expectation**. A difference caused solely by elapsed
  time is acceptable only when all underlying persisted inputs match
  exactly AND the recorded threshold calculation fully explains the
  difference. Do not run a live Shopify sync, rewrite
  `last_sync_at`/`created_at`, resolve evidence, or otherwise mutate
  restored state merely to recreate the pre-backup alert response.
- [ ] N4. Evidence to retain (`restore/`): the comparison table, restored
  revision, hashes; the N2 isolation record (confirmation that no
  application process was started, the operator shell session's
  backend identifier, the distinct broker name, the outbound Shopify
  controls in force and the store-field capture method used under them,
  the per-control shell function/query used, and both pre- and post-N3
  watermark captures).
- [ ] N5. **Issue the gate-tested revision pack.** On successful G1 + G2
  closure, record one immutable revision pack in `signoff/`:

  ```
  GATE_TESTED_COMMIT_SHA
  GATE_TESTED_GIT_TREE_SHA
  GATE_TESTED_IMAGE_OR_ARTIFACT_DIGEST
  GATE_TESTED_MIGRATION_MANIFEST
  GATE_TESTED_FRONTEND_BUNDLE_DIGEST
  FRONTEND_BUILD_ORIGIN
  G1_EVIDENCE_MANIFEST_HASH
  G2_EVIDENCE_MANIFEST_HASH
  G2_BACKUP_HASH
  ```

  This pack is the ONLY revision authority for the merchant cutover (§Q).

  **The artifact rule is SPLIT between the two artifacts.** The backend
  image/artifact digest is portable: the merchant deployment must run
  exactly `GATE_TESTED_IMAGE_OR_ARTIFACT_DIGEST`. The frontend bundle
  is NOT portable: its API origin is compiled in at build time
  (§E3/§G1c), so `GATE_TESTED_FRONTEND_BUNDLE_DIGEST` +
  `FRONTEND_BUILD_ORIGIN` prove what the REHEARSAL environment built
  and served — a deployment with a different origin must REBUILD the
  frontend from exactly `GATE_TESTED_COMMIT_SHA` with its own recorded
  origin and verify it per §G1c (§Q step 1). A frontend bundle built
  with another environment's origin must never serve.

STOP if: any control differs, or the restore requires manual database
repair of any kind.

Sign-off: operator ______ date ______

---

## O. Abort and rollback conditions (red box)

**Abort pilot activation or merchant-data intake immediately on any of:**

- wrong application revision on any service;
- a frontend bundle whose compiled-in API origin is not the serving
  deployment's recorded `FRONTEND_BUILD_ORIGIN` — including the
  `localhost:8000` build default, or a bundle built for another
  environment (e.g. the rehearsal-built bundle carried into the
  merchant deployment);
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
- any application process (web, frontend, beat, worker) started in the
  restore environment before N3 is recorded, any HTTP or Django-admin
  login / session-login / token refresh performed against the restored
  database, outbound Shopify synchronization possible from it, or a
  broker shared with the live deployment;
- merchant-environment activation without fresh F2/F3 evidence on the
  merchant host for that merchant (rehearsal evidence transferred);
- need for raw-SQL repair;
- any merchant reliance on unsupported reports;
- real merchant store or data used in the rehearsal database;
- rehearsal database proposed for promotion to the merchant database;
- write ingress not blocked before backup;
- beat still running at the backup control point;
- active/reserved/scheduled Celery work at the control point;
- web still running during `pg_dump`;
- application DB sessions remaining at the control point;
- BusinessEvent watermark or control-manifest hash changing before or
  during the backup;
- synthetic history appearing in the real merchant database.
- worker or beat active before the merchant GO decision;
- Shopify webhook ingress active before GO;
- an interactive Shopify sync endpoint usable during the intake hold;
- the queued initial task consumed before GO;
- the automatic initial-sync enqueue failed or uncertain;
- any source/product/order/refund/payout/financial row appearing before
  GO;
- a blanket zero-event assertion treating the non-financial
  `SHOPIFY_STORE_CONNECTED` event as financial ingestion;
- a GO decision that does not explicitly authorize the full intake
  contract (§I definition: sets A and B, any-age complete parent orders
  and refund histories, catalog, payout leg), or GO wording that
  authorizes only source dates up to seven days old;
- founder or merchant refusal of the any-age parent-order reach, or of
  the complete refund-history intake (the §Q step-11 STOP governs the
  disposition: no worker or webhook release, no queue purge);
- the refund catch-up leg of the latest accounted execution reporting
  status `error` or incomplete evidence, or an earlier execution's
  refund-leg failure that has not been closed per the §I closure rule;
- `fetch_failures > 0` on the latest accounted execution, or on any
  earlier execution whose affected candidate's complete refund history
  is still not present;
- the A/B candidate-set overlap double counted;
- a B candidate missing its local parent, its cancellation provenance
  stamp (where Shopify shows `cancelled_at`), or its complete refund
  evidence at a checkpoint, unless the gap is `pilot_scope_skipped`
  (expected 0 on the EGP store) or a counted loud error
  (`cancelled_processing_errors` / `fetch_failures` / `errors`) that
  has been dispositioned AND CLOSED by a recorded, explained
  re-execution (parent, stamp, and complete refunds now present) — a
  retry that leaves any of them absent, a candidate no re-execution
  can re-select, or a permanent malformed-payload rejection is NOT
  accounted for; the §I intermediate-state allowance covers only the
  period between the initial run and the completed closure;
- a cancelled B candidate classified as harmless, skipped, or outside
  the required parent/evidence set;
- a first-seen cancelled captured-money order in A left unbooked, or
  carrying a stamp-failure entry in `cancelled_processing_errors` that
  is not closed;
- `pilot_scope_skipped` nonzero in either leg on the EGP store;
- the per-leg inequality `cancelled_financial_candidates −
  cancelled_financial_processed ≤ cancelled_processing_errors +
  pilot_scope_skipped` failing;
- `cancelled_processing_errors` or `errors` nonzero and not
  individually dispositioned and closed;
- a refund-leg `status = "ok"` read as proof of zero errors, or the
  top-level task `status` read as proof of anything;
- a leg result shape carrying no counters (token missing/revoked,
  `unavailable`, or a caught exception) accepted as a completed leg;
- a task result with no leg keys (`skipped` "Store not active" /
  "tenant not writable", `error` "Store not found"), or a task that
  raised before any leg (Celery FAILURE, no result dict) — the queued
  task consumed without executing the authorized intake — or a
  re-enqueue before its cause is explained and the no-ingestion proof
  re-proven;
- a GO record or I13 sign-off that records a VALUE for
  `INITIAL_SYNC_STARTED_AT`, an effective window boundary, or any
  per-leg result field before the worker has executed (a retrospective
  authorization — the contract wording names them as future values,
  which is required; a filled-in value is the violation), or that is
  amended after signing to include one;
- a GO record whose "version <n>", or an I13 sign-off, does not name
  the `INTAKE_CONTRACT_VERSION` (the §B runbook revision) in force, or
  a merchant-run runbook revision whose §I definition block is not
  textually identical to the G1-rehearsed one;
- the `[A52] _sync_orders start` line absent, or recorded windows not
  matching its `created_at_min`/`created_at_max`, or
  `created_at_max − created_at_min ≠ 7 days`, or the ordering
  authorization sign-off `< TASK_RECEIVED_AT ≤ INITIAL_SYNC_STARTED_AT`
  failing (a larger-than-seconds gap between the last two is not an
  abort by itself but must be explained in the record), or a shop
  domain copied from that line into attachable evidence;
- a closure re-execution that books any order outside
  AUTHORIZED_PARENT_ORDER_SET, or that is not recorded as an explained
  second execution;
- more than one unexplained initial task;
- an older parent order or refund excluded merely because its date
  predates the seven-day window;
- while the §I nested-collection caps are in force: an order with more
  than 50 line items or a product with more than 60 variants in the
  intake (the merchant-shape precondition violated); a §K per-order
  line-item or per-product variant completeness control missing or
  failed (line items: an export/evidence count mismatch; variants: an
  export count over the cap — mirror/catalog equality is deliberately
  not demanded, §K); the variant-truncation worker warning present; a
  truncated line-item or variant collection read as complete evidence
  or a complete catalog; or a GO / I13 sign-off given without the
  merchant-shape precondition record;
- any claim that `import_mode="skip"` suppresses the refund catch-up;
- an initial-sync result not observed and retained;
- webhook release before initial-task reconciliation;
- beat restarted before initial-task and webhook-retry reconciliation;
- a queue purge proposed as evidence of a clean cutover.

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

**Raw operational evidence is PRIVATE by default.**

Safe to attach without merchant-specific redaction:

- Git commit and tree SHAs;
- public CI run IDs and conclusions;
- the fresh-database zero-count output when every business count is zero
  and no host/database identifier is included;
- explicitly synthetic, manually inspected aggregate `/_health/alerts`
  output.

The following must **never** be attached raw: `pilot_preflight` JSON;
`/_health/full`; `/_metrics/`; application, worker, beat, or proxy logs;
bootstrap API responses or screenshots; source CSVs and Shopify exports;
reconciliation/control packs; backups; request/response headers;
environment dumps.

Before any GitHub attachment:

1. Preserve the unmodified original in private encrypted storage.
2. Create a separate sanitized copy.
3. Inspect for and remove: names; email addresses; shop domains; company
   slugs; customer/order/refund identifiers; source row contents;
   merchant amounts; hostnames/IP addresses where private; cookies;
   authorization headers; CSRF tokens; Shopify secrets/tokens;
   database/Redis/Sentry URLs or credentials.
4. Record the private original's hash and the sanitized copy's hash.
5. Upload only the sanitized copy.

For `pilot_preflight` results, GitHub evidence may contain only a manually
redacted summary of: phase; exit code; violation codes; final PASS/FAIL.
Do not attach raw violation messages — they can contain `shop_domain`.
For `/_health/full` and `/_metrics/`: private evidence only; never attach
raw to a public GitHub issue or PR (consistent with the §F2 deployment
restriction on those endpoints). For real merchant runs, even aggregate
counts may be commercially sensitive — keep them private unless the
founder explicitly approves a sanitized excerpt.

---

## Q. Post-G2 first-merchant cutover — separate authorization required

This phase is procedural only and is **not executed by this PR**. Only
after G1 and G2 are recorded complete in the
[live tracker](../status/constrained_pilot_status.md) may the founder:

1. **load the completed gate-tested revision pack (§N5)** and verify the
   intended merchant deployment uses it exactly:
   deployed commit SHA == `GATE_TESTED_COMMIT_SHA`; deployed BACKEND
   image/artifact digest == `GATE_TESTED_IMAGE_OR_ARTIFACT_DIGEST`;
   deployed migration manifest == `GATE_TESTED_MIGRATION_MANIFEST`.
   The FRONTEND bundle is the one deliberate exception to
   artifact-identity (§N5 split rule): the rehearsal bundle carries the
   rehearsal API origin compiled in, so it must NOT be carried into the
   merchant deployment — rebuild the frontend from exactly
   `GATE_TESTED_COMMIT_SHA` with the merchant deployment's
   `NEXT_PUBLIC_API_URL`, verify the built bundle per §G1c (merchant
   origin present; `localhost:8000` absent), and record the merchant
   deployment's own `FRONTEND_BUNDLE_DIGEST` + `FRONTEND_BUILD_ORIGIN`
   beside the pack values. Serving the rehearsal-built bundle — or any
   bundle whose recorded build origin is not this deployment's origin —
   is a STOP.
   If ANY backend pack value differs, or the frontend was not rebuilt
   from `GATE_TESTED_COMMIT_SHA`: STOP — perform a fresh G1 and G2
   cycle on the desired revision, issue a new revision pack, and use
   only that newer completed pack. Equivalence is NEVER inferred from
   a same branch name, a version label, green CI, a small diff,
   "docs only", or developer judgment.
   (If `main` advanced but the deployment uses the exact already-tested
   commit and artifact, the existing G1/G2 proof remains applicable.)
2. provision a **NEW empty isolated merchant database**;
3. repeat, against the new database and the new host: the revision proof
   (§B); the fresh-database zero-count proof (§C); the environment-safety
   proof (§E — including the §E3 frontend build-time variable record
   with THIS deployment's `NEXT_PUBLIC_API_URL`); deployment, service
   startup, version proof and boot health (§G — including the §G1c
   frontend build from `GATE_TESTED_COMMIT_SHA` with THIS deployment's
   origin and its built-artifact verification, per step 1); **the §F
   blockers with FRESH evidence for THIS
   deployment and THIS merchant** — F1 confirmed by verifying that
   `GATE_TESTED_COMMIT_SHA` contains the fixing PR recorded in the G1
   `preflight/` evidence (hash-bound via `G1_EVIDENCE_MANIFEST_HASH`;
   not re-proven), F2 proven per its §F evidence field — the
   reverse-proxy/config test AND an external HTTP probe from outside
   the merchant host (`/_health/alerts` answers; `/_health/full` and
   `/_metrics/` are refused) — and F3 decided and proven for the real
   merchant's expected webhook burst and retry volume —
   synthetic-rehearsal F2/F3 evidence does NOT transfer, and step 4's
   intake hold verifies neither; base onboarding (§H); activation-aware
   validation (§I1); pilot activation (§I2) — **never before the fresh
   F1–F3 evidence exists for this deployment**; pilot-aware Shopify
   provisioning (§I3);
4. establish the **controlled Shopify intake hold** (§I4 form): webhook
   and interactive-sync ingress blocked with retryable non-success
   responses; beat stopped; worker drained and stopped; Redis/broker
   AVAILABLE; task-queue baseline recorded;
5. connect the **real merchant Shopify store** — for the first time
   anywhere in this process — through the controlled **standalone OAuth
   path**;
6. prove the post-connect state: exactly one ACTIVE real store; **no
   active `ShopifyUserBinding` yet**; the `initial_store_sync` enqueue
   succeeded (private log "Queued initial Shopify sync for <shop>" — on
   "Could not queue initial Shopify sync" or uncertainty, apply the §I5
   enqueue-failure disposition and STOP before GO); **no sync has
   executed**;
7. perform the **real-store J0 binding ceremony** (the same ceremony
   class the synthetic I6/J0 rehearsal proved): third-party cookies
   disabled under the designated pilot browser posture → session token →
   `not_bound` → the standalone intended merchant OWNER creates the
   linking nonce → embedded redemption → exact real
   store/`sub`/membership/company binding → nonce replay refusal →
   bound embedded session login resolving the correct merchant company
   and membership;
8. complete provider, bank, and remaining configuration;
9. run the **first binding-dependent go-live preflight** — legitimately
   using the read-only store-currency probe, since no product sync has
   run under the hold — and require it clean, including the expected
   OWNER/store binding;
10. capture the **pre-GO no-ingestion baseline** (read-only): prove no
    new `ShopifyOrder`; no `ShopifyRefund`; no synchronized Shopify
    product `Item`; no `ShopifyPayout` or provider-payout financial
    state; no sync-created `ProviderRawObject`; no order/refund/payout
    financial `BusinessEvent`; no posted `JournalEntry` from Shopify
    ingestion; no sync-caused `last_sync_at` update. Name the EXPECTED
    bootstrap/configuration state explicitly — the `ShopifyStore` row,
    the post-ceremony `ShopifyUserBinding`, the Shopify
    warehouse/Customer/PostingProfile setup records, the module-account
    mappings, the non-financial `SHOPIFY_STORE_CONNECTED` event, and
    account/provider configuration — a blanket "zero BusinessEvent"
    assertion is WRONG because store connection itself emits a
    non-financial connection event. Capture:
    `PRE_GO_INGESTION_BASELINE_HASH`, `PRE_GO_EVENT_TYPE_COUNTS`,
    `PRE_GO_JOURNAL_COUNT`, `PRE_GO_SHOPIFY_SOURCE_COUNTS`,
    `PRE_GO_TIMESTAMP`. If merchant source or financial data has
    already been ingested: STOP and recreate the fresh merchant
    environment — never delete it manually to recover the proof;
11. sign the **dated GO decision**, using the intake-contract
    definition (§I, before I13). The GO record authorizes the FUTURE,
    execution-relative contract: the worker is stopped until step 12,
    so `INITIAL_SYNC_STARTED_AT` and the effective window boundaries
    cannot exist at signing, and a value supplied later would make the
    authorization retrospective — the GO must precede the first
    merchant source write and must not cite anything that does not yet
    exist. It must state: "I authorize release of Shopify financial and
    source intake for this merchant. I authorize the initial store sync
    to execute after this GO under the recorded intake contract,
    version <n>. At execution start the worker records
    `INITIAL_SYNC_STARTED_AT`; `ORDER_CREATED_WINDOW` and
    `REFUND_CANDIDATE_UPDATED_WINDOW` are derived from that
    execution-time value with `INITIAL_LOOKBACK_DAYS = 7`. I understand
    the actual `INITIAL_SYNC_STARTED_AT` and the effective window
    boundaries do not exist at signing and will be captured and
    reconciled to this contract in step 12 before any merchant-facing
    checkpoint. Under that contract the task ingests: (1) eligible
    orders created during the execution-time seven-day
    `ORDER_CREATED_WINDOW`; (2) refunded or partially refunded orders
    whose Shopify order.`updated_at` falls during the execution-time
    seven-day `REFUND_CANDIDATE_UPDATED_WINDOW`, regardless of the
    parent order's age; (3) for every order selected by item 2, the
    complete parent order and its complete refund history as available
    when the catch-up executes, regardless of the individual refund
    dates; (4) the merchant product catalog; (5) the payout
    synchronization leg, while payout accounting remains blocked under
    `ISOLATED_SHADOW_LEDGER_V1`. I understand that an order created
    months or years before this GO decision may be imported and booked
    in full when its refund state changed during the seven-day
    refund-candidate window. I understand that refunds attached to
    that selected order may have dates outside the seven-day window. I
    authorize this as shadow-ledger intake, subject to the documented
    controls and stop conditions." Record: `GO_TIMESTAMP`,
    `GO_OPERATOR`, the redacted Shopify store identity,
    `INITIAL_LOOKBACK_DAYS = 7`, `PRE_GO_INGESTION_BASELINE_HASH`, the
    authorized intake-contract version — `INTAKE_CONTRACT_VERSION`,
    the §B runbook revision SHA whose §I definition text is the
    contract (this is the "version <n>" the wording cites) — and
    merchant acknowledgement/approval where required. While the §I
    nested-collection caps are in force the record must also carry
    `MERCHANT_SHAPE_PRECONDITION_VERIFIED`: a dated verification, from
    the merchant's Shopify export (export capture date; the per-order
    line-item and per-product variant counts retained privately), that
    no order has more than 50 line items and no product has more than
    60 variants — stated as a STANDING constraint for the pilot's
    operating period while the caps are in force (§I ongoing rule),
    not a one-time export fact. This field sits AROUND the frozen GO
    text, which is deliberately unchanged: under the verified precondition the GO's
    "complete parent order" (item 3) and "merchant product catalog"
    (item 4) are provable; without it they are not — the caps truncate
    the stored order's `line_items` evidence beyond 50 and the product
    mirror beyond 60 variants (never an invoice or journal amount —
    §I money-truth). A GO signed without this field while the caps are
    in force, or a merchant whose export violates either cap, is a
    STOP: no worker or webhook release, no queue purge — the same
    disposition as the any-age refusal below; the §I pagination fix
    must merge and a new gate cycle must complete on a revision
    containing it before that merchant's GO. The GO record
    must NOT record a VALUE for `INITIAL_SYNC_STARTED_AT`, any
    effective window boundary, or any per-leg result field (the
    contract wording above names them as future values — that is
    required; a filled-in value is the violation) — those exist only
    after execution and are first recorded in the post-execution
    controls of step 12 (from which the §K control pack copies them);
    the GO record is never amended after signing to add one. The GO
    record must NOT claim that all imported source timestamps are at
    most seven days old, must not promise post-GO-only source dates,
    and must not describe the reach as a simple seven-day historical
    window.
    Clarify in the record: `import_mode="skip"` suppresses the
    onboarding historical-import request; it does NOT suppress the
    OAuth-triggered initial sync or its refund catch-up. **If the
    founder or merchant does not accept the any-age parent-order and
    complete refund-history reach: STOP before releasing any worker or
    webhook ingress — do not purge the queued task, and do not claim a
    post-GO-only source-date cutover under the current code. A
    code-level intake-selection control must be designed, implemented,
    reviewed, and proven before connecting that merchant store;**
12. release intake — **worker only**: keep webhooks blocked and beat
    stopped; start the Celery worker; observe the queued
    `shopify.initial_store_sync` task; record its task id, start/end
    timestamps, and complete result. **Post-execution controls — the
    FIRST place these values are recorded (they never appear in the
    step-11 GO record; the §K control pack copies them from here):**
    from the worker's private `[A52] _sync_orders start …
    created_at_min=… created_at_max=…` INFO line (transcribe ONLY the
    two timestamps — the line carries the shop domain; the windows are
    NOT in the task result) record `INITIAL_SYNC_STARTED_AT` =
    `created_at_max`, `ORDER_CREATED_WINDOW_START`/`_END` =
    `created_at_min`/`created_at_max`,
    `REFUND_CANDIDATE_UPDATED_WINDOW_START`/`_END` = the same pair (the
    refund leg receives the identical values and logs no window of its
    own), plus `TASK_RECEIVED_AT` from the task's durable
    `django_celery_results` TaskResult row (start time, keyed by task
    id; corroborated by the Celery received/started log line), and
    every per-leg result field; reconcile them to the step-11 GO
    record — `INTAKE_CONTRACT_VERSION` and `INITIAL_LOOKBACK_DAYS = 7`
    — by the §I rule: `created_at_max − created_at_min` = exactly 7
    days, and `GO_TIMESTAMP < TASK_RECEIVED_AT ≤ INITIAL_SYNC_STARTED_AT`
    (the inequality failing is a STOP; the last two are normally
    seconds apart — a larger gap is not a STOP by itself but must be
    explained in the record) — before any merchant-facing checkpoint;
    an absent A52 start line is a STOP. Require exactly one initial
    task consumed on release; any later re-execution used for §I
    closure is a separately recorded, explained execution (own task id
    or CLI invocation, own A52 line, counters and inequality, retained
    beside the release result), and more than one UNEXPLAINED initial
    task is a STOP. A task result with NO leg keys (`skipped` "Store
    not active" / "tenant not writable", `error` "Store not found"), or
    a task that raised before any leg (Celery FAILURE, no result dict),
    means the queued task was consumed without executing the
    authorized intake — STOP; do not re-enqueue until the cause is
    explained and the step-10 no-ingestion baseline is re-proven.
    Require completion or a fully explained loud failure, evaluated PER
    LEG (the top-level `status` is unconditionally `"ok"` when the legs
    run and proves nothing — §I definition).
    **Reconcile to the authorized intake contract** (§I definition),
    not merely to source timestamps within seven days: record the A
    and B candidate sets and their overlap; reconcile their union
    from order ids without double counting (never by summing leg
    counters); require, for every B candidate — cancelled or not — a
    local parent order, its complete refund history, and — where
    Shopify shows `cancelled_at` — the cancellation provenance stamp
    verified from the task result and worker log (no stamp-failure
    entry, no "Cancellation provenance stamp failed" warning; the
    raw-payload field is corroboration only — §I definition), or the
    leg to fail loudly — the ONLY B candidates permitted to lack a
    parent or evidence at the checkpoint are those accounted for by
    the §I definition's two legitimate outcomes: `pilot_scope_skipped`
    (must be 0 for this EGP merchant), or a counted loud error
    (`cancelled_processing_errors` / `fetch_failures` / `errors`) that
    has been dispositioned AND CLOSED by a recorded, explained
    re-execution (parent, stamp, and complete refunds now present —
    §I closure rule; a candidate no re-execution can re-select or a
    permanent malformed-payload rejection is never accounted for), and
    any other gap is a STOP; require every first-seen cancelled
    captured-money order in A to be booked and stamped and every
    never-captured cancellation to appear in
    `cancelled_no_effect_skipped` (or, where its writer failed, in
    `errors`/`cancelled_processing_errors`, dispositioned and closed
    there per the §I no-financial-effect closure);
    record the per-leg counters (`pilot_scope_skipped`,
    `cancelled_financial_candidates`, `cancelled_financial_processed`,
    `cancelled_no_effect_skipped` (orders leg only),
    `cancelled_processing_errors`) and require the per-leg inequality
    `cancelled_financial_candidates − cancelled_financial_processed ≤
    cancelled_processing_errors + pilot_scope_skipped` to hold in both
    legs; require the refund leg's `status = "ok"` and
    `fetch_failures == 0` on the latest accounted execution — a
    release-execution fetch failure is a STOP at that moment and is
    closed only per the §I closure rule (a recorded re-execution whose
    refund leg is `ok` with `fetch_failures = 0` and the affected
    candidate's complete refund history present, both results
    retained) — AND read `errors` and `cancelled_processing_errors`
    directly (`status = "ok"` flips only on fetch failures and is not
    evidence of zero errors — §I leg-status truth); require every
    refund amount to use the complete
    transaction/line evidence; make old parent orders and old refund
    dates visible in the control pack (they are NOT violations when
    selected through B, and a cancelled candidate's booked parent and
    refunds are the required outcome, not a variance); verify every
    posted financial effect occurs exactly once; keep task-level
    errors visible and dispositioned; rejected/failed/quarantined
    outcomes appear in their operator surfaces; no unsupported
    payout-accounting effect occurs; every product remains NON_STOCK;
    no inventory/COGS residue appears; the fulfillment backfill and
    `deferred_cogs` leg appear only in their expected non-financial
    state (§I definition's mechanical side effects); and — while the
    §I nested-collection caps are in force — verify the §K
    completeness controls against the merchant's Shopify export:
    every intake order's line-item count is at most 50 and equals
    the stored order evidence's count, every product's variant count
    in the export is at most 60 (export-side only — the sync
    legitimately skips SKU-less variants and collapses shared SKUs,
    so no mirror/catalog equality is demanded — §I/§K), and the
    worker log carries no variant-truncation warning (a cap breach,
    a line-item count mismatch, or a missing control is a STOP);
13. rerun `pilot_preflight --phase go-live`, `/_health/alerts`, and the
    source/control totals;
14. only after the initial task is accounted for, unblock Shopify
    webhooks; observe and reconcile webhook retries; prove duplicate
    webhook delivery does not duplicate financial effects;
15. start Celery beat **LAST**;
16. rerun the go-live preflight and alerts after beat starts;
17. sign the final **intake-complete checkpoint**. Do NOT sign while:
    the refund catch-up leg's status is not `ok`, or `fetch_failures >
    0`, on the latest accounted execution, or an earlier execution's
    fetch failure remains unclosed per the §I closure rule; an old
    candidate parent's complete history is unexplained; a B
    candidate — cancelled or not — lacks its local parent, its
    provenance stamp, or its complete refund evidence and is not
    accounted for by `pilot_scope_skipped` or a counted loud error
    that has been dispositioned AND CLOSED (parent, stamp, and
    complete refunds present after a recorded, explained
    re-execution — a gap that is only counted, dispositioned, or
    retried without closure, a candidate no re-execution can
    re-select, or a permanent malformed-payload rejection is not
    accounted for); a cancelled candidate has been classified as
    harmless, skipped, or outside the required parent/evidence set;
    `pilot_scope_skipped` is nonzero in either leg; the per-leg
    inequality fails; `cancelled_processing_errors` or `errors` is
    nonzero and undispositioned or unclosed; the refund leg's
    `status = "ok"` is being read as proof of zero errors; the task
    result had no leg keys or the task raised before any leg; the
    step-12 post-execution controls (`INITIAL_SYNC_STARTED_AT`,
    `TASK_RECEIVED_AT`, effective windows from the A52 line, per-leg
    fields) are missing or do not reconcile to the step-11 GO record
    and `INTAKE_CONTRACT_VERSION`; a closure re-execution booked an
    order outside AUTHORIZED_PARENT_ORDER_SET or is unrecorded; the GO
    record was amended after signing; the A/B overlap is double
    counted; a partial fetch is treated as success; or — while the §I
    nested-collection caps are in force — the
    `MERCHANT_SHAPE_PRECONDITION_VERIFIED` record is absent from the
    GO record, a §K per-order line-item or per-product variant
    completeness control is missing or failed, or any order over
    50 line items or product over 60 variants entered the intake.

The GO decision precedes the first merchant product/order/refund source
write.

STOP if: the intended merchant deployment uses a revision or image not
named by the completed revision pack; the merchant deployment serves a
frontend bundle not rebuilt from `GATE_TESTED_COMMIT_SHA` with the
merchant deployment's recorded `FRONTEND_BUILD_ORIGIN` and verified per
§G1c (the backend artifact-identity rule never transfers to the
origin-specific frontend bundle — §N5 split rule); the merchant company is activated
(§I2) without fresh F1–F3 evidence for the merchant deployment and
merchant (rehearsal F2/F3 evidence transferred); the rehearsal database
or its backup is reused; synthetic financial history appears in the
merchant database; both synthetic and real stores coexist; the real
store is connected before G1/G2 closure; the real-store go-live preflight is run
before the binding ceremony; the connect path silently creates or
selects an unrelated binding; the intended merchant OWNER is not the
bound membership; a first-owner fallback occurs; the real embedded user
reaches Nxentra before the explicit binding; the initial task runs
before GO; the GO record records a value for `INITIAL_SYNC_STARTED_AT`,
an effective window boundary, or a per-leg result field that did not
exist at signing, or is amended after signing; its intake differs from the
authorized intake contract (§I definition); a cancelled B candidate is
treated as skipped or outside the required evidence set; more than one
unexplained initial task runs; webhooks are
unblocked before the initial task is accounted for; beat starts before
initial intake and retries are reconciled; any unsupported
payout/COGS/inventory financial effect appears; any source row lacks a
truthful outcome; merchant financial data arrives before the final
go-live preflight and GO sign-off; or go-live preflight is not clean.

**Future product debt (non-blocking):** the first pilot uses an
operator-enforced intake hold because store connection and initial
synchronization are currently coupled. A future self-service pilot/beta
should add a persistent, server-enforced store-ingestion state or an
explicitly authorized suppress-initial-sync / release-intake mechanism
respected by scheduled tasks and webhooks. It is deliberately NOT
implemented in this PR.

No in-place synthetic-store-to-real-store replacement procedure exists or
is permitted: the exactly-one-ACTIVE-store constraint and the rehearsal
database's synthetic financial history make in-place promotion the wrong
model.

Sign-off: founder/operator ______ date ______
