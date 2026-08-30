# tests/e2e/test_a4_matched_line_exclusion_serialization.py
"""A4 matched-line exclusion gate — PostgreSQL two-connection ordering proofs.

Same discipline as ``test_a4_runtime_admission_serialization.py``: the REAL
production path acquires the locks; monkeypatch pause points sit INSIDE the
command body after admission; a test never manufactures a lock.

Three proofs for the conditional ``UNSAFE_BANK_MATCH`` gate on ``exclude_line``:

1. ACTIVATION FIRST — the real activation holds its Company FOR UPDATE; a
   matched-line exclusion observably waits on the admission row, then reads the
   ACTIVE profile and refuses with zero side effects.
2. EXCLUDE FIRST — on profile NONE the admitted exclusion owns the Company
   admission lock through its commit; activation observably waits; no exclusion
   mutation commits after the activation boundary (the serial ordering is the
   assertion; the subsequent activation's own verdict is reported as observed).
3. CONCURRENT MANUAL MATCH vs EXCLUDE — manual_match wins the BankStatementLine
   row lock first; exclude waits on that row, then classifies from the freshly
   locked (now matched) state and refuses under the pilot — a stale UNMATCHED
   observation can never bypass the gate.
"""

import threading
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command
from django.db import connections

from accounting.models import Account, BankStatementLine, JournalEntry
from accounts.models import Company
from accounts.pilot_policy import PilotScopeBlocked

pytestmark = [pytest.mark.django_db(transaction=True)]

WATCHDOG = 30
ISO = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1


# --------------------------------------------------------------------------- #
# Harness (same discipline as test_a4_runtime_admission_serialization.py)
# --------------------------------------------------------------------------- #


def _wait(predicate, why: str, timeout: float = WATCHDOG):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(f"WATCHDOG: gave up waiting for: {why}")


def _someone_is_lock_waiting() -> bool:
    with connections["default"].cursor() as cur:
        cur.execute(
            """
            SELECT count(*)
            FROM pg_locks l
            JOIN pg_stat_activity a ON a.pid = l.pid
            WHERE NOT l.granted AND a.datname = current_database()
            """
        )
        return cur.fetchone()[0] > 0


class _Worker:
    def __init__(self, fn, name: str):
        self.result = None
        self.error = None
        self.name = name

        def target():
            try:
                self.result = fn()
            except BaseException as exc:
                self.error = exc
            finally:
                connections.close_all()

        self.thread = threading.Thread(target=target, name=name, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def join(self, timeout: float = WATCHDOG):
        self.thread.join(timeout)
        if self.thread.is_alive():
            pytest.fail(f"WATCHDOG: worker {self.name!r} did not finish within {timeout}s")
        return self


def _actor(company):
    from accounts.authz import system_actor_for_company

    return system_actor_for_company(company)


def _activation_clean(company):
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.fiscal_year_start_month = 1
    company.save()
    from projections.models import FiscalPeriodConfig

    FiscalPeriodConfig.objects.get_or_create(company=company, fiscal_year=2026, defaults={"period_count": 13})
    return company


def _fresh_profile(company):
    from accounts.rls import rls_bypass

    with rls_bypass():
        return Company.objects.get(pk=company.pk).pilot_profile


def _run_activation(company_id):
    import io

    buf = io.StringIO()
    try:
        call_command("activate_pilot_profile", "--company", str(company_id), "--yes", stdout=buf)
        return None, buf.getvalue()
    except CommandError as exc:
        return exc, buf.getvalue()


def _pause_hook(monkeypatch, module, attr, admitted: threading.Event, release: threading.Event):
    """Wrap ``module.attr`` so the ORIGINAL runs first, then the FIRST call
    pauses (signal ``admitted``, wait for ``release``) — inside the production
    transaction while the production-acquired locks are held."""
    original = getattr(module, attr)
    fired = {"n": 0}

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        fired["n"] += 1
        if fired["n"] == 1:
            admitted.set()
            _wait(release.is_set, f"release of paused {attr}")
        return result

    monkeypatch.setattr(module, attr, wrapper)


def _preflight_pause_hook(monkeypatch, locked: threading.Event, proceed: threading.Event):
    from accounts import pilot_preflight

    original = pilot_preflight.run_preflight

    def wrapper(*args, **kwargs):
        locked.set()
        _wait(proceed.is_set, "activation released to run its preflight")
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot_preflight, "run_preflight", wrapper)


# --------------------------------------------------------------------------- #
# Seeders — a manually matched EGP bank line (pre-activation setup history)
# --------------------------------------------------------------------------- #


def _seed_manual_matched_line(company):
    """Posted manual JE + EGP bank statement, flag-flip manual match."""
    from accounting.bank_reconciliation import import_bank_statement
    from accounting.commands import (
        create_manual_journal_entry,
        post_manual_journal_entry,
        save_manual_journal_entry_complete,
    )
    from projections.write_barrier import projection_writes_allowed
    from reconciliation.commands import manual_match

    actor = _actor(company)
    with projection_writes_allowed():
        bank = Account.objects.projection().create(
            company=company,
            code="10197",
            name="Adm Bank — EGP",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        income = Account.objects.projection().create(
            company=company,
            code="40997",
            name="Adm Income",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )

    lines = [
        {"account_id": bank.id, "debit": Decimal("300.00"), "credit": Decimal("0")},
        {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("300.00")},
    ]
    created = create_manual_journal_entry(actor, date(2026, 4, 25), memo="setup deposit", lines=lines)
    assert created.success, created.error
    entry = created.data
    assert save_manual_journal_entry_complete(actor, entry.id, lines=lines).success
    assert post_manual_journal_entry(actor, entry.id).success
    bank_jl = entry.lines.get(account=bank)

    line_date = date(2026, 4, 26)
    imported = import_bank_statement(
        actor=actor,
        account_id=bank.id,
        statement_date=line_date,
        period_start=line_date - timedelta(days=2),
        period_end=line_date + timedelta(days=2),
        opening_balance=Decimal("0"),
        closing_balance=Decimal("300.00"),
        lines_data=[
            {
                "line_date": line_date.isoformat(),
                "value_date": line_date.isoformat(),
                "amount": "300.00",
                "description": "deposit",
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="EGP",
    )
    assert imported.success, imported.error
    line = imported.data["statement"].lines.get()

    matched = manual_match(actor, line.id, bank_jl.id)
    assert matched.success, matched.error
    line.refresh_from_db()
    assert line.match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED
    return line, bank_jl


def _run_exclude(company, line_id):
    from reconciliation.commands import exclude_line

    return exclude_line(_actor(company), line_id)


def _line_state(line_id):
    from accounts.rls import rls_bypass

    with rls_bypass():
        line = BankStatementLine.objects.get(id=line_id)
        return (line.match_status, line.matched_journal_line_id)


def _reversal_count(company):
    from accounts.rls import rls_bypass

    with rls_bypass():
        return JournalEntry.objects.filter(company=company, kind=JournalEntry.Kind.REVERSAL).count()


# =============================================================================
# 1. ACTIVATION FIRST — exclusion waits on the admission row, then refuses
# =============================================================================


def test_activation_first_matched_exclusion_refuses(company, owner_membership, monkeypatch):
    _activation_clean(company)
    line, _bank_jl = _seed_manual_matched_line(company)
    before = _line_state(line.id)

    locked, proceed = threading.Event(), threading.Event()
    _preflight_pause_hook(monkeypatch, locked, proceed)

    a = _Worker(lambda: _run_activation(company.id), "activation").start()
    _wait(locked.is_set, "activation holds its production Company FOR UPDATE")

    m = _Worker(lambda: _run_exclude(company, line.id), "exclude").start()
    _wait(_someone_is_lock_waiting, "exclude observably waiting on activation's Company lock")

    proceed.set()
    a.join()
    m.join()

    assert a.error is None, f"activation worker crashed: {a.error}"
    refusal, _out = a.result
    assert refusal is None, f"activation must succeed on the setup-history state: {refusal}"
    assert _fresh_profile(company) == ISO

    assert isinstance(m.error, PilotScopeBlocked), f"expected PilotScopeBlocked, got {m.error or m.result}"
    assert m.error.capability == "unsafe_bank_match"
    # Zero side effects: match intact, no reversal journal.
    assert _line_state(line.id) == before
    assert _reversal_count(company) == 0


# =============================================================================
# 2. EXCLUDE FIRST — the admitted exclusion owns admission through commit
# =============================================================================


def test_exclude_first_activation_waits_serial_ordering(company, owner_membership, monkeypatch):
    import reconciliation.commands as recon_commands

    _activation_clean(company)
    line, _bank_jl = _seed_manual_matched_line(company)

    admitted, release = threading.Event(), threading.Event()
    # First call inside the body AFTER the admission lock is held.
    _pause_hook(monkeypatch, recon_commands, "_run_reconciliation_projection_sync", admitted, release)

    m = _Worker(lambda: _run_exclude(company, line.id), "exclude").start()
    _wait(admitted.is_set, "exclusion admitted (production admission lock held) and paused in-body")

    a = _Worker(lambda: _run_activation(company.id), "activation").start()
    _wait(_someone_is_lock_waiting, "activation observably waiting on the Company admission row")
    assert a.thread.is_alive(), "activation must block while the exclusion holds the admission lock"

    release.set()
    m.join()
    a.join()

    # Profile NONE: the matched exclusion itself completes (pre-existing
    # behavior) and its mutation committed BEFORE activation's decision point.
    assert m.error is None and m.result.success, (m.error, getattr(m.result, "error", None))
    assert _line_state(line.id)[0] == BankStatementLine.MatchStatus.EXCLUDED

    # Serial ordering is the assertion; the activation's own verdict on the
    # now-EXCLUDED state is reported as observed, not prescribed.
    assert a.error is None, f"activation worker crashed: {a.error}"
    refusal, _out = a.result
    observed_profile = _fresh_profile(company)
    if refusal is None:
        assert observed_profile == ISO
    else:
        assert observed_profile == Company.PilotProfile.NONE
    # Either way: no exclusion mutation committed after the activation
    # boundary — the line's terminal state was durable before activation ran.
    assert _line_state(line.id)[0] == BankStatementLine.MatchStatus.EXCLUDED


# =============================================================================
# 3. CONCURRENT MANUAL MATCH vs EXCLUDE — the row lock defeats the stale read
# =============================================================================


def test_concurrent_manual_match_then_exclude_refuses(company, owner_membership, monkeypatch):
    """manual_match wins the BankStatementLine lock; exclude waits on the row,
    re-reads the committed matched state, and refuses under the pilot — its
    earlier UNMATCHED observation can never bypass the conditional gate."""
    import reconciliation.commands as recon_commands

    _activation_clean(company)
    # Build the matched fixture, then unmatch is NOT available under the pilot —
    # so construct the pre-race state directly: a fresh UNMATCHED line plus a
    # posted JE target (a second statement against the same JE bank line would
    # double-book; instead seed a brand-new pair).
    from accounting.bank_reconciliation import import_bank_statement
    from accounting.commands import (
        create_manual_journal_entry,
        post_manual_journal_entry,
        save_manual_journal_entry_complete,
    )
    from projections.write_barrier import projection_writes_allowed
    from reconciliation.commands import manual_match

    actor = _actor(company)
    with projection_writes_allowed():
        bank = Account.objects.projection().create(
            company=company,
            code="10198",
            name="Race Bank — EGP",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        income = Account.objects.projection().create(
            company=company,
            code="40998",
            name="Race Income",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    lines = [
        {"account_id": bank.id, "debit": Decimal("120.00"), "credit": Decimal("0")},
        {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("120.00")},
    ]
    created = create_manual_journal_entry(actor, date(2026, 4, 25), memo="race target", lines=lines)
    assert created.success, created.error
    entry = created.data
    assert save_manual_journal_entry_complete(actor, entry.id, lines=lines).success
    assert post_manual_journal_entry(actor, entry.id).success
    bank_jl = entry.lines.get(account=bank)

    line_date = date(2026, 4, 26)
    imported = import_bank_statement(
        actor=actor,
        account_id=bank.id,
        statement_date=line_date,
        period_start=line_date - timedelta(days=2),
        period_end=line_date + timedelta(days=2),
        opening_balance=Decimal("0"),
        closing_balance=Decimal("120.00"),
        lines_data=[
            {
                "line_date": line_date.isoformat(),
                "value_date": line_date.isoformat(),
                "amount": "120.00",
                "description": "race deposit",
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="EGP",
    )
    assert imported.success, imported.error
    line = imported.data["statement"].lines.get()
    assert line.match_status == BankStatementLine.MatchStatus.UNMATCHED

    # Activate the pilot (the race is between the two commands, not activation;
    # the durable activation row mirrors activate_pilot_profile's write).
    company.pilot_profile = ISO
    company.save(update_fields=["pilot_profile"])
    from accounts.models import PilotProfileActivation

    PilotProfileActivation.objects.get_or_create(company=company, defaults={"profile": str(ISO)})

    admitted, release = threading.Event(), threading.Event()
    # _is_settlement_ebd_pick runs in manual_match AFTER its BankStatementLine
    # select_for_update — pausing there holds the production row lock.
    _pause_hook(monkeypatch, recon_commands, "_is_settlement_ebd_pick", admitted, release)

    matcher = _Worker(lambda: manual_match(_actor(company), line.id, bank_jl.id), "manual_match").start()
    _wait(admitted.is_set, "manual_match holds the BankStatementLine row lock")

    excluder = _Worker(lambda: _run_exclude(company, line.id), "exclude").start()
    _wait(_someone_is_lock_waiting, "exclude observably waiting on the locked BankStatementLine row")

    release.set()
    matcher.join()
    excluder.join()

    assert matcher.error is None and matcher.result.success, (
        matcher.error,
        getattr(matcher.result, "error", None),
    )
    assert isinstance(excluder.error, PilotScopeBlocked), (
        f"expected PilotScopeBlocked from the re-read matched state, got {excluder.error or excluder.result}"
    )
    assert excluder.error.capability == "unsafe_bank_match"

    # The committed manual match survives untouched; nothing was reversed.
    status, matched_jl_id = _line_state(line.id)
    assert status == BankStatementLine.MatchStatus.MANUAL_MATCHED
    assert matched_jl_id == bank_jl.id
    assert _reversal_count(company) == 0
