# Fresh isolated pilot — executable runbook

**Status: PROCEDURE — partially rehearsed, nothing closed.** It defines
how to take a reviewed revision from a clean deployment target to a
supervised first-pilot environment and then prove G1 and G2. On the
rehearsal deployment the §B–§G steps as they stood at runbook revision
`0c1e2b1` were executed with evidence at `36b8de4` on 2026-09-05; the
first §H–§I shakedown attempt there found a P1 that PR #145 fixed; the
deployment was re-pinned to `968e486` on 2026-09-08 (a backend-only
change: the §B1 pin, the §C fresh-database proof, F1/F3 ancestry and §G1f
boot health were redone, and the §G1c bundle evidence was carried on
recorded backend-only reasoning); §H1–§I3 then passed a non-evidence
shakedown on a throwaway database — the §B re-pin rule working, not G1
progress. On 2026-09-11 the deployment was re-pinned to `d9c94c6` (the
PR #149 merge: the `8a61e36` floor plus the runbook revision that added
§E5, §G1g and the amended §B1/§G1c/§G1f probes) with a fresh §G1c build,
a restart of every process from the re-pinned checkout, and the §B–§G
evidence redone — §E5, §G1g and the amended probes included; the §B1 pin
record's hosting-region field remains to be filled — the pilot database
untouched, §H onward not re-run; the §L1 external monitor was put in
place the same day (a deployment property, not G1 evidence). The Shopify
app-identity precondition added to §E3 (a dedicated app whose webhook,
compliance, redirect and app URLs point at the deployment host;
`NEXT_PUBLIC_SHOPIFY_API_KEY` set at build) was executed on 2026-09-13:
the deployment was re-pinned to `710d089` (PR #150) with the app switch
folded into a full §B1 re-pin (fresh §G1c build, every process
restarted, §B–§G evidence redone with the §E3 app-identity record and
the §G1c client-id verification). The §H–§I window then ran on that
deployment 2026-09-17 → 2026-09-22 (§I4 hold, §I5, J0, §I7–§I13, the
§I14 release) and STOPPED at §I14 on a code defect — a re-entrant
projection drain consumed two same-pass refunds — fixed by PR #152
(`17dd1a9`, the new §B1 floor); founder decision D11 = (a): a fresh
database, a new pin containing this revision, and §H–§I re-run in full.
Nothing closed.
Neither that rehearsal nor this document closes anything: **G1 and G2
remain OPEN and merchant data remains blocked** until the live tracker
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
    `git status --porcelain` (must be empty). **Re-run the porcelain
    check after every §G1c frontend build:** `next build` (Next
    14.2.35) rewrites the tracked `frontend/next-env.d.ts` (it
    regenerates the file's documentation-URL comment), so a tree that
    was clean at pin time is dirty after the build. A diff confined to
    that one file is the build side-effect, not a modified deployment,
    ONLY if `git diff frontend/next-env.d.ts` shows nothing but that
    comment line changing: record that diff, restore the file (from the
    repository root `git checkout -- frontend/next-env.d.ts`; from
    `frontend/` `git checkout -- next-env.d.ts`), re-run the porcelain
    check, and record both porcelain results (`revision/`) — the file is
    a TypeScript declaration reference and does not enter the served
    bundle. Any other change to that file, or any other dirt after the
    build, is a STOP.
  - Expected result: HEAD is a commit on `main` that has a fully green CI
    run (all seven jobs including Quality Gate). **Minimum
    application-code baseline (revision floor):**
    `17dd1a98d439a1eb439f4788c5c72042a61238b7`
    (code tree `cc380e4d781e4201eb650d042f47d695f4696647`; green main CI
    run 35966835373, seven jobs) — the merge of PR #152 (the
    `process_pending` re-entrancy guard), which contains, in merge order: the PR #139 refund-completeness
    correction (`3fc79de`), the PR #140 cancelled-order refund-recovery
    correction (`cd8bc9d`), the PR #141 store-sweep history guard
    (`ee003d5`), the PR #143 nested-collection pagination fix
    (`2be1819`), the PR #142 NEXT_TASKS follow-up filing (`0a59880`,
    docs only), this runbook's first merged revision (PR #138,
    `0c1e2b1`), the PR #144 F3 dedicated webhook-throttle scope
    (`36b8de4`), the PR #145 emit-boundary RLS-context restore
    (`968e486` — the fix for the P1 the first rehearsal shakedown
    found: `POST /api/auth/register/` 500 under a least-privilege
    NOBYPASSRLS database role), the PR #147 npm-audit gate with its
    explicit expiring allowlist plus `images.unoptimized` (`418173c`,
    A246), the PR #146 stale-credential 401 hardening (`f5ad19e`,
    A244), the PR #148 login-time company switch through the
    canonical writer (`8a61e36`, A245), the PR #149/#150/#151 runbook
    and tracker revisions (`d9c94c6`, `710d089`, `e1b5948`, docs only)
    and the PR #152 projection-pass re-entrancy guard (`17dd1a9`: a
    projection never observes its own in-flight pass, so an order and
    its refund pending in one projection pass no longer consume the
    refund — found by the 2026-09-22 rehearsal §I14 release at
    `710d089`). **No earlier commit is eligible** for G1/G2: the
    previously named floors `2be1819` (PR #143) and `8a61e36`
    (PR #148), the rehearsal deployment's interim §B pins `36b8de4`
    (PR #144), `968e486` (PR #145), `d9c94c6` (PR #149) and `710d089`
    (PR #150) — and every commit before them — predate at least one of
    these fixes and may no longer be used; no verdict transfers from
    them. (The rehearsal deployment, pinned at
    `968e486` until 2026-09-11, was re-pinned that day to `d9c94c6` —
    the PR #149 merge, at or after the floor — with a fresh §G1c
    frontend build AND a restart of every process from the re-pinned
    checkout, so that §G1e's version proof holds for the bundle and for
    the running Next server, which reads `images.unoptimized` from
    `next.config.js` at start, and its §B–§G evidence redone. On
    2026-09-13 it was re-pinned again to `710d089` (PR #150) with the
    §E3 app switch; the §H–§I window then ran there 2026-09-17 →
    2026-09-22 and STOPPED at §I14 — see the header and the tracker.
    Every later re-pin — including the one this document's own merge
    requires under the rule below — repeats exactly that.) Because this runbook
    document itself merges after that baseline, the exact revision
    selected at execution must be a `main` commit that contains the
    floor (`git merge-base --is-ancestor 17dd1a98d439a1eb439f4788c5c72042a61238b7 <EXECUTED_SHA>` exits 0)
    AND this merged runbook revision, with its own green required CI;
    the operator records that exact selected SHA, and the runbook
    document revision is recorded separately from the deployed
    application revision. A later reviewed `main` revision with green
    required CI may be **selected here, before a new rehearsal begins**
    — that selected revision and its immutable artifact then become the
    subject of this runbook's G1 and G2, and the operator records the
    new exact SHA. This selection rule never transfers a PRIOR G1/G2
    verdict forward — green CI is a selection precondition, not an
    operational proof: the merchant cutover (§Q) must deploy the exact
    revision pack that passed the gates.
    **Selection-time gate note (A246, PR #147):** the required
    Security & Deploy Check runs `scripts/npm_audit_gate.py` against
    `frontend/npm-audit-allowlist.json`, which accepts exactly two
    Next 14.x critical advisories — GHSA-2xp9-vwfh-vxw4 (the Image
    Optimization API RCE; mitigated by `images.unoptimized=true`, §G1c/
    §G1g) and GHSA-p293-qw3h-jr36 (Windows-hosted servers only;
    mitigated by Linux-only hosting) — each until **2026-11-30**,
    tracked by E11 (the Next 15 upgrade). An expired entry stops
    suppressing and turns the gate red by design; a revision whose
    required CI is red for that reason is NOT eligible here — E11 (or a
    fresh, narrow, dated allowlist decision) must land on `main` first.
    Never select around a red gate.
  - Evidence to retain (`revision/`): commit SHA, tree SHA, CI run id and
    conclusion, deployment/image identifier, the frontend build origin,
    Shopify client id and bundle digest (`FRONTEND_BUILD_ORIGIN` /
    `FRONTEND_BUILD_SHOPIFY_CLIENT_ID` / `FRONTEND_BUNDLE_DIGEST` —
    recorded when §G1c builds; the API origin is compiled into the
    client bundle and the Shopify app client id into the server build,
    §E3), deployment
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
A database from a STOPPED or superseded window is ARCHIVED, never dropped
or reused in place: `pg_dump` (custom format) with its SHA-256 recorded in
the private manifest, the old database kept under its own name, a NEW
database name for the fresh proof, and C2 run on the new name BEFORE any
process boots against it (the `EventBookmark` caveat). Every process's
`DATABASE_URL` is changed by re-creating the process (§G1e proves the
connected database per process); the §I4 hold stays in force across the
swap with NO gap — a Shopify-capable route with no ACTIVE store row
answers a discarding 200 (`shopify_connector/views.py`), so verify the
retryable 503 before and after every restart or proxy reload; the broker
is listed read-only before §I5 (a leftover task from the old context would
target the new store, whose ids restart at 1) and never purged (§O); §C1
seeds, the §E5 role posture and the §G1d periodic-task registry are
re-created and re-evidenced on the new database; the §L1 monitor is paused
with a dated note for the swap and RESUMED — with one recorded probe
result against the replacement deployment in `alerts/` — as soon as §G1f
boot health passes and before §H opens (§O treats a failing monitor as an
abort condition and §L1 requires its scheduled probes throughout); the
pause and resume timestamps are recorded together; TaskResult and row ids
restart at 1 (re-base the §I12/§I14 expectations).

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
    production — A162); `ALLOWED_HOSTS` containing `<DEPLOYMENT_HOST>` — record the exact
    value in `environment/` (not a secret) and whether it also lists a
    loopback host (`127.0.0.1` / `localhost`): the §G1f bare-probe
    status code depends on that entry, which is a recorded
    deployment-posture decision, never something changed to make a
    probe pass;
    `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` set to the real
    https origins (boot refuses wildcard/localhost values in production);
    `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`, `SHOPIFY_APP_URL` and
    `SHOPIFY_SCOPES` (with the build-time `NEXT_PUBLIC_SHOPIFY_API_KEY`
    below, the five values the app-identity rule below binds to ONE
    Shopify app);
    `SENTRY_DSN`; email settings as chosen. Optional tunables with
    defaults: `ALERT_UNRESOLVED_FAILURES_MAX` (0),
    `ALERT_PROJECTION_LAG_THRESHOLD` (50),
    `ALERT_PROJECTION_STALENESS_SECONDS` (21600),
    `SHOPIFY_SOURCE_STALE_SECONDS` (28800); `LOG_LEVEL` absent or
    `INFO` (never stricter — the §I intake-contract capture reads the
    worker's INFO-level `[A52] _sync_orders start` line; Celery's own
    task lines are not available on this logging configuration — §G1d
    — and the TaskResult row's `date_started` stands in).
  - **Frontend BUILD-time variables** — these are compiled into the
    build output by `npm run build` (the client bundle under
    `.next/static/`, or, for `_document.tsx`, the server-rendered
    document under `.next/server/`) — inlined at build; a variable
    absent at build is not recovered from the runtime environment by
    the client bundle or by any prerendered page — so they
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
    - `NEXT_PUBLIC_SHOPIFY_API_KEY` — REQUIRED for every deployment
      (never rely on the `_document.tsx` fallback, even where its value
      would equal the published id), recorded as this deployment's
      `FRONTEND_BUILD_SHOPIFY_CLIENT_ID`: `frontend/pages/_document.tsx`
      compiles it into the `shopify-api-key` meta tag that App Bridge
      is configured from; with it absent the build still SUCCEEDS and
      the fallback survives: the PUBLISHED app's public client id
      (`2258d6303a3672a381fe7606c2d2917b` — a public identifier, not a
      secret) is rendered into every prerendered `.html`, and
      `pages/_document.js` keeps the un-inlined `||` fallback (Next
      inlines only `NEXT_PUBLIC_` variables present at build), so
      inside any OTHER app's admin surface App Bridge
      would be configured with the wrong client id and the embedded
      path could not pass I6/J0 — at App Bridge initialization, or at
      the backend, which accepts only session tokens whose `aud` equals
      `SHOPIFY_API_KEY` (`verify_shopify_session_token`; the
      app-identity rule below). Its value must equal the backend
      `SHOPIFY_API_KEY`; §G1c verifies the built artifact.
    - `NEXT_PUBLIC_SENTRY_DSN` — optional; enables the frontend
      Sentry build path (record presence only, never the value).
  - **Shopify app identity — a dedicated app per deployment host is a
    PRECONDITION, not a convenience.** Shopify webhook subscriptions,
    the privacy-compliance URLs, the OAuth redirect URL and the
    application URL are declared APP-WIDE in the app's configuration
    file (`shopify.app.toml` for the published app: one fixed
    `[[webhooks.subscriptions]]` `uri`, `[webhooks.privacy_compliance]`,
    `[auth] redirect_urls`, `application_url`) and released as an app
    VERSION by `shopify app deploy`; the application registers no
    per-store webhooks (A51 removed `register_webhooks` — declarative
    subscriptions only). A store installed on the PUBLISHED app
    (`Nxentra Sync`, client id `2258d630…`, every URL on
    `https://app.nxentra.com`) therefore delivers EVERY webhook to the
    live host and none to this deployment — the §I15 retry
    reconciliation and the §J webhook proofs cannot run, and the §I4
    hold's webhook block has nothing real to hold back (its worker and
    beat arms still hold the OAuth-triggered initial sync) — and adding
    a second subscription URI to the
    published app would mirror every REAL merchant's webhooks to this
    host, which is forbidden. Consequently the app that the §I5 store
    installs must be an app whose `application_url`, redirect URL,
    subscription `uri` and compliance URLs ALL point at
    `<DEPLOYMENT_HOST>`. For the rehearsal that is the dedicated app
    **`Nxentra Sync REHEARSAL`** (client id
    `c2d69bb5f3c53a213faaa9936d33585d`, configured by the founder-held
    `shopify.app.rehearsal.toml` — the same shape as the tracked
    `shopify.app.toml` with every URL on the rehearsal host and
    `read_all_orders` removed; public identifiers only, no `[build]`
    table; not tracked at this revision — and released with
    `shopify app deploy --config rehearsal`, which never touches the
    published app; its protected-customer-data access is the
    unreviewed development-store posture (reasons recorded, no review
    submitted), and — as a rule of this runbook, not a Shopify
    enforcement — it is installed on nothing but the §I5 synthetic
    development store). The published app's configuration is never
    changed for a rehearsal purpose. Five deployment values are bound
    to that ONE app and must agree with each other and with the app's
    ACTIVE version:
    `SHOPIFY_API_KEY` — the app's client id: the OAuth `client_id`,
    and the `audience` that `verify_shopify_session_token` requires
    on every App Bridge session token the backend accepts;
    `SHOPIFY_API_SECRET` — that app's client secret: the OAuth token
    exchange, the webhook HMAC check and the session-token signature
    all depend on it;
    `SHOPIFY_APP_URL` — `https://<DEPLOYMENT_HOST>` exactly: the
    standalone OAuth path builds the `redirect_uri` it sends to
    Shopify as `<SHOPIFY_APP_URL>/api/shopify/callback/`
    (`shopify_connector/commands.py`), which must be an entry in the
    app's `[auth] redirect_urls`;
    `NEXT_PUBLIC_SHOPIFY_API_KEY` — the same client id, compiled into
    the build at §G1c (above);
    `SHOPIFY_SCOPES` — the scope set the app's active version declares
    (scope strings are compared as SETS of scope names — comma-split,
    order-insensitive — and recorded raw). Set it explicitly; never
    rely on the settings default, which INCLUDES `read_all_orders`, a
    scope the rehearsal app does not carry. The OAuth authorize URL
    sends this string and the string Shopify grants is stored on
    `ShopifyStore.scopes` at §I5. Without `read_all_orders`: the
    onboarding historical import — which §I3 never requests — clamps
    itself to a 59-day floor, and the initial-sync legs, unclamped by
    code, can read only the orders Shopify exposes to `read_orders`
    alone (the last 60 days — A126), so the §I definition's any-age
    parent reach on the B leg is NOT exercisable on the rehearsal app.
    That is a declared rehearsal/merchant scope difference — moot for a
    freshly created development store, which holds no older order;
    recorded in `environment/` and in the tracker — and a §Q step-3
    requirement, not a choice: for a real merchant the chosen app's
    ACTIVE version must carry `read_all_orders` — the §I definition's
    any-age parent reach is a GO requirement (§O; §Q step 11), so a
    merchant app whose active version lacks it cannot execute the
    contract the GO must authorize: a STOP at §Q step 3 (its intake
    would differ from the authorized contract), closed only by the
    per-app approval released in a NEW active version and this record
    redone against it; a missing scope is never a bound, and the
    step-11 disposition (a code-level intake-selection control)
    applies only where the founder or merchant does not want the
    reach — and its protected-customer-data access must be approved
    for non-development stores; both are per-app Shopify approvals
    that a dedicated app does not inherit from the published app, and
    the rehearsal app's unreviewed development-store posture proves
    nothing about either.
  - Evidence to retain (`environment/`, all public identifiers): the
    app name, client id and Partner/Dev Dashboard app id, the ACTIVE
    version name, `application_url`, the redirect URL and this
    deployment's `SHOPIFY_APP_URL`, the subscription `uri`, the
    compliance URLs, both scope strings raw — this deployment's
    `SHOPIFY_SCOPES` and the active version's declared list — with the
    set-comparison result (the granted string is recorded at §I5), the
    webhook API version, and the frontend build's client id
    (`FRONTEND_BUILD_SHOPIFY_CLIENT_ID`); for the rehearsal, also the
    PUBLISHED app's ACTIVE version name and release date as shown in
    the Dev Dashboard — expected unchanged from the pre-rehearsal
    value (`nxentra-sync-9`, released 2026-06-19, every URL on
    `https://app.nxentra.com`) — so the last STOP below is decided
    from the record; the secret by presence only, never its value.
    Before any §I5 on a new database after a long §I4 hold (days of
    503s): re-confirm in the Dev Dashboard that the ACTIVE app version
    still declares every §I4 topic (the application registers no
    per-store webhooks — `register_webhooks` was removed — so Shopify's
    "deleted after 8 consecutive failures if configured using the
    Admin API" rule is not expected to apply: an inference from its
    documentation as read 2026-09-24, not a guarantee); read and record
    (counts only) the app's webhook delivery/failure metrics there;
    and check the app's emergency developer inbox for Shopify's
    warning e-mails (expected after a hold; not a STOP) — record WHICH
    mailbox that is as a §E3 value, off-repo.
  - STOP if: a required value is missing; the process would start
    against the wrong database; `NEXT_PUBLIC_API_URL` is absent,
    non-https, missing its `/api` path, or not this deployment's real
    API base URL at the moment the frontend build runs;
    `NEXT_PUBLIC_SHOPIFY_API_KEY` is absent at that moment; any of the
    app's `application_url`, redirect, subscription or compliance URLs
    is on another host; `SHOPIFY_APP_URL` is not
    `https://<DEPLOYMENT_HOST>`, or `<SHOPIFY_APP_URL>/api/shopify/callback/`
    is not in the active version's redirect URLs; `SHOPIFY_API_KEY`,
    `NEXT_PUBLIC_SHOPIFY_API_KEY` and the recorded app client id are
    not one and the same value; the rehearsal deployment carries the
    published app's client id; `SHOPIFY_SCOPES` names a different
    scope set than the active version; or the published app's
    configuration was changed for a rehearsal purpose.

- [ ] **E4. Deploy check.**
  - Command / action: `python manage.py check --deploy --fail-level WARNING`
  - Expected result: exit 0, zero warnings (this is the same gate CI's
    Security & Deploy Check job enforces).
  - Evidence to retain: full output.
  - STOP if: any warning or error remains, or a secret value appears in
    captured evidence (redact and re-capture before proceeding).

- [ ] **E5. Database ROLE posture — RLS is enforced only for a
  non-bypassing role.**
  - Why: every RLS migration in the repository pairs `ENABLE ROW LEVEL
    SECURITY` with `FORCE ROW LEVEL SECURITY`, so table ownership does
    not bypass the tenant policies — but a PostgreSQL **superuser** or
    a role with **BYPASSRLS** bypasses them entirely, whatever the
    application sets. The PR #145 defect class (the emit boundary
    RESET the caller's RLS session context, so `register_signup` could
    not read back the membership row it had just projected —
    `POST /api/auth/register/` 500) was invisible on SQLite (the RLS
    session parameters are no-ops there), in CI (the Postgres
    superuser) and on the legacy droplet (the cluster admin role); it
    surfaced only on the first deployment that connected as a
    least-privilege role. A bypassing application role therefore makes
    G1 prove nothing about tenant isolation and hides this whole class.
  - Command / action: connect as the application role named in
    `DATABASE_URL` (the role the web, worker and beat processes use):
    from `backend/` with the deployment `.env` in place,
    `python manage.py dbshell` (it needs the `psql` client on the host;
    otherwise `psql "$DATABASE_URL"` from the same environment). Record
    names, booleans and counts only, never the password:
    `SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;`
    `SELECT count(*) FILTER (WHERE relrowsecurity) AS rls_tables, count(*) FILTER (WHERE relrowsecurity AND NOT relforcerowsecurity) AS not_forced FROM pg_class;`
    `SELECT tableowner, count(*) AS tables FROM pg_tables WHERE schemaname = 'public' GROUP BY tableowner;`
    (the application role owns the tables when it ran `migrate` — the
    expected posture; FORCE keeps an owner subject to the policies, so
    ownership is recorded, not refused). Confirm `RLS_BYPASS` is absent
    (§E2): production settings then add no `-c app.rls_bypass=on`
    connection option.
  - Expected result: `rolsuper = false`, `rolbypassrls = false`,
    `rls_tables > 0`, `not_forced = 0`, and the table owner(s) known.
  - Evidence to retain (`environment/`): the role name, the two
    booleans, the two counts and the owner listing, recorded as this
    deployment's `DATABASE_ROLE_POSTURE`.
  - STOP if: the application role is a superuser or has BYPASSRLS
    (never grant either to "fix" a request that fails under the
    least-privilege role — that failure is a code defect to report and
    fix on `main`, as PR #145 was, followed by a §B re-pin), or any
    RLS-enabled table is not FORCE'd.

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

- [ ] **F3. Shopify webhook-throttle decision — CODE ARM ON RECORD,
  EVIDENCE ARM PER DEPLOYMENT.**
  - Required outcome: the Shopify-capable webhook ingress carries its
    own throttle posture (no longer the shared anonymous bucket), and
    evidence that the first merchant's realistic webhook burst plus
    Shopify's retries fits safely within THIS deployment's EFFECTIVE
    policy.
  - Code arm on record: **PR #144** (merge `36b8de4371e6d95868a0b743a662b5e155b66227`).
    `platform_connectors/throttles.py::PlatformWebhookThrottle`
    (scope `platform_webhook`, default rate
    `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["platform_webhook"] = "120/minute"`)
    is the ONLY throttle class on BOTH §I4-enumerated Shopify-capable
    webhook routes — `/api/shopify/webhooks[/]` and
    `/api/platforms/<slug>/webhooks/`. The budget is per ident (DRF's
    `AnonRateThrottle.get_ident` with `NUM_PROXIES` unset: the whole
    `X-Forwarded-For` value when the proxy sends one, else
    `REMOTE_ADDR`), charged in `APIView.initial()` BEFORE each view's
    HMAC verification — the HMAC check stays the authenticity gate and
    the throttle is a per-client budget for unauthenticated traffic.
    An over-limit request gets DRF's 429 with `Retry-After`, a
    retryable non-success (Shopify retries any non-2xx delivery up to
    8 times over 4 hours — per Shopify's webhook documentation as read
    2026-09-24, which also states that after 8 consecutive failures a
    subscription is deleted only if it was created through the Admin
    API, and that warning e-mails go to the app's emergency developer
    address; this retry clock binds §I15, see there), never a
    discarding 200. Regression tests:
    `backend/tests/test_g1_f3_webhook_throttle.py`.
  - Effective policy (founder disposition B, 2026-09-05): the throttle
    counters live in DRF's default per-process `LocMemCache` (the
    repository configures no `CACHES`), so the ceiling is the nominal
    rate **× the number of web worker processes** — the §G1d command
    runs gunicorn with `--workers 3`, so ≤ 360/minute per ident
    effective — and the counters reset when a worker restarts. This is
    loose in the availability direction and is DISCLOSED, not fixed: a
    shared-cache bound is its own future task, never a rider on a G1
    run.
  - Evidence field (`preflight/`): (a) code arm — `git merge-base
    --is-ancestor 36b8de4371e6d95868a0b743a662b5e155b66227 <EXECUTED_SHA>` exits 0 ______; (b) this
    deployment's configured rate (the settings default unless
    overridden — record which), web worker count, the resulting
    effective ceiling, and the proxy's `X-Forwarded-For` posture (it
    must SET the header to the connecting client's address, never
    append or pass through a client-supplied value — otherwise the
    ident is attacker-controllable) ______; (c) the written burst/retry
    decision for THIS merchant against that effective ceiling ______.

**Evidence scope — per deployment, not per revision.** F1 is a code
property and travels with the revision: it is proven once by its fixing
PR and regression test, and every later environment only confirms the
executed revision contains that PR. F3 has the same kind of code arm
(PR #144, confirmed by ancestry) PLUS a per-deployment evidence arm.
**F2 and the F3 evidence arm do not transfer between environments:** F2
lives in each deployment's proxy/firewall, and F3's effective ceiling
depends on each deployment's worker count and proxy posture and on the
specific merchant's expected webhook burst and retry volume.
Synthetic-rehearsal evidence for F2/F3 therefore proves nothing about
the real merchant deployment — §Q step 3 requires fresh F2/F3 evidence
on the merchant host, for that merchant, before the merchant company is
activated. The §G1g `/_next/image` refusal follows the F2 shape (a proxy
rule plus external and loopback probes, per deployment).

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
- [ ] **G1c. Frontend build — with the recorded build origin AND
  Shopify client id, verified in the built artifact.**
  - Command / action: from `frontend/`: `npm ci` then, with the §E3
    frontend build-time variables in place (`NEXT_PUBLIC_API_URL` =
    this deployment's production https API base URL including the
    `/api` path; `NEXT_PUBLIC_SHOPIFY_API_KEY` = this deployment's
    `FRONTEND_BUILD_SHOPIFY_CLIENT_ID`), `npm run build`.
    Then verify the ARTIFACT, not the build process: the served client
    bundle (`.next/static/`) must contain the EXACT recorded
    `FRONTEND_BUILD_ORIGIN` value and
    must NOT contain `localhost:8000` (e.g.
    `grep -rl "<FRONTEND_BUILD_ORIGIN>" .next/static/` finds matches;
    `grep -rl "localhost:8000" .next/static/` finds none — a correct
    production build constant-folds the unset-variable fallback away,
    so any surviving `localhost:8000` means the origin was NOT baked
    correctly; never explain it away as dead code). Verify the Shopify
    client id the same way — it is compiled into the SERVER build, not
    the client bundle: Next compiles `frontend/pages/_document.tsx`
    into `pages/_document.js`, duplicates that module into some page
    and shared chunks, and renders its `shopify-api-key` meta tag into
    every prerendered `.html` under `.next/server/pages/`, so with
    `NEXT_PUBLIC_SHOPIFY_API_KEY` set (§E3 — required)
    `grep -rl "<FRONTEND_BUILD_SHOPIFY_CLIENT_ID>" .next/server/` lists
    MANY files (a Next 14.2.35 build of this revision: 30 — 18 `.html`,
    `pages/_document.js`, 11 other chunks) — record the count and
    confirm `pages/_document.js` is among them; a grep over
    `.next/server/` for any OTHER Shopify client id this repository
    knows (the published app's `2258d6303a3672a381fe7606c2d2917b` on a
    deployment whose app is not the published app; the rehearsal app's
    id on any other deployment) finds nothing — the same
    constant-folding rule: another known id surviving means the
    variable was absent, or carried that other id, at build time,
    and either is the STOP below (the greps are scoped to
    `.next/server/` and `.next/static/` on purpose — the webpack cache
    under `.next/cache/` keeps the source literal and is not part of
    the served build); and record whether the
    id also appears under `.next/static/` (at this revision only
    `_document.tsx` references the variable, so it should not — an
    appearance is a recorded observation, not a STOP; the server-build
    rule is the gate). Record the grep results, `FRONTEND_BUILD_ORIGIN`,
    `FRONTEND_BUILD_SHOPIFY_CLIENT_ID`, and a build digest
    (`FRONTEND_BUNDLE_DIGEST` — e.g. a SHA-256 over the `.next/static/`
    and `.next/server/` build output together with `.next/BUILD_ID`,
    so that the digest covers the server build that carries the client
    id as well as the client bundle that carries the origin). Then re-run
    the §B1 porcelain check: `next build` rewrites the tracked
    `frontend/next-env.d.ts` (§B1 — record its diff, which must show
    only the comment line; restore it — from `frontend/`:
    `git checkout -- next-env.d.ts`; re-check; record both porcelain
    results; any other change is a STOP).
  - Expected result: build succeeds; `.next/BUILD_ID` exists; the
    production API base URL is baked into the client bundle; the
    localhost default is absent; the server build carries exactly the
    recorded `FRONTEND_BUILD_SHOPIFY_CLIENT_ID` (`pages/_document.js`
    included; no other KNOWN client id — the published app's, or the
    rehearsal app's on any other deployment — anywhere under
    `.next/server/`). Note that `images: { unoptimized: true }`
    in `frontend/next.config.js` (A246, PR #147 — present in every
    revision at or after the §B floor) is read by the Next server from
    that file when `npm run start` launches it, not baked by this
    build: the running server then answers `/_next/image` with 404
    before any parameter validation or `sharp` call, and §G1g probes
    the RUNNING process for it — so §G1d must start the frontend from
    the pinned checkout, never serve a rebuilt bundle under an old
    process. A `GET /` returning 200 is NOT evidence of the bundle's API origin
    — the page serves regardless of which origin is compiled in.
  - STOP if: the built client bundle contains `localhost:8000`, or its
    compiled-in API base URL is anything other than the exact recorded
    `FRONTEND_BUILD_ORIGIN`; or any file under `.next/server/` carries
    another known Shopify client id, or `pages/_document.js` lacks the
    recorded `FRONTEND_BUILD_SHOPIFY_CLIENT_ID`. (The origin rule is about the API base URL
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
  a restored backup carries those rows (§N2). Record the worker's
  process definition verbatim (command including `--concurrency 1`,
  working directory, out/err log paths) in the same `environment/`
  record; the running value is re-proven at §G1e after every restart,
  and a restart that reverts it to the CPU count is a §G1d deviation
  and a STOP before §I14.

  | Service | Startup command (verified) | Health signal |
  |---|---|---|
  | Web (Django) | `gunicorn nxentra_backend.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120` | `GET /_health/live` → 200; `GET /_health/ready` → 200 (through the proxy, or on loopback with the `Host` + `X-Forwarded-Proto` headers — §G1f) |
  | Worker | `celery -A nxentra_backend worker -l INFO --concurrency 1` — one prefork process is the worker form this runbook prescribes for the pilot (founder decision D12, 2026-09-23, after rehearsal deviation D8; a deployment-topology rule, not a supported-product-contract change): `process_pending` has no mutual exclusion across connections, so a second worker process could run the same projection for the same company concurrently (an open hazard, review finding F-K), and the §I14 expectations (one contiguous release pass, entry numbers in ingest order) assume a single process. The checked-in `docker-compose.yml` says `--concurrency=2` and a process manager restarting from a saved definition silently reverts to the CPU count — §G1e proves the running value. | worker log shows ready; `/_health/alerts` not reporting missing consumers after first drain. Log streams (Celery re-homes the root logger onto STDERR after Django's logging setup; nothing in the settings disables it): loggers named in `ops/logging_config.py` (`projections`, `events`, `accounts`, `tenant`, `ops`, `nxentra.accounting.*`) keep their STDOUT JSON handler — the `projections.base` "terminally skipped" / deferred WARNING lines land there; every other application logger (`shopify_connector.*`: the `[A52]` lines, "Queued initial Shopify sync", partial-response warnings) propagates to the root and lands on STDERR in plain text (copy every A52 timestamp verbatim, never re-render; the window values of `initial_store_sync` and of the prescribed `sync_store_orders` task are aware `+00:00` strings); Celery's own "Task received/succeeded" lines are expected on NEITHER stream (the `celery` logger does not propagate and the re-homing empties its handlers — observed 2026-09-22 as zero such lines for the release execution). Read BOTH streams; the startup banner (`concurrency: 1 (prefork)`) is written to the process's original stdout — record which file holds it. |
  | Beat | `celery -A nxentra_backend beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler` | beat log ticking; scheduled tasks appear in worker log |
  | Broker | Redis reachable at `REDIS_URL` | `/_health/full` `redis` check |
  | Frontend | `npm run start` (Next.js, port 3000; the checked-in PM2 app name for the frontend is `nxentra-web`) | `GET /` on the frontend → 200 |
  | Reverse proxy + TLS | deployment-specific | https reaches frontend and API; §F2 restriction in force |

  Known, accepted quirk: `STATIC_ROOT` is not defined, so `collectstatic`
  cannot succeed (the backend Dockerfile deliberately ignores its failure);
  Django static assets are not part of this deployment's serving path.
- [ ] **G1e. Version, posture and database proof.**
  - Command / action: verify every running service is executing the §B
    revision (image tag / deployed checkout SHA per service); verify the
    worker runs ONE prefork process — the CURRENT start's banner line
    `concurrency: 1 (prefork)` in the worker log, or a read-only
    `celery -A nxentra_backend inspect stats` showing pool
    max-concurrency 1 — and that the process manager's saved arguments
    contain `--concurrency 1`; verify per process (web, worker, beat)
    the database name it is connected to equals the §C database (a
    process manager captures the environment at process creation, so a
    plain restart keeps an OLD `DATABASE_URL` — re-create the process
    or restart with the environment updated); verify the installed
    `django-celery-results` is ≥ 2.6.0 (`pip show django-celery-results`
    in the deployment's virtualenv) and that migration
    `django_celery_results 0012_taskresult_date_started` is applied
    (`python manage.py showmigrations django_celery_results`) — the
    §I intake contract reads `TaskResult.date_started`, which that
    version introduced. Record all four in
    `environment/`; the worker form is carried into the §N5 pack. (One
    prefork process removes only the Celery-side concurrency — the web
    workers still run synchronous drains on other connections; that
    cross-connection case is covered by the `ProjectionAppliedEvent`
    unique constraint and the A23 bounded retry, not by this setting.)
  - STOP if: **any service is running a different application
    revision**; the worker's effective concurrency is not 1; any
    process is connected to a database other than the §C database; or
    `django-celery-results` is older than 2.6.0 / its `0012` migration
    is unapplied.
- [ ] **G1f. Boot health.**
  - Command / action: `curl` `/_health/live`, `/_health/ready`,
    `/_health/full` (internal path), and run
    `python manage.py alert_check`. **Probe the backend on loopback the
    way the proxy does — send BOTH headers the proxy sets.** Production
    settings enforce `SECURE_SSL_REDIRECT = True` with
    `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`, and
    Django validates every request's `Host` against `ALLOWED_HOSTS` (in
    `SecurityMiddleware` before it builds the redirect target, and again
    in `CommonMiddleware`). A bare
    `curl http://127.0.0.1:<port>/_health/live` (curl sends
    `Host: 127.0.0.1:<port>`) therefore answers **301** (the redirect to
    https) when a loopback host is in this deployment's `ALLOWED_HOSTS`
    (§E3 record) and **400** (`DisallowedHost`) when it is not — either
    way the transport/host posture working, not an unhealthy service,
    and not the expected 200. The faithful loopback probe sends the
    `Host` the proxy forwards and the proto header:
    `curl -s -o /dev/null -w "%{http_code}\n" -H "Host: <DEPLOYMENT_HOST>" -H "X-Forwarded-Proto: https" http://127.0.0.1:<port>/_health/live`
    (same for `ready`; for `full` keep the body — `-o full.json` instead
    of `-o /dev/null` — because the failing check names live in it;
    `/_health/full` is externally refused by §F2, so it is probed here
    on loopback only). A 400 with both headers means `<DEPLOYMENT_HOST>`
    is not in `ALLOWED_HOSTS` — fix the §E3 value, never the probe.
  - Expected result: the bare probe 301 or 400, never 200; live/ready
    200 with both headers; full reports `healthy` (503 with any failing
    check names otherwise); `alert_check` prints the aggregate state
    and exits 0.
  - Evidence to retain (`environment/`): the four outputs (the `full`
    body included) plus the bare-probe status code, beside the §E3
    `ALLOWED_HOSTS` record that explains it.
  - STOP if: full health or `alert_check` is unhealthy at boot on an empty
    database — that indicates a mis-set environment, not merchant data;
    the bare loopback probe answers 200 (the §E transport posture is not
    in force); or the two-header probe answers anything but 200.
- [ ] **G1g. Image-optimizer route refusal at the proxy (per
  deployment, F2 shape).**
  - Why: GHSA-2xp9-vwfh-vxw4 (critical, published 2026-09-08) is an
    unauthenticated remote code execution in Next.js's Image
    Optimization API (`/_next/image`) via `libheif`/`sharp` when an
    AVIF is optimized; no Next 14.x patch exists (E11 tracks the Next
    15 upgrade; the CI allowlist entry expires 2026-11-30, §B1). The
    application uses no `next/image`, so the route served nothing but
    attack surface: every revision at or after the §B floor carries
    `images: { unoptimized: true }` in `frontend/next.config.js`
    (A246), which the Next server reads when it starts and then
    answers the route with 404 before any parameter validation or
    `sharp` call. The deployment's reverse proxy additionally
    refuses the route — defense in depth that needs no rebuild — and,
    exactly like §F2, that rule lives in the deployment (the
    repository ships no proxy configuration) and must be proven, not
    assumed.
  - Command / action: add a proxy rule answering `/_next/image` with
    404 (nginx: `location = /_next/image { return 404; }` above the
    frontend `location /` in the SERVED https server block), test and
    reload the proxy, then run three probes: from OUTSIDE the host
    `curl -s -o /dev/null -w "%{http_code}\n" "https://<DEPLOYMENT_HOST>/_next/image?url=%2Ffavicon-32x32.png&w=32&q=75"`;
    on the host against the frontend process directly (bypassing the
    proxy) the same path on `http://127.0.0.1:<frontend port>`; and the
    serving control
    `curl -s -o /dev/null -w "%{http_code}\n" https://<DEPLOYMENT_HOST>/login`.
  - Expected result: 404 from both image probes — the proxy's rule
    externally, and on loopback the RUNNING Next server's own
    `unoptimized` refusal (it reads `next.config.js` at start) — and
    200 from the control. A 200 image response on the loopback probe
    means the running frontend process was started from a checkout
    without `images.unoptimized` (a pre-floor working copy, or a
    process not restarted after the re-pin) — a §G1e version-proof
    failure for the frontend service; a 404 there says nothing about
    the bundle, which §G1c/§G1e prove separately.
  - Evidence to retain (`environment/`): the proxy rule, the proxy
    config-test output, the three probe codes ______.
  - STOP if: either image probe returns anything other than 404, or the
    control returns anything other than 200.

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
    exists; `onboarding_completed` is true;
    `FiscalPeriodConfig.current_period` equals the submitted
    `current_period` while **every `FiscalPeriod.is_current` flag is
    false** — `complete_onboarding` records the current period on the
    config row only (its `_create_periods` never sets the per-period
    flag); that flag is written solely by the `set_current_period`
    command's `FISCAL_PERIOD_CURRENT_SET` event, a
    `CURRENCY_FISCAL_CHANGE`-gated door under the active pilot. This is
    an observation, not a defect and not a STOP: preflight does not
    read `is_current`; do not add a `set_current_period` call to the
    bootstrap sequence to "repair" it (the sequence the 2026-09-08
    shakedown validated has none); **no** `ShopifyStore` exists;
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
    webhooks (subscribed app-wide by the app version the §E3 record
    names — they reach THIS host only because that app's subscription
    URI points at it) can also begin delivering immediately after
    connection.
    Store connection is therefore NOT inert — ingestion must be held
    until deliberately released. (`import_mode="skip"` suppresses only
    the onboarding historical-import request; it does NOT suppress the
    OAuth-triggered initial sync or its refund catch-up leg.)
  - Command / action, in order: (1) keep the web process available for
    exactly: Shopify OAuth install/callback, embedded session login,
    linking-nonce creation/redemption, authenticated configuration
    reads/writes, and preflight; (2) block EVERY route through which
    Shopify webhooks can reach Django at the reverse proxy, enumerated
    from the URL configuration at the executed revision — currently the
    dedicated endpoint (`/api/shopify/webhooks` and
    `/api/shopify/webhooks/`) AND the generic platform endpoint
    `/api/platforms/shopify/webhooks/`, a second live HMAC-verified
    Shopify ingress whose pilot gates block only the payout/dispute
    topics while order/refund/fulfillment processing stays in scope —
    each with a
    retryable non-success response (e.g. 503; never a discarding 200),
    and prevent accidental calls to interactive sync/resync endpoints
    during the hold; (3) stop Celery beat and prove it stopped;
    (4) inspect active/reserved/scheduled worker tasks, drain to empty
    and accounted-for, then stop the worker; (5) keep Redis/the broker
    AVAILABLE so the OAuth-triggered initial sync can be queued;
    (6) record an empty (or fully accounted-for) task-queue baseline.
  - Evidence to retain (`preflight/`): the route enumeration itself
    (every Shopify-capable webhook route found in the executed
    revision's URL configuration, with where each is registered),
    proxy rule proof covering every enumerated route, beat/worker
    stop proofs, queue baseline, timestamps, operator.
  - STOP if: a worker can consume tasks; beat can enqueue the periodic
    catch-up; a Shopify webhook can reach Django through ANY route
    (the generic platform endpoint included); an interactive sync
    endpoint remains usable; or the pre-OAuth task queue contains
    unexplained work.
- [ ] **I5. Connect exactly one SYNTHETIC development store —
  deliberately UNBOUND.**
  - Command / action: initiate the connection through the **top-level
    standalone Nxentra OAuth path**: `/shopify/settings` →
    `POST /api/shopify/install/` → complete OAuth for
    `<SYNTHETIC_DEV_SHOP_DOMAIN>` — on the app the §E3 record names
    (for the rehearsal, the dedicated `Nxentra Sync REHEARSAL` app;
    never the published app, whose webhooks would go to the live host
    and never arrive here). The development store is created in the
    same Partner organization with an obviously synthetic name, seeded
    only with Shopify's generated test data — nothing copied from any
    merchant; the synthetic orders and refunds the §I13/§J cases need
    are created in that store later; its `.myshopify.com` domain is
    PRIVATE evidence (§P — never in attachable evidence, never in Git,
    and never written into any Shopify CLI configuration file, tracked
    or founder-held, as `[build] dev_store_url`). Do **not** use the embedded
    token-exchange installation path for this step — it automatically
    creates a `ShopifyUserBinding` when it holds a Shopify `sub`, which
    would make the required unbound-state proof impossible. On a fresh
    database after a STOPPED window the app is already installed on the
    synthetic store: keep its Shopify Admin app tile UNOPENED until this
    step has passed (an embedded launch would run token exchange, create
    the store row AND a binding — the STOP below); do not uninstall it
    to reset (the `app/uninstalled` webhook is held by §I4 and the later
    `shop/redact` blanks credentials on every row matching the domain);
    the founder's explicit lift of any earlier window's "never re-run
    OAuth" rule is recorded at this step; the 8-hour stale-source clock
    (`SHOPIFY_SOURCE_STALE_SECONDS`, anchored on `ShopifyStore.created_at`
    while `last_sync_at` is null) starts at this Connect click, so
    §I5 → §I14 must fit one sitting; verify NTP first. The store
    MUST be a non-production Shopify development/test store containing
    only synthetic catalog and transaction data — no copied real
    customer/order/refund data — while still exercising real OAuth,
    App Bridge/session-token behavior, signed webhook delivery, and API
    synchronization.
  - Expected result: exactly one ACTIVE synthetic Shopify development
    store exists, installed on the §E3 app, and its stored
    `ShopifyStore.scopes` names exactly the §E3 scope set
    (comma-split, order-insensitive; for the rehearsal:
    `read_all_orders` absent) — record the raw string.
    **No active `ShopifyUserBinding` exists yet** for the
    synthetic Shopify user and store — this is deliberate: the
    immediately following I6/J0 ceremony must first prove the
    fail-closed `not_bound` state and then create the canonical binding.
    (The standalone OAuth path creates/activates the store but never a
    binding; only the embedded token-exchange path and the linking-nonce
    redemption create bindings.) The OAuth-triggered
    `initial_store_sync` enqueue is recorded in the private application
    log ("Queued initial Shopify sync for <shop>" — the line carries no
    task id; the id is read from the §I12 broker listing); the worker and beat
    remain stopped and webhook ingress remains blocked, so **the task is
    queued but cannot execute** and no automatic sync has run.
    **Enqueue-failure disposition:** OAuth success is NOT proof the task
    was queued — the helper swallows broker failures. If the log shows
    "Could not queue initial Shopify sync" or the enqueue result is
    uncertain: STOP before any release, keep worker/beat/webhooks
    blocked, repair the broker condition; do not rely silently on the
    periodic catch-up and do not blindly enqueue a second task while the
    first task's existence is uncertain. Any manual enqueue recovery
    command for the INITIAL sync must first be verified against live
    code, must return a recorded task id, and must be proven on the
    synthetic rehearsal before it may appear in this runbook (none
    does). One enqueue form is named elsewhere in this runbook — the
    explicit-window re-execution task `shopify.sync_store_orders` (the
    §I closure rule; the §I15 replacement execution) — it runs the
    orders leg only and is never a substitute for a failed or
    uncertain initial-sync enqueue — and §I5 grants
    it a CONTROLLED FIRST SYNTHETIC PROOF: on the synthetic rehearsal
    only, with the worker running (§I14 onward), enqueued once per
    recorded need and recorded as its own execution (task id at
    enqueue, TaskResult row, A52 line, §K row). That record is its
    proof; until it exists the form may not be used on the merchant
    path (§Q), and a merchant-path need arising before then is a STOP
    for founder decision.
  - STOP if: any active `ShopifyUserBinding` already exists before J0;
    the connect path automatically authenticates the embedded user; the
    store was connected through a path that bypasses the intended
    unbound state; more than one ACTIVE store exists; the domain is the
    first merchant's live store; the store contains real merchant
    catalog, customers, orders, or financial history; data was copied
    from the merchant merely to make the test realistic; the store was
    installed on the published app, or on any app whose webhook
    subscription, compliance, redirect or application URLs are not on
    `<DEPLOYMENT_HOST>` (§E3);
    or the granted scope set differs from the §E3 record.
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
     Each launch that reaches a successful `token-exchange/` call queues
     one further `initial_store_sync` (K counts them — §I13/§I14); the
     page's own reload after redemption is such a launch.
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
  link, and successful post-link embedded login (crop each to the
  Nxentra page: the Shopify admin address bar, browser tab title,
  sidebar, header account name, app-panel text and the Connected Store
  card all carry the shop domain — §P; never paste them into a chat or
  ticket); a redacted network
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
  Record the queue baseline from a read-only broker listing (queue
  length and each queued task's name and id in queue order — never a
  purge, §O): expected `initial_store_sync` × (1 + K) plus the
  projection tasks the J0 logins queued; the first `initial_store_sync`
  id is the §I14 release execution.

**Initial-intake contract — reusable definition** (used by I13, I14, the
§K controls, §Q and — for its three replacement terms — §I15; every
initial-sync window is computed at EXECUTION time, when the worker
starts the task — never at OAuth time; the one exception is
`REPLACEMENT_WINDOW`, which the operator declares in the §I15 addendum
BEFORE that execution and then verifies equal to its A52 line):

```
INITIAL_SYNC_STARTED_AT = the execution-time `now` the initial_store_sync
                          task computes when its store sync begins —
                          each of the 1 + K initial tasks computes its
                          OWN, recorded per execution; the unqualified
                          name denotes the release execution's value,
                          which the GO / I13 reconciliation binds; each
                          K re-execution applies the same seven-day and
                          ordering rules to its own A52 pair and its
                          own `date_started` (§I14, §Q step 12) —
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

TASK_RECEIVED_AT        = the task's `date_started` from its durable
                          `django_celery_results` TaskResult row, keyed
                          by task id (the result backend is django-db
                          and STARTED tracking is on, so the row is
                          first written at STARTED — `date_created` ≈
                          `date_started` — and `date_done` is the
                          finish; the enqueue time is recorded
                          nowhere; the field exists from
                          django-celery-results 2.6.0 — the pinned
                          floor, its migration
                          `django_celery_results.0012_taskresult_date_started`
                          applied by §C1 and verified at §G1e),
                          corroborated by the `[A52]
                          _sync_orders start` line that follows within
                          seconds (Celery's own received/started lines
                          reach neither worker stream on this logging
                          configuration — §G1d; recorded separately;
                          used only for ordering)

ORDER_CREATED_WINDOW =
    [INITIAL_SYNC_STARTED_AT − 7 days, INITIAL_SYNC_STARTED_AT]
    = the [created_at_min, created_at_max] pair of that log line

REFUND_CANDIDATE_UPDATED_WINDOW =
    [INITIAL_SYNC_STARTED_AT − 7 days, INITIAL_SYNC_STARTED_AT]
    = the SAME pair by construction (`_sync_store` passes one
      min_date/max_date to both legs; the refund leg logs no window
      of its own)

A = eligible Shopify orders whose created_at is in ORDER_CREATED_WINDOW
    (computed per execution from that execution's window)

B = orders currently refunded or partially_refunded whose Shopify
    order.updated_at is in REFUND_CANDIDATE_UPDATED_WINDOW (computed
    per execution from that execution's window; Shopify evaluates it
    against the order's updated_at AT THE READ — a value the system
    neither requests nor stores and a later edit moves — so B_k is
    never reconstructed after the fact: its durable evidence is the
    refund leg's `scanned` (the size of B_k at the read) and the
    parents that leg DISPATCHED — those it booked or promoted, and
    those it dispatched but failed to book, each named by Shopify
    order id in that execution's `[A159] Could not book parent order
    …` / `[A159] Parent-order booking failed for …` WARNING line on
    the worker stream (transcribe the order id only; the line carries
    the shop domain) — every one of which was in B_k because only a
    selected order is dispatched; a dispatched-and-failed parent
    therefore stays INSIDE AUTHORIZED_PARENT_ORDER_SET for the
    recorded re-execution that closes it (§I closure rule), so that
    closure never books outside the set; the only selected orders
    that neither book nor leave such a line are `pilot_scope_skipped`
    (must be 0) and an id-less payload (a STOP already); A_k is
    reconstructed from the orders export, Shopify created_at never
    changing)

AUTHORIZED_PARENT_ORDER_SET = the union, over the 1 + K initial tasks, of
                          each execution's own A union B, deduplicated
                          by Shopify order id — a record that newly
                          qualifies in the gap between consecutive
                          executions' INITIAL_SYNC_STARTED_AT (the
                          windows themselves overlap: each reaches
                          seven days back from its own start) is
                          authorized for the execution that first
                          selects it (on the synthetic store the K
                          later sets contribute no new record)

REPLACEMENT_ORDER_SET   = (§I15 lapsed-retry path only) the synthetic
                          orders created under the re-verified webhook
                          block (§I4 items 1–2, beat still stopped, the
                          worker running since §I14)
                          AFTER the LAST of the 1 + K executions'
                          INITIAL_SYNC_STARTED_AT (an order created
                          between two of them lies inside the later
                          one's ORDER_CREATED_WINDOW), listed by
                          Shopify order id in a dated replacement
                          addendum signed BEFORE the replacement
                          execution; outside every execution's
                          ORDER_CREATED_WINDOW and therefore outside
                          AUTHORIZED_PARENT_ORDER_SET by construction;
                          contains no cancelled order of any kind (a
                          never-captured one counts in `skipped` and
                          in `cancelled_no_effect_skipped`; a
                          captured-money one books through the paid
                          writer and counts in `cancelled_financial_*`
                          — either breaks the `cancelled_*` = 0
                          clause; the cancelled path is exercised at
                          §I14 and §J, not here), and every member
                          is paid at creation (Shopify
                          `financial_status` paid, partially_paid,
                          refunded or partially_refunded — a pending
                          order books no journal and proves nothing
                          here)

REPLACEMENT_WINDOW      = that execution's [created_at_min,
                          created_at_max] task arguments = [T0, T1],
                          T0 a UTC timestamp read on the deployment
                          host directly in the required form
                          (`date -u +%Y-%m-%dT%H:%M:%S+00:00` — never
                          hand-rendered from a bare `date`) at least one
                          minute BEFORE the first replacement order is
                          created and T1 one read at least one minute
                          AFTER the last (the margin absorbs clock
                          skew against Shopify; no Shopify `created_at`
                          needs to be read to the second); the
                          synthetic store receives no other order
                          creation in [T0, T1] (the operator controls
                          it), so the window contains exactly
                          REPLACEMENT_ORDER_SET; both
                          bounds recorded and passed as
                          YYYY-MM-DDTHH:MM:SS+00:00 (aware UTC, second
                          precision, no Z, no date-only);
                          must equal, byte for byte, the
                          created_at_min / created_at_max of its own
                          `[A52] _sync_orders start` line (the task
                          passes the strings through verbatim)

REPLACEMENT_EXECUTION_STARTED_AT
                        = that execution's TaskResult `date_started`
                          (the task-form twin of TASK_RECEIVED_AT);
                          the ordering rule for this execution is
                          I15_REPLACEMENT_SIGNOFF_TIMESTAMP <
                          REPLACEMENT_EXECUTION_STARTED_AT

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
ONE rule — the same rule, applied to each execution's own values —
wherever it is applied to an
`initial_store_sync` execution (the release execution and the K
explained re-executions; an explicit-window execution — a closure
re-execution or the §I15 replacement execution — is reconciled instead
to its declared window, which is by construction not seven days):
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
   live code has three callers of the enqueue helper
   (`shopify_connector/commands.py` `_schedule_initial_sync`): the
   standalone OAuth completion (`complete_oauth` — the §I5 door), the
   Shopify-initiated pending-install finalize
   (`finalize_shopify_install` — never exercised inside the window;
   the store is installed through §I5 only) and the embedded token
   exchange (`complete_oauth_token_exchange` — unconditional on EVERY
   successful `token-exchange/` call, so each embedded launch after
   the J0 binding that reaches token exchange queues one further
   `initial_store_sync`; rehearsed 2026-09-22, K = 2). None is an
   operator command and none may be used as one; any OPERATOR
   enqueue-recovery command must be verified against live code,
   return a recorded task id, and be proven on the synthetic rehearsal
   before it may appear here (the I5 rule; its one exception is the
   controlled first synthetic proof §I5 grants the re-execution task
   below). The
   A-leg re-execution that DOES take an explicit `created_at` window
   is the worker task `shopify.sync_store_orders` with explicit
   `created_at_min` / `created_at_max` — the evidence-bearing form:
   the task returns the complete orders-leg result (`status`,
   `fetched`, `created`, `skipped`, `errors`, `cogs_fulfillments`,
   `refunds_backfilled`, `pilot_scope_skipped` and the four
   `cancelled_*` counters) and the django-db result backend stores it
   in the task's TaskResult row beside its `date_started`; its A52
   lines land on the worker's streams (§G1d). Enqueue it from
   `backend/` with the deployment `.env` in place (operator action;
   `<store_id>` is the `store_id` printed on the §I14 A52 start line —
   private, never in attachable evidence; both timestamps as ISO
   strings with an explicit `+00:00` offset, which the task passes
   verbatim to its A52 start line):

   ```bash
   python manage.py shell -c "
   from shopify_connector.tasks import sync_shopify_store_orders
   r = sync_shopify_store_orders.delay(store_id=<store_id>, created_at_min='<ISO>', created_at_max='<ISO>')
   print(r.id)"
   ```

   Record the printed task id at once. This enqueue form is verified
   against live code at this revision (the task's signature and
   return value; it is the same call the application's own import
   path makes); §I5 grants it a controlled first synthetic proof — its
   first recorded use on the synthetic rehearsal, with the worker
   running, is that proof — and on the merchant path it may be used
   only after such a recorded synthetic proof exists; otherwise the
   closure is a STOP for founder decision, never an improvised run.
   Read the execution's TaskResult row (read-only, same environment):

   ```bash
   python manage.py shell -c "
   from django_celery_results.models import TaskResult
   r = TaskResult.objects.get(task_id='<task id>')
   print(r.status, r.date_started, r.date_done); print(r.result)"
   ```

   Run it after the worker's `[A52] _sync_orders done` line: `r.status`
   must read `SUCCESS` (the row is written only when the task starts —
   `CELERY_TASK_TRACK_STARTED` — so a `DoesNotExist` or `STARTED` read
   is early, not absent: read again); the leg's `status` "ok" is a key
   inside `r.result`, distinct from `r.status`.
   The CLI `python manage.py resync_shopify_orders --company <slug>
   --from <ISO> --to <ISO>` runs the same orders leg but prints only
   `fetched`, `created`, `skipped` and `errors` (its A52 done line
   adds `pilot_scope_skipped`), re-renders the given timestamps, writes
   its A52 lines to its own process output rather than the worker's,
   and DISCARDS the rest of the result, so it cannot carry the §I
   inequality or the refund counters and is NOT an evidence-bearing
   form for any execution whose counters enter the §K pack. With any
   window the order leg routes a cancelled
   or refunded order through the paid writer, the stamp and the refund
   backfill, so it can close a candidate whose creation date the
   operator names. It books EVERY order created in the named window,
   so the window MUST be the narrowest that re-selects the candidate,
   the re-execution is recorded as an explained second execution (its
   own task id and TaskResult row, its own `[A52] _sync_orders start`
   line, counters and inequality, in the §K pack), and any order it
   books outside AUTHORIZED_PARENT_ORDER_SET is an intake-contract
   variance and a STOP. (The §I15 lapsed-retry path runs the same
   worker task for orders created AFTER the LAST of the 1 + K
   executions' `INITIAL_SYNC_STARTED_AT`; that is not a closure
   re-execution — it is authorized by its own signed
   `REPLACEMENT_ORDER_SET`, §I15.)
   The interactive resync endpoint is NOT a
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

Since PR #152 (`17dd1a9`) a projection pass is non-re-entrant per
(projection, company): a command invoked from inside a handler still
drains every OTHER projection synchronously but never the projection
whose handler is running, so within one pass an order's event is fully
settled — posted, deferred or quarantined — before its refund is
attempted. Operator-visible consequence: a refund that reaches the
worker in the same pass as its order posts in that pass with ascending
entry numbers; events of a type that a projection's own handler
commands emit would wait for the next pass instead of posting in the
same one (no projection does this today — a code-maintenance item,
NEXT_TASKS A175).

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

**Nested-collection completeness fix on record (PR #143):** merge
`2be1819e176399956ae509ec28a43729318d881a`; reviewed head
`7de32798fc3b937e09d010bba7fbcf2268797afe`; main CI 33919019309 —
success. Before this fix the order queries selected each order's line
items as `lineItems(first: 50)` with no pagination and no `pageInfo`
(an order with more than 50 line items yielded exactly the first 50
with no warning, counter, or error anywhere), and the product sync
capped `variants(first: 60)`, logging a private worker warning while
dropping every variant past the first page. Since PR #143 both nested
connections are cursor-drained to exhaustion with per-parent overflow
queries; any pagination anomaly — invalid shape, repeated cursor,
hasNextPage without endCursor, parent vanishing mid-fetch — raises
`ShopifyGraphQLIncomplete` (the PR #139 fail-loud contract); the
overflow reads never use allow_partial; an absent `lineItems`
connection on an order node fails loudly instead of yielding empty
evidence. The caps never changed an invoice or journal amount — the
Shopify invoice is built from order-level totals, never by summing
line items — what they truncated was the stored order payload's
`line_items` evidence, NON_STOCK item auto-provisioning beyond the
first 50 lines' SKUs, and the product mirror / variant catalog beyond
60, on the GraphQL sync paths only (webhook payloads always carried
the complete list). These caps CANNOT be in force at any execution of
this runbook: §B requires the executed revision to contain PR #143 and
this merged runbook (which postdates it), so every eligible revision
carries the fix — the interim merchant-shape precondition and its cap
controls, drafted and retired inside this document's own review cycle,
therefore do not appear here. One same-class residual stays on
record: the per-order fulfillment query remains capped
(`FULFILLMENTS_PER_ORDER = 10`, `FULFILLMENT_LINE_ITEMS = 50`) — its
output is non-financial under the pilot (the mechanical side effects
above; NON_STOCK, no COGS booking) and a capped fulfillment list must
never be read as complete COGS evidence. Separately, as a safeguard
over the FIXED reads (not a residual), the §K control pack keeps an
export-vs-evidence per-order line-item count comparison as an
independent completeness control.

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
  refunds), never skipped. This sign-off authorizes the contract at
  `INTAKE_CONTRACT_VERSION` = <the §B runbook revision SHA> (the
  rehearsal twin of the GO record's "version <n>"). Like the §Q GO
  record, it authorizes the FUTURE execution-relative contract: the
  worker is stopped at signing, so `INITIAL_SYNC_STARTED_AT` and the
  effective window boundaries do not exist yet and no VALUE for them
  may be recorded here — they are captured at I14 and reconciled to
  this authorization there.
  The sign-off pre-declares the initial tasks the release will consume:
  `INITIAL_TASKS_QUEUED` = 1 + K, with the task ids in queue order from
  the §I12 read-only broker listing (the first = the §I5 enqueue = the
  release execution) and K = the number of successful embedded
  `token-exchange/` calls since §I5 (each queues one task —
  `complete_oauth_token_exchange`; counted from the proxy access log
  and cross-checked against the broker count). No embedded launch may
  occur between signing and the §I14 worker start; if one does,
  re-sign. It also names every Shopify order the execution-time
  windows will select — including orders left in the synthetic store
  by an earlier STOPPED window while they still fall inside the
  seven-day windows — records that nobody edits those orders in
  Shopify Admin (an edit bumps `updated_at` and re-qualifies them for
  set B), and records the planned §I15 unblock time (≤ ~4 h after the
  first synthetic order whose retries the §I15 proof relies on). It
  does not pre-authorize the §I15 lapsed-retry replacement execution:
  that path needs its own dated replacement addendum, signed before
  the execution (§I15).
  Sign-off: operator ______ `I13_SIGNOFF_TIMESTAMP` (UTC, to the
  second — the rehearsal twin of `GO_TIMESTAMP`, ordered against
  `TASK_RECEIVED_AT` at I14) ______
- [ ] **I14. Controlled initial-sync release — worker only. Verify the
  authorized intake CONTRACT, not a simple window.** Start the Celery
  worker ONLY; keep beat stopped and webhooks blocked. Observe the
  queued `shopify.initial_store_sync` task: record its task id (from
  the §I12 queue baseline and its durable `django_celery_results`
  TaskResult row — Celery's own receive/start lines reach neither
  worker stream, §G1d), its start/finish timestamps, and its complete result
  (privately) — and export each evidence TaskResult row — every one of
  the 1 + K initial-task rows (the release execution AND the K
  pre-declared re-executions whose per-window reconciliation lives in
  their complete results), every closure re-execution row and the §I15
  replacement row — (`task_id`, `status`, `date_started`, `date_done`,
  `result`) into `preflight/`
  BEFORE §I16: once beat runs, the DatabaseScheduler-installed
  `celery.backend_cleanup` (04:00 UTC daily — `CELERY_TIMEZONE` is
  UTC) deletes TaskResult rows older than `result_expires`, Celery's
  default 24 h (the settings set no override); from §I16 on these
  exports — not the live rows — are the §K source; require exactly
  1 + K initial
  tasks consumed on release,
  where K is the number of successful embedded `token-exchange/` calls
  after the J0 binding that the I13 sign-off pre-declared (each queues
  one task through `complete_oauth_token_exchange`; session-login-only
  launches queue none): the FIRST in queue order — the §I5 enqueue,
  its id recorded in the §I12 queue baseline — is the release
  execution; the other K are explained re-executions, each reconciled
  against its OWN execution-time window (its own A52 pair; the
  seven-day rule; `I13_SIGNOFF_TIMESTAMP < its date_started ≤ its
  INITIAL_SYNC_STARTED_AT`), that may add exactly what newly qualified
  since its predecessor, per record class. Two legs write orders and
  refunds, and their counters differ: the orders leg fetches by
  Shopify `created_at` inside its window W (that execution's A52
  pair, evaluated by Shopify at second precision) and counts every
  dispatch that did something in `created` — a new row, an in-place
  promotion, an in-place cancellation disposition — and every benign
  no-op in `skipped`; the refund leg selects by the CURRENT order
  `updated_at` inside the same pair, books an unbooked parent through
  the paid writer BEFORE its backfill and counts NO parent at all
  (`scanned` counts every selected order, `refunds_created` the
  refunds it booked); the products leg (no window) writes `Item`
  rows and `ShopifyProduct` mappings — no order or refund row, no
  event. A row's Shopify `shopify_created_at` tells the order legs
  apart afterwards: inside W the orders leg wrote or dispatched it,
  outside W the refund leg did — except an inside-W order whose
  orders-leg paid-writer dispatch FAILED (its `Failed to process
  order …` / `Error processing order …` line; a stamp or backfill
  line only follows a committed booking) and which the refund leg
  then booked. Five classes, each with its read-only identification
  and its evidence:
  (1) a parent order — a new `ShopifyOrder` row (local `created_at`
  in [its `date_started`, its `date_done`]): an A record
  (`shopify_created_at` inside W) booked by the orders leg — a
  `pending` one as a PENDING_CAPTURE stub that books no journal —
  explained by its Shopify `created_at` (orders export) inside
  (predecessor's `INITIAL_SYNC_STARTED_AT`, own
  `INITIAL_SYNC_STARTED_AT`] — or, when that `created_at` lies at
  most 60 s at or before the predecessor's `INITIAL_SYNC_STARTED_AT`,
  as a boundary record of the predecessor's read (the pair is
  computed before the page read, Shopify stores `created_at` to the
  second and documents no freshness guarantee for the search-backed
  orders read), admitted only when no earlier execution's worker
  stream carries a line naming that order id and the row is new in
  this span, and recorded with the order id, its `created_at` and its
  distance in seconds — older than 60 s is a STOP; or an order the
  predecessor fetched in an unrouted status (voided, expired,
  unknown — one of its `skipped`, no row) that moved to a paid-writer
  or `pending` status in the gap, explained by that transition in the
  order's Admin timeline inside (predecessor's
  `INITIAL_SYNC_STARTED_AT`, own `date_done`] (it moves the
  predecessor's `skipped` against this execution's `created` by one:
  a named residual); or a B-only record (`shopify_created_at`
  outside W) booked by the refund leg, explained by the in-gap refund
  or edit that put its `updated_at` inside the pair
  (transaction-history file / Admin timeline — order `updated_at` is
  in neither the stored payload nor the orders CSV and is never read
  from the system); a parent first booked already refunded brings its
  complete refund history with it (refund rows of any date, explained
  by the parent);
  (2) a gap refund on an already-booked parent — a new `ShopifyRefund`
  row: by the orders leg's backfill when that leg re-fetches the
  parent (`shopify_created_at` inside W) already refunded, by the
  refund leg when the parent lies outside W and its current
  `updated_at` is inside the pair (a refund issued after
  `created_at_max` moves `updated_at` past the pair, so that leg does
  not SELECT an order for it — but its backfill reads the order's
  complete refund list moments after the page read, so a refund
  landing in between is booked too); the two legs never book one
  refund twice; explained by the refund's Shopify timestamp
  (transaction-history file / Admin timeline) inside (predecessor's
  `INITIAL_SYNC_STARTED_AT`, own `date_done`] — an older refund only
  when the record says why no earlier execution booked it: its
  parent's `updated_at` first entered the pair in this execution
  (an in-gap edit); its parent's `financial_status` first became
  refunded or partially_refunded in the gap (its first money refund,
  inside the bound) — the backfill then books every earlier Refund
  object of that parent, restock-only and zero-amount ones included,
  because no leg reads a paid parent's refund list; or a counted
  backfill error of an earlier execution (the §I closure rule);
  (3) a PENDING_CAPTURE stub promoted in place — a `pending` (COD)
  order booked as a stub by an earlier execution (class 1 then),
  re-fetched as paid and promoted by the paid writer: NO new
  `ShopifyOrder` row (its auto-created Items and mappings, if any,
  are class-5 rows explained by this order); identified as the
  `ShopifyOrder` row whose local `created_at` lies BEFORE this
  execution's `date_started` and whose `event_id` names a
  `BusinessEvent` whose `recorded_at` lies in [its `date_started`,
  its `date_done`]; by the orders leg when `shopify_created_at` is
  inside W, by the refund leg when it is outside; explained by the
  transaction — payment, partial payment or authorization — that
  moved Shopify's `financial_status` to a paid-writer status (paid,
  authorized, partially_paid, refunded, partially_refunded; for COD
  the merchant's mark-as-paid) in the order's Admin timeline
  — inside (predecessor's `INITIAL_SYNC_STARTED_AT`, own `date_done`]
  when the orders leg promoted it; when the refund leg did, the
  payment may be older and the in-pair `updated_at` (the refund or
  edit that selected the order) is the explanation;
  (4) a never-captured cancellation dispositioned in place — an order
  inside W that Shopify reports cancelled with a never-captured
  `financial_status` (authorized, voided, pending, expired, unknown)
  while a local row exists: a stub, which the cancellation writer
  flips to CANCELLED the first time and re-stamps on every later
  execution, or a BOOKED row — an order booked while `authorized`
  and voided since — whose `raw_payload` the writer re-stamps; NO
  new row, NO event, NO journal, yet counted in `created` and in
  `cancelled_no_effect_skipped` — one without a local row counts in
  `skipped` and in `cancelled_no_effect_skipped` instead; identified
  from the orders export, per execution: the cancelled never-captured
  orders with Shopify `created_at` inside W and `cancelled_at` before
  this execution's `date_started` that have a local row (any status);
  one whose `cancelled_at` lies in [its `date_started`, its
  `date_done`] was cancelled during the run — it is explained by that
  `cancelled_at` either way; record it as such and let its counter
  effect fall into the residual below; explained by that
  `cancelled_at`: inside (predecessor's `INITIAL_SYNC_STARTED_AT`, own
  `date_started`] for the first disposition, earlier for a re-stamp
  (explained by its earlier one); (4b) a captured-money cancellation
  stamped in place — a cancelled order whose money was captured
  (paid, partially_paid, refunded, partially_refunded), inside W or
  selected by the refund leg, whose local row is already booked: the
  paid writer is a no-op (`skipped`), the cancellation writer stamps
  `cancelled_at` into `raw_payload`, and the leg counts it in
  `cancelled_financial_candidates` / `cancelled_financial_processed`
  — no row, no event, no journal, never `created`; identified from
  the orders export per execution (cancelled orders with a
  captured-money status inside W, or selected by the refund leg, that
  have a booked local row); explained by that `cancelled_at`;
  (5) a product — a new `ShopifyProduct` mapping row (local
  `created_at` in [its `date_started`, its `date_done`]): the paid
  writer's when a worker INFO line of the paid writer's form
  `Auto-created Item <code> (<title>) cost=… (inventory=…, cogs=…)`
  naming its Item code lies in the execution's span — the paid writer
  auto-creates an Item and a mapping for a line whose Item code (the
  SKU, else a synthetic code from the variant or product id) is
  unknown and was prepared before the admission lock; it skips a
  line with no identifiable handle, one whose code already exists,
  and a second variant-less line (at most one variant-id-0 mapping
  exists per company), which leaves a `Failed to auto-create Item
  <code>: …` WARNING, no Item and no mapping while the order still
  books — expected, record the order id beside the warning; the
  products leg's own lines read `… with cost=…, accounts: …` —
  explained by the order that line's booking belongs to (a
  paid-writer line whose booking then failed, its `Failed to process
  order …` line following, left no row: a named residual);
  every other new mapping is the products leg's (counted in products
  `created` or `linked`), explained by construction, not by a
  timestamp: the products leg has no window and reads the whole
  catalog on every execution, so a mapping it first creates belongs
  to a variant that did not exist, or carried no SKU, at the
  predecessor's read — PROVIDED the predecessor's products leg
  completed on its counter shape (`created` / `linked` / `updated` /
  `skipped` / `errors`, no `status` key; its `unavailable` and `error`
  shapes leave the addition unexplained: STOP; an execution whose OWN
  products leg ended on a non-counter shape cannot be reconciled for
  this class: STOP); no Shopify product timestamp exists in the
  system (the product query requests no product or variant timestamp
  and a mapping's `raw_data` holds at most the variant snapshot), and
  the leg books no journal;
  or a re-selection that CLOSES a counted error of an earlier
  execution, recorded per the §I closure rule (a pre-declared
  re-execution may be that closer); anything else a STOP. The STOP
  criterion is the rows and in-place changes: every new
  `ShopifyOrder` / `ShopifyRefund` / `ShopifyProduct` row with local
  `created_at` in [its `date_started`, its `date_done`], every
  `BusinessEvent` recorded in that span, and every class-4
  disposition, is explained by its class as above — one that is not
  is a STOP (single worker, webhooks blocked, beat stopped). The
  counters corroborate and never decide (founder decision D14 (B),
  2026-09-24): normally orders leg `created` = class-1 A rows +
  orders-leg class-3 promotions + class-4 dispositions, orders leg
  `refunds_backfilled` + refund leg `refunds_created` = every new
  `ShopifyRefund` row, and products `created + linked` + the paid
  writer's `Auto-created Item … cost=… (inventory=…, cogs=…)` lines
  in the execution's span of the worker stream (from its A52 start
  line to the next task's A52 start line or the end of the stream;
  one prefork process) = every new `ShopifyProduct` row; the legs'
  post-commit error paths (stamp, fulfillment and refund backfills —
  they leave the booking, its mapping and its line in place), an
  in-execution closure by the refund leg and a same-second boundary
  can each move a counter by a small count — record any residual with
  its explanation in the execution's row; a residual that no row,
  line or warning explains is a STOP. The exact merchant-path counter
  algebra belongs to the merchant-cutover document of the post-G1
  runbook split, not to this revision. Record every addition — the
  new rows of classes 1, 2 and 5 and the in-place rows of classes 3
  and 4 — by identity (Shopify order id, refund id,
  `shopify_product_id` / `shopify_variant_id`; numeric ids only — for
  an auto-created mapping whose variant id is unknown, stored as 0,
  the Item code and the booking order's id) with the execution's row
  in the control pack. On the synthetic
  store, where nothing is created or edited between executions, that
  is zero additions and the counters are exact: orders leg
  `created 0`, `skipped` equal to
  `fetched`, `errors 0`, `refunds_backfilled 0`,
  `pilot_scope_skipped 0`; refund leg `refunds_created 0`, `errors 0`,
  `fetch_failures 0`, `pilot_scope_skipped 0`; products `created 0`,
  `linked 0`, `updated` equal to the release execution's products
  `created + linked + updated`, `skipped` equal to the release
  execution's `skipped`. Their ids equal the pre-declared ids in
  order. Any
  other re-execution used for §I closure is a separately recorded,
  explained execution with its own task id and TaskResult row (the
  worker-task form of the §I closure rule — the CLI prints four
  counters and discards the rest), A52 line and counters, and any
  UNEXPLAINED initial task is a STOP.
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
  row's `date_started` (the earliest timestamp the row carries — the
  enqueue time is recorded nowhere; Celery's received/started lines
  reach neither worker stream on this logging configuration, §G1d),
  corroborated by the `[A52] _sync_orders start` line that must follow
  it within seconds. This is the FIRST point at which these values exist (they never
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
  - `AUTHORIZED_PARENT_ORDER_SET` — the union over the 1 + K
    executions of each one's own A_k ∪ B_k — is deduplicated by
    Shopify order id and reconciled from durable identities, NEVER
    by summing leg counters: each A_k from the orders export (Shopify
    `created_at` inside that execution's pair — the windows overlap,
    so one order may sit in several A_k; its row is ADDED by exactly
    one execution, normally the first that selects it, after a
    counted error its closer, and every later execution that
    re-selects it sees a `skipped` no-op or an in-place change of its
    §I14 class, never a second row), each B_k by its size (that
    execution's refund-leg `scanned`) and by the parents that leg
    dispatched — booked or promoted, and dispatched-and-failed by its
    `[A159] Could not book parent order …` / `[A159] Parent-order
    booking failed for …` WARNING lines, order id only (§I definition
    — the selected set itself is not reconstructed) — so the union is
    evaluated as the union of the A_k plus every parent a refund leg
    of the 1 + K dispatched; every parent row booked by the 1 + K
    executions and their recorded closure re-executions must be in
    it (a closure's booking of a previously failed selected parent
    included); an overlapping
    A/B order legitimately appears in both legs' counters (`fetched`
    and `scanned`, and — when cancelled — in both legs'
    `cancelled_financial_candidates`);
  - the §K per-order line-item completeness control holds: every
    intake order's line-item count from the Shopify export equals the
    stored order evidence's `line_items` count (the PR #143 drained
    reads return the complete list and the stored list is unfiltered,
    so equality is exact — an independent control over the fix, not a
    cap check);
  - all source and financial effects reconcile to the authorized
    intake contract — not merely to source timestamps within seven
    days.
  Record: `OLDEST_IMPORTED_PARENT_ORDER_CREATED_AT`,
  `OLDEST_IMPORTED_REFUND_CREATED_AT`,
  `NEWEST_IMPORTED_REFUND_CREATED_AT`, `CANDIDATE_ORDER_COUNT_A` /
  `_B` per execution (A_k from the export, B_k = that execution's
  refund-leg `scanned`, for each of the 1 + K initial tasks),
  `CANDIDATE_ORDER_UNION_COUNT` (= |`AUTHORIZED_PARENT_ORDER_SET`|,
  evaluated as the union of the A_k plus every parent a refund leg
  dispatched — booked, promoted or dispatched-and-failed — over the
  1 + K executions),
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
  no leg keys or the task raised before any leg; an `initial_store_sync`
  execution's A52 start line is absent, or the recorded windows do not
  match its `created_at_min`/`created_at_max`, or its
  `created_at_max − created_at_min ≠ 7 days` (an explicit-window
  closure re-execution is reconciled to its declared window instead),
  or
  `I13_SIGNOFF_TIMESTAMP < TASK_RECEIVED_AT ≤ INITIAL_SYNC_STARTED_AT`
  fails; a closure re-execution booked an order outside
  AUTHORIZED_PARENT_ORDER_SET; the A_k (from the export), the
  refund-leg `scanned` counts and the union (the A_k plus every
  refund-leg-dispatched parent = AUTHORIZED_PARENT_ORDER_SET) cannot be
  reconciled; an older imported parent/refund is omitted
  merely to preserve a seven-day timestamp narrative; or a per-order
  line-item count differs between the export and the stored order
  evidence (the §K completeness control over the PR #143 drained
  reads).
  STOP also if, after the release drain is complete (queue 0 / unacked
  0, `/_health/alerts` `total_lag 0`): any `ProjectionFailureLog` row
  is unresolved or `/_health/alerts` is not 200 (the release must
  leave health stable — §I16 cannot open on a 503; distinguish a
  CONSUMED failure — applied marker present, `total_lag 0`,
  `errored_consumers 0`, `occurrences 1`, unchanged by later passes —
  from a RETRYABLE one — lag or errored consumers nonzero, self-heals
  on the next pass; only the former is unhealable in-window); any
  refund with a nonzero amount has no POSTED credit note (one posted
  credit note per nonzero `ShopifyRefund`); or journal entries are not
  in ingest order — the pass condition is that journal-entry ids,
  `entry_number`s and their source events' company sequence numbers
  ascend together and `posted_at` is non-decreasing (a partial
  inversion also fails; entry numbers descending against ids was the
  fingerprint of a nested projection drain — the 2026-09-22 rehearsal
  STOP, fixed by PR #152 `17dd1a9`). A consumed refund cannot be
  healed in-window: re-sync dedups on the existing row, resolving the
  failure row edits only the log, rebuild is pilot-blocked and direct
  repair is an §O abort — the disposition is a code fix, a new §B pin
  and a fresh database (§C).
- [ ] **I15. Webhook release and retry reconciliation.** Unblock the
  Shopify webhook routes — every I4-enumerated endpoint, the generic
  platform endpoint included — while beat remains stopped. Allow queued
  Shopify retries to arrive; verify idempotency — duplicate deliveries
  create no duplicate financial effects; account for the webhook
  backlog.
  Retry clock (Shopify documentation as read 2026-09-24: 8 retries over
  ~4 hours after the FIRST delivery attempt; after 8 consecutive
  failures a subscription is deleted only if it was created through the
  Admin API — the §E3 app declares its subscriptions in its app version,
  so that rule is not expected to apply: an inference, re-confirmed at
  §E3): a synthetic order's deliveries are first attempted at its
  creation under the §I4 hold, so the routes must be unblocked no later
  than ~4 hours after the FIRST synthetic order whose retries this proof
  relies on — plan §I13 → §I14 → §I15 inside one sitting and reach the
  §I14 read-back verdict with margin (by about three hours after that
  first synthetic order). This clock is
  separate from the 8-hour stale-source clock that starts at the first
  §I5 Connect click and is defused by the §I14 first sync. Evidence
  (`preflight/`): per topic, the count of retried deliveries answered
  200 after unblocking (proxy log — statuses and timestamps only);
  `ShopifyOrder`, `ShopifyRefund` and `JournalEntry` counts unchanged by
  those deliveries; 0 new `ProjectionFailureLog` or
  `ShopifyRejectedEvidence` rows; `/_health/alerts` 200. If the clock
  was missed, record the backlog as LAPSED. Neither §I13 (a sign-off)
  nor §I14 (the consumption of already-queued sync tasks) creates a
  webhook delivery, so repeating them produces nothing for Shopify to
  retry: to exercise this proof the path restarts at the synthetic
  ORDER CREATION under the webhook block (new orders and refunds in the
  synthetic store, whose deliveries are then held and retried — §I4
  items 1–2 must be re-established (if the routes were already opened)
  and re-verified in force before they are created, with
  beat still stopped; the worker keeps running, as it has since §I14,
  because the replacement execution below runs in it), then —
  because every 1 + K initial task was consumed at §I14 and creating
  an order enqueues no sync — a RECORDED replacement execution that
  books the replacement orders BEFORE the routes open. It is NOT a §I
  closure re-execution: every replacement order is created after the
  LAST of the 1 + K executions' `INITIAL_SYNC_STARTED_AT`, so it lies
  outside every execution's `ORDER_CREATED_WINDOW` and therefore
  outside `AUTHORIZED_PARENT_ORDER_SET` by construction,
  and a closure re-execution that books outside that set is a STOP
  (§I14, §O). It is authorized by its own signed set instead: BEFORE
  the task is enqueued, sign a dated replacement addendum in
  `signoff/` (the I13 twin in authority, not in form: its window is
  declared here in advance because this execution takes an explicit
  window — the §O bullet against pre-recorded window values binds
  the GO record and the I13 sign-off, whose windows are
  execution-relative; this addendum is neither, and its window is
  declared, not effective; the I13 sign-off does not cover it) that
  lists `REPLACEMENT_ORDER_SET` — the replacement
  orders by Shopify order id, none cancelled, each paid at creation,
  with their refunds (issued BEFORE the addendum is signed, so the
  addendum lists refunds that exist) — and
  `REPLACEMENT_WINDOW` — the `created_at_min`/`created_at_max` pair =
  [T0, T1], the UTC timestamps read on the deployment host at least
  one minute before the first and one minute after the last
  replacement order was created — records that
  nobody edits any earlier order in Shopify Admin (this
  leg selects by `created_at` within `REPLACEMENT_WINDOW` only and
  runs no `updated_at` refund leg, so no earlier order is reachable;
  the rule keeps the read-back attributable to the declared set and
  the §K B-set evidence unchanged), records
  that no embedded launch may occur between the first replacement
  order's creation and the replacement execution's read-back (the
  worker is running, so a launch's `initial_store_sync` would be
  consumed at once, book the replacement orders under a window of its
  own and stand outside the 1 + K pre-declaration — a STOP, not a
  re-sign), authorizes the same contract at `INTAKE_CONTRACT_VERSION`
  (its own window rule is `REPLACEMENT_WINDOW`'s equality, not the
  seven-day rule), names the planned unblock time (≤ ~4 h after the
  first replacement order), and carries the operator's signature with
  `I15_REPLACEMENT_SIGNOFF_TIMESTAMP` (UTC, to the
  second). Then enqueue the worker task `shopify.sync_store_orders`
  with exactly `REPLACEMENT_WINDOW` (the enqueue form and its I5-rule
  status are in the §I closure rule above; the CLI
  `resync_shopify_orders` prints four counters and discards the rest,
  so it is not the evidence-bearing form here), recorded as its own
  execution: its task id (printed at enqueue and written into the
  replacement execution's `preflight/` record at that moment — task
  id, then the A52 pair, then the exported TaskResult row; the
  addendum, signed earlier, pre-declares
  exactly ONE `shopify.sync_store_orders` task by name and count, never
  an id; it is not an initial task and does not disturb the 1 + K
  count), its TaskResult row
  (`date_started` and the complete orders-leg result), its own `[A52]
  _sync_orders start` line on the worker's streams whose
  `created_at_min`/`created_at_max` equal `REPLACEMENT_WINDOW`
  verbatim, and a §K row, with `I15_REPLACEMENT_SIGNOFF_TIMESTAMP` <
  `REPLACEMENT_EXECUTION_STARTED_AT` (that row's `date_started`).
  Replacement evidence contract, read from that TaskResult row (the
  read-only shell form in the §I closure rule; export the row into
  `preflight/` before §I16 — §I14) against the addendum:
  `status` "ok"; `created` = |`REPLACEMENT_ORDER_SET`| (their
  deliveries were held, so none was ingested before) AND every member
  present in the read-back — together the proof that nothing outside
  the set was booked; `skipped` = `fetched` − `created` (0 when the
  window holds only replacement orders); `errors` = 0 (this counter
  folds in every refund-backfill fetch failure — the orders leg has
  no separate `fetch_failures` field); `refunds_backfilled` = the
  refund count the addendum lists (only an order already
  refunded/partially_refunded at fetch time is backfilled — refund
  the replacement orders BEFORE the addendum is signed, hence before
  the enqueue); `pilot_scope_skipped` =
  0; the four `cancelled_*` counters 0 (`REPLACEMENT_ORDER_SET`
  contains no cancelled order by definition);
  `cogs_fulfillments` recorded, no financial effect expected under
  the pilot profile (§I definition, mechanical side effects); its
  refund backfill books their refunds. Any order it
  books outside `REPLACEMENT_ORDER_SET` is a STOP (a re-selection of an
  already-booked order that adds nothing is the idempotent §I closure
  behaviour, not a booking); `AUTHORIZED_PARENT_ORDER_SET`, the I13
  sign-off and the §I14 verdict are unchanged by it. The execution is
  verified in a read-back (rows, events and journals present, 0
  failures), and only then the unblock within ~4 hours of the first
  replacement order — the held deliveries then arrive as true
  duplicates of existing effects. Orders created AFTER the unblock are
  first ingestions, not duplicates. Otherwise
  record an explicit gap against J1's duplicate-delivery proof — never
  a lapsed backlog as exercised.
  STOP if: any retried delivery creates a duplicate row, event or
  journal; a subscription declared by the §E3 app version is found
  missing after the hold; or a replacement execution ran before its
  addendum was signed, or booked an order outside
  `REPLACEMENT_ORDER_SET`, or its A52 window differs from
  `REPLACEMENT_WINDOW`, or it is not recorded as its own execution, or
  its TaskResult row is absent or fails the replacement evidence
  contract ("ran before its addendum was signed" =
  `I15_REPLACEMENT_SIGNOFF_TIMESTAMP` not earlier than
  `REPLACEMENT_EXECUTION_STARTED_AT`).
- [ ] **I16. Beat restart LAST + drift cadence.** Once the initial task
  and webhook retries are reconciled, health is stable and every
  evidence TaskResult row is exported into `preflight/` (§I14 — beat
  installs the 24 h `celery.backend_cleanup`), start Celery
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
development store installed on the deployment's own app (§E3 — so that
signed webhook deliveries actually reach this host), not merely direct
fake command calls; settlement and
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
| Initial-intake set A per execution — A_k for each of the 1 + K initial tasks: order count, ids, totals (that execution's `created_at` window; the windows overlap, so an order may sit in several A_k — the execution whose dispatch of it first succeeded is the one that added it, normally the first that selected it) | Shopify admin/exports |
| Initial-intake set B per execution — B_k for each of the 1 + K initial tasks: its size (that execution's refund-leg `scanned`) and the identities of the parents that leg dispatched: booked (rows in its span with `shopify_created_at` outside its window; inside-window rows whose orders-leg dispatch left a `Failed to process order …` / `Error processing order …` line — the in-execution closure; plus its promotions) or dispatched-and-failed (the order ids in its `[A159] Could not book parent order …` / `[A159] Parent-order booking failed for …` WARNING lines); the selected set itself is not reconstructed — Shopify evaluated it against an order `updated_at` the system neither requests nor stores (§I definition) | task result + system rows (private) |
| `AUTHORIZED_PARENT_ORDER_SET` = the union over the 1 + K executions of A_k ∪ B_k, deduplicated by Shopify order id — evaluated as the union of the A_k plus every parent a refund leg dispatched (booked, promoted or dispatched-and-failed — a failed one stays in the set for its closure); a gap record's row is ADDED by exactly one execution (normally the first that selects it; after a counted error, its closer) and it may remain a member of later candidate sets as a `skipped` no-op or an in-place change of its class — plus, per execution, the refund-leg `scanned` count | Shopify admin/exports vs system |
| Complete refund count and totals per refund-leg-dispatched parent (the B candidates as a class = the union over the 1 + K executions of the parents each refund leg dispatched — the "set B per execution" row; refund evidence supplies each one's complete refund history and totals over the order's whole life, never the membership; never `ShopifyOrder.financial_status`, which is written only at creation) | Shopify "Export transaction histories" file (the orders CSV carries only a per-order refunded amount) vs system |
| Per-order line-item count for every intake order (must equal the stored order evidence's `line_items` count — an independent completeness control over the PR #143 drained reads; the stored list is unfiltered, so equality is exact. Deliberately no variant-count twin: the sync legitimately skips SKU-less variants and the NON_STOCK catalog collapses shared SKUs, so no export-vs-system variant equality exists to demand) | Shopify admin/exports vs stored order evidence (read-only) |
| Oldest and newest imported parent-order dates | system (read-only) vs Shopify |
| Oldest and newest imported refund dates | system (read-only) vs Shopify |
| Refund fetch-failure count (must be 0 on the latest accounted execution; any earlier execution's fetch failure recorded with its §I closure) | every one of the 1 + K `initial_store_sync` task results (the release execution and the K pre-declared re-executions) — refund leg `fetch_failures` AND the orders leg's `errors`, which folds in every per-order refund-backfill fetch failure; for every recorded closure re-execution and any §I15 replacement execution — orders-leg-only `sync_store_orders` TaskResult rows — the `errors` counter, which folds in every refund-backfill fetch failure (that leg has no `fetch_failures` field) and must be 0 |
| Cancelled B candidates: count and ids; for each — local parent present, complete refund count/totals, and the cancellation provenance stamp verified per §I (no stamp-failure entry in `cancelled_processing_errors` and no "Cancellation provenance stamp failed" warning for that order; raw-payload `cancelled_at` corroboration only) | Shopify admin/exports vs system (read-only) + task result / worker log (private) |
| Cancelled A orders: captured-money (booked + stamped) vs never-captured (no financial effect) split | Shopify admin/exports vs `cancelled_financial_processed` (booked + stamped) / `cancelled_no_effect_skipped` (never captured, writer succeeded); any `cancelled_financial_candidates` − `cancelled_financial_processed` gap must be accounted for by `cancelled_processing_errors` + `pilot_scope_skipped` per the §I inequality (row below) |
| Per-leg pilot/cancelled counters (`pilot_scope_skipped`, `cancelled_financial_candidates`, `cancelled_financial_processed`, `cancelled_no_effect_skipped` (orders leg only), `cancelled_processing_errors`) and the inequality check per leg | every one of the 1 + K `initial_store_sync` task results (the K re-executions reconciled per §I14 / §Q step 12 — zero additions on the synthetic store) |
| `pilot_scope_skipped` per leg (must be 0 on the EGP store) | every one of the 1 + K `initial_store_sync` task results (the K re-executions reconciled per §I14 / §Q step 12 — zero additions on the synthetic store) |
| `INITIAL_SYNC_STARTED_AT`, effective window boundaries (timestamps only), `TASK_RECEIVED_AT`, `INTAKE_CONTRACT_VERSION` | worker log `[A52] _sync_orders start` line + the TaskResult row's `date_started` (private) vs the §B runbook revision |
| The complete `initial_store_sync` task result of every one of the 1 + K initial tasks (the release execution and the K pre-declared re-executions, each reconciled against its own execution-time window per §I14 / §Q step 12 — additions only for records that newly qualified since the predecessor, per the five §I14 record classes and their own bounds (parent orders by the gap between consecutive executions' `INITIAL_SYNC_STARTED_AT`; refunds and in-place promotions up to the execution's own `date_done`; in-place cancellations by `cancelled_at`; products by construction), the identity of every addition — Shopify order id, refund id, `shopify_product_id` / `shopify_variant_id`, numeric ids only (an auto-created mapping with variant id 0: the Item code and the booking order's id) — listed with the execution's row, together with any counter residual and its explanation (D14 (B): rows and in-place changes decide, counters corroborate): zero additions, an empty list and no residual on the synthetic store), plus the complete result of every recorded closure re-execution and of any §I15 replacement execution (each with its own task id and TaskResult row — the worker-task form of the §I closure rule; an orders-leg-only result carries the twelve `_sync_orders` fields and no refund-leg keys — A52 line, counters, and inequality; the replacement execution also with its signed addendum — `REPLACEMENT_ORDER_SET`, `REPLACEMENT_WINDOW`, `I15_REPLACEMENT_SIGNOFF_TIMESTAMP` — the A52-window equality and the replacement evidence contract) | worker log / task result / the pre-§I16 TaskResult exports in `preflight/` (§I14; the live rows expire 24 h after beat starts) (private) |
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
other absence is a variance.

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
    at the reverse proxy: reject Shopify webhook requests (every
    I4-enumerated Shopify webhook route, the generic platform endpoint
    included) and every
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
  FRONTEND_BUILD_SHOPIFY_CLIENT_ID
  G1_EVIDENCE_MANIFEST_HASH
  G2_EVIDENCE_MANIFEST_HASH
  G2_BACKUP_HASH
  ```

  This pack is the ONLY revision authority for the merchant cutover (§Q).

  **The artifact rule is SPLIT between the two artifacts.** The backend
  image/artifact digest is portable: the merchant deployment must run
  exactly `GATE_TESTED_IMAGE_OR_ARTIFACT_DIGEST`. The frontend bundle
  is NOT portable: its API origin AND its Shopify app client id are
  compiled in at build time (§E3/§G1c), so
  `GATE_TESTED_FRONTEND_BUNDLE_DIGEST` + `FRONTEND_BUILD_ORIGIN` +
  `FRONTEND_BUILD_SHOPIFY_CLIENT_ID` prove what the REHEARSAL
  environment built and served — a deployment with a different origin
  or a different app must REBUILD the frontend from exactly
  `GATE_TESTED_COMMIT_SHA` with its own recorded origin and client id
  and verify it per §G1c (§Q step 1). A frontend bundle built with
  another environment's origin or app must never serve on any other
  deployment — the rehearsal-built bundle, carrying the rehearsal
  app's id, on the merchant deployment included.

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
- a frontend bundle whose compiled-in Shopify client id is not the
  serving deployment's recorded `FRONTEND_BUILD_SHOPIFY_CLIENT_ID`,
  or a Shopify store connected on an app whose webhook subscription
  URI, compliance URLs, redirect URL or application URL is not on the
  serving deployment's host (§E3 — the published app on the rehearsal host,
  or the rehearsal app anywhere else);
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
- Shopify webhook ingress active before GO through any route (the
  generic platform endpoint included);
- an intake-hold or backup ingress block that covers only the dedicated
  Shopify webhook endpoint while another enumerated Shopify-capable
  route stays reachable;
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
  amended after signing to include one (the §I15 replacement addendum
  declares `REPLACEMENT_WINDOW` in advance by design and is outside
  this bullet);
- a GO record whose "version <n>", or an I13 sign-off, does not name
  the `INTAKE_CONTRACT_VERSION` (the §B runbook revision) in force, or
  a merchant-run runbook revision whose §I definition block is not
  textually identical to the G1-rehearsed one;
- the release execution's `[A52] _sync_orders start` line absent, or
  recorded windows not matching its `created_at_min`/`created_at_max`,
  or `created_at_max − created_at_min ≠ 7 days` (the seven-day rule
  binds `initial_store_sync` executions only; an explicit-window
  closure or replacement execution is reconciled to its declared
  window), or the ordering
  authorization sign-off `< TASK_RECEIVED_AT ≤ INITIAL_SYNC_STARTED_AT`
  failing (a larger-than-seconds gap between the last two is not an
  abort by itself but must be explained in the record), or a shop
  domain copied from that line into attachable evidence;
- a closure re-execution that books any order outside
  AUTHORIZED_PARENT_ORDER_SET, or that is not recorded as an explained
  second execution in the worker-task form with its TaskResult row
  (the CLI form's four printed counters cannot carry the §K contract);
- a §I15 replacement execution (the lapsed-retry path) started before
  its replacement addendum was signed (I15_REPLACEMENT_SIGNOFF_TIMESTAMP
  not earlier than REPLACEMENT_EXECUTION_STARTED_AT), or that books any
  order outside its signed REPLACEMENT_ORDER_SET, or whose A52 window
  differs from REPLACEMENT_WINDOW, or that is not recorded as its own
  execution, or whose TaskResult row is absent or fails the
  replacement evidence contract;
- any unexplained initial task (the expected count is 1 + K — §I14);
- an older parent order or refund excluded merely because its date
  predates the seven-day window;
- the §K per-order line-item completeness control missing or failed
  (an export/evidence count mismatch on the PR #143 drained reads), or
  a capped per-order fulfillment list read as complete COGS evidence
  (§I fulfillment-cap residual);
- any claim that `import_mode="skip"` suppresses the refund catch-up;
- an initial-sync result not observed and retained;
- webhook release before initial-task reconciliation;
- beat restarted before initial-task and webhook-retry reconciliation,
  or before every evidence TaskResult row was exported (§I14);
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
preflight/          # §F blockers, §H bootstrap, §I preflight outputs, the pre-§I16 TaskResult exports (§I14)
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
3. Inspect for and remove: names; email addresses; shop domains (in
   screenshots: address bar, tab title, admin header, app panel); company
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
   rehearsal API origin AND the rehearsal app's client id compiled in,
   so it must NOT be carried into the merchant deployment — rebuild the
   frontend from exactly
   `GATE_TESTED_COMMIT_SHA` with the merchant deployment's
   `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_SHOPIFY_API_KEY` (the client
   id of the app the merchant store installs — the step-3 app-identity
   decision, taken before this rebuild), verify the built bundle per
   §G1c (merchant origin present; `localhost:8000` absent; the server
   build carrying that client id and no other), and record the
   merchant deployment's own
   `FRONTEND_BUNDLE_DIGEST` + `FRONTEND_BUILD_ORIGIN` +
   `FRONTEND_BUILD_SHOPIFY_CLIENT_ID` beside the pack values. Serving
   the rehearsal-built bundle — or any bundle whose recorded build
   origin or client id is not this deployment's — is a STOP.
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
   with THIS deployment's `NEXT_PUBLIC_API_URL` and
   `NEXT_PUBLIC_SHOPIFY_API_KEY`, and the §E3 Shopify app-identity
   record for THIS deployment: the app the real merchant store
   installs must have its `application_url`, redirect URL, webhook
   subscription URI and compliance URLs on the merchant deployment's
   host; the choice of app is a recorded founder decision taken BEFORE
   step 1, whose rebuild compiles that app's client id in — and the
   chosen app's ACTIVE version must carry `read_all_orders` (the §I
   definition's any-age parent reach is a GO requirement — §O, step 11
   — so an app without it cannot execute the contract the GO
   authorizes: STOP here, obtain the approval, release a new active
   version and redo the §E3 record — the step-11 code-level control
   applies only where the reach itself is not wanted) and must hold
   protected-customer-data access approved for non-development stores,
   both per-app Shopify approvals that a dedicated merchant app must
   hold when this record is made and that neither the published app's
   approvals nor the rehearsal app's unreviewed development-store
   posture transfer; the published
   app's active version points every URL at `https://app.nxentra.com`,
   and a new version of the published app pointing elsewhere would
   re-point EVERY store installed on it — the same app-wide property
   §E3 relies on — so a merchant host with any other origin uses its
   own dedicated app unless the founder records why re-pointing the
   published app is acceptable for every install it has; the rehearsal
   app is never used for a real merchant); deployment, service
   startup, version proof and boot health (§G — including the §G1c
   frontend build from `GATE_TESTED_COMMIT_SHA` with THIS deployment's
   origin and client id and its built-artifact verification, per step
   1, and the
   §G1g `/_next/image` refusal probed externally on the merchant host);
   **the §F
   blockers with FRESH evidence for THIS
   deployment and THIS merchant** — F1 confirmed by verifying that
   `GATE_TESTED_COMMIT_SHA` contains the fixing PR recorded in the G1
   `preflight/` evidence (hash-bound via `G1_EVIDENCE_MANIFEST_HASH`;
   not re-proven), F2 proven per its §F evidence field — the
   reverse-proxy/config test AND an external HTTP probe from outside
   the merchant host (`/_health/alerts` answers; `/_health/full` and
   `/_metrics/` are refused) — and F3 decided and proven for the real
   merchant's expected webhook burst and retry volume against the
   merchant deployment's EFFECTIVE ceiling (rate × web workers, proxy
   ident posture — §F3 evidence arm; the code arm re-confirmed by
   ancestry) — synthetic-rehearsal F2/F3 evidence does NOT transfer,
   and step 4's
   intake hold verifies neither; base onboarding (§H); activation-aware
   validation (§I1); pilot activation (§I2) — **never before the fresh
   F1–F3 evidence exists for this deployment**; pilot-aware Shopify
   provisioning (§I3);
4. establish the **controlled Shopify intake hold** (§I4 form): webhook
   ingress blocked on EVERY I4-enumerated Shopify webhook route (the
   dedicated endpoint and the generic platform endpoint) and
   interactive-sync ingress blocked, all with retryable non-success
   responses; beat stopped; worker drained and stopped; Redis/broker
   AVAILABLE; task-queue baseline recorded;
5. connect the **real merchant Shopify store** — for the first time
   anywhere in this process — through the controlled **standalone OAuth
   path**;
6. prove the post-connect state: exactly one ACTIVE real store, whose
   stored `ShopifyStore.scopes` names exactly THIS deployment's §E3
   scope set — `read_all_orders` present — compared comma-split and
   order-insensitive with the raw string recorded (the §I5
   granted-scope check; a difference is a STOP before GO); **no
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
    `PRE_GO_INITIAL_TASK_IDS` (the queued `initial_store_sync` ids in
    queue order from a read-only broker listing — never a purge;
    expected 1 + K), `PRE_GO_TIMESTAMP`. If merchant source or financial data has
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
    contract (this is the "version <n>" the wording cites) —
    `INITIAL_TASKS_QUEUED = 1 + K` with `K` (the successful embedded
    `token-exchange/` calls since the merchant store's connection) and
    the ordered task ids copied from step 10's
    `PRE_GO_INITIAL_TASK_IDS` (the first is the release execution; no
    embedded launch may occur between signing and the step-12 worker
    start — re-sign if one does), and merchant
    acknowledgement/approval where required. (The GO's
    "complete parent order" (item 3) and "merchant product catalog"
    (item 4) are provable because the executed revision contains the
    PR #143 nested-collection pagination fix — §B requires it, and the
    §I fix-on-record note carries the detail. The §K per-order
    line-item completeness control additionally verifies the drained
    ORDER reads independently at step 12; the catalog side rests on
    the revision proof and the fix's own fail-loud contract.) The GO record
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
    `shopify.initial_store_sync` task; record, for every one of the
    1 + K initial tasks, its task id, start/end timestamps and complete
    result (the release execution first in queue order; each of the K
    pre-declared re-executions reconciled against its OWN
    execution-time window — its own A52 pair, the seven-day rule and
    `GO_TIMESTAMP < its date_started ≤ its INITIAL_SYNC_STARTED_AT` —
    and permitted to add exactly what newly qualified since its
    predecessor, because the merchant keeps trading while the hold is
    on — per record class and evidence as §I14 states, its five
    classes: (1) parent orders — an A record (`shopify_created_at`
    inside this execution's window) booked by the orders leg,
    explained by a Shopify `created_at` inside (predecessor's
    `INITIAL_SYNC_STARTED_AT`, own `INITIAL_SYNC_STARTED_AT`] (or a
    boundary record at most 60 s before the predecessor's start that
    no earlier execution dispatched, or an order the predecessor
    fetched unrouted that became routable in the gap — admitted and
    recorded as §I14 states); a
    B-only record (outside the window) booked by the refund leg,
    explained by the in-gap refund or edit that put its `updated_at`
    inside the pair; a parent first booked already refunded brings
    its refund history; (2) gap refunds on already-booked parents by
    the refund's Shopify timestamp inside (predecessor's
    `INITIAL_SYNC_STARTED_AT`, own `date_done`] — older only when the
    parent's `updated_at` first entered the pair in this execution,
    when its `financial_status` first became refunded in the gap, or
    as the closer of an earlier counted backfill error; (3)
    PENDING_CAPTURE stubs promoted in place (a COD order paid in the
    gap) — no new order row, identified by an `event_id` whose
    `BusinessEvent` `recorded_at` lies in [its `date_started`, its
    `date_done`] on a row created before `date_started`, explained by
    the transaction that moved `financial_status` to a paid-writer
    status in the order's Admin timeline (inside the class-2
    bound when the orders leg promoted it; when the refund leg did,
    by the in-pair `updated_at` that selected the order); (4)
    never-captured cancellations dispositioned in place — a stub
    flipped to CANCELLED or re-stamped, or a booked-while-authorized
    row voided since and re-stamped: no row, no event, no journal;
    identified from the orders export as the cancelled never-captured
    orders inside the window with `cancelled_at` before this
    execution's `date_started` that have a local row (one cancelled
    during the run — `cancelled_at` in [its `date_started`, its
    `date_done`] — is recorded as such and explained either way);
    explained by that `cancelled_at` (a captured-money cancellation on
    a booked row is only stamped — `skipped`, never `created` — and
    is explained the same way); (5) products — a new
    `ShopifyProduct` mapping is the paid writer's when a paid-writer
    `Auto-created Item … cost=… (inventory=…, cogs=…)` line naming
    its Item code lies in the execution's span, explained by that
    line's order; every other new
    mapping is the products leg's, explained by construction (the
    windowless products leg read the whole catalog on the
    predecessor, whose products leg must have completed on its
    counter shape) with no Shopify timestamp (none is read into the
    system); a closer of an earlier counted error per the §I closure
    rule. The STOP criterion is the rows and in-place changes: every
    local `ShopifyOrder` / `ShopifyRefund` / `ShopifyProduct` row
    whose `created_at` lies in [its `date_started`, its `date_done`],
    every `BusinessEvent` recorded in that span and every class-4
    disposition explained by its class — an unexplained one is a
    STOP; the counters corroborate and never decide (D14 (B)):
    normally `created` = A rows + orders-leg promotions + class-4
    dispositions, `refunds_backfilled + refunds_created` = every new
    refund row, products `created + linked` + the paid writer's
    `Auto-created Item … cost=… (inventory=…, cogs=…)` worker lines
    in the execution's span = every new mapping; any residual is
    recorded with its explanation (post-commit error paths,
    in-execution closures, same-second boundaries), and a residual no
    row, line or warning explains is a STOP; the exact merchant-path
    counter algebra belongs to the merchant-cutover document of the
    post-G1 runbook split; every addition's identity (order id,
    refund id, `shopify_product_id` / `shopify_variant_id` — the Item
    code and the booking order's id for an auto-created mapping with
    variant id 0) recorded with the execution's row — and each parent-order
    addition joins `AUTHORIZED_PARENT_ORDER_SET` for that execution,
    anything else is a STOP; all 1 + K rows exported before
    step 15). **Post-execution
    controls — the
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
    `django_celery_results` TaskResult row (`date_started`, keyed by
    task id; corroborated by the `[A52] _sync_orders start` line that
    follows within seconds — §I definition), and
    every per-leg result field; reconcile them to the step-11 GO
    record — `INTAKE_CONTRACT_VERSION` and `INITIAL_LOOKBACK_DAYS = 7`
    — by the §I rule: `created_at_max − created_at_min` = exactly 7
    days, and `GO_TIMESTAMP < TASK_RECEIVED_AT ≤ INITIAL_SYNC_STARTED_AT`
    (the inequality failing is a STOP; the last two are normally
    seconds apart — a larger gap is not a STOP by itself but must be
    explained in the record) — before any merchant-facing checkpoint;
    an absent A52 start line is a STOP. Require exactly 1 + K initial
    tasks consumed on release, K being the successful embedded
    `token-exchange/` calls after the merchant's binding that the GO
    record pre-declares from `PRE_GO_INITIAL_TASK_IDS` (each queues one
    task — §I14; the first in queue order is the release execution, the
    others explained re-executions that add only what newly qualified
    since the predecessor, per the five §I14 record classes and
    their own bounds — step 12); any later
    re-execution used for §I closure is a separately recorded,
    explained execution (own task id and TaskResult row — the
    worker-task form of the §I closure rule, usable here only after a
    recorded synthetic proof of the enqueue form — own A52 line,
    counters and inequality, retained beside the release result), and
    any UNEXPLAINED initial task is a STOP. A task result with NO leg
    keys (`skipped` "Store
    not active" / "tenant not writable", `error` "Store not found"), or
    a task that raised before any leg (Celery FAILURE, no result dict),
    means the queued task was consumed without executing the
    authorized intake — STOP; do not re-enqueue until the cause is
    explained and the step-10 no-ingestion baseline is re-proven.
    Require completion or a fully explained loud failure, evaluated PER
    LEG (the top-level `status` is unconditionally `"ok"` when the legs
    run and proves nothing — §I definition).
    **Reconcile to the authorized intake contract** (§I definition),
    not merely to source timestamps within seven days: record, per
    execution (each of the 1 + K), A_k from the orders export and B_k
    by its `scanned` size and the parents its refund leg dispatched —
    booked, promoted, or dispatched-and-failed by its `[A159]`
    failure lines (the selected set itself is not reconstructed — §I
    definition; the B candidates as a class are those dispatched
    parents, §K, their refund histories checked against refund
    evidence); reconcile `AUTHORIZED_PARENT_ORDER_SET` — the union of
    the A_k plus every refund-leg-dispatched parent over the 1 + K
    executions — from order ids without double counting (a gap
    record's row is added by exactly one execution — normally the
    first that selects it, after a counted error its closer — and it
    may sit in later candidate sets as a `skipped` no-op or an
    in-place change of its class; never by summing leg counters); require, for every B candidate — cancelled or not — a
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
    state (§I definition's mechanical side effects); and verify the
    §K per-order line-item completeness control against the merchant's
    Shopify export: every intake order's line-item count equals the
    stored order evidence's count (an independent control over the
    PR #143 drained reads; a mismatch or a missing control is a STOP);
13. rerun `pilot_preflight --phase go-live`, `/_health/alerts`, and the
    source/control totals;
14. only after the initial task is accounted for, unblock Shopify
    webhooks; observe and reconcile webhook retries; prove duplicate
    webhook delivery does not duplicate financial effects. The same
    retry clock as §I15 binds this step: Shopify retries a held
    delivery 8 times over ~4 hours from its first attempt, so the
    hold must be lifted within ~4 hours of the FIRST merchant
    delivery it held (plan steps 8–14 inside one sitting); a
    delivery that lapsed reaches the ledger only through the
    periodic catch-up after step 15 and cannot serve as the
    duplicate-delivery proof — record any lapse explicitly. The
    duplicate proof is carried by held deliveries for orders any of
    the 1 + K step-12 executions already booked; a held delivery for
    an order created after the LAST of those executions'
    `INITIAL_SYNC_STARTED_AT` is a FIRST ingestion when it arrives
    (not a duplicate — an order created in a gap between consecutive
    executions was booked by the later one and its held delivery IS a
    duplicate), and an order created after the unblock
    is ordinary webhook intake;
15. start Celery beat **LAST** — after every evidence TaskResult row
    (every one of the 1 + K initial-task rows — the release execution
    and the K pre-declared re-executions whose per-window
    reconciliation step 12 reads from their complete results — and
    every recorded
    closure re-execution's) is exported as in §I14, because beat
    installs the 24 h
    `celery.backend_cleanup`;
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
    counted; a partial fetch is treated as success; or the §K
    per-order line-item completeness control is missing or failed.

The GO decision precedes the first merchant product/order/refund source
write.

STOP if: the intended merchant deployment uses a revision or image not
named by the completed revision pack; the merchant deployment serves a
frontend bundle not rebuilt from `GATE_TESTED_COMMIT_SHA` with the
merchant deployment's recorded `FRONTEND_BUILD_ORIGIN` and
`FRONTEND_BUILD_SHOPIFY_CLIENT_ID` and verified per §G1c (the backend
artifact-identity rule never transfers to the origin- and app-specific
frontend bundle — §N5 split rule); the real store's granted scope set
differs from the §E3 record (`read_all_orders` absent); the merchant
company is activated
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
treated as skipped or outside the required evidence set; any
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
