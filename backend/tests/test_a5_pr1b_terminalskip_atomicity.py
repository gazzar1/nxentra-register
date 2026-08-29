# tests/test_a5_pr1b_terminalskip_atomicity.py
"""A5-PR1b: TerminalSkip trace-before-consume atomicity.

The contract under test: on ``ProjectionTerminalSkip``, the mandatory
``ProjectionFailureLog`` upsert + the ``ProjectionAppliedEvent`` marker + the
``EventBookmark`` advancement commit in ONE owning transaction — all three or
none. A transient DB fault while persisting the evidence must never let the
quarantined event be consumed traceless: ``process_pending`` propagates, the
bookmark stays put, and the event remains pending for retry (fail-closed even
under ``stop_on_error=False``).

Everything else stays as it was: generic exceptions remain best-effort/
non-consumed (``on_error`` keeps swallowing its own log-write failures),
DeferEvent remains a pure rollback + rewind, the A105 self-heal stamp remains
on the successful-retry path, and the A3 apply validator's quarantine verdict
rides the same atomic consume.

Fault injection follows the repo idiom: monkeypatch call-count-gated "flaky"
wrappers / raisers on the exact writer, exception messages branded as
test-injected, and assertions on BOTH the surfaced error and the durable
state after rollback.
"""

from uuid import uuid4

import pytest
from django.db import OperationalError

from events.models import BusinessEvent, CompanyEventCounter, EventBookmark
from projections.base import BaseProjection, DeferEvent
from projections.exceptions import ProjectionTerminalSkip
from projections.models import (
    SELF_HEALED_RESOLUTION_NOTE,
    ProjectionAppliedEvent,
    ProjectionFailureLog,
)
from projections.write_barrier import projection_writes_allowed

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Minimal probe projection + event factory
# --------------------------------------------------------------------------- #


class _AtomicityProbe(BaseProjection):
    """Mode-driven minimal projection: `skip` raises ProjectionTerminalSkip,
    `boom` raises a plain generic exception, `defer` raises DeferEvent,
    `flaky` fails generically until `succeed_now` is flipped (A105 probe)."""

    def __init__(self):
        self.handled: list[str] = []
        self.succeed_now = False

    @property
    def name(self) -> str:
        return "test_a5_pr1b_atomicity"

    @property
    def consumes(self) -> list[str]:
        return ["test.pr1b_event"]

    def handle(self, event: BusinessEvent) -> None:
        mode = (event.data or {}).get("mode")
        if mode == "skip":
            raise ProjectionTerminalSkip("terminal — quarantine me", fix_hint="operator: reopen the period")
        if mode == "boom":
            raise ValueError("plain generic failure")
        if mode == "defer":
            raise DeferEvent("precondition pending")
        if mode == "flaky" and not self.succeed_now:
            raise ValueError("flaky generic failure")
        self.handled.append(str(event.id))


def _make_event(company, *, mode: str = "ok") -> BusinessEvent:
    counter, _ = CompanyEventCounter.objects.get_or_create(company=company)
    counter.last_sequence += 1
    counter.save()
    return BusinessEvent.objects.create(
        company=company,
        event_type="test.pr1b_event",
        aggregate_type="TestAggregate",
        aggregate_id=str(uuid4()),
        company_sequence=counter.last_sequence,
        idempotency_key=f"test.pr1b_event:{uuid4()}",
        data={"mode": mode},
    )


def _bookmark(projection, company) -> EventBookmark | None:
    return EventBookmark.objects.filter(consumer_name=projection.name, company=company).first()


# --------------------------------------------------------------------------- #
# (1) Normal terminal consume: all three commit together
# --------------------------------------------------------------------------- #


def test_terminal_consume_commits_log_marker_and_bookmark_together(company):
    proj = _AtomicityProbe()
    event = _make_event(company, mode="skip")

    processed = proj.process_pending(company)

    assert processed == 1
    log = ProjectionFailureLog.objects.get(company=company, projection_name=proj.name, event=event)
    assert log.category == ProjectionFailureLog.Category.MISSING_CONFIG
    assert log.fix_hint == "operator: reopen the period"
    assert log.occurrence_count == 1
    assert log.resolved is False
    assert ProjectionAppliedEvent.objects.filter(company=company, projection_name=proj.name, event=event).exists()
    assert _bookmark(proj, company).last_event_id == event.id
    assert proj.handled == []  # the handler's effect rolled back with the per-event tx


def test_terminal_skip_does_not_stall_the_stream(company):
    """A later event still processes in the same pass and the quarantine
    stays visible — the original terminal semantic is preserved."""
    proj = _AtomicityProbe()
    skip_event = _make_event(company, mode="skip")
    ok_event = _make_event(company, mode="ok")

    processed = proj.process_pending(company)

    assert processed == 2
    assert proj.handled == [str(ok_event.id)]
    assert ProjectionFailureLog.objects.filter(company=company, event=skip_event, resolved=False).exists()
    assert _bookmark(proj, company).last_event_id == ok_event.id


def test_second_pass_short_circuits_without_reupserting(company):
    """Consumed means consumed: a second pass neither re-runs the writer
    (occurrence stays 1) nor flips the row (quarantine rows never self-heal)."""
    proj = _AtomicityProbe()
    event = _make_event(company, mode="skip")

    assert proj.process_pending(company) == 1
    assert proj.process_pending(company) == 0

    log = ProjectionFailureLog.objects.get(company=company, projection_name=proj.name, event=event)
    assert log.occurrence_count == 1
    assert log.resolved is False


def test_seeded_bare_marker_short_circuits_without_writer(company):
    """A pre-existing applied marker (the legacy consumed shape) short-circuits
    on the idempotency stamp — the terminal writer must not run there."""
    proj = _AtomicityProbe()
    event = _make_event(company, mode="skip")
    with projection_writes_allowed():
        ProjectionAppliedEvent.objects.create(company=company, projection_name=proj.name, event=event)

    processed = proj.process_pending(company)

    assert processed == 1  # short-circuit advances past the already-consumed event
    assert not ProjectionFailureLog.objects.filter(company=company, event=event).exists()
    assert _bookmark(proj, company).last_event_id == event.id


# --------------------------------------------------------------------------- #
# (3) Fault during the mandatory failure-log write: nothing survives
# --------------------------------------------------------------------------- #


def test_log_write_failure_aborts_the_consume_then_retry_succeeds(company, monkeypatch):
    proj = _AtomicityProbe()
    event = _make_event(company, mode="skip")

    def boom(*args, **kwargs):
        raise OperationalError("boom (test-injected failure-log write failure)")

    monkeypatch.setattr(ProjectionFailureLog.objects, "get_or_create", boom)

    with pytest.raises(OperationalError, match="test-injected failure-log"):
        proj.process_pending(company)

    assert not ProjectionFailureLog.objects.filter(company=company, event=event).exists()
    assert not ProjectionAppliedEvent.objects.filter(company=company, event=event).exists()
    bookmark = _bookmark(proj, company)
    assert bookmark.last_event_id is None
    assert event in list(bookmark.get_unprocessed_events(event_types=proj.consumes, limit=10))

    # Fault clears -> the retry consumes exactly once, with full evidence.
    monkeypatch.undo()
    assert proj.process_pending(company) == 1
    log = ProjectionFailureLog.objects.get(company=company, projection_name=proj.name, event=event)
    assert log.occurrence_count == 1
    assert ProjectionAppliedEvent.objects.filter(company=company, projection_name=proj.name, event=event).exists()
    assert _bookmark(proj, company).last_event_id == event.id


# --------------------------------------------------------------------------- #
# (4) Fault during the marker write: the already-written log rolls back too
# --------------------------------------------------------------------------- #


def test_marker_write_failure_rolls_back_the_log(company, monkeypatch):
    proj = _AtomicityProbe()
    event = _make_event(company, mode="skip")

    real = ProjectionAppliedEvent.objects.get_or_create
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        # Call 1 is the per-event idempotency stamp (rolled back with the
        # handler raise); call 2 is the marker write inside the owning
        # consume transaction — by then the failure log has been written.
        calls["n"] += 1
        if calls["n"] == 2:
            assert ProjectionFailureLog.objects.filter(company=company, event=event).exists(), (
                "injection ordering broken: the failure log must already be written when the marker write runs"
            )
            raise OperationalError("boom (test-injected marker write failure)")
        return real(*args, **kwargs)

    monkeypatch.setattr(ProjectionAppliedEvent.objects, "get_or_create", flaky)

    with pytest.raises(OperationalError, match="test-injected marker"):
        proj.process_pending(company)

    assert calls["n"] == 2
    assert not ProjectionFailureLog.objects.filter(company=company, event=event).exists()
    assert not ProjectionAppliedEvent.objects.filter(company=company, event=event).exists()
    assert _bookmark(proj, company).last_event_id is None


# --------------------------------------------------------------------------- #
# (5) Fault during the bookmark advance: log + marker roll back too
# --------------------------------------------------------------------------- #


def test_bookmark_failure_rolls_back_log_and_marker_then_retry_advances_once(company, monkeypatch):
    proj = _AtomicityProbe()
    event = _make_event(company, mode="skip")

    def boom(self, evt):
        assert ProjectionFailureLog.objects.filter(company=company, event=evt).exists()
        assert ProjectionAppliedEvent.objects.filter(company=company, event=evt).exists()
        raise OperationalError("boom (test-injected bookmark write failure)")

    monkeypatch.setattr(EventBookmark, "mark_processed", boom)

    with pytest.raises(OperationalError, match="test-injected bookmark"):
        proj.process_pending(company)

    assert not ProjectionFailureLog.objects.filter(company=company, event=event).exists()
    assert not ProjectionAppliedEvent.objects.filter(company=company, event=event).exists()
    assert _bookmark(proj, company).last_event_id is None

    monkeypatch.undo()
    assert proj.process_pending(company) == 1
    assert ProjectionFailureLog.objects.filter(company=company, event=event).count() == 1
    assert ProjectionAppliedEvent.objects.filter(company=company, event=event).count() == 1
    assert _bookmark(proj, company).last_event_id == event.id


# --------------------------------------------------------------------------- #
# (8) stop_on_error=False does not soften the evidence contract
# --------------------------------------------------------------------------- #


def test_stop_on_error_false_still_propagates_evidence_failure(company, monkeypatch):
    proj = _AtomicityProbe()
    _make_event(company, mode="skip")

    def boom(*args, **kwargs):
        raise OperationalError("boom (test-injected failure-log write failure)")

    monkeypatch.setattr(ProjectionFailureLog.objects, "get_or_create", boom)

    with pytest.raises(OperationalError, match="test-injected failure-log"):
        proj.process_pending(company, stop_on_error=False)


# --------------------------------------------------------------------------- #
# PR #133 Codex P1: a propagating evidence fault must not abandon a deferred
# event whose bookmark position a later success already advanced past
# --------------------------------------------------------------------------- #


def test_evidence_fault_still_rewinds_for_deferred_events(company, monkeypatch):
    """Defer A, succeed B (bookmark at B), terminal C whose evidence write
    faults: the exception must still exit process_pending, but the A41 rewind
    must run first — otherwise the bookmark stays at B and deferred event A is
    permanently excluded from get_unprocessed_events (silent abandonment)."""
    proj = _AtomicityProbe()
    a = _make_event(company, mode="defer")
    b = _make_event(company, mode="ok")
    c = _make_event(company, mode="skip")

    def boom(*args, **kwargs):
        raise OperationalError("boom (test-injected failure-log write failure)")

    monkeypatch.setattr(ProjectionFailureLog.objects, "get_or_create", boom)

    with pytest.raises(OperationalError, match="test-injected failure-log"):
        proj.process_pending(company)

    # B committed (handled + marker); the fault on C still propagated; the
    # rewind ran: bookmark sits before A (A's predecessor is None), so BOTH
    # A and C remain visible as pending. Nothing about C survived.
    assert proj.handled == [str(b.id)]
    assert ProjectionAppliedEvent.objects.filter(company=company, event=b).exists()
    assert not ProjectionFailureLog.objects.filter(company=company, event=c).exists()
    assert not ProjectionAppliedEvent.objects.filter(company=company, event=c).exists()
    bookmark = _bookmark(proj, company)
    assert bookmark.last_event_id is None
    pending = list(bookmark.get_unprocessed_events(event_types=proj.consumes, limit=10))
    assert a in pending
    assert c in pending

    # Fault clears: B short-circuits idempotently, C consumes with full
    # evidence, A (still deferring) keeps the bookmark rewound before it.
    monkeypatch.undo()
    assert proj.process_pending(company) == 2  # B short-circuit + C consume
    assert ProjectionFailureLog.objects.filter(company=company, event=c, resolved=False).count() == 1
    assert ProjectionAppliedEvent.objects.filter(company=company, event=c).exists()
    bookmark = _bookmark(proj, company)
    assert bookmark.last_event_id is None  # A deferred again -> rewound again
    assert a in list(bookmark.get_unprocessed_events(event_types=proj.consumes, limit=10))


# --------------------------------------------------------------------------- #
# (6) Seeded rows: occurrence bump and reopen semantics are exact
# --------------------------------------------------------------------------- #


def test_existing_unresolved_row_bumps_occurrence_and_refreshes(company):
    proj = _AtomicityProbe()
    event = _make_event(company, mode="skip")
    ProjectionFailureLog.objects.create(
        company=company,
        projection_name=proj.name,
        event=event,
        event_type=event.event_type,
        category=ProjectionFailureLog.Category.UNEXPECTED,
        message="stale message",
        fix_hint="",
    )

    assert proj.process_pending(company) == 1

    log = ProjectionFailureLog.objects.get(company=company, projection_name=proj.name, event=event)
    assert log.occurrence_count == 2
    assert log.category == ProjectionFailureLog.Category.MISSING_CONFIG
    assert log.message == "terminal — quarantine me"
    assert log.fix_hint == "operator: reopen the period"
    assert log.resolved is False


def test_prematurely_resolved_row_reopens_on_reencounter(company):
    proj = _AtomicityProbe()
    event = _make_event(company, mode="skip")
    seeded = ProjectionFailureLog.objects.create(
        company=company,
        projection_name=proj.name,
        event=event,
        event_type=event.event_type,
        category=ProjectionFailureLog.Category.MISSING_CONFIG,
        message="old",
        fix_hint="",
    )
    seeded.mark_resolved(user=None, note="looks fine to me")

    assert proj.process_pending(company) == 1

    log = ProjectionFailureLog.objects.get(pk=seeded.pk)
    assert log.resolved is False
    assert log.resolved_at is None
    assert log.resolved_by is None
    assert log.occurrence_count == 2
    # The reopen itself is the signal; the operator's note is left as-is.
    assert log.resolution_note == "looks fine to me"


def test_genuine_reencounter_after_marker_clear_reuses_the_same_row(company):
    """The rebuild shape: markers cleared, failure-log rows kept. The terminal
    writer must hit the EXISTING unique row (upsert), not raise on a duplicate."""
    proj = _AtomicityProbe()
    event = _make_event(company, mode="skip")
    assert proj.process_pending(company) == 1

    ProjectionAppliedEvent.objects.filter(company=company, projection_name=proj.name).delete()
    bookmark = _bookmark(proj, company)
    bookmark.last_event = None
    bookmark.save(update_fields=["last_event", "updated_at"])

    assert proj.process_pending(company) == 1

    log = ProjectionFailureLog.objects.get(company=company, projection_name=proj.name, event=event)
    assert log.occurrence_count == 2
    assert log.resolved is False


# --------------------------------------------------------------------------- #
# (7) Generic exceptions keep their best-effort, non-consumed semantics
# --------------------------------------------------------------------------- #


def test_generic_failure_is_not_consumed_and_log_failure_stays_swallowed(company, monkeypatch):
    proj = _AtomicityProbe()
    event = _make_event(company, mode="boom")

    def boom(*args, **kwargs):
        raise OperationalError("boom (test-injected failure-log write failure)")

    monkeypatch.setattr(ProjectionFailureLog.objects, "get_or_create", boom)

    # Must NOT raise: on_error remains fail-soft for non-consumed failures.
    processed = proj.process_pending(company)

    assert processed == 0
    assert not ProjectionAppliedEvent.objects.filter(company=company, event=event).exists()
    bookmark = _bookmark(proj, company)
    assert bookmark.last_event_id is None
    assert bookmark.error_count == 1
    assert "plain generic failure" in bookmark.last_error
    assert event in list(bookmark.get_unprocessed_events(event_types=proj.consumes, limit=10))


def test_generic_failure_writes_log_when_healthy(company):
    proj = _AtomicityProbe()
    event = _make_event(company, mode="boom")

    assert proj.process_pending(company) == 0

    log = ProjectionFailureLog.objects.get(company=company, projection_name=proj.name, event=event)
    assert log.category == ProjectionFailureLog.Category.UNEXPECTED
    assert not ProjectionAppliedEvent.objects.filter(company=company, event=event).exists()
    assert _bookmark(proj, company).error_count == 1


# --------------------------------------------------------------------------- #
# (9) DeferEvent regression: pure rollback + rewind, no evidence writes
# --------------------------------------------------------------------------- #


def test_defer_leaves_no_log_no_marker_and_stays_pending(company):
    proj = _AtomicityProbe()
    event = _make_event(company, mode="defer")

    assert proj.process_pending(company) == 0

    assert not ProjectionFailureLog.objects.filter(company=company, event=event).exists()
    assert not ProjectionAppliedEvent.objects.filter(company=company, event=event).exists()
    bookmark = _bookmark(proj, company)
    assert bookmark.last_event_id is None
    assert event in list(bookmark.get_unprocessed_events(event_types=proj.consumes, limit=10))


# --------------------------------------------------------------------------- #
# (10) A105 self-heal regression: successful retry resolves with the sentinel
# --------------------------------------------------------------------------- #


def test_successful_retry_still_self_heals(company):
    proj = _AtomicityProbe()
    event = _make_event(company, mode="flaky")

    assert proj.process_pending(company) == 0
    assert ProjectionFailureLog.objects.filter(company=company, event=event, resolved=False).exists()

    proj.succeed_now = True
    assert proj.process_pending(company) == 1

    log = ProjectionFailureLog.objects.get(company=company, projection_name=proj.name, event=event)
    assert log.resolved is True
    assert log.resolved_by is None
    assert log.resolution_note == SELF_HEALED_RESOLUTION_NOTE
    assert ProjectionAppliedEvent.objects.filter(company=company, event=event).exists()


# --------------------------------------------------------------------------- #
# (2) The MERGED A3 apply validator rides the same atomic consume
# --------------------------------------------------------------------------- #


def _emit_unbalanced_journal_event(company, user, cash_account, revenue_account):
    """Emit a schema-valid journal_entry.posted event, then corrupt the stored
    payload to be unbalanced — the at-rest-corruption surface the A3 apply
    boundary quarantines (see tests/test_a3_apply_boundary.py)."""
    from datetime import date

    from events.emitter import emit_event
    from events.types import EventTypes

    entry_id = uuid4()
    amount = "100.00"
    payload = {
        "entry_public_id": str(entry_id),
        "entry_number": "JE-PR1B-1",
        "date": date.today().isoformat(),
        "memo": "pr1b atomicity test",
        "kind": "NORMAL",
        "posted_at": "2026-01-01T12:00:00",
        "posted_by_id": user.id,
        "posted_by_email": user.email,
        "period": 1,
        "total_debit": amount,
        "total_credit": amount,
        "lines": [
            {
                "line_no": 1,
                "account_public_id": str(cash_account.public_id),
                "description": "cash",
                "debit": amount,
                "credit": "0.00",
            },
            {
                "line_no": 2,
                "account_public_id": str(revenue_account.public_id),
                "description": "revenue",
                "debit": "0.00",
                "credit": amount,
            },
        ],
    }
    event = emit_event(
        company=company,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=str(entry_id),
        data=payload,
        caused_by_user=user,
        idempotency_key=f"pr1b:posted:{entry_id}",
    )
    bad = dict(payload)
    bad["lines"] = [dict(payload["lines"][0]), dict(payload["lines"][1], credit="90.00")]
    BusinessEvent.objects.filter(pk=event.pk).update(data=bad)
    return BusinessEvent.objects.get(pk=event.pk)


def test_a3_validator_quarantine_commits_all_three_atomically(company, user, cash_account, revenue_account):
    from accounting.models import JournalEntry
    from projections.accounting import JournalEntryProjection

    event = _emit_unbalanced_journal_event(company, user, cash_account, revenue_account)
    projection = JournalEntryProjection()

    processed = projection.process_pending(company)

    assert processed == 1
    log = ProjectionFailureLog.objects.get(company=company, projection_name=projection.name, event=event)
    assert "JE_UNBALANCED" in log.message
    assert log.category == ProjectionFailureLog.Category.MISSING_CONFIG
    assert log.resolved is False
    assert ProjectionAppliedEvent.objects.filter(company=company, projection_name=projection.name, event=event).exists()
    bookmark = EventBookmark.objects.get(consumer_name=projection.name, company=company)
    assert bookmark.last_event_id == event.id
    assert not JournalEntry.objects.filter(company=company).exists()


def test_a3_validator_quarantine_aborts_without_evidence_then_retries(
    company, user, cash_account, revenue_account, monkeypatch
):
    from projections.accounting import JournalEntryProjection

    event = _emit_unbalanced_journal_event(company, user, cash_account, revenue_account)
    projection = JournalEntryProjection()

    def boom(*args, **kwargs):
        raise OperationalError("boom (test-injected failure-log write failure)")

    monkeypatch.setattr(ProjectionFailureLog.objects, "get_or_create", boom)

    with pytest.raises(OperationalError, match="test-injected failure-log"):
        projection.process_pending(company)

    assert not ProjectionFailureLog.objects.filter(company=company, event=event).exists()
    assert not ProjectionAppliedEvent.objects.filter(company=company, event=event).exists()
    bookmark = EventBookmark.objects.get(consumer_name=projection.name, company=company)
    assert bookmark.last_event_id is None

    monkeypatch.undo()
    assert projection.process_pending(company) == 1
    log = ProjectionFailureLog.objects.get(company=company, projection_name=projection.name, event=event)
    assert log.occurrence_count == 1
    assert ProjectionAppliedEvent.objects.filter(company=company, projection_name=projection.name, event=event).exists()
