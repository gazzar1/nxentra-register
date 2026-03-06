# Nxentra Register — Smart ERP Platform

Multi-tenant accounting and ERP platform built with Django REST Framework (backend) and Next.js 14 (frontend).

## Architecture

- **backend/** — Django 4.2 + DRF + PostgreSQL with Row-Level Security (RLS) for tenant isolation. Event-sourced accounting core with CQRS projections.
- **frontend/** — Next.js 14 + TailwindCSS + shadcn/ui. JWT-authenticated SPA with Arabic/English support.

## Key Modules

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
- **Projection consumer**: `python manage.py run_projections --daemon --interval 5` (set `PROJECTIONS_SYNC=False`)
- **Celery worker**: `celery -A nxentra_backend worker -l info`
- **Celery beat**: `celery -A nxentra_backend beat -l info`
- **Frontend**: `npm run build && npm start` or deploy to Vercel

### Pre-Release Validation

```bash
./scripts/security-check.sh    # Secrets, deps, deploy check, authz audit
./scripts/rc-smoke-test.sh     # Health, auth, API, frontend smoke tests
```

## CI/CD

GitHub Actions workflow (`.github/workflows/ci.yml`) runs on every push/PR to `main`:

1. **Backend Tests** — unit + integration on SQLite
2. **Backend E2E** — full tests on PostgreSQL 16
3. **Frontend Build** — Next.js type-check + build
4. **Security Check** — `manage.py check --deploy` + dependency audit
5. **Quality Gate** — all jobs must pass to merge
