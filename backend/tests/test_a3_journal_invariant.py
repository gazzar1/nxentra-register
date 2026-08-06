# tests/test_a3_journal_invariant.py
"""Pure tests for accounting/journal_invariant.py — the canonical A3 core.

Deliberately NO django_db marker and no database fixtures anywhere in this
file: pytest-django hard-fails any accidental ORM access, which is itself the
proof that check_posted_journal() is pure. The ORM helper
(load_account_facts) is tested in the scanner's database-enabled tests.
"""

from __future__ import annotations

import pytest

from accounting.journal_invariant import (
    EMIT_ONLY_CODES,
    JE_ACCOUNT_CROSS_COMPANY,
    JE_ACCOUNT_INACTIVE,
    JE_ACCOUNT_NOT_POSTABLE,
    JE_ACCOUNT_UNKNOWN,
    JE_AMOUNT_INVALID,
    JE_DUPLICATE_LINE_NO,
    JE_HEADER_TOTAL_MISMATCH,
    JE_LINE_NEGATIVE,
    JE_LINE_TWO_SIDED,
    JE_LINE_ZERO,
    JE_NO_CREDIT_SIDE,
    JE_NO_DEBIT_SIDE,
    JE_TOO_FEW_LINES,
    JE_UNBALANCED,
    JE_VIOLATION_CODES,
    AccountFacts,
    check_posted_journal,
)

COMPANY_ID = 7

ACC_A = "11111111-1111-1111-1111-111111111111"
ACC_B = "22222222-2222-2222-2222-222222222222"

ACC_M = "33333333-3333-3333-3333-333333333333"  # memo account (account_type == MEMO)

FACTS = {
    ACC_A: AccountFacts(public_id=ACC_A, company_id=COMPANY_ID, is_active=True, is_postable=True),
    ACC_B: AccountFacts(public_id=ACC_B, company_id=COMPANY_ID, is_active=True, is_postable=True),
    ACC_M: AccountFacts(public_id=ACC_M, company_id=COMPANY_ID, is_active=True, is_postable=True, is_memo=True),
}


def _line(line_no, account, debit="0", credit="0", **extra):
    return {
        "line_no": line_no,
        "account_public_id": account,
        "account_code": "1000",
        "description": "line",
        "debit": debit,
        "credit": credit,
        **extra,
    }


def _payload(lines, total_debit="100.00", total_credit="100.00", **extra):
    return {
        "entry_public_id": "e-1",
        "entry_number": "JE-1",
        "date": "2026-01-15",
        "memo": "test",
        "kind": "NORMAL",
        "posted_at": "2026-01-15T10:00:00",
        "posted_by_id": 1,
        "posted_by_email": "owner@test.com",
        "total_debit": total_debit,
        "total_credit": total_credit,
        "lines": lines,
        **extra,
    }


def _valid():
    return _payload([_line(1, ACC_A, debit="100.00"), _line(2, ACC_B, credit="100.00")])


def _codes(violations):
    return [v.code for v in violations]


def _check(data, *, facts=FACTS, mode="emit", company_id=COMPANY_ID):
    return check_posted_journal(data, company_id=company_id, account_facts=facts, mode=mode)


# --------------------------------------------------------------------------- #
# valid shapes
# --------------------------------------------------------------------------- #
def test_valid_two_line_entry_passes_both_modes():
    assert _check(_valid(), mode="emit") == []
    assert _check(_valid(), mode="apply") == []


def test_valid_multi_line_entry_passes():
    data = _payload(
        [
            _line(1, ACC_A, debit="60.00"),
            _line(2, ACC_A, debit="40.00"),
            _line(3, ACC_B, credit="75.50"),
            _line(4, ACC_B, credit="24.50"),
        ]
    )
    assert _check(data) == []


def test_memo_lines_excluded_from_totals_and_count():
    # Two financial lines + one MEMO-ACCOUNT line; totals exclude the memo line
    # (classification from account facts, matching JournalEntry total props).
    data = _payload(
        [
            _line(1, ACC_A, debit="100.00"),
            _line(2, ACC_B, credit="100.00"),
            _line(3, ACC_M, debit="5.00", is_memo_line=True),  # statistical qty, one-sided
        ]
    )
    assert _check(data) == []


def test_memo_only_entry_is_too_few_lines():
    data = _payload([_line(1, ACC_M, is_memo_line=True), _line(2, ACC_M, is_memo_line=True)])
    codes = _codes(_check(data))
    assert JE_TOO_FEW_LINES in codes


def test_unknown_extra_line_keys_tolerated():
    data = _valid()
    data["lines"][0]["line_public_id"] = "hist-key"  # historical non-schema key
    data["lines"][1]["some_future_key"] = {"nested": True}
    assert _check(data) == []


def test_missing_optional_header_keys_tolerated():
    # Historical payloads lack source_module/source_document/currency — fine.
    data = _valid()
    for key in ("source_module", "source_document", "currency", "exchange_rate", "period"):
        data.pop(key, None)
    assert _check(data) == []


# --------------------------------------------------------------------------- #
# one test per violation code
# --------------------------------------------------------------------------- #
def test_too_few_lines():
    codes = _codes(_check(_payload([_line(1, ACC_A, debit="10.00")], "10.00", "10.00")))
    assert JE_TOO_FEW_LINES in codes
    # Empty lines list too.
    assert JE_TOO_FEW_LINES in _codes(_check(_payload([], "0.00", "0.00")))


def test_no_debit_side():
    data = _payload([_line(1, ACC_A, credit="50.00"), _line(2, ACC_B, credit="50.00")], "100.00", "100.00")
    codes = _codes(_check(data))
    assert JE_NO_DEBIT_SIDE in codes


def test_no_credit_side():
    data = _payload([_line(1, ACC_A, debit="50.00"), _line(2, ACC_B, debit="50.00")], "100.00", "100.00")
    codes = _codes(_check(data))
    assert JE_NO_CREDIT_SIDE in codes


def test_unbalanced():
    data = _payload([_line(1, ACC_A, debit="100.00"), _line(2, ACC_B, credit="99.97")], "100.00", "99.97")
    codes = _codes(_check(data))
    assert JE_UNBALANCED in codes


def test_unbalanced_by_a_cent_no_tolerance():
    # D3: exactly one cent off must fail — no ±0.05 tolerance exists.
    data = _payload([_line(1, ACC_A, debit="100.00"), _line(2, ACC_B, credit="99.99")], "100.00", "99.99")
    assert JE_UNBALANCED in _codes(_check(data))


def test_header_total_mismatch():
    data = _payload(
        [_line(1, ACC_A, debit="100.00"), _line(2, ACC_B, credit="100.00")],
        total_debit="90.00",
        total_credit="100.00",
    )
    codes = _codes(_check(data))
    assert JE_HEADER_TOTAL_MISMATCH in codes
    assert JE_UNBALANCED not in codes  # lines themselves balance


def test_line_zero():
    data = _payload(
        [
            _line(1, ACC_A, debit="100.00"),
            _line(2, ACC_B, credit="100.00"),
            _line(3, ACC_A),  # 0/0 financial line
        ]
    )
    violations = _check(data)
    assert JE_LINE_ZERO in _codes(violations)
    assert any(v.code == JE_LINE_ZERO and v.line_no == 3 for v in violations)


def test_line_two_sided():
    data = _payload([_line(1, ACC_A, debit="100.00", credit="1.00"), _line(2, ACC_B, credit="99.00")])
    assert JE_LINE_TWO_SIDED in _codes(_check(data))


def test_line_negative():
    data = _payload(
        [_line(1, ACC_A, debit="-100.00"), _line(2, ACC_B, credit="-100.00")],
        "-100.00",
        "-100.00",
    )
    codes = _codes(_check(data))
    assert codes.count(JE_LINE_NEGATIVE) == 2


def test_amount_invalid_unparseable():
    data = _payload([_line(1, ACC_A, debit="banana"), _line(2, ACC_B, credit="100.00")])
    codes = _codes(_check(data))
    assert JE_AMOUNT_INVALID in codes
    # Derived checks are suppressed when amounts are dirty.
    assert JE_UNBALANCED not in codes
    assert JE_HEADER_TOTAL_MISMATCH not in codes


def test_duplicate_line_no():
    data = _payload(
        [
            _line(1, ACC_A, debit="60.00"),
            _line(1, ACC_B, credit="60.00"),  # duplicate line_no 1
        ]
    )
    data["total_debit"] = "60.00"
    data["total_credit"] = "60.00"
    violations = _check(data)
    assert JE_DUPLICATE_LINE_NO in _codes(violations)
    assert any(v.code == JE_DUPLICATE_LINE_NO and v.line_no == 1 for v in violations)


def test_account_unknown():
    data = _valid()
    facts = {ACC_A: FACTS[ACC_A]}  # ACC_B missing
    codes = _codes(_check(data, facts=facts))
    assert JE_ACCOUNT_UNKNOWN in codes


def test_account_cross_company():
    facts = {
        ACC_A: FACTS[ACC_A],
        ACC_B: AccountFacts(public_id=ACC_B, company_id=999, is_active=True, is_postable=True),
    }
    codes = _codes(_check(_valid(), facts=facts))
    assert JE_ACCOUNT_CROSS_COMPANY in codes
    # Cross-company is rejected in BOTH modes.
    assert JE_ACCOUNT_CROSS_COMPANY in _codes(_check(_valid(), facts=facts, mode="apply"))


def test_account_inactive_emit_only():
    facts = {
        ACC_A: FACTS[ACC_A],
        ACC_B: AccountFacts(public_id=ACC_B, company_id=COMPANY_ID, is_active=False, is_postable=False),
    }
    assert JE_ACCOUNT_INACTIVE in _codes(_check(_valid(), facts=facts, mode="emit"))
    # Apply mode: still resolvable + same company → accepted (history stays replayable).
    assert _check(_valid(), facts=facts, mode="apply") == []


def test_account_not_postable_emit_only():
    facts = {
        ACC_A: FACTS[ACC_A],
        # Active header account: postable=False but active=True → NOT_POSTABLE (not INACTIVE).
        ACC_B: AccountFacts(public_id=ACC_B, company_id=COMPANY_ID, is_active=True, is_postable=False),
    }
    emit_codes = _codes(_check(_valid(), facts=facts, mode="emit"))
    assert JE_ACCOUNT_NOT_POSTABLE in emit_codes
    assert JE_ACCOUNT_INACTIVE not in emit_codes  # mutually exclusive by design
    assert _check(_valid(), facts=facts, mode="apply") == []


# --------------------------------------------------------------------------- #
# decimal & rounding contract
# --------------------------------------------------------------------------- #
def test_nan_and_infinity_rejected():
    for bad in ("NaN", "Infinity", "-Infinity"):
        data = _payload([_line(1, ACC_A, debit=bad), _line(2, ACC_B, credit="1.00")])
        assert JE_AMOUNT_INVALID in _codes(_check(data)), bad


def test_bool_rejected_as_amount():
    data = _payload([_line(1, ACC_A, debit=True), _line(2, ACC_B, credit="1.00")])
    assert JE_AMOUNT_INVALID in _codes(_check(data))


def test_header_nan_rejected():
    data = _payload(
        [_line(1, ACC_A, debit="1.00"), _line(2, ACC_B, credit="1.00")],
        total_debit="NaN",
        total_credit="1.00",
    )
    assert JE_AMOUNT_INVALID in _codes(_check(data))


def test_half_even_quantization_boundaries():
    """Final P1 pass split these semantics by mode: HISTORICAL evaluation
    (mode="apply", the corpus scanner) keeps the quantized interpretation
    pending D3; mode="emit" REJECTS over-precision outright (a new event
    must already be its exact 2dp canonical value — otherwise the payload
    that validates differs from what the (18,2) columns materialize)."""
    # 10.005 → 10.00 (ties to even) under APPLY, balancing against 10.00.
    data = _payload(
        [_line(1, ACC_A, debit="10.005"), _line(2, ACC_B, credit="10.00")],
        total_debit="10.00",
        total_credit="10.00",
    )
    assert _check(data, mode="apply") == []
    # The same payload is REJECTED at emit — over-precision, not rounding.
    assert JE_AMOUNT_INVALID in _codes(_check(data, mode="emit"))
    # 10.015 → 10.02 (ties to even) under APPLY...
    data = _payload(
        [_line(1, ACC_A, debit="10.015"), _line(2, ACC_B, credit="10.02")],
        total_debit="10.02",
        total_credit="10.02",
    )
    assert _check(data, mode="apply") == []
    assert JE_AMOUNT_INVALID in _codes(_check(data, mode="emit"))
    # ...and 10.01 does not balance it under APPLY.
    data = _payload(
        [_line(1, ACC_A, debit="10.015"), _line(2, ACC_B, credit="10.01")],
        total_debit="10.02",
        total_credit="10.01",
    )
    assert JE_UNBALANCED in _codes(_check(data, mode="apply"))


def test_per_line_quantization_matches_ledger_materialization():
    """Ledger-materialization semantics: each line quantizes FIRST (the
    JournalLine columns store 2dp per line), and the entry is judged on what
    would actually be stored — NOT on aggregate-then-quantize."""
    # 0.006 + 0.006 debits vs 0.012 credit: stored lines are 0.01 + 0.01 vs
    # 0.01 → materially unbalanced, even though raw sums (0.012 == 0.012) and
    # aggregate-quantized sums (0.01 == 0.01) would both look balanced.
    data = _payload(
        [
            _line(1, ACC_A, debit="0.006"),
            _line(2, ACC_A, debit="0.006"),
            _line(3, ACC_B, credit="0.012"),
        ],
        total_debit="0.02",
        total_credit="0.01",
    )
    assert JE_UNBALANCED in _codes(_check(data, mode="apply"))
    # At emit, the over-precise inputs never reach balance logic at all.
    assert JE_AMOUNT_INVALID in _codes(_check(data, mode="emit"))

    # Per-line quantization that lands exactly is fine UNDER APPLY: 50.002 →
    # 50.00 and 49.998 → 50.00 stored, vs 100.00 credit → balanced as stored.
    data = _payload(
        [
            _line(1, ACC_A, debit="50.002"),
            _line(2, ACC_A, debit="49.998"),
            _line(3, ACC_B, credit="100.00"),
        ]
    )
    assert _check(data, mode="apply") == []
    # Emit still rejects: the raw payload is not what would be stored.
    assert JE_AMOUNT_INVALID in _codes(_check(data, mode="emit"))


def test_lines_that_quantize_to_zero_are_zero_lines():
    # 0.004 debit vs 0.004 credit: both store as 0.00 → zero-value financial
    # lines that cannot form a valid JE (APPLY interpretation).
    data = _payload(
        [_line(1, ACC_A, debit="0.004"), _line(2, ACC_B, credit="0.004")],
        total_debit="0.00",
        total_credit="0.00",
    )
    codes = _codes(_check(data, mode="apply"))
    assert codes.count(JE_LINE_ZERO) == 2
    # At EMIT the sub-cent values are rejected as over-precision —
    # JE_AMOUNT_INVALID, deliberately NOT JE_LINE_ZERO.
    emit_codes = _codes(_check(data, mode="emit"))
    assert emit_codes.count(JE_AMOUNT_INVALID) == 2
    assert JE_LINE_ZERO not in emit_codes


# --------------------------------------------------------------------------- #
# representability (JournalLine max_digits=18, decimal_places=2)
# --------------------------------------------------------------------------- #
def test_oversized_amount_1e100_is_invalid_not_a_crash():
    data = _payload([_line(1, ACC_A, debit="1e100"), _line(2, ACC_B, credit="1.00")])
    codes = _codes(_check(data))  # must return, never raise
    assert JE_AMOUNT_INVALID in codes


def test_max_representable_amount_is_valid():
    from accounting.journal_invariant import MAX_ABS_AMOUNT

    big = str(MAX_ABS_AMOUNT)  # 9999999999999999.99
    data = _payload(
        [_line(1, ACC_A, debit=big), _line(2, ACC_B, credit=big)],
        total_debit=big,
        total_credit=big,
    )
    assert _check(data) == []


def test_just_beyond_max_representable_is_invalid():
    from accounting.journal_invariant import MAX_ABS_AMOUNT, TWO_PLACES

    too_big = str(MAX_ABS_AMOUNT + TWO_PLACES)  # 10000000000000000.00
    data = _payload([_line(1, ACC_A, debit=too_big), _line(2, ACC_B, credit="1.00")])
    assert JE_AMOUNT_INVALID in _codes(_check(data))


def test_negative_oversized_amount_is_invalid_not_negative():
    """An unrepresentable negative must be JE_AMOUNT_INVALID — JE_LINE_NEGATIVE
    must not be derived from a value that failed normalization."""
    data = _payload([_line(1, ACC_A, debit="-1e100"), _line(2, ACC_B, credit="1.00")])
    codes = _codes(_check(data))
    assert JE_AMOUNT_INVALID in codes
    assert JE_LINE_NEGATIVE not in codes


def test_oversized_header_total_is_invalid():
    data = _payload(
        [_line(1, ACC_A, debit="1.00"), _line(2, ACC_B, credit="1.00")],
        total_debit="1e100",
        total_credit="1.00",
    )
    assert JE_AMOUNT_INVALID in _codes(_check(data))


def test_quantization_failure_never_escapes():
    # A grab-bag of hostile values in every monetary slot: the function must
    # return a violations list, never raise.
    for bad in ("1e100", "-1e100", "9" * 40, "NaN", "Infinity", "-Infinity", True, [], {}):
        data = _payload(
            [_line(1, ACC_A, debit=bad), _line(2, ACC_B, credit=bad)],
            total_debit=bad if not isinstance(bad, list | dict) else "1.00",
            total_credit="1.00",
        )
        codes = _codes(_check(data))
        assert JE_AMOUNT_INVALID in codes, repr(bad)


# --------------------------------------------------------------------------- #
# memo classification — derived from the RESOLVED ACCOUNT, never the flag
# --------------------------------------------------------------------------- #
def _memo_line(line_no, account, flag, **amounts):
    extra = {"is_memo_line": flag} if flag is not None else {}
    return _line(line_no, account, **amounts, **extra)


def test_memo_account_with_flag_true_is_memo():
    data = _payload(
        [
            _line(1, ACC_A, debit="100.00"),
            _line(2, ACC_B, credit="100.00"),
            _memo_line(3, ACC_M, True, debit="5.00"),
        ]
    )
    assert _check(data) == []


def test_memo_account_with_flag_false_or_missing_is_still_memo():
    for flag in (False, None):
        data = _payload(
            [
                _line(1, ACC_A, debit="100.00"),
                _line(2, ACC_B, credit="100.00"),
                _memo_line(3, ACC_M, flag, debit="5.00"),
            ]
        )
        assert _check(data) == [], f"flag={flag}"


def test_financial_account_with_flag_true_stays_financial():
    """The smuggling case: a real financial line mislabeled memo must count —
    its amount unbalances the entry and mismatches the header."""
    data = _payload(
        [
            _line(1, ACC_A, debit="100.00"),
            _line(2, ACC_B, credit="100.00"),
            _memo_line(3, ACC_A, True, debit="50.00"),
        ]
    )
    codes = _codes(_check(data))
    assert JE_UNBALANCED in codes
    assert JE_HEADER_TOTAL_MISMATCH in codes


def test_financial_account_with_flag_false_is_financial():
    data = _payload(
        [
            _memo_line(1, ACC_A, False, debit="100.00"),
            _memo_line(2, ACC_B, False, credit="100.00"),
        ]
    )
    assert _check(data) == []


def test_unknown_account_with_flag_true_is_not_guessed_memo():
    facts = {ACC_B: FACTS[ACC_B]}
    data = _payload(
        [
            _memo_line(1, ACC_A, True, debit="100.00"),  # unresolvable — stays financial
            _line(2, ACC_B, credit="100.00"),
        ]
    )
    codes = _codes(_check(data, facts=facts))
    assert JE_ACCOUNT_UNKNOWN in codes
    # Its amount still counts: header claims 100/100 and lines are 100/100 —
    # balanced — but the line was NOT silently dropped as memo.
    assert JE_TOO_FEW_LINES not in codes


def test_cross_company_memo_account_flags_cross_company():
    facts = dict(FACTS)
    facts[ACC_M] = AccountFacts(public_id=ACC_M, company_id=999, is_active=True, is_postable=True, is_memo=True)
    data = _payload(
        [
            _line(1, ACC_A, debit="100.00"),
            _line(2, ACC_B, credit="100.00"),
            _memo_line(3, ACC_M, True),
        ]
    )
    assert JE_ACCOUNT_CROSS_COMPANY in _codes(_check(data, facts=facts))


def test_mixed_financial_and_memo_lines():
    data = _payload(
        [
            _line(1, ACC_A, debit="60.00"),
            _memo_line(2, ACC_M, None, debit="7.00"),
            _line(3, ACC_A, debit="40.00"),
            _memo_line(4, ACC_M, True, credit="3.00"),
            _line(5, ACC_B, credit="100.00"),
        ]
    )
    assert _check(data) == []


def test_memo_classification_identical_in_emit_and_apply():
    data = _payload(
        [
            _line(1, ACC_A, debit="100.00"),
            _line(2, ACC_B, credit="100.00"),
            _memo_line(3, ACC_M, False, debit="2.50"),
        ]
    )
    assert _check(data, mode="emit") == _check(data, mode="apply") == []


def test_memo_line_amounts_must_still_parse():
    data = _payload(
        [
            _line(1, ACC_A, debit="100.00"),
            _line(2, ACC_B, credit="100.00"),
            _memo_line(3, ACC_M, True, debit="NaN"),
        ]
    )
    assert JE_AMOUNT_INVALID in _codes(_check(data))


# --------------------------------------------------------------------------- #
# memo-line SHAPE validation (storage contract applies to every line)
# --------------------------------------------------------------------------- #
def _with_memo(memo_kwargs):
    """A balanced financial pair plus one memo-account line built from kwargs."""
    return _payload(
        [
            _line(1, ACC_A, debit="100.00"),
            _line(2, ACC_B, credit="100.00"),
            _line(3, ACC_M, is_memo_line=True, **memo_kwargs),
        ]
    )


def test_memo_positive_debit_only_is_valid_and_excluded():
    assert _check(_with_memo({"debit": "5.00"})) == []


def test_memo_positive_credit_only_is_valid_and_excluded():
    assert _check(_with_memo({"credit": "5.00"})) == []


def test_memo_negative_debit_is_negative():
    violations = _check(_with_memo({"debit": "-5.00"}))
    assert any(v.code == JE_LINE_NEGATIVE and v.line_no == 3 for v in violations)


def test_memo_negative_credit_is_negative():
    violations = _check(_with_memo({"credit": "-5.00"}))
    assert any(v.code == JE_LINE_NEGATIVE and v.line_no == 3 for v in violations)


def test_memo_two_sided_is_two_sided():
    violations = _check(_with_memo({"debit": "5.00", "credit": "5.00"}))
    assert any(v.code == JE_LINE_TWO_SIDED and v.line_no == 3 for v in violations)


def test_memo_zero_zero_is_line_zero():
    violations = _check(_with_memo({}))  # defaults 0/0
    assert any(v.code == JE_LINE_ZERO and v.line_no == 3 for v in violations)


def test_memo_sub_cent_quantizing_to_zero_is_line_zero():
    # APPLY interpretation: the memo line stores as 0.00 → JE_LINE_ZERO.
    violations = _check(_with_memo({"debit": "0.004"}), mode="apply")
    assert any(v.code == JE_LINE_ZERO and v.line_no == 3 for v in violations)
    # EMIT rejects the over-precision itself, before any zero-shape logic.
    emit_violations = _check(_with_memo({"debit": "0.004"}), mode="emit")
    assert any(v.code == JE_AMOUNT_INVALID and v.line_no == 3 for v in emit_violations)


def test_memo_oversized_amount_is_invalid():
    from accounting.journal_invariant import MAX_ABS_AMOUNT, TWO_PLACES

    violations = _check(_with_memo({"debit": str(MAX_ABS_AMOUNT + TWO_PLACES)}))
    assert any(v.code == JE_AMOUNT_INVALID and v.line_no == 3 for v in violations)


def test_memo_nan_infinity_is_invalid_but_financials_still_checked():
    for bad in ("NaN", "Infinity"):
        violations = _check(_with_memo({"debit": bad}))
        codes = _codes(violations)
        assert JE_AMOUNT_INVALID in codes, bad
        # A bad MEMO amount must not suppress the financial balance checks —
        # the clean financial pair still balances, so no derived codes appear.
        assert JE_UNBALANCED not in codes
        assert JE_HEADER_TOTAL_MISMATCH not in codes


def test_valid_memo_plus_balanced_financials_overall_valid():
    data = _payload(
        [
            _line(1, ACC_A, debit="100.00"),
            _line(2, ACC_B, credit="100.00"),
            _line(3, ACC_M, debit="42.00", is_memo_line=True),
            _line(4, ACC_M, credit="17.00"),  # memo by ACCOUNT, flag absent
        ]
    )
    assert _check(data) == []


def test_memo_only_lines_do_not_satisfy_financial_requirements():
    data = _payload(
        [_line(1, ACC_M, debit="5.00"), _line(2, ACC_M, credit="5.00")],
        total_debit="0.00",
        total_credit="0.00",
    )
    codes = _codes(_check(data))
    assert JE_TOO_FEW_LINES in codes
    # No financial lines at all → the side requirements are not evaluated
    # against memo quantities (the `financial` list is empty).
    assert JE_NO_DEBIT_SIDE not in codes
    assert JE_NO_CREDIT_SIDE not in codes


# --------------------------------------------------------------------------- #
# malformed line containers
# --------------------------------------------------------------------------- #
def test_non_list_lines_container_is_structural_amount_invalid():
    for bad in (1, "two lines", {"line_no": 1}, ("a", "b")):
        data = _payload([], "1.00", "1.00")
        data["lines"] = bad
        violations = _check(data)
        codes = _codes(violations)
        assert codes == [JE_AMOUNT_INVALID], repr(bad)
        # No line/account/balance/header logic ran on the invalid container.
        assert len(violations) == 1, repr(bad)


def test_missing_or_null_lines_follow_required_field_contract():
    # Missing/None lines evaluate as an empty list (the schema layer owns
    # required-field presence): structurally sound, financially too few.
    data = _payload([], "0.00", "0.00")
    del data["lines"]
    assert JE_TOO_FEW_LINES in _codes(_check(data))
    data = _payload([], "0.00", "0.00")
    data["lines"] = None
    assert JE_TOO_FEW_LINES in _codes(_check(data))


def test_empty_list_lines_is_too_few_not_invalid():
    data = _payload([], "0.00", "0.00")
    codes = _codes(_check(data))
    assert JE_TOO_FEW_LINES in codes
    assert JE_AMOUNT_INVALID not in codes


# --------------------------------------------------------------------------- #
# aggregate headers vs per-line storage bounds
# --------------------------------------------------------------------------- #
def test_large_aggregate_of_valid_lines_is_accepted():
    from accounting.journal_invariant import MAX_ABS_AMOUNT

    big = str(MAX_ABS_AMOUNT)  # each line individually representable
    aggregate = str(MAX_ABS_AMOUNT * 3)  # header far beyond one line's max
    data = _payload(
        [
            _line(1, ACC_A, debit=big),
            _line(2, ACC_A, debit=big),
            _line(3, ACC_A, debit=big),
            _line(4, ACC_B, credit=big),
            _line(5, ACC_B, credit=big),
            _line(6, ACC_B, credit=big),
        ],
        total_debit=aggregate,
        total_credit=aggregate,
    )
    assert _check(data) == []


def test_header_mismatch_against_large_valid_aggregate():
    from accounting.journal_invariant import MAX_ABS_AMOUNT

    big = str(MAX_ABS_AMOUNT)
    data = _payload(
        [
            _line(1, ACC_A, debit=big),
            _line(2, ACC_A, debit=big),
            _line(3, ACC_B, credit=big),
            _line(4, ACC_B, credit=big),
        ],
        total_debit=str(MAX_ABS_AMOUNT * 2),
        total_credit="100.00",  # wrong
    )
    assert JE_HEADER_TOTAL_MISMATCH in _codes(_check(data))


# --------------------------------------------------------------------------- #
# account-id canonicalization
# --------------------------------------------------------------------------- #
def test_uppercase_uuid_string_resolves_to_same_facts():
    data = _valid()
    data["lines"][0]["account_public_id"] = ACC_A.upper()
    assert _check(data) == []


def test_uuid_object_resolves_to_same_facts():
    from uuid import UUID as _UUID

    data = _valid()
    data["lines"][0]["account_public_id"] = _UUID(ACC_A)
    assert _check(data) == []


def test_malformed_account_id_is_unknown_not_a_crash():
    data = _valid()
    data["lines"][0]["account_public_id"] = "not-a-uuid"
    violations = _check(data)
    assert JE_ACCOUNT_UNKNOWN in _codes(violations)


def test_canonical_account_id_helper():
    from accounting.journal_invariant import canonical_account_id

    assert canonical_account_id(ACC_A) == ACC_A
    assert canonical_account_id(ACC_A.upper()) == ACC_A
    from uuid import UUID as _UUID

    assert canonical_account_id(_UUID(ACC_A)) == ACC_A
    for bad in ("not-a-uuid", "", None, True, 12, [], {}):
        assert canonical_account_id(bad) is None, repr(bad)


# --------------------------------------------------------------------------- #
# determinism, ordering, misc contract
# --------------------------------------------------------------------------- #
def test_multiple_violations_deterministic_order():
    data = _payload(
        [
            _line(1, ACC_A, debit="-5.00"),  # negative (financial)
            _line(1, ACC_M, is_memo_line=True),  # memo ACCOUNT + duplicate line_no
        ],
        total_debit="banana",
        total_credit="0.00",
    )
    # One financial line → TOO_FEW fires; the unparseable HEADER total reports
    # JE_AMOUNT_INVALID while line-derived checks still run (lines were clean).
    first = _check(data)
    second = _check(data)
    third = _check(data)
    assert first == second == third
    codes = _codes(first)
    # Line-level first (payload order), then entry-level fixed sequence.
    assert codes.index(JE_LINE_NEGATIVE) < codes.index(JE_DUPLICATE_LINE_NO)
    assert codes.index(JE_DUPLICATE_LINE_NO) < codes.index(JE_TOO_FEW_LINES)


def test_repeated_execution_identical_on_valid_input():
    assert _check(_valid()) == _check(_valid()) == []


def test_account_checks_skipped_when_facts_none():
    data = _valid()
    assert check_posted_journal(data, company_id=COMPANY_ID, account_facts=None, mode="emit") == []


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        check_posted_journal(_valid(), company_id=COMPANY_ID, account_facts=None, mode="replay")


def test_code_set_is_exactly_fourteen():
    assert len(JE_VIOLATION_CODES) == 14
    assert EMIT_ONLY_CODES <= JE_VIOLATION_CODES
    assert "SCANNER_UNREADABLE_PAYLOAD" not in JE_VIOLATION_CODES


def test_violation_codes_are_stable_strings():
    # Assert codes, not English prose — messages may evolve, codes may not.
    data = _payload([_line(1, ACC_A, debit="100.00"), _line(2, ACC_B, credit="99.00")], "100.00", "99.00")
    violations = _check(data)
    assert all(v.code in JE_VIOLATION_CODES for v in violations)
    assert all(isinstance(v.as_dict()["code"], str) for v in violations)


def test_totals_use_decimal_never_float():
    # A float-ish string with binary-representation noise must still compare
    # exactly under Decimal semantics.
    data = _payload(
        [_line(1, ACC_A, debit="0.1"), _line(2, ACC_A, debit="0.2"), _line(3, ACC_B, credit="0.3")],
        total_debit="0.30",
        total_credit="0.30",
    )
    assert _check(data) == []


# --------------------------------------------------------------------------- #
# Final P1 pass: emit-only exact-representation rule (mode distinction)
# --------------------------------------------------------------------------- #
def test_emit_accepts_numerically_canonical_spellings():
    """The strict rule compares by VALUE, not by string: 10, 10.0, 10.00 and
    1.230 are all exactly their 2dp canonical value."""
    from decimal import Decimal as D

    data = _payload(
        [_line(1, ACC_A, debit=10), _line(2, ACC_B, credit="10.0")],
        total_debit="10.00",
        total_credit="10",
    )
    assert _check(data, mode="emit") == []

    data = _payload(
        [_line(1, ACC_A, debit="1.230"), _line(2, ACC_B, credit=D("1.23"))],
        total_debit="1.23",
        total_credit="1.230",
    )
    assert _check(data, mode="emit") == []


def test_emit_rejects_over_precise_headers_even_with_exact_lines():
    data = _payload(
        [_line(1, ACC_A, debit="10.00"), _line(2, ACC_B, credit="10.00")],
        total_debit="10.000001",
        total_credit="10.00",
    )
    emit_codes = _codes(_check(data, mode="emit"))
    assert JE_AMOUNT_INVALID in emit_codes
    # APPLY keeps the quantized interpretation: 10.000001 → 10.00 matches.
    assert _check(data, mode="apply") == []


def test_emit_accepts_large_exact_aggregate_headers():
    """Headers keep the prior decision: no per-line max bound — a large but
    EXACT aggregate is fine in both modes."""
    from accounting.journal_invariant import MAX_ABS_AMOUNT

    big = str(MAX_ABS_AMOUNT)
    double_big = str(MAX_ABS_AMOUNT * 2)  # beyond any single line, exact 2dp
    data = _payload(
        [
            _line(1, ACC_A, debit=big),
            _line(2, ACC_A, debit=big),
            _line(3, ACC_B, credit=double_big),
        ],
        total_debit=double_big,
        total_credit=double_big,
    )
    # The credit line itself exceeds the per-line bound → invalid; header
    # totals alone must NOT trip the bound. Prove headers directly with a
    # balanced two-line big entry.
    data = _payload(
        [_line(1, ACC_A, debit=big), _line(2, ACC_B, credit=big)],
        total_debit=big,
        total_credit=big,
    )
    assert _check(data, mode="emit") == []


def test_emit_strictness_keeps_deterministic_ordering():
    """Repeated evaluation returns the identical list, and the line-then-entry
    ordering is unchanged by the strict rule."""
    data = _payload(
        [
            _line(1, ACC_A, debit="10.005"),
            _line(2, ACC_B, credit="-1.00"),
        ],
        total_debit="10.00",
        total_credit="-1.00",
    )
    first = _check(data, mode="emit")
    second = _check(data, mode="emit")
    assert first == second
    codes = _codes(first)
    # Line-level in line order: line 1 over-precision, line 2 negative;
    # entry-level (too-few) after.
    assert codes.index(JE_AMOUNT_INVALID) < codes.index(JE_LINE_NEGATIVE)
    assert JE_TOO_FEW_LINES in codes
