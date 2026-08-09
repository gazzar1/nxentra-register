# tests/e2e/test_account_state_serialization.py
"""A3-PR2b PostgreSQL concurrency proof — account-state serialization.

The supported guarantee under test, stated exactly:

    For a newly inserted JOURNAL_ENTRY_POSTED event, all account facts used
    by canonical validation come from Account rows serialized against the
    CompanyEventCounter linearization point.

Real two-connection races: every worker runs in its own thread with its own
database connection and explicit ``transaction.atomic`` boundaries.
Synchronization is by ``threading.Event`` plus OBSERVED database state
(``pg_locks`` shows the competitor genuinely blocked) — never by
sleep-for-interleaving. Every wait is a bounded poll on an explicit
condition with a hard watchdog (``pytest.fail`` on expiry) so the suite can
never hang silently; every thread join carries a timeout.

This suite does NOT claim global deadlock elimination — the nine deferred
pre-existing lock pairs are documented in the Lock-Order Normalization
workstream and deliberately have no tests here.
"""

import threading
import time
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import connection, connections, transaction
from django.utils import timezone

from accounting.journal_invariant import (
    JE_ACCOUNT_INACTIVE,
    JE_ACCOUNT_NOT_POSTABLE,
    JE_ACCOUNT_UNKNOWN,
    PostedJournalInvalid,
)
from accounting.models import Account
from accounting.posted_journal_boundary import emit_posted_journal
from events.locks import lock_company_event_counter
from events.models import BusinessEvent, CompanyEventCounter

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="row-lock serialization is only provable on PostgreSQL",
    ),
]

WATCHDOG = 30  # seconds — hard per-wait / per-join ceiling


@pytest.fixture(autouse=True)
def _no_post_commit_projection_stampede(monkeypatch):
    """The on_commit projection re-dispatch (events.emitter
    _schedule_projection_processing) is a REDUNDANT second pass — the
    in-transaction synchronous drain (`_process_projections`, the thing
    whose lock behavior this suite proves) has already run. Under eager
    Celery + threaded workers the post-commit stampede adds tens of seconds
    of unrelated work per committed transaction, so it is disabled here.
    The serialization contract under test is untouched."""
    import events.emitter as emitter

    monkeypatch.setattr(emitter, "_schedule_projection_processing", lambda company_id: None)


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _wait(predicate, why: str, timeout: float = WATCHDOG):
    """Bounded 10ms poll on an explicit observable condition; hard-fails on
    expiry so no test can hang silently (required watchdog semantics)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(f"WATCHDOG: gave up waiting for: {why}")


def _someone_is_lock_waiting() -> bool:
    """True when any backend on this database is waiting on an ungranted
    lock — the observable proof that the competitor genuinely blocked."""
    with connections["default"].cursor() as cur:
        cur.execute(
            """
            SELECT count(*)
            FROM pg_locks l
            JOIN pg_stat_activity a ON a.pid = l.pid
            WHERE NOT l.granted
              AND a.datname = current_database()
            """
        )
        return cur.fetchone()[0] > 0


def _activity_dump() -> str:
    """Visible-failure diagnostics: what every backend on this database was
    doing when a watchdog fired (state, wait event, blocking pids, query)."""
    try:
        with connections["default"].cursor() as cur:
            cur.execute(
                """
                SELECT pid, state, wait_event_type, wait_event,
                       pg_blocking_pids(pid), left(query, 160)
                FROM pg_stat_activity
                WHERE datname = current_database()
                """
            )
            rows = cur.fetchall()
        return "\n".join(str(r) for r in rows)
    except Exception as exc:  # pragma: no cover — diagnostics only
        return f"(activity dump failed: {exc})"


class _Worker:
    """Run `fn` in a thread with its own DB connection; join with watchdog."""

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
            pytest.fail(
                f"WATCHDOG: worker {self.name!r} did not finish within {timeout}s.\n"
                f"Backend activity:\n{_activity_dump()}"
            )
        return self


# --------------------------------------------------------------------------- #
# Fixtures / builders
# --------------------------------------------------------------------------- #


def _account(company, code, *, account_type="ASSET", status="ACTIVE"):
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code=code,
        name=f"A3PR2B {code}",
        account_type=account_type,
        normal_balance=Account.NormalBalance.DEBIT,
        status=status,
    )


def _payload(company, debit_account, credit_account, amount="50.00"):
    return {
        "entry_public_id": str(uuid4()),
        "entry_number": "",
        "date": date.today().isoformat(),
        "memo": "pr2b serialization probe",
        "kind": "NORMAL",
        "period": date.today().month,
        "currency": company.default_currency or "EGP",
        "exchange_rate": "1.0",
        "posted_at": timezone.now().isoformat(),
        "posted_by_id": 0,
        "posted_by_email": "pr2b@example.com",
        "total_debit": amount,
        "total_credit": amount,
        "lines": [
            {"line_no": 1, "account_public_id": str(debit_account.public_id), "debit": amount, "credit": "0"},
            {"line_no": 2, "account_public_id": str(credit_account.public_id), "debit": "0", "credit": amount},
        ],
    }


def _emit(company, payload, key=None):
    return emit_posted_journal(
        company=company,
        data=payload,
        aggregate_type="JournalEntry",
        aggregate_id=payload["entry_public_id"],
        idempotency_key=key or f"pr2b:{uuid4()}",
    )


def _posted_events(company):
    # transaction=True drops the RLS-bypass GUC set by the session fixtures
    # on this connection (known PG-e2e trap) — bookkeeping reads must bypass
    # explicitly or they silently see zero rows.
    from accounts.rls import rls_bypass

    with rls_bypass():
        return list(BusinessEvent.objects.filter(company=company, event_type="journal_entry.posted"))


def _events(company, event_type):
    from accounts.rls import rls_bypass

    with rls_bypass():
        return list(BusinessEvent.objects.filter(company=company, event_type=event_type))


def _events_with_key(company, key):
    from accounts.rls import rls_bypass

    with rls_bypass():
        return list(BusinessEvent.objects.filter(company=company, idempotency_key=key))


def _counter_value(company):
    from accounts.rls import rls_bypass

    with rls_bypass():
        row = CompanyEventCounter.objects.filter(company=company).first()
    return row.last_sequence if row else 0


def _system_actor(company):
    from accounts.authz import system_actor_for_company

    return system_actor_for_company(company)


def _held_counter_writer(company, mutate, acquired: threading.Event, release: threading.Event, *, fail=False):
    """Worker body: acquire the Counter (the serialized-writer protocol),
    run `mutate`, signal, hold until released, then commit (or roll back)."""

    def body():
        try:
            with transaction.atomic():
                lock_company_event_counter(company)
                mutate()
                acquired.set()
                _wait(release.is_set, "release signal for held writer")
                if fail:
                    raise RuntimeError("forced rollback")
        except RuntimeError:
            return "rolled-back"
        return "committed"

    return body


# =============================================================================
# A. Account-state serialization (§11 tests 1-12)
# =============================================================================


def test_01_je_first_gets_lower_sequence_and_mutation_waits(company, owner_membership):
    cash, revenue = _account(company, "1000"), _account(company, "4000", account_type="REVENUE")
    boundary_done = threading.Event()
    release = threading.Event()

    def je_holding():
        with transaction.atomic():
            event = _emit(company, _payload(company, cash, revenue))
            boundary_done.set()
            _wait(release.is_set, "release signal for JE holder")
            return event.company_sequence

    je = _Worker(je_holding, "je").start()
    _wait(boundary_done.is_set, "JE boundary finished validation+insert")

    def deactivate():
        from accounting.commands import update_account

        return update_account(_system_actor(company), cash.id, status="INACTIVE")

    mutator = _Worker(deactivate, "mutator").start()

    # PROOF the mutation genuinely blocks on the Counter while the JE holds it.
    _wait(_someone_is_lock_waiting, "update_account blocked on CompanyEventCounter")
    release.set()
    je.join()
    mutator.join()

    assert je.error is None, je.error
    assert mutator.error is None, mutator.error
    assert mutator.result.success, mutator.result.error
    je_seq = je.result
    (acct_event,) = _events(company, "account.updated")
    assert je_seq < acct_event.company_sequence, "JE linearized first must hold the LOWER company sequence"
    cash.refresh_from_db()
    assert cash.status == Account.Status.INACTIVE


def test_02_deactivation_first_je_rejects_with_no_event_or_sequence(company, owner_membership):
    cash, revenue = _account(company, "1001"), _account(company, "4001", account_type="REVENUE")
    acquired, release = threading.Event(), threading.Event()

    def mutate():
        from accounting.commands import update_account

        result = update_account(_system_actor(company), cash.id, status="INACTIVE")
        assert result.success, result.error

    writer = _Worker(_held_counter_writer(company, mutate, acquired, release), "deactivator").start()
    _wait(acquired.is_set, "deactivation writer holds the Counter")

    def je_attempt():
        with transaction.atomic():
            return _emit(company, _payload(company, cash, revenue))

    je = _Worker(je_attempt, "je").start()
    _wait(_someone_is_lock_waiting, "JE blocked on CompanyEventCounter behind the mutation")
    release.set()
    writer.join()
    je.join()

    assert isinstance(je.error, PostedJournalInvalid), f"expected rejection, got {je.error!r} / {je.result!r}"
    assert JE_ACCOUNT_INACTIVE in je.error.codes
    assert len(_posted_events(company)) == 0
    (acct_event,) = _events(company, "account.updated")
    assert _counter_value(company) == acct_event.company_sequence, "a rejected JE must consume NO sequence"


@pytest.mark.parametrize(
    "test_no, field_updates, expected_codes",
    [
        ("03-header", {"is_header": True}, {JE_ACCOUNT_NOT_POSTABLE}),
        ("04-statistical", {"ledger_domain": "STATISTICAL", "unit_of_measure": "EA"}, None),
        ("05-off-balance", {"ledger_domain": "OFF_BALANCE", "unit_of_measure": "EA"}, None),
    ],
)
def test_03_04_05_nonpostable_and_domain_flips_versus_je(
    company, owner_membership, test_no, field_updates, expected_codes
):
    """Header conversion and ledger-domain flips have no live command today
    (command layer forbids them) — the serialized-writer protocol is
    simulated (Counter → Account row write) to prove the boundary side: a
    JE waiting on the Counter observes the committed mutation and rejects/
    reclassifies. Domain flips memo-reclassify the credit line, so the
    2-line journal loses its financial credit side."""
    cash, revenue = _account(company, f"1{test_no[:2]}0"), _account(company, f"4{test_no[:2]}0", account_type="REVENUE")
    acquired, release = threading.Event(), threading.Event()

    def mutate():
        Account.objects.filter(pk=revenue.pk).update(**field_updates)

    writer = _Worker(_held_counter_writer(company, mutate, acquired, release), "flipper").start()
    _wait(acquired.is_set, "flip writer holds the Counter")

    je = _Worker(lambda: _emit(company, _payload(company, cash, revenue)), "je").start()
    _wait(_someone_is_lock_waiting, "JE blocked behind the flip")
    release.set()
    writer.join()
    je.join()

    assert isinstance(je.error, PostedJournalInvalid), f"{test_no}: expected rejection, got {je.result!r}"
    if expected_codes:
        assert expected_codes & set(je.error.codes)
    else:
        assert {"JE_NO_CREDIT_SIDE", "JE_UNBALANCED", "JE_LINE_COUNT"} & set(je.error.codes)
    assert len(_posted_events(company)) == 0


def test_06_legacy_memo_type_flip_versus_je(company, owner_membership):
    """account_type -> MEMO is a LIVE mutation (update_account allowed
    field): the serialized flip must memo-reclassify the account for the
    waiting JE."""
    cash, revenue = _account(company, "1060"), _account(company, "4060", account_type="REVENUE")
    acquired, release = threading.Event(), threading.Event()

    def mutate():
        from accounting.commands import update_account

        result = update_account(_system_actor(company), revenue.id, account_type="MEMO", unit_of_measure="EA")
        assert result.success, result.error

    writer = _Worker(_held_counter_writer(company, mutate, acquired, release), "memo-flip").start()
    _wait(acquired.is_set, "memo flip holds the Counter")

    je = _Worker(lambda: _emit(company, _payload(company, cash, revenue)), "je").start()
    _wait(_someone_is_lock_waiting, "JE blocked behind memo flip")
    release.set()
    writer.join()
    je.join()

    assert isinstance(je.error, PostedJournalInvalid), f"expected rejection, got {je.result!r}"
    assert {"JE_NO_CREDIT_SIDE", "JE_UNBALANCED", "JE_LINE_COUNT"} & set(je.error.codes)
    assert len(_posted_events(company)) == 0


def test_07_multi_account_je_versus_opposite_order_mutations(company, owner_membership):
    """One JE references [A, B]; two concurrent mutations arrive targeting
    B then A. pk-sorted account locking + Counter-first mutations must all
    complete under the watchdog (no deadlock)."""
    a, b = _account(company, "1070"), _account(company, "1071")
    revenue = _account(company, "4070", account_type="REVENUE")
    payload = _payload(company, a, revenue)
    payload["lines"].insert(1, {"line_no": 9, "account_public_id": str(b.public_id), "debit": "0", "credit": "0"})
    # a zero/zero line is invalid; instead reference B properly: 30 + 20 = 50
    payload["lines"] = [
        {"line_no": 1, "account_public_id": str(a.public_id), "debit": "30.00", "credit": "0"},
        {"line_no": 2, "account_public_id": str(b.public_id), "debit": "20.00", "credit": "0"},
        {"line_no": 3, "account_public_id": str(revenue.public_id), "debit": "0", "credit": "50.00"},
    ]
    start = threading.Barrier(3, timeout=WATCHDOG)

    def je_run():
        start.wait()
        return _emit(company, payload)

    def mutate(account):
        def body():
            from accounting.commands import update_account

            start.wait()
            return update_account(_system_actor(company), account.id, description=f"touched {account.code}")

        return body

    workers = [
        _Worker(je_run, "je").start(),
        _Worker(mutate(b), "mut-b").start(),
        _Worker(mutate(a), "mut-a").start(),
    ]
    for w in workers:
        w.join()
        assert w.error is None, f"{w.name}: {w.error!r}"
    assert len(_posted_events(company)) == 1


def test_08_two_jes_overlapping_accounts_opposite_payload_order(company, owner_membership):
    a, b = _account(company, "1080"), _account(company, "1081")
    revenue = _account(company, "4080", account_type="REVENUE")
    p1 = _payload(company, a, revenue)
    p1["lines"] = [
        {"line_no": 1, "account_public_id": str(a.public_id), "debit": "30.00", "credit": "0"},
        {"line_no": 2, "account_public_id": str(b.public_id), "debit": "20.00", "credit": "0"},
        {"line_no": 3, "account_public_id": str(revenue.public_id), "debit": "0", "credit": "50.00"},
    ]
    p2 = _payload(company, b, revenue)
    p2["lines"] = [
        {"line_no": 1, "account_public_id": str(b.public_id), "debit": "20.00", "credit": "0"},
        {"line_no": 2, "account_public_id": str(a.public_id), "debit": "30.00", "credit": "0"},
        {"line_no": 3, "account_public_id": str(revenue.public_id), "debit": "0", "credit": "50.00"},
    ]
    start = threading.Barrier(2, timeout=WATCHDOG)

    def run(payload):
        def body():
            start.wait()
            return _emit(company, payload)

        return body

    w1, w2 = _Worker(run(p1), "je1").start(), _Worker(run(p2), "je2").start()
    w1.join()
    w2.join()
    assert w1.error is None and w2.error is None, (w1.error, w2.error)
    events = sorted(_posted_events(company), key=lambda e: e.company_sequence)
    assert len(events) == 2
    assert events[0].company_sequence < events[1].company_sequence


def test_09_mutation_rolls_back_waiting_je_sees_unchanged_state(company, owner_membership):
    cash, revenue = _account(company, "1090"), _account(company, "4090", account_type="REVENUE")
    acquired, release = threading.Event(), threading.Event()

    def mutate():
        from accounting.commands import update_account

        result = update_account(_system_actor(company), cash.id, status="INACTIVE")
        assert result.success, result.error

    writer = _Worker(_held_counter_writer(company, mutate, acquired, release, fail=True), "rollback-writer").start()
    _wait(acquired.is_set, "writer holds the Counter")

    je = _Worker(lambda: _emit(company, _payload(company, cash, revenue)), "je").start()
    _wait(_someone_is_lock_waiting, "JE blocked behind the doomed mutation")
    release.set()
    writer.join()
    je.join()

    assert writer.result == "rolled-back"
    assert je.error is None, f"JE must succeed against the UNCHANGED state: {je.error!r}"
    cash.refresh_from_db()
    assert cash.status == Account.Status.ACTIVE
    assert len(_posted_events(company)) == 1
    assert len(_events(company, "account.updated")) == 0


def test_10_je_rolls_back_mutation_proceeds_no_sequence_burned(company, owner_membership):
    cash, revenue = _account(company, "1100"), _account(company, "4100", account_type="REVENUE")
    boundary_done, release = threading.Event(), threading.Event()

    def je_doomed():
        try:
            with transaction.atomic():
                _emit(company, _payload(company, cash, revenue))
                boundary_done.set()
                _wait(release.is_set, "release for doomed JE")
                raise RuntimeError("forced rollback")
        except RuntimeError:
            return "rolled-back"

    je = _Worker(je_doomed, "je-doomed").start()
    _wait(boundary_done.is_set, "JE inserted (uncommitted)")

    def mutate():
        from accounting.commands import update_account

        return update_account(_system_actor(company), cash.id, status="INACTIVE")

    mutator = _Worker(mutate, "mutator").start()
    _wait(_someone_is_lock_waiting, "mutation blocked behind doomed JE")
    release.set()
    je.join()
    mutator.join()

    assert je.result == "rolled-back"
    assert mutator.error is None and mutator.result.success
    assert len(_posted_events(company)) == 0, "rolled-back JE must leave no event"
    (acct_event,) = _events(company, "account.updated")
    assert _counter_value(company) == acct_event.company_sequence, (
        "the rolled-back JE's sequence increment must vanish with it — no gap, no burn"
    )


def test_11_companies_do_not_serialize_each_other(company, second_company, owner_membership):
    cash_a, rev_a = _account(company, "1110"), _account(company, "4110", account_type="REVENUE")
    cash_b, rev_b = _account(second_company, "1110"), _account(second_company, "4110", account_type="REVENUE")
    held, release = threading.Event(), threading.Event()

    def hold_company_a():
        with transaction.atomic():
            _emit(company, _payload(company, cash_a, rev_a))
            held.set()
            _wait(release.is_set, "release for company-A holder")
        return True

    holder = _Worker(hold_company_a, "holder-A").start()
    _wait(held.is_set, "company A Counter held")

    # Company B must complete WHILE A's Counter is still held.
    b = _Worker(lambda: _emit(second_company, _payload(second_company, cash_b, rev_b)), "emit-B").start()
    b.join()
    assert b.error is None, f"company B serialized behind company A: {b.error!r}"
    release.set()
    holder.join()
    assert len(_posted_events(second_company)) == 1


def test_12_first_ever_event_counter_creation_race(company, owner_membership):
    CompanyEventCounter.objects.filter(company=company).delete()
    cash, revenue = _account(company, "1120"), _account(company, "4120", account_type="REVENUE")
    start = threading.Barrier(2, timeout=WATCHDOG)

    def run():
        start.wait()
        return _emit(company, _payload(company, cash, revenue))

    w1, w2 = _Worker(run, "first-1").start(), _Worker(run, "first-2").start()
    w1.join()
    w2.join()
    assert w1.error is None and w2.error is None, (w1.error, w2.error)
    from accounts.rls import rls_bypass

    with rls_bypass():
        assert CompanyEventCounter.objects.filter(company=company).count() == 1
    seqs = sorted(e.company_sequence for e in _posted_events(company))
    assert seqs == [1, 2]


# =============================================================================
# B. Idempotency (§11 tests 13-16)
# =============================================================================


def test_13_pre_lookup_hit_returns_stored_event_without_any_locks(company, owner_membership):
    cash, revenue = _account(company, "1130"), _account(company, "4130", account_type="REVENUE")
    payload = _payload(company, cash, revenue)
    key = f"pr2b.idem:{uuid4()}"
    stored = _emit(company, payload, key=key)

    held, release = threading.Event(), threading.Event()

    def hold_counter():
        with transaction.atomic():
            lock_company_event_counter(company)
            held.set()
            _wait(release.is_set, "release for counter holder")
        return True

    holder = _Worker(hold_counter, "counter-holder").start()
    _wait(held.is_set, "counter held by competitor")

    # Retry must return the stored event IMMEDIATELY, while the company
    # Counter is exclusively held — proof it takes no locks and does not
    # revalidate account state.
    Account.objects.filter(pk=cash.pk).update(status="INACTIVE")
    retry = _Worker(lambda: _emit(company, payload, key=key), "retry").start()
    retry.join(timeout=10)
    assert retry.error is None
    assert retry.result.id == stored.id
    release.set()
    holder.join()
    assert len(_posted_events(company)) == 1


def _recheck_race(company, monkeypatch, competitor_payload, local_payload, key):
    """Shared §11.14/15 shape: local pre-lookup misses; the competitor
    commits the same key on an INDEPENDENT connection; the local payload
    then fails validation; the post-failure recheck returns the stored
    event."""
    import accounting.posted_journal_boundary as boundary

    real = boundary._stored_event
    state = {"first": True}
    stored_holder = {}

    def inject():
        def commit_competitor():
            return _emit(company, competitor_payload, key=key)

        w = _Worker(commit_competitor, "competitor").start()
        w.join()
        assert w.error is None, w.error
        stored_holder["event"] = w.result

    def wrapper(cmp, idem_key):
        result = real(cmp, idem_key)
        if state["first"]:
            state["first"] = False
            if result is None:
                inject()
        return result

    monkeypatch.setattr(boundary, "_stored_event", wrapper)
    returned = _emit(company, local_payload, key=key)
    assert returned.id == stored_holder["event"].id
    assert len(_events_with_key(company, key)) == 1


def test_14_recheck_returns_concurrently_committed_event(company, owner_membership, monkeypatch):
    cash, revenue = _account(company, "1140"), _account(company, "4140", account_type="REVENUE")
    good = _payload(company, cash, revenue)
    bad = _payload(company, cash, revenue)
    bad["lines"][0]["account_public_id"] = str(uuid4())  # unknown → genuinely invalid
    _recheck_race(company, monkeypatch, good, bad, key=f"pr2b.race:{uuid4()}")


def test_15_recheck_same_key_materially_different_payload(company, owner_membership, monkeypatch):
    cash, revenue = _account(company, "1150"), _account(company, "4150", account_type="REVENUE")
    good = _payload(company, cash, revenue, amount="50.00")
    different = _payload(company, cash, revenue, amount="999.00")
    different["lines"][0]["account_public_id"] = str(uuid4())
    _recheck_race(company, monkeypatch, good, different, key=f"pr2b.race:{uuid4()}")


def test_16_no_competitor_original_violations_and_no_sequence(company, owner_membership):
    cash, revenue = _account(company, "1160"), _account(company, "4160", account_type="REVENUE")
    before = _counter_value(company)
    bad = _payload(company, cash, revenue)
    bad["lines"][0]["account_public_id"] = str(uuid4())

    with pytest.raises(PostedJournalInvalid) as excinfo:
        _emit(company, bad)

    assert JE_ACCOUNT_UNKNOWN in excinfo.value.codes
    assert len(_posted_events(company)) == 0
    assert _counter_value(company) == before, "a rejected JE consumes no sequence even though the Counter was held"


# =============================================================================
# C. Touched document ordering (§11 tests 17-24)
# =============================================================================


def _sales_scaffold(company):
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from sales.models import Customer, PostingProfile

    with projection_writes_allowed():
        ar = Account.objects.projection().create(
            company=company,
            code="11499",
            name="PR2B AR Control",
            account_type=Account.AccountType.ASSET,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company,
            code="41099",
            name="PR2B Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
        bank = Account.objects.projection().create(
            company=company,
            code="10199",
            name="PR2B Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
    with command_writes_allowed():
        customer = Customer.objects.create(company=company, code="PR2B-CUST", name="PR2B Customer")
        profile = PostingProfile.objects.create(
            company=company,
            code="PR2B-PROFILE",
            name="PR2B Profile",
            profile_type=PostingProfile.ProfileType.CUSTOMER,
            control_account=ar,
        )
    return customer, profile, ar, revenue, bank


def _posted_platform_invoice(company, customer, profile, revenue, source_document_id, amount="100.00"):
    from sales.commands import create_and_post_invoice_for_platform

    result = create_and_post_invoice_for_platform(
        company=company,
        customer_id=customer.id,
        posting_profile_id=profile.id,
        lines=[
            {
                "account_id": revenue.id,
                "description": "PR2B order",
                "quantity": "1",
                "unit_price": amount,
                "discount_amount": "0",
            }
        ],
        invoice_date=date.today(),
        source="shopify",
        source_document_id=source_document_id,
    )
    assert result.success, result.error
    return result.data["invoice"]


def _receipt(company, customer, ar, bank, invoice, amount):
    from accounting.commands import record_customer_receipt

    return record_customer_receipt(
        _system_actor(company),
        customer_id=customer.id,
        receipt_date=date.today().isoformat(),
        amount=amount,
        bank_account_id=bank.id,
        ar_control_account_id=ar.id,
        allocations=[{"invoice_public_id": str(invoice.public_id), "amount": amount}],
    )


def test_17_18_concurrent_vendor_payments_serialize_on_bill_no_deadlock(company, owner_membership):
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from purchases.models import PurchaseBill, Vendor

    with projection_writes_allowed():
        ap = Account.objects.projection().create(
            company=company,
            code="21099",
            name="PR2B AP Control",
            account_type=Account.AccountType.LIABILITY,
            role=Account.AccountRole.PAYABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        bank = Account.objects.projection().create(
            company=company,
            code="10299",
            name="PR2B Bank AP",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
    from sales.models import PostingProfile

    with command_writes_allowed():
        vendor = Vendor.objects.create(company=company, code="PR2B-VEND", name="PR2B Vendor")
        ap_profile = PostingProfile.objects.create(
            company=company,
            code="PR2B-AP-PROFILE",
            name="PR2B AP Profile",
            profile_type=PostingProfile.ProfileType.VENDOR,
            control_account=ap,
        )
        bill = PurchaseBill.objects.create(
            company=company,
            vendor=vendor,
            posting_profile=ap_profile,
            bill_number="PR2B-BILL-1",
            bill_date=date.today(),
            status=PurchaseBill.Status.POSTED,
            subtotal=Decimal("100.00"),
            total_amount=Decimal("100.00"),
            amount_paid=Decimal("0"),
        )

    from accounting.commands import record_vendor_payment

    start = threading.Barrier(2, timeout=WATCHDOG)

    def pay():
        start.wait()
        return record_vendor_payment(
            _system_actor(company),
            vendor_id=vendor.id,
            payment_date=date.today().isoformat(),
            amount="60.00",
            bank_account_id=bank.id,
            ap_control_account_id=ap.id,
            allocations=[{"bill_reference": "PR2B-BILL-1", "amount": "60.00"}],
        )

    w1, w2 = _Worker(pay, "pay1").start(), _Worker(pay, "pay2").start()
    w1.join()
    w2.join()
    assert w1.error is None and w2.error is None, (w1.error, w2.error)
    outcomes = sorted([w1.result.success, w2.result.success])
    assert outcomes == [False, True], "exactly one payment must win; the loser sees the serialized outstanding"
    loser = w1.result if not w1.result.success else w2.result
    assert "exceeds outstanding" in loser.error
    bill.refresh_from_db()
    assert bill.amount_paid == Decimal("60.00"), "no over-application under concurrency"


def test_19_20_concurrent_receipts_serialize_on_invoice(company, owner_membership):
    customer, profile, ar, revenue, bank = _sales_scaffold(company)
    invoice = _posted_platform_invoice(company, customer, profile, revenue, "PR2B-ORDER-19")
    start = threading.Barrier(2, timeout=WATCHDOG)

    def receipt():
        start.wait()
        return _receipt(company, customer, ar, bank, invoice, "60.00")

    w1, w2 = _Worker(receipt, "rcpt1").start(), _Worker(receipt, "rcpt2").start()
    w1.join()
    w2.join()
    assert w1.error is None and w2.error is None, (w1.error, w2.error)
    outcomes = sorted([w1.result.success, w2.result.success])
    assert outcomes == [False, True], "exactly one receipt must win on a 100.00 invoice with 60.00 allocations"
    loser = w1.result if not w1.result.success else w2.result
    assert "exceeds" in loser.error
    invoice.refresh_from_db()
    assert invoice.amount_paid == Decimal("60.00"), "no over-application under concurrency"


def test_21_credit_note_posting_versus_receipt_no_deadlock(company, owner_membership, actor_context):
    from sales.commands import create_credit_note, post_credit_note

    customer, profile, ar, revenue, bank = _sales_scaffold(company)
    invoice = _posted_platform_invoice(company, customer, profile, revenue, "PR2B-ORDER-21")
    created = create_credit_note(
        actor=actor_context,
        invoice_id=invoice.id,
        lines=[
            {
                "account_id": revenue.id,
                "description": "PR2B CN line",
                "quantity": "1",
                "unit_price": "30.00",
                "discount_amount": "0",
            }
        ],
        credit_note_date=date.today(),
        reason="RETURN",
    )
    assert created.success, created.error
    cn = created.data["credit_note"]

    start = threading.Barrier(2, timeout=WATCHDOG)

    def post_cn():
        start.wait()
        return post_credit_note(actor_context, cn.id)

    def receipt():
        start.wait()
        return _receipt(company, customer, ar, bank, invoice, "40.00")

    w1, w2 = _Worker(post_cn, "cn").start(), _Worker(receipt, "receipt").start()
    w1.join()
    w2.join()
    assert w1.error is None and w2.error is None, (w1.error, w2.error)
    assert w1.result.success, w1.result.error
    assert w2.result.success, w2.result.error
    invoice.refresh_from_db()
    assert invoice.amount_paid == Decimal("70.00"), "CN 30 + receipt 40 must both apply exactly once"


def test_22_23_draft_documents_fail_visibly_while_counter_held(company, owner_membership, actor_context):
    """The §9.4 contract under REAL lock pressure: with the company Counter
    exclusively held by a competitor, the platform wrappers must return the
    visible DRAFT failure IMMEDIATELY — proof they acquire neither the
    Counter nor the document row."""
    from sales.commands import (
        create_and_post_credit_note_for_platform,
        create_and_post_invoice_for_platform,
        create_credit_note,
        create_sales_invoice,
    )

    customer, profile, _ar, revenue, _bank = _sales_scaffold(company)
    posted = _posted_platform_invoice(company, customer, profile, revenue, "PR2B-ORDER-22")
    draft_inv = create_sales_invoice(
        actor=actor_context,
        customer_id=customer.id,
        posting_profile_id=profile.id,
        lines=[
            {
                "account_id": revenue.id,
                "description": "PR2B stranded",
                "quantity": "1",
                "unit_price": "10.00",
                "discount_amount": "0",
            }
        ],
        invoice_date=date.today(),
        source="shopify",
        source_document_id="PR2B-DRAFT-22",
    )
    assert draft_inv.success, draft_inv.error
    draft_cn = create_credit_note(
        actor=actor_context,
        invoice_id=posted.id,
        lines=[
            {
                "account_id": revenue.id,
                "description": "PR2B stranded CN",
                "quantity": "1",
                "unit_price": "5.00",
                "discount_amount": "0",
            }
        ],
        credit_note_date=date.today(),
        reason="RETURN",
        source="shopify",
        source_document_id="PR2B-DRAFT-23",
    )
    assert draft_cn.success, draft_cn.error

    held, release = threading.Event(), threading.Event()

    def hold_counter():
        with transaction.atomic():
            lock_company_event_counter(company)
            held.set()
            _wait(release.is_set, "release for counter holder")
        return True

    holder = _Worker(hold_counter, "holder").start()
    _wait(held.is_set, "counter held")

    def wrapper_calls():
        inv = create_and_post_invoice_for_platform(
            company=company,
            customer_id=customer.id,
            posting_profile_id=profile.id,
            lines=[
                {
                    "account_id": revenue.id,
                    "description": "retry",
                    "quantity": "1",
                    "unit_price": "10.00",
                    "discount_amount": "0",
                }
            ],
            invoice_date=date.today(),
            source="shopify",
            source_document_id="PR2B-DRAFT-22",
        )
        cn = create_and_post_credit_note_for_platform(
            company=company,
            invoice_id=posted.id,
            lines=[
                {
                    "account_id": revenue.id,
                    "description": "retry",
                    "quantity": "1",
                    "unit_price": "5.00",
                    "discount_amount": "0",
                }
            ],
            credit_note_date=date.today(),
            source="shopify",
            source_document_id="PR2B-DRAFT-23",
        )
        return inv, cn

    caller = _Worker(wrapper_calls, "wrappers").start()
    caller.join(timeout=10)  # must NOT block behind the held Counter
    assert caller.error is None, caller.error
    inv_result, cn_result = caller.result
    assert not inv_result.success and "DRAFT" in inv_result.error
    assert not cn_result.success and "DRAFT" in cn_result.error
    release.set()
    holder.join()


def test_24_fresh_platform_documents_post_inside_counter_owning_transaction(company, owner_membership):
    """The supported projection shape: a source transaction that already
    holds the Counter (first emit done) composes the platform wrappers for
    FRESH documents — reentrant Counter, new rows only, fully successful."""
    from sales.commands import create_and_post_credit_note_for_platform, create_and_post_invoice_for_platform
    from sales.models import SalesCreditNote, SalesInvoice

    customer, profile, _ar, revenue, _bank = _sales_scaffold(company)

    def source_transaction():
        with transaction.atomic():
            lock_company_event_counter(company)  # the source event's hold
            inv = create_and_post_invoice_for_platform(
                company=company,
                customer_id=customer.id,
                posting_profile_id=profile.id,
                lines=[
                    {
                        "account_id": revenue.id,
                        "description": "fresh order",
                        "quantity": "1",
                        "unit_price": "80.00",
                        "discount_amount": "0",
                    }
                ],
                invoice_date=date.today(),
                source="shopify",
                source_document_id="PR2B-FRESH-24",
            )
            assert inv.success, inv.error
            cn = create_and_post_credit_note_for_platform(
                company=company,
                invoice_id=inv.data["invoice"].id,
                lines=[
                    {
                        "account_id": revenue.id,
                        "description": "fresh refund",
                        "quantity": "1",
                        "unit_price": "20.00",
                        "discount_amount": "0",
                    }
                ],
                credit_note_date=date.today(),
                source="shopify",
                source_document_id="PR2B-FRESH-24-CN",
            )
            assert cn.success, cn.error
        return True

    worker = _Worker(source_transaction, "source-txn").start()
    worker.join()
    assert worker.error is None, worker.error
    assert SalesInvoice.objects.get(company=company, source_document_id="PR2B-FRESH-24").status == "POSTED"
    assert SalesCreditNote.objects.get(company=company, source_document_id="PR2B-FRESH-24-CN").status == "POSTED"
