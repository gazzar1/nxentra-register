"""
A152 item 4 — unify the manual and system period gates via on-demand fiscal
period provisioning.

Pre-A152 the two gates diverged on a date with no FiscalPeriod row:
- the MANUAL gate (accounting.policies.can_post_to_period) refused loudly;
- the SYSTEM gate (accounting.validation._check_period) silently ALLOWED and
  the JE was stamped with a degraded calendar-month period number.

Now both auto-provision the year's 13 periods for an in-range date (so a
postable date always resolves a real, correctly-numbered period) and refuse
identically for out-of-sane-range dates.
"""

from datetime import date

import pytest

from accounting.policies import can_post_to_period
from accounting.validation import _check_period
from accounts.authz import ActorContext
from projections.models import FiscalPeriod, FiscalPeriodConfig
from projections.models import FiscalYear as FiscalYearModel
from projections.periods import (
    _AUTOPROVISION_LOOKBACK_YEARS,
    ensure_fiscal_periods_for_date,
)


def _make_actor(company, user, membership):
    perms = frozenset(membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=membership, perms=perms)


def _in_range_unconfigured_year() -> int:
    # A year comfortably inside the sane window but not the current (seeded) one.
    return date.today().year - 2


@pytest.mark.django_db
def test_ensure_creates_13_periods_idempotently(company):
    fy = _in_range_unconfigured_year()
    assert not FiscalPeriod.objects.filter(company=company, fiscal_year=fy).exists()

    assert ensure_fiscal_periods_for_date(company, date(fy, 6, 15)) is True

    periods = FiscalPeriod.objects.filter(company=company, fiscal_year=fy)
    assert periods.count() == 13
    assert periods.filter(period_type=FiscalPeriod.PeriodType.NORMAL).count() == 12
    assert periods.filter(period_type=FiscalPeriod.PeriodType.ADJUSTMENT, period=13).count() == 1
    assert all(p.status == FiscalPeriod.Status.OPEN for p in periods)
    assert FiscalPeriodConfig.objects.filter(company=company, fiscal_year=fy).exists()
    assert FiscalYearModel.objects.filter(company=company, fiscal_year=fy, status=FiscalYearModel.Status.OPEN).exists()

    # Idempotent — a second call neither duplicates nor errors.
    assert ensure_fiscal_periods_for_date(company, date(fy, 1, 1)) is True
    assert FiscalPeriod.objects.filter(company=company, fiscal_year=fy).count() == 13


@pytest.mark.django_db
def test_ensure_refuses_out_of_range(company):
    # Far past (typo year) and far future both leave nothing.
    old = date.today().year - (_AUTOPROVISION_LOOKBACK_YEARS + 5)
    future = date.today().year + 5
    assert ensure_fiscal_periods_for_date(company, date(old, 1, 1)) is False
    assert ensure_fiscal_periods_for_date(company, date(future, 1, 1)) is False
    assert not FiscalPeriod.objects.filter(company=company, fiscal_year=old).exists()
    assert not FiscalPeriod.objects.filter(company=company, fiscal_year=future).exists()


@pytest.mark.django_db
def test_ensure_empty_or_bad_date_returns_false(company):
    assert ensure_fiscal_periods_for_date(company, None) is False
    assert ensure_fiscal_periods_for_date(company, "not-a-date") is False


@pytest.mark.django_db
def test_system_gate_allows_and_provisions_in_range_year(company):
    """_check_period auto-provisions an in-range unconfigured year and allows."""
    fy = _in_range_unconfigured_year()
    d = date(fy, 6, 15)
    assert not FiscalPeriod.objects.filter(company=company, fiscal_year=fy).exists()

    assert _check_period(company, d) is None  # allowed
    assert FiscalPeriod.objects.filter(company=company, fiscal_year=fy).count() == 13


@pytest.mark.django_db
def test_system_gate_refuses_out_of_range(company):
    """The system gate no longer silently allows: an out-of-range date refuses,
    matching the manual gate (was: returned None = allow)."""
    d = date(date.today().year - (_AUTOPROVISION_LOOKBACK_YEARS + 5), 6, 15)
    err = _check_period(company, d)
    assert err is not None
    assert "no fiscal period" in err.lower()


@pytest.mark.django_db
def test_manual_gate_allows_in_range_unconfigured(company, user, owner_membership):
    actor = _make_actor(company, user, owner_membership)
    fy = _in_range_unconfigured_year()
    allowed, reason = can_post_to_period(actor, date(fy, 6, 15))
    assert allowed, f"in-range unconfigured date should auto-provision and allow: {reason}"
    assert FiscalPeriod.objects.filter(company=company, fiscal_year=fy).count() == 13


@pytest.mark.django_db
def test_manual_gate_refuses_out_of_range(company, user, owner_membership):
    actor = _make_actor(company, user, owner_membership)
    d = date(date.today().year - (_AUTOPROVISION_LOOKBACK_YEARS + 5), 6, 15)
    allowed, reason = can_post_to_period(actor, d)
    assert not allowed
    assert "no fiscal period" in reason.lower()


@pytest.mark.django_db
def test_start_month_change_never_creates_overlapping_calendar(company, user, owner_membership):
    """Review C1/C7 (blocker class): a company seeded with calendar-year periods
    whose fiscal_year_start_month later changes must NOT get a second,
    overlapping fiscal calendar auto-provisioned. The gate refuses loudly
    instead — surfacing the misconfiguration, never corrupting the rows."""
    this_year = date.today().year
    # The conftest company is seeded with calendar periods for the current year
    # (start_month=1). Simulate the onboarding/settings change to an April start.
    company.fiscal_year_start_month = 4
    company.save()

    before = FiscalPeriod.objects.filter(company=company).count()
    # Feb of the current year computes fiscal_year = this_year - 1 under the
    # NEW convention; provisioning that label (Apr prev — Mar current) would
    # overlap the seeded Jan–Dec calendar. Must refuse, not create.
    assert ensure_fiscal_periods_for_date(company, date(this_year, 2, 15)) is True  # covered by seeded rows
    assert FiscalPeriod.objects.filter(company=company).count() == before  # nothing created

    # A date NOT covered by any row but whose computed span overlaps the seeded
    # calendar (Jan of NEXT year → label this_year under April-start; span
    # Apr this_year – Mar next overlaps seeded Jan–Dec this_year): refuse.
    result = ensure_fiscal_periods_for_date(company, date(this_year + 1, 1, 15))
    assert result is False
    assert FiscalPeriod.objects.filter(company=company).count() == before  # still nothing

    # And both gates refuse that date loudly rather than resolving a wrong row.
    err = _check_period(company, date(this_year + 1, 1, 15))
    assert err is not None and "no fiscal period" in err.lower()
    actor = _make_actor(company, user, owner_membership)
    allowed, reason = can_post_to_period(actor, date(this_year + 1, 1, 15))
    assert not allowed and "no fiscal period" in reason.lower()


@pytest.mark.django_db
def test_coverage_fast_path_ignores_label_mismatch(company):
    """Review C7: the fast path keys on DATE COVERAGE, not the fiscal_year
    label — a date covered by rows under a different labeling convention
    reports provisioned without creating anything."""
    from projections.write_barrier import projection_writes_allowed

    this_year = date.today().year
    # Hand-create a period under an end-year label (the onboarding wizard's
    # convention for Jul–Dec starts): July this_year labeled fiscal_year+1.
    with projection_writes_allowed():
        FiscalPeriod.objects.create(
            company=company,
            fiscal_year=this_year + 1,  # end-year label
            period=1,
            period_type=FiscalPeriod.PeriodType.NORMAL,
            start_date=date(this_year, 7, 1),
            end_date=date(this_year, 7, 31),
            status=FiscalPeriod.Status.OPEN,
        )
    before = FiscalPeriod.objects.filter(company=company).count()
    assert ensure_fiscal_periods_for_date(company, date(this_year, 7, 15)) is True
    assert FiscalPeriod.objects.filter(company=company).count() == before  # no duplicate calendar


@pytest.mark.django_db
def test_non_january_fiscal_start_stamps_correct_period_number(company):
    """The degraded-calendar-month bug: an April-start company's April date is
    period 1, not period 4. Auto-provisioning honours fiscal_year_start_month."""
    company.fiscal_year_start_month = 4
    company.save()
    fy = _in_range_unconfigured_year()

    assert ensure_fiscal_periods_for_date(company, date(fy, 4, 15)) is True
    fp = FiscalPeriod.objects.get(
        company=company,
        start_date__lte=date(fy, 4, 15),
        end_date__gte=date(fy, 4, 15),
        period_type=FiscalPeriod.PeriodType.NORMAL,
    )
    assert fp.period == 1  # April = period 1 for an April fiscal-year start
    assert fp.fiscal_year == fy
