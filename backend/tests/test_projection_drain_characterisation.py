# tests/test_projection_drain_characterisation.py
"""
Characterisation of every production projection-DRAIN shape (M1, Phase 1).

A "drain" is a loop that walks ``projection_registry.all()`` (or selects one
projection) and calls ``process_pending``. Before the drains are consolidated
into ``projections.runtime`` these tests pin what each shape does TODAY, so the
extraction can be proven behaviour-preserving: same selection and order, same
``limit``, same ``exclude`` semantics, same gate, same error propagation, no
transaction or on_commit of its own. They must pass with the same assertions
before AND after the move; the copy list shrinks only when a copy is deleted
(the zero-caller scratchpad copy went with the extraction).

Shapes (names from the M1 inventory):
  V1  command-layer synchronous drain — six private copies of the same
      gate → walk → ``process_pending(company, limit=1000)`` body (accounting,
      accounts, edim, properties.commands, properties.tasks, scratchpad; four
      of them with an ``exclude`` filter no caller passes); gated on
      settings.PROJECTIONS_SYNC at CALL time; whole registry in registration
      order; first escaping exception propagates and aborts the walk;
      returns None.
  V2  events.emitter post-commit fallback — ungated; only when Celery
      ``.delay`` raises; ``limit=100``; the whole loop is swallowed at WARNING.
  V3  projections.tasks.process_company_projections — include-list by name in
      caller order (unknown dropped, ``[]`` == all); per-projection
      log-and-continue with a result dict.
  V5b reconciliation single-projection drain — fresh instance, default limit,
      UNGATED (runs even with PROJECTIONS_SYNC=False), in-transaction.
  V6  the two Shopify seed commands — inverse gate; ``limit=10000``.

Spy idiom: ``BaseProjection.process_pending`` is monkeypatched on the BASE
class (no subclass gains an override, so the no-override architecture rule
stays green); the spy records ``(name, args, kwargs, in_atomic_block,
savepoints, on_commit_hooks, sync_flag)`` and returns 0 unless told to raise.
"""

from __future__ import annotations

import importlib
import logging
from uuid import uuid4

import pytest
from django.conf import settings as django_settings
from django.db import connection, transaction

from events.models import BusinessEvent, CompanyEventCounter, EventBookmark
from projections.base import BaseProjection, projection_registry

pytestmark = pytest.mark.django_db

PROBE_EVENT = "test.drain_probe_event"

# The five live command-layer names (the sixth copy, scratchpad.commands, had
# no caller and was deleted with the extraction). After M1 every name is an
# alias of projections.runtime.command_drain; the tests below must not care.
V1_COPIES = [
    "accounting.commands._process_projections",
    "accounts.commands._process_projections",
    "edim.commands._process_projections",
    "properties.commands._process_projections",
    "properties.tasks._process_projections",
]
V1_COPIES_WITH_EXCLUDE = [
    "accounting.commands._process_projections",
    "edim.commands._process_projections",
    "properties.commands._process_projections",
]


def _resolve(dotted: str):
    module, _, attr = dotted.rpartition(".")
    return getattr(importlib.import_module(module), attr)


class _Probe(BaseProjection):
    """Minimal projection: ``ok`` records the event, ``boom`` raises."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.handled: list[int] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def consumes(self) -> list[str]:
        return [PROBE_EVENT]

    def handle(self, event: BusinessEvent) -> None:
        if (event.data or {}).get("mode") == "boom":
            raise ValueError("handler failure")
        self.handled.append(event.company_sequence)


def _make_event(company, *, mode: str = "ok") -> BusinessEvent:
    counter, _ = CompanyEventCounter.objects.get_or_create(company=company)
    counter.last_sequence += 1
    counter.save()
    return BusinessEvent.objects.create(
        company=company,
        event_type=PROBE_EVENT,
        aggregate_type="DrainProbe",
        aggregate_id=str(uuid4()),
        company_sequence=counter.last_sequence,
        idempotency_key=f"{PROBE_EVENT}:{uuid4()}",
        data={"mode": mode},
    )


@pytest.fixture
def registered():
    names: list[str] = []

    def _register(probe: BaseProjection) -> BaseProjection:
        projection_registry.register(probe, allow_override=True)
        names.append(probe.name)
        return probe

    yield _register
    for name in names:
        projection_registry._projections.pop(name, None)


@pytest.fixture
def probes(registered):
    return registered(_Probe("zz_probe_a")), registered(_Probe("zz_probe_b"))


@pytest.fixture
def spy(monkeypatch):
    """Record every process_pending dispatch; optionally raise for one name."""
    calls: list[tuple] = []
    raise_for: dict[str, Exception] = {}

    def _spy(self, *args, **kwargs):
        calls.append(
            (
                self.name,
                args,
                kwargs,
                connection.in_atomic_block,
                len(connection.savepoint_ids),
                len(connection.run_on_commit),
                django_settings.PROJECTIONS_SYNC,
            )
        )
        if self.name in raise_for:
            raise raise_for[self.name]
        return 0

    monkeypatch.setattr(BaseProjection, "process_pending", _spy)
    _spy.calls = calls  # type: ignore[attr-defined]
    _spy.raise_for = raise_for  # type: ignore[attr-defined]
    return _spy


@pytest.fixture(autouse=True)
def _capture_non_propagating_loggers(caplog):
    """ops.logging_config sets propagate=False on ``events`` and
    ``projections``; attach caplog's handler so their records are visible."""
    targets = [logging.getLogger("events.emitter"), logging.getLogger("projections.tasks")]
    for lg in targets:
        lg.addHandler(caplog.handler)
    yield
    for lg in targets:
        lg.removeHandler(caplog.handler)


def _registry_order() -> list[str]:
    return [p.name for p in projection_registry.all()]


# --------------------------------------------------------------------------- V1


@pytest.mark.parametrize("dotted", V1_COPIES)
def test_v1_walks_the_whole_registry_in_registration_order_with_limit_1000(company, probes, spy, dotted):
    fn = _resolve(dotted)

    result = fn(company)

    assert result is None
    assert [c[0] for c in spy.calls] == _registry_order()
    assert all(c[1] == (company,) and c[1][0] is company for c in spy.calls), (
        "company is passed positionally, by identity"
    )
    assert all(c[2] == {"limit": 1000} for c in spy.calls)


@pytest.mark.parametrize("dotted", V1_COPIES_WITH_EXCLUDE)
def test_v1_exclude_skips_by_exact_name_and_ignores_unknown_names(company, probes, spy, dotted):
    fn = _resolve(dotted)
    full = _registry_order()

    fn(company, exclude={"zz_probe_b"})
    assert [c[0] for c in spy.calls] == [n for n in full if n != "zz_probe_b"]

    for empty in (None, set(), frozenset()):
        spy.calls.clear()
        fn(company, exclude=empty)
        assert [c[0] for c in spy.calls] == full

    spy.calls.clear()
    fn(company, exclude={"does_not_exist"})
    assert [c[0] for c in spy.calls] == full


@pytest.mark.parametrize("dotted", V1_COPIES)
def test_v1_reads_the_sync_flag_at_call_time(company, probes, spy, settings, dotted):
    fn = _resolve(dotted)

    settings.PROJECTIONS_SYNC = False
    fn(company)
    assert spy.calls == [], "gated off: nothing dispatched"

    settings.PROJECTIONS_SYNC = True
    fn(company)
    assert [c[0] for c in spy.calls] == _registry_order()


@pytest.mark.parametrize("dotted", V1_COPIES)
def test_v1_first_escaping_exception_propagates_and_aborts_the_walk(company, probes, spy, dotted):
    fn = _resolve(dotted)
    order = _registry_order()
    third = order[2]
    spy.raise_for[third] = RuntimeError("framework fault")

    with pytest.raises(RuntimeError, match="framework fault"):
        fn(company)

    assert [c[0] for c in spy.calls] == order[:3], "later projections are not visited"


def test_v1_handler_errors_stay_fail_soft_inside_process_pending(company, probes):
    """No spy: a HANDLER failure is caught by process_pending (mark_error,
    stop) and never escapes the drain; the peer projection still runs."""
    probe_a, probe_b = probes
    _make_event(company, mode="boom")

    _resolve("accounting.commands._process_projections")(company)  # must not raise

    bookmark_a = EventBookmark.objects.get(consumer_name=probe_a.name, company=company)
    assert bookmark_a.error_count >= 1 and bookmark_a.last_error
    assert probe_a.handled == []
    # the peer saw the same boom event and also stopped fail-soft — and the
    # drain still returned normally after both
    assert probe_b.handled == []
    bookmark_b = EventBookmark.objects.get(consumer_name=probe_b.name, company=company)
    assert bookmark_b.error_count >= 1


def test_v1_opens_no_transaction_savepoint_or_on_commit_of_its_own(
    company, probes, spy, django_capture_on_commit_callbacks
):
    fn = _resolve("accounting.commands._process_projections")

    with django_capture_on_commit_callbacks() as callbacks:
        with transaction.atomic():
            savepoints_before = len(connection.savepoint_ids)
            hooks_before = len(connection.run_on_commit)
            fn(company)
    assert spy.calls, "drain ran"
    assert all(c[3] is True for c in spy.calls), "dispatch happens inside the caller's atomic"
    assert all(c[4] == savepoints_before for c in spy.calls), "no wrapper savepoint"
    assert all(c[5] == hooks_before for c in spy.calls), "no on_commit registered by the drain"
    assert callbacks == []


def test_v1_accounts_copy_drains_an_arbitrary_company_and_never_swallows_none(
    company, second_company, probes, spy, monkeypatch
):
    fn = _resolve("accounts.commands._process_projections")

    fn(second_company)
    assert spy.calls and all(c[1][0] is second_company for c in spy.calls)

    monkeypatch.undo()  # real process_pending: None has no .id
    with pytest.raises(AttributeError):
        fn(None)


def test_registry_enumeration_is_live_and_insertion_ordered(company, spy, registered):
    fn = _resolve("accounting.commands._process_projections")
    fn(company)
    first = [c[0] for c in spy.calls]
    assert "zz_probe_a" not in first

    registered(_Probe("zz_probe_a"))
    spy.calls.clear()
    fn(company)
    second = [c[0] for c in spy.calls]
    assert second == first + ["zz_probe_a"], "the registry is walked live on every drain, in insertion order"

    registered(_Probe("zz_probe_a"))  # allow_override keeps the original position
    spy.calls.clear()
    fn(company)
    assert [c[0] for c in spy.calls] == second


# --------------------------------------------------------------------------- V2


def _fail_delay(monkeypatch):
    from projections import tasks as projection_tasks

    def _boom(*args, **kwargs):
        raise RuntimeError("no broker")

    monkeypatch.setattr(projection_tasks.process_company_projections, "delay", _boom)


def test_v2_emitter_fallback_walks_the_registry_with_limit_100_ungated(
    company, probes, spy, settings, monkeypatch, django_capture_on_commit_callbacks
):
    from events.emitter import _schedule_projection_processing

    _fail_delay(monkeypatch)
    settings.PROJECTIONS_SYNC = False  # the fallback is NOT gated on the flag

    with django_capture_on_commit_callbacks(execute=True):
        _schedule_projection_processing(company.id)

    assert [c[0] for c in spy.calls] == _registry_order()
    for _, args, kwargs, *_rest in spy.calls:
        passed = kwargs.get("company", args[0] if args else None)
        assert passed is not None and passed.id == company.id
        assert kwargs["limit"] == 100


def test_v2_emitter_fallback_swallows_the_whole_loop_at_warning(
    company, probes, spy, monkeypatch, caplog, django_capture_on_commit_callbacks
):
    from events.emitter import _schedule_projection_processing

    _fail_delay(monkeypatch)
    order = _registry_order()
    spy.raise_for[order[2]] = RuntimeError("framework fault")

    with caplog.at_level(logging.WARNING, logger="events.emitter"):
        with django_capture_on_commit_callbacks(execute=True):
            _schedule_projection_processing(company.id)  # must not raise

    assert [c[0] for c in spy.calls] == order[:3]
    warnings = [r for r in caplog.records if r.name == "events.emitter" and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].getMessage().startswith("Failed to process projections synchronously for company")


def test_v2_emitter_fallback_swallows_a_missing_company(
    probes, spy, monkeypatch, caplog, django_capture_on_commit_callbacks
):
    from events.emitter import _schedule_projection_processing

    _fail_delay(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="events.emitter"):
        with django_capture_on_commit_callbacks(execute=True):
            _schedule_projection_processing(999_999_999)

    assert spy.calls == []
    assert any(r.name == "events.emitter" and r.levelno == logging.WARNING for r in caplog.records)


def test_v2_celery_first_when_delay_succeeds(company, probes, spy, monkeypatch, django_capture_on_commit_callbacks):
    from events.emitter import _schedule_projection_processing
    from projections import tasks as projection_tasks

    seen: list[dict] = []
    monkeypatch.setattr(projection_tasks.process_company_projections, "delay", lambda **kw: seen.append(kw))

    with django_capture_on_commit_callbacks(execute=True):
        _schedule_projection_processing(company.id)

    assert seen == [{"company_id": company.id}]
    assert spy.calls == [], "the synchronous loop is only the fallback"


# --------------------------------------------------------------------------- V3


def test_v3_task_include_list_is_caller_ordered_unknown_dropped_and_errors_continue(company, probes, spy, caplog):
    from projections.tasks import process_company_projections

    result = process_company_projections.run(
        company_id=company.id, projection_names=["zz_probe_b", "unknown", "zz_probe_a"]
    )
    assert [c[0] for c in spy.calls] == ["zz_probe_b", "zz_probe_a"]
    assert all(c[2] == {"limit": 1000} for c in spy.calls)
    assert list(result["projections"]) == ["zz_probe_b", "zz_probe_a"], "result keys follow walk order"

    spy.calls.clear()
    process_company_projections.run(company_id=company.id, projection_names=[], limit=7)
    assert [c[0] for c in spy.calls] == _registry_order(), "[] means the whole registry"
    assert all(c[2] == {"limit": 7} for c in spy.calls)

    spy.calls.clear()
    spy.raise_for["zz_probe_a"] = RuntimeError("framework fault")
    with caplog.at_level(logging.ERROR, logger="projections.tasks"):
        result = process_company_projections.run(company_id=company.id, projection_names=["zz_probe_a", "zz_probe_b"])
    assert [c[0] for c in spy.calls] == ["zz_probe_a", "zz_probe_b"], "later projections still visited"
    assert result["projections"]["zz_probe_a"] == {"error": "framework fault", "status": "error"}
    assert result["projections"]["zz_probe_b"]["status"] == "success"
    errors = [r for r in caplog.records if r.name == "projections.tasks" and r.levelno == logging.ERROR]
    assert len(errors) == 1 and errors[0].getMessage().startswith("Error in projection zz_probe_a")

    assert process_company_projections.run(company_id=999_999_999) == {"error": "Company 999999999 not found"}


# --------------------------------------------------------------------------- V5b


def test_v5b_reconciliation_sync_drain_is_a_single_ungated_default_limit_pass(company, probes, spy, settings):
    from reconciliation.commands import _run_reconciliation_projection_sync
    from reconciliation.projections import PROJECTION_NAME

    settings.PROJECTIONS_SYNC = False  # UNGATED by design — nobody may "harmonise" this with V1
    _run_reconciliation_projection_sync(company)

    assert len(spy.calls) == 1
    name, args, kwargs, *_ = spy.calls[0]
    assert name == PROJECTION_NAME
    assert args == (company,) and kwargs == {}


# --------------------------------------------------------------------------- V6


def test_v6_seed_command_drain_is_inverse_gated_with_limit_10000(company, probes, spy, settings):
    from shopify_connector.management.commands.seed_test_csv_pack import Command

    settings.PROJECTIONS_SYNC = True
    Command()._run_projections(company)
    assert spy.calls == []

    settings.PROJECTIONS_SYNC = False
    Command()._run_projections(company)
    assert [c[0] for c in spy.calls] == _registry_order()
    for _, args, kwargs, *_rest in spy.calls:
        passed = kwargs.get("company", args[0] if args else None)
        assert passed is company and kwargs["limit"] == 10000
