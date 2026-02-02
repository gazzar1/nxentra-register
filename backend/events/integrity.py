# events/integrity.py
"""
Integrity verification exceptions for Ledger Survivability.

These exceptions represent hard failures that must abort replay.
When raised during replay, the system should be marked as unsafe for
operations until the integrity issue is resolved.

PRD Reference: Section 12 - Failure Scenarios & Handling
- Payload missing -> Replay hard fail
- Hash mismatch -> System integrity violation
- Partial projection failure -> Full rollback
- Replay aborted -> System marked unsafe

No silent recovery allowed.
"""

from typing import Optional, Dict, Any


class IntegrityViolationError(Exception):
    """
    Base class for all integrity violations during replay.

    All integrity violations carry:
    - event_id: The event that failed verification
    - details: Additional context for debugging

    When raised, replay MUST be aborted and the system marked unsafe.
    """

    def __init__(
        self,
        message: str,
        event_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.event_id = event_id
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a dictionary for logging/diagnostics."""
        return {
            'error_type': self.__class__.__name__,
            'message': str(self),
            'event_id': str(self.event_id) if self.event_id else None,
            'details': self.details,
        }


class PayloadMissingError(IntegrityViolationError):
    """
    External payload reference exists but payload record is missing.

    This indicates data loss or corruption in the EventPayload table.
    The event references a payload that no longer exists.

    Recovery: Restore EventPayload table from backup.
    """
    pass


class PayloadHashMismatchError(IntegrityViolationError):
    """
    Stored payload does not match its hash - data corruption detected.

    This indicates that either:
    1. The payload was modified after creation (should be impossible)
    2. Storage corruption occurred
    3. The hash was computed incorrectly

    Recovery: Restore from backup. Investigate storage system.
    """
    pass


class ChunkMissingError(IntegrityViolationError):
    """
    Chunked event is missing one or more chunk events.

    For chunked journal entries, all chunk events must be present
    to reconstruct the full payload. A missing chunk means the
    journal cannot be replayed.

    Recovery: Restore missing events from backup.
    """
    pass


class SequenceGapError(IntegrityViolationError):
    """
    Gap detected in company_sequence - events may be missing.

    The company_sequence should be monotonically increasing without gaps.
    A gap indicates that events were either:
    1. Not properly written
    2. Deleted (should be impossible)
    3. Lost due to storage failure

    Recovery: Investigate the gap and restore missing events.
    """
    pass


class ReplayAbortedError(IntegrityViolationError):
    """
    Replay was aborted due to an integrity violation.

    This is raised when a replay operation encounters any integrity
    violation and must stop. The system should be marked unsafe.

    Contains the original error that caused the abort.
    """

    def __init__(
        self,
        message: str,
        cause: Optional[IntegrityViolationError] = None,
        event_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, event_id, details)
        self.cause = cause

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        if self.cause:
            result['cause'] = self.cause.to_dict()
        return result
