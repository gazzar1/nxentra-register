"""
Celery tasks for async projection processing.

These tasks handle event processing in the background, allowing the API
to remain responsive while projections update read models.

Tasks:
- process_company_projections: Process all projections for a company
- process_all_projections: Process projections for all active companies
- rebuild_projection: Rebuild a single projection from scratch

Usage:
    # Trigger projection processing after an event
    from projections.tasks import process_company_projections
    process_company_projections.delay(company_id=company.id)

    # Scheduled periodic processing
    # Configure in Django admin -> Periodic Tasks
"""
import logging
from typing import Optional

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def process_company_projections(
    self,
    company_id: int,
    projection_names: Optional[list] = None,
    limit: int = 1000,
) -> dict:
    """
    Process all pending projection events for a company.

    Args:
        company_id: ID of the company to process
        projection_names: Optional list of specific projections to process
        limit: Maximum events per projection

    Returns:
        Dict with processing results per projection
    """
    from accounts.models import Company
    from accounts.rls import rls_bypass
    from projections.base import projection_registry

    logger.info(f"Processing projections for company {company_id}")

    with rls_bypass():
        try:
            company = Company.objects.get(id=company_id)
        except Company.DoesNotExist:
            logger.error(f"Company {company_id} not found")
            return {"error": f"Company {company_id} not found"}

    # Get projections to process
    if projection_names:
        projections = [
            projection_registry.get(name)
            for name in projection_names
            if projection_registry.get(name)
        ]
    else:
        projections = projection_registry.all()

    results = {}
    total_processed = 0

    for projection in projections:
        try:
            processed = projection.process_pending(company, limit=limit)
            results[projection.name] = {"processed": processed, "status": "success"}
            total_processed += processed
        except Exception as e:
            logger.exception(f"Error in projection {projection.name}: {e}")
            results[projection.name] = {"error": str(e), "status": "error"}

    logger.info(
        f"Completed projections for company {company_id}: "
        f"{total_processed} events processed"
    )

    return {
        "company_id": company_id,
        "total_processed": total_processed,
        "projections": results,
    }


@shared_task(bind=True)
def process_all_projections(self, limit: int = 1000) -> dict:
    """
    Process projections for all active companies.

    This task is designed to be run periodically (e.g., every minute)
    to catch up on any pending events.

    Args:
        limit: Maximum events per projection per company

    Returns:
        Summary of processing results
    """
    from accounts.models import Company
    from accounts.rls import rls_bypass
    from tenant.models import TenantDirectory

    logger.info("Processing projections for all companies")

    with rls_bypass():
        # Get all active companies
        active_companies = Company.objects.filter(is_active=True)

        # Check tenant status - skip companies being migrated
        migrating_ids = set(
            TenantDirectory.objects.filter(
                status=TenantDirectory.Status.MIGRATING
            ).values_list("company_id", flat=True)
        )

        companies_to_process = [
            c for c in active_companies if c.id not in migrating_ids
        ]

    results = {}
    total_processed = 0

    for company in companies_to_process:
        try:
            result = process_company_projections(
                company_id=company.id,
                limit=limit,
            )
            results[company.slug] = result
            total_processed += result.get("total_processed", 0)
        except Exception as e:
            logger.exception(f"Error processing company {company.slug}: {e}")
            results[company.slug] = {"error": str(e)}

    logger.info(f"Completed all projections: {total_processed} total events processed")

    return {
        "companies_processed": len(companies_to_process),
        "total_events_processed": total_processed,
        "results": results,
    }


@shared_task(
    bind=True,
    max_retries=1,
    time_limit=3600,  # 1 hour
)
def rebuild_projection(
    self,
    company_id: int,
    projection_name: str,
) -> dict:
    """
    Rebuild a projection from scratch for a company.

    This task:
    1. Resets the projection's bookmark
    2. Clears existing projected data
    3. Replays all relevant events

    Args:
        company_id: ID of the company
        projection_name: Name of the projection to rebuild

    Returns:
        Dict with rebuild results
    """
    from accounts.models import Company
    from accounts.rls import rls_bypass
    from projections.base import projection_registry

    logger.info(f"Rebuilding projection {projection_name} for company {company_id}")

    with rls_bypass():
        try:
            company = Company.objects.get(id=company_id)
        except Company.DoesNotExist:
            return {"error": f"Company {company_id} not found"}

    projection = projection_registry.get(projection_name)
    if not projection:
        return {"error": f"Projection {projection_name} not found"}

    try:
        processed = projection.rebuild(company)
        logger.info(
            f"Rebuilt projection {projection_name} for {company.slug}: "
            f"{processed} events processed"
        )
        return {
            "company_id": company_id,
            "projection": projection_name,
            "events_processed": processed,
            "status": "success",
        }
    except Exception as e:
        logger.exception(f"Error rebuilding projection {projection_name}: {e}")
        return {
            "company_id": company_id,
            "projection": projection_name,
            "error": str(e),
            "status": "error",
        }


@shared_task(bind=True)
def rebuild_all_projections(self, company_id: int) -> dict:
    """
    Rebuild all projections for a company.

    Useful after data migration or to fix projection inconsistencies.

    Args:
        company_id: ID of the company

    Returns:
        Dict with rebuild results for each projection
    """
    from projections.base import projection_registry

    logger.info(f"Rebuilding all projections for company {company_id}")

    results = {}
    total_processed = 0

    for projection in projection_registry.all():
        result = rebuild_projection(
            company_id=company_id,
            projection_name=projection.name,
        )
        results[projection.name] = result
        total_processed += result.get("events_processed", 0)

    return {
        "company_id": company_id,
        "total_events_processed": total_processed,
        "projections": results,
    }


@shared_task(bind=True)
def check_projection_health(self) -> dict:
    """
    Health check task for projection processing.

    Reports lag across all projections for alerting purposes.

    Returns:
        Health report with lag metrics
    """
    from accounts.models import Company
    from accounts.rls import rls_bypass
    from events.models import EventBookmark
    from projections.base import projection_registry

    threshold = getattr(settings, "PROJECTION_LAG_THRESHOLD", 1000)

    with rls_bypass():
        companies = Company.objects.filter(is_active=True)

        report = {
            "healthy": True,
            "total_lag": 0,
            "companies_with_lag": [],
            "threshold": threshold,
        }

        for company in companies:
            company_lag = 0
            lagging_projections = []

            for projection in projection_registry.all():
                lag = projection.get_lag(company)
                company_lag += lag

                if lag > 0:
                    lagging_projections.append({
                        "projection": projection.name,
                        "lag": lag,
                    })

            if company_lag > 0:
                report["companies_with_lag"].append({
                    "company": company.slug,
                    "total_lag": company_lag,
                    "projections": lagging_projections,
                })

            report["total_lag"] += company_lag

        if report["total_lag"] >= threshold:
            report["healthy"] = False
            logger.warning(
                f"Projection lag threshold exceeded: {report['total_lag']} >= {threshold}"
            )

    return report
