# tests/test_a3_corpus_scanner.py
"""Tests for the read-only posted-JE corpus scanner (A3-PR1) and the ORM
helper load_account_facts. Database-enabled — the pure-invariant tests live in
test_a3_journal_invariant.py with no database access at all."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command


def _mk_account(company, code):
    from accounting.models import Account

    return Account.objects.create(company=company, code=code, name=f"A{code}", account_type="ASSET", status="ACTIVE")


def _mk_posted_event(company, lines, total_debit, total_credit, memo="scan-test", seq_hint=""):
    """Create a stored JOURNAL_ENTRY_POSTED BusinessEvent directly (the
    repository's established adversarial pattern — bypasses the emitter)."""
    from uuid import uuid4

    from events.models import BusinessEvent
    from events.types import EventTypes

    return BusinessEvent.objects.create(
        company=company,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=str(uuid4())[:36],
        idempotency_key=f"a3scan:{uuid4()}{seq_hint}",
        data={
            "entry_public_id": str(uuid4()),
            "entry_number": "JE-SCAN",
            "date": "2026-01-15",
            "memo": memo,
            "kind": "NORMAL",
            "posted_at": "2026-01-15T10:00:00",
            "posted_by_id": 1,
            "posted_by_email": "owner@test.com",
            "total_debit": total_debit,
            "total_credit": total_credit,
            "lines": lines,
        },
    )


def _line(line_no, account, debit="0", credit="0"):
    return {
        "line_no": line_no,
        "account_public_id": str(account.public_id),
        "account_code": account.code,
        "description": "line",
        "debit": debit,
        "credit": credit,
    }


def _valid_event(company, a1, a2):
    return _mk_posted_event(
        company,
        [_line(1, a1, debit="100.00"), _line(2, a2, credit="100.00")],
        "100.00",
        "100.00",
    )


def _unbalanced_event(company, a1, a2, memo="secret-memo-must-not-leak"):
    return _mk_posted_event(
        company,
        [_line(1, a1, debit="100.00"), _line(2, a2, credit="99.00")],
        "100.00",
        "99.00",
        memo=memo,
    )


def _run(*args):
    out = StringIO()
    call_command("audit_posted_journal_corpus", *args, stdout=out)
    return out.getvalue()


def _run_json(*args):
    return json.loads(_run("--json", *args))


# --------------------------------------------------------------------------- #
# load_account_facts (the ORM helper)
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_load_account_facts_reports_status_and_postability(company):
    from accounting.journal_invariant import load_account_facts
    from accounting.models import Account

    active = _mk_account(company, "1000")
    header = Account.objects.create(
        company=company, code="1", name="Assets", account_type="ASSET", status="ACTIVE", is_header=True
    )
    inactive = Account.objects.create(company=company, code="1001", name="Old", account_type="ASSET", status="INACTIVE")
    facts = load_account_facts(company, [active.public_id, header.public_id, inactive.public_id, None, ""])
    assert facts[str(active.public_id)].is_postable is True
    assert facts[str(header.public_id)].is_active is True
    assert facts[str(header.public_id)].is_postable is False
    assert facts[str(inactive.public_id)].is_active is False
    assert load_account_facts(company, []) == {}


# --------------------------------------------------------------------------- #
# scanner behavior
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_clean_corpus_passes_and_strict_exits_zero(company):
    a1, a2 = _mk_account(company, "1000"), _mk_account(company, "4000")
    _valid_event(company, a1, a2)
    out = _run("--strict")  # must NOT raise SystemExit
    assert "PASS" in out
    data = _run_json()
    assert data["total_violating_events"] == 0
    assert data["total_events_scanned"] >= 1


@pytest.mark.django_db
def test_violations_reported_and_strict_exits_nonzero(company):
    a1, a2 = _mk_account(company, "1000"), _mk_account(company, "4000")
    _unbalanced_event(company, a1, a2)
    out = _run()  # non-strict: reports, exits 0
    assert "JE_UNBALANCED" in out and "FAIL" in out
    with pytest.raises(SystemExit) as exc:
        _run("--strict")
    assert exc.value.code == 1


@pytest.mark.django_db
def test_multiple_companies_grouped_independently(company, second_company):
    a1, a2 = _mk_account(company, "1000"), _mk_account(company, "4000")
    b1, b2 = _mk_account(second_company, "1000"), _mk_account(second_company, "4000")
    _valid_event(company, a1, a2)
    _unbalanced_event(second_company, b1, b2)
    data = _run_json()
    by_id = {c["company_id"]: c for c in data["companies"]}
    assert by_id[company.id]["findings"] == []
    assert len(by_id[second_company.id]["findings"]) == 1
    assert by_id[second_company.id]["violation_counts"] == {"JE_UNBALANCED": 1}


@pytest.mark.django_db
def test_company_filter(company, second_company):
    a1, a2 = _mk_account(company, "1000"), _mk_account(company, "4000")
    b1, b2 = _mk_account(second_company, "1000"), _mk_account(second_company, "4000")
    _valid_event(company, a1, a2)
    _unbalanced_event(second_company, b1, b2)
    data = _run_json("--company", str(company.id))
    assert [c["company_id"] for c in data["companies"]] == [company.id]
    assert data["total_violating_events"] == 0
    # Unknown company id is a command error, not a silent empty pass.
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        _run("--company", "999999")


@pytest.mark.django_db
def test_deterministic_json(company, second_company):
    a1, a2 = _mk_account(company, "1000"), _mk_account(company, "4000")
    _unbalanced_event(company, a1, a2)
    _valid_event(company, a1, a2)
    first = _run("--json")
    second = _run("--json")
    assert first == second


@pytest.mark.django_db
def test_unreadable_payload_is_visible_not_skipped(company):
    from uuid import uuid4

    from events.models import BusinessEvent
    from events.types import EventTypes

    # A non-dict payload: structurally storable, canonically unevaluable.
    BusinessEvent.objects.create(
        company=company,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id="unreadable-1",
        idempotency_key=f"a3scan:{uuid4()}",
        data=["not", "a", "dict"],
    )
    data = _run_json()
    entry = next(c for c in data["companies"] if c["company_id"] == company.id)
    assert entry["violation_counts"] == {"SCANNER_UNREADABLE_PAYLOAD": 1}
    with pytest.raises(SystemExit):
        _run("--strict")


@pytest.mark.django_db
def test_scanner_mutates_nothing(company):
    from events.models import BusinessEvent
    from projections.models import ProjectionAppliedEvent, ProjectionFailureLog

    a1, a2 = _mk_account(company, "1000"), _mk_account(company, "4000")
    _unbalanced_event(company, a1, a2)
    before = (
        BusinessEvent.objects.count(),
        ProjectionAppliedEvent.objects.count(),
        ProjectionFailureLog.objects.count(),
    )
    _run()
    after = (
        BusinessEvent.objects.count(),
        ProjectionAppliedEvent.objects.count(),
        ProjectionFailureLog.objects.count(),
    )
    assert before == after


@pytest.mark.django_db
def test_no_pii_or_payload_bodies_in_output(company):
    a1, a2 = _mk_account(company, "1000"), _mk_account(company, "4000")
    _unbalanced_event(company, a1, a2, memo="secret-memo-must-not-leak")
    human = _run()
    machine = _run("--json")
    for output in (human, machine):
        assert "secret-memo-must-not-leak" not in output
        assert "owner@test.com" not in output
        assert "100.00" not in output  # no amounts either


@pytest.mark.django_db
def test_apply_mode_ignores_later_account_deactivation(company):
    """Scanner uses apply mode: an account deactivated AFTER posting must not
    flag historical events (D1/D3 evidence purity)."""
    from accounting.models import Account

    a1, a2 = _mk_account(company, "1000"), _mk_account(company, "4000")
    _valid_event(company, a1, a2)
    Account.objects.filter(pk=a1.pk).update(status="INACTIVE")
    data = _run_json("--company", str(company.id))
    assert data["total_violating_events"] == 0


@pytest.mark.django_db
def test_malformed_account_id_is_unknown_and_scan_continues(company):
    """A readable payload with a malformed account_public_id must classify as
    JE_ACCOUNT_UNKNOWN (never SCANNER_UNREADABLE_PAYLOAD, never a crash), and
    later events in the corpus must still be evaluated."""
    a1, a2 = _mk_account(company, "1000"), _mk_account(company, "4000")
    bad_lines = [
        {
            "line_no": 1,
            "account_public_id": "not-a-uuid",
            "account_code": "9999",
            "description": "line",
            "debit": "100.00",
            "credit": "0",
        },
        _line(2, a2, credit="100.00"),
    ]
    _mk_posted_event(company, bad_lines, "100.00", "100.00")
    _unbalanced_event(company, a1, a2)  # later event — must still be scanned
    data = _run_json("--company", str(company.id))
    entry = data["companies"][0]
    assert entry["events_scanned"] == 2
    assert entry["violation_counts"].get("JE_ACCOUNT_UNKNOWN") == 1
    assert entry["violation_counts"].get("JE_UNBALANCED") == 1
    assert "SCANNER_UNREADABLE_PAYLOAD" not in entry["violation_counts"]


@pytest.mark.django_db
def test_mixed_valid_and_malformed_ids_in_one_event(company):
    a2 = _mk_account(company, "4000")
    lines = [
        {
            "line_no": 1,
            "account_public_id": "also-not-a-uuid",
            "account_code": "9998",
            "description": "line",
            "debit": "50.00",
            "credit": "0",
        },
        _line(2, a2, debit="50.00"),
        {
            "line_no": 3,
            "account_public_id": str(a2.public_id).upper(),  # equivalent UUID string
            "account_code": a2.code,
            "description": "line",
            "debit": "0",
            "credit": "100.00",
        },
    ]
    _mk_posted_event(company, lines, "100.00", "100.00")
    data = _run_json("--company", str(company.id))
    entry = data["companies"][0]
    # Only the malformed id is unknown; the valid + uppercase-equivalent ids resolve.
    assert entry["violation_counts"] == {"JE_ACCOUNT_UNKNOWN": 1}


@pytest.mark.django_db
def test_load_account_facts_sanitizes_malformed_ids(company):
    """No invalid UUID value may reach the ORM filter: malformed input returns
    an empty/partial mapping instead of raising, and UUID objects work."""
    from accounting.journal_invariant import load_account_facts

    a1 = _mk_account(company, "1000")
    # Pure-malformed input: no crash, empty result.
    assert load_account_facts(company, ["not-a-uuid", "", None]) == {}
    # Mixed input: valid id resolves (as UUID object AND string), malformed skipped.
    facts = load_account_facts(company, [a1.public_id, "not-a-uuid", str(a1.public_id).upper()])
    assert list(facts.keys()) == [str(a1.public_id)]


@pytest.mark.django_db
def test_non_list_lines_event_reported_and_scan_continues(company):
    """{"lines": 1} is a readable dict payload with a structural defect: it
    must classify as JE_AMOUNT_INVALID (not SCANNER_UNREADABLE_PAYLOAD, not a
    TypeError aborting the scan), and later events must still be evaluated."""
    a1, a2 = _mk_account(company, "1000"), _mk_account(company, "4000")
    from uuid import uuid4

    from events.models import BusinessEvent
    from events.types import EventTypes

    BusinessEvent.objects.create(
        company=company,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id="nonlist-1",
        idempotency_key=f"a3scan:{uuid4()}",
        data={
            "entry_public_id": str(uuid4()),
            "entry_number": "JE-NONLIST",
            "date": "2026-01-15",
            "memo": "container",
            "kind": "NORMAL",
            "posted_at": "2026-01-15T10:00:00",
            "posted_by_id": 1,
            "posted_by_email": "owner@test.com",
            "total_debit": "1.00",
            "total_credit": "1.00",
            "lines": 1,
        },
    )
    _valid_event(company, a1, a2)  # later valid event — must still be scanned
    data = _run_json("--company", str(company.id))
    entry = data["companies"][0]
    assert entry["events_scanned"] == 2
    assert entry["violation_counts"].get("JE_AMOUNT_INVALID") == 1
    assert "SCANNER_UNREADABLE_PAYLOAD" not in entry["violation_counts"]
    # Default mode reports and exits 0; --strict exits non-zero.
    out = _run("--company", str(company.id))
    assert "JE_AMOUNT_INVALID" in out
    with pytest.raises(SystemExit) as exc:
        _run("--strict", "--company", str(company.id))
    assert exc.value.code == 1


@pytest.mark.django_db
def test_load_account_facts_memo_matches_is_memo_account(company):
    """AccountFacts.is_memo must agree with Account.is_memo_account for every
    supported (account_type, ledger_domain) combination."""
    from accounting.journal_invariant import load_account_facts
    from accounting.models import Account

    combos = [
        ("MEMO", Account.LedgerDomain.FINANCIAL),  # legacy memo type
        ("ASSET", Account.LedgerDomain.STATISTICAL),  # five-type, statistical domain
        ("EXPENSE", Account.LedgerDomain.OFF_BALANCE),  # five-type, off-balance domain
        ("ASSET", Account.LedgerDomain.FINANCIAL),  # ordinary financial account
        ("REVENUE", Account.LedgerDomain.FINANCIAL),
    ]
    accounts = []
    for i, (acct_type, domain) in enumerate(combos):
        needs_uom = acct_type == "MEMO" or domain != Account.LedgerDomain.FINANCIAL
        accounts.append(
            Account.objects.create(
                company=company,
                code=f"96{i:02d}",
                name=f"combo-{i}",
                account_type=acct_type,
                ledger_domain=domain,
                status="ACTIVE",
                unit_of_measure="EA" if needs_uom else "",
            )
        )
    facts = load_account_facts(company, [a.public_id for a in accounts])
    for account in accounts:
        assert facts[str(account.public_id)].is_memo == account.is_memo_account, (
            account.account_type,
            account.ledger_domain,
        )
    # Explicit expectations, not just agreement:
    assert facts[str(accounts[0].public_id)].is_memo is True  # MEMO type
    assert facts[str(accounts[1].public_id)].is_memo is True  # STATISTICAL domain
    assert facts[str(accounts[2].public_id)].is_memo is True  # OFF_BALANCE domain
    assert facts[str(accounts[3].public_id)].is_memo is False  # ordinary FINANCIAL
    assert facts[str(accounts[4].public_id)].is_memo is False


@pytest.mark.django_db
def test_statistical_line_excluded_from_financial_totals_in_scan(company):
    """A five-type STATISTICAL-domain line (payload flag absent) must be
    classified memo by the scanner path — the entry stays clean."""
    from accounting.models import Account

    a1, a2 = _mk_account(company, "1000"), _mk_account(company, "4000")
    stat = Account.objects.create(
        company=company,
        code="9700",
        name="Stat Qty",
        account_type="ASSET",
        ledger_domain=Account.LedgerDomain.STATISTICAL,
        status="ACTIVE",
        unit_of_measure="EA",
    )
    lines = [
        _line(1, a1, debit="100.00"),
        _line(2, a2, credit="100.00"),
        _line(3, stat, debit="12.00"),  # no is_memo_line flag at all
    ]
    _mk_posted_event(company, lines, "100.00", "100.00")
    data = _run_json("--company", str(company.id))
    assert data["total_violating_events"] == 0


# --------------------------------------------------------------------------- #
# scanner date sanitation
# --------------------------------------------------------------------------- #
def _event_with_date(company, account_pair, date_value, memo="date-test"):
    a1, a2 = account_pair
    lines = [_line(1, a1, debit="100.00"), _line(2, a2, credit="99.00")]  # violating → appears in findings
    event = _mk_posted_event(company, lines, "100.00", "99.00", memo=memo)
    # Rewrite the stored payload date directly (events are immutable via
    # save(); use queryset update as the established test drift pattern).
    from events.models import BusinessEvent

    data = dict(event.data)
    if date_value is _MISSING:
        data.pop("date", None)
    else:
        data["date"] = date_value
    BusinessEvent.objects.filter(pk=event.pk).update(data=data)
    return event


_MISSING = object()


@pytest.mark.django_db
def test_scanner_date_valid_iso_accepted(company):
    pair = _mk_account(company, "1000"), _mk_account(company, "4000")
    _event_with_date(company, pair, "2026-03-05")
    data = _run_json("--company", str(company.id))
    assert data["companies"][0]["findings"][0]["date"] == "2026-03-05"


@pytest.mark.django_db
def test_scanner_date_invalid_values_fall_back_to_occurred_at(company):
    pair = _mk_account(company, "1000"), _mk_account(company, "4000")
    bad_values = [
        "2026-02-30",  # impossible calendar date
        "leak-me-PII-string@example.com",  # arbitrary text
        {"nested": "object"},  # mapping
        ["2026-01-01"],  # list
        20260101,  # integer
        True,  # boolean
        "2026-01-15T10:00:00",  # datetime string — outside the date-only contract
        _MISSING,  # missing key
        None,  # null
    ]
    events = [_event_with_date(company, pair, bad) for bad in bad_values]
    data = _run_json("--company", str(company.id))
    findings = data["companies"][0]["findings"]
    assert len(findings) == len(bad_values)
    expected = {str(e.pk): e.occurred_at.date().isoformat() for e in events}
    for finding in findings:
        assert finding["date"] == expected[finding["event_id"]]
        # Canonical shape only.
        import re

        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", finding["date"])


@pytest.mark.django_db
def test_scanner_date_rejected_raw_values_never_echoed(company):
    pair = _mk_account(company, "1000"), _mk_account(company, "4000")
    _event_with_date(company, pair, "leak-me-PII-string@example.com")
    human = _run("--company", str(company.id))
    machine = _run("--json", "--company", str(company.id))
    for output in (human, machine):
        assert "leak-me-PII" not in output
        assert "example.com" not in output


@pytest.mark.django_db
def test_bounded_queries(company, django_assert_max_num_queries):
    """Facts are cached per company and payloads use select_related — five
    same-account events must not produce per-line/per-account query storms.
    Generous bound by design (no fragile exact counts)."""
    a1, a2 = _mk_account(company, "1000"), _mk_account(company, "4000")
    for _ in range(5):
        _valid_event(company, a1, a2)
    with django_assert_max_num_queries(15):
        _run("--company", str(company.id))
