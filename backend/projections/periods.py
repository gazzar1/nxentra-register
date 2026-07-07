"""
Fiscal period projection.

Handles creation and lifecycle of fiscal periods and fiscal years.
Standard setup: 12 monthly periods + 1 adjustment period (P13).
"""

import calendar
from datetime import date

from accounts.models import Company
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection, projection_registry
from projections.models import FiscalPeriod, FiscalPeriodConfig, FiscalYear


def _fiscal_year_for_date(target_date: date, start_month: int) -> int:
    return target_date.year - 1 if target_date.month < start_month else target_date.year


def _period_dates(fiscal_year: int, start_month: int, period: int) -> tuple[date, date]:
    """Calculate start/end dates for a monthly-aligned period."""
    offset = period - 1
    month_index = (start_month - 1) + offset
    year = fiscal_year + (month_index // 12)
    month = (month_index % 12) + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


# A152 item 4 — sane range for on-demand fiscal-period provisioning. A date
# whose fiscal year is within [today's FY − LOOKBACK, today's FY + LOOKAHEAD]
# auto-provisions its 13 periods; anything outside (typo years like 0002/9999)
# is left unprovisioned so both posting gates refuse it loudly. 10y back covers
# A126 historical import and Shopify/settlement backfill.
_AUTOPROVISION_LOOKBACK_YEARS = 10
_AUTOPROVISION_LOOKAHEAD_YEARS = 1


def ensure_fiscal_periods_for_date(company, target_date) -> bool:
    """A152 item 4 — lazily provision a company's 13 fiscal periods for the
    fiscal year containing ``target_date`` when they don't exist yet.

    Both the manual (`accounting.policies.can_post_to_period`) and system
    (`accounting.validation._check_period`) posting gates call this first, so a
    postable date always resolves a real FiscalPeriod — which unifies their
    no-row behaviour (both refuse only genuinely out-of-range dates) AND stamps
    the correct fiscal period number instead of the degraded calendar month.

    Mirrors the COMPANY_CREATED seeding but is idempotent + race-safe
    (get_or_create against the uniq_fiscal_period/-year/-config constraints).
    Returns True when the year's periods exist (already or freshly created);
    False when ``target_date`` is empty/unparseable or outside the sane range,
    in which case nothing is created and the caller refuses.

    NOTE: this writes read-model rows directly rather than via the
    COMPANY_CREATED projection (the gates need them synchronously; projection
    processing is async). Consistent with the existing direct-write precedents
    (accounts.commands._create_periods, seed_shopify_demo._ensure_fiscal_periods).
    A full event replay does not recreate on-demand years — an accepted
    limitation shared with those precedents; the rows are deterministically
    re-derivable from the company's fiscal-year-start month + date.
    """
    from datetime import date as _date
    from datetime import datetime as _datetime

    from django.db import transaction

    from projections.write_barrier import projection_writes_allowed

    if not target_date:
        return False
    if isinstance(target_date, str):
        try:
            target_date = _datetime.fromisoformat(target_date).date()
        except ValueError:
            return False
    elif isinstance(target_date, _datetime):
        target_date = target_date.date()
    elif not isinstance(target_date, _date):
        return False

    start_month = company.fiscal_year_start_month or 1
    fiscal_year = _fiscal_year_for_date(target_date, start_month)

    today_fy = _fiscal_year_for_date(date.today(), start_month)
    if not (today_fy - _AUTOPROVISION_LOOKBACK_YEARS <= fiscal_year <= today_fy + _AUTOPROVISION_LOOKAHEAD_YEARS):
        return False

    # Idempotent fast path — COVERAGE-based, not label-based: if any NORMAL
    # period already contains the date, the gates can resolve it, whatever
    # fiscal-year label it carries. Labels are NOT trusted because two seeding
    # conventions coexist (start-year labeling here vs the onboarding wizard's
    # end-year labeling for Jul–Dec starts) and fiscal_year_start_month can be
    # changed after seeding without regenerating rows.
    if FiscalPeriod.objects.filter(
        company=company,
        start_date__lte=target_date,
        end_date__gte=target_date,
        period_type=FiscalPeriod.PeriodType.NORMAL,
    ).exists():
        return True

    # Overlap guard: NEVER create a year whose computed span intersects any
    # existing period (a fiscal-start change after seeding, or a labeling
    # mismatch, would otherwise mint a second overlapping calendar — two NORMAL
    # rows covering one date, nondeterministic gate results, closes landing on
    # the wrong row). Refusing here means the gates refuse loudly (the pre-A152
    # manual behaviour), surfacing the misconfiguration instead of papering
    # over it with corrupted rows.
    span_start, _ = _period_dates(fiscal_year, start_month, 1)
    _, span_end = _period_dates(fiscal_year, start_month, 12)
    if FiscalPeriod.objects.filter(
        company=company,
        start_date__lte=span_end,
        end_date__gte=span_start,
    ).exists():
        return False

    with projection_writes_allowed(), transaction.atomic():
        for period in range(1, 13):
            start_date, end_date = _period_dates(fiscal_year, start_month, period)
            FiscalPeriod.objects.get_or_create(
                company=company,
                fiscal_year=fiscal_year,
                period=period,
                defaults={
                    "period_type": FiscalPeriod.PeriodType.NORMAL,
                    "start_date": start_date,
                    "end_date": end_date,
                    "status": FiscalPeriod.Status.OPEN,
                },
            )
        # Period 13: adjustment period sharing Period 12's end date.
        _, p12_end = _period_dates(fiscal_year, start_month, 12)
        FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=fiscal_year,
            period=13,
            defaults={
                "period_type": FiscalPeriod.PeriodType.ADJUSTMENT,
                "start_date": p12_end,
                "end_date": p12_end,
                "status": FiscalPeriod.Status.OPEN,
            },
        )
        FiscalPeriodConfig.objects.get_or_create(
            company=company,
            fiscal_year=fiscal_year,
            defaults={"period_count": 13, "current_period": 1},
        )
        FiscalYear.objects.get_or_create(
            company=company,
            fiscal_year=fiscal_year,
            defaults={"status": FiscalYear.Status.OPEN},
        )
    return True


class FiscalPeriodProjection(BaseProjection):
    @property
    def name(self) -> str:
        return "fiscal_period_read_model"

    @property
    def consumes(self):
        return [
            EventTypes.COMPANY_CREATED,
            EventTypes.FISCAL_PERIOD_CLOSED,
            EventTypes.FISCAL_PERIOD_OPENED,
            EventTypes.FISCAL_PERIODS_CONFIGURED,
            EventTypes.FISCAL_PERIOD_RANGE_SET,
            EventTypes.FISCAL_PERIOD_CURRENT_SET,
            EventTypes.FISCAL_PERIOD_DATES_UPDATED,
            EventTypes.FISCAL_YEAR_CLOSED,
            EventTypes.FISCAL_YEAR_REOPENED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        data = event.get_data()

        if event.event_type == EventTypes.COMPANY_CREATED:
            company, _ = Company.objects.update_or_create(
                public_id=data["company_public_id"],
                defaults={
                    "name": data.get("name", ""),
                    "name_ar": data.get("name_ar", ""),
                    "slug": data.get("slug", ""),
                    "default_currency": data.get("default_currency", "USD"),
                    "fiscal_year_start_month": data.get("fiscal_year_start_month", 1),
                    "is_active": data.get("is_active", True),
                },
            )

            fiscal_year = _fiscal_year_for_date(date.today(), company.fiscal_year_start_month)
            if FiscalPeriod.objects.filter(company=company, fiscal_year=fiscal_year).exists():
                return

            # Create 12 monthly periods + Period 13 (adjustment)
            for period in range(1, 13):
                start_date, end_date = _period_dates(
                    fiscal_year,
                    company.fiscal_year_start_month,
                    period,
                )
                FiscalPeriod.objects.create(
                    company=company,
                    fiscal_year=fiscal_year,
                    period=period,
                    period_type=FiscalPeriod.PeriodType.NORMAL,
                    start_date=start_date,
                    end_date=end_date,
                    status=FiscalPeriod.Status.OPEN,
                )

            # Period 13: adjustment period with same end date as Period 12
            _, p12_end = _period_dates(fiscal_year, company.fiscal_year_start_month, 12)
            FiscalPeriod.objects.create(
                company=company,
                fiscal_year=fiscal_year,
                period=13,
                period_type=FiscalPeriod.PeriodType.ADJUSTMENT,
                start_date=p12_end,
                end_date=p12_end,
                status=FiscalPeriod.Status.OPEN,
            )

            # Create default config (13 periods)
            FiscalPeriodConfig.objects.get_or_create(
                company=company,
                fiscal_year=fiscal_year,
                defaults={"period_count": 13, "current_period": 1},
            )

            # Create FiscalYear record
            FiscalYear.objects.get_or_create(
                company=company,
                fiscal_year=fiscal_year,
                defaults={"status": FiscalYear.Status.OPEN},
            )
            return

        if event.event_type == EventTypes.FISCAL_PERIOD_CLOSED:
            company = Company.objects.filter(public_id=data["company_public_id"]).first()
            if not company:
                return

            fiscal_year = int(data["fiscal_year"])
            period = int(data["period"])
            FiscalPeriod.objects.filter(
                company=company,
                fiscal_year=fiscal_year,
                period=period,
            ).update(status=FiscalPeriod.Status.CLOSED)
            return

        if event.event_type == EventTypes.FISCAL_PERIOD_OPENED:
            company = Company.objects.filter(public_id=data["company_public_id"]).first()
            if not company:
                return

            fiscal_year = int(data["fiscal_year"])
            period = int(data["period"])
            FiscalPeriod.objects.filter(
                company=company,
                fiscal_year=fiscal_year,
                period=period,
            ).update(status=FiscalPeriod.Status.OPEN)
            return

        if event.event_type == EventTypes.FISCAL_PERIODS_CONFIGURED:
            company = Company.objects.filter(public_id=data["company_public_id"]).first()
            if not company:
                return

            fiscal_year = int(data["fiscal_year"])
            period_count = int(data["period_count"])
            periods_data = data.get("periods", [])

            # Delete old periods for this fiscal year
            FiscalPeriod.objects.filter(
                company=company,
                fiscal_year=fiscal_year,
            ).delete()

            # Determine if this is a year-end auto-creation (previous year closed).
            # Uses explicit schema field; falls back to idempotency_key for old events.
            is_yearend_creation = data.get("is_yearend_creation", False)

            # Create new periods from event data
            for p in periods_data:
                period_num = p["period"]
                if is_yearend_creation:
                    # Year-end auto-creation: only Period 1 is OPEN
                    period_status = FiscalPeriod.Status.OPEN if period_num == 1 else FiscalPeriod.Status.CLOSED
                else:
                    # Manual configuration: all periods OPEN
                    period_status = FiscalPeriod.Status.OPEN

                FiscalPeriod.objects.create(
                    company=company,
                    fiscal_year=fiscal_year,
                    period=period_num,
                    period_type=p.get("period_type", FiscalPeriod.PeriodType.NORMAL),
                    start_date=p["start_date"],
                    end_date=p["end_date"],
                    status=period_status,
                )

            # Update config
            FiscalPeriodConfig.objects.update_or_create(
                company=company,
                fiscal_year=fiscal_year,
                defaults={
                    "period_count": period_count,
                    "current_period": 1 if is_yearend_creation else None,
                    "open_from_period": 1 if is_yearend_creation else None,
                    "open_to_period": 1 if is_yearend_creation else None,
                },
            )

            # Ensure FiscalYear record exists
            FiscalYear.objects.get_or_create(
                company=company,
                fiscal_year=fiscal_year,
                defaults={"status": FiscalYear.Status.OPEN},
            )
            return

        if event.event_type == EventTypes.FISCAL_PERIOD_RANGE_SET:
            company = Company.objects.filter(public_id=data["company_public_id"]).first()
            if not company:
                return

            fiscal_year = int(data["fiscal_year"])
            open_from = int(data["open_from_period"])
            open_to = int(data["open_to_period"])

            # Open periods in range
            FiscalPeriod.objects.filter(
                company=company,
                fiscal_year=fiscal_year,
                period__gte=open_from,
                period__lte=open_to,
            ).update(status=FiscalPeriod.Status.OPEN)

            # Close periods outside range
            FiscalPeriod.objects.filter(
                company=company,
                fiscal_year=fiscal_year,
            ).exclude(
                period__gte=open_from,
                period__lte=open_to,
            ).update(status=FiscalPeriod.Status.CLOSED)

            # Update config
            FiscalPeriodConfig.objects.update_or_create(
                company=company,
                fiscal_year=fiscal_year,
                defaults={
                    "open_from_period": open_from,
                    "open_to_period": open_to,
                },
            )
            return

        if event.event_type == EventTypes.FISCAL_PERIOD_CURRENT_SET:
            company = Company.objects.filter(public_id=data["company_public_id"]).first()
            if not company:
                return

            fiscal_year = int(data["fiscal_year"])
            period = int(data["period"])

            # Clear all is_current for this year
            FiscalPeriod.objects.filter(
                company=company,
                fiscal_year=fiscal_year,
            ).update(is_current=False)

            # Set the target period as current
            FiscalPeriod.objects.filter(
                company=company,
                fiscal_year=fiscal_year,
                period=period,
            ).update(is_current=True)

            # Update config
            FiscalPeriodConfig.objects.update_or_create(
                company=company,
                fiscal_year=fiscal_year,
                defaults={"current_period": period},
            )
            return

        if event.event_type == EventTypes.FISCAL_PERIOD_DATES_UPDATED:
            company = Company.objects.filter(public_id=data["company_public_id"]).first()
            if not company:
                return

            fiscal_year = int(data["fiscal_year"])
            period = int(data["period"])
            FiscalPeriod.objects.filter(
                company=company,
                fiscal_year=fiscal_year,
                period=period,
            ).update(
                start_date=data["start_date"],
                end_date=data["end_date"],
            )
            return

        if event.event_type == EventTypes.FISCAL_YEAR_CLOSED:
            company = Company.objects.filter(public_id=data["company_public_id"]).first()
            if not company:
                return

            fiscal_year = int(data["fiscal_year"])
            FiscalYear.objects.update_or_create(
                company=company,
                fiscal_year=fiscal_year,
                defaults={
                    "status": FiscalYear.Status.CLOSED,
                    "closed_at": data.get("closed_at"),
                    "retained_earnings_entry_public_id": data.get("closing_entry_public_id", ""),
                },
            )
            return

        if event.event_type == EventTypes.FISCAL_YEAR_REOPENED:
            company = Company.objects.filter(public_id=data["company_public_id"]).first()
            if not company:
                return

            fiscal_year = int(data["fiscal_year"])
            FiscalYear.objects.filter(
                company=company,
                fiscal_year=fiscal_year,
            ).update(
                status=FiscalYear.Status.OPEN,
                closed_at=None,
                retained_earnings_entry_public_id="",
            )
            return


projection_registry.register(FiscalPeriodProjection())
