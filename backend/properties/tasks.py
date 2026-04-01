# properties/tasks.py
"""
Celery tasks for property management module.

Daily tasks:
- post_rent_dues_and_detect_overdue: Transition schedule lines
  from UPCOMING → DUE and DUE → OVERDUE based on dates and grace periods.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.db import transaction

from accounts.models import Company
from accounts.rls import rls_bypass
from events.emitter import emit_event_no_actor
from events.payload_policy import PayloadOrigin
from events.types import EventTypes
from projections.write_barrier import command_writes_allowed

from .event_types import (
    LeaseExpiryAlertData,
    RentDuePostedData,
    RentOverdueDetectedData,
)
from .models import Lease, RentScheduleLine

logger = logging.getLogger(__name__)


@shared_task(name="properties.post_rent_dues_and_detect_overdue")
def post_rent_dues_and_detect_overdue():
    """
    Daily task: transition upcoming → due and due → overdue.

    Runs for all active companies. Each company is processed independently
    so that a failure in one company does not block others.
    """
    with rls_bypass():
        companies = list(Company.objects.filter(is_active=True))

    for company in companies:
        try:
            _process_company(company)
        except Exception:
            logger.exception(
                "Error processing rent dues for company %s", company.name
            )


def _process_projections(company):
    """Run all registered projections synchronously after events are emitted."""
    if not settings.PROJECTIONS_SYNC:
        return

    from projections.base import projection_registry

    for projection in projection_registry.all():
        projection.process_pending(company, limit=1000)


def _process_company(company):
    """Process rent schedule transitions for a single company."""
    # PRD A.8: use company timezone (field may not exist yet, default UTC)
    tz = ZoneInfo(getattr(company, "timezone", None) or "UTC")
    today = datetime.now(tz).date()

    with rls_bypass():
        # 1. UPCOMING → DUE  (due_date <= today)
        upcoming_lines = (
            RentScheduleLine.objects.filter(
                company=company,
                status=RentScheduleLine.ScheduleStatus.UPCOMING,
                due_date__lte=today,
            )
            .select_related("lease")
        )

        for line in upcoming_lines:
            _post_due(company, line)

        # 2. DUE → OVERDUE  (due_date + grace_days < today)
        due_lines = (
            RentScheduleLine.objects.filter(
                company=company,
                status=RentScheduleLine.ScheduleStatus.DUE,
            )
            .select_related("lease")
        )

        for line in due_lines:
            grace_days = line.lease.grace_days or 0
            if line.due_date + timedelta(days=grace_days) < today:
                _detect_overdue(company, line, today)

    # Process projections after all events have been emitted
    _process_projections(company)


@transaction.atomic
def _post_due(company, line):
    """Transition a single schedule line from UPCOMING to DUE."""
    with command_writes_allowed():
        line.status = RentScheduleLine.ScheduleStatus.DUE
        line.save(update_fields=["status", "updated_at"])

    emit_event_no_actor(
        company,
        event_type=EventTypes.RENT_DUE_POSTED,
        aggregate_type="RentScheduleLine",
        aggregate_id=str(line.public_id),
        idempotency_key=f"rent.due_posted:{line.public_id}",
        data=RentDuePostedData(
            schedule_line_public_id=str(line.public_id),
            lease_public_id=str(line.lease.public_id),
            contract_no=line.lease.contract_no,
            installment_no=line.installment_no,
            due_date=str(line.due_date),
            total_due=str(line.total_due),
            currency=line.lease.currency,
        ),
        payload_origin=PayloadOrigin.SYSTEM_BATCH,
    )

    logger.info(
        "Posted due: lease %s installment %s",
        line.lease.contract_no,
        line.installment_no,
    )


@transaction.atomic
def _detect_overdue(company, line, today):
    """Transition a single schedule line from DUE to OVERDUE."""
    days_overdue = (today - line.due_date).days

    with command_writes_allowed():
        line.status = RentScheduleLine.ScheduleStatus.OVERDUE
        line.save(update_fields=["status", "updated_at"])

    emit_event_no_actor(
        company,
        event_type=EventTypes.RENT_OVERDUE_DETECTED,
        aggregate_type="RentScheduleLine",
        aggregate_id=str(line.public_id),
        idempotency_key=f"rent.overdue_detected:{line.public_id}",
        data=RentOverdueDetectedData(
            schedule_line_public_id=str(line.public_id),
            lease_public_id=str(line.lease.public_id),
            contract_no=line.lease.contract_no,
            installment_no=line.installment_no,
            due_date=str(line.due_date),
            outstanding=str(line.outstanding),
            currency=line.lease.currency,
            days_overdue=days_overdue,
        ),
        payload_origin=PayloadOrigin.SYSTEM_BATCH,
    )

    logger.info(
        "Detected overdue: lease %s installment %s (%d days)",
        line.lease.contract_no,
        line.installment_no,
        days_overdue,
    )


# =============================================================================
# Lease Expiry Alert Task
# =============================================================================

EXPIRY_THRESHOLDS = [90, 60, 30]


@shared_task(name="properties.check_lease_expiry")
def check_lease_expiry():
    """
    Daily task: flag leases with end_date within 90/60/30 days.

    Emits lease.expiry_alert events with idempotency keys per PRD A.2.
    """
    with rls_bypass():
        companies = list(Company.objects.filter(is_active=True))

    for company in companies:
        try:
            _check_expiry_for_company(company)
        except Exception:
            logger.exception(
                "Error checking lease expiry for company %s", company.name
            )


def _check_expiry_for_company(company):
    """Check lease expiry alerts for a single company."""
    tz = ZoneInfo(getattr(company, "timezone", None) or "UTC")
    today = datetime.now(tz).date()

    with rls_bypass():
        for threshold in EXPIRY_THRESHOLDS:
            cutoff = today + timedelta(days=threshold)
            # Find leases expiring on exactly the threshold day or
            # within the window (for first-time detection)
            if threshold == 90:
                lower = today + timedelta(days=61)
            elif threshold == 60:
                lower = today + timedelta(days=31)
            else:
                lower = today

            leases = (
                Lease.objects.filter(
                    company=company,
                    status=Lease.LeaseStatus.ACTIVE,
                    end_date__gte=lower,
                    end_date__lte=cutoff,
                )
                .select_related("property", "unit", "lessee")
            )

            for lease in leases:
                _emit_expiry_alert(company, lease, today, threshold)


def _emit_expiry_alert(company, lease, today, threshold):
    """Emit a lease expiry alert event."""
    days_until = (lease.end_date - today).days

    emit_event_no_actor(
        company,
        event_type=EventTypes.LEASE_EXPIRY_ALERT,
        aggregate_type="Lease",
        aggregate_id=str(lease.public_id),
        idempotency_key=f"lease.expiry_alert:{lease.public_id}:{threshold}",
        data=LeaseExpiryAlertData(
            lease_public_id=str(lease.public_id),
            contract_no=lease.contract_no,
            property_public_id=str(lease.property.public_id),
            unit_public_id=str(lease.unit.public_id) if lease.unit else "",
            lessee_public_id=str(lease.lessee.public_id),
            lessee_name=lease.lessee.display_name,
            start_date=str(lease.start_date),
            end_date=str(lease.end_date),
            threshold_days=threshold,
            days_until_expiry=days_until,
        ),
        payload_origin=PayloadOrigin.SYSTEM_BATCH,
    )

    logger.info(
        "Expiry alert: lease %s expires in %d days (threshold %d)",
        lease.contract_no,
        days_until,
        threshold,
    )
