# tests/e2e/test_a5_pr1b_terminalskip_rollback.py
"""A5-PR1b: REAL PostgreSQL transaction rollback for the TerminalSkip consume.

The SQLite battery proves the atomic composition under savepoints; this suite
proves it with real transactions on PostgreSQL (``transaction=True`` — no
wrapping test transaction, every commit/rollback is the database's own). The
load-bearing shape: the failure log (and marker) are VERIFIED WRITTEN inside
the owning transaction at the moment the later write faults, and VERIFIED
GONE after process_pending propagates — a mock raising before any DB write
could not prove that.
"""

from uuid import uuid4

import pytest
from django.db import OperationalError, connection

from events.models import BusinessEvent, CompanyEventCounter, EventBookmark
from projections.base import BaseProjection
from projections.exceptions import ProjectionTerminalSkip
from projections.models import ProjectionAppliedEvent, ProjectionFailureLog

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="A5-PR1b consume-atomicity rollback is only provable with real transactions on PostgreSQL",
    ),
]


class _RollbackProbe(BaseProjection):
    @property
    def name(self) -> str:
        return "test_a5_pr1b_pg_rollback"

    @property
    def consumes(self) -> list[str]:
        return ["test.pr1b_pg_event"]

    def handle(self, event: BusinessEvent) -> None:
        if (event.data or {}).get("mode") == "skip":
            raise ProjectionTerminalSkip("terminal — quarantine me", fix_hint="operator: act")


def _make_event(company, *, mode: str = "skip") -> BusinessEvent:
    counter, _ = CompanyEventCounter.objects.get_or_create(company=company)
    counter.last_sequence += 1
    counter.save()
    return BusinessEvent.objects.create(
        company=company,
        event_type="test.pr1b_pg_event",
        aggregate_type="TestAggregate",
        aggregate_id=str(uuid4()),
        company_sequence=counter.last_sequence,
        idempotency_key=f"test.pr1b_pg_event:{uuid4()}",
        data={"mode": mode},
    )


def test_terminal_consume_commits_all_three_for_real(company):
    """Control: with no fault, one real commit carries log + marker + bookmark."""
    proj = _RollbackProbe()
    event = _make_event(company)

    assert proj.process_pending(company) == 1

    log = ProjectionFailureLog.objects.get(company=company, projection_name=proj.name, event=event)
    assert log.resolved is False
    assert ProjectionAppliedEvent.objects.filter(company=company, projection_name=proj.name, event=event).exists()
    assert EventBookmark.objects.get(consumer_name=proj.name, company=company).last_event_id == event.id


def test_bookmark_fault_after_log_and_marker_rolls_back_all_three(company, monkeypatch):
    """The headline PG proof: the log AND marker are really written inside the
    owning transaction when the bookmark advance faults — and really gone,
    with the event still pending, after the rollback."""
    proj = _RollbackProbe()
    event = _make_event(company)
    seen_in_tx = {}

    def boom(self, evt):
        seen_in_tx["log"] = ProjectionFailureLog.objects.filter(company=company, event=evt).exists()
        seen_in_tx["marker"] = ProjectionAppliedEvent.objects.filter(company=company, event=evt).exists()
        raise OperationalError("boom (test-injected bookmark write failure)")

    monkeypatch.setattr(EventBookmark, "mark_processed", boom)

    with pytest.raises(OperationalError, match="test-injected bookmark"):
        proj.process_pending(company)

    assert seen_in_tx == {"log": True, "marker": True}, (
        "injection ordering broken: log + marker must be written in-transaction before the bookmark advance runs"
    )
    assert not ProjectionFailureLog.objects.filter(company=company, event=event).exists()
    assert not ProjectionAppliedEvent.objects.filter(company=company, event=event).exists()
    bookmark = EventBookmark.objects.get(consumer_name=proj.name, company=company)
    assert bookmark.last_event_id is None
    assert event in list(bookmark.get_unprocessed_events(event_types=proj.consumes, limit=10))

    # Fault clears -> the retry consumes exactly once with full evidence.
    monkeypatch.undo()
    assert proj.process_pending(company) == 1
    log = ProjectionFailureLog.objects.get(company=company, projection_name=proj.name, event=event)
    assert log.occurrence_count == 1
    assert ProjectionAppliedEvent.objects.filter(company=company, projection_name=proj.name, event=event).count() == 1
    assert EventBookmark.objects.get(consumer_name=proj.name, company=company).last_event_id == event.id


def test_marker_fault_after_log_rolls_back_the_log(company, monkeypatch):
    proj = _RollbackProbe()
    event = _make_event(company)

    real = ProjectionAppliedEvent.objects.get_or_create
    calls = {"n": 0, "log_seen": None}

    def flaky(*args, **kwargs):
        # Call 1: the per-event idempotency stamp (rolled back with the
        # handler raise). Call 2: the consume-transaction marker write —
        # by then the failure log is already written in-transaction.
        calls["n"] += 1
        if calls["n"] == 2:
            calls["log_seen"] = ProjectionFailureLog.objects.filter(company=company, event=event).exists()
            raise OperationalError("boom (test-injected marker write failure)")
        return real(*args, **kwargs)

    monkeypatch.setattr(ProjectionAppliedEvent.objects, "get_or_create", flaky)

    with pytest.raises(OperationalError, match="test-injected marker"):
        proj.process_pending(company)

    assert calls["n"] == 2
    assert calls["log_seen"] is True
    assert not ProjectionFailureLog.objects.filter(company=company, event=event).exists()
    assert not ProjectionAppliedEvent.objects.filter(company=company, event=event).exists()
    assert EventBookmark.objects.get(consumer_name=proj.name, company=company).last_event_id is None
