# Nxentra Operations Playbook

## Table of Contents

1. [System Overview](#system-overview)
2. [Health Checks & Monitoring](#health-checks--monitoring)
3. [Celery Workers](#celery-workers)
4. [Tenant Migration](#tenant-migration)
5. [Disaster Recovery](#disaster-recovery)
6. [Runbooks](#runbooks)

---

## System Overview

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Load Balancer                           │
└─────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           ▼                  ▼                  ▼
    ┌───────────┐      ┌───────────┐      ┌───────────┐
    │  Django   │      │  Django   │      │  Django   │
    │  Worker   │      │  Worker   │      │  Worker   │
    └───────────┘      └───────────┘      └───────────┘
           │                  │                  │
           └──────────────────┼──────────────────┘
                              ▼
    ┌─────────────────────────────────────────────────────────┐
    │                     Redis                                │
    │            (Celery Broker + Channels)                   │
    └─────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           ▼                  ▼                  ▼
    ┌───────────┐      ┌───────────┐      ┌───────────┐
    │  Celery   │      │  Celery   │      │  Celery   │
    │  Worker   │      │  Worker   │      │   Beat    │
    └───────────┘      └───────────┘      └───────────┘
           │                  │                  │
           └──────────────────┼──────────────────┘
                              ▼
    ┌─────────────────────────────────────────────────────────┐
    │                   PostgreSQL                             │
    │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
    │  │   default    │  │ tenant_acme  │  │ tenant_corp  │   │
    │  │   (shared)   │  │ (dedicated)  │  │ (dedicated)  │   │
    │  └──────────────┘  └──────────────┘  └──────────────┘   │
    └─────────────────────────────────────────────────────────┘
```

### Key Components

| Component | Purpose | Scaling |
|-----------|---------|---------|
| Django Workers | HTTP API | Horizontal (stateless) |
| Celery Workers | Async tasks, projections | Horizontal |
| Celery Beat | Periodic tasks | Single instance only |
| Redis | Task broker, channels | Single/Cluster |
| PostgreSQL | System DB + Tenant DBs | Vertical + Read replicas |

---

## Health Checks & Monitoring

### Endpoints

| Endpoint | Purpose | Auth | SLA |
|----------|---------|------|-----|
| `/_health/live` | Liveness probe | No | <10ms |
| `/_health/ready` | Readiness probe | No | <100ms |
| `/_health/full` | Full health report | No* | <1s |
| `/_metrics/` | Prometheus metrics | No* | <500ms |

*Should be protected at network level in production (internal only).

### Kubernetes Probes

```yaml
livenessProbe:
  httpGet:
    path: /_health/live
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /_health/ready
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 5
  failureThreshold: 3
```

### Key Metrics to Monitor

| Metric | Alert Threshold | Description |
|--------|-----------------|-------------|
| `nxentra_projection_lag` | >1000 | Events pending processing |
| `nxentra_request_duration_seconds` | p99 >2s | API latency |
| `nxentra_active_requests` | >100/worker | Request backlog |
| Database connections | >80% pool | Connection exhaustion |

### Grafana Dashboard Queries

```promql
# Projection lag by company
sum by (company_slug) (nxentra_projection_lag)

# API latency p99
histogram_quantile(0.99, rate(nxentra_request_duration_seconds_bucket[5m]))

# Error rate
rate(nxentra_request_duration_seconds_count{status="5xx"}[5m])
```

---

## Celery Workers

### Starting Workers

```bash
# Development (single process with beat)
celery -A nxentra_backend worker -B -l INFO

# Production worker
celery -A nxentra_backend worker \
  -l INFO \
  --concurrency=4 \
  --prefetch-multiplier=1 \
  -Q default,projections

# Production beat (single instance only!)
celery -A nxentra_backend beat \
  -l INFO \
  --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

### Task Queues

| Queue | Tasks | Workers |
|-------|-------|---------|
| `default` | General tasks | 2-4 per host |
| `projections` | Projection processing | 1-2 per host |
| `migrations` | Tenant migrations | 1 (serial) |

### Periodic Tasks

Configure via Django Admin → Periodic Tasks:

| Task | Schedule | Purpose |
|------|----------|---------|
| `projections.tasks.process_all_projections` | Every 1 min | Catch-up processing |
| `projections.tasks.check_projection_health` | Every 5 min | Health monitoring |

### Monitoring Celery

```bash
# Check active tasks
celery -A nxentra_backend inspect active

# Check registered tasks
celery -A nxentra_backend inspect registered

# Check stats
celery -A nxentra_backend inspect stats

# Purge all tasks (emergency only!)
celery -A nxentra_backend purge
```

---

## Tenant Migration

### Overview

Migration moves a tenant from shared (RLS) to dedicated database.

```
SHARED (default DB + RLS) → DEDICATED (own database)
```

### Pre-Migration Checklist

- [ ] Target database created and accessible
- [ ] `DATABASE_URL_TENANT_{NAME}` environment variable set
- [ ] Django migrations applied to target
- [ ] Backup of source data taken
- [ ] Maintenance window scheduled
- [ ] Stakeholders notified

### Migration Command

```bash
# Dry run first
python manage.py migrate_tenant \
  --tenant-slug acme-corp \
  --target-alias tenant_acme \
  --dry-run

# Execute migration
python manage.py migrate_tenant \
  --tenant-slug acme-corp \
  --target-alias tenant_acme \
  --operator "ops-team"
```

### Migration Steps

1. **[1/7] MIGRATING status** - Write freeze begins
2. **[2/7] Export events** - JSON export with hash
3. **[3/7] Run migrations** - Schema on target DB
4. **[4/7] Import events** - Import with verification
5. **[5/7] Replay projections** - Rebuild read models
6. **[6/7] Verify integrity** - Hash, count, trial balance
7. **[7/7] Update directory** - Switch routing

### Verification Failures

If verification fails:
- Tenant remains in SHARED mode
- Status reverts to ACTIVE
- MigrationLog records error
- No data loss occurs

### Post-Migration

```bash
# Verify routing
python manage.py shell -c "
from tenant.models import TenantDirectory
td = TenantDirectory.objects.get(company__slug='acme-corp')
print(f'Mode: {td.mode}, DB: {td.db_alias}')
"

# Check logs
SELECT * FROM tenant_migration_log
WHERE tenant_id = (SELECT id FROM tenant_tenantdirectory WHERE company_id = X)
ORDER BY started_at DESC;
```

---

## Disaster Recovery

### Backup Strategy

| Component | Method | Frequency | Retention |
|-----------|--------|-----------|-----------|
| System DB | pg_dump | Daily | 30 days |
| Tenant DBs | pg_dump | Daily | 30 days |
| Media files | S3 sync | Continuous | 90 days |
| Redis | RDB snapshot | Hourly | 7 days |

### Backup Commands

```bash
# System database
pg_dump -Fc nxentra_default > backup_default_$(date +%Y%m%d).dump

# Tenant database
pg_dump -Fc tenant_acme > backup_tenant_acme_$(date +%Y%m%d).dump

# All databases
for db in nxentra_default tenant_acme tenant_corp; do
  pg_dump -Fc $db > backup_${db}_$(date +%Y%m%d).dump
done
```

### Restore Procedures

#### Full System Restore

```bash
# 1. Stop all services
systemctl stop nxentra-web nxentra-worker nxentra-beat

# 2. Restore system database
pg_restore -d nxentra_default backup_default.dump

# 3. Restore tenant databases
pg_restore -d tenant_acme backup_tenant_acme.dump

# 4. Verify TenantDirectory consistency
python manage.py seed_tenant_directory --dry-run

# 5. Rebuild projections (if needed)
python manage.py replay_projections --all --rebuild

# 6. Restart services
systemctl start nxentra-web nxentra-worker nxentra-beat
```

#### Single Tenant Restore

```bash
# 1. Set tenant to READ_ONLY
python manage.py shell -c "
from tenant.models import TenantDirectory
td = TenantDirectory.objects.get(company__slug='acme-corp')
td.status = TenantDirectory.Status.READ_ONLY
td.save()
"

# 2. Restore database
pg_restore -d tenant_acme backup_tenant_acme.dump

# 3. Replay projections
python manage.py replay_projections --company-slug acme-corp --rebuild

# 4. Set tenant to ACTIVE
python manage.py shell -c "
from tenant.models import TenantDirectory
td = TenantDirectory.objects.get(company__slug='acme-corp')
td.status = TenantDirectory.Status.ACTIVE
td.save()
"
```

### Rollback Tenant Migration

If a tenant migration needs to be rolled back:

```bash
# 1. Verify events still exist in source (default) DB
python manage.py shell -c "
from events.models import BusinessEvent
from accounts.models import Company
company = Company.objects.get(slug='acme-corp')
count = BusinessEvent.objects.using('default').filter(company=company).count()
print(f'Events in default: {count}')
"

# 2. Update TenantDirectory to shared mode
python manage.py shell -c "
from tenant.models import TenantDirectory
td = TenantDirectory.objects.get(company__slug='acme-corp')
td.mode = TenantDirectory.IsolationMode.SHARED
td.db_alias = 'default'
td.status = TenantDirectory.Status.ACTIVE
td.migrated_at = None
td.save()
"

# 3. Clear middleware cache
# (Restart Django workers or call invalidate_tenant_cache)
```

---

## Runbooks

### RB-001: High Projection Lag

**Symptoms:**
- `nxentra_projection_lag` > 1000
- Dashboard data is stale
- User reports of missing data

**Diagnosis:**
```bash
# Check lag per projection
python manage.py shell -c "
from events.metrics import get_projection_lag_metrics
for m in get_projection_lag_metrics():
    if m['lag'] > 0:
        print(f\"{m['consumer_name']} ({m['company_name']}): {m['lag']} behind\")
"

# Check for paused projections
SELECT * FROM events_eventbookmark WHERE is_paused = true;

# Check for errors
SELECT consumer_name, company_id, error_count, last_error
FROM events_eventbookmark
WHERE error_count > 0;
```

**Resolution:**
```bash
# 1. Resume paused projections
python manage.py shell -c "
from events.models import EventBookmark
EventBookmark.objects.filter(is_paused=True).update(is_paused=False)
"

# 2. Clear errors and retry
python manage.py shell -c "
from events.models import EventBookmark
EventBookmark.objects.filter(error_count__gt=0).update(error_count=0, last_error='')
"

# 3. Trigger catch-up processing
celery -A nxentra_backend call projections.tasks.process_all_projections

# 4. If still failing, rebuild projection
python manage.py replay_projections --company-slug acme-corp --rebuild
```

### RB-002: Database Connection Exhaustion

**Symptoms:**
- "too many connections" errors
- API requests timing out
- Health check failures

**Diagnosis:**
```sql
-- Check active connections
SELECT count(*), state, usename, application_name
FROM pg_stat_activity
GROUP BY state, usename, application_name
ORDER BY count DESC;

-- Check connection limits
SHOW max_connections;
```

**Resolution:**
```bash
# 1. Scale down workers temporarily
kubectl scale deployment nxentra-worker --replicas=1

# 2. Kill idle connections
psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle' AND query_start < now() - interval '10 minutes';"

# 3. Increase pool size (if capacity allows)
# Edit DATABASE settings: conn_max_age, CONN_MAX_AGE_SECONDS

# 4. Scale workers back up
kubectl scale deployment nxentra-worker --replicas=4
```

### RB-003: TenantDirectory Inconsistency

**Symptoms:**
- Startup fails with "TENANT HEALTH CHECK FAILED"
- Routing errors for specific companies
- 500 errors on company switch

**Diagnosis:**
```bash
python manage.py seed_tenant_directory --dry-run
```

**Resolution:**
```bash
# Create missing entries
python manage.py seed_tenant_directory

# Verify
python manage.py shell -c "
from accounts.models import Company
from tenant.models import TenantDirectory
companies = Company.objects.count()
tenants = TenantDirectory.objects.count()
print(f'Companies: {companies}, TenantDirectory: {tenants}')
assert companies == tenants, 'Mismatch!'
print('OK')
"
```

### RB-004: Event Store Corruption

**Symptoms:**
- Hash verification failures during migration
- Missing events in sequence
- Projection rebuilds fail

**Diagnosis:**
```sql
-- Check for gaps in company_sequence
SELECT company_id,
       company_sequence,
       LAG(company_sequence) OVER (PARTITION BY company_id ORDER BY company_sequence) as prev_seq,
       company_sequence - LAG(company_sequence) OVER (PARTITION BY company_id ORDER BY company_sequence) as gap
FROM events_businessevent
WHERE company_id = X
HAVING gap > 1;

-- Verify event integrity
SELECT event_type, COUNT(*),
       COUNT(DISTINCT idempotency_key) as unique_keys
FROM events_businessevent
WHERE company_id = X
GROUP BY event_type;
```

**Resolution:**
```bash
# If sequence gaps exist but events are not missing (just misnumbered):
# This is cosmetic - projections should still work

# If events are actually missing:
# 1. Restore from backup
# 2. Replay events from source system (if available)
# 3. Rebuild projections
python manage.py replay_projections --company-slug acme-corp --rebuild
```

---

## Environment Variables

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_URL` | System database | `postgresql://...` |
| `DJANGO_SECRET_KEY` | Django secret | `random-string` |
| `REDIS_URL` | Celery broker | `redis://localhost:6379/0` |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `DJANGO_DEBUG` | `False` | Debug mode |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FORMAT` | `json` | `json` or `console` |
| `TENANT_HEALTH_CHECK` | `error` | `error`, `warn`, `skip` |
| `PROJECTION_LAG_THRESHOLD` | `1000` | Alert threshold |
| `APP_VERSION` | `dev` | Deployment version |

### Tenant Databases

```bash
DATABASE_URL_TENANT_ACME=postgresql://user:pass@host:5432/tenant_acme
DATABASE_URL_TENANT_CORP=postgresql://user:pass@host:5432/tenant_corp
```

---

## Contact & Escalation

| Level | Contact | Response Time |
|-------|---------|---------------|
| L1 | On-call engineer | 15 min |
| L2 | Backend team lead | 1 hour |
| L3 | CTO / Architecture | 4 hours |

### Incident Severity

| Severity | Definition | Examples |
|----------|------------|----------|
| P1 | Service down | All users affected |
| P2 | Degraded | Single tenant down |
| P3 | Minor | Feature unavailable |
| P4 | Low | Cosmetic issues |
