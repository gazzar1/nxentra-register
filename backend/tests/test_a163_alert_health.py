# tests/test_a163_alert_health.py
"""
A163 — alerting that reaches a human (2026-07-11 dual audit).

The Prometheus/Alertmanager stack was inert (placeholder Slack URL,
rules on never-emitted metrics, middleware never installed) and the
projection-health Celery task only logger.warning'd — nothing could
notify anyone. /_health/ready deliberately checks ONLY the database, so
an external pinger on it can never see a projection failure.

The fix ships GET /_health/alerts: 503 whenever a projection failure /
relevance-aware lag / paused or erroring consumer needs a human — the
condition an external uptime pinger (and `manage.py alert_check`)
watches. Runs in the WEB process (the Celery worker being dead is the
failure class this must catch). Pure reads; aggregate-only body (the
/_health/ prefix is auth-exempt).
"""

from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings

from events.models import BusinessEvent, EventBookmark
from projections.base import BaseProjection
from projections.exceptions import ProjectionStateError
from projections.models import ProjectionFailureLog

pytestmark = pytest.mark.django_db

URL = "/_health/alerts"


class _DummyProjection(BaseProjection):
    @property
    def name(self) -> str:
        return "test_a163_dummy"

    @property
    def consumes(self) -> list[str]:
        return ["test.a163.tick"]

    def handle(self, event) -> None:
        pass


def _make_event(company, seq=1):
    return BusinessEvent.objects.create(
        company=company,
        event_type="test.a163.tick",
        aggregate_type="TestTick",
        aggregate_id=str(seq),
        idempotency_key=f"test.a163:{company.id}:{seq}:{uuid4().hex[:6]}",
        data={"n": seq},
    )


def test_clean_state_returns_200(company):
    resp = Client().get(URL)
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["unresolved_failures"] == 0


def test_ready_endpoint_still_ignores_projection_failures(company, user):
    """Pinned contract: /_health/ready feeds load balancers and must stay
    DB-only — a projection failure must NOT pull web processes out of
    rotation. /_health/alerts is the endpoint that alerts."""
    event = _make_event(company)
    _DummyProjection().on_error(event, ProjectionStateError("mapping missing", fix_hint="run wizard"))
    assert ProjectionFailureLog.objects.filter(resolved=False).exists()

    assert Client().get("/_health/ready").status_code == 200


def test_unresolved_failure_returns_503(company, user):
    event = _make_event(company)
    _DummyProjection().on_error(event, ProjectionStateError("mapping missing", fix_hint="run wizard"))

    resp = Client().get(URL)
    assert resp.status_code == 503, resp.content
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert body["unresolved_failures"] == 1


def test_resolving_the_failure_recovers(company, user):
    event = _make_event(company)
    _DummyProjection().on_error(event, ProjectionStateError("mapping missing", fix_hint="run wizard"))
    log = ProjectionFailureLog.objects.get()
    log.mark_resolved(user=user, note="fixed the mapping")

    resp = Client().get(URL)
    assert resp.status_code == 200, resp.content


@override_settings(ALERT_PROJECTION_LAG_THRESHOLD=0)
def test_relevance_aware_lag_returns_503(company):
    # A registered projection with unprocessed events of a type it consumes.
    from projections.base import projection_registry

    projection_registry.register(_DummyProjection(), allow_override=True)
    try:
        EventBookmark.objects.create(consumer_name="test_a163_dummy", company=company)
        _make_event(company, seq=1)

        resp = Client().get(URL)
        assert resp.status_code == 503, resp.content
        assert resp.json()["total_lag"] >= 1
    finally:
        projection_registry._projections.pop("test_a163_dummy", None)


@override_settings(ALERT_PROJECTION_LAG_THRESHOLD=0)
def test_irrelevant_events_do_not_count_as_lag(company):
    """Guards against regressing to the pre-A135 coarse whole-stream
    counter, which paged on healthy systems (phantom lag)."""
    from projections.base import projection_registry

    projection_registry.register(_DummyProjection(), allow_override=True)
    try:
        EventBookmark.objects.create(consumer_name="test_a163_dummy", company=company)
        BusinessEvent.objects.create(
            company=company,
            event_type="totally.unrelated",
            aggregate_type="X",
            aggregate_id="1",
            idempotency_key=f"test.a163.unrelated:{uuid4().hex[:8]}",
            data={},
        )

        resp = Client().get(URL)
        assert resp.status_code == 200, resp.content
        assert resp.json()["total_lag"] == 0
    finally:
        projection_registry._projections.pop("test_a163_dummy", None)


def test_paused_bookmark_returns_503(company):
    EventBookmark.objects.create(consumer_name="account_balance", company=company, is_paused=True)
    resp = Client().get(URL)
    assert resp.status_code == 503, resp.content
    assert resp.json()["paused_consumers"] == 1


def test_null_company_bookmark_does_not_crash(company):
    EventBookmark.objects.create(consumer_name="some_global_consumer", company=None)
    resp = Client().get(URL)
    assert resp.status_code in (200, 503)


def test_body_is_aggregate_only(company, user):
    """The /_health/ prefix is auth-exempt — no company names/slugs."""
    event = _make_event(company)
    _DummyProjection().on_error(event, ProjectionStateError("boom"))
    body = Client().get(URL).json()
    flat = str(body)
    assert company.slug not in flat
    assert company.name not in flat


class TestAlertCheckCommand:
    def test_exits_nonzero_when_unhealthy(self, company, user):
        event = _make_event(company)
        _DummyProjection().on_error(event, ProjectionStateError("boom"))
        with pytest.raises(CommandError, match="ALERT"):
            call_command("alert_check")

    def test_succeeds_when_clean(self, company):
        call_command("alert_check")
