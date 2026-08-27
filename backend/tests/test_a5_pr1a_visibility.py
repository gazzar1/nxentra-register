# tests/test_a5_pr1a_visibility.py
"""A5-PR1a — exception visibility and alert health fail closed.

The core invariant under test: missing or failed visibility data must never be
presented as an all-clear state.

Pins, per the PR contract:
- /_health/alerts staleness: a small-but-OLD pending backlog pages even under
  the count threshold (dead workers with a live broker previously read healthy
  indefinitely); a recent backlog and a legitimately idle consumer never
  false-page.
- /_health/alerts missing consumers: a registered projection/company pair with
  relevant events but NO bookmark row pages (the bookmark-derived scan read it
  as zero lag).
- The unordered [:500] bookmark cap is gone — a paused/erroring bookmark past
  500 rows cannot be hidden.
- Counter registry safety: conflicting duplicate registration raises loudly;
  an idempotent same-callback repeat (re-run AppConfig.ready) is allowed; a
  raising counter yields a structured unhealthy 503 via alert_counter_errors —
  never a silent zero, never an uncontrolled 500.
- Shopify source health: pilot-scoped ACTIVE stores page on needs_reauth and
  on a stale scheduled sync (with a created_at grace for never-synced stores);
  non-pilot stores never contaminate the constrained-pilot alert.
- The alert body stays aggregate-only/PII-free and every pre-existing response
  key is unchanged (new fields are additive).
- failure-log endpoints enforce reports.view (list/detail/summary) and clamp
  limit at both bounds.
- /api/reports/projection-status/ no longer 500s (EventBookmark has no
  last_event_sequence field — the stream position is last_event.company_sequence).
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import Client
from django.utils import timezone

from events.models import BusinessEvent, EventBookmark
from projections.base import BaseProjection, projection_registry
from projections.models import ProjectionFailureLog

pytestmark = pytest.mark.django_db

ALERTS_URL = "/_health/alerts"


class _ProbeProjection(BaseProjection):
    @property
    def name(self) -> str:
        return "test_a5pr1a_probe"

    @property
    def consumes(self) -> list[str]:
        return ["test.a5pr1a.tick"]

    def handle(self, event) -> None:
        pass


def _make_event(company, seq=1):
    return BusinessEvent.objects.create(
        company=company,
        event_type="test.a5pr1a.tick",
        aggregate_type="TestTick",
        aggregate_id=str(seq),
        idempotency_key=f"test.a5pr1a:{company.id}:{seq}:{uuid4().hex[:6]}",
        data={"n": seq},
    )


@pytest.fixture
def probe_registered():
    projection_registry.register(_ProbeProjection(), allow_override=True)
    try:
        yield
    finally:
        projection_registry._projections.pop("test_a5pr1a_probe", None)


@pytest.fixture
def clean_counter_registries():
    """Snapshot/restore the alert counter registries around mutation tests."""
    from ops import health

    evidence_before = dict(health._REJECTED_EVIDENCE_COUNTERS)
    source_before = dict(health._SOURCE_HEALTH_COUNTERS)
    try:
        yield
    finally:
        health._REJECTED_EVIDENCE_COUNTERS.clear()
        health._REJECTED_EVIDENCE_COUNTERS.update(evidence_before)
        health._SOURCE_HEALTH_COUNTERS.clear()
        health._SOURCE_HEALTH_COUNTERS.update(source_before)


# =============================================================================
# /_health/alerts — projection staleness (J-11..13)
# =============================================================================


def test_small_old_backlog_is_unhealthy_via_staleness(company, settings, probe_registered):
    """J-11: one pending relevant event, far under the count threshold, aged
    past the staleness threshold → unhealthy. This is exactly the dead-worker
    blindness: count-only lag stayed 200 indefinitely."""
    settings.ALERT_PROJECTION_LAG_THRESHOLD = 50
    settings.ALERT_PROJECTION_STALENESS_SECONDS = 3600

    EventBookmark.objects.create(consumer_name="test_a5pr1a_probe", company=company)
    event = _make_event(company)
    BusinessEvent.objects.filter(pk=event.pk).update(recorded_at=timezone.now() - timedelta(hours=2))

    resp = Client().get(ALERTS_URL)
    assert resp.status_code == 503, resp.content
    body = resp.json()
    assert body["stale_consumers"] == 1
    assert body["total_lag"] >= 1  # truthfully reported, just under the count threshold
    assert body["thresholds"]["projection_staleness_seconds"] == 3600


def test_recent_small_backlog_does_not_false_page(company, settings, probe_registered):
    """J-12: the same pending backlog, still fresh → healthy."""
    settings.ALERT_PROJECTION_LAG_THRESHOLD = 50
    settings.ALERT_PROJECTION_STALENESS_SECONDS = 3600

    EventBookmark.objects.create(consumer_name="test_a5pr1a_probe", company=company)
    _make_event(company)

    resp = Client().get(ALERTS_URL)
    assert resp.status_code == 200, resp.content
    assert resp.json()["stale_consumers"] == 0


def test_idle_consumer_with_old_last_processed_does_not_page(company, settings, probe_registered):
    """J-13: no pending relevant work → never stale, regardless of how old
    last_processed_at is. Staleness keys on pending-event age, not idleness."""
    settings.ALERT_PROJECTION_STALENESS_SECONDS = 1

    event = _make_event(company)
    BusinessEvent.objects.filter(pk=event.pk).update(recorded_at=timezone.now() - timedelta(days=30))
    EventBookmark.objects.create(
        consumer_name="test_a5pr1a_probe",
        company=company,
        last_event=event,
        last_processed_at=timezone.now() - timedelta(days=30),
    )

    resp = Client().get(ALERTS_URL)
    assert resp.status_code == 200, resp.content
    assert resp.json()["stale_consumers"] == 0


# =============================================================================
# /_health/alerts — missing consumers (J-14)
# =============================================================================


def test_registered_consumer_with_events_but_no_bookmark_is_unhealthy(company, probe_registered):
    """J-14: relevant events exist but the consumer never drained (no bookmark
    row) — the bookmark-derived scan read this as zero lag; it must page."""
    _make_event(company)
    assert not EventBookmark.objects.filter(consumer_name="test_a5pr1a_probe", company=company).exists()

    resp = Client().get(ALERTS_URL)
    assert resp.status_code == 503, resp.content
    assert resp.json()["missing_consumers"] >= 1

    # Once the bookmark exists (the consumer drained at least once), the pair
    # stops being missing — the fresh 1-event backlog is plain lag again.
    EventBookmark.objects.create(consumer_name="test_a5pr1a_probe", company=company)
    resp2 = Client().get(ALERTS_URL)
    assert resp2.json()["missing_consumers"] == 0
    assert resp2.status_code == 200, resp2.content


# =============================================================================
# /_health/alerts — paused / errored / >500 scan (J-15..17)
# =============================================================================


def test_paused_consumer_is_unhealthy(company):
    EventBookmark.objects.create(consumer_name="a5pr1a_paused_probe", company=company, is_paused=True)
    resp = Client().get(ALERTS_URL)
    assert resp.status_code == 503, resp.content
    assert resp.json()["paused_consumers"] == 1


def test_errored_consumer_is_unhealthy(company):
    """J-16: previously the errored==0 clause had zero test coverage."""
    EventBookmark.objects.create(consumer_name="a5pr1a_errored_probe", company=company, error_count=2)
    resp = Client().get(ALERTS_URL)
    assert resp.status_code == 503, resp.content
    assert resp.json()["errored_consumers"] == 1


def test_more_than_500_bookmarks_cannot_hide_an_unhealthy_one(company):
    """J-17: the old unordered [:500] slice made the scanned subset
    database-arbitrary above 500 rows — a paused bookmark past the cap was
    invisible. The scan is now uncapped, so exactly one paused bookmark among
    501 must always be counted."""
    EventBookmark.objects.bulk_create(
        [EventBookmark(consumer_name=f"a5pr1a_cap_{i}", company=company) for i in range(500)]
    )
    EventBookmark.objects.create(consumer_name="a5pr1a_cap_paused", company=company, is_paused=True)
    assert EventBookmark.objects.filter(company=company).count() == 501

    resp = Client().get(ALERTS_URL)
    assert resp.status_code == 503, resp.content
    assert resp.json()["paused_consumers"] == 1


# =============================================================================
# Counter registry safety (J-18..20)
# =============================================================================


def _make_probe_counter():
    def _a5pr1a_probe_counter() -> int:
        return 0

    return _a5pr1a_probe_counter


def _a5pr1a_conflicting_counter() -> int:
    return 0


def test_conflicting_duplicate_registration_raises(clean_counter_registries):
    """J-18: a DIFFERENT callback under an existing name must refuse loudly —
    a silent replace would drop a family from the alert pool."""
    from ops.health import register_source_health_counter

    register_source_health_counter("a5pr1a_probe_condition", _make_probe_counter())
    with pytest.raises(RuntimeError, match="Conflicting source-health counter"):
        register_source_health_counter("a5pr1a_probe_condition", _a5pr1a_conflicting_counter)


def test_idempotent_same_callback_registration_is_allowed(clean_counter_registries):
    """J-19: a re-run AppConfig.ready re-defines the same closure at the same
    source location (same __module__ + __qualname__) — that repeat must not
    raise."""
    from ops.health import register_source_health_counter

    register_source_health_counter("a5pr1a_probe_condition", _make_probe_counter())
    register_source_health_counter("a5pr1a_probe_condition", _make_probe_counter())


def test_rejected_evidence_registry_has_same_conflict_guard(clean_counter_registries):
    from ops.health import register_rejected_evidence_counter

    register_rejected_evidence_counter("a5pr1a_probe_source", _make_probe_counter())
    with pytest.raises(RuntimeError, match="Conflicting rejected-evidence counter"):
        register_rejected_evidence_counter("a5pr1a_probe_source", _a5pr1a_conflicting_counter)


def test_source_health_condition_cannot_shadow_core_field(clean_counter_registries):
    from ops.health import register_source_health_counter

    with pytest.raises(RuntimeError, match="shadow a core"):
        register_source_health_counter("total_lag", _make_probe_counter())


def test_raising_counter_yields_structured_unhealthy_503(company, clean_counter_registries):
    """J-20: a counter exception must page (its count is UNKNOWN — never zero)
    through the structured 200/503 contract, not an uncontrolled 500."""
    from ops.health import register_source_health_counter

    def _a5pr1a_boom_counter() -> int:
        raise RuntimeError("counter blew up")

    register_source_health_counter("a5pr1a_boom_condition", _a5pr1a_boom_counter)

    resp = Client().get(ALERTS_URL)
    assert resp.status_code == 503, resp.content
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert body["alert_counter_errors"] == 1
    # The failed condition's value is unknown — it must be OMITTED, never
    # fabricated as 0.
    assert "a5pr1a_boom_condition" not in body
    # And the exception text must not leak into the auth-exempt body.
    assert "counter blew up" not in str(body)


# =============================================================================
# Shopify source health (J-23..28)
# =============================================================================


def _make_store(company, **kw):
    from shopify_connector.models import ShopifyStore

    defaults = dict(
        shop_domain=f"store-{uuid4().hex[:8]}.myshopify.com",
        status=ShopifyStore.Status.ACTIVE,
    )
    defaults.update(kw)
    return ShopifyStore.objects.create(company=company, **defaults)


def _make_pilot(company):
    from accounts.models import Company

    company.pilot_profile = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1
    company.save(update_fields=["pilot_profile"])
    return company


def test_pilot_store_needs_reauth_is_unhealthy(company):
    """J-23: a revoked token halts ALL ingestion while producing no events —
    lag/failure counters read all-clear, so the durable needs_reauth flag must
    page through the alert contract."""
    _make_pilot(company)
    _make_store(company, needs_reauth=True, last_sync_at=timezone.now())

    resp = Client().get(ALERTS_URL)
    assert resp.status_code == 503, resp.content
    assert resp.json()["shopify_reauth_required"] == 1


def test_stale_pilot_store_is_unhealthy(company, settings):
    """J-24: the scheduled sync has not completed inside the threshold."""
    settings.SHOPIFY_SOURCE_STALE_SECONDS = 3600
    _make_pilot(company)
    _make_store(company, last_sync_at=timezone.now() - timedelta(hours=2))

    resp = Client().get(ALERTS_URL)
    assert resp.status_code == 503, resp.content
    assert resp.json()["shopify_stale_sources"] == 1


def test_recently_synced_pilot_store_is_healthy(company, settings):
    """J-25."""
    settings.SHOPIFY_SOURCE_STALE_SECONDS = 3600
    _make_pilot(company)
    _make_store(company, last_sync_at=timezone.now() - timedelta(minutes=5))

    resp = Client().get(ALERTS_URL)
    assert resp.status_code == 200, resp.content
    assert resp.json()["shopify_stale_sources"] == 0


def test_never_synced_new_store_gets_grace_period(company, settings):
    """J-26: a newly connected store with last_sync_at=NULL is graded from
    created_at + the same threshold, not declared stale immediately — but a
    never-synced OLD store is stale."""
    settings.SHOPIFY_SOURCE_STALE_SECONDS = 3600
    _make_pilot(company)
    store = _make_store(company, last_sync_at=None)  # created_at = now

    resp = Client().get(ALERTS_URL)
    assert resp.status_code == 200, resp.content
    assert resp.json()["shopify_stale_sources"] == 0

    from shopify_connector.models import ShopifyStore

    ShopifyStore.objects.filter(pk=store.pk).update(created_at=timezone.now() - timedelta(hours=2))
    resp2 = Client().get(ALERTS_URL)
    assert resp2.status_code == 503, resp2.content
    assert resp2.json()["shopify_stale_sources"] == 1


def test_non_pilot_stores_do_not_contaminate_the_alert(company, second_company, settings):
    """J-27: the conditions are scoped to ISOLATED_SHADOW_LEDGER_V1 companies
    and ACTIVE stores — a non-pilot company's dead store must not page the
    constrained-pilot alert, and a DISCONNECTED pilot store must not either."""
    from shopify_connector.models import ShopifyStore

    settings.SHOPIFY_SOURCE_STALE_SECONDS = 3600
    # Non-pilot company (fixture default pilot_profile = NONE), dead ACTIVE store:
    _make_store(company, needs_reauth=True, last_sync_at=timezone.now() - timedelta(days=7))
    # Pilot company, but its dead store is DISCONNECTED:
    _make_pilot(second_company)
    _make_store(
        second_company,
        status=ShopifyStore.Status.DISCONNECTED,
        needs_reauth=True,
        last_sync_at=timezone.now() - timedelta(days=7),
    )

    resp = Client().get(ALERTS_URL)
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["shopify_reauth_required"] == 0
    assert body["shopify_stale_sources"] == 0


def test_source_health_counters_registered_by_adapter_not_core(company):
    """J-28: the callbacks run through adapter registration (AppConfig.ready)
    and core ops never imports shopify_connector. The architecture ratchet
    (Rule 16) pins the exact registered set."""
    import inspect

    from ops import health

    assert set(health._SOURCE_HEALTH_COUNTERS) >= {"shopify_reauth_required", "shopify_stale_sources"}
    for name in ("shopify_reauth_required", "shopify_stale_sources"):
        assert health._SOURCE_HEALTH_COUNTERS[name].__module__ == "shopify_connector.apps"
    assert "shopify" not in inspect.getsource(health).lower()


# =============================================================================
# Alert body contract (J-21, J-22)
# =============================================================================


def test_body_stays_aggregate_only_and_pii_free(company, user):
    """J-21: with every condition family active, the auth-exempt body carries
    no company identity, no shop domain, no filenames, no messages."""
    from projections.exceptions import ProjectionStateError

    _make_pilot(company)
    store = _make_store(company, needs_reauth=True, last_sync_at=timezone.now() - timedelta(days=2))
    event = _make_event(company)
    _ProbeProjection().on_error(event, ProjectionStateError("secret mapping detail"))

    body = Client().get(ALERTS_URL).json()
    flat = str(body)
    assert company.slug not in flat
    assert company.name not in flat
    assert store.shop_domain not in flat
    assert "secret mapping detail" not in flat


def test_existing_response_keys_unchanged_and_new_fields_additive(company):
    """J-22: the pre-A5-PR1a keys all survive with their meanings; the new
    fields are fixed-name integers; thresholds gains only an additive subkey."""
    body = Client().get(ALERTS_URL).json()

    pre_existing = {
        "status",
        "unresolved_failures",
        "unresolved_import_rejects",
        "open_rejected_evidence",
        "rejected_evidence_by_source",
        "total_lag",
        "paused_consumers",
        "errored_consumers",
        "thresholds",
    }
    new_fields = {
        "stale_consumers",
        "missing_consumers",
        "alert_counter_errors",
        "shopify_reauth_required",
        "shopify_stale_sources",
    }
    assert set(body) == pre_existing | new_fields, sorted(body)
    for field in new_fields:
        assert isinstance(body[field], int), field
    assert set(body["thresholds"]) == {
        "unresolved_failures_max",
        "projection_lag_threshold",
        "projection_staleness_seconds",
    }
    assert body["status"] in ("healthy", "unhealthy")


# =============================================================================
# Failure-log endpoint permissions (J-7..10)
# =============================================================================


def _make_failure(company):
    event = _make_event(company)
    return ProjectionFailureLog.objects.create(
        company=company,
        projection_name="test_a5pr1a_probe",
        event=event,
        event_type=event.event_type,
        message="probe failure",
    )


def test_member_without_reports_view_cannot_read_failure_logs(company, regular_user, user_membership, api_client):
    """J-7/J-9: list, detail and summary all 403 for a member without
    reports.view — the detail endpoint serves the RAW event payload, so the
    same read gate as the sibling evidence queues applies. No payload field
    may appear in the refusal."""
    log = _make_failure(company)
    api_client.force_authenticate(user=regular_user)

    for url in (
        "/api/reports/projection-failures/",
        f"/api/reports/projection-failures/{log.id}/",
        "/api/reports/projection-failures/summary/",
    ):
        resp = api_client.get(url)
        assert resp.status_code == 403, (url, resp.status_code)
        assert "event_data" not in str(resp.data), url


def test_member_with_reports_view_can_read_failure_logs(company, user, owner_membership, api_client):
    """J-8: OWNER (implicit reports.view) reads list, detail incl. payload,
    and summary."""
    log = _make_failure(company)
    api_client.force_authenticate(user=user)

    resp_list = api_client.get("/api/reports/projection-failures/")
    assert resp_list.status_code == 200, resp_list.data
    assert resp_list.data["total_count"] == 1

    resp_detail = api_client.get(f"/api/reports/projection-failures/{log.id}/")
    assert resp_detail.status_code == 200, resp_detail.data
    assert "event_data" in resp_detail.data

    resp_summary = api_client.get("/api/reports/projection-failures/summary/")
    assert resp_summary.status_code == 200, resp_summary.data
    assert resp_summary.data["total_unresolved"] == 1


def test_failure_list_clamps_negative_limit(company, user, owner_membership, api_client):
    """J-10: ?limit=-1 must not reach the queryset slice (Django refuses
    negative indexing with a 500)."""
    _make_failure(company)
    api_client.force_authenticate(user=user)
    resp = api_client.get("/api/reports/projection-failures/?limit=-1")
    assert resp.status_code == 200, resp.data
    assert resp.data["total_count"] == 1


# =============================================================================
# /api/reports/projection-status/ (section C)
# =============================================================================


def test_projection_status_serializes_bookmark_with_last_event(
    company, user, owner_membership, api_client, probe_registered
):
    """The endpoint read EventBookmark.last_event_sequence — a field that only
    exists on ProjectionStatus — so every bookmark-backed call 500ed. The
    stream position is last_event.company_sequence."""
    event = _make_event(company)
    EventBookmark.objects.create(consumer_name="test_a5pr1a_probe", company=company, last_event=event)

    api_client.force_authenticate(user=user)
    resp = api_client.get("/api/reports/projection-status/")
    assert resp.status_code == 200, getattr(resp, "data", resp.content)

    by_name = {p["projection_name"]: p for p in resp.data["projections"]}
    probe = by_name["test_a5pr1a_probe"]
    assert probe["last_event_sequence"] == event.company_sequence
    assert probe["lag"] == 0
    assert probe["is_paused"] is False


def test_projection_status_serializes_bookmark_without_last_event(
    company, user, owner_membership, api_client, probe_registered
):
    EventBookmark.objects.create(consumer_name="test_a5pr1a_probe", company=company)
    _make_event(company)

    api_client.force_authenticate(user=user)
    resp = api_client.get("/api/reports/projection-status/")
    assert resp.status_code == 200, getattr(resp, "data", resp.content)

    by_name = {p["projection_name"]: p for p in resp.data["projections"]}
    probe = by_name["test_a5pr1a_probe"]
    assert probe["last_event_sequence"] is None
    assert probe["lag"] == 1
    assert resp.data["all_healthy"] is False


def test_projection_status_shows_paused_and_erroring_state(
    company, user, owner_membership, api_client, probe_registered
):
    EventBookmark.objects.create(
        consumer_name="test_a5pr1a_probe",
        company=company,
        is_paused=True,
        error_count=3,
        last_error="handler exploded",
    )

    api_client.force_authenticate(user=user)
    resp = api_client.get("/api/reports/projection-status/")
    assert resp.status_code == 200, getattr(resp, "data", resp.content)

    by_name = {p["projection_name"]: p for p in resp.data["projections"]}
    probe = by_name["test_a5pr1a_probe"]
    assert probe["is_paused"] is True
    assert probe["error_count"] == 3
    assert probe["last_error"] == "handler exploded"


def test_projection_status_requires_reports_view(company, regular_user, user_membership, api_client):
    api_client.force_authenticate(user=regular_user)
    resp = api_client.get("/api/reports/projection-status/")
    assert resp.status_code == 403, resp.status_code
