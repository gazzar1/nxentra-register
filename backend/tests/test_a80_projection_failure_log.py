# tests/test_a80_projection_failure_log.py
"""
A80 — Projection failure log (2026-05-25).

Locks in the silent-failure-tolerance fix: every projection handler that
raises an exception must produce a ProjectionFailureLog entry so operators
can see WHY their projection isn't producing records, instead of having to
grep Django logs.

The A78 incident exposed this gap: the shopify_accounting projection had 5
silent early-returns (`logger.warning + return`) that swallowed real errors.
Events were marked consumed, no records created, no operator-visible signal.
Three weeks of broken Shopify projection that nobody noticed.

Post-A80: BaseProjection.on_error writes a ProjectionFailureLog entry on
every handler exception, categorized by error type:
- ProjectionStateError → MISSING_CONFIG  (operator action required)
- ProjectionInvalidDataError → INVALID_DATA (source data needs fixing)
- ProjectionCommandFailedError → DOWNSTREAM_FAILED (code change needed)
- Anything else → UNEXPECTED  (bug — file ticket)

See docs/finance_event_first_policy.md §8 for the canonical rule.
"""

from uuid import uuid4

from events.models import BusinessEvent, CompanyEventCounter
from projections.base import BaseProjection
from projections.exceptions import (
    ProjectionCommandFailedError,
    ProjectionInvalidDataError,
    ProjectionStateError,
)
from projections.models import ProjectionFailureLog


class _DummyProjection(BaseProjection):
    """Minimal projection for testing on_error in isolation."""

    @property
    def name(self) -> str:
        return "test_dummy_projection"

    @property
    def consumes(self):
        return ["test.event"]

    def handle(self, event):
        # Not invoked in these tests — we call on_error directly.
        pass


def _make_event(company, *, event_type="test.event"):
    counter, _ = CompanyEventCounter.objects.get_or_create(company=company)
    counter.last_sequence += 1
    counter.save()
    return BusinessEvent.objects.create(
        company=company,
        event_type=event_type,
        aggregate_type="TestAggregate",
        aggregate_id=str(uuid4()),
        company_sequence=counter.last_sequence,
        idempotency_key=f"{event_type}:{uuid4()}",
        data={},
    )


# =============================================================================
# Categorization: each exception type maps to its category
# =============================================================================


def test_state_error_creates_missing_config_log(db, company):
    """ProjectionStateError → category=MISSING_CONFIG, fix_hint preserved."""
    event = _make_event(company)
    proj = _DummyProjection()

    proj.on_error(
        event,
        ProjectionStateError(
            "ModuleAccountMapping missing",
            fix_hint="Run setup wizard",
        ),
    )

    log = ProjectionFailureLog.objects.get(company=company, event=event)
    assert log.category == ProjectionFailureLog.Category.MISSING_CONFIG
    assert log.message == "ModuleAccountMapping missing"
    assert log.fix_hint == "Run setup wizard"
    assert log.projection_name == "test_dummy_projection"
    assert log.event_type == "test.event"
    assert log.occurrence_count == 1
    assert log.resolved is False


def test_invalid_data_error_creates_invalid_data_log(db, company):
    """ProjectionInvalidDataError → category=INVALID_DATA."""
    event = _make_event(company)
    proj = _DummyProjection()

    proj.on_error(event, ProjectionInvalidDataError("Order has no postable lines"))

    log = ProjectionFailureLog.objects.get(company=company, event=event)
    assert log.category == ProjectionFailureLog.Category.INVALID_DATA
    assert "no postable lines" in log.message


def test_command_failed_error_creates_downstream_failed_log(db, company):
    """ProjectionCommandFailedError → category=DOWNSTREAM_FAILED."""
    event = _make_event(company)
    proj = _DummyProjection()

    proj.on_error(
        event,
        ProjectionCommandFailedError(
            "create_and_post_invoice_for_platform failed: bad profile",
            command_name="create_and_post_invoice_for_platform",
            original_error="bad profile",
        ),
    )

    log = ProjectionFailureLog.objects.get(company=company, event=event)
    assert log.category == ProjectionFailureLog.Category.DOWNSTREAM_FAILED
    assert "bad profile" in log.message


def test_unhandled_exception_creates_unexpected_log(db, company):
    """Any other exception → category=UNEXPECTED (operator should file ticket)."""
    event = _make_event(company)
    proj = _DummyProjection()

    proj.on_error(event, RuntimeError("unexpected crash"))

    log = ProjectionFailureLog.objects.get(company=company, event=event)
    assert log.category == ProjectionFailureLog.Category.UNEXPECTED
    assert log.message == "unexpected crash"


# =============================================================================
# Dedup: same event failing repeatedly bumps occurrence_count
# =============================================================================


def test_same_event_failing_twice_dedupes_and_increments_count(db, company):
    """A80 dedup contract: (company, projection, event) unique. Repeated
    failures of the same event before resolution bump occurrence_count
    instead of duplicating rows."""
    event = _make_event(company)
    proj = _DummyProjection()

    proj.on_error(event, ProjectionStateError("first failure"))
    proj.on_error(event, ProjectionStateError("second failure with new message"))

    logs = ProjectionFailureLog.objects.filter(company=company, event=event)
    assert logs.count() == 1, "Same event should dedupe to one log row"

    log = logs.first()
    assert log.occurrence_count == 2
    # Message should reflect the most recent failure
    assert log.message == "second failure with new message"


def test_resolved_failure_recurring_clears_resolved_flag(db, company):
    """If an operator manually marks a failure resolved but the underlying
    issue isn't actually fixed, the next failure re-opens it (resolved=False
    again) and increments the count. This prevents stale 'resolved' state
    from hiding ongoing failures."""
    event = _make_event(company)
    proj = _DummyProjection()

    proj.on_error(event, ProjectionStateError("first"))
    log = ProjectionFailureLog.objects.get(company=company, event=event)
    log.mark_resolved(note="thought we fixed it")
    assert log.resolved is True

    # Same event fails again
    proj.on_error(event, ProjectionStateError("nope, still broken"))
    log.refresh_from_db()
    assert log.resolved is False
    assert log.resolved_at is None
    assert log.resolved_by is None
    assert log.occurrence_count == 2


# =============================================================================
# Resilience: on_error must never crash the projection loop
# =============================================================================


def test_on_error_swallows_its_own_failures(db, company, monkeypatch):
    """If writing to ProjectionFailureLog itself fails (DB down, etc.), the
    framework must not crash the projection loop. Worst case we lose
    visibility on one failure; never block other events from processing."""
    event = _make_event(company)
    proj = _DummyProjection()

    def _explode(*args, **kwargs):
        raise RuntimeError("simulated log-write failure")

    monkeypatch.setattr(
        "projections.models.ProjectionFailureLog.objects",
        type("X", (), {"get_or_create": _explode})(),
    )

    # Must not raise.
    proj.on_error(event, ProjectionStateError("trigger"))
