# accounting/journal_invariant.py
"""A3: THE canonical posted-journal-entry invariant.

One pure function — ``check_posted_journal`` — validates a
``JOURNAL_ENTRY_POSTED`` payload for BOTH boundaries:

  - ``mode="emit"``  — before the event is emitted (full set, including
    account active/postable posting policy);
  - ``mode="apply"`` — before a projection materializes the event (payload
    structure + account existence/company only).

The emit/apply asymmetry is deliberate: ``is_active``/``is_postable`` are
posting-time POLICY, enforced when the entry is posted. Re-checking them at
apply time would make history unreplayable the moment an account is later
deactivated or converted to a header. Apply mode still rejects missing and
cross-company accounts — referential integrity is not time-dependent.

Canonical decimal contract (verified 2026-07-29 at ``3a14f48``: the codebase
contains no ``getcontext``/``setcontext``/``localcontext`` override and no
explicit rounding mode on any ``quantize`` call, so the ambient default is
``ROUND_HALF_EVEN``): monetary comparison quantizes to ``Decimal("0.01")``
with an EXPLICIT ``ROUND_HALF_EVEN`` — never the ambient context. NaN,
±Infinity, unparseable strings and booleans are rejected (``JE_AMOUNT_INVALID``);
nothing is ever coerced to zero. There is NO tolerance — not ±0.05, not
anything (founder decision D3).

Canonical memo-line definition: a line whose payload field ``is_memo_line``
is truthy (the field the P1 emitter uses to exclude lines from emitted
totals, accounting/commands.py). Compatibility fallback: historical payloads
that lack the key default to ``False`` (financial) — identical to
``JournalLineData``'s dataclass default, so pre-2026 events evaluate exactly
as they were emitted.

Unknown EXTRA line keys are tolerated (several emitters historically shipped
``line_public_id``, which is not part of the schema).

Deterministic ordering: line-level violations are reported in line order
(parse → negative → two-sided → zero → account checks), then entry-level
violations in a fixed sequence (duplicate line_no → too few lines → sides →
balance → header totals). Repeated evaluation of the same input returns the
identical list.

This module is import-light and provider-neutral: no imports from any
connector or vertical app, and the pure function performs no database access
(the single ORM helper ``load_account_facts`` imports the model lazily).
Not a rules engine — one fixed code set, one function.

Governance: docs/architecture/architecture-constitution.md Rule 3;
docs/architecture/canonical-money-spine.md §7 (A3).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Literal, Mapping

# --------------------------------------------------------------------------- #
# Stable violation codes (the canonical 14 — frozen by an architecture test).
# SCANNER_UNREADABLE_PAYLOAD is deliberately NOT here: it is a scanner-level
# outcome and is never returned by check_posted_journal().
# --------------------------------------------------------------------------- #

JE_TOO_FEW_LINES = "JE_TOO_FEW_LINES"
JE_NO_DEBIT_SIDE = "JE_NO_DEBIT_SIDE"
JE_NO_CREDIT_SIDE = "JE_NO_CREDIT_SIDE"
JE_UNBALANCED = "JE_UNBALANCED"
JE_HEADER_TOTAL_MISMATCH = "JE_HEADER_TOTAL_MISMATCH"
JE_LINE_ZERO = "JE_LINE_ZERO"
JE_LINE_TWO_SIDED = "JE_LINE_TWO_SIDED"
JE_LINE_NEGATIVE = "JE_LINE_NEGATIVE"
JE_AMOUNT_INVALID = "JE_AMOUNT_INVALID"
JE_DUPLICATE_LINE_NO = "JE_DUPLICATE_LINE_NO"
JE_ACCOUNT_UNKNOWN = "JE_ACCOUNT_UNKNOWN"
JE_ACCOUNT_CROSS_COMPANY = "JE_ACCOUNT_CROSS_COMPANY"
JE_ACCOUNT_INACTIVE = "JE_ACCOUNT_INACTIVE"
JE_ACCOUNT_NOT_POSTABLE = "JE_ACCOUNT_NOT_POSTABLE"

JE_VIOLATION_CODES: frozenset[str] = frozenset(
    {
        JE_TOO_FEW_LINES,
        JE_NO_DEBIT_SIDE,
        JE_NO_CREDIT_SIDE,
        JE_UNBALANCED,
        JE_HEADER_TOTAL_MISMATCH,
        JE_LINE_ZERO,
        JE_LINE_TWO_SIDED,
        JE_LINE_NEGATIVE,
        JE_AMOUNT_INVALID,
        JE_DUPLICATE_LINE_NO,
        JE_ACCOUNT_UNKNOWN,
        JE_ACCOUNT_CROSS_COMPANY,
        JE_ACCOUNT_INACTIVE,
        JE_ACCOUNT_NOT_POSTABLE,
    }
)

# Emit-only posting-policy codes (see module docstring for the rationale).
EMIT_ONLY_CODES: frozenset[str] = frozenset({JE_ACCOUNT_INACTIVE, JE_ACCOUNT_NOT_POSTABLE})

TWO_PLACES = Decimal("0.01")
MONEY_ROUNDING = ROUND_HALF_EVEN

Mode = Literal["emit", "apply"]


@dataclass(frozen=True)
class JournalViolation:
    code: str
    message: str
    line_no: int | None = None

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "line_no": self.line_no}


@dataclass(frozen=True)
class AccountFacts:
    """The referential facts the invariant may consult — nothing more."""

    public_id: str
    company_id: int
    is_active: bool
    is_postable: bool  # active AND not a header/grouping account


# --------------------------------------------------------------------------- #
# Decimal parsing — strict, never coercing
# --------------------------------------------------------------------------- #


def _parse_money(value: object) -> Decimal | None:
    """Parse a monetary value strictly. Returns None when invalid: booleans,
    NaN, ±Infinity, unparseable strings, and unsupported types are all invalid.
    Never coerces to zero."""
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None
    if not isinstance(value, str | int | Decimal):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _q(value: Decimal) -> Decimal:
    """Canonical monetary quantization: 0.01, explicit ROUND_HALF_EVEN."""
    return value.quantize(TWO_PLACES, rounding=MONEY_ROUNDING)


# --------------------------------------------------------------------------- #
# The one canonical check
# --------------------------------------------------------------------------- #


def check_posted_journal(
    data: Mapping,
    *,
    company_id: int,
    account_facts: Mapping[str, AccountFacts] | None,
    mode: Mode,
) -> list[JournalViolation]:
    """Validate a JOURNAL_ENTRY_POSTED payload. Pure: consults only its
    arguments; performs no database access.

    ``account_facts`` maps ``account_public_id`` → :class:`AccountFacts`
    (build it with :func:`load_account_facts`). ``None`` skips the account
    checks entirely — payload-structure-only evaluation; both real boundaries
    always pass facts.

    Returns the (possibly empty) deterministic list of violations.
    """
    if mode not in ("emit", "apply"):
        raise ValueError(f"mode must be 'emit' or 'apply', got {mode!r}")

    violations: list[JournalViolation] = []
    lines = data.get("lines") or []

    # ---- line-level pass (in payload order) -------------------------------
    financial: list[tuple[object, Decimal, Decimal]] = []  # (line_no, debit, credit)
    financial_count = 0
    amounts_clean = True
    seen_line_nos: dict[int, int] = {}
    duplicate_line_nos: list[int] = []

    for line in lines:
        if not isinstance(line, Mapping):
            amounts_clean = False
            violations.append(JournalViolation(JE_AMOUNT_INVALID, "Line is not a mapping/dict.", None))
            continue

        raw_line_no = line.get("line_no")
        line_no = raw_line_no if isinstance(raw_line_no, int) and not isinstance(raw_line_no, bool) else None
        if line_no is not None:
            seen_line_nos[line_no] = seen_line_nos.get(line_no, 0) + 1
            if seen_line_nos[line_no] == 2:  # report each duplicated value once
                duplicate_line_nos.append(line_no)

        if line.get("is_memo_line"):
            continue  # memo lines carry no financial weight and are not posted
        financial_count += 1

        debit = _parse_money(line.get("debit", "0"))
        credit = _parse_money(line.get("credit", "0"))
        if debit is None or credit is None:
            amounts_clean = False
            violations.append(
                JournalViolation(
                    JE_AMOUNT_INVALID,
                    "Line debit/credit is not a finite, parseable decimal.",
                    line_no,
                )
            )
            continue

        if debit < 0 or credit < 0:
            violations.append(JournalViolation(JE_LINE_NEGATIVE, "Line amount is negative.", line_no))
        elif debit > 0 and credit > 0:
            violations.append(JournalViolation(JE_LINE_TWO_SIDED, "Line has both debit and credit populated.", line_no))
        elif debit == 0 and credit == 0:
            violations.append(JournalViolation(JE_LINE_ZERO, "Financial line has zero debit and zero credit.", line_no))

        financial.append((line_no, debit, credit))

        # ---- account referential checks (both modes) ----------------------
        if account_facts is not None:
            account_public_id = line.get("account_public_id")
            if not account_public_id or str(account_public_id) not in account_facts:
                violations.append(
                    JournalViolation(
                        JE_ACCOUNT_UNKNOWN,
                        f"Account {account_public_id!r} does not resolve.",
                        line_no,
                    )
                )
            else:
                facts = account_facts[str(account_public_id)]
                if facts.company_id != company_id:
                    violations.append(
                        JournalViolation(
                            JE_ACCOUNT_CROSS_COMPANY,
                            f"Account {account_public_id} belongs to another company.",
                            line_no,
                        )
                    )
                elif mode == "emit":
                    # Posting-time policy — emit boundary only (see docstring).
                    if not facts.is_active:
                        violations.append(
                            JournalViolation(
                                JE_ACCOUNT_INACTIVE,
                                f"Account {account_public_id} is not active.",
                                line_no,
                            )
                        )
                    elif not facts.is_postable:
                        violations.append(
                            JournalViolation(
                                JE_ACCOUNT_NOT_POSTABLE,
                                f"Account {account_public_id} is a header/non-postable account.",
                                line_no,
                            )
                        )

    # ---- entry-level pass (fixed sequence) ---------------------------------
    for dup in duplicate_line_nos:
        violations.append(JournalViolation(JE_DUPLICATE_LINE_NO, f"line_no {dup} appears more than once.", dup))

    if financial_count < 2:
        violations.append(
            JournalViolation(
                JE_TOO_FEW_LINES,
                f"Entry has {financial_count} financial (non-memo) line(s); at least 2 required.",
            )
        )

    # Side/balance/header checks are only meaningful when every financial
    # amount parsed cleanly — otherwise JE_AMOUNT_INVALID already tells the
    # truth and derived checks would be noise.
    if amounts_clean and financial:
        total_debit = _q(sum((d for _n, d, _c in financial), Decimal("0")))
        total_credit = _q(sum((c for _n, _d, c in financial), Decimal("0")))

        if not any(d > 0 for _n, d, _c in financial):
            violations.append(JournalViolation(JE_NO_DEBIT_SIDE, "No line carries a debit."))
        if not any(c > 0 for _n, _d, c in financial):
            violations.append(JournalViolation(JE_NO_CREDIT_SIDE, "No line carries a credit."))

        if total_debit != total_credit:
            violations.append(
                JournalViolation(
                    JE_UNBALANCED,
                    f"Debits {total_debit} != credits {total_credit} (quantized 0.01, half-even).",
                )
            )

        header_debit = _parse_money(data.get("total_debit"))
        header_credit = _parse_money(data.get("total_credit"))
        if header_debit is None or header_credit is None:
            violations.append(
                JournalViolation(
                    JE_AMOUNT_INVALID,
                    "Header total_debit/total_credit is not a finite, parseable decimal.",
                )
            )
        elif _q(header_debit) != total_debit or _q(header_credit) != total_credit:
            violations.append(
                JournalViolation(
                    JE_HEADER_TOTAL_MISMATCH,
                    f"Header totals ({_q(header_debit)}/{_q(header_credit)}) do not equal "
                    f"calculated non-memo line totals ({total_debit}/{total_credit}).",
                )
            )

    return violations


# --------------------------------------------------------------------------- #
# The one narrow ORM helper (no validation policy of its own)
# --------------------------------------------------------------------------- #


def load_account_facts(company, account_public_ids) -> dict[str, AccountFacts]:
    """Load :class:`AccountFacts` for the given ``account_public_id`` values.

    One company-agnostic query by public_id (cross-company detection requires
    seeing accounts from OTHER companies, so the query is deliberately not
    company-scoped — the pure function does the company comparison). Contains
    no policy: it only reports what exists.
    """
    from accounting.models import Account  # lazy: keeps module import Django-free

    ids = [str(a) for a in account_public_ids if a]
    if not ids:
        return {}
    facts: dict[str, AccountFacts] = {}
    rows = Account.objects.filter(public_id__in=ids).values_list("public_id", "company_id", "status", "is_header")
    for public_id, company_id, status, is_header in rows:
        is_active = status == "ACTIVE"
        facts[str(public_id)] = AccountFacts(
            public_id=str(public_id),
            company_id=company_id,
            is_active=is_active,
            is_postable=is_active and not is_header,
        )
    return facts
