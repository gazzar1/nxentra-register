# tests/test_a105_exception_queue_trust.py
"""
A105 + F21 + F22 — exception-queue trust.

Before this fix a merchant could neither SEE nor CLEAR their own stuck
exceptions:
- A105: the ProjectionFailureLog docstring promised that a successful
  retry auto-resolves the entry — no such code existed, so self-healed
  failures sat unresolved forever (and, since A163, kept /_health/alerts
  firing 503 at the uptime pinger).
- F21: /finance/exceptions was reachable only by typing the URL — the
  Finance sidebar never linked it.
- F22: mark-resolved was Django is_staff-gated on BOTH ends, so a
  company OWNER saw "Admin only" on their own queue.
"""

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from rest_framework.test import APIClient

from accounts.models import CompanyMembership
from events.models import BusinessEvent
from projections.base import BaseProjection
from projections.exceptions import ProjectionStateError, ProjectionTerminalSkip
from projections.models import ProjectionFailureLog

pytestmark = pytest.mark.django_db

User = get_user_model()


class _FlakyProjection(BaseProjection):
    """Fails with an operator-fixable StateError until healed."""

    heal = False

    @property
    def name(self) -> str:
        return "test_a105_flaky"

    @property
    def consumes(self) -> list[str]:
        return ["test.a105.tick"]

    def handle(self, event) -> None:
        if not type(self).heal:
            raise ProjectionStateError("mapping missing", fix_hint="wire the mapping")


class _QuarantineProjection(BaseProjection):
    """Terminal-skips its event (immutable-payload defect)."""

    @property
    def name(self) -> str:
        return "test_a105_quarantine"

    @property
    def consumes(self) -> list[str]:
        return ["test.a105.quarantine"]

    def handle(self, event) -> None:
        raise ProjectionTerminalSkip("payload broken", fix_hint="re-import under a new batch id")


def _make_event(company, event_type="test.a105.tick"):
    return BusinessEvent.objects.create(
        company=company,
        event_type=event_type,
        aggregate_type="TestTick",
        aggregate_id=uuid4().hex[:8],
        idempotency_key=f"{event_type}:{uuid4().hex[:10]}",
        data={},
    )


class TestSelfHealAutoResolve:
    def test_successful_retry_auto_resolves_the_failure_row(self, company):
        """The docstring promise, finally implemented: fail -> unresolved
        row; fix the state; retry succeeds -> row resolves itself."""
        _FlakyProjection.heal = False
        projection = _FlakyProjection()
        _make_event(company)

        projection.process_pending(company)
        log = ProjectionFailureLog.objects.get(company=company, projection_name="test_a105_flaky")
        assert log.resolved is False

        _FlakyProjection.heal = True
        projection.process_pending(company)

        log.refresh_from_db()
        assert log.resolved is True, "a successful retry must auto-resolve the failure entry"
        assert "Self-healed" in log.resolution_note
        assert log.resolved_at is not None

    def test_self_heal_recovers_health_alerts(self, company):
        """A163 integration: the self-heal must flip /_health/alerts back
        to 200 without operator action."""
        _FlakyProjection.heal = False
        projection = _FlakyProjection()
        _make_event(company)
        projection.process_pending(company)
        assert Client().get("/_health/alerts").status_code == 503

        _FlakyProjection.heal = True
        projection.process_pending(company)
        assert Client().get("/_health/alerts").status_code == 200, (
            "self-heal must clear the alert without a human resolving it"
        )

    def test_terminal_skip_rows_stay_open(self, company):
        """TerminalSkip advances past the event (applied + bookmark), so a
        later pass re-scans it through the idempotent short-circuit — that
        must NOT auto-resolve the quarantine row (handle never succeeded)."""
        projection = _QuarantineProjection()
        _make_event(company, event_type="test.a105.quarantine")

        projection.process_pending(company)
        log = ProjectionFailureLog.objects.get(company=company, projection_name="test_a105_quarantine")
        assert log.resolved is False

        # Second pass: the event is applied; the short-circuit path runs.
        projection.process_pending(company)
        log.refresh_from_db()
        assert log.resolved is False, "quarantined (terminal-skip) rows must stay open until an operator acts"


def _failure_row(company):
    projection = _FlakyProjection()
    _FlakyProjection.heal = False
    _make_event(company)
    projection.process_pending(company)
    return ProjectionFailureLog.objects.filter(company=company, projection_name="test_a105_flaky").latest("id")


def _member(company, role, tag):
    user = User.objects.create_user(
        public_id=uuid4(), email=f"{tag}-{uuid4().hex[:6]}@test.com", password="x", name=tag
    )
    user.active_company = company
    user.save()
    CompanyMembership.objects.create(public_id=uuid4(), company=company, user=user, role=role, is_active=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class TestOwnerCanResolve:
    def test_owner_without_staff_flag_can_resolve(self, company):
        log = _failure_row(company)
        client = _member(company, CompanyMembership.Role.OWNER, "owner")

        resp = client.post(
            f"/api/reports/projection-failures/{log.pk}/resolve/",
            {"resolution_note": "fixed the mapping myself"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        log.refresh_from_db()
        assert log.resolved is True
        assert log.resolution_note == "fixed the mapping myself"

    def test_admin_role_can_resolve(self, company):
        log = _failure_row(company)
        client = _member(company, CompanyMembership.Role.ADMIN, "admin")
        resp = client.post(f"/api/reports/projection-failures/{log.pk}/resolve/", {}, format="json")
        assert resp.status_code == 200, resp.content

    def test_user_and_viewer_roles_cannot_resolve(self, company):
        log = _failure_row(company)
        for role, tag in ((CompanyMembership.Role.USER, "user"), (CompanyMembership.Role.VIEWER, "viewer")):
            client = _member(company, role, tag)
            resp = client.post(f"/api/reports/projection-failures/{log.pk}/resolve/", {}, format="json")
            assert resp.status_code == 403, f"{tag} must not clear the queue: {resp.content}"
        log.refresh_from_db()
        assert log.resolved is False


class TestSidebarLink:
    def test_finance_sidebar_links_exceptions(self, company, user, owner_membership):
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.get("/api/sidebar/")
        assert resp.status_code == 200, resp.content

        body = resp.json()
        hrefs = [
            item.get("href")
            for sections in body.values()
            for section in sections
            for item in section.get("nav_items", [])
        ]
        assert "/finance/exceptions" in hrefs, (
            "the exception queue must be reachable from the Finance sidebar, not URL-only"
        )
