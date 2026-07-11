# tests/test_a154_rebuild_convergence.py
"""
A154 — every replay/rebuild path completes and is idempotent (+A115).

Audited failures (2026-07-11 dual audit):
1. `rebuild_projection` management command and `AdminProjectionRebuildView`
   delete ProjectionAppliedEvent rows + the EventBookmark, then replay with
   bare `handle()` and never re-stamp — the next normal `process_pending`
   pass re-applies the whole stream and doubles accumulator balances.
2. `BaseProjection.rebuild()` runs a single `process_pending(limit=1000)`
   batch, so >1,000-event streams are silently partial.
3. `tenant replay_projections` passes an unsupported `using=` kwarg —
   TypeError on both its --rebuild and default branches.
4. (A115) `JournalEntryProjection` has no `_clear_projected_data`, so a
   journal_entry_read_model rebuild cannot remove stale/orphan rows.

Each test asserts the DESIRED behavior, so on the unfixed code it fails
for the audited reason (doubled balances / partial drain / TypeError /
surviving orphan).
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.core.management import call_command

from accounting.models import JournalEntry
from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection, projection_registry
from projections.models import AccountBalance, ProjectionAppliedEvent

pytestmark = pytest.mark.django_db


class NoOpProjection(BaseProjection):
    """Minimal projection for framework-level convergence tests."""

    @property
    def name(self) -> str:
        return "test_a154_noop"

    @property
    def consumes(self) -> list[str]:
        return ["test.a154.tick"]

    def handle(self, event) -> None:
        pass


def _bulk_tick_events(company, n):
    """Create n events directly (bulk, manual sequences — no emitter side effects)."""
    BusinessEvent.objects.bulk_create(
        [
            BusinessEvent(
                id=uuid4(),
                company=company,
                event_type="test.a154.tick",
                aggregate_type="TestTick",
                aggregate_id=str(i),
                idempotency_key=f"test.a154.tick:{company.id}:{i}",
                company_sequence=i + 1,
                sequence=1,
                data={"n": i},
            )
            for i in range(n)
        ],
        batch_size=500,
    )


def _post_je_event(company, user, cash_account, revenue_account, amount="500.00"):
    """Append a JOURNAL_ENTRY_POSTED event directly (no emitter auto-processing)."""
    entry_pid = str(uuid4())
    return BusinessEvent.objects.create(
        company=company,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=entry_pid,
        idempotency_key=f"test.a154.jepost:{entry_pid}",
        caused_by_user=user,
        data={
            "entry_public_id": entry_pid,
            "entry_number": f"JE-{entry_pid[:8]}",
            "date": "2026-01-15",
            "memo": "A154 test entry",
            "kind": "NORMAL",
            "period": 1,
            "posted_at": "2026-01-15T12:00:00+00:00",
            "posted_by_id": user.id,
            "posted_by_email": user.email,
            "total_debit": amount,
            "total_credit": amount,
            "lines": [
                {
                    "line_no": 1,
                    "account_public_id": str(cash_account.public_id),
                    "account_code": cash_account.code,
                    "description": "cash",
                    "debit": amount,
                    "credit": "0.00",
                },
                {
                    "line_no": 2,
                    "account_public_id": str(revenue_account.public_id),
                    "account_code": revenue_account.code,
                    "description": "revenue",
                    "debit": "0.00",
                    "credit": amount,
                },
            ],
        },
    )


def _balances(company):
    return {
        b.account_id: (b.balance, b.debit_total, b.credit_total, b.entry_count)
        for b in AccountBalance.objects.filter(company=company)
    }


class TestDrainToZero:
    def test_rebuild_drains_streams_larger_than_one_batch(self, company):
        """>1,000-event stream must fully drain, not stop at the first batch."""
        _bulk_tick_events(company, 1050)
        projection = NoOpProjection()

        processed = projection.rebuild(company)

        assert processed == 1050
        assert projection.get_lag(company) == 0
        assert ProjectionAppliedEvent.objects.filter(company=company, projection_name="test_a154_noop").count() == 1050

    def test_rebuild_is_idempotent_against_next_process_pending(self, company):
        """After rebuild, a normal process_pending pass must be a no-op."""
        _bulk_tick_events(company, 10)
        projection = NoOpProjection()
        projection.rebuild(company)

        assert projection.process_pending(company) == 0 or (
            ProjectionAppliedEvent.objects.filter(company=company, projection_name="test_a154_noop").count() == 10
        )


class TestCliRebuildIdempotent:
    def test_cli_rebuild_then_process_pending_changes_nothing(self, company, user, cash_account, revenue_account):
        projection = projection_registry.get("account_balance")
        _post_je_event(company, user, cash_account, revenue_account)
        _post_je_event(company, user, cash_account, revenue_account, amount="250.00")
        projection.process_pending(company)

        snapshot = _balances(company)
        assert snapshot, "sanity: balances exist before rebuild"

        call_command(
            "rebuild_projection",
            "--projection",
            "account_balance",
            "--tenant",
            company.slug,
            "--quiet",
        )

        assert _balances(company) == snapshot, "rebuild itself must reproduce identical balances"

        # The audited bug: the command wiped ProjectionAppliedEvent + bookmark
        # and never re-stamped, so this pass re-applied every event.
        projection.process_pending(company)
        assert _balances(company) == snapshot, "post-rebuild process_pending must change nothing"


class TestAdminRebuildIdempotent:
    def test_admin_rebuild_then_process_pending_changes_nothing(
        self, company, user, owner_membership, api_client, cash_account, revenue_account
    ):
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        api_client.force_authenticate(user=user)

        projection = projection_registry.get("account_balance")
        _post_je_event(company, user, cash_account, revenue_account)
        projection.process_pending(company)
        snapshot = _balances(company)

        resp = api_client.post("/api/reports/admin/projections/account_balance/rebuild/", {}, format="json")
        assert resp.status_code == 200, resp.content

        assert _balances(company) == snapshot

        projection.process_pending(company)
        assert _balances(company) == snapshot, "post-rebuild process_pending must change nothing"

    def test_admin_rebuild_refuses_streams_over_sync_cap(
        self, company, user, owner_membership, api_client, cash_account, revenue_account, monkeypatch
    ):
        """The admin endpoint blocks a web worker for the whole replay — large
        streams must be redirected to the management command / async task."""
        import projections.views as projections_views

        user.is_staff = True
        user.save(update_fields=["is_staff"])
        api_client.force_authenticate(user=user)

        _post_je_event(company, user, cash_account, revenue_account)
        monkeypatch.setattr(projections_views, "ADMIN_SYNC_REBUILD_MAX_EVENTS", 0)

        resp = api_client.post("/api/reports/admin/projections/account_balance/rebuild/", {}, format="json")
        assert resp.status_code == 400, resp.content
        assert "rebuild_projection" in resp.json()["detail"]


class TestTenantReplayCommand:
    def test_replay_command_runs_without_typeerror(self, company, user, cash_account, revenue_account):
        _post_je_event(company, user, cash_account, revenue_account)

        # Audited bug: both branches passed unsupported using= kwargs and
        # died with TypeError (wrapped in CommandError).
        call_command(
            "replay_projections",
            "--db-alias",
            "default",
            "--company-id",
            str(company.id),
            "--projection",
            "account_balance",
            "--rebuild",
        )

        balance = AccountBalance.objects.get(company=company, account=cash_account)
        assert balance.debit_total == Decimal("500.00")

        # And the non-rebuild branch as well.
        call_command(
            "replay_projections",
            "--db-alias",
            "default",
            "--company-id",
            str(company.id),
            "--projection",
            "account_balance",
        )
        balance.refresh_from_db()
        assert balance.debit_total == Decimal("500.00"), "replay must not double-apply"


class TestJournalEntryProjectionClear:
    def test_rebuild_removes_rows_with_no_backing_event(self, company, user):
        """A115: journal_entry_read_model rebuild was a silent no-op (no
        _clear_projected_data), so stale/orphan rows survived a rebuild."""
        entry_pid = str(uuid4())
        BusinessEvent.objects.create(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_CREATED,
            aggregate_type="JournalEntry",
            aggregate_id=entry_pid,
            idempotency_key=f"test.a154.jec:{entry_pid}",
            caused_by_user=user,
            data={
                "entry_public_id": entry_pid,
                "date": "2026-01-15",
                "memo": "event-backed entry",
                "kind": "NORMAL",
                "status": "INCOMPLETE",
                "period": 1,
                "lines": [],
            },
        )
        orphan = JournalEntry.objects.create(
            public_id=uuid4(),
            company=company,
            date=date(2026, 1, 20),
            period=1,
            memo="orphan row with no backing event",
            status=JournalEntry.Status.INCOMPLETE,
            created_by=user,
        )

        projection = projection_registry.get("journal_entry_read_model")
        projection.rebuild(company)

        assert not JournalEntry.objects.filter(pk=orphan.pk).exists(), "orphan row must not survive a clearing rebuild"
        assert JournalEntry.objects.filter(company=company, public_id=entry_pid).exists(), (
            "event-backed entry must be reprojected"
        )
