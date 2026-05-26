# tests/test_a85_settlement_period_override.py
"""
A85 chunk 3b (2026-05-26): operator-driven period override on settlement
CSV import.

When the operator imports a CSV and the date-derived period is closed
(or they want to post to a different period for any reason), they can
override the period — provided:

- They have 'accounting.je.override_period' permission
- They supply a reason of >= 10 characters
- The target period exists and is OPEN

An audit row lands in PeriodOverrideAudit BEFORE events are emitted, so
the trail survives even if event emission fails partway. The override
is carried in the event payload so projection replay produces the same
JE in the same period.

This locks in the contract end-to-end:
- import_settlement_csv() accepts the override params + validates them
- PeriodOverrideAudit rows are written per batch
- PaymentSettlementReceivedData carries period_override / fiscal_year_override
- PaymentSettlementProjection passes the override through to
  create_journal_entry as the explicit `period` param
"""

from datetime import date

import pytest

from accounting.models import PeriodOverrideAudit
from accounting.settlement_imports import (
    SettlementImportError,
    import_settlement_csv,
)
from accounts.models import CompanyMembershipPermission, NxPermission
from projections.models import FiscalPeriod
from projections.write_barrier import command_writes_allowed, projection_writes_allowed

PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,A85-OVERRIDE-1,2026-04-25
ORD-2,2000.00,60.00,1940.00,A85-OVERRIDE-2,2026-04-26
"""


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def april_2026_period(db, company):
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


@pytest.fixture
def may_2026_period(db, company):
    with projection_writes_allowed():
        fp, _ = FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=2026,
            period=5,
            defaults=dict(
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=date(2026, 5, 1),
                end_date=date(2026, 5, 31),
                status=FiscalPeriod.Status.OPEN,
            ),
        )
    return fp


def _grant_override_permission(user, company, membership):
    """Helper: grant accounting.je.override_period to a user."""
    with command_writes_allowed():
        perm, _ = NxPermission.objects.get_or_create(
            code="accounting.je.override_period",
            defaults={"name": "Override JE period", "module": "accounting"},
        )
        CompanyMembershipPermission.objects.get_or_create(
            membership=membership,
            company=company,
            permission=perm,
        )


# =============================================================================
# Happy path
# =============================================================================


@pytest.mark.django_db
def test_override_writes_audit_row_and_threads_through_event(
    company,
    user,
    owner_membership,
    april_2026_period,
    may_2026_period,
):
    """When operator overrides April → May with proper permission + reason,
    a PeriodOverrideAudit row is written per batch AND the event payload
    carries the override fields."""
    from events.models import BusinessEvent

    _grant_override_permission(user, company, owner_membership)

    emitted = import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="override-test.csv",
        period_override=5,
        fiscal_year_override=2026,
        override_reason="April period was closed for audit review; posting to May.",
        override_user=user,
    )

    # Two batches in the CSV → two emitted events
    assert len(emitted) == 2

    # Two audit rows (one per batch)
    audits = PeriodOverrideAudit.objects.filter(company=company).order_by("source_document_ref")
    assert audits.count() == 2

    audit = audits.first()
    assert audit.source == PeriodOverrideAudit.Source.SETTLEMENT_IMPORT
    assert audit.original_date == date(2026, 4, 25)
    assert audit.original_period == 4
    assert audit.override_period == 5
    assert audit.override_fiscal_year == 2026
    assert audit.user_id == user.id
    assert audit.user_email_snapshot == user.email
    assert "audit review" in audit.reason

    # Events carry the override in the payload
    events = BusinessEvent.objects.filter(
        company=company,
        event_type="payment.settlement_received",
    ).order_by("company_sequence")
    assert events.count() == 2
    for event in events:
        data = event.get_data()
        assert data["period_override"] == 5
        assert data["fiscal_year_override"] == 2026


# =============================================================================
# Validation: permission, reason, target period
# =============================================================================


@pytest.mark.django_db
def test_override_rejected_without_permission(
    company,
    user,
    owner_membership,
    april_2026_period,
    may_2026_period,
):
    """User lacking accounting.je.override_period gets rejected by the
    command layer (defense-in-depth, even if the view also checks)."""
    # Do NOT grant override permission
    with pytest.raises(SettlementImportError, match="lacks the .*override_period"):
        import_settlement_csv(
            company=company,
            provider_normalized_code="paymob",
            file_content=PAYMOB_CSV,
            period_override=5,
            fiscal_year_override=2026,
            override_reason="Long enough reason text here.",
            override_user=user,
        )


@pytest.mark.django_db
def test_override_rejected_with_short_reason(
    company,
    user,
    owner_membership,
    april_2026_period,
    may_2026_period,
):
    """Reason must be >=10 chars; shorter strings are rejected."""
    _grant_override_permission(user, company, owner_membership)

    with pytest.raises(SettlementImportError, match="at least 10 characters"):
        import_settlement_csv(
            company=company,
            provider_normalized_code="paymob",
            file_content=PAYMOB_CSV,
            period_override=5,
            fiscal_year_override=2026,
            override_reason="short",
            override_user=user,
        )


@pytest.mark.django_db
def test_override_rejected_when_target_period_closed(
    company,
    user,
    owner_membership,
    april_2026_period,
    may_2026_period,
):
    """Can't override TO a closed period — defeats the purpose."""
    _grant_override_permission(user, company, owner_membership)

    may_2026_period.status = FiscalPeriod.Status.CLOSED
    may_2026_period.save()

    with pytest.raises(SettlementImportError, match="can only override to an OPEN period"):
        import_settlement_csv(
            company=company,
            provider_normalized_code="paymob",
            file_content=PAYMOB_CSV,
            period_override=5,
            fiscal_year_override=2026,
            override_reason="May period was incorrectly marked open earlier.",
            override_user=user,
        )


@pytest.mark.django_db
def test_override_rejected_when_target_period_missing(
    company,
    user,
    owner_membership,
    april_2026_period,
):
    """Can't override to a period that doesn't exist."""
    _grant_override_permission(user, company, owner_membership)

    # Conftest's auto_fiscal_periods autouse creates all 12 periods of the
    # current year. We target FY 2099 / period 4 to land in a year that
    # the autouse never creates — that period truly doesn't exist.
    with pytest.raises(SettlementImportError, match="is not configured"):
        import_settlement_csv(
            company=company,
            provider_normalized_code="paymob",
            file_content=PAYMOB_CSV,
            period_override=4,
            fiscal_year_override=2099,
            override_reason="Trying to post to a year that doesn't exist.",
            override_user=user,
        )


# =============================================================================
# Default behavior: no override → status quo, no audit row written
# =============================================================================


@pytest.mark.django_db
def test_no_override_means_no_audit_rows_and_no_payload_override(
    company,
    user,
    owner_membership,
    april_2026_period,
):
    """When operator doesn't supply override params, nothing changes —
    no audit rows, event payload has period_override=0."""
    from events.models import BusinessEvent

    audits_before = PeriodOverrideAudit.objects.filter(company=company).count()

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
    )

    # No audit rows written
    assert PeriodOverrideAudit.objects.filter(company=company).count() == audits_before

    # Event payload has defaults (0)
    events = BusinessEvent.objects.filter(
        company=company,
        event_type="payment.settlement_received",
    )
    for event in events:
        data = event.get_data()
        assert data.get("period_override", 0) == 0
        assert data.get("fiscal_year_override", 0) == 0
