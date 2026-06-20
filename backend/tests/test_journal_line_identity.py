# tests/test_journal_line_identity.py
"""ADR-0001 prerequisite P1 — JournalLine.public_id determinism.

A line's public_id must be a deterministic function of (entry public_id,
line_no) so it survives re-post / projection rebuild. Before P1,
JournalEntryProjection._replace_lines delete+recreated lines with a fresh
uuid4 on every projecting event, churning the id and dangling any reference
to it (a reconciliation match's journal_line_public_id, or a future
ReconciliationLink leg).
"""

from datetime import date

import pytest

from accounting.commands import create_journal_entry
from accounting.models import Account, JournalEntry, JournalLine
from accounts.authz import ActorContext
from projections.accounting import JournalEntryProjection, derive_journal_line_public_id
from projections.write_barrier import projection_writes_allowed


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


@pytest.fixture
def accounts(db, company):
    with projection_writes_allowed():
        cash = Account.objects.projection().create(
            company=company,
            code="10000",
            name="Cash P1",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        rev = Account.objects.projection().create(
            company=company,
            code="40000",
            name="Revenue P1",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    return cash, rev


def _make_je(actor, cash, rev):
    result = create_journal_entry(
        actor=actor,
        date=date(2026, 4, 26),
        memo="P1 determinism JE",
        lines=[
            {"account_id": cash.id, "description": "dr", "debit": "100", "credit": "0"},
            {"account_id": rev.id, "description": "cr", "debit": "0", "credit": "100"},
        ],
        kind=JournalEntry.Kind.NORMAL,
    )
    assert result.success, result
    return result.data


@pytest.mark.django_db
def test_journal_line_public_id_is_deterministic(actor, company, accounts):
    cash, rev = accounts
    entry = _make_je(actor, cash, rev)

    lines = list(JournalLine.objects.filter(entry=entry).order_by("line_no"))
    assert len(lines) == 2
    for line in lines:
        assert line.public_id == derive_journal_line_public_id(entry.public_id, line.line_no)


@pytest.mark.django_db
def test_journal_line_public_id_survives_rematerialization(actor, company, accounts):
    cash, rev = accounts
    entry = _make_je(actor, cash, rev)

    before_pid = {ln.line_no: ln.public_id for ln in JournalLine.objects.filter(entry=entry)}
    before_pk = {ln.line_no: ln.pk for ln in JournalLine.objects.filter(entry=entry)}
    assert before_pid

    # Force the JE projection to re-process from the event log (re-runs _replace_lines).
    from events.models import EventBookmark
    from projections.models import ProjectionAppliedEvent

    proj_name = JournalEntryProjection().name
    ProjectionAppliedEvent.objects.filter(company=company, projection_name=proj_name).delete()
    EventBookmark.objects.filter(company=company, consumer_name=proj_name).delete()
    JournalEntryProjection().process_pending(company)

    after = {ln.line_no: (ln.public_id, ln.pk) for ln in JournalLine.objects.filter(entry=entry)}

    # Guard against a false pass: re-materialization must have actually recreated
    # the rows (delete+recreate → new int pks). If pks are unchanged, the
    # projection did not re-run and the stability assertion below is meaningless.
    assert all(after[ln][1] != before_pk[ln] for ln in before_pk), (
        "lines were not re-materialized (int pks unchanged) — the reset/reprocess did not run"
    )

    # P1 invariant: public_ids are stable across re-materialization.
    assert {ln: after[ln][0] for ln in after} == before_pid, (
        "JournalLine public_ids changed across re-materialization (P1 regression)"
    )
