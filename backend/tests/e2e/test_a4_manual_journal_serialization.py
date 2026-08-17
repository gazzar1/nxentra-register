# tests/e2e/test_a4_manual_journal_serialization.py
"""A4 Manual-Journal EGP Admission — activation serialization on real PostgreSQL.

The manual wrappers (`*_manual_journal_entry`) own `serialized_company_admission`
(Company FOR NO KEY UPDATE) and `activate_pilot_profile` keeps its own Company
FOR UPDATE, so exactly one ordering can occur. Proven here in BOTH directions
through the REAL production commands (a test never manufactures the lock):

* activation-first, EGP     — activation commits; the waiting EGP manual journal
  then resumes and SUCCEEDS (the supported process never 403s);
* activation-first, foreign — the waiting foreign manual journal resumes, reads
  the fresh ACTIVE profile under the lock, and rejects with a stable
  PilotScopeBlocked and ZERO side effects;
* mutation-first, foreign (profile NONE) — the manual mutation owns admission;
  activation observably waits; the (legitimate-under-NONE) foreign journal
  commits; activation resumes and its preflight REFUSES on the committed
  non_egp_journal_data residue — the profile stays NONE;
* mutation-first, clean EGP — activation waits, the EGP journal commits, and
  activation then SUCCEEDS (all other preflight conditions being clean).

Harness reused verbatim from the merged admission-serialization suite.
"""

import threading
from datetime import date
from decimal import Decimal

import pytest
from django.db import connection

from accounts.models import Company
from accounts.pilot_policy import NON_EGP_INGESTION, PilotScopeBlocked

from .test_a4_runtime_admission_serialization import (
    _activation_clean,
    _activation_first,
    _actor,
    _fresh_profile,
    _pause_hook,
    _run_activation,
    _seed_account,
    _someone_is_lock_waiting,
    _wait,
    _Worker,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="manual-journal/activation admission serialization is only provable on PostgreSQL",
    ),
]

ISO = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1


@pytest.fixture(autouse=True)
def _suppress_projection_dispatch(monkeypatch):
    import events.emitter as emitter

    monkeypatch.setattr(emitter, "_schedule_projection_processing", lambda *a, **k: None)


def _seed_je_accounts(company):
    from accounting.models import Account

    cash = _seed_account(company, "10981", "MJ Cash", Account.AccountType.ASSET)
    revenue = _seed_account(company, "40981", "MJ Revenue", Account.AccountType.REVENUE)
    return cash, revenue


def _run_manual_create(company, cash, revenue, *, currency):
    from accounting.commands import create_manual_journal_entry

    return create_manual_journal_entry(
        _actor(company),
        date=date.today(),
        memo=f"serialization {currency or 'home'}",
        currency=currency,
        lines=[
            {"account_id": cash.id, "description": "d", "debit": Decimal("25.00"), "credit": Decimal("0")},
            {"account_id": revenue.id, "description": "c", "debit": Decimal("0"), "credit": Decimal("25.00")},
        ],
    )


def _journal_state(company):
    from accounting.models import JournalEntry
    from accounts.rls import rls_bypass
    from events.models import BusinessEvent

    with rls_bypass():
        return (
            JournalEntry.objects.filter(company=company).count(),
            BusinessEvent.objects.filter(company=company).count(),
        )


# --------------------------------------------------------------------------- #
# ACTIVATION FIRST — activation holds its Company FOR UPDATE; the manual
# mutation observably waits, then resumes against the fresh ACTIVE profile.
# --------------------------------------------------------------------------- #


def test_activation_first_egp_manual_journal_succeeds(company, owner_membership, monkeypatch):
    """The supported process never 403s: after activation commits, the waiting
    EGP manual journal resumes and succeeds under the active pilot."""
    _activation_clean(company)
    cash, revenue = _seed_je_accounts(company)

    m = _activation_first(monkeypatch, company, lambda: _run_manual_create(company, cash, revenue, currency="EGP"))

    assert m.error is None, f"EGP manual journal must succeed under the active pilot: {m.error}"
    assert m.result.success, m.result.error
    rows, _events = _journal_state(company)
    assert rows == 1


def test_activation_first_foreign_manual_journal_rejected_zero_side_effects(company, owner_membership, monkeypatch):
    """The waiting foreign manual journal reads the ACTIVE profile on the locked
    row and rejects deterministically — no row, no event."""
    _activation_clean(company)
    cash, revenue = _seed_je_accounts(company)
    before = _journal_state(company)

    m = _activation_first(monkeypatch, company, lambda: _run_manual_create(company, cash, revenue, currency="USD"))

    assert isinstance(m.error, PilotScopeBlocked), f"expected PilotScopeBlocked, got {m.error or m.result}"
    assert m.error.capability == NON_EGP_INGESTION
    assert _journal_state(company) == before, "a rejected foreign manual journal must write nothing"


# --------------------------------------------------------------------------- #
# MUTATION FIRST — the manual wrapper's admission lock blocks activation.
# --------------------------------------------------------------------------- #


def _mutation_first(company, monkeypatch, runner):
    """Run the manual mutation, pause it INSIDE the wrapper's admission
    transaction (on emit_event, i.e. after the shared command started writing),
    prove activation observably waits, then release and join both."""
    import accounting.commands as accounting_commands

    admitted, release = threading.Event(), threading.Event()
    _pause_hook(monkeypatch, accounting_commands, "emit_event", admitted, release)

    m = _Worker(runner, "mutation").start()
    _wait(admitted.is_set, "manual mutation admitted (Company admission lock held) and paused mid-write")

    a = _Worker(lambda: _run_activation(company.id), "activation").start()
    _wait(_someone_is_lock_waiting, "activation observably waiting on the Company admission row")
    assert a.thread.is_alive(), "activation must block while the manual wrapper holds the admission lock"

    release.set()
    m.join()
    a.join()
    return m, a


def test_mutation_first_foreign_none_journal_commits_then_preflight_refuses(company, owner_membership, monkeypatch):
    """Under profile NONE a foreign manual journal is legitimate: it commits
    while holding admission; activation (which observably waited) then runs its
    preflight against the committed residue and REFUSES — profile stays NONE."""
    _activation_clean(company)
    cash, revenue = _seed_je_accounts(company)

    m, a = _mutation_first(company, monkeypatch, lambda: _run_manual_create(company, cash, revenue, currency="USD"))

    assert m.error is None and m.result.success, (m.error, getattr(m.result, "error", None))
    refusal, out = a.result
    assert refusal is not None, "activation must refuse on the committed non-EGP journal residue"
    assert "non_egp_journal_data" in (str(refusal) + out)
    assert _fresh_profile(company) == Company.PilotProfile.NONE


def test_mutation_first_clean_egp_journal_then_activation_succeeds(company, owner_membership, monkeypatch):
    """A clean EGP manual journal is contract-compatible state: activation waits
    for its commit, then proceeds and activates."""
    _activation_clean(company)
    cash, revenue = _seed_je_accounts(company)

    m, a = _mutation_first(company, monkeypatch, lambda: _run_manual_create(company, cash, revenue, currency="EGP"))

    assert m.error is None and m.result.success, (m.error, getattr(m.result, "error", None))
    refusal, out = a.result
    assert refusal is None, f"activation must succeed after the clean EGP journal committed: {refusal}\n{out}"
    assert _fresh_profile(company) == ISO
