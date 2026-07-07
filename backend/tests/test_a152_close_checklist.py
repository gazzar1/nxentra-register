"""
A152 item 3 — close_period evaluates the readiness checklist server-side.

Pre-A152 close_period was a bare status flip; the 8-point gate lived only in the
Month-End Close UI, so the periods-table Close button and the raw API skipped
it. Now the command runs the SAME checklist and blocks on a FAILing BLOCKING
check (trial balance, drafts) unless the caller passes force + a reason. Close
stays advisory (WARN and non-blocking Shopify checks never block).
"""

from datetime import date
from uuid import uuid4

import pytest

from accounting.commands import close_period
from accounts.authz import ActorContext
from projections.close_checks import (
    BLOCKING_CHECKS,
    checklist_has_blocking_failure,
    run_close_checklist,
)
from projections.models import FiscalPeriod


def _make_actor(company, user, membership):
    perms = frozenset(membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=membership, perms=perms)


def _current_open_period(company):
    today = date.today()
    return FiscalPeriod.objects.get(company=company, fiscal_year=today.year, period=today.month)


def _make_draft_entry(company, user, when: date):
    from accounting.models import JournalEntry

    return JournalEntry.objects.projection().create(
        public_id=uuid4(),
        company=company,
        date=when,
        period=when.month,
        memo="unfinished",
        status=JournalEntry.Status.DRAFT,
        created_by=user,
        entry_number=f"JE-D-{uuid4().hex[:6]}",
    )


@pytest.mark.django_db
def test_blocking_set_is_trial_balance_and_drafts():
    assert frozenset({"trial_balance", "draft_entries"}) == BLOCKING_CHECKS


@pytest.mark.django_db
def test_checklist_marks_blocking_flag(company):
    today = date.today()
    checks = run_close_checklist(company, today.replace(day=1), today)
    by_check = {c["check"]: c for c in checks}
    assert by_check["trial_balance"]["blocking"] is True
    assert by_check["draft_entries"]["blocking"] is True
    # A Shopify-specific check is present but never blocks.
    assert by_check["shopify_store"]["blocking"] is False


@pytest.mark.django_db
def test_non_blocking_failure_does_not_block(company):
    """A no-Shopify-store company FAILs the store check, but that is advisory:
    checklist_has_blocking_failure stays False, so the close is allowed."""
    today = date.today()
    checks = run_close_checklist(company, today.replace(day=1), today)
    store = next(c for c in checks if c["check"] == "shopify_store")
    assert store["status"] == "FAIL"  # no active store on a bare test company
    assert checklist_has_blocking_failure(checks) is False


@pytest.mark.django_db
def test_close_succeeds_when_no_blocking_failure(company, user, owner_membership):
    actor = _make_actor(company, user, owner_membership)
    period = _current_open_period(company)

    result = close_period(actor, period.fiscal_year, period.period)

    assert result.success, result.error
    period.refresh_from_db()
    assert period.status == FiscalPeriod.Status.CLOSED
    # A clean close is not "forced".
    assert result.event.get_data().get("forced") is False


@pytest.mark.django_db
def test_draft_in_period_blocks_close_without_force(company, user, owner_membership):
    actor = _make_actor(company, user, owner_membership)
    period = _current_open_period(company)
    _make_draft_entry(company, user, date.today())

    result = close_period(actor, period.fiscal_year, period.period)

    assert not result.success
    assert isinstance(result.data, dict)
    assert result.data.get("requires_force") is True
    checklist = result.data.get("checklist")
    drafts = next(c for c in checklist if c["check"] == "draft_entries")
    assert drafts["status"] == "FAIL"
    period.refresh_from_db()
    assert period.status == FiscalPeriod.Status.OPEN  # NOT closed


@pytest.mark.django_db
def test_force_without_reason_is_refused(company, user, owner_membership):
    actor = _make_actor(company, user, owner_membership)
    period = _current_open_period(company)
    _make_draft_entry(company, user, date.today())

    result = close_period(actor, period.fiscal_year, period.period, force=True, reason="   ")

    assert not result.success
    assert result.data.get("requires_reason") is True
    period.refresh_from_db()
    assert period.status == FiscalPeriod.Status.OPEN


@pytest.mark.django_db
def test_force_with_reason_closes_and_audits(company, user, owner_membership):
    actor = _make_actor(company, user, owner_membership)
    period = _current_open_period(company)
    _make_draft_entry(company, user, date.today())

    result = close_period(
        actor, period.fiscal_year, period.period, force=True, reason="Auditor sign-off pending; closing per CFO."
    )

    assert result.success, result.error
    period.refresh_from_db()
    assert period.status == FiscalPeriod.Status.CLOSED
    data = result.event.get_data()
    assert data.get("forced") is True
    assert "CFO" in data.get("force_reason")


@pytest.mark.django_db
def test_already_closed_period_is_rejected(company, user, owner_membership):
    actor = _make_actor(company, user, owner_membership)
    period = _current_open_period(company)
    close_period(actor, period.fiscal_year, period.period)  # first close

    result = close_period(actor, period.fiscal_year, period.period)
    assert not result.success
    assert "already closed" in result.error.lower()


@pytest.mark.django_db
def test_month_end_close_view_returns_blocking_flag(authenticated_client, company, owner_membership):
    today = date.today()
    resp = authenticated_client.get(f"/api/reports/month-end-close/?year={today.year}&month={today.month}")
    assert resp.status_code == 200
    body = resp.json()
    by_check = {c["check"]: c for c in body["checks"]}
    assert by_check["trial_balance"]["blocking"] is True
    assert by_check["shopify_store"]["blocking"] is False
