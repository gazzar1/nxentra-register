# tests/test_projection_pass_reentrancy.py
"""
Unit suite for the non-re-entrancy guard in ``BaseProjection.process_pending``.

Invariant: a projection never observes its own in-flight pass. A nested
``process_pending`` for the same (projection, company) — reached through a
command's synchronous ``_process_projections`` drain, the emitter's
synchronous fallback loop, or a handler calling it directly — is a no-op
that returns 0; the outer pass owns stream order. Other projections and
other companies still drain synchronously from inside a handler.

Probe idiom follows tests/test_a5_pr1b_terminalskip_atomicity.py: a minimal
BaseProjection subclass driven by an event ``mode`` field.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from events.models import BusinessEvent, CompanyEventCounter, EventBookmark
from projections.base import BaseProjection, DeferEvent, projection_in_flight, projection_registry
from projections.exceptions import ProjectionTerminalSkip
from projections.models import ProjectionAppliedEvent, ProjectionFailureLog

pytestmark = pytest.mark.django_db

EVENT_TYPE = "test.reentrancy_event"
PEER_EVENT_TYPE = "test.reentrancy_peer_event"


class _ReentryProbe(BaseProjection):
    """Mode-driven probe. ``reenter`` calls its own process_pending from the
    handler; ``drain`` runs the accounting drain (every registered projection);
    ``emitter_loop`` runs the emitter's synchronous fallback loop; ``peer``
    calls ``self.peer`` = (projection, company); ``boom`` / ``skip`` / ``defer``
    raise the three failure kinds; ``ok`` just records the event."""

    def __init__(self, name: str = "test_reentrancy_probe", event_type: str = EVENT_TYPE) -> None:
        self._name = name
        self._event_type = event_type
        self.handled: list[int] = []
        self.nested_returns: list[int] = []
        self.max_depth = 0
        self._depth = 0
        self.peer: tuple[BaseProjection, object] | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def consumes(self) -> list[str]:
        return [self._event_type]

    def handle(self, event: BusinessEvent) -> None:
        mode = (event.data or {}).get("mode", "ok")
        self._depth += 1
        self.max_depth = max(self.max_depth, self._depth)
        try:
            if mode == "reenter":
                self.nested_returns.append(self.process_pending(event.company))
            elif mode == "drain":
                from accounting.commands import _process_projections

                _process_projections(event.company)
            elif mode == "emitter_loop":
                for projection in projection_registry.all():
                    projection.process_pending(event.company, limit=100)
            elif mode == "peer":
                assert self.peer is not None
                target, target_company = self.peer
                self.nested_returns.append(target.process_pending(target_company))
            elif mode == "boom":
                raise ValueError("generic failure")
            elif mode == "skip":
                raise ProjectionTerminalSkip("terminal", fix_hint="operator")
            elif mode == "defer":
                raise DeferEvent("precondition pending")
            self.handled.append(event.company_sequence)
        finally:
            self._depth -= 1


def _make_event(company, *, mode: str = "ok", event_type: str = EVENT_TYPE) -> BusinessEvent:
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
        data={"mode": mode},
    )


def _markers(probe: BaseProjection, company) -> int:
    return ProjectionAppliedEvent.objects.filter(company=company, projection_name=probe.name).count()


def _bookmark(probe: BaseProjection, company) -> EventBookmark:
    return EventBookmark.objects.get(consumer_name=probe.name, company=company)


@pytest.fixture
def registered():
    """Register probes in the live registry for the duration of one test (the
    accounting drain and the emitter loop iterate the registry)."""
    names: list[str] = []

    def _register(probe: BaseProjection) -> BaseProjection:
        projection_registry.register(probe, allow_override=True)
        names.append(probe.name)
        return probe

    yield _register
    # ProjectionRegistry has no unregister; test-scoped removal of the probes so
    # registry-walking tests never see them (same idiom as test_a5_pr1a_visibility).
    for name in names:
        projection_registry._projections.pop(name, None)


# ---------------------------------------------------------------------------


def test_nested_pass_for_the_same_projection_is_a_no_op(company):
    probe = _ReentryProbe()
    events = [_make_event(company, mode="reenter") for _ in range(3)]

    processed = probe.process_pending(company)

    assert processed == 3
    assert probe.nested_returns == [0, 0, 0], "a re-entered pass must return 0 without touching the stream"
    assert probe.handled == [e.company_sequence for e in events], "the outer pass owns stream order"
    assert probe.max_depth == 1
    assert _markers(probe, company) == 3
    assert _bookmark(probe, company).last_event_id == events[-1].id
    assert projection_in_flight(probe.name, company.id) is False


def test_guard_is_keyed_per_company(company, second_company):
    probe = _ReentryProbe()
    probe.peer = (probe, second_company)
    _make_event(company, mode="peer")
    other = [_make_event(second_company, mode="ok") for _ in range(2)]

    assert probe.process_pending(company) == 1
    assert probe.nested_returns == [2], "the same projection for ANOTHER company still drains from inside a handler"
    assert _bookmark(probe, second_company).last_event_id == other[-1].id


def test_guard_is_keyed_per_projection(company):
    probe = _ReentryProbe()
    peer = _ReentryProbe(name="test_reentrancy_peer", event_type=PEER_EVENT_TYPE)
    probe.peer = (peer, company)
    _make_event(company, mode="peer")
    peer_events = [_make_event(company, mode="ok", event_type=PEER_EVENT_TYPE) for _ in range(2)]

    assert probe.process_pending(company) == 1
    assert probe.nested_returns == [2], "ANOTHER projection for the same company still drains from inside a handler"
    assert peer.handled == [e.company_sequence for e in peer_events]


@pytest.mark.parametrize("mode", ["boom", "skip", "defer"])
def test_in_flight_flag_clears_after_each_failure_kind(company, mode):
    probe = _ReentryProbe()
    failing = _make_event(company, mode=mode)

    probe.process_pending(company)  # boom: caught (stop_on_error); skip: consumed; defer: rewound

    assert projection_in_flight(probe.name, company.id) is False
    # Heal the failing event (boom/defer are retried head-of-line by design) and
    # add a follow-up: the next top-level pass must run — a leaked in-flight
    # flag would turn it into a silent no-op returning 0.
    BusinessEvent.objects.filter(pk=failing.pk).update(data={"mode": "ok"})
    _make_event(company, mode="ok")
    assert probe.process_pending(company) >= 1
    assert probe.handled, "the follow-up event was handled"


def test_in_flight_flag_clears_when_terminal_evidence_write_fails(company, monkeypatch):
    """A5-PR1b: a fault while persisting terminal evidence propagates. The
    guard must still be released on that path."""
    probe = _ReentryProbe()
    _make_event(company, mode="skip")

    def _boom(event, error):
        raise RuntimeError("evidence write fault")

    monkeypatch.setattr(probe, "_persist_failure_log", _boom)
    with pytest.raises(RuntimeError, match="evidence write fault"):
        probe.process_pending(company)

    assert projection_in_flight(probe.name, company.id) is False
    monkeypatch.undo()
    assert probe.process_pending(company) == 1  # the skip is consumed normally now
    assert ProjectionFailureLog.objects.filter(company=company, projection_name=probe.name).count() == 1


def test_rebuild_is_unaffected(company):
    """rebuild() loops process_pending sequentially (never nested)."""
    probe = _ReentryProbe()
    events = [_make_event(company, mode="ok") for _ in range(3)]

    assert probe.rebuild(company) == 3
    assert probe.handled == [e.company_sequence for e in events]
    assert projection_in_flight(probe.name, company.id) is False


def test_command_drain_from_inside_a_handler_never_nests_the_same_projection(company, registered):
    """The production shape: a handler calls a command whose synchronous
    ``_process_projections`` drains every projection. Before the guard this
    re-entered the projection once per pending event (depth == pending count,
    LIFO completion, RecursionError once a first sync had a few hundred
    pending orders). Now depth stays 1 and completion order is FIFO."""
    probe = registered(_ReentryProbe())
    events = [_make_event(company, mode="drain") for _ in range(20)]

    processed = probe.process_pending(company)

    assert processed == 20
    assert probe.max_depth == 1
    assert probe.handled == [e.company_sequence for e in events]
    assert _markers(probe, company) == 20
    assert _bookmark(probe, company).last_event_id == events[-1].id


def test_emitter_fallback_loop_inside_a_handler_skips_only_itself(company, registered):
    probe = registered(_ReentryProbe())
    peer = registered(_ReentryProbe(name="test_reentrancy_peer", event_type=PEER_EVENT_TYPE))
    events = [_make_event(company, mode="emitter_loop") for _ in range(3)]
    peer_events = [_make_event(company, mode="ok", event_type=PEER_EVENT_TYPE) for _ in range(2)]

    processed = probe.process_pending(company)

    assert processed == 3
    assert probe.max_depth == 1
    assert probe.handled == [e.company_sequence for e in events]
    assert peer.handled == [e.company_sequence for e in peer_events], "other projections still drain"
