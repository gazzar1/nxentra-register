# Nxentra Register

Nxentra is a **commerce accounting control plane and financial truth engine**: it connects sales channels, fulfillment/courier evidence, payment settlements, and banks to a double-entry general ledger, and reconciles them with explained differences.

The initial supported contract is the **constrained isolated shadow-ledger pilot** — [`ISOLATED_SHADOW_LEDGER_V1`](docs/architecture/supported-product-contracts.md). Everything else in this repository is implementation surface, not supported product scope (see [Modules present in the repository](#modules-present-in-the-repository) below).

Built with Django REST Framework (backend) and Next.js 14 (frontend).

## Architecture

- **backend/** — Django 4.2 + DRF + PostgreSQL with Row-Level Security (RLS) for tenant isolation. Event-sourced accounting core with CQRS projections.
- **frontend/** — Next.js 14 + TailwindCSS + shadcn/ui. JWT-authenticated SPA with Arabic/English support.

### Architecture governance

- [Architecture constitution](docs/architecture/architecture-constitution.md) — six binding rules + exception/ratchet policy
- [Canonical money spine](docs/architecture/canonical-money-spine.md) — writers, readers, and legacy paths for every financial fact
- [Supported product contracts](docs/architecture/supported-product-contracts.md) — `ISOLATED_SHADOW_LEDGER_V1` as implemented
- [Architecture Decision Records](docs/adr/) — ADRs + [template](docs/adr/template.md); governance baseline in [ADR-0003](docs/adr/0003-architecture-constitution-governance.md)
- [Constrained-pilot status tracker](docs/status/constrained_pilot_status.md) — authoritative A1–A5/G1–G2 status

## Modules present in the repository

The table below lists what **exists in the codebase**. It is **not** the supported pilot scope. [`ISOLATED_SHADOW_LEDGER_V1`](docs/architecture/supported-product-contracts.md) supports: one merchant, one owner, one active Shopify store, EGP only, Shopify orders/refunds, Paymob/Bosta settlement evidence, expected bank deposits, bank CSV, general ledger, reconciliation/exceptions — **shadow accounting only**. Inventory accounting, purchasing accounting, multi-currency input, multiple users, Stripe, Shopify Payments payout accounting, disputes, rebuild/replay as a recovery mechanism, and legacy banking routes are **explicitly unsupported in the pilot** regardless of their presence below; runtime pilot gates enforce this.

| Module | Description |
|---|---|
| **Accounting** | Double-entry journal entries, chart of accounts, fiscal periods (13-period), year-end close |
| **Sales** | Sales invoices, customer AR subledger, receipts |
| **Purchases** | Purchase bills, vendor AP subledger, payments |
| **Inventory** | Warehouses, items, stock balances, adjustments, opening balance |
| **Reports** | Trial balance, balance sheet, income statement, cash flow, AR/AP aging, account inquiry |
| **Analysis Dimensions** | Configurable cost centers / departments / projects with per-account defaults |
| **Tax** | Tax codes with configurable rates and posting profiles |
| **Scratchpad** | Quick journal entry drafting with voice input (OpenAI) |
| **Admin** | Multi-tenant company management, user roles & permissions, audit log |

## Local Development

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # Edit with your DB credentials
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The app is available at `http://localhost:3000`. Set `NEXT_PUBLIC_API_URL=http://localhost:8000/api` in `frontend/.env.local`.

## Testing

```bash
# Backend unit/integration tests (SQLite, fast)
cd backend && python -m pytest tests/ accounting/tests/ events/tests/ accounts/tests/ --ignore=tests/e2e/

# Backend e2e tests (requires PostgreSQL)
TEST_DATABASE_URL=postgres://user:pass@localhost:5432/nxentra_test python -m pytest tests/e2e/

# Frontend build check
cd frontend && npm run build
```

## Production Deployment

### Required Environment Variables

| Variable | Example |
|---|---|
| `SECRET_KEY` | Long random string (≥50 chars) |
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | `app.nxentra.com` |
| `DATABASE_URL` | `postgres://user:pass@host:5432/nxentra` |
| `CORS_ALLOWED_ORIGINS` | `https://app.nxentra.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://app.nxentra.com` |
| `REDIS_URL` | `redis://host:6379/0` |
| `NEXT_PUBLIC_API_URL` | `https://api.nxentra.com/api` |

### Security Checklist

When `DEBUG=False`, the following are enforced automatically:
- `SECURE_SSL_REDIRECT`, HSTS (1 year, preload-ready)
- Secure session & CSRF cookies
- SECRET_KEY validation (rejects default `changeme`)
- CORS/CSRF origin validation (rejects localhost entries)

Run the deploy check: `python manage.py check --deploy` — must return **0 warnings**.

### Services

- **Backend**: Gunicorn/Uvicorn behind nginx with HTTPS
- **Projections**: run synchronously in-process — `PROJECTIONS_SYNC=True` is REQUIRED in production and asserted at boot (A162). `run_projections --daemon` remains a catch-up supplement only.
- **Celery worker**: `celery -A nxentra_backend worker -l info`
- **Celery beat**: `celery -A nxentra_backend beat -l info`
- **Frontend**: `npm run build && npm start` under pm2 — the controlled procedure is [docs/runbook-frontend-deploy.md](docs/runbook-frontend-deploy.md). Production deployments are controlled and documented; do not blind-pull onto the production host.

### Pre-Release Validation

```bash
./scripts/security-check.sh    # Secrets, deps, deploy check, authz audit
./scripts/rc-smoke-test.sh     # Health, auth, API, frontend smoke tests
```

## CI/CD

GitHub Actions on every push/PR to `main`:

- **CI workflow** (`.github/workflows/ci.yml`) — seven jobs: Backend Tests (SQLite), Backend Invariants (Postgres), Backend E2E Tests (Postgres), Frontend Tests & Build, Lint & Type Check, Security & Deploy Check, and Quality Gate (the aggregator job).
- **PR Architecture Contract** (`.github/workflows/pr-architecture-contract.yml`) — a separate workflow validating the enforced pull-request template ([ADR-0003](docs/adr/0003-architecture-constitution-governance.md)).

Two checks are **required by branch protection** to merge into `main`, listed distinctly:

1. **Quality Gate**
2. **PR Architecture Contract**
