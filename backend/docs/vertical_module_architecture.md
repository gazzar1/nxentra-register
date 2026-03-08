# Vertical Module Architecture

## Overview

Nxentra uses a declarative, auto-discovered registration pattern for vertical modules (property management, clinic, ecommerce, etc.). Each module declares its projections, event types, and account roles on its Django `AppConfig`. The core `ProjectionsConfig.ready()` discovers and registers everything at startup.

## 1. Projection Registration

### How it works

Each vertical module lists its projections as dotted import paths on its `AppConfig`:

```python
# properties/apps.py
class PropertiesConfig(AppConfig):
    name = "properties"
    projections = ["projections.property.PropertyAccountingProjection"]
```

At startup, `ProjectionsConfig.ready()`:
1. Imports all `CORE_PROJECTION_MODULES` (always-on projections like account_balance, accounting, periods).
2. Iterates every installed `AppConfig`, looks for a `projections` attribute, imports each class, instantiates it, and registers it in `projection_registry`.
3. Calls `_assert_registration_integrity()` to verify every declared projection was actually registered.

Duplicate projection names raise `RuntimeError` immediately.

### Core vs. vertical projections

| Type | Declared in | Registered by |
|------|------------|---------------|
| Core | `CORE_PROJECTION_MODULES` list in `projections/apps.py` | Module-level `projection_registry.register()` calls |
| Vertical | `AppConfig.projections` list | Auto-discovery in `ProjectionsConfig.ready()` |

## 2. Event Type Declaration

Each vertical module declares an `event_types_module` on its `AppConfig`:

```python
class PropertiesConfig(AppConfig):
    event_types_module = "properties.event_types"
```

That module must expose a `REGISTERED_EVENTS` dict:

```python
# properties/event_types.py
REGISTERED_EVENTS: dict[str, type[BaseEventData]] = {
    EventTypes.RENT_DUE_POSTED: RentDuePostedData,
    EventTypes.RENT_PAYMENT_RECEIVED: RentPaymentReceivedData,
    # ...
}
```

At startup, `ProjectionsConfig.ready()` validates:
- The module is importable
- `REGISTERED_EVENTS` is a dict
- All keys are strings
- All values are `BaseEventData` subclasses
- No duplicate event types across modules (raises `RuntimeError`)

Valid entries are merged into the central `EVENT_DATA_CLASSES` registry.

## 3. Account-Role Mapping

The generic `ModuleAccountMapping` model (`accounting/mappings.py`) replaces per-module mapping tables. Each vertical module declares the roles it needs:

```python
class PropertiesConfig(AppConfig):
    account_roles = [
        "RENTAL_INCOME", "ACCOUNTS_RECEIVABLE", "CASH_BANK",
        "SECURITY_DEPOSIT", "PROPERTY_EXPENSE",
    ]
```

Usage in a projection:

```python
from accounting.mappings import ModuleAccountMapping

mapping = ModuleAccountMapping.get_mapping(company, "properties")
ar = mapping.get("ACCOUNTS_RECEIVABLE")

# Or for a single role:
account = ModuleAccountMapping.get_account(company, "properties", "CASH_BANK")

# Check all required roles are mapped:
missing = ModuleAccountMapping.check_required_roles(
    company, "properties", ["RENTAL_INCOME", "ACCOUNTS_RECEIVABLE"]
)
```

The model enforces `unique_together = ("company", "module", "role")` and uses the same write-barrier pattern as other configuration models.

## 4. Two Accounting Integration Patterns

### Pattern A: Command creates JournalEntry directly

Used by: Sales, Purchases, Manual Journal Entries.

```
User action -> Command -> creates JournalEntry + emits BusinessEvent (audit)
```

The event is an audit record. The projection does not create accounting entries.

### Pattern B: Projection creates JournalEntry from domain event

Used by: Property Management (and future verticals with complex domain logic).

```
User action -> Command -> emits domain BusinessEvent
                          -> Projection handles event -> creates JournalEntry
```

The projection is the only writer of accounting entries. This enables full rebuild from the event log.

Both patterns are supported. Choose based on whether the accounting entry is a direct consequence of a user action (Pattern A) or derived from domain events (Pattern B).

## 5. FinancialEventData

For Pattern B modules, event data classes should extend `FinancialEventData`:

```python
from events.types import FinancialEventData

@dataclass
class RentDuePostedData(FinancialEventData):
    schedule_line_public_id: str = ""
    lease_public_id: str = ""
    # ...
```

`FinancialEventData` provides canonical fields: `amount`, `currency`, `transaction_date`, `document_ref`.

## 6. How to Add a New Vertical Module

Checklist for adding a new vertical (e.g., "clinic"):

1. **Create the Django app**: `python manage.py startapp clinic`

2. **Define event data classes** in `clinic/event_types.py`:
   ```python
   from dataclasses import dataclass
   from events.types import FinancialEventData, BaseEventData, EventTypes

   @dataclass
   class ConsultationFeeData(FinancialEventData):
       patient_public_id: str = ""
       doctor_public_id: str = ""

   REGISTERED_EVENTS: dict[str, type[BaseEventData]] = {
       EventTypes.CLINIC_CONSULTATION_FEE: ConsultationFeeData,
   }
   ```

3. **Add event type constants** to `events/types.py` → `EventTypes` class.

4. **Create the projection** in `projections/clinic.py` (or `clinic/projections.py`):
   ```python
   from projections.base import BaseProjection

   class ClinicAccountingProjection(BaseProjection):
       @property
       def name(self): return "clinic_accounting"

       @property
       def consumes(self): return [EventTypes.CLINIC_CONSULTATION_FEE]

       def handle(self, event): ...
   ```

5. **Configure the AppConfig** in `clinic/apps.py`:
   ```python
   class ClinicConfig(AppConfig):
       name = "clinic"
       projections = ["projections.clinic.ClinicAccountingProjection"]
       event_types_module = "clinic.event_types"
       account_roles = ["CASH_BANK", "CONSULTATION_REVENUE"]
   ```

6. **Add to INSTALLED_APPS** in settings.

7. **Run `makemigrations` and `migrate`** if models were added.

8. **Verify**: Run `python -m pytest tests/test_vertical_module_integrity.py -v` — all integrity tests should pass automatically for the new module.

No manual registration calls needed. No changes to `ProjectionsConfig.ready()`. The discovery mechanism handles everything.
