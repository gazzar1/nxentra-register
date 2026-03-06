# Projection Idempotency Architecture

## The Rule

**Event-level idempotency lives in `BaseProjection.process_pending()`, not in `_apply_line()`.**

`ProjectionAppliedEvent` (unique constraint on `company + projection_name + event`) guarantees
each event is processed exactly once per projection. This is the single idempotency mechanism.

## What `_apply_line()` Must Never Do

Never skip a line based on `last_event_id`:

```python
# WRONG - silently drops legitimate lines
if balance.last_event_id == event.id:
    return
```

A single event can legally contain multiple lines to the same account, entity, or item:

- Consolidated postings
- Cost allocations across departments hitting the same control account
- Tax lines (multiple line items, each with VAT to the same tax payable account)
- Batch imports grouping lines
- Reversals netting multiple amounts into the same account

The `last_event` field on projection models is **metadata only** (audit trail of which event
last touched the record). It must never be used as a skip/guard condition.

## How Idempotency Actually Works

```
process_pending()
  for event in unprocessed_events:
    with transaction.atomic():
      applied, created = ProjectionAppliedEvent.get_or_create(event)
      if not created:        # already processed -> skip entire event
        bookmark.mark_processed(event)
        continue
      self.handle(event)     # processes ALL lines in the event
      bookmark.mark_processed(event)
```

The bookmark advances only after the event is fully applied inside a transaction.
If the transaction fails, neither the bookmark nor ProjectionAppliedEvent are committed,
so the event will be retried on the next run.

## Affected Projections

This rule applies to all projections that process multi-line events:

| Projection | File | Lines applied per event |
|---|---|---|
| AccountBalanceProjection | `projections/account_balance.py` | One per journal line |
| SubledgerBalanceProjection | `projections/subledger_balance.py` | One per line with customer/vendor |
| PeriodAccountBalanceProjection | `projections/period_balance.py` | One per journal line per period |
| InventoryBalanceProjection | `projections/inventory_balance.py` | One per stock entry |

## History

This document was created after fixing a bug (March 2026) where all four projections
had per-account `last_event_id` guards that silently dropped the second line when a
single journal entry had multiple lines to the same account. The fix removed all six
guard instances and added a regression test (`test_multiple_lines_same_account_in_single_event`).
