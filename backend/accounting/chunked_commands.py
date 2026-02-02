# accounting/chunked_commands.py
"""
LEPH Chunked Journal Commands.

This module provides commands for emitting large journal entries as
multiple events (chunked storage strategy). This is used by EDIM batch
imports when journal entries have more than MAX_LINES_PER_CHUNK lines.

Chunked Journal Flow:
1. JOURNAL_CREATED event: Journal header (date, memo, currency)
2. JOURNAL_LINES_CHUNK_ADDED events: Batches of lines (up to 500 per chunk)
3. JOURNAL_FINALIZED event: Completion marker with totals

Projections process chunk events individually for efficiency, while
the full payload can be reconstructed via event.get_data() on the
JOURNAL_CREATED event.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid

from django.db import transaction

from events.emitter import emit_event
from events.models import BusinessEvent
from events.types import (
    EventTypes,
    JournalCreatedData,
    JournalLinesChunkData,
    JournalFinalizedData,
)
from events.payload_policy import (
    PayloadOrigin,
    MAX_LINES_PER_CHUNK,
    chunk_lines,
)


def emit_chunked_journal(
    actor,
    company,
    journal_entry,
    lines: List[Dict[str, Any]],
    *,
    origin: PayloadOrigin = PayloadOrigin.SYSTEM_BATCH,
    batch_id: Optional[str] = None,
) -> List[BusinessEvent]:
    """
    Emit a large journal entry as multiple chunked events.

    This function is used for journal entries with more than MAX_LINES_PER_CHUNK
    lines. Instead of storing all lines in a single event, it emits:
    1. One JOURNAL_CREATED event with the header information
    2. Multiple JOURNAL_LINES_CHUNK_ADDED events with batches of lines
    3. One JOURNAL_FINALIZED event with totals for verification

    Args:
        actor: The actor context (user/company)
        company: The company
        journal_entry: The JournalEntry model instance
        lines: List of journal line dicts (in JournalLineData format)
        origin: Origin of the payload (default: SYSTEM_BATCH)
        batch_id: Optional EDIM batch public_id

    Returns:
        List of all emitted BusinessEvent instances

    Example:
        events = emit_chunked_journal(
            actor=actor,
            company=company,
            journal_entry=journal_entry,
            lines=large_lines_list,
            batch_id=str(batch.public_id),
        )
    """
    events = []
    entry_id = str(journal_entry.public_id)
    company_public_id = str(company.public_id)

    # Split lines into chunks
    line_chunks = chunk_lines(lines, MAX_LINES_PER_CHUNK)
    total_chunks = len(line_chunks)

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 1: Emit JOURNAL_CREATED (header only)
    # ═══════════════════════════════════════════════════════════════════════════
    created_event = emit_event(
        actor,
        EventTypes.JOURNAL_CREATED,
        "journal_entry",
        entry_id,
        JournalCreatedData(
            journal_entry_id=entry_id,
            company_public_id=company_public_id,
            date=str(journal_entry.date),
            memo=journal_entry.memo or "",
            memo_ar=journal_entry.memo_ar or "",
            currency=journal_entry.currency or company.default_currency or "USD",
            kind=journal_entry.kind,
            origin=origin.value,
            batch_id=batch_id,
        ),
        idempotency_key=f"journal-{entry_id}-created",
        payload_origin=origin,
    )
    events.append(created_event)

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 2: Emit JOURNAL_LINES_CHUNK_ADDED for each chunk
    # ═══════════════════════════════════════════════════════════════════════════
    for idx, chunk in enumerate(line_chunks):
        chunk_event = emit_event(
            actor,
            EventTypes.JOURNAL_LINES_CHUNK_ADDED,
            "journal_entry",
            entry_id,
            JournalLinesChunkData(
                journal_entry_id=entry_id,
                company_public_id=company_public_id,
                chunk_index=idx,
                total_chunks=total_chunks,
                lines=chunk,
            ),
            idempotency_key=f"journal-{entry_id}-chunk-{idx}",
            caused_by_event=created_event,
            payload_origin=origin,
        )
        events.append(chunk_event)

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 3: Emit JOURNAL_FINALIZED (totals only)
    # ═══════════════════════════════════════════════════════════════════════════
    total_debit = sum(Decimal(line.get("debit", "0") or "0") for line in lines)
    total_credit = sum(Decimal(line.get("credit", "0") or "0") for line in lines)

    finalized_event = emit_event(
        actor,
        EventTypes.JOURNAL_FINALIZED,
        "journal_entry",
        entry_id,
        JournalFinalizedData(
            journal_entry_id=entry_id,
            company_public_id=company_public_id,
            total_debit=str(total_debit),
            total_credit=str(total_credit),
            line_count=len(lines),
            chunk_count=total_chunks,
            status=journal_entry.status,
        ),
        idempotency_key=f"journal-{entry_id}-finalized",
        caused_by_event=created_event,
        payload_origin=origin,
    )
    events.append(finalized_event)

    return events


def emit_chunked_journal_posted(
    actor,
    company,
    journal_entry,
    lines: List[Dict[str, Any]],
    *,
    entry_number: str,
    posted_at: str,
    origin: PayloadOrigin = PayloadOrigin.SYSTEM_BATCH,
    batch_id: Optional[str] = None,
) -> List[BusinessEvent]:
    """
    Emit a large journal entry posting as chunked events.

    Similar to emit_chunked_journal but for posted entries.
    The JOURNAL_FINALIZED event includes posted status and entry_number.

    Args:
        actor: The actor context
        company: The company
        journal_entry: The JournalEntry model instance
        lines: List of journal line dicts
        entry_number: The assigned entry number
        posted_at: ISO timestamp of posting
        origin: Origin of the payload
        batch_id: Optional EDIM batch public_id

    Returns:
        List of all emitted BusinessEvent instances
    """
    events = []
    entry_id = str(journal_entry.public_id)
    company_public_id = str(company.public_id)

    # Split lines into chunks
    line_chunks = chunk_lines(lines, MAX_LINES_PER_CHUNK)
    total_chunks = len(line_chunks)

    # Step 1: Emit JOURNAL_CREATED with posted context
    created_event = emit_event(
        actor,
        EventTypes.JOURNAL_CREATED,
        "journal_entry",
        entry_id,
        JournalCreatedData(
            journal_entry_id=entry_id,
            company_public_id=company_public_id,
            date=str(journal_entry.date),
            memo=journal_entry.memo or "",
            memo_ar=journal_entry.memo_ar or "",
            currency=journal_entry.currency or company.default_currency or "USD",
            kind=journal_entry.kind,
            origin=origin.value,
            batch_id=batch_id,
        ),
        idempotency_key=f"journal-{entry_id}-posted-created",
        payload_origin=origin,
    )
    events.append(created_event)

    # Step 2: Emit chunks
    for idx, chunk in enumerate(line_chunks):
        chunk_event = emit_event(
            actor,
            EventTypes.JOURNAL_LINES_CHUNK_ADDED,
            "journal_entry",
            entry_id,
            JournalLinesChunkData(
                journal_entry_id=entry_id,
                company_public_id=company_public_id,
                chunk_index=idx,
                total_chunks=total_chunks,
                lines=chunk,
            ),
            idempotency_key=f"journal-{entry_id}-posted-chunk-{idx}",
            caused_by_event=created_event,
            payload_origin=origin,
        )
        events.append(chunk_event)

    # Step 3: Emit finalization with POSTED status
    total_debit = sum(Decimal(line.get("debit", "0") or "0") for line in lines)
    total_credit = sum(Decimal(line.get("credit", "0") or "0") for line in lines)

    finalized_event = emit_event(
        actor,
        EventTypes.JOURNAL_FINALIZED,
        "journal_entry",
        entry_id,
        JournalFinalizedData(
            journal_entry_id=entry_id,
            company_public_id=company_public_id,
            total_debit=str(total_debit),
            total_credit=str(total_credit),
            line_count=len(lines),
            chunk_count=total_chunks,
            status="POSTED",
        ),
        idempotency_key=f"journal-{entry_id}-posted-finalized",
        caused_by_event=created_event,
        payload_origin=origin,
    )
    events.append(finalized_event)

    return events


def should_use_chunked_emission(lines: List[Dict[str, Any]]) -> bool:
    """
    Check if a journal entry should use chunked emission.

    Args:
        lines: List of journal line dicts

    Returns:
        True if the number of lines exceeds MAX_LINES_PER_CHUNK
    """
    return len(lines) > MAX_LINES_PER_CHUNK


def get_chunk_stats(lines: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Get statistics about how a journal would be chunked.

    Args:
        lines: List of journal line dicts

    Returns:
        Dict with line_count, chunk_count, lines_per_chunk
    """
    line_count = len(lines)
    chunk_count = (line_count + MAX_LINES_PER_CHUNK - 1) // MAX_LINES_PER_CHUNK

    return {
        "line_count": line_count,
        "chunk_count": chunk_count,
        "lines_per_chunk": MAX_LINES_PER_CHUNK,
        "should_chunk": line_count > MAX_LINES_PER_CHUNK,
    }
