# Nxentra Requirements — Database-per-Tenant Premium Isolation

> **Version:** 1.0
> **Status:** Draft
> **Last Updated:** 2026-01-31

---

## 0. Objective

Nxentra must support **two tenancy storage modes**:

### Shared Mode (default)

- All tenants in one Postgres DB
- Tenant isolation via JWT `company_id` + RLS
- Lowest ops cost, fastest iteration

### Dedicated DB Mode (premium / paying customers)

- Tenant has its own Postgres database (managed Postgres instance or separate DB on same cluster)
- Nxentra routes tenant requests to that tenant's DB
- Tenant migration is achieved via **event-only export/import + projection replay**

The system must allow migrating a tenant from **Shared → Dedicated DB** with:

- Minimal downtime
- Verifiable integrity
- An auditable trail
- Reversibility (at least for early phase)

---

## 1. Core Principles

| ID | Principle | Description |
|----|-----------|-------------|
| **P1** | Truth is events | `BusinessEvent` is the source of truth. Read models/projections are rebuildable from events. |
| **P2** | System vs Tenant separation | "System data" lives only in System DB (default DB). "Tenant data" lives in either Shared tenant DB or Dedicated tenant DB. |
| **P3** | Routing must be deterministic | Each request must deterministically route to exactly one tenant DB based on: authenticated identity, tenant context (`company_id`) from JWT claim, and Tenant Directory lookup in System DB. |
| **P4** | Migration must be repeatable | Export/import/replay must be scripted, testable, and idempotent. |

---

## 2. Data Model Requirements

### 2.1 Tenant Directory (System DB)

#### TD-1: TenantDirectory Table

Create a System DB table, e.g. `system_tenantdirectory`, containing:

| Column | Type | Description |
|--------|------|-------------|
| `tenant_id` | UUID or int | Must match Nxentra company/workspace id |
| `mode` | enum | `shared`, `dedicated_db` |
| `db_alias` | string (nullable) | An alias key, **not** raw credentials |
| `status` | enum | `active`, `migrating_out`, `migrated`, `suspended` |
| `created_at` | datetime | |
| `updated_at` | datetime | |
| `migrated_at` | datetime (nullable) | |
| `migration_version` | int (nullable) | For future migrations/upgrades |
| `notes` | text (nullable) | |

**Constraints:**

- Unique index on `tenant_id`
- `mode = shared` implies `db_alias IS NULL` (or a known constant like `"shared"`)
- `mode = dedicated_db` implies `db_alias IS NOT NULL`

#### TD-2: DB Connection Mapping Policy (no secrets in DB)

TenantDirectory must **NOT** store plaintext DSNs/passwords.

Instead, store:
- `db_alias` referencing a server-side configuration map

**Example mapping locations:**
- Environment variables
- A secure secrets manager
- A local encrypted config file (later)

> **Acceptance Requirement:** No database credentials committed to repo or stored in plaintext DB fields.

### 2.2 System vs Tenant Tables

#### TD-3: System Tables (always in System DB)

System DB must contain:

- Users / auth identities
- Memberships (user↔company) and role/permissions
- Tenant directory
- Billing/subscription status (future)
- Operational logs and migration history

#### TD-4: Tenant Tables (routable)

Tenant DB must contain:

- Event store tables (`events_businessevent`, etc.)
- Projections/read models
- Tenant-local configs that can be rebuilt from events (preferred)

> **Rule:** If a table contains tenant financial truth, it belongs to tenant DB, not System DB.

---

## 3. Routing Layer Requirements

### 3.1 Inputs and Resolution

#### RL-1: Tenant Identification Source

Tenant id must be taken from JWT claim:
```
company_id
```

**No fallback** to `user.active_company_id` for routing.

#### RL-2: Tenant Directory Lookup

On each authenticated request with `company_id`:
1. Query `TenantDirectory` in System DB
2. Determine tenant `mode` + `db_alias`

#### RL-3: Routing Decision

| Mode | Routing Target |
|------|----------------|
| `shared` | Route to shared tenant DB (often same physical DB but logically default alias for tenant models) |
| `dedicated_db` | Route to tenant DB specified by `db_alias` |

#### RL-4: Allowlist When company_id Is Missing

Requests without `company_id` claim must be restricted to a small allowlist:

- `login` / `register` / `refresh` / `verify-email`
- List companies
- Switch company (issues new tenant-bound token)

**Everything else returns 403.**

### 3.2 Django Implementation Requirements

#### RL-5: Tenant DB Context Must Be Per-Request

Implement a request-scoped context holder (`thread-local` or `contextvar`) that stores:
- `current_tenant_id`
- `current_tenant_db_alias`

#### RL-6: DB Router

Create a Django DB router that:
- Routes all **tenant models** to `current_tenant_db_alias`
- Routes **system models** to `"default"` always
- Prevents accidental writes of system models to tenant DB and vice versa

#### RL-7: Model Classification

Define a canonical rule for model classification:

| Classification | Routing |
|----------------|---------|
| `SystemModel` | Routed to `default` always |
| `TenantModel` | Routed via router to tenant alias |

> **Acceptance Requirement:** Every model is explicitly classified (even if by module path rule).

#### RL-8: Middleware Order

Middleware must:
1. Authenticate JWT early (or run after DRF auth but before DB access)
2. Extract `company_id`
3. Resolve `db_alias` from `TenantDirectory`
4. Set tenant DB context
5. Set RLS context only for shared mode (optional for dedicated mode)

### 3.3 RLS Behavior by Mode

#### RL-9: Shared Mode Uses RLS

For shared tenants:
- Set `app.current_company_id`
- RLS policies must remain enforced

#### RL-10: Dedicated Mode May Disable RLS (optional)

In dedicated DB mode you **may** disable RLS because there is only one tenant.
But you **must** keep application-level scoping intact.

**Requirement:** Choose one:
- Keep RLS enabled for defense-in-depth, **or**
- Disable RLS but ensure no cross-tenant data exists in that DB

> **Recommended:** Keep RLS in shared mode, optional in dedicated mode.

---

## 4. Event-Only Export/Import + Replay Requirements

### 4.1 Export Command

#### MIG-1: Export Events for a Tenant

Implement a management command:

```bash
export_tenant_events --tenant-id=<id> --out=<file>
```

It must export:
- All events where `company_id = tenant_id`
- Ordered deterministically (by event id / created_at)
- Include event metadata required for replay:

| Field | Required |
|-------|----------|
| `event_id` | Yes |
| `aggregate_type` / `aggregate_id` | Yes |
| `event_type` | Yes |
| `payload` | Yes |
| `actor_id` | Yes |
| `timestamps` | Yes |
| `schema_version` | Yes |

**Format:**
- JSON Lines (recommended) or compressed JSON
- Include a header with export version + counts + hash

#### MIG-2: Export Only What Is Rebuildable

Do **NOT** export projections (unless required for speed and treated as cache).

The canonical migration must rely on replay.

If there is required tenant metadata not in events, you must:
- Either represent it as events going forward, **or**
- Explicitly document it as "System/Tenant config export" and treat it as source of truth (discouraged)

### 4.2 Import Command

#### MIG-3: Import Events into Target Tenant DB

Implement:

```bash
import_tenant_events --db-alias=<tenant_db_alias> --in=<file>
```

It must:
- Validate file schema/version
- Validate tenant id in file matches target tenant
- Insert events idempotently (skip if already exists)
- Preserve event ids (or map, but mapping complicates audits)

#### MIG-4: Idempotency

Running import twice must **NOT** duplicate events.

### 4.3 Replay Projections

#### MIG-5: Replay from Event Store

Provide a command:

```bash
replay_projections --db-alias=<tenant_db_alias> --tenant-id=<id>
```

It must:
- Rebuild all projections from events
- Reset projection tables safely before replay
- Track progress and allow resuming

#### MIG-6: Consistency Checks After Replay

After import+replay, run checks:

**Counts:**
- Number of events imported equals export header count
- Number of projection applied events equals events count (if you track)

**Hashes:**
- Compute hash of event stream in source and target (deterministic)
- Compare hashes

**Spot Checks:**
- Trial balance totals match
- Posted journal entries count matches
- Account balances for a sample set match

#### MIG-7: Migration Log

Write a migration record in System DB:

| Field | Description |
|-------|-------------|
| `tenant_id` | Migrated tenant |
| `from_mode` / `to_mode` | Migration direction |
| `start_time` / `end_time` | Timing |
| `export_hash` | Source verification |
| `import_hash` | Target verification |
| `operator_identity` | Who performed migration |
| `result` | success/fail + error logs |

### 4.4 Cutover Procedure

#### MIG-8: Cutover Requires Freeze Window

During cutover:
1. Prevent new writes for tenant in source (shared) DB
2. Export last events
3. Import + replay
4. Update `TenantDirectory.mode` to `dedicated` with `db_alias`
5. Re-enable tenant writes

> **Requirement:** Nxentra must support a **"tenant write freeze"** mechanism.

---

## 5. Rebuild-from-Events Discipline

> **This is the part most systems lie about. Nxentra must enforce it, or migration becomes fantasy.**

### 5.1 Projection Rebuildability

#### DISC-1: Every Projection Must Be Fully Replayable

For each projection table, you must be able to **drop it** and **rebuild from event stream** with deterministic result.

> **Acceptance:** A `replay_projections` command can recreate the system's financial reports correctly for any tenant.

### 5.2 No Truth-Changing Direct Writes

#### DISC-2: Direct Writes Affecting Financial Truth Are Prohibited

Any change that affects accounting truth must be expressed as an event.

If there are exceptional direct writes (e.g., file uploads):
- They must **NOT** affect financial truth, **or**
- They must be mirrored as events (recommended)

#### DISC-3: Enforcement Mechanism

- Keep projection write guards
- Add code audit rules or lint checks to detect `.save()` on tenant models outside allowed contexts
- Maintain a list of approved bypasses (should trend toward zero)

---

## 6. Security Requirements

| ID | Requirement |
|----|-------------|
| **SEC-1** | **Tenant routing cannot be user-controlled by headers.** Tenant ID comes from signed JWT claim only. |
| **SEC-2** | **Membership validation.** When minting or refreshing tenant-bound tokens, Nxentra must validate: user is a member of `company_id`, tenant is not suspended, (optional) roles/permission version checks. |
| **SEC-3** | **Data exfil protection.** Dedicated tenant DB credentials must be scoped: least privilege, separate DB user per tenant DB if possible, encrypted at rest (managed Postgres). |

---

## 7. Operational Requirements

| ID | Requirement |
|----|-------------|
| **OPS-1** | **Provisioning for dedicated DB.** Provide an internal/admin-only action to: provision a new tenant DB, apply migrations, verify connectivity, register `db_alias` in `TenantDirectory`. |
| **OPS-2** | **Monitoring/backup.** For dedicated tenants: backups and restore procedures must be documented, health check endpoint validates DB connectivity. |
| **OPS-3** | **Safe rollback (early phase).** If cutover fails: tenant remains in shared mode, no partial routing change, dedicated DB can be discarded or retried. |

---

## 8. Acceptance Criteria

A release is acceptable when:

| # | Criterion |
|---|-----------|
| 1 | Shared tenants continue to function exactly as before. |
| 2 | A dedicated tenant routes all tenant models to their DB and system models to default DB. |
| 3 | Export/import/replay can migrate a tenant with: matching event counts, matching event stream hash, matching trial balance totals. |
| 4 | Refresh/login validate tenant membership and refuse revoked access. |
| 5 | Migration is logged in System DB with hashes and timestamps. |
| 6 | No secrets are stored in `TenantDirectory` in plaintext. |

---

## 9. Implementation Milestones

### Milestone 1 — TenantDirectory + Routing Skeleton

| Deliverable | Description |
|-------------|-------------|
| System model created | `TenantDirectory` table in System DB |
| Request context established | Per-request tenant context holder |
| Router routes tenant models | Tenant models route to selected alias |
| Tests prove correct routing | Unit/integration tests per request |

### Milestone 2 — Export/Import/Replay

| Deliverable | Description |
|-------------|-------------|
| Export tenant events | `export_tenant_events` command |
| Import into dedicated DB | `import_tenant_events` command |
| Replay projections | `replay_projections` command |
| Run consistency checks | Hash + count verification |

### Milestone 3 — Cutover + Freeze

| Deliverable | Description |
|-------------|-------------|
| Write freeze mechanism | Per-tenant write freeze |
| End-to-end migration script | Orchestrated migration |
| Rollback plan | Documented rollback procedure |

### Milestone 4 — Discipline Enforcement

| Deliverable | Description |
|-------------|-------------|
| Audit bypasses | Document all `.save()` bypasses |
| Ensure all projections rebuildable | Replay test for each projection |
| Documentation + team rules | Even if team = you |

---

## Appendix A: Model Classification Reference

### System Models (always `default` DB)

```python
# accounts app
User
Company  # metadata only, financial data in tenant DB
CompanyMembership
CompanyMembershipPermission
NxPermission
EmailVerificationToken

# system app (new)
TenantDirectory
MigrationLog
```

### Tenant Models (routed per tenant)

```python
# events app
BusinessEvent
EventPayload  # if using payload store

# accounting app
Account
JournalEntry
JournalEntryLine

# projections app
AccountBalance
FiscalPeriod
FiscalPeriodConfig
TrialBalanceSnapshot

# edim app
SourceSystem
MappingProfile
IngestionBatch
StagedRecord
IdentityCrosswalk
```

---

## Appendix B: Environment Configuration Example

```bash
# System DB (default)
DATABASE_URL=postgres://user:pass@host:5432/nxentra_system

# Tenant DB aliases (resolved from TenantDirectory.db_alias)
# These are NOT stored in DB - only alias names are stored
TENANT_DB_ALIAS_TENANT_001=postgres://tenant001:pass@tenant-db-1:5432/tenant_001
TENANT_DB_ALIAS_TENANT_002=postgres://tenant002:pass@tenant-db-2:5432/tenant_002

# Or via secrets manager
TENANT_DB_SECRETS_PATH=/run/secrets/tenant-db-credentials.json
```

---

## Appendix C: Migration Checklist

### Pre-Migration

- [ ] Tenant has no outstanding errors in event store
- [ ] All projections up to date
- [ ] Dedicated DB provisioned and migrations applied
- [ ] DB alias registered in secrets manager
- [ ] TenantDirectory entry created with `status=active`, `mode=shared`

### During Migration

- [ ] Set `TenantDirectory.status = migrating_out`
- [ ] Enable write freeze for tenant
- [ ] Run `export_tenant_events`
- [ ] Record export hash and count
- [ ] Run `import_tenant_events` on dedicated DB
- [ ] Run `replay_projections` on dedicated DB
- [ ] Verify: event count, hash, trial balance, sample accounts

### Post-Migration

- [ ] Update `TenantDirectory.mode = dedicated_db`, `db_alias = <alias>`
- [ ] Update `TenantDirectory.status = active`
- [ ] Disable write freeze
- [ ] Test tenant access (login, read, write)
- [ ] Archive source events (optional, keep for audit period)
- [ ] Create migration log entry

### Rollback (if needed)

- [ ] Set `TenantDirectory.mode = shared`, `db_alias = NULL`
- [ ] Set `TenantDirectory.status = active`
- [ ] Disable write freeze
- [ ] Drop or archive dedicated DB (do not reuse)
- [ ] Document failure reason in migration log
