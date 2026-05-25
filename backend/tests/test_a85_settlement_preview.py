# tests/test_a85_settlement_preview.py
"""
A85 (2026-05-25): settlement CSV import preview (dry-run).

Tests that `preview_settlement_import()` parses a CSV and returns an
accurate plan without emitting events or posting JEs. The plan is what
the operator-facing pre-flight modal renders so they can review:
- how many journal entries will be created
- which fiscal periods will be touched
- whether any periods are closed (would block the post)
- whether any batches are already imported (would be deduped)
- which order IDs in the CSV don't match Shopify orders (operator review)

See:
- accounting/settlement_imports.py preview_settlement_import()
- docs/finance_event_first_policy.md §8 (loud failures, not silent)
"""

from datetime import date

import pytest

from accounting.settlement_imports import (
    SettlementImportError,
    preview_settlement_import,
)
from projections.models import FiscalPeriod
from projections.write_barrier import projection_writes_allowed

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def april_2026_period(db, company):
    """Create an OPEN FiscalPeriod covering April 2026 (the date range used
    in the test CSVs below)."""
    with projection_writes_allowed():
        fp, _ = FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=2026,
            period=4,
            defaults=dict(
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 30),
                status=FiscalPeriod.Status.OPEN,
            ),
        )
    return fp


# Same CSV shape as test_settlement_imports.py
PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,A85-BATCH-A,2026-04-25
ORD-2,500.00,15.00,485.00,A85-BATCH-A,2026-04-25
ORD-3,2000.00,60.00,1940.00,A85-BATCH-B,2026-04-26
"""


# =============================================================================
# Happy path
# =============================================================================


@pytest.mark.django_db
def test_preview_paymob_returns_per_batch_summary(company, april_2026_period):
    """Basic happy path: preview parses the CSV and returns one entry per batch
    plus an aggregate summary, without emitting any events."""
    from events.models import BusinessEvent

    events_before = BusinessEvent.objects.filter(company=company).count()

    preview = preview_settlement_import(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="test.csv",
    )

    # NO events emitted (dry-run guarantee)
    events_after = BusinessEvent.objects.filter(company=company).count()
    assert events_after == events_before, "preview_settlement_import must NOT emit events"

    # Per-batch entries
    assert len(preview["batches"]) == 2
    batches_by_id = {b["batch_id"]: b for b in preview["batches"]}
    assert "A85-BATCH-A" in batches_by_id
    assert "A85-BATCH-B" in batches_by_id

    batch_a = batches_by_id["A85-BATCH-A"]
    assert batch_a["gross"] == "1500.00"
    assert batch_a["fees"] == "45.00"
    assert batch_a["net"] == "1455.00"
    assert batch_a["line_count"] == 2
    assert batch_a["will_create_journal_entry"] is True
    assert batch_a["already_imported"] is False

    # Summary aggregate
    summary = preview["summary"]
    assert summary["total_batches"] == 2
    assert summary["total_journal_entries_to_create"] == 2
    assert summary["total_gross"] == "3500.00"
    assert summary["total_fees"] == "105.00"
    assert summary["total_net"] == "3395.00"
    assert summary["blockers"] == []
    assert summary["dry_run_safe"] is True


# =============================================================================
# Period resolution
# =============================================================================


@pytest.mark.django_db
def test_preview_resolves_period_from_payout_date(company, april_2026_period):
    """Each batch's resolved_period must reflect the FiscalPeriod the
    payout_date falls into."""
    preview = preview_settlement_import(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
    )

    for batch in preview["batches"]:
        rp = batch["resolved_period"]
        assert rp["resolved"] is True
        assert rp["fiscal_year"] == 2026
        assert rp["period"] == 4
        assert rp["status"] == FiscalPeriod.Status.OPEN
        assert rp["warning"] is None

    # Periods_affected aggregates by (year, period)
    pa = preview["summary"]["periods_affected"]
    assert len(pa) == 1
    assert pa[0]["fiscal_year"] == 2026
    assert pa[0]["period"] == 4
    assert pa[0]["journal_entries"] == 2


@pytest.mark.django_db
def test_preview_flags_missing_period_as_blocker(company):
    """If no FiscalPeriod covers the payout date, the preview returns a
    blocker (cannot determine which period to post to)."""
    # Deliberately NOT creating april_2026_period
    preview = preview_settlement_import(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
    )

    assert preview["summary"]["dry_run_safe"] is False
    assert any("No FiscalPeriod" in b or "configured covering" in b for b in preview["summary"]["blockers"])


# =============================================================================
# Closed-period detection
# =============================================================================


@pytest.mark.django_db
def test_preview_flags_closed_period_as_blocker(company, april_2026_period):
    """If the resolved period is CLOSED, the preview flags it as a blocker
    so the operator sees they can't post without reopening (or overriding
    once A85 chunk 3 ships the override audit log)."""
    april_2026_period.status = FiscalPeriod.Status.CLOSED
    april_2026_period.save()

    preview = preview_settlement_import(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
    )

    assert preview["summary"]["dry_run_safe"] is False
    assert any("CLOSED" in b for b in preview["summary"]["blockers"])

    # Each batch carries the warning too
    for batch in preview["batches"]:
        assert any("CLOSED" in w for w in batch["warnings"])


# =============================================================================
# Dedup detection
# =============================================================================


@pytest.mark.django_db
def test_preview_flags_already_imported_batches(company, april_2026_period):
    """If the same idempotency_key already exists in BusinessEvent, the
    preview marks the batch as already_imported and excludes it from
    the JE count."""

    from events.models import BusinessEvent, CompanyEventCounter

    # Pre-emit an event with the same idempotency_key the preview would compute
    counter, _ = CompanyEventCounter.objects.get_or_create(company=company)
    counter.last_sequence += 1
    counter.save()
    BusinessEvent.objects.create(
        company=company,
        event_type="payment.settlement.received",
        aggregate_type="PaymentSettlement",
        aggregate_id="paymob:A85-BATCH-A",
        company_sequence=counter.last_sequence,
        idempotency_key="payment.settlement.received:paymob:A85-BATCH-A",
        data={},
    )

    preview = preview_settlement_import(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
    )

    batches_by_id = {b["batch_id"]: b for b in preview["batches"]}
    assert batches_by_id["A85-BATCH-A"]["already_imported"] is True
    assert batches_by_id["A85-BATCH-A"]["will_create_journal_entry"] is False
    assert batches_by_id["A85-BATCH-B"]["already_imported"] is False
    assert batches_by_id["A85-BATCH-B"]["will_create_journal_entry"] is True

    # Only one new JE will be created (BATCH-B)
    assert preview["summary"]["total_journal_entries_to_create"] == 1


# =============================================================================
# Error paths
# =============================================================================


@pytest.mark.django_db
def test_preview_raises_on_unsupported_provider(company):
    with pytest.raises(SettlementImportError):
        preview_settlement_import(
            company=company,
            provider_normalized_code="stripe",  # not yet supported
            file_content=PAYMOB_CSV,
        )


@pytest.mark.django_db
def test_preview_handles_empty_csv(company):
    """Empty CSV (header only, no data rows) raises SettlementImportError
    from the underlying parser — preview doesn't swallow it."""
    empty = b"order_id,gross,fee,net,payout_batch_id,payout_date\n"

    with pytest.raises(SettlementImportError):
        preview_settlement_import(
            company=company,
            provider_normalized_code="paymob",
            file_content=empty,
        )
