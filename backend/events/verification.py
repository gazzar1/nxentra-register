# events/verification.py
"""
Event verification utilities for Ledger Survivability.

These functions implement hard-fail verification as required by
PRD Section 5.2: "Abort on any inconsistency".

Verification inputs:
- BusinessEvent table
- EventPayload store (if referenced)

Verification checks:
- Payload existence (for external storage)
- Payload hash integrity
- Chunk completeness (for chunked payloads)
- Sequence continuity

All verification failures raise IntegrityViolationError subclasses.
"""

from typing import Generator, Tuple, Optional, Dict, Any, List
from decimal import Decimal
import logging

from django.db.models import Count

from events.models import BusinessEvent, EventPayload
from events.serialization import compute_payload_hash
from events.integrity import (
    IntegrityViolationError,
    PayloadMissingError,
    PayloadHashMismatchError,
    ChunkMissingError,
    SequenceGapError,
)
from events.types import EventTypes


logger = logging.getLogger(__name__)


def verify_event_payload(event: BusinessEvent) -> Dict[str, Any]:
    """
    Verify the integrity of a single event's payload.

    Performs verification based on the event's storage strategy:
    - inline: Verify hash matches data field
    - external: Verify payload exists and hash matches
    - chunked: Verify all chunks exist

    Args:
        event: The BusinessEvent to verify

    Returns:
        dict with verification result:
        {
            'valid': bool,
            'payload_size': int,
            'storage_strategy': str,
        }

    Raises:
        PayloadMissingError: If external payload is missing
        PayloadHashMismatchError: If hash verification fails
        ChunkMissingError: If chunks are missing
    """
    result = {
        'valid': True,
        'payload_size': 0,
        'storage_strategy': event.payload_storage,
    }

    if event.payload_storage == 'inline':
        # Verify inline payload hash if present
        if event.payload_hash:
            computed = compute_payload_hash(event.data)
            if computed != event.payload_hash:
                raise PayloadHashMismatchError(
                    f"Inline payload hash mismatch for event {event.id}",
                    event_id=str(event.id),
                    details={
                        'expected': event.payload_hash,
                        'computed': computed,
                        'event_type': event.event_type,
                    }
                )
        result['payload_size'] = len(str(event.data))

    elif event.payload_storage == 'external':
        # Verify external payload exists
        if not event.payload_ref_id:
            raise PayloadMissingError(
                f"Event {event.id} has external storage but no payload_ref",
                event_id=str(event.id),
                details={
                    'event_type': event.event_type,
                    'aggregate_type': event.aggregate_type,
                    'aggregate_id': event.aggregate_id,
                }
            )

        try:
            payload_record = EventPayload.objects.get(id=event.payload_ref_id)
        except EventPayload.DoesNotExist:
            raise PayloadMissingError(
                f"External payload {event.payload_ref_id} not found for event {event.id}",
                event_id=str(event.id),
                details={
                    'payload_ref_id': event.payload_ref_id,
                    'event_type': event.event_type,
                }
            )

        # Verify hash
        if event.payload_hash:
            computed = compute_payload_hash(payload_record.payload)
            if computed != event.payload_hash:
                raise PayloadHashMismatchError(
                    f"External payload hash mismatch for event {event.id}",
                    event_id=str(event.id),
                    details={
                        'expected': event.payload_hash,
                        'computed': computed,
                        'payload_ref_id': event.payload_ref_id,
                    }
                )

        result['payload_size'] = payload_record.size_bytes

    elif event.payload_storage == 'chunked':
        # Verify chunk events exist
        if event.event_type == EventTypes.JOURNAL_CREATED:
            chunk_count = BusinessEvent.objects.filter(
                caused_by_event=event,
                event_type=EventTypes.JOURNAL_LINES_CHUNK_ADDED,
            ).count()

            # Check if finalized event exists to verify expected chunk count
            finalized = BusinessEvent.objects.filter(
                caused_by_event=event,
                event_type=EventTypes.JOURNAL_FINALIZED,
            ).first()

            if finalized:
                finalized_data = finalized.data if finalized.payload_storage == 'inline' else {}
                expected_chunks = finalized_data.get('chunk_count', 0)
                if chunk_count != expected_chunks:
                    raise ChunkMissingError(
                        f"Expected {expected_chunks} chunks, found {chunk_count} for event {event.id}",
                        event_id=str(event.id),
                        details={
                            'expected_chunks': expected_chunks,
                            'found_chunks': chunk_count,
                            'finalized_event_id': str(finalized.id),
                        }
                    )

    return result


def verify_sequence_continuity(
    company,
    start_sequence: int = 0,
    end_sequence: Optional[int] = None,
) -> Generator[Tuple[int, int], None, None]:
    """
    Check for gaps in company_sequence.

    The company_sequence should be monotonically increasing without gaps.
    This generator yields any detected gaps.

    Args:
        company: The company to check
        start_sequence: Start checking from this sequence (inclusive)
        end_sequence: Stop checking at this sequence (inclusive)

    Yields:
        Tuples of (gap_start, gap_end) for each detected gap
    """
    events = BusinessEvent.objects.filter(
        company=company,
        company_sequence__gt=start_sequence,
    )

    if end_sequence is not None:
        events = events.filter(company_sequence__lte=end_sequence)

    events = events.order_by('company_sequence').values_list('company_sequence', flat=True)

    expected = start_sequence + 1
    for seq in events:
        if seq != expected:
            yield (expected, seq - 1)
        expected = seq + 1


def full_integrity_check(company, verbose: bool = False) -> Dict[str, Any]:
    """
    Perform full integrity check for a company's event stream.

    This is the main entry point for integrity verification.
    It checks:
    1. Sequence continuity (no gaps)
    2. All event payloads (inline, external, chunked)

    Args:
        company: The company to check
        verbose: If True, log progress

    Returns:
        dict with check results:
        {
            'total_events': int,
            'verified_events': int,
            'payload_errors': list,
            'sequence_gaps': list,
            'external_payload_count': int,
            'chunked_event_count': int,
            'total_payload_bytes': int,
            'is_valid': bool,
        }
    """
    result = {
        'total_events': 0,
        'verified_events': 0,
        'payload_errors': [],
        'sequence_gaps': [],
        'external_payload_count': 0,
        'chunked_event_count': 0,
        'inline_event_count': 0,
        'total_payload_bytes': 0,
        'is_valid': True,
    }

    # Check sequence continuity
    if verbose:
        logger.info(f"Checking sequence continuity for {company.name}...")

    for gap_start, gap_end in verify_sequence_continuity(company):
        result['sequence_gaps'].append({
            'start': gap_start,
            'end': gap_end,
            'missing_count': gap_end - gap_start + 1,
        })
        result['is_valid'] = False

    # Verify all events
    events = BusinessEvent.objects.filter(company=company).order_by('company_sequence')
    result['total_events'] = events.count()

    if verbose:
        logger.info(f"Verifying {result['total_events']} events...")

    for event in events.iterator():
        try:
            verification = verify_event_payload(event)
            result['verified_events'] += 1
            result['total_payload_bytes'] += verification['payload_size']

            if verification['storage_strategy'] == 'external':
                result['external_payload_count'] += 1
            elif verification['storage_strategy'] == 'chunked':
                result['chunked_event_count'] += 1
            else:
                result['inline_event_count'] += 1

        except IntegrityViolationError as e:
            result['payload_errors'].append(e.to_dict())
            result['is_valid'] = False

            if verbose:
                logger.error(f"Integrity error: {e}")

    if verbose:
        if result['is_valid']:
            logger.info(f"Integrity check passed: {result['verified_events']} events verified")
        else:
            logger.error(
                f"Integrity check FAILED: {len(result['payload_errors'])} errors, "
                f"{len(result['sequence_gaps'])} gaps"
            )

    return result


def verify_event_by_id(event_id: str) -> Dict[str, Any]:
    """
    Verify a single event by its ID.

    Args:
        event_id: The UUID of the event to verify

    Returns:
        dict with verification result

    Raises:
        BusinessEvent.DoesNotExist: If event not found
        IntegrityViolationError: If verification fails
    """
    event = BusinessEvent.objects.get(id=event_id)
    return verify_event_payload(event)


def get_integrity_summary(company) -> Dict[str, Any]:
    """
    Get a quick summary of event integrity status.

    This is a lightweight check that returns statistics without
    full verification. Use for dashboards and monitoring.

    Args:
        company: The company to summarize

    Returns:
        dict with summary statistics
    """
    from django.db.models import Max

    events = BusinessEvent.objects.filter(company=company)

    # Count by storage type
    storage_counts = events.values('payload_storage').annotate(count=Count('id'))
    storage_map = {s['payload_storage']: s['count'] for s in storage_counts}

    # Count by origin
    origin_counts = events.values('origin').annotate(count=Count('id'))
    origin_map = {o['origin']: o['count'] for o in origin_counts}

    # Get max sequence
    max_seq = events.aggregate(max_seq=Max('company_sequence'))['max_seq'] or 0
    total_events = events.count()

    # Check for obvious sequence gaps
    expected_events = max_seq  # If no gaps, total should equal max sequence
    has_potential_gaps = total_events < expected_events if max_seq > 0 else False

    return {
        'total_events': total_events,
        'max_sequence': max_seq,
        'has_potential_gaps': has_potential_gaps,
        'storage_breakdown': storage_map,
        'origin_breakdown': origin_map,
        'external_payload_count': storage_map.get('external', 0),
        'chunked_event_count': storage_map.get('chunked', 0),
    }
