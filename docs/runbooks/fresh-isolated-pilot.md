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
    run (all seven jobs including Quality Gate). Expected revision for the
    first execution: `c4896ff31b283d165a5180ca210cb981a93b3588`
    (tree `8104cf6824e473e92cc4c184c04525cd160ba95f`; main CI run
    33394909451, seven jobs green). A later reviewed `main` revision with
    green required CI may be **selected here, before a new rehearsal
    begins** — that selected revision then becomes the subject of this
    runbook's G1 and G2, and the operator records the new exact SHA. This
    selection rule never transfers a PRIOR G1/G2 verdict forward: the
    merchant cutover (§Q) must deploy the exact revision pack that passed
    the gates.
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
store connect and sync → configuration proofs → go-live preflight →
drift-verification cadence. For every run record: exact command, company
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
- [ ] **I4. Connect exactly one SYNTHETIC development store.**
  - Command / action: from `/shopify/settings`:
    `POST /api/shopify/install/` → complete OAuth for
    `<SYNTHETIC_DEV_SHOP_DOMAIN>`. The store MUST be a non-production
    Shopify development/test store containing only synthetic catalog and
    transaction data — no copied real customer/order/refund data — while
    still exercising real OAuth, App Bridge/session-token behavior,
    signed webhook delivery, and API synchronization.
  - Expected result: exactly one ACTIVE synthetic development store in
    the rehearsal database; the OWNER's Shopify user binding exists
    (go-live preflight checks `store_count` and `binding_missing`).
  - STOP if: the domain is the first merchant's live store; the store
    contains real merchant catalog, customers, orders, or financial
    history; or data was copied from the merchant merely to make the
    test realistic.
- [ ] **I5. Product sync and EGP currency proof (synthetic catalog
  only).**
  - Command / action: run the initial **product sync** only now — after
    activation — so every synchronized item is forced NON_STOCK. The
    sync imports only the synthetic development-store catalog. The
    product sync is the path that persists the durable `shop_currency`
    snapshot (an order sync alone does not persist it; the go-live
    preflight then falls back to a read-only live probe). The go-live
    preflight (`store_currency_unknown` / `store_currency_not_egp`) is
    the proof. Historical import stays `skip` throughout bootstrap and
    G1 preparation — no live historical merchant-order import occurs
    before G1 and G2 close.
  - Expected result: durable `shop_currency` = EGP; every synchronized
    item is NON_STOCK; zero item inventory/COGS account links; zero
    inventory ledger/FIFO residue.
  - STOP if: preflight reports either store-currency violation, or any
    INVENTORY item / inventory residue appears.
- [ ] **I6. Settlement providers and posting profiles.**
  - Command / action: the Shopify setup bootstraps the provider rows and
    `PG-*` posting profiles; review at `/shopify/settings`
    (`PATCH /api/accounting/settlement-providers/<pk>/`) so that at least
    one of **paymob** / **bosta** is ACTIVE — and note the preflight
    checks **every** ACTIVE supported provider, so each provider left
    ACTIVE must route to an ACTIVE posting profile with a postable
    control account (preflight codes `provider_missing`,
    `provider_posting_profile`).
- [ ] **I7. Cash/Bank account.**
  - Command / action: confirm an ACTIVE, non-header LIQUIDITY account
    exists (template account `11000 Cash and Bank`, or create one at
    `/accounting/chart-of-accounts/new`). Preflight code:
    `bank_account_missing`.
- [ ] **I8. Single-company / single-owner proof.**
  - Command / action: re-run the §C2 count for `Company` (expect exactly
    1) and confirm in the UI there is exactly one active OWNER membership
    and no other members. (After activation, `deployment_has_pilot()`
    blocks all further signup/company creation deployment-wide.)
  - STOP if: more than one company or active membership exists.
- [ ] **I9. Excluded-capability refusal checks.**
  - Command / action: confirm purchases/clinic/properties are not enabled
    and their enable doors refuse under the active pilot (spot-check one
    refusal; the rehearsal in §J exercises more).
- [ ] **I10. Go-live preflight.**
  - Command / action:
    `python manage.py pilot_preflight --company <PILOT_COMPANY_ID> --phase go-live --json`
  - Expected result: `ok: true`, exit 0 — this is the full agreed-workflow
    proof (EGP store, OWNER↔store binding, postable clearing/EBD mappings,
    active provider + posting profile, canonical bank account).
  - STOP if: any violation.
- [ ] **I11. Drift verification cadence.** Re-run I10 after every
  sync/import during the rehearsal (§J) and before every sign-off in this
  runbook. Any new violation is a STOP.

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

For each row: run → verify the durable outcome and its operator surface →
verify `/_health/alerts` (or `python manage.py alert_check`) reflects it →
verify recovery where the contract heals.

- [ ] **J0. A1 live Shopify embedded-authentication proof —
  independently signed, BEFORE the financial scenarios.** Uses only the
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

  Sign-off (J0 alone): operator ______ date ______
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

**G1 cannot close unless J0 passes.** The financial J1–J7 matrix alone is
insufficient — the tracker's A1 row remains operationally open until J0
evidence exists. This documentation PR marks neither A1 nor G1 complete.

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
    only the approved read-only surfaces; record every source-file hash;
    serialize the final control manifest; compute and record its
    SHA-256; record the timestamp. After this point no source or system
    control may be edited.
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
- [ ] N2. Boot the application (same pinned revision) against the scratch
  database, isolated from production traffic and from Shopify webhooks.
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
  inputs and the restore-time clock; require `/_health/alerts` to match
  that **age-aware expectation**. A difference caused solely by elapsed
  time is acceptable only when all underlying persisted inputs match
  exactly AND the recorded threshold calculation fully explains the
  difference. Do not run a live Shopify sync, rewrite
  `last_sync_at`/`created_at`, resolve evidence, or otherwise mutate
  restored state merely to recreate the pre-backup alert response.
- [ ] N4. Evidence to retain (`restore/`): the comparison table, restored
  revision, hashes.
- [ ] N5. **Issue the gate-tested revision pack.** On successful G1 + G2
  closure, record one immutable revision pack in `signoff/`:

  ```
  GATE_TESTED_COMMIT_SHA
  GATE_TESTED_GIT_TREE_SHA
  GATE_TESTED_IMAGE_OR_ARTIFACT_DIGEST
  GATE_TESTED_MIGRATION_MANIFEST
  G1_EVIDENCE_MANIFEST_HASH
  G2_EVIDENCE_MANIFEST_HASH
  G2_BACKUP_HASH
  ```

  This pack is the ONLY revision authority for the merchant cutover (§Q).

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
   deployed commit SHA == `GATE_TESTED_COMMIT_SHA`; deployed
   image/artifact digest == `GATE_TESTED_IMAGE_OR_ARTIFACT_DIGEST`;
   deployed migration manifest == `GATE_TESTED_MIGRATION_MANIFEST`.
   If ANY differs: STOP — perform a fresh G1 and G2 cycle on the desired
   revision, issue a new revision pack, and use only that newer completed
   pack. Equivalence is NEVER inferred from a same branch name, a version
   label, green CI, a small diff, "docs only", or developer judgment.
   (If `main` advanced but the deployment uses the exact already-tested
   commit and artifact, the existing G1/G2 proof remains applicable.)
2. provision a **NEW empty isolated merchant database**;
3. repeat, against the new database: the revision proof (§B); the
   fresh-database zero-count proof (§C); the environment-safety proof
   (§E); base onboarding (§H); activation-aware validation (§I1); pilot
   activation (§I2); pilot-aware Shopify provisioning (§I3);
4. connect the **real merchant Shopify store** — for the first time
   anywhere in this process;
5. run the real-store product sync only after the gates have closed;
6. verify: exactly one ACTIVE store; OWNER binding; EGP store currency;
   every product NON_STOCK; no inventory/COGS mappings or residue;
   provider/posting-profile configuration; bank account; go-live
   preflight (§I10) clean; `/_health/alerts` healthy;
7. **merchant cutover authentication smoke proof** — after the real
   store is connected and before any merchant financial item is
   admitted: a real-store `ShopifyUserBinding` exists for the intended
   merchant operator; one successful embedded launch using the
   designated pilot browser posture with third-party cookies disabled;
   session-token authentication resolving to the correct merchant
   company and membership; clean go-live preflight including the
   expected OWNER/store binding. This is a merchant-specific smoke
   proof, not a substitute for the synthetic J0 rehearsal. STOP before
   merchant data if the real operator cannot authenticate in the
   embedded app through the explicit binding;
8. keep historical order import set to `skip` until the founder
   separately authorizes the intake window;
9. sign a dated GO decision before the first real
   order/refund/settlement/bank item is admitted.

STOP if: the intended merchant deployment uses a revision or image not
named by the completed revision pack; the rehearsal database or its
backup is reused; synthetic financial history appears in the merchant
database; both synthetic and real stores coexist; the real store is
connected before G1/G2 closure; or go-live preflight is not clean.

No in-place synthetic-store-to-real-store replacement procedure exists or
is permitted: the exactly-one-ACTIVE-store constraint and the rehearsal
database's synthetic financial history make in-place promotion the wrong
model.

Sign-off: founder/operator ______ date ______
