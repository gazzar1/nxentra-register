# NXENTRA SYSTEM MAP

> Multi-tenant ERP SaaS — Event-Sourced, CQRS Architecture
> Backend: Django + DRF + Celery | Frontend: Next.js 14 + React 18 + TanStack Query

---

## 1. Apps & Modules

| Module | الوظيفة | يستهلك | ينتج |
|--------|---------|--------|------|
| **accounts** | Auth, Users, Companies, Roles, Permissions, Invitations, Onboarding | JWT tokens, user input | User sessions, membership, permission grants |
| **tenant** | Multi-tenant DB isolation (shared RLS + dedicated DB) | Company ID from JWT | DB routing, RLS context |
| **accounting** | Chart of Accounts, Journal Entries, Customers, Vendors, Exchange Rates, Bank Reconciliation | Commands from views | BusinessEvents → projections update balances |
| **events** | Immutable event store (event sourcing) | Event payloads from commands | Persisted events for projections to consume |
| **projections** | Read models (balances, periods, aging) | BusinessEvents | AccountBalance, PeriodBalance, CustomerBalance, VendorBalance, DimensionBalance, InventoryBalance |
| **sales** | Invoices, Items, Tax Codes, Posting Profiles, Receipt Allocation | User input | Sales events → journal entries |
| **purchases** | Purchase Bills, Vendor Payments | User input | Purchase events → journal entries |
| **inventory** | Warehouses, Stock Ledger, Adjustments | Stock movements | StockLedgerEntry → InventoryBalance projection |
| **scratchpad** | Voice-enabled draft transaction entry (OpenAI Whisper + GPT) | Audio/text input | Draft rows → converted to journal entries |
| **edim** | EDI/Data Import Mapping, Identity Crosswalk | External CSV/data files | Staged records → mapped to internal entities |
| **shopify_connector** | Shopify OAuth sync (orders, payouts, refunds, products) | Shopify API | Synced records + journal entries + reconciliation |
| **stripe_connector** | Stripe sync (charges, payouts, refunds, disputes) | Stripe API | Synced records + journal entries + reconciliation |
| **bank_connector** | Bank statement import & transaction matching | CSV files | BankTransaction + ReconciliationException |
| **properties** | Property/asset management, leases, lessees, units | User input | Property events |
| **clinic** | Healthcare domain (doctors, patients, visits) | User input | Clinic events |
| **ops** | Health checks, Prometheus metrics | HTTP probes | `/_health/`, `/_metrics/` |
| **backups** | Data export/import | Company data | Backup files |

---

## 2. Request Entry Point

```
Client Request
    │
    ▼
Django WSGI/ASGI (nxentra_backend/wsgi.py)
    │
    ▼
Middleware Stack (بالترتيب):
    1. SecurityMiddleware
    2. SessionMiddleware
    3. CorsMiddleware
    4. CommonMiddleware
    5. CsrfViewMiddleware
    6. AuthenticationMiddleware
    7. ★ TenantRlsMiddleware ← يستخرج company_id من JWT، يضبط tenant context
    8. MessageMiddleware
    9. XFrameOptionsMiddleware
    │
    ▼
URL Router (nxentra_backend/urls.py)
    │
    ├── _health/          → ops (K8s probes)
    ├── _metrics/         → ops (Prometheus)
    ├── api/              → accounts (auth, users, companies)
    ├── api/accounting/   → accounting (GL, journals)
    ├── api/sales/        → sales (invoices)
    ├── api/purchases/    → purchases (bills)
    ├── api/inventory/    → inventory (stock)
    ├── api/scratchpad/   → scratchpad (voice, drafts)
    ├── api/reports/      → projections (balances, reports)
    ├── api/edim/         → edim (data import)
    ├── api/properties/   → properties
    ├── api/clinic/       → clinic
    ├── api/shopify/      → shopify_connector
    ├── api/stripe/       → stripe_connector
    ├── api/bank/         → bank_connector
    ├── api/platforms/    → platform_connectors
    ├── api/events/       → events (export, API keys)
    └── api/backups/      → backups
```

---

## 3. Event Sourcing

```
Command (accounting/commands.py)
    │
    ├── validates input + checks policies (accounting/policies.py)
    ├── checks permissions via require(actor, "perm.code")
    │
    ▼
emit_event() (events/emitter.py)
    │
    ├── validates payload schema (events/types.py — 100+ event types)
    ├── stores immutable BusinessEvent + EventPayload
    │
    ▼
Projections consume events (projections/base.py → BaseProjection)
    │
    ├── Each projection declares `consumes = [event_type_list]`
    ├── handle(event) updates read models idempotently
    ├── EventBookmark tracks progress per projection per company
    │
    ▼
Read Models Updated:
    ├── AccountBalance
    ├── PeriodAccountBalance
    ├── CustomerBalance / VendorBalance
    ├── DimensionBalance
    ├── InventoryBalance
    └── FiscalYear / FiscalPeriod
```

**Key Event Types:**
- `journal_entry.created/posted/reversed` — GL lifecycle
- `account.created/updated/deleted` — COA changes
- `fiscal_period.closed/opened` — Period management
- `fiscal_year.closed/reopened` — Year-end
- `receipt_allocation.created` — AR cash application
- `payment_allocation.created` — AP payments
- `user.registered/logged_in` — Auth audit

---

## 4. Projections

| Projection File | يستهلك | ينتج |
|-----------------|--------|------|
| `projections/accounting.py` | Account + JournalEntry events | Account, JournalEntry read models |
| `projections/account_balance.py` | `journal_entry.posted/reversed` | `AccountBalance` (per currency, per dimension) |
| `projections/period_balance.py` | `journal_entry.posted` + period events | `PeriodAccountBalance` |
| `projections/subledger_balance.py` | Receipt/payment allocation events | `CustomerBalance`, `VendorBalance` (aging) |
| `projections/dimension_balance.py` | Journal line analysis events | `DimensionBalance` |
| `projections/inventory_balance.py` | Stock ledger events | `InventoryBalance` (per warehouse) |
| `projections/statistical_entry.py` | Statistical entry events | Statistical read models |
| `projections/accounts.py` | User/company/membership events | User/company read models |

---

## 5. Permissions

**Location:** `accounts/permissions.py`, `accounts/authz.py`, `accounts/permission_defaults.py`

```
Roles: OWNER > ADMIN > USER > VIEWER

OWNER/SUPERUSER → implicit allow (all permissions)
Others          → explicit grants only (CompanyMembershipPermission)

Permission Format: module.action
Examples: accounting.post_journal, sales.create_invoice

Authorization Flow:
    request → resolve_actor(request) → ActorContext{user, company, membership, perms}
    view    → require(actor, "permission.code") → allow or 403
```

**Module Access:** `CompanyModule` controls which modules are visible per company.
Frontend enforces via `ModuleGuard` component.

---

## 6. Tenant Isolation

**Location:** `tenant/models.py`, `tenant/context.py`, `tenant/router.py`, `accounts/middleware.py`, `accounts/rls.py`

```
Mode 1: SHARED (default)
    ├── All tenants in single "default" database
    ├── PostgreSQL RLS policies enforce isolation
    ├── SET app.current_company_id = {id} per connection
    └── Cost-effective SaaS multi-tenant

Mode 2: DEDICATED_DB (premium)
    ├── Tenant has own database (DATABASE_URL_TENANT_{SLUG})
    ├── RLS bypassed (single tenant)
    └── Maximum isolation, compliance-ready

Request Flow:
    JWT → extract company_id → TenantDirectory lookup → set db_alias + is_shared
    → TenantRlsMiddleware sets PostgreSQL session var → TenantDatabaseRouter routes queries

System models (User, Company) → always "default" DB
Tenant models (events, accounting, projections) → context DB alias
```

**Write Barriers** (`projections/write_barrier.py`):
- Commands wrap with `command_writes_allowed()`
- Projections wrap with `projection_writes_allowed()`
- Direct `.save()` outside allowed context → `RuntimeError`

---

## 7. Accounting & Journal Posting

**Location:** `accounting/commands.py`, `accounting/policies.py`

```
Journal Entry Lifecycle:

    INCOMPLETE (created, editable)
        │ save_journal_entry_complete()
        ▼
    COMPLETE (locked for edit)
        │ post_journal_entry()
        ▼
    POSTED (finalized, immutable)
        │ reverse_journal_entry() [optional]
        ▼
    Creates reversal entry (same amounts, opposite debit/credit)
```

**Key Commands:**
- `create/update/delete_account()` — COA management
- `create/update/save_complete/post/reverse/delete_journal_entry()` — Full GL lifecycle
- `record_customer_receipt()` — AR cash application + allocation
- `record_vendor_payment()` — AP payment + allocation
- `close/open_period()` — Period management
- `close/reopen_fiscal_year()` — Year-end (auto-generates closing entries)
- Analysis dimensions: create/update/delete dimension + values + defaults

---

## 8. Reconciliation Logic

**Location:** `accounting/bank_reconciliation.py`, `accounting/bank_views.py`

```
Bank Reconciliation Flow:

    1. Import CSV → parse → BankStatement + BankStatementLines
    2. Auto-Match (confidence scoring):
        100: exact (date + amount + description)
         85: amount + date
         60: amount only
        threshold: 80+ = auto-match
    3. Manual Match: user links bank line → GL journal line
    4. Exclude: mark lines as excluded
    5. Reconcile: validate all matched → mark statement reconciled
```

**Commerce Reconciliation:**
- `shopify_connector/reconciliation.py` — Match Shopify payouts → GL
- `stripe_connector/reconciliation.py` — Match Stripe payouts → GL
- Three-column view: Bank | GL | Commerce (`CommerceReconciliationView`)

---

## 9. Integrations

| Integration | Protocol | Sync Items | Files |
|-------------|----------|------------|-------|
| **Shopify** | OAuth2 | Orders, Products, Refunds, Payouts, Fulfillments, Disputes | `shopify_connector/` |
| **Stripe** | API Key | Charges, Refunds, Payouts, Disputes | `stripe_connector/` |
| **Bank** | CSV Import | Statements, Transactions | `bank_connector/` |
| **EDIM** | File Upload | Generic data import with field mapping + identity crosswalk | `edim/` |
| **Voice/AI** | OpenAI API | Audio → Whisper ASR → GPT parse → structured transaction | `scratchpad/voice_parser.py` |

---

## 10. Top 10 Critical Files

| # | File | لماذا خطير؟ |
|---|------|-------------|
| 1 | `accounting/commands.py` | كل عمليات GL: إنشاء/ترحيل/عكس القيود. خطأ هنا = أرصدة خاطئة |
| 2 | `events/emitter.py` | نقطة إصدار كل الأحداث. تعطل = توقف النظام بالكامل |
| 3 | `projections/account_balance.py` | حساب الأرصدة. خطأ = تقارير مالية خاطئة |
| 4 | `accounts/middleware.py` (TenantRlsMiddleware) | عزل المستأجرين. ثغرة = تسريب بيانات بين الشركات |
| 5 | `tenant/router.py` (TenantDatabaseRouter) | توجيه الاستعلامات للقاعدة الصحيحة. خطأ = كتابة بيانات في قاعدة شركة أخرى |
| 6 | `events/types.py` | تعريف 100+ نوع حدث + validation. تغيير = كسر التوافق |
| 7 | `projections/base.py` | البنية التحتية لكل الـ projections. خطأ = كل القراءات تتوقف |
| 8 | `accounting/policies.py` | قواعد الأعمال (هل يمكن الترحيل؟ الحذف؟). تجاوز = فساد بيانات |
| 9 | `accounts/authz.py` | نظام الصلاحيات. ثغرة = وصول غير مصرح |
| 10 | `projections/write_barrier.py` | حاجز الكتابة. تعطل = كتابة مباشرة بدون أحداث = فقدان مسار التدقيق |

---

## Frontend Architecture (Summary)

```
Next.js 14 (Pages Router) + TypeScript
    │
    ├── /contexts/AuthContext.tsx    → Auth state, JWT tokens, company switching
    ├── /lib/api-client.ts          → Axios + auto-refresh interceptor
    ├── /services/*.service.ts      → API clients per domain (25+ service files)
    ├── /queries/*.ts               → TanStack Query hooks + key factories
    ├── /components/layout/         → AppLayout, Sidebar, Header, CommandPalette
    ├── /components/ui/             → 23 Radix UI components
    ├── /components/forms/          → Domain forms (Account, Journal, Customer...)
    └── /pages/                     → File-based routing (40+ pages)

State: React Context (Auth, Theme, Sidebar) + TanStack React Query (server state)
UI: Tailwind CSS + Radix UI + Lucide icons + Recharts + RTL support (AR/EN)
```
