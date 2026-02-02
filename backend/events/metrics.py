# events/metrics.py
"""
Observability metrics for event sourcing.

Provides metrics collection for monitoring the health and performance
of the event sourcing system. These metrics can be exposed via
management commands or integrated with monitoring systems.

Key metrics:
- event_payload_size_bytes: Average/total payload sizes
- external_payload_ratio: % of events using external storage
- projection_lag: Events pending per projection
- replay_duration: Time for replay operations
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from decimal import Decimal
import logging
import time

from django.db.models import Count, Sum, Avg, Max, Min, F
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone

from events.models import BusinessEvent, EventPayload, EventBookmark
from accounts.models import Company


logger = logging.getLogger(__name__)


def get_event_storage_metrics(company: Optional[Company] = None) -> Dict[str, Any]:
    """
    Get metrics about event storage distribution.

    Returns breakdown of inline vs external vs chunked storage,
    payload sizes, and storage efficiency.
    """
    qs = BusinessEvent.objects.all()
    if company:
        qs = qs.filter(company=company)

    # Count by storage type
    storage_breakdown = dict(
        qs.values('payload_storage')
        .annotate(count=Count('id'))
        .values_list('payload_storage', 'count')
    )

    total_events = sum(storage_breakdown.values())
    if total_events == 0:
        return {
            'total_events': 0,
            'storage_breakdown': {},
            'external_payload_ratio': 0.0,
            'chunked_ratio': 0.0,
        }

    external_count = storage_breakdown.get('external', 0)
    chunked_count = storage_breakdown.get('chunked', 0)

    return {
        'total_events': total_events,
        'storage_breakdown': storage_breakdown,
        'external_payload_ratio': external_count / total_events if total_events else 0.0,
        'chunked_ratio': chunked_count / total_events if total_events else 0.0,
    }


def get_payload_size_metrics(company: Optional[Company] = None) -> Dict[str, Any]:
    """
    Get metrics about payload sizes.

    Returns average, total, min, max payload sizes for external payloads.
    """
    qs = EventPayload.objects.all()
    if company:
        qs = qs.filter(company=company)

    stats = qs.aggregate(
        total_bytes=Sum('size_bytes'),
        avg_bytes=Avg('size_bytes'),
        min_bytes=Min('size_bytes'),
        max_bytes=Max('size_bytes'),
        count=Count('id'),
    )

    return {
        'external_payload_count': stats['count'] or 0,
        'total_bytes': stats['total_bytes'] or 0,
        'avg_bytes': float(stats['avg_bytes'] or 0),
        'min_bytes': stats['min_bytes'] or 0,
        'max_bytes': stats['max_bytes'] or 0,
    }


def get_event_origin_metrics(company: Optional[Company] = None) -> Dict[str, Any]:
    """
    Get metrics about event origins.

    Returns breakdown by origin (human, batch, api, system).
    """
    qs = BusinessEvent.objects.all()
    if company:
        qs = qs.filter(company=company)

    origin_breakdown = dict(
        qs.values('origin')
        .annotate(count=Count('id'))
        .values_list('origin', 'count')
    )

    total = sum(origin_breakdown.values())

    return {
        'total_events': total,
        'origin_breakdown': origin_breakdown,
        'human_ratio': origin_breakdown.get('human', 0) / total if total else 0.0,
        'batch_ratio': origin_breakdown.get('batch', 0) / total if total else 0.0,
        'api_ratio': origin_breakdown.get('api', 0) / total if total else 0.0,
        'system_ratio': origin_breakdown.get('system', 0) / total if total else 0.0,
    }


def get_projection_lag_metrics() -> List[Dict[str, Any]]:
    """
    Get lag metrics for all projection consumers.

    Returns list of consumer bookmarks with their lag (pending event count).
    """
    from projections.base import projection_registry

    results = []

    for bookmark in EventBookmark.objects.select_related('company', 'last_event').all():
        # Get total events for company
        total_events = BusinessEvent.objects.filter(company=bookmark.company).count()

        # Get processed events (up to bookmark)
        if bookmark.last_event:
            processed = BusinessEvent.objects.filter(
                company=bookmark.company,
                company_sequence__lte=bookmark.last_event.company_sequence,
            ).count()
        else:
            processed = 0

        lag = total_events - processed

        results.append({
            'consumer_name': bookmark.consumer_name,
            'company_id': str(bookmark.company.public_id),
            'company_name': bookmark.company.name,
            'total_events': total_events,
            'processed_events': processed,
            'lag': lag,
            'last_processed_at': bookmark.last_processed_at,
            'is_paused': bookmark.is_paused,
            'error_count': bookmark.error_count,
        })

    return results


def get_event_throughput_metrics(
    company: Optional[Company] = None,
    hours: int = 24,
) -> Dict[str, Any]:
    """
    Get event throughput metrics over time.

    Returns events per hour for the specified time window.
    """
    cutoff = timezone.now() - timedelta(hours=hours)

    qs = BusinessEvent.objects.filter(occurred_at__gte=cutoff)
    if company:
        qs = qs.filter(company=company)

    hourly_counts = list(
        qs.annotate(hour=TruncHour('occurred_at'))
        .values('hour')
        .annotate(count=Count('id'))
        .order_by('hour')
    )

    total_events = sum(h['count'] for h in hourly_counts)
    avg_per_hour = total_events / hours if hours else 0

    return {
        'time_window_hours': hours,
        'total_events': total_events,
        'avg_events_per_hour': avg_per_hour,
        'hourly_breakdown': [
            {
                'hour': h['hour'].isoformat() if h['hour'] else None,
                'count': h['count'],
            }
            for h in hourly_counts
        ],
    }


def get_event_type_metrics(company: Optional[Company] = None) -> Dict[str, Any]:
    """
    Get metrics by event type.

    Returns count of events by type.
    """
    qs = BusinessEvent.objects.all()
    if company:
        qs = qs.filter(company=company)

    type_breakdown = dict(
        qs.values('event_type')
        .annotate(count=Count('id'))
        .order_by('-count')
        .values_list('event_type', 'count')
    )

    return {
        'total_event_types': len(type_breakdown),
        'type_breakdown': type_breakdown,
    }


def get_aggregate_metrics(company: Optional[Company] = None) -> Dict[str, Any]:
    """
    Get metrics by aggregate type.

    Returns count of events and unique aggregates by type.
    """
    qs = BusinessEvent.objects.all()
    if company:
        qs = qs.filter(company=company)

    aggregate_stats = list(
        qs.values('aggregate_type')
        .annotate(
            event_count=Count('id'),
            unique_aggregates=Count('aggregate_id', distinct=True),
        )
        .order_by('-event_count')
    )

    return {
        'aggregate_types': [
            {
                'type': stat['aggregate_type'],
                'event_count': stat['event_count'],
                'unique_aggregates': stat['unique_aggregates'],
                'avg_events_per_aggregate': (
                    stat['event_count'] / stat['unique_aggregates']
                    if stat['unique_aggregates'] else 0
                ),
            }
            for stat in aggregate_stats
        ],
    }


def measure_replay_duration(func):
    """
    Decorator to measure replay operation duration.

    Logs the duration and returns it in the result.
    """
    def wrapper(*args, **kwargs):
        start = time.time()
        try:
            result = func(*args, **kwargs)
            duration = time.time() - start

            logger.info(
                f"Replay operation {func.__name__} completed in {duration:.2f}s"
            )

            if isinstance(result, dict):
                result['replay_duration_seconds'] = duration
            return result

        except Exception as e:
            duration = time.time() - start
            logger.error(
                f"Replay operation {func.__name__} failed after {duration:.2f}s: {e}"
            )
            raise

    return wrapper


def get_full_metrics_report(company: Optional[Company] = None) -> Dict[str, Any]:
    """
    Get a comprehensive metrics report.

    Combines all metric categories into a single report.
    """
    return {
        'generated_at': timezone.now().isoformat(),
        'company_id': str(company.public_id) if company else None,
        'company_name': company.name if company else 'All Companies',
        'storage': get_event_storage_metrics(company),
        'payload_sizes': get_payload_size_metrics(company),
        'origins': get_event_origin_metrics(company),
        'projections': get_projection_lag_metrics(),
        'throughput_24h': get_event_throughput_metrics(company, hours=24),
        'event_types': get_event_type_metrics(company),
        'aggregates': get_aggregate_metrics(company),
    }
