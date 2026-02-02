# events/payload_policy.py
"""
Payload policy engine for LEPH (Large Event Payload Handling).

This module determines the storage strategy for event payloads based on:
1. Payload size
2. Payload origin (human, batch import, API)
3. Payload structure (presence of 'lines' array for journal entries)

Storage strategies:
- inline: Small payloads stored directly in BusinessEvent.data (default)
- external: Large payloads stored in EventPayload table
- chunked: Very large journal entries split into multiple events
"""

from enum import Enum
from typing import Any, Dict, Tuple

from events.serialization import estimate_json_size


# =============================================================================
# Configuration Thresholds
# =============================================================================

# Maximum size for inline storage (64KB)
INLINE_MAX_SIZE = 64 * 1024

# Threshold for using chunked events (1MB)
CHUNK_THRESHOLD = 1024 * 1024

# Maximum lines per chunk for chunked journal entries
MAX_LINES_PER_CHUNK = 500


# =============================================================================
# Payload Origin
# =============================================================================

class PayloadOrigin(Enum):
    """
    Origin of the event payload.

    The origin affects storage strategy decisions:
    - HUMAN: Manual UI entry - small payloads, prefer inline
    - SYSTEM_BATCH: EDIM imports - potentially large, consider chunking
    - API: External API calls - vary in size, use standard thresholds
    """

    HUMAN = 'human'
    SYSTEM_BATCH = 'batch'
    API = 'api'


# =============================================================================
# Storage Strategy
# =============================================================================

class PayloadStrategy(Enum):
    """
    Storage strategy for event payloads.
    """

    INLINE = 'inline'      # Store in BusinessEvent.data
    EXTERNAL = 'external'  # Store in EventPayload table
    CHUNKED = 'chunked'    # Split into multiple events


# =============================================================================
# Policy Functions
# =============================================================================

def determine_storage_strategy(
    payload: Dict[str, Any],
    origin: PayloadOrigin = PayloadOrigin.HUMAN,
) -> Tuple[PayloadStrategy, Dict[str, Any]]:
    """
    Determine the storage strategy for a payload.

    This function analyzes the payload size and structure to determine
    the optimal storage strategy.

    Args:
        payload: The event payload dict
        origin: Origin of the payload (affects chunking decisions)

    Returns:
        Tuple of (strategy, metadata)
        - strategy: The recommended storage strategy
        - metadata: Additional information about the decision

    Examples:
        # Small payload -> inline
        strategy, meta = determine_storage_strategy({"key": "value"})
        # strategy = PayloadStrategy.INLINE
        # meta = {"size": 15, "reason": "below_threshold"}

        # Large payload from batch import with many lines -> chunked
        strategy, meta = determine_storage_strategy(
            {"lines": [line1, line2, ..., line1000]},
            origin=PayloadOrigin.SYSTEM_BATCH
        )
        # strategy = PayloadStrategy.CHUNKED
        # meta = {"line_count": 1000, "chunk_count": 2, "reason": "batch_lines"}

        # Large payload without lines -> external
        strategy, meta = determine_storage_strategy({"large": "data" * 10000})
        # strategy = PayloadStrategy.EXTERNAL
        # meta = {"size": 50000, "reason": "above_threshold"}
    """
    size = estimate_json_size(payload)

    # Small payloads: always inline
    if size <= INLINE_MAX_SIZE:
        return (
            PayloadStrategy.INLINE,
            {'size': size, 'reason': 'below_threshold'}
        )

    # Check for chunking eligibility (batch imports with journal lines)
    if origin == PayloadOrigin.SYSTEM_BATCH:
        lines = payload.get('lines', [])
        if isinstance(lines, list) and len(lines) > MAX_LINES_PER_CHUNK:
            chunk_count = (len(lines) + MAX_LINES_PER_CHUNK - 1) // MAX_LINES_PER_CHUNK
            return (
                PayloadStrategy.CHUNKED,
                {
                    'size': size,
                    'line_count': len(lines),
                    'chunk_count': chunk_count,
                    'chunk_size': MAX_LINES_PER_CHUNK,
                    'reason': 'batch_lines',
                }
            )

    # Large payloads: external storage
    return (
        PayloadStrategy.EXTERNAL,
        {'size': size, 'reason': 'above_threshold'}
    )


def should_use_chunking(
    payload: Dict[str, Any],
    origin: PayloadOrigin = PayloadOrigin.HUMAN,
) -> bool:
    """
    Quick check if a payload should use chunked storage.

    Args:
        payload: The event payload
        origin: Origin of the payload

    Returns:
        True if chunking should be used
    """
    strategy, _ = determine_storage_strategy(payload, origin)
    return strategy == PayloadStrategy.CHUNKED


def chunk_lines(lines: list, chunk_size: int = MAX_LINES_PER_CHUNK) -> list:
    """
    Split a list of lines into chunks.

    Args:
        lines: List of journal lines
        chunk_size: Maximum lines per chunk

    Returns:
        List of line chunks

    Example:
        >>> lines = list(range(1250))
        >>> chunks = chunk_lines(lines, chunk_size=500)
        >>> len(chunks)
        3
        >>> len(chunks[0]), len(chunks[1]), len(chunks[2])
        (500, 500, 250)
    """
    return [
        lines[i:i + chunk_size]
        for i in range(0, len(lines), chunk_size)
    ]


def get_storage_thresholds() -> Dict[str, int]:
    """
    Get the current storage thresholds for monitoring/debugging.

    Returns:
        Dict with threshold values
    """
    return {
        'inline_max_size': INLINE_MAX_SIZE,
        'chunk_threshold': CHUNK_THRESHOLD,
        'max_lines_per_chunk': MAX_LINES_PER_CHUNK,
    }
