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

Canonical memo-line definition: a line is memo iff its RESOLVED ACCOUNT is a
memo/non-financial account per ``Account.is_memo_account`` — the legacy
``account_type == MEMO`` OR a ``ledger_domain`` of STATISTICAL/OFF_BALANCE
(models.py:608-613). Memo classification exempts a line ONLY from the
financial aggregates (count, sides, balance, header totals); every line —
memo included — must still satisfy the JournalLine storage-shape contract
(normalized amounts, one-sided, non-negative, non-zero). The
payload ``is_memo_line`` flag is tolerated as historical metadata but is NOT
authoritative: a financial-account line flagged memo stays financial (so a
smuggled amount surfaces as UNBALANCED/HEADER_TOTAL_MISMATCH), a memo-account
line is memo even when the flag is absent, and unknown/malformed/
cross-company accounts are never guessed to be memo. Only when
``account_facts is None`` (payload-only evaluation, no resolution possible)
does the flag serve as the sole signal.

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
from uuid import UUID

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

# Representability contract, derived from the JournalLine storage fields
# (accounting/models.py: DecimalField(max_digits=18, decimal_places=2)).
# An amount the ledger columns cannot store is JE_AMOUNT_INVALID — the
# invariant reflects exactly what materialization can hold, never the ambient
# Decimal context.
LEDGER_MAX_DIGITS = 18
LEDGER_DECIMAL_PLACES = 2
MAX_ABS_AMOUNT = Decimal(10) ** (LEDGER_MAX_DIGITS - LEDGER_DECIMAL_PLACES) - TWO_PLACES

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
    is_memo: bool = False  # Account.is_memo_account semantics: MEMO type OR STATISTICAL/OFF_BALANCE domain


# --------------------------------------------------------------------------- #
# Decimal parsing — strict, never coercing
# --------------------------------------------------------------------------- #


def _parse_and_quantize(value: object) -> tuple[Decimal, Decimal] | None:
    """Shared normalization core: parse, then quantize to the canonical
    ledger quantum (0.01, explicit ROUND_HALF_EVEN). Returns
    ``(parsed, quantized)``, or None when invalid: booleans, NaN, ±Infinity,
    unparseable strings, unsupported types, and quantization failures (e.g.
    Decimal("1e100") — finite but unquantizable within the decimal context).
    Never coerces to zero; callers must not run any further monetary logic
    on a value that failed normalization."""
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
    try:
        return parsed, parsed.quantize(TWO_PLACES, rounding=MONEY_ROUNDING)
    except (InvalidOperation, OverflowError):
        return None


def _normalize_money(value: object, *, strict: bool = False) -> Decimal | None:
    """LINE-amount normalization: the shared core PLUS the representability
    bound of the JournalLine storage columns (|v| <= MAX_ABS_AMOUNT).

    ``strict=True`` (mode="emit", final P1 correction): the supplied value
    must ALREADY equal its canonical two-decimal representation — an
    over-precise amount ("10.005", "0.004") is rejected rather than rounded,
    because the validated view would otherwise diverge from what the
    (18,2) ledger columns materialize from the raw emitted payload.
    Numerically canonical spellings ("10", "10.0", "1.230") stay valid —
    the comparison is by VALUE, not by string. Historical evaluation
    (mode="apply"/the corpus scanner) keeps the quantized interpretation
    pending founder decision D3."""
    pq = _parse_and_quantize(value)
    if pq is None:
        return None
    parsed, quantized = pq
    if strict and parsed != quantized:
        return None
    if quantized.copy_abs() > MAX_ABS_AMOUNT:
        return None
    return quantized


def _normalize_header_total(value: object, *, strict: bool = False) -> Decimal | None:
    """HEADER-total normalization: the shared core WITHOUT the per-line
    storage bound — JournalEntry.total_debit/total_credit are computed
    aggregates, not (18,2) columns, and a many-line entry may legitimately
    sum beyond one line's maximum. Unquantizable magnitudes still fail.
    ``strict`` follows the same emit-only exact-representation rule as
    :func:`_normalize_money`."""
    pq = _parse_and_quantize(value)
    if pq is None:
        return None
    parsed, quantized = pq
    if strict and parsed != quantized:
        return None
    return quantized


def canonical_account_id(value: object) -> str | None:
    """Normalize an account_public_id to ONE canonical string form (lowercase
    hyphenated UUID) so UUID objects and equivalent strings resolve to the
    same AccountFacts entry. Returns None for malformed identifiers — input
    sanitation only, not a financial-policy decision: a malformed reference
    simply does not resolve (JE_ACCOUNT_UNKNOWN)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


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

    # Final P1 correction: mode="emit" requires every monetary value to
    # ALREADY be its canonical two-decimal representation (see
    # _normalize_money); apply/scanner keep the quantized interpretation.
    strict = mode == "emit"

    # The line CONTAINER must be a list before anything iterates it. A
    # JSON-valid payload like {"lines": 1} is a structural defect the same
    # family as a malformed line value — deterministic JE_AMOUNT_INVALID,
    # never a TypeError, and no line/account/balance/header logic runs on it.
    raw_lines = data.get("lines")
    if raw_lines is None:
        lines: list = []
    elif isinstance(raw_lines, list):
        lines = raw_lines
    else:
        violations.append(
            JournalViolation(
                JE_AMOUNT_INVALID,
                f"'lines' must be a list of line dicts, got {type(raw_lines).__name__}.",
            )
        )
        return violations

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

        # ---- account resolution + referential checks (both modes) ---------
        # Resolved FIRST because memo classification is derived from the
        # referenced account (Account.is_memo == account_type MEMO — the same
        # semantics the JournalEntry total properties use). The payload
        # is_memo_line flag is tolerated as historical metadata but is NOT
        # authoritative: a financial-account line flagged memo stays financial
        # (its amounts count, so a smuggled line surfaces as UNBALANCED/
        # HEADER_TOTAL_MISMATCH), and a memo-account line is memo even when
        # the flag is absent. Unknown/malformed/cross-company accounts are
        # never guessed to be memo. With account_facts=None (payload-only
        # evaluation) the flag is the only signal available and is used as-is.
        is_memo = bool(line.get("is_memo_line"))
        if account_facts is not None:
            account_public_id = line.get("account_public_id")
            canonical_id = canonical_account_id(account_public_id)
            facts = account_facts.get(canonical_id) if canonical_id is not None else None
            if facts is not None:
                is_memo = facts.is_memo
            else:
                is_memo = False  # unresolvable is never memo
            if facts is None:
                # Missing, malformed ("not-a-uuid"), or simply not found —
                # all mean the reference does not resolve.
                violations.append(
                    JournalViolation(
                        JE_ACCOUNT_UNKNOWN,
                        f"Account {account_public_id!r} does not resolve.",
                        line_no,
                    )
                )
            elif facts.company_id != company_id:
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

        # EVERY line — memo or financial — must satisfy the JournalLine
        # storage-shape contract: normalization (parse + quantize 0.01
        # half-even + representability) and one-sided/non-negative/non-zero
        # shape. Memo classification exempts a line ONLY from the financial
        # aggregates (count, sides, balance, header totals). A line that
        # fails normalization contributes to NOTHING further; only FINANCIAL
        # normalization failures suppress the derived balance checks (a bad
        # memo amount cannot corrupt financial totals it never enters).
        debit = _normalize_money(line.get("debit", "0"), strict=strict)
        credit = _normalize_money(line.get("credit", "0"), strict=strict)
        if debit is None or credit is None:
            if not is_memo:
                amounts_clean = False
            violations.append(
                JournalViolation(
                    JE_AMOUNT_INVALID,
                    "Line debit/credit is not a finite, representable ledger decimal.",
                    line_no,
                )
            )
            continue

        if debit < 0 or credit < 0:
            violations.append(JournalViolation(JE_LINE_NEGATIVE, "Line amount is negative.", line_no))
        elif debit > 0 and credit > 0:
            violations.append(JournalViolation(JE_LINE_TWO_SIDED, "Line has both debit and credit populated.", line_no))
        elif debit == 0 and credit == 0:
            violations.append(JournalViolation(JE_LINE_ZERO, "Line has zero debit and zero credit.", line_no))

        if is_memo:
            continue  # shape-valid memo line: excluded from financial aggregates
        financial_count += 1
        financial.append((line_no, debit, credit))

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
        # Sums of per-line-quantized values are exact at 2dp — no further
        # rounding is applied, so the totals equal what the stored lines
        # would sum to after materialization.
        total_debit = sum((d for _n, d, _c in financial), Decimal("0"))
        total_credit = sum((c for _n, _d, c in financial), Decimal("0"))

        if not any(d > 0 for _n, d, _c in financial):
            violations.append(JournalViolation(JE_NO_DEBIT_SIDE, "No line carries a debit."))
        if not any(c > 0 for _n, _d, c in financial):
            violations.append(JournalViolation(JE_NO_CREDIT_SIDE, "No line carries a credit."))

        if total_debit != total_credit:
            violations.append(
                JournalViolation(
                    JE_UNBALANCED,
                    f"Debits {total_debit} != credits {total_credit} "
                    "(each line quantized to 0.01 half-even before summing).",
                )
            )

        header_debit = _normalize_header_total(data.get("total_debit"), strict=strict)
        header_credit = _normalize_header_total(data.get("total_credit"), strict=strict)
        if header_debit is None or header_credit is None:
            violations.append(
                JournalViolation(
                    JE_AMOUNT_INVALID,
                    "Header total_debit/total_credit is not a finite, representable ledger decimal.",
                )
            )
        elif header_debit != total_debit or header_credit != total_credit:
            violations.append(
                JournalViolation(
                    JE_HEADER_TOTAL_MISMATCH,
                    f"Header totals ({header_debit}/{header_credit}) do not equal "
                    f"calculated non-memo line totals ({total_debit}/{total_credit}).",
                )
            )

    return violations


# --------------------------------------------------------------------------- #
# The one raising emit-preparation boundary (A3-PR2) and its typed exception
# --------------------------------------------------------------------------- #


class PostedJournalInvalid(Exception):
    """Raised at the emit boundary when a JOURNAL_ENTRY_POSTED payload fails
    the canonical emit-mode invariant.

    Raised by :func:`prepare_posted_journal_for_emit`. Carries the structured
    :class:`JournalViolation` list so callers act on
    STABLE CODES — never by parsing the message. ``str(exc)`` contains only
    the deduplicated codes (no amounts, memos, or account identifiers), so
    the exception is safe to log and to surface verbatim."""

    def __init__(self, violations: list[JournalViolation], *, company_id: int) -> None:
        self.violations = list(violations)
        self.codes: list[str] = [v.code for v in self.violations]
        self.company_id = company_id
        unique_codes = ", ".join(dict.fromkeys(self.codes))
        super().__init__(f"Posted journal payload failed the canonical invariant: {unique_codes}")

    def as_dicts(self) -> list[dict]:
        return [v.as_dict() for v in self.violations]


def prepare_posted_journal_for_emit(company, data: Mapping) -> dict:
    """THE emit boundary (A3-PR2, final P1 correction): produce the EXACT
    canonical payload that will be emitted as JOURNAL_ENTRY_POSTED — or
    raise :class:`PostedJournalInvalid`.

    The invariant must validate the exact ledger representation that is
    emitted and later applied, so validate-then-emit-raw is forbidden:
    every emitter passes THIS function's return value to ``emit_event``.

    Contract:

    - ONE account-facts query covers every well-formed referenced account id
      (malformed references are sanitized out by :func:`load_account_facts`
      and therefore stay unresolved → ``JE_ACCOUNT_UNKNOWN``); facts are
      never loaded twice;
    - the caller's payload object is NEVER mutated — the returned payload is
      a copy in which ONLY the authoritative derived field ``is_memo_line``
      is normalized: for every RESOLVED account the emitted flag equals
      ``AccountFacts.is_memo`` (MEMO type OR STATISTICAL/OFF_BALANCE ledger
      domain), regardless of any caller-supplied value or type. Unresolved
      accounts get no invented memo truth (their lines keep the caller value
      and validation fails with the existing referential violations).
      Nothing else — identity, idempotency, account ids, line numbers,
      memos, references, source metadata, counterparties, provider fields —
      is touched;
    - monetary amounts are NOT silently normalized: mode="emit" REJECTS any
      amount that is not already its exact two-decimal canonical value
      (``JE_AMOUNT_INVALID``);
    - full ``mode="emit"`` policy runs against the exact prepared payload:
      active + postable accounts, exact canonical balance, zero tolerance
      (founder decision D3);
    - consults NO settings flag — TESTING / DISABLE_EVENT_VALIDATION do not
      exist at this boundary and never will;
    - violation ordering is deterministic (inherited from
      :func:`check_posted_journal`).
    """
    raw_lines = data.get("lines")
    referenced: list[object] = []
    if isinstance(raw_lines, list):
        referenced = [line.get("account_public_id") for line in raw_lines if isinstance(line, Mapping)]
    facts = load_account_facts(company, referenced)

    prepared = dict(data)
    if isinstance(raw_lines, list):
        prepared_lines: list = []
        for line in raw_lines:
            if isinstance(line, Mapping):
                new_line = dict(line)
                canonical_id = canonical_account_id(new_line.get("account_public_id"))
                line_facts = facts.get(canonical_id) if canonical_id is not None else None
                if line_facts is not None:
                    new_line["is_memo_line"] = line_facts.is_memo
                prepared_lines.append(new_line)
            else:
                prepared_lines.append(line)
        prepared["lines"] = prepared_lines

    violations = check_posted_journal(prepared, company_id=company.id, account_facts=facts, mode="emit")
    if violations:
        raise PostedJournalInvalid(violations, company_id=company.id)
    return prepared


# --------------------------------------------------------------------------- #
# The one narrow ORM helper (no validation policy of its own)
# --------------------------------------------------------------------------- #


def load_account_facts(company, account_public_ids) -> dict[str, AccountFacts]:
    """Load :class:`AccountFacts` for the given ``account_public_id`` values,
    keyed by the canonical string form (see :func:`canonical_account_id`).

    Malformed identifiers (anything that is not a UUID) are sanitized OUT
    before the ``UUIDField __in`` lookup — they can never resolve, so they
    simply stay absent from the result and the invariant reports
    ``JE_ACCOUNT_UNKNOWN``. One company-agnostic query by public_id
    (cross-company detection requires seeing accounts from OTHER companies,
    so the query is deliberately not company-scoped — the pure function does
    the company comparison). Contains no policy: it only reports what exists.
    """
    from accounting.models import Account  # lazy: keeps module import Django-free

    ids = {cid for cid in (canonical_account_id(a) for a in account_public_ids) if cid is not None}
    if not ids:
        return {}
    facts: dict[str, AccountFacts] = {}
    rows = Account.objects.filter(public_id__in=sorted(ids)).values_list(
        "public_id", "company_id", "status", "is_header", "account_type", "ledger_domain"
    )
    memo_domains = {Account.LedgerDomain.STATISTICAL, Account.LedgerDomain.OFF_BALANCE}
    for public_id, company_id, status, is_header, account_type, ledger_domain in rows:
        is_active = status == Account.Status.ACTIVE
        key = canonical_account_id(public_id) or str(public_id)
        facts[key] = AccountFacts(
            public_id=key,
            company_id=company_id,
            is_active=is_active,
            is_postable=is_active and not is_header,
            # Mirrors Account.is_memo_account exactly (models.py:608-613): the
            # legacy MEMO type OR a non-financial ledger domain (STATISTICAL /
            # OFF_BALANCE) — computed from the fetched fields so the pure
            # invariant never touches the model.
            is_memo=(account_type == Account.AccountType.MEMO or ledger_domain in memo_domains),
        )
    return facts
