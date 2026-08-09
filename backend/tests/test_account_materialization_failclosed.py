# tests/test_account_materialization_failclosed.py
"""
A3-PR2b fresh-review P1: fail-closed required materialization for account
mutations.

update_account/delete_account emit their account event and then run the
synchronous projection drain — which is deliberately fail-soft: a paused
account_read_model bookmark, a handler error, or PROJECTIONS_SYNC=False all
return control with the event pending and the Account row untouched. Before
this correction the command committed that lower-sequence event anyway,
letting a later JOURNAL_ENTRY_POSTED validate at a higher company sequence
against stale AccountFacts — the forbidden history.

These tests prove the fail-closed contract: the command reports success ONLY
when the exact ProjectionAppliedEvent marker exists AND the Account-row
postcondition holds; otherwise everything — event, consumed sequence, marker,
row change, on_commit projection dispatch — rolls back. Rollback is proven
from database state, never from CommandResult alone.

Scenario map (per the founder test matrix):
  A sync disabled   B bookmark paused   C handler raises   D silent no-op
  E update success  F delete success    G financial-history proof
"""

from datetime import date
from uuid import uuid4

import pytest
from django.utils import timezone

from accounting.commands import delete_account, update_account
from accounting.models import Account
from accounting.posted_journal_boundary import emit_posted_journal
from accounts.authz import system_actor_for_company
from accounts.rls import rls_bypass
from events.models import BusinessEvent, CompanyEventCounter, EventBookmark
from projections.accounting import AccountProjection
from projections.models import ProjectionAppliedEvent

ACCOUNT_MUTATION_EVENTS = ("account.updated", "account.deleted")


@pytest.fixture(autouse=True)
def _owner(owner_membership):
    """system_actor_for_company requires an active OWNER membership."""
    return owner_membership


# --------------------------------------------------------------------------- #
# Builders / probes
# --------------------------------------------------------------------------- #


def _account(company, code="1000", *, account_type=None, status="ACTIVE"):
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code=code,
        name=f"P1 {code}",
        account_type=account_type or Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        status=status,
    )


def _actor(company):
    return system_actor_for_company(company)


def _counter(company):
    with rls_bypass():
        row = CompanyEventCounter.objects.filter(company=company).first()
    return row.last_sequence if row else 0


def _snapshot(company):
    """Every financial surface a failed mutation must leave untouched."""
    with rls_bypass():
        return {
            "events": BusinessEvent.objects.filter(company=company).count(),
            "sequence": _counter(company),
            "accounts": list(
                Account.objects.filter(company=company)
                .order_by("pk")
                .values("pk", "status", "name", "description", "account_type", "code")
            ),
            "markers": ProjectionAppliedEvent.objects.filter(company=company).count(),
        }


def _mutation_events(company):
    with rls_bypass():
        return list(BusinessEvent.objects.filter(company=company, event_type__in=ACCOUNT_MUTATION_EVENTS))


def _pause_account_read_model(company):
    bookmark, _ = EventBookmark.objects.get_or_create(
        consumer_name="account_read_model",
        company=company,
    )
    bookmark.is_paused = True
    bookmark.save(update_fields=["is_paused", "updated_at"])
    return bookmark


def _mutate(company, op, account):
    if op == "update":
        return update_account(_actor(company), account.id, name="Renamed by P1 test")
    return delete_account(_actor(company), account.id)


def _assert_failed_closed(result, before, company):
    assert not result.success
    assert "did not materialize" in result.error
    assert _snapshot(company) == before


# --------------------------------------------------------------------------- #
# A — projection synchronization disabled
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
@pytest.mark.parametrize("op", ["update", "delete"])
def test_a_sync_disabled_fails_closed(company, settings, django_capture_on_commit_callbacks, op):
    account = _account(company)
    before = _snapshot(company)
    settings.PROJECTIONS_SYNC = False

    with django_capture_on_commit_callbacks() as callbacks:
        result = _mutate(company, op, account)

    _assert_failed_closed(result, before, company)
    # The on_commit projection dispatch registered at event insert must be
    # discarded with the rolled-back transaction — nothing survives to run.
    assert callbacks == []


# --------------------------------------------------------------------------- #
# B — account_read_model bookmark paused
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
@pytest.mark.parametrize("op", ["update", "delete"])
def test_b_bookmark_paused_fails_closed(company, op):
    account = _account(company)
    _pause_account_read_model(company)
    before = _snapshot(company)

    result = _mutate(company, op, account)

    _assert_failed_closed(result, before, company)
    # The pause state itself survives — the rollback covers only this
    # attempt's writes, not the operator's pause.
    bookmark = EventBookmark.objects.get(consumer_name="account_read_model", company=company)
    assert bookmark.is_paused is True


# --------------------------------------------------------------------------- #
# C — AccountProjection handler raises
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
@pytest.mark.parametrize("op", ["update", "delete"])
def test_c_handler_raises_fails_closed(company, monkeypatch, op):
    account = _account(company)
    before = _snapshot(company)

    def boom(self, event):
        raise RuntimeError("forced handler failure")

    monkeypatch.setattr(AccountProjection, "handle", boom)

    # The drain is fail-soft (it logs and never re-raises), so the command
    # must fail via the materialization check — not surface a 500.
    result = _mutate(company, op, account)

    _assert_failed_closed(result, before, company)


# --------------------------------------------------------------------------- #
# D — handler silently returns without applying
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
@pytest.mark.parametrize("op", ["update", "delete"])
def test_d_silent_noop_handler_fails_closed(company, monkeypatch, op):
    account = _account(company)
    before = _snapshot(company)

    # A no-op handler lets process_pending stamp the ProjectionAppliedEvent
    # marker while the row stays untouched — the row postcondition, not the
    # marker, must catch this.
    monkeypatch.setattr(AccountProjection, "handle", lambda self, event: None)

    result = _mutate(company, op, account)

    _assert_failed_closed(result, before, company)
    # The stamped marker itself was rolled back with the mutation.
    with rls_bypass():
        assert not ProjectionAppliedEvent.objects.filter(company=company).exists()


# --------------------------------------------------------------------------- #
# E / F — normal success proofs
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_e_update_success_commits_event_marker_and_fields(company):
    account = _account(company)
    sequence_before = _counter(company)

    result = update_account(_actor(company), account.id, name="Renamed", description="new description")

    assert result.success
    account.refresh_from_db()
    assert account.name == "Renamed"
    assert account.description == "new description"
    events = _mutation_events(company)
    assert len(events) == 1 and events[0].event_type == "account.updated"
    with rls_bypass():
        assert ProjectionAppliedEvent.objects.filter(
            company=company, projection_name="account_read_model", event=events[0]
        ).exists()
    assert _counter(company) == sequence_before + 1


@pytest.mark.django_db
def test_f_delete_success_commits_event_marker_and_status(company):
    account = _account(company)
    sequence_before = _counter(company)

    result = delete_account(_actor(company), account.id)

    assert result.success
    account.refresh_from_db()
    assert account.status == Account.Status.INACTIVE
    events = _mutation_events(company)
    assert len(events) == 1 and events[0].event_type == "account.deleted"
    with rls_bypass():
        assert ProjectionAppliedEvent.objects.filter(
            company=company, projection_name="account_read_model", event=events[0]
        ).exists()
    assert _counter(company) == sequence_before + 1


# --------------------------------------------------------------------------- #
# G — financial-history proof
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_g_journal_posts_against_account_only_because_mutation_never_committed(company):
    """After a failed (paused) deactivation attempt, an otherwise-valid
    journal may post against the still-active Account — and that is
    acceptable ONLY because the mutation itself did not commit: no
    lower-sequence account event exists waiting to apply."""
    cash = _account(company, "1000")
    revenue = _account(company, "4000", account_type=Account.AccountType.REVENUE)
    _pause_account_read_model(company)

    result = delete_account(_actor(company), cash.id)
    assert not result.success

    # No account mutation event survived — nothing lower-sequence is pending.
    assert _mutation_events(company) == []
    cash.refresh_from_db()
    assert cash.status == Account.Status.ACTIVE

    amount = "50.00"
    payload = {
        "entry_public_id": str(uuid4()),
        "entry_number": "",
        "date": date.today().isoformat(),
        "memo": "p1 financial-history probe",
        "kind": "NORMAL",
        "period": date.today().month,
        "currency": company.default_currency or "EGP",
        "exchange_rate": "1.0",
        "posted_at": timezone.now().isoformat(),
        "posted_by_id": 0,
        "posted_by_email": "p1@example.com",
        "total_debit": amount,
        "total_credit": amount,
        "lines": [
            {
                "line_no": 1,
                "account_public_id": str(cash.public_id),
                "debit": amount,
                "credit": "0",
            },
            {
                "line_no": 2,
                "account_public_id": str(revenue.public_id),
                "debit": "0",
                "credit": amount,
            },
        ],
    }
    event = emit_posted_journal(
        company=company,
        data=payload,
        aggregate_type="JournalEntry",
        aggregate_id=payload["entry_public_id"],
        idempotency_key=f"p1:{uuid4()}",
    )
    assert event is not None
    # The journal validated against genuinely committed state — the account
    # is truly ACTIVE, not stale-pending-deactivation.
    assert _mutation_events(company) == []


# --------------------------------------------------------------------------- #
# H — stale idempotent replay diagnosed accurately, not blamed on projection
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_h_stale_idempotent_replay_fails_with_accurate_diagnosis(company):
    """Rename A→B, B→A, then A→B again: the third call's changes hash
    collides with the FIRST event's idempotency key, so emit_event dedups
    and returns the historical event — nothing new is emitted and the drain
    has nothing pending. Before this correction that path silently returned
    success with the row unchanged (a success-lie); it must now fail with
    the stale-replay diagnosis and MUST NOT blame projection health (the
    projection is perfectly healthy here)."""
    account = _account(company)
    actor = _actor(company)
    original_name = account.name

    assert update_account(actor, account.id, name="Toggled").success
    assert update_account(actor, account.id, name=original_name).success
    before = _snapshot(company)

    result = update_account(actor, account.id, name="Toggled")

    assert not result.success
    assert "stale idempotent replay" in result.error
    assert "NOT a projection failure" in result.error
    assert "retry after the account projection is healthy" not in result.error
    # Nothing was emitted, consumed, or changed by the replayed attempt.
    assert _snapshot(company) == before
