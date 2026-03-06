# Nxentra Architecture Map

One-page reference for the command → event → projection pipeline.

## Data Flow

```
User Action (browser)
       │
       ▼
   API Layer (views.py)
       │  ← permission checks, request parsing
       ▼
   Command Layer (commands.py)
       │  ← policy validation, business rules
       │  ← creates/modifies domain objects
       ▼
   Event Emission (emit_event)
       │  ← payload validation against schema
       │  ← LEPH: inline if <64KB, external if >64KB
       │  ← idempotency_key dedup (unique constraint)
       │  ← company_sequence allocated (gap-free, monotonic)
       ▼
   Event Store (BusinessEvent)
       │  ← immutable append-only log per company
       │  ← source of truth for all state
       ▼
   Projection Engine (process_pending)
       │  ← EventBookmark tracks cursor per projection
       │  ← ProjectionAppliedEvent prevents double-processing
       │  ← transaction.atomic() per event
       ▼
   Read Models (AccountBalance, CustomerBalance, etc.)
       │  ← materialized views, rebuildable from events
       ▼
   Reports & UI (trial balance, aging, statements)
```

## Modules

### Accounting (`backend/accounting/`)

| Layer | Key Components |
|---|---|
| **Views** | `AccountListCreateView`, `JournalEntryListCreateView`, `JournalPostView`, `JournalReverseView` |
| **Commands** | `create_account`, `post_journal_entry`, `reverse_journal_entry`, `close_period`, `close_fiscal_year` |
| **Events** | `journal_entry.posted`, `journal_entry.reversed`, `fiscal_period.closed`, `fiscal_year.closed` |
| **Policies** | `can_post_entry`, `can_post_to_period`, `validate_subledger_tieout`, `validate_line_counterparty` |
| **Invariants** | Truth (replay, idempotency, reversal), Control (period gating, subledger tie-out) |

### Sales (`backend/sales/`)

| Layer | Key Components |
|---|---|
| **Views** | `SalesInvoiceListCreateView`, `SalesInvoicePostView`, `SalesInvoiceVoidView` |
| **Commands** | `create_sales_invoice`, `post_sales_invoice`, `void_sales_invoice` |
| **Events** | `sales.invoice_posted`, `sales.invoice_voided` |
| **Flow** | Post invoice → creates JE → emits `journal_entry.posted` → updates GL + AR subledger |

### Purchases (`backend/purchases/`)

| Layer | Key Components |
|---|---|
| **Views** | `PurchaseBillListCreateView`, `PurchaseBillPostView`, `PurchaseBillVoidView` |
| **Commands** | `create_purchase_bill`, `post_purchase_bill`, `void_purchase_bill` |
| **Events** | `purchases.bill_posted`, `purchases.bill_voided` |
| **Flow** | Post bill → creates JE + stock receipt → updates GL + AP subledger + inventory |

### Inventory (`backend/inventory/`)

| Layer | Key Components |
|---|---|
| **Views** | `WarehouseViewSet`, `InventoryAdjustmentViewSet`, `StockAvailabilityViewSet` |
| **Commands** | `record_stock_receipt`, `record_stock_issue`, `adjust_inventory`, `record_opening_balance` |
| **Events** | `inventory.stock_received`, `inventory.stock_issued`, `inventory.adjusted` |
| **Flow** | Stock movement → emits inventory event → updates `InventoryBalance` (qty, avg_cost, value) |

### Accounts (`backend/accounts/`)

| Layer | Key Components |
|---|---|
| **Views** | User registration, company creation, membership management |
| **Commands** | `register_signup`, `create_company`, `update_membership_role`, `grant_permission` |
| **Events** | `user.registered`, `company.created`, `membership.created`, `permission.granted` |

## Projections

### Financial Projections (invariant-tested)

| Projection | Consumes | Writes To | File |
|---|---|---|---|
| `AccountBalanceProjection` | `journal_entry.posted` | `AccountBalance` | `projections/account_balance.py` |
| `SubledgerBalanceProjection` | `journal_entry.posted` | `CustomerBalance`, `VendorBalance` | `projections/subledger_balance.py` |
| `PeriodAccountBalanceProjection` | `journal_entry.posted` | `PeriodAccountBalance` | `projections/period_balance.py` |
| `InventoryBalanceProjection` | `inventory.stock_*` | `InventoryBalance` | `projections/inventory_balance.py` |

### Structural Projections

| Projection | Consumes | Writes To | File |
|---|---|---|---|
| `FiscalPeriodProjection` | `fiscal_period.*`, `fiscal_year.*` | `FiscalPeriod`, `FiscalYear` | `projections/periods.py` |
| `AccountProjection` | `account.*` | `Account` | `projections/accounting.py` |
| `JournalEntryProjection` | `journal_entry.*` | `JournalEntry`, `JournalLine` | `projections/accounting.py` |
| `CompanyProjection` | `company.*` | `Company` | `projections/accounts.py` |
| `MembershipProjection` | `membership.*` | `CompanyMembership` | `projections/accounts.py` |
| `StatisticalEntryProjection` | `statistical.*` | `StatisticalEntry` | `projections/statistical_entry.py` |

## Key Architectural Flows

### Invoice Posting (Sales)

```
post_sales_invoice
  → validate: period open, account postable, lines balanced
  → create JournalEntry (POSTED)
  → emit journal_entry.posted
  → AccountBalanceProjection: update GL balances
  → SubledgerBalanceProjection: update CustomerBalance (AR)
  → PeriodAccountBalanceProjection: update period balances
```

### Bill Posting (Purchases)

```
post_purchase_bill
  → validate: period open, account postable, lines balanced
  → create JournalEntry (POSTED)
  → emit journal_entry.posted + inventory.stock_received
  → AccountBalanceProjection: update GL balances
  → SubledgerBalanceProjection: update VendorBalance (AP)
  → InventoryBalanceProjection: update qty_on_hand, avg_cost
```

### Year-End Close

```
close_fiscal_year
  → validate: all periods closed, TB balanced
  → emit fiscal_year.closed
  → FiscalPeriodProjection: mark year CLOSED
  → can_post_to_period: rejects future postings to closed year
```

## Idempotency & Safety

| Mechanism | Location | Purpose |
|---|---|---|
| `idempotency_key` | `BusinessEvent` unique constraint | Prevents duplicate event emission |
| `ProjectionAppliedEvent` | `BaseProjection.process_pending()` | Prevents double-processing per projection |
| `company_sequence` | `BusinessEvent` monotonic counter | Gap-free ordering for replay |
| `EventBookmark` | Per-projection cursor | Tracks last processed event |
| `select_for_update()` | Balance projections | Row-level locking during updates |
| `transaction.atomic()` | Per-event processing | All-or-nothing per event |

See [projection-idempotency.md](projection-idempotency.md) for detailed explanation.

## Invariant Protection

| Suite | File | Tests | Scope |
|---|---|---|---|
| Truth Invariants | `tests/test_truth_invariants.py` | 11 | Math, replay, idempotency |
| Control Invariants | `tests/test_control_invariants.py` | 11 | Period gating, subledger tie-out, LEPH |
| Runtime Invariants | `tests/test_runtime_invariants.py` | 9 | Concurrency, crash recovery, lag |
| LEPH Tests | `tests/test_leph_*.py` | 7 | External payload storage |

See [core-assurance-baseline.md](core-assurance-baseline.md) for what is and is not covered.

## File Structure

```
backend/
├── accounting/
│   ├── commands.py      ← journal entry, account, period, fiscal year commands
│   ├── models.py        ← Account, JournalEntry, JournalLine, Customer, Vendor
│   ├── policies.py      ← all business rule validation
│   ├── views.py         ← REST API endpoints
│   └── behaviors.py     ← account type/role validation
├── sales/
│   ├── commands.py      ← invoice, item, tax code commands
│   ├── models.py        ← SalesInvoice, Item, TaxCode, PostingProfile
│   └── views.py
├── purchases/
│   ├── commands.py      ← purchase bill commands
│   ├── models.py        ← PurchaseBill
│   └── views.py
├── inventory/
│   ├── commands.py      ← stock receipt/issue, warehouse commands
│   ├── models.py        ← Warehouse, StockLedgerEntry
│   └── views.py
├── events/
│   ├── emitter.py       ← emit_event() with LEPH, validation, idempotency
│   ├── models.py        ← BusinessEvent, EventBookmark, EventPayloadExternal
│   ├── types.py         ← EventTypes constants + payload schemas
│   └── payload_policy.py ← LEPH inline/external routing
├── projections/
│   ├── base.py          ← BaseProjection, ProjectionRegistry, process_pending
│   ├── models.py        ← AccountBalance, CustomerBalance, VendorBalance, etc.
│   ├── account_balance.py
│   ├── subledger_balance.py
│   ├── period_balance.py
│   ├── inventory_balance.py
│   └── periods.py
├── accounts/
│   ├── commands.py      ← user, company, membership commands
│   ├── models.py        ← User, Company, CompanyMembership
│   ├── authz.py         ← ActorContext
│   └── rls.py           ← row-level security
└── tests/
    ├── test_truth_invariants.py
    ├── test_control_invariants.py
    ├── test_runtime_invariants.py
    ├── test_leph_e2e_projection.py
    └── test_leph_safety.py
```
