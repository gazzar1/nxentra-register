# events/payload_store.py
"""
External payload storage for LEPH (Large Event Payload Handling).

This module re-exports the EventPayload model from events.models.
The model is defined in models.py for proper Django model discovery.

Key features:
1. Content-addressed storage using SHA-256 hashes
2. Immutable records (cannot be modified or deleted)
3. Automatic deduplication (same content = same record)
4. Optional compression support (future)

EventPayload records are referenced by BusinessEvent.payload_ref for
events that use external storage strategy.
"""

# Re-export EventPayload from models for backward compatibility
from events.models import EventPayload

__all__ = ['EventPayload']
