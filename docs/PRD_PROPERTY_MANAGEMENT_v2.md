# Nxentra Property Management Module — Refined PRD (Phase 1)

**Version:** 2.1 (Critical Decisions Locked)
**Date:** March 6, 2026
**Owner:** Product
**Audience:** Backend developer, frontend developer, QA, tech lead

---

## 1. Product Goal

Build a production-grade Property Management module for Nxentra that manages
operational property workflows and derives accounting outcomes through Nxentra's
event-sourced command/projection architecture.

This module must prove:

- Tenant-scoped operational workflows with `ProjectionWriteGuard` and `command_writes_allowed`.
- Operational-to-financial mapping through commands → events → accounting projections.
- Recurring billing and collection lifecycle.
- Dimension-based profitability reporting via existing `AnalysisDimension` system.

---

## 2. Design Principles

1. **Operational records are primary; accounting entries are derived.**
   Financial JournalEntries are created by projections consuming property events,
   never by direct controller/view writes.
2. **All writes go through the command layer** (`properties/commands.py`) and emit
   canonical events via `emit_event()`. Commands use `@transaction.atomic`,
   `ActorContext`, and `require()` for authorization — same pattern as `sales/commands.py`.
3. **Models extend `ProjectionWriteGuard`** with `allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}` — same pattern as `SalesInvoice`.
4. **Financial projections are rebuildable** from the event stream.
5. **Status changes follow explicit state machines** enforced in commands, never in serializers or views.
6. **Single currency per lease.** Multi-currency is out of scope for Phase 1. Currency on lease/payment/deposit must match the company's base currency.

---

## 3. In Scope (Phase 1)

- Property and unit master data
- Lessee (property tenant) master data
- Lease contracts with state machine
- Rent schedule generation from lease terms
- Rent due lifecycle (upcoming → due → overdue → paid)
- Payment receipt and allocation to schedule lines
- Security deposit lifecycle (receive, adjust, refund, forfeit)
- Property expenses
- Lease expiry alerts (90/60/30 days)
- Account mapping configuration (per-tenant GL account assignment)
- Core dashboard widgets and reports
- Event emission + accounting projection for all Phase 1 operations

## 4. Out of Scope (Phase 1)

- Maintenance tickets / work orders
- Vendor management workflows
- Payroll allocation to properties
- WhatsApp / SMS reminders
- Tenant portal
- Online payment gateways
- OCR / AI document processing
- Owner distributions / profit sharing
- Multi-currency leases
- Lease escalation clauses (auto rent increase)

---

## 5. Naming Convention: "Lessee" not "Tenant"

> **Critical:** Nxentra uses "tenant" to mean a company/organization in the
> multi-tenant isolation layer (`TenantDirectory`, `tenant_context`, `TenantDatabaseRouter`).
> The property renter MUST be called **`Lessee`** in code, models, APIs, and UI
> to avoid confusion. The PRD uses "lessee" throughout.

---

## 6. User Roles (Tenant-Scoped)

Roles use Nxentra's existing `CompanyMembership.role` + permission system.

| Role | Key Permissions |
|------|----------------|
| Super Admin / Owner | All permissions |
| Property Manager | `properties.manage`, `units.manage`, `lessees.manage`, `leases.manage`, `expenses.manage`, `alerts.manage` |
| Collections Officer | `collections.receive`, `deposits.manage`, `leases.view` |
| Accountant | `reports.view`, `exports.download`, read-only on all operational data |
| Read-Only Owner | `reports.view` only |

Permission actions to register:

```
properties.manage, properties.view
units.manage, units.view
lessees.manage, lessees.view
leases.manage, leases.view
collections.receive, collections.view
deposits.manage, deposits.view
expenses.manage, expenses.view
reports.view
alerts.manage
exports.download
```

---

## 7. Core Entities (Phase 1)

All models live in a single Django app: **`properties`**
(`backend/properties/`), registered in `INSTALLED_APPS` and added to
`TenantDatabaseRouter.TENANT_APPS`.

Model files split for readability:
- `properties/models/property.py` — Property, Unit
- `properties/models/lessee.py` — Lessee
- `properties/models/lease.py` — Lease, RentScheduleLine
- `properties/models/payment.py` — PaymentReceipt, PaymentAllocation
- `properties/models/deposit.py` — SecurityDepositTransaction
- `properties/models/expense.py` — PropertyExpense
- `properties/models/config.py` — PropertyAccountMapping

### 7.1 Property

```python
class Property(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company           = FK(Company)
    public_id         = UUIDField(unique=True)
    code              = CharField(max_length=20)       # unique per company
    name              = CharField(max_length=255)
    name_ar           = CharField(blank=True)
    property_type     = CharField(choices=PropertyType)  # see enum below
    owner_entity_ref  = CharField(nullable)             # external owner reference
    address           = TextField(blank=True)
    city              = CharField(blank=True)
    region            = CharField(blank=True)
    country           = CharField(default="SA")
    status            = CharField(choices=PropertyStatus)  # active, inactive
    acquisition_date  = DateField(nullable)
    area_sqm          = DecimalField(nullable)
    valuation         = DecimalField(nullable)
    notes             = TextField(blank=True)
    created_at, updated_at
```

**PropertyType enum:**
`residential_building, apartment_block, villa, office_building, warehouse, retail, land, mixed_use`

**PropertyStatus enum:**
`active, inactive`

**Constraints:**
- `UniqueConstraint(company, code)` — `uniq_property_code_per_company`

### 7.2 Unit

```python
class Unit(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company           = FK(Company)
    property          = FK(Property, related_name="units")
    public_id         = UUIDField(unique=True)
    unit_code         = CharField(max_length=20)       # unique per property
    floor             = CharField(nullable)
    unit_type         = CharField(choices=UnitType)
    bedrooms          = SmallIntegerField(nullable)
    bathrooms         = SmallIntegerField(nullable)
    area_sqm          = DecimalField(nullable)
    status            = CharField(choices=UnitStatus)   # see state machine
    default_rent      = DecimalField(nullable)
    notes             = TextField(blank=True)
    created_at, updated_at
```

**UnitType enum:**
`apartment, office, shop, warehouse_bay, room, parking, other`

**UnitStatus enum:**
`vacant, reserved, occupied, under_maintenance, inactive`

**Constraints:**
- `UniqueConstraint(property, unit_code)` — `uniq_unit_code_per_property`

### 7.3 Lessee

> Named `Lessee` to avoid collision with Nxentra's system-level "Tenant".

```python
class Lessee(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company           = FK(Company)
    public_id         = UUIDField(unique=True)
    code              = CharField(max_length=20)       # unique per company
    lessee_type       = CharField(choices=LesseeType)  # individual, company
    display_name      = CharField(max_length=255)
    national_id       = CharField(nullable)            # national ID or CR number
    phone             = CharField(nullable)
    whatsapp          = CharField(nullable)
    email             = EmailField(nullable)
    address           = TextField(nullable)
    emergency_contact = CharField(nullable)
    status            = CharField(choices=LesseeStatus)  # active, inactive, blacklisted
    risk_rating       = CharField(choices=RiskRating, nullable)  # low, medium, high
    notes             = TextField(blank=True)
    created_at, updated_at
```

**Constraints:**
- `UniqueConstraint(company, code)` — `uniq_lessee_code_per_company`

### 7.4 Lease

```python
class Lease(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company              = FK(Company)
    public_id            = UUIDField(unique=True)
    contract_no          = CharField(max_length=50)      # unique per company
    property             = FK(Property)
    unit                 = FK(Unit, nullable)             # null = whole-property lease
    lessee               = FK(Lessee)
    start_date           = DateField
    end_date             = DateField
    handover_date        = DateField(nullable)
    payment_frequency    = CharField(choices=PaymentFrequency)
    rent_amount          = DecimalField(max_digits=18, decimal_places=2)
    currency             = CharField(max_length=3, default="SAR")
    grace_days           = IntegerField(default=0)
    due_day_rule         = CharField(choices=DueDayRule)
    specific_due_day     = SmallIntegerField(nullable)
    deposit_amount       = DecimalField(default=0)
    status               = CharField(choices=LeaseStatus)  # see state machine
    renewed_from_lease   = FK("self", nullable)            # link to previous lease
    renewal_option       = BooleanField(default=False)
    notice_period_days   = IntegerField(nullable)
    terms_summary        = TextField(nullable)
    document_ref         = CharField(nullable)
    activated_at         = DateTimeField(nullable)
    terminated_at        = DateTimeField(nullable)
    termination_reason   = TextField(nullable)
    created_at, updated_at
```

**PaymentFrequency enum:**
`monthly, quarterly, semiannual, annual`

**DueDayRule enum:**
`first_day, specific_day`

**LeaseStatus enum:**
`draft, active, expired, terminated, renewed`

**Constraints:**
- `UniqueConstraint(company, contract_no)` — `uniq_lease_contract_per_company`
- **No-overlap rule:** enforced in command layer, not DB constraint (requires date range check)

### 7.5 Rent Schedule Line

```python
class RentScheduleLine(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company           = FK(Company)
    lease             = FK(Lease, related_name="schedule_lines")
    public_id         = UUIDField(unique=True)
    installment_no    = PositiveIntegerField
    period_start      = DateField
    period_end        = DateField
    due_date          = DateField
    base_rent         = DecimalField
    adjustments       = DecimalField(default=0)
    penalties         = DecimalField(default=0)
    total_due         = DecimalField
    total_allocated   = DecimalField(default=0)
    outstanding       = DecimalField           # = total_due - total_allocated
    status            = CharField(choices=ScheduleStatus)
    posted_event_id   = UUIDField(nullable)    # event that created the AR entry
    created_at, updated_at
```

**ScheduleStatus enum:**
`upcoming, due, overdue, partially_paid, paid, waived`

**Constraints:**
- `UniqueConstraint(lease, installment_no)` — `uniq_schedule_installment`

### 7.6 Payment Receipt

```python
class PaymentReceipt(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company              = FK(Company)
    public_id            = UUIDField(unique=True)
    receipt_no           = CharField(max_length=50)     # unique per company
    lessee               = FK(Lessee)
    lease                = FK(Lease)
    payment_date         = DateField
    amount               = DecimalField
    currency             = CharField(max_length=3)
    method               = CharField(choices=PaymentMethod)
    reference_no         = CharField(nullable)
    received_by          = FK(User)
    notes                = TextField(nullable)
    allocation_status    = CharField(choices=AllocationStatus)
    voided               = BooleanField(default=False)
    voided_at            = DateTimeField(nullable)
    voided_reason        = TextField(nullable)
    created_at, updated_at
```

**PaymentMethod enum:**
`cash, bank_transfer, cheque, wallet`

**AllocationStatus enum:**
`unallocated, partially_allocated, fully_allocated`

**Constraints:**
- `UniqueConstraint(company, receipt_no)` — `uniq_receipt_no_per_company`

### 7.7 Payment Allocation

```python
class PaymentAllocation(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company           = FK(Company)
    public_id         = UUIDField(unique=True)
    payment           = FK(PaymentReceipt, related_name="allocations")
    schedule_line     = FK(RentScheduleLine, related_name="allocations")
    allocated_amount  = DecimalField
    created_at
```

**Constraints:**
- `UniqueConstraint(payment, schedule_line)` — `uniq_allocation_per_payment_line`

### 7.8 Security Deposit Transaction

```python
class SecurityDepositTransaction(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company            = FK(Company)
    public_id          = UUIDField(unique=True)
    lease              = FK(Lease, related_name="deposit_transactions")
    transaction_type   = CharField(choices=DepositTransactionType)
    amount             = DecimalField
    currency           = CharField(max_length=3)
    transaction_date   = DateField
    reason             = TextField(nullable)
    reference          = CharField(nullable)
    created_at, updated_at
```

**DepositTransactionType enum:**
`received, adjusted, refunded, forfeited`

### 7.9 Property Expense

```python
class PropertyExpense(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company           = FK(Company)
    public_id         = UUIDField(unique=True)
    property          = FK(Property)
    unit              = FK(Unit, nullable)
    category          = CharField(choices=ExpenseCategory)
    vendor_ref        = CharField(nullable)
    expense_date      = DateField
    amount            = DecimalField
    currency          = CharField(max_length=3)
    payment_mode      = CharField(choices=PaymentMode)
    paid_status       = CharField(choices=PaidStatus)
    description       = TextField(nullable)
    document_ref      = CharField(nullable)
    created_at, updated_at
```

**ExpenseCategory enum:**
`maintenance, utilities, cleaning, security, salary, tax, insurance, legal, marketing, other`

**PaymentMode enum:**
`cash_paid, credit`

**PaidStatus enum:**
`unpaid, paid, partially_paid`

### 7.10 Property Account Mapping (NEW — not in original PRD)

Each tenant configures which GL accounts to use for property accounting entries.
Without this, projections cannot create journal entries.

```python
class PropertyAccountMapping(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    company                        = OneToOneField(Company)
    public_id                      = UUIDField(unique=True)

    # Revenue
    rental_income_account          = FK(Account, nullable)
    other_income_account           = FK(Account, nullable)    # forfeit, penalties

    # Assets
    accounts_receivable_account    = FK(Account, nullable)    # AR for rent dues
    cash_bank_account              = FK(Account, nullable)    # default cash/bank
    unapplied_cash_account         = FK(Account, nullable)    # unapplied cash holding

    # Liabilities
    security_deposit_account       = FK(Account, nullable)    # deposit held
    accounts_payable_account       = FK(Account, nullable)    # credit expenses

    # Expenses
    property_expense_account       = FK(Account, nullable)    # default expense

    created_at, updated_at
```

**Validation rule:** Lease activation MUST fail if the required accounts
(`rental_income_account`, `accounts_receivable_account`) are not configured.
Payments MUST fail if `cash_bank_account` and `unapplied_cash_account` are not configured.

---

## 8. State Machines

### 8.1 Lease Status Transitions

```
draft → active         (activate_lease command)
active → expired       (system: end_date passed, or manual expire)
active → terminated    (terminate_lease command, requires reason)
active → renewed       (renew_lease command, creates new lease)
```

- `renewed` is a **terminal state** for the old lease. A new `draft` lease is created with `renewed_from_lease` pointing to the old one.
- Invalid transitions MUST be rejected in the command layer with a clear error.

### 8.2 Unit Status Transitions

```
vacant → reserved              (reserve_unit)
vacant → occupied              (lease activation)
reserved → occupied            (lease activation)
reserved → vacant              (cancel reservation)     ← ADDED
occupied → vacant              (lease termination/expiry)
occupied → under_maintenance   (maintenance command)
under_maintenance → vacant     (maintenance complete)
* → inactive                   (privileged deactivation only)
```

### 8.3 Schedule Line Status Transitions

```
upcoming → due                 (when due_date <= today, via daily Celery task)
due → overdue                  (when due_date + grace_days < today, via daily Celery task)
due → partially_paid           (partial payment allocation)
due → paid                     (full payment allocation)
overdue → partially_paid       (partial payment allocation)
overdue → paid                 (full payment allocation)
partially_paid → paid          (remaining balance allocated)
due/overdue → waived           (manual waive with audit reason)
```

### 8.4 Payment Receipt Lifecycle

```
active (default)
active → voided                (void_payment command, requires reason)
```

When voided: reverse all allocations, reopen affected schedule lines,
emit `rent.payment_voided` event which triggers reversing journal entry.

---

## 9. Commands and Events

### 9.1 Command Pattern

All commands follow the existing `sales/commands.py` pattern:

```python
@transaction.atomic
def activate_lease(actor: ActorContext, lease_id: int, ...) -> CommandResult:
    require(actor, "leases.manage")
    with command_writes_allowed():
        # validate state machine
        # perform operation
        # emit event
        return CommandResult(success=True, data=lease, event=event)
```

### 9.2 Commands (minimum)

| Command | Emits Event |
|---------|-------------|
| `create_property` | `property.created` |
| `update_property` | `property.updated` |
| `create_unit` | `unit.created` |
| `update_unit_status` | `unit.status_changed` |
| `create_lessee` | `lessee.created` |
| `update_lessee` | `lessee.updated` |
| `create_lease` | `lease.created` |
| `activate_lease` | `lease.activated` + `rent.schedule_generated` + `unit.status_changed` |
| `terminate_lease` | `lease.terminated` + `unit.status_changed` |
| `renew_lease` | `lease.renewed` (old) + `lease.created` (new) |
| `record_rent_payment` | `rent.payment_received` |
| `allocate_rent_payment` | `rent.payment_allocated` (per line) |
| `void_payment` | `rent.payment_voided` |
| `record_deposit_transaction` | `deposit.received` / `deposit.adjusted` / `deposit.refunded` / `deposit.forfeited` |
| `record_property_expense` | `property.expense_recorded` |
| `waive_schedule_line` | `rent.line_waived` |
| `post_rent_dues` | `rent.due_posted` (batch, via Celery task) |
| `detect_overdue` | `rent.overdue_detected` (batch, via Celery task) |

### 9.3 Event Data Types

All event payloads defined in `events/types.py` as `@dataclass` classes
extending `BaseEventData`, following the existing pattern:

```python
@dataclass
class LeaseActivatedData(BaseEventData):
    lease_public_id: str
    contract_no: str
    property_public_id: str
    unit_public_id: str       # "" if whole-property
    lessee_public_id: str
    start_date: str
    end_date: str
    rent_amount: str           # Decimal as string
    currency: str
    deposit_amount: str
    payment_frequency: str
    schedule_line_count: int
    activated_by_email: str
    activated_at: str
```

### 9.4 Overdue Detection Mechanism

A **daily Celery beat task** (`properties.tasks.detect_overdue_and_post_dues`):

1. Queries all schedule lines where `status=upcoming` and `due_date <= today` → transitions to `due`, emits `rent.due_posted`.
2. Queries all schedule lines where `status=due` and `due_date + grace_days < today` → transitions to `overdue`, emits `rent.overdue_detected`.
3. Runs once per day, idempotent (skips already-transitioned lines).

Also available as management command: `python manage.py process_rent_dues`

---

## 10. Accounting Mapping Rules

All accounting entries are created by a **projection** (`PropertyAccountingProjection`)
that consumes property events and creates `JournalEntry` records using the
existing `accounting.commands.create_journal_entry` / `post_journal_entry` flow.

The projection reads account codes from `PropertyAccountMapping` for the company.

### 10.1 Fiscal Period Mapping

Journal entries use the **`due_date`** (for rent) or **`transaction_date`**
(for deposits/expenses) to determine the fiscal period, using
Nxentra's existing `FiscalPeriod.get_period_for_date(company, date)`.

### 10.2 Mapping Rules

| Event | Debit | Credit | Memo Pattern |
|-------|-------|--------|-------------|
| `rent.due_posted` | AR (accounts_receivable_account) | Rental Income (rental_income_account) | "Rent due: {contract_no} #{installment_no}" |
| `rent.payment_received` | Cash/Bank (cash_bank_account) | Unapplied Cash (unapplied_cash_account) | "Payment received: {receipt_no}" |
| `rent.payment_allocated` | Unapplied Cash (unapplied_cash_account) | AR (accounts_receivable_account) | "Rent payment: {receipt_no} → {contract_no}" |
| `rent.payment_voided` | Unapplied Cash (unapplied_cash_account) | Cash/Bank (cash_bank_account) | "VOID: {receipt_no}" |
| `deposit.received` | Cash/Bank | Security Deposits Held (security_deposit_account) | "Deposit received: {contract_no}" |
| `deposit.refunded` | Security Deposits Held | Cash/Bank | "Deposit refund: {contract_no}" |
| `deposit.forfeited` | Security Deposits Held | Other Income (other_income_account) | "Deposit forfeited: {contract_no}" |
| `deposit.adjusted` | Depends on direction | Depends on direction | "Deposit adjustment: {contract_no}" |
| `property.expense_recorded` (cash_paid) | Expense (property_expense_account) | Cash/Bank | "Property expense: {property_code}" |
| `property.expense_recorded` (credit) | Expense | AP (accounts_payable_account) | "Property expense (credit): {property_code}" |

### 10.3 Dimension Tags on Journal Lines

All journal entries created by the property projection include dimension tags
using Nxentra's existing `AnalysisDimensionValue` system:

- **Property** → mapped to an AnalysisDimension (e.g., dimension code `PROPERTY`)
- **Unit** → sub-value under property dimension
- **Lessee** → optional dimension tag

The module creates these dimension values automatically when properties/units
are created, if an `AnalysisDimension` with code `PROPERTY` exists for the company.
This is **opt-in** — if the dimension doesn't exist, no tags are attached.

---

## 11. Required Workflows and Acceptance Criteria

### Workflow A: Onboard Property and Units

1. User creates property with required fields.
2. User creates one or more units under the property.
3. Unit codes are unique within the property.
4. Unit initial status defaults to `vacant`.
5. `property.created` and `unit.created` events emitted and visible in audit trail.

### Workflow B: Create Lessee

1. User creates lessee with required identity and contact fields.
2. Duplicate lessee code rejected (unique per company).
3. `lessee.created` event emitted.

### Workflow C: Create and Activate Lease

1. Draft lease can be saved without accounting impact.
2. **Pre-activation validation:**
   - `PropertyAccountMapping` must have `rental_income_account` and `accounts_receivable_account` configured.
   - No overlapping active lease for the same unit in the same date range.
   - `start_date <= end_date`.
3. On activate:
   - Lease status → `active`.
   - Unit status → `occupied` (if unit-level lease).
   - Rent schedule generated based on `payment_frequency`, `start_date`, `end_date`, `due_day_rule`.
   - `lease.activated` and `rent.schedule_generated` events emitted.

### Workflow D: Record and Allocate Payment

1. User records payment against lessee/lease.
2. System shows open schedule lines ordered by `due_date` (FIFO).
3. Allocation supports partial and multi-line.
4. `sum(allocations) <= payment.amount` enforced.
5. Schedule `outstanding` and `status` updated correctly.
6. `UniqueConstraint(payment, schedule_line)` prevents duplicate allocation.
7. `rent.payment_received` + `rent.payment_allocated` events emitted.

### Workflow E: Void Payment

1. User voids a payment with reason.
2. All allocations reversed: schedule lines reopened, outstanding recalculated.
3. `rent.payment_voided` event emitted → projection creates reversing journal entry.

### Workflow F: Deposit Lifecycle

1. Deposit receive/refund/forfeit/adjust recorded separately from rent.
2. Deposit running balance per lease = `sum(received + adjusted) - sum(refunded + forfeited)`.
3. Running balance cannot go below zero (enforced in command).
4. Correct accounting mapping applied per transaction type.

### Workflow G: Record Property Expense

1. User records expense with property and optional unit.
2. Category required.
3. `cash_paid` vs `credit` mode controls accounting mapping (Dr Expense / Cr Cash vs Cr AP).
4. `property.expense_recorded` event emitted.

### Workflow H: Lease Termination

1. Termination requires reason and effective date.
2. Command computes context: unpaid dues count, deposit balance.
3. Lease status → `terminated`, `terminated_at` set.
4. Unit status → `vacant`.
5. `lease.terminated` + `unit.status_changed` events emitted.

### Workflow I: Lease Renewal

1. Renew command creates a **new lease** (draft) with `renewed_from_lease` FK.
2. Old lease status → `renewed` (terminal).
3. New lease inherits property/unit/lessee but allows updated terms.
4. New lease must be activated separately (Workflow C).

### Workflow J: Expiry Alerts

1. Daily Celery task flags leases with `end_date` within 90/60/30 days.
2. Alert list available in UI and API.
3. Also available as management command: `python manage.py check_lease_expiry`

---

## 12. API Endpoints

REST endpoints under `/api/properties/`:

### CRUD Endpoints

| Method | Endpoint | Permission |
|--------|----------|-----------|
| GET/POST | `/api/properties/properties/` | `properties.view` / `properties.manage` |
| GET/PUT/PATCH | `/api/properties/properties/{id}/` | `properties.view` / `properties.manage` |
| GET/POST | `/api/properties/units/` | `units.view` / `units.manage` |
| GET/PUT | `/api/properties/units/{id}/` | `units.view` / `units.manage` |
| GET/POST | `/api/properties/lessees/` | `lessees.view` / `lessees.manage` |
| GET/PUT | `/api/properties/lessees/{id}/` | `lessees.view` / `lessees.manage` |
| GET/POST | `/api/properties/leases/` | `leases.view` / `leases.manage` |
| GET | `/api/properties/leases/{id}/` | `leases.view` |
| POST | `/api/properties/leases/{id}/activate/` | `leases.manage` |
| POST | `/api/properties/leases/{id}/terminate/` | `leases.manage` |
| POST | `/api/properties/leases/{id}/renew/` | `leases.manage` |
| GET | `/api/properties/leases/{id}/schedule/` | `leases.view` |
| GET/POST | `/api/properties/payments/` | `collections.view` / `collections.receive` |
| POST | `/api/properties/payments/{id}/allocate/` | `collections.receive` |
| POST | `/api/properties/payments/{id}/void/` | `collections.receive` |
| GET/POST | `/api/properties/deposits/` | `deposits.view` / `deposits.manage` |
| GET/POST | `/api/properties/expenses/` | `expenses.view` / `expenses.manage` |
| GET | `/api/properties/alerts/` | `alerts.manage` |
| GET/PUT | `/api/properties/account-mapping/` | `properties.manage` |

### List Endpoint Filters

All list endpoints support:
- `property` (ID or code)
- `unit` (ID)
- `lessee` (ID or code)
- `lease` (ID or contract_no)
- `status`
- `date_from`, `date_to` (for date ranges)
- Pagination (`page`, `page_size`)
- Sorting (`ordering`)

### Report Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/properties/reports/rent-roll/` | Current rent roll |
| GET | `/api/properties/reports/overdue/` | Overdue balances by lessee |
| GET | `/api/properties/reports/expiry/` | Lease expiry report (30/60/90) |
| GET | `/api/properties/reports/occupancy/` | Occupancy summary |
| GET | `/api/properties/reports/income/` | Monthly net income by property |
| GET | `/api/properties/reports/collections/` | Rent billed vs collected |
| GET | `/api/properties/reports/expenses/` | Expense breakdown |
| GET | `/api/properties/reports/deposits/` | Security deposit liability |
| GET | `/api/properties/dashboard/` | Dashboard summary data |

---

## 13. UI / Navigation

### Sidebar Menu

```
Properties (top-level)
├── Dashboard
├── Properties
├── Units
├── Lessees
├── Leases
├── Collections
├── Expenses
├── Alerts
└── Reports
```

### Pages (minimum)

| Page | Tabs / Key Features |
|------|-------------------|
| Property List | Filter by status, type. Create button. |
| Property Detail | Overview, Units, Leases, Collections, Expenses, Profitability |
| Unit List | Filter by property, status. Create button. |
| Lessee List | Filter by status, risk. Create button. |
| Lessee Detail | Info, Active Leases, Payment History |
| Lease List | Filter by status, property, lessee. Create button. |
| Lease Detail | Contract Info, Schedule, Payments, Deposits, Alerts, Activity Log |
| Collections | Record payment, allocate to open dues (FIFO suggested) |
| Expense List | Filter by property, category, date range. Create button. |
| Alerts | Expiry warnings (90/60/30), overdue notices |
| Reports | All reports listed in Section 12 |
| Account Mapping | Settings page for GL account configuration |

---

## 14. Reports (Phase 1)

### Operational

1. **Rent Roll** — all active leases with current installment status
2. **Overdue Balances by Lessee** — lessees with outstanding overdue amounts
3. **Lease Expiry Report** — leases expiring within 30/60/90 days
4. **Occupancy Summary** — occupied vs vacant units, by property

### Financial

5. **Monthly Net Income by Property** — rental income minus expenses per property
6. **Rent Billed vs Collected** — schedule total_due vs total_allocated per period
7. **Expense Breakdown** — by property and category
8. **Security Deposit Liability** — current deposit balance per lease

All reports consume data from **projections/read models**, not direct event queries.
Reports support `fiscal_year` and `date_range` filters where applicable.

---

## 15. Non-Functional Requirements

1. **Tenant isolation:** enforced by existing `tenant_context` and `TenantDatabaseRouter`. `properties` app added to `TENANT_APPS`.
2. **Authorization:** `require(actor, permission)` on every command. Views check permissions before calling commands.
3. **Audit trail:** all state changes emit events, visible via existing event store.
4. **Idempotency:** commands use `idempotency_key` on `emit_event()` for retry safety.
5. **Write barrier:** all models use `ProjectionWriteGuard` — no direct saves outside command/projection context.
6. **Performance:** list endpoints p95 < 500ms for 10K schedule lines per tenant.
7. **Timezone:** all dates stored as `DateField` (no time component). Timestamps use `DateTimeField` with `USE_TZ=True`.

---

## 16. Data Integrity Rules

1. No overlapping active leases for the same unit (checked in `activate_lease`).
2. `start_date <= end_date` on every lease.
3. Schedule total must match lease terms at generation time.
4. `sum(payment.allocations) <= payment.amount`.
5. Schedule `outstanding` cannot go below zero.
6. Deposit running balance cannot become negative.
7. `currency` on payment/deposit must match `currency` on lease.
8. Account mapping must be configured before lease activation or payment recording.

---

## 17. Testing Requirements

### Unit Tests (`properties/tests/test_commands.py`)

- All commands return `CommandResult` with correct success/error states.
- State machine transitions: valid transitions succeed, invalid transitions rejected.
- Business rules: no-overlap lease, allocation bounds, deposit balance.

### Invariant Tests (`tests/test_property_invariants.py`)

- **Replay equals incremental:** rebuild projections from events matches incremental processing.
- **Allocation idempotency:** duplicate allocation attempts are rejected.
- **No-overlap lease rule:** concurrent activation of overlapping leases fails.
- **Deposit liability correctness:** `sum(deposit transactions)` matches reported liability.
- **Unit status consistency:** unit status always reflects active lease state.
- **Schedule integrity:** `sum(schedule.total_due)` equals expected total from lease terms.

### E2E Tests

- Full lifecycle: lease activation → schedule generation → due posting → payment → allocation → reporting.
- Termination with unpaid dues and deposit settlement context.

### Permission Tests

- Each role can only access permitted endpoints.
- Cross-role escalation attempts rejected.

### Multi-Tenant Isolation Tests

- Company A cannot see Company B's properties/leases/payments.
- Events are scoped to company.

---

## 18. Delivery Plan (Revised — 5 Sprints)

### Sprint 1: Foundation (Models + CRUD)
- Data models + migrations for all entities
- Basic CRUD APIs for Property, Unit, Lessee
- Basic UI pages (list + create/edit)
- `PropertyAccountMapping` model and settings page
- Event type definitions in `events/types.py`

### Sprint 2: Lease Lifecycle
- Lease CRUD + activate/terminate/renew commands
- Rent schedule generation logic
- State machine enforcement
- Lease detail page with schedule tab
- Unit status transitions wired to lease lifecycle

### Sprint 3: Payments + Deposits
- Payment receipt + allocation commands
- Payment void flow
- Security deposit transaction commands
- Collections page UI
- Deposit tab on lease detail

### Sprint 4: Expenses + Accounting Projection
- Property expense recording
- `PropertyAccountingProjection` — consumes events, creates journal entries
- Account mapping validation on activation/payment
- Daily Celery task for due posting + overdue detection

### Sprint 5: Reports + Alerts + Hardening
- All 8 reports
- Dashboard widgets
- Expiry alert system (Celery task + UI page)
- Full test suite (invariants, E2E, permissions, isolation)
- Bug fixes, edge cases, performance tuning

---

## 19. Definition of Done (Phase 1)

- [ ] All in-scope workflows pass UAT
- [ ] All events emitted for all write actions (verified by event trail)
- [ ] Accounting projections create correct journal entries (verified by trial balance)
- [ ] All 8 reports available and validated with test data
- [ ] No open P0/P1 defects
- [ ] CI green: backend tests + frontend build
- [ ] Tenant isolation verified (new module added to isolation tests)
- [ ] Account mapping documented in Settings UI
- [ ] Projection rebuild produces identical results to incremental processing

---

## 20. Explicit "Do Not Do"

- Do NOT add WhatsApp or lessee portal in Phase 1.
- Do NOT add AI/OCR scope.
- Do NOT make schedule fully manual (always generated from lease terms).
- Do NOT treat deposit as rent revenue.
- Do NOT bypass command/event pipeline (no direct model saves from views).
- Do NOT over-abstract to generic asset/contract framework at MVP stage.
- Do NOT call the property renter "Tenant" in code (use "Lessee").
- Do NOT create journal entries directly from views — always through projection.
- Do NOT add multi-currency support in Phase 1.

---

## Appendix A: Locked Critical Decisions (v2.1)

These decisions were locked after external architecture review and must be followed
exactly during implementation. Each one resolves an ambiguity or gap from v2.0.

### A.1 Unapplied Cash Account (Two-Step Payment Posting)

**Decision:** Add `unapplied_cash_account` to `PropertyAccountMapping`.

Payments follow a two-step posting model:

1. **On receipt** (`rent.payment_received`):
   `Dr Cash/Bank → Cr Unapplied Cash`
2. **On allocation** (`rent.payment_allocated`):
   `Dr Unapplied Cash → Cr AR`

This prevents AR being credited before the payment is explicitly allocated to
specific schedule lines. The `unapplied_cash_account` field is **required** —
payment receipt MUST fail if it is not configured.

**Updated PropertyAccountMapping field:**
```python
unapplied_cash_account = FK(Account, nullable)  # unapplied cash holding
```

**Updated Accounting Mapping Rules (Section 10.2):**

| Event | Debit | Credit |
|-------|-------|--------|
| `rent.payment_received` | Cash/Bank (`cash_bank_account`) | Unapplied Cash (`unapplied_cash_account`) |
| `rent.payment_allocated` | Unapplied Cash (`unapplied_cash_account`) | AR (`accounts_receivable_account`) |
| `rent.payment_voided` | Unapplied Cash (`unapplied_cash_account`) | Cash/Bank (`cash_bank_account`) |

When a payment is voided, the reversal targets Unapplied Cash (not AR), then
any allocations that were already made also get reversed separately
(`Dr AR → Cr Unapplied Cash` per allocation line).

### A.2 Idempotency Key Specifications

**Decision:** All commands that emit events MUST provide an `idempotency_key` to
`emit_event()`. The key format for each event type:

| Event | Idempotency Key Format |
|-------|----------------------|
| `rent.due_posted` | `rent.due_posted:{schedule_line.public_id}` |
| `rent.overdue_detected` | `rent.overdue_detected:{schedule_line.public_id}` |
| `lease.expiry_alert` | `lease.expiry_alert:{lease.public_id}:{threshold_days}` |
| `rent.payment_received` | `rent.payment_received:{payment.public_id}` |
| `rent.payment_allocated` | `rent.payment_allocated:{allocation.public_id}` |
| `rent.payment_voided` | `rent.payment_voided:{payment.public_id}` |
| `lease.activated` | `lease.activated:{lease.public_id}` |
| `lease.terminated` | `lease.terminated:{lease.public_id}` |
| `lease.renewed` | `lease.renewed:{lease.public_id}` |
| `deposit.*` | `deposit.{type}:{transaction.public_id}` |
| `property.expense_recorded` | `property.expense_recorded:{expense.public_id}` |

The Celery daily tasks (`post_rent_dues`, `detect_overdue`) are inherently
idempotent because they use the `schedule_line.public_id` in the key — a
second run for the same line is a no-op at the event store level.

### A.3 Projection Recursion Guard

**Decision:** `PropertyAccountingProjection` MUST track `source_event_id` and
skip events that it originated itself to prevent infinite loops.

Implementation pattern:
```python
class PropertyAccountingProjection:
    def handle_event(self, event):
        # Skip events originated by this projection
        if event.metadata.get("source_projection") == "PropertyAccountingProjection":
            return

        # When creating journal entries, tag the emitted events
        journal_event = emit_event(
            ...,
            metadata={"source_projection": "PropertyAccountingProjection"}
        )
```

This is critical because `create_journal_entry` itself emits events
(`journal.entry_created`), and the projection must not re-process those.

### A.4 Schedule Generation Algorithm

**Decision:** The rent schedule generation in `activate_lease` follows these rules:

1. **End date is inclusive** — a lease from Jan 1 to Dec 31 covers the full year.
2. **Month-end clamping** — if `due_day_rule = specific_day` and the day exceeds
   the month length (e.g., day 31 in February), clamp to the last day of the month.
3. **Proration by calendar days** — first and last periods that are shorter than
   a full cycle use: `rent_amount * (days_in_partial_period / days_in_full_period)`.
4. **Installment numbering** — starts at 1, sequential, no gaps.
5. **Period boundaries** — `period_start` of installment N+1 = `period_end` of
   installment N + 1 day (no gaps, no overlaps).

Example for monthly lease, Jan 15 to Jun 30, rent = 12,000 SAR:
- Installment 1: Jan 15–Jan 31, due Jan 15, amount = 12,000 * 17/31 = 6,580.65
- Installment 2: Feb 1–Feb 28, due Feb 1, amount = 12,000
- Installments 3–5: Mar–May full months, 12,000 each
- Installment 6: Jun 1–Jun 30, due Jun 1, amount = 12,000

### A.5 Void Accounting Edge Cases

**Decision:** Payment void reversals follow these rules:

1. **Current period:** Reversing journal entry created in the same fiscal period
   as the original entry.
2. **Closed period:** If the original entry's fiscal period is closed, the
   reversing entry is created in the **first open fiscal period** (not the
   original period). The memo includes: "VOID (original period: {period})".
3. **Void cascade:** When a payment is voided:
   - Step 1: Reverse all allocation journal entries (`Dr AR → Cr Unapplied Cash`)
   - Step 2: Reverse the receipt journal entry (`Dr Unapplied Cash → Cr Cash/Bank`)
   - Step 3: Reopen affected schedule lines (recalculate `outstanding`, reset status)
4. **Partial allocation void:** Not supported — voiding a payment voids ALL its
   allocations. To correct a single allocation, void the entire payment and
   re-record with correct allocations.

### A.6 No-Overlap Concurrency Strategy

**Decision:** `activate_lease` uses `SELECT FOR UPDATE` on the `Unit` row to
prevent concurrent lease activations for the same unit.

```python
@transaction.atomic
def activate_lease(actor, lease_id, ...):
    lease = Lease.objects.select_related("unit").get(id=lease_id)

    if lease.unit:
        # Lock the unit row to prevent concurrent activations
        Unit.objects.select_for_update().get(id=lease.unit_id)

        # Now check for overlapping active leases
        overlapping = Lease.objects.filter(
            unit=lease.unit,
            status="active",
            start_date__lte=lease.end_date,
            end_date__gte=lease.start_date,
        ).exists()
        if overlapping:
            return CommandResult(success=False, error="Overlapping active lease exists")
    ...
```

For **whole-property leases** (unit is null), the lock is acquired on the
`Property` row instead, and overlap check includes all units of that property.

### A.7 Dimension Cardinality and Naming

**Decision:** Property and unit dimension values follow a strict naming convention:

- **Property dimension values:** `PROP-{property.code}` (e.g., `PROP-BLD001`)
- **Unit dimension values:** `UNIT-{property.code}-{unit.unit_code}` (e.g., `UNIT-BLD001-101`)

Rules:
1. **Opt-in:** Dimension values are only created if an `AnalysisDimension` with
   code `PROPERTY` exists for the company. No error if it doesn't exist.
2. **Inherit `is_active`:** When a property or unit is deactivated, the
   corresponding dimension value's `is_active` is set to `False`.
3. **Creation timing:** Dimension values are created in the `create_property`
   and `create_unit` commands (not in projections).
4. **No duplicate creation:** Commands check for existing dimension value before
   creating (idempotent).

### A.8 Timezone Rule for Daily Tasks

**Decision:** All daily Celery tasks (`post_rent_dues`, `detect_overdue`,
`check_lease_expiry`) use the **company's timezone** to determine "today".

```python
from zoneinfo import ZoneInfo

company_tz = ZoneInfo(company.timezone or "UTC")
today = datetime.now(company_tz).date()
```

All date comparisons (`due_date <= today`, `end_date` within 90 days, etc.)
use date-only comparison (no time component). This ensures correct behavior
for companies in UTC+ timezones where "today" differs from UTC date.

### A.9 Backfill and Bootstrap Plan

**Decision:** A management command `setup_property_module` handles first-time
setup for existing tenants:

```bash
python manage.py setup_property_module --company-id <id>
```

This command:
1. Creates the `PROPERTY` AnalysisDimension if it doesn't exist.
2. Creates a default `PropertyAccountMapping` with all fields null (requires
   manual configuration before lease activation).
3. Registers property-specific permissions in the permission system.
4. Is idempotent — safe to run multiple times.

### A.10 UAT Dataset

**Decision:** Sprint 5 includes a `seed_property_demo_data` management command
that creates a realistic demo dataset for UAT:

```bash
python manage.py seed_property_demo_data --company-id <id>
```

Dataset includes:
- 3 properties (residential building, office building, retail)
- 10-15 units across properties
- 5-8 lessees
- Active, expired, and terminated leases
- Rent schedules with mix of paid, overdue, and upcoming lines
- Payment receipts with allocations
- Security deposits (received, partially refunded)
- Property expenses across categories
- One voided payment for testing reversal flow

### A.11 Whole-Property and Unit Lease Coexistence Rule

**Decision:** A property cannot have both a whole-property lease and unit-level
leases active simultaneously. This is enforced in `activate_lease`:

- If activating a **whole-property lease** (unit is null): reject if ANY unit
  of that property has an active lease with overlapping dates.
- If activating a **unit-level lease**: reject if the property has an active
  whole-property lease with overlapping dates.

This prevents double-counting of rental income for the same physical space.

---

## Appendix B: Implementation Checklist (from Locked Decisions)

Use this checklist during Sprint implementation to verify all locked decisions
are correctly implemented:

- [ ] `PropertyAccountMapping` includes `unapplied_cash_account` field
- [ ] Payment receipt creates `Dr Cash → Cr Unapplied Cash` (not `Cr AR`)
- [ ] Payment allocation creates `Dr Unapplied Cash → Cr AR`
- [ ] All event emissions include idempotency keys per A.2 format
- [ ] `PropertyAccountingProjection` has recursion guard (A.3)
- [ ] Schedule generation handles month-end clamping and proration (A.4)
- [ ] Void reversal uses first open period when original is closed (A.5)
- [ ] `activate_lease` uses `SELECT FOR UPDATE` on Unit/Property (A.6)
- [ ] Dimension values use `PROP-{code}` / `UNIT-{code}-{code}` naming (A.7)
- [ ] Daily tasks use company timezone for "today" (A.8)
- [ ] `setup_property_module` management command exists and is idempotent (A.9)
- [ ] `seed_property_demo_data` command exists with full dataset (A.10)
- [ ] Whole-property vs unit lease mutual exclusion enforced (A.11)
