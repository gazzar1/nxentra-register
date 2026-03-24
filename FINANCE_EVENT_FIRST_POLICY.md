# Finance Event-First Policy

> **Rule**: Every finance-impacting action in Nxentra must emit a canonical business event.
> Journal entries must only be created through approved posting/projection paths.
> No direct model writes to finance truth tables outside allowed write-barrier contexts.

## Architecture Summary

Nxentra is **hybrid event-sourced with CQRS separation**:

- **Command layer** (`commands.py`) validates, mutates command-owned models, and emits events via `emit_event()`.
- **Event store** (`BusinessEvent`) is the immutable audit log with causation chains (`caused_by_event`), LEPH for large payloads, and per-aggregate sequencing.
- **Projection layer** (`BaseProjection` subclasses) subscribes to event types, processes them idempotently within `projection_writes_allowed()`, and writes read models (JournalEntry, AccountBalance, etc.).
- **Write barriers** (`write_barrier.py`) enforce that finance models can only be written within explicit contexts: `command`, `projection`, `bootstrap`, `migration`, or `admin_emergency`.

Finance flows are **event-first**: the event is the source of truth, projections derive state from events, and rebuilds reproduce identical results.

---

## 1. What Counts as Finance-Impacting

Any action that creates, modifies, or reverses a journal entry, or that changes account balances, subledger balances, or dimension balances. This includes:

| Flow | Command | Event(s) Emitted |
|------|---------|-----------------|
| Journal entry posting | `post_journal_entry` | `journal_entry.posted` |
| Journal entry reversal | `reverse_journal_entry` | `journal_entry.reversed` |
| Sales invoice posting | `post_sales_invoice` | `sales.invoice_posted` → `journal_entry.posted` |
| Purchase bill posting | `post_purchase_bill` | `purchases.bill_posted` → `journal_entry.posted` |
| Customer receipt | `record_customer_receipt` | `cash.customer_receipt_recorded` → `journal_entry.posted` |
| Vendor payment | `record_vendor_payment` | `cash.vendor_payment_recorded` → `journal_entry.posted` |
| Rent due (properties) | projection handles `rent.due_posted` | `rent.due_posted` → `journal_entry.posted` |
| FX revaluation | `CurrencyRevaluationView.post` | `journal_entry.posted` (+ optional reversal) |
| Vertical module events | Module-specific commands | Module event → `journal_entry.posted` |

---

## 2. Runtime Enforcement

### Write Barriers (already enforced)

All accounting read models use `ProjectionWriteManager` which checks write context:

```python
# Only projection and command contexts can write JournalEntry/JournalLine
class ProjectionWriteManager(models.Manager):
    def create(self, *args, **kwargs):
        if not write_context_allowed({"projection", "command"}):
            raise RuntimeError("...")
```

**Allowed write contexts:**
- `command` — Command layer (e.g., `create_journal_entry`)
- `projection` — Projection processing (e.g., `AccountBalanceProjection.handle`)
- `bootstrap` — Initial data seeding
- `migration` — Schema migrations
- `admin_emergency` — Requires `ALLOW_ADMIN_EMERGENCY_WRITES=True` (disabled in production)

### Causation Chain

Every `BusinessEvent` can link to its parent via `caused_by_event`:
```
SALES_INVOICE_POSTED → JOURNAL_ENTRY_POSTED → AccountBalance updated
```

This chain enables full audit: "why does this balance exist?" → trace events backward.

---

## 3. Boot-Time Enforcement (already enforced)

`ProjectionsConfig.ready()` in `projections/apps.py`:

1. Loads core projection modules (account_balance, accounting, periods, etc.)
2. Discovers vertical projections from `AppConfig.projections` declarations
3. Discovers event types from `AppConfig.event_types_module` declarations
4. Runs `_assert_registration_integrity()` — fails boot if declared projections or event types are missing

Every finance-impacting module **must** declare:
- `projections = ["module.projections.MyProjection"]` in its AppConfig
- `event_types_module = "module.event_types"` with a `REGISTERED_EVENTS` dict
- Account-role mappings via `ModuleAccountMapping` (validated at runtime when projection handles events)

---

## 4. CI Enforcement (test suites)

### Invariant Tests (`test_truth_invariants.py`)

| # | Invariant | What It Catches |
|---|-----------|----------------|
| 1 | Replay-from-zero = incremental | Order-dependent projection bugs |
| 2 | Double-apply = no-op | Broken idempotency guards |
| 3 | Reversal fully offsets original | Incorrect swap logic |
| 4 | Multi-line same-account sums correctly | Aggregation bugs |
| 5 | External payload = inline payload | LEPH storage/retrieval bugs |
| 6 | `verify_all_balances` agrees with projection | Projection drift from events |
| 7 | Trial balance always balanced | Fundamental accounting equation violation |
| 8 | Every posted JE traces to a business event | Orphan JE detection |
| 9 | No finance writes outside write barriers | Write barrier bypass |
| 10 | Event causation chain is intact | Broken `caused_by_event` links |

### Module Integrity Tests (`test_vertical_module_integrity.py`)

- All declared projections are registered and unique
- No orphan projections (registered but not declared)
- All event types from `REGISTERED_EVENTS` are in central `EVENT_DATA_CLASSES`
- Event data classes subclass `BaseEventData`
- `FinancialEventData` carries required fields (amount, currency, transaction_date, document_ref)
- Finance event types have at least one consuming projection
- Every finance module has required account-role mappings declared

### Module Enforcement Tests (`test_module_enforcement.py`)

- Disabled modules return 403
- Core modules always accessible
- Data preserved after disable
- Re-enable restores access

---

## 5. Audit Checks (scheduled / on-demand)

### Management Command: `audit_event_first`

```bash
# Human-readable output
python manage.py audit_event_first

# JSON for monitoring pipelines
python manage.py audit_event_first --json

# Fail on violations (for CI gates)
python manage.py audit_event_first --strict

# Single company
python manage.py audit_event_first --company my-company
```

**Checks performed:**

| Check | Expected | Severity |
|-------|----------|----------|
| Posted JEs without corresponding `journal_entry.posted` event | 0 | CRITICAL |
| `journal_entry.posted` events without downstream AccountBalance update | 0 | WARNING |
| Broken causation chains (event references non-existent parent) | 0 | CRITICAL |
| AccountBalance projection lag (unprocessed events) | 0 | WARNING |
| Trial balance imbalance | 0 | CRITICAL |

### Existing: `reconciliation_check`

```bash
python manage.py reconciliation_check --strict
```

AR/AP subledger tied to GL control accounts.

---

## 6. Governance

### PR Checklist (for finance-impacting changes)

- [ ] Finance-impacting path emits a business event via `emit_event()`
- [ ] Event type registered in `EVENT_DATA_CLASSES` (via module's `event_types.py`)
- [ ] Journal entries created only within `command_writes_allowed()` or `projection_writes_allowed()`
- [ ] Causation chain: `caused_by_event` links derived events to source events
- [ ] Projection is idempotent (re-processing same event = no-op)
- [ ] Rebuild produces identical results (tested)
- [ ] New event types have corresponding test coverage in invariant suites

### Codeowners

Finance-critical paths require review from accounting/infrastructure owners:
- `backend/accounting/commands.py`
- `backend/events/emitter.py`
- `backend/projections/`
- `backend/events/types.py`

---

## 7. Reducing Direct-Write Exceptions

Current known direct-write paths (acceptable for now):

1. **FX revaluation** — `CurrencyRevaluationView.post()` creates JE via `create_journal_entry` command (event-first via command layer, but the revaluation calculation itself is a view, not a command). Future: extract to a proper command.

2. **Opening balances** — May be imported directly. Future: emit `opening_balance.imported` events.

3. **Manual JE creation** — Users create entries via the JE form → `create_journal_entry` command → event. This is correct.

**Goal**: Over time, reduce exceptions by moving all finance-impacting calculations into commands that emit events. The view layer should only orchestrate commands, never write finance models directly.

---

## References

| File | Purpose |
|------|---------|
| `backend/events/emitter.py` | Event emission with LEPH, idempotency, sequencing |
| `backend/events/models.py` | `BusinessEvent` model with causation chain |
| `backend/events/types.py` | `EventTypes`, `EVENT_DATA_CLASSES`, data classes |
| `backend/projections/write_barrier.py` | Write context stack |
| `backend/projections/base.py` | `BaseProjection` with idempotent processing |
| `backend/projections/apps.py` | Boot-time registration and integrity assertion |
| `backend/projections/account_balance.py` | AccountBalance projection |
| `backend/accounting/commands.py` | Command layer for JE/invoice/bill/receipt/payment |
| `backend/accounting/models.py` | JournalEntry with `source_module`/`source_document` |
| `backend/tests/test_truth_invariants.py` | Accounting invariant test suite |
| `backend/tests/test_vertical_module_integrity.py` | Module registration tests |
