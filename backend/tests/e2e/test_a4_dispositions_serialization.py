# tests/e2e/test_a4_dispositions_serialization.py
"""A4 dispositions PR — activation serialization for the NEW decorator family,
proven on real PostgreSQL in BOTH orderings through the REAL production
commands (a test never manufactures the lock).

Representative family: ``record_customer_receipt`` (@requires_capability(
MANUAL_AR)) — chosen because it is the one new family that is BOTH a direct
posted-journal emitter (bypassing every shared journal command) and a
capability-blocked door. Every other new decorator family in this PR
(post/void sales documents, EDIM commit, the fiscal doors) rides the IDENTICAL
``requires_capability`` primitive whose lock ownership is pinned by Rule 12b
and already proven both-orderings by the merged admission-serialization suite;
the new in-view admission blocks (revaluation, exchange rates, draft-delete)
use the same ``serialized_company_admission`` primitive proven by the
manual-journal suite.

* activation-first — activation commits; the waiting receipt resumes, re-reads
  the fresh ACTIVE profile on the locked row, and rejects with a stable
  PilotScopeBlocked(manual_ar) and ZERO side effects;
* mutation-first  — the receipt (legitimate under NONE) owns admission;
  activation observably waits; the receipt commits; activation resumes and its
  preflight REFUSES on the committed manual_ar_financial_state residue — the
  profile stays NONE.
"""

import threading

import pytest
from django.db import connection

from accounts.models import Company
from accounts.pilot_policy import Capability, PilotScopeBlocked

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
        reason="dispositions/activation admission serialization is only provable on PostgreSQL",
    ),
]

ISO = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1


@pytest.fixture(autouse=True)
def _suppress_projection_dispatch(monkeypatch):
    import events.emitter as emitter

    monkeypatch.setattr(emitter, "_schedule_projection_processing", lambda *a, **k: None)


def _seed_receipt_fixtures(company):
    from accounting.models import Account, Customer
    from projections.write_barrier import command_writes_allowed

    bank = _seed_account(company, "10991", "Receipt Bank", Account.AccountType.ASSET)
    ar = _seed_account(company, "11991", "Receipt AR Control", Account.AccountType.ASSET)
    with command_writes_allowed():
        customer = Customer.objects.create(company=company, code="RC-1", name="Receipt Cust", currency="EGP")
    return customer, bank, ar


def _run_receipt(company, customer, bank, ar):
    from accounting.commands import record_customer_receipt

    return record_customer_receipt(
        _actor(company),
        customer_id=customer.id,
        receipt_date="2026-08-15",
        amount="25.00",
        bank_account_id=bank.id,
        ar_control_account_id=ar.id,
        memo="serialization receipt",
    )


def _receipt_state(company):
    from accounting.models import JournalEntry
    from accounts.rls import rls_bypass
    from events.models import BusinessEvent
    from events.types import EventTypes

    with rls_bypass():
        return (
            JournalEntry.objects.filter(company=company).count(),
            BusinessEvent.objects.filter(company=company, event_type=str(EventTypes.CUSTOMER_RECEIPT_RECORDED)).count(),
        )


def test_activation_first_receipt_rejected_zero_side_effects(company, owner_membership, monkeypatch):
    """After activation commits, the waiting receipt resumes against the fresh
    ACTIVE profile and the SERIALIZED decorator rejects — no journal, no event."""
    _activation_clean(company)
    customer, bank, ar = _seed_receipt_fixtures(company)
    before = _receipt_state(company)

    m = _activation_first(monkeypatch, company, lambda: _run_receipt(company, customer, bank, ar))

    assert isinstance(m.error, PilotScopeBlocked), f"expected PilotScopeBlocked, got {m.error or m.result}"
    assert m.error.capability == Capability.MANUAL_AR.value
    assert _receipt_state(company) == before, "a rejected receipt must write nothing"


def test_mutation_first_receipt_commits_then_preflight_refuses(company, owner_membership, monkeypatch):
    """Under profile NONE the receipt is legitimate: it commits while holding
    admission; activation (which observably waited) then runs its preflight
    against the committed receipt evidence and REFUSES — profile stays NONE."""
    import accounting.commands as accounting_commands

    _activation_clean(company)
    customer, bank, ar = _seed_receipt_fixtures(company)

    admitted, release = threading.Event(), threading.Event()
    _pause_hook(monkeypatch, accounting_commands, "emit_posted_journal", admitted, release)

    m = _Worker(lambda: _run_receipt(company, customer, bank, ar), "receipt").start()
    _wait(admitted.is_set, "receipt admitted (Company admission lock held) and paused mid-emit")

    a = _Worker(lambda: _run_activation(company.id), "activation").start()
    _wait(_someone_is_lock_waiting, "activation observably waiting on the Company admission row")
    assert a.thread.is_alive(), "activation must block while the receipt holds the admission lock"

    release.set()
    m.join()
    a.join()

    assert m.error is None and m.result.success, (m.error, getattr(m.result, "error", None))
    rows, events = _receipt_state(company)
    assert rows >= 1 and events == 1

    refusal, out = a.result
    assert refusal is not None, "activation must refuse on the committed receipt residue"
    assert "manual_ar_financial_state" in (str(refusal) + out)
    assert _fresh_profile(company) == Company.PilotProfile.NONE
