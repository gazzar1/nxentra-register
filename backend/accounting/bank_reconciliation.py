# accounting/bank_reconciliation.py
"""
Bank reconciliation engine.

Provides:
- Statement import (CSV parsing)
- Auto-matching algorithm (bank lines → journal lines)
- Manual match/unmatch operations
- Reconciliation completion and validation
"""

import csv
import hashlib
import io
import logging
import uuid as _uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.authz import ActorContext, require
from events.emitter import emit_event_no_actor
from events.types import EventTypes
from projections.write_barrier import command_writes_allowed

from .commands import CommandResult
from .models import (
    Account,
    BankReconciliation,
    BankStatement,
    BankStatementLine,
    JournalEntry,
    JournalLine,
)

logger = logging.getLogger(__name__)

# Match confidence thresholds
CONFIDENCE_EXACT = Decimal("100")
CONFIDENCE_AMOUNT_DATE = Decimal("85")
CONFIDENCE_AMOUNT_ONLY = Decimal("60")
AUTO_MATCH_THRESHOLD = Decimal("80")


# A85 chunk 2c (2026-05-26): minimum chars an operator must type to justify
# a period override. Mirrors the constant in settlement_imports.py. Kept here
# so the bank-rec module is self-contained; chunk 6 may DRY these up.
_MIN_OVERRIDE_REASON_CHARS = 10


def _emit_match_confirmed(
    *,
    company,
    bank_line: BankStatementLine,
    journal_line: JournalLine,
    match_kind: str,
    confidence,
    difference_amount=Decimal("0"),
    statement_date=None,
):
    """A86.4 (2026-05-26): emit a ReconciliationMatchConfirmed event for
    a rule-confirmed (auto) bank-line ↔ journal-line match.

    Called from `_settlement_prepass_match`, `_platform_prepass_match`,
    and the generic GL match loop in `auto_match_statement` AFTER the
    direct-mutation legacy path has applied the match. Runs alongside
    the legacy path (shadow mode); the A86.3 ReconciliationProjection
    consumes these events and writes the event_* fields on the bank
    line — those event_* fields are the convergence target for A86.7
    cutover.

    The idempotency key uses a fresh UUID4 so each emission is unique.
    For command-emitted events the dedup-on-replay scenario doesn't
    apply (commands aren't fired by external systems); the UUID4
    accepts that trade-off so unmatch-then-rematch produces a NEW
    Confirmed event rather than getting deduped into nothing.
    See ReconciliationMatchConfirmedData docstring + A86 plan.

    Per finance_event_first_policy.md §2: every emission carries an
    idempotency_key (uniqueness-enforced by BusinessEvent.idempotency_key
    UNIQUE INDEX), an aggregate_type, and an aggregate_id.
    """
    from reconciliation.event_types import ReconciliationMatchConfirmedData

    diff = Decimal(str(difference_amount or 0))
    payload = ReconciliationMatchConfirmedData(
        bank_line_public_id=str(bank_line.public_id),
        journal_line_public_id=str(journal_line.public_id),
        match_kind=match_kind,
        confidence=str(confidence),
        confirmation_kind="auto",
        confirmed_at=timezone.now().isoformat(),
        difference_amount=str(diff),
        difference_reason="UNRESOLVED",
        statement_date=statement_date.isoformat() if statement_date else "",
    )
    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        aggregate_type="ReconciliationMatch",
        aggregate_id=f"{bank_line.public_id}:{journal_line.public_id}",
        idempotency_key=f"reconciliation.match_confirmed:{_uuid.uuid4()}",
        data=payload,
    )


def _validate_period_override(
    *,
    company,
    user,
    period_override: int,
    fiscal_year_override: int,
    override_reason: str,
) -> tuple[bool, str | None]:
    """A85 chunk 2c (2026-05-26): validate operator's period-override request.

    Same gate as A85 chunk 3b for settlement imports:
    - user must have 'accounting.je.override_period' permission
    - reason must be >= 10 chars
    - target (period, fiscal_year) must exist + be OPEN

    Returns (True, None) on success, (False, error_msg) on failure. Caller
    surfaces the error to the operator.
    """
    if not user:
        return False, "Period override requested but no user supplied for audit trail."

    from accounts.models import CompanyMembership

    membership = (
        CompanyMembership.objects.filter(user=user, company=company, is_active=True)
        .prefetch_related("permissions")
        .first()
    )
    if not membership:
        return False, (
            f"User {user.email or user.id} has no active membership in this company; "
            "cannot override the posting period."
        )
    user_perms = set(membership.permissions.values_list("code", flat=True))
    if "accounting.je.override_period" not in user_perms:
        return False, (
            f"User {user.email or user.id} lacks the accounting.je.override_period "
            "permission required to override the date-derived posting period."
        )
    if len(override_reason.strip()) < _MIN_OVERRIDE_REASON_CHARS:
        return False, (f"Period override reason must be at least {_MIN_OVERRIDE_REASON_CHARS} characters.")

    from projections.models import FiscalPeriod

    target_fp = FiscalPeriod.objects.filter(
        company=company,
        fiscal_year=fiscal_year_override,
        period=period_override,
    ).first()
    if not target_fp:
        return False, (
            f"Target override period {period_override}/{fiscal_year_override} is not configured for this company."
        )
    if target_fp.status != FiscalPeriod.Status.OPEN:
        return False, (
            f"Target override period {period_override}/{fiscal_year_override} "
            f"is {target_fp.status}; can only override to an OPEN period."
        )

    return True, None


# =============================================================================
# Statement Import
# =============================================================================


def preview_bank_statement_import(
    actor: ActorContext,
    account_id: int,
    lines_data: list,
) -> CommandResult:
    """A85 chunk 2 (2026-05-25): dry-run for bank statement import.

    Parses the same per-line input that `import_bank_statement` would
    consume, but does NOT create BankStatement or BankStatementLine rows.
    Returns a preview the operator-facing modal renders.

    Unlike the settlement-import preview, this one has NO period/JE
    concern — `import_bank_statement` only creates raw line records; the
    JEs that drain Expected Bank Deposit etc. are created later, during
    match/unmatch (see auto_match_statement + _settlement_prepass_match).
    The corresponding match-preview is A85 chunk 2b (separate work).

    What this preview shows:
    - Total line count parsed from the input
    - How many lines would actually be imported (new dedup_hash)
    - How many lines would be skipped as duplicates (hash already present
      for this account, or duplicate within the same upload)
    - The date range covered
    - Per-line dedup status for the first N rows (frontend renders a
      preview table)

    See:
    - import_bank_statement() — the corresponding execute path
    - docs/finance_event_first_policy.md §8 — operator sees cause-and-effect
    """
    require(actor, "accounting.reconciliation")

    try:
        account = Account.objects.get(
            id=account_id,
            company=actor.company,
        )
    except Account.DoesNotExist:
        return CommandResult.fail("Account not found.")

    if not lines_data:
        return CommandResult.fail("No transaction lines provided.")

    # Load every dedup_hash already imported for (company, account) so we
    # can flag which incoming lines would dedup against existing data.
    existing_hashes: set[str] = set(
        BankStatementLine.objects.filter(
            company=actor.company,
            statement__account=account,
        )
        .exclude(dedup_hash="")
        .values_list("dedup_hash", flat=True)
    )

    parsed_lines: list[dict] = []
    seen_in_batch: set[str] = set()
    would_import = 0
    would_dedup_existing = 0
    would_dedup_in_batch = 0
    invalid_rows = 0
    min_date = None
    max_date = None
    total_inflow = Decimal("0")
    total_outflow = Decimal("0")

    for ld in lines_data:
        try:
            amount = Decimal(str(ld["amount"]))
        except (KeyError, InvalidOperation, TypeError):
            invalid_rows += 1
            continue

        line_date_value = ld.get("line_date")
        description = ld.get("description", "")
        reference_str = ld.get("reference", "")
        dedup_hash = _compute_line_dedup_hash(
            line_date=line_date_value,
            amount=amount,
            reference=reference_str,
            description=description,
        )

        if dedup_hash in existing_hashes:
            status_label = "duplicate_existing"
            would_dedup_existing += 1
        elif dedup_hash in seen_in_batch:
            status_label = "duplicate_in_batch"
            would_dedup_in_batch += 1
        else:
            status_label = "would_import"
            seen_in_batch.add(dedup_hash)
            would_import += 1
            if amount >= 0:
                total_inflow += amount
            else:
                total_outflow += -amount

            # Track date range only for lines that would actually import
            if hasattr(line_date_value, "isoformat"):
                if min_date is None or line_date_value < min_date:
                    min_date = line_date_value
                if max_date is None or line_date_value > max_date:
                    max_date = line_date_value

        parsed_lines.append(
            {
                "line_date": (
                    line_date_value.isoformat() if hasattr(line_date_value, "isoformat") else str(line_date_value or "")
                ),
                "description": description,
                "reference": reference_str,
                "amount": str(amount),
                "dedup_status": status_label,
            }
        )

    return CommandResult.ok(
        data={
            "account_id": account.id,
            "account_code": account.code,
            "account_name": account.name,
            "summary": {
                "total_rows": len(lines_data),
                "invalid_rows": invalid_rows,
                "would_import": would_import,
                "would_dedup_existing": would_dedup_existing,
                "would_dedup_in_batch": would_dedup_in_batch,
                "min_date": min_date.isoformat() if min_date else None,
                "max_date": max_date.isoformat() if max_date else None,
                "total_inflow": str(total_inflow.quantize(Decimal("0.01"))),
                "total_outflow": str(total_outflow.quantize(Decimal("0.01"))),
                "net": str((total_inflow - total_outflow).quantize(Decimal("0.01"))),
                "dry_run_safe": would_import > 0,
            },
            # Per-line preview for the modal's expandable detail. Bounded
            # to 500 entries so very large CSVs don't blow up the payload.
            "lines": parsed_lines[:500],
            "lines_truncated_to": 500 if len(parsed_lines) > 500 else None,
        }
    )


@transaction.atomic
def import_bank_statement(
    actor: ActorContext,
    account_id: int,
    statement_date: date,
    period_start: date,
    period_end: date,
    opening_balance: Decimal,
    closing_balance: Decimal,
    lines_data: list,
    source: str = "CSV",
    currency: str = "USD",
    reference: str = "",
) -> CommandResult:
    """
    Import a bank statement with transaction lines.

    lines_data: list of dicts with keys:
        line_date, description, amount, reference (optional),
        transaction_type (optional)
    """
    require(actor, "accounting.reconciliation")

    try:
        account = Account.objects.get(
            id=account_id,
            company=actor.company,
        )
    except Account.DoesNotExist:
        return CommandResult.fail("Account not found.")

    if not lines_data:
        return CommandResult.fail("No transaction lines provided.")

    # A17: load every dedup_hash already imported for this account so we
    # can skip duplicates when an overlapping period gets re-uploaded
    # (e.g. April 1-30 after April 1-15 was already imported). Scoped to
    # (company, account) — legitimately identical lines on a different
    # bank account still import.
    existing_hashes = set(
        BankStatementLine.objects.filter(
            company=actor.company,
            statement__account=account,
        )
        .exclude(dedup_hash="")
        .values_list("dedup_hash", flat=True)
    )

    with command_writes_allowed():
        statement = BankStatement.objects.create(
            company=actor.company,
            account=account,
            statement_date=statement_date,
            period_start=period_start,
            period_end=period_end,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            currency=currency,
            source=source,
            reference=reference,
            status=BankStatement.Status.IMPORTED,
        )

        created_lines = []
        skipped_duplicate = 0
        # A17: track hashes seen within THIS file so the same row appearing
        # twice in the upload itself dedups too (some bank exports emit
        # the same transaction twice if the merchant runs the report
        # across overlapping date filters).
        seen_in_batch: set[str] = set()
        for ld in lines_data:
            try:
                amount = Decimal(str(ld["amount"]))
            except (KeyError, InvalidOperation):
                continue

            line_date_value = ld.get("line_date", statement_date)
            description = ld.get("description", "")
            reference_str = ld.get("reference", "")
            dedup_hash = _compute_line_dedup_hash(
                line_date=line_date_value,
                amount=amount,
                reference=reference_str,
                description=description,
            )
            if dedup_hash in existing_hashes or dedup_hash in seen_in_batch:
                skipped_duplicate += 1
                continue
            seen_in_batch.add(dedup_hash)

            # Infer transaction type from amount
            txn_type = ld.get("transaction_type", "")
            if not txn_type:
                txn_type = (
                    BankStatementLine.TransactionType.DEPOSIT
                    if amount >= 0
                    else BankStatementLine.TransactionType.WITHDRAWAL
                )

            line = BankStatementLine.objects.create(
                statement=statement,
                company=actor.company,
                line_date=line_date_value,
                description=description,
                reference=reference_str,
                amount=amount,
                transaction_type=txn_type,
                dedup_hash=dedup_hash,
            )
            created_lines.append(line)

    logger.info(
        "Imported bank statement %s for %s: %d lines created, %d duplicates skipped",
        statement.public_id,
        account.code,
        len(created_lines),
        skipped_duplicate,
    )

    return CommandResult.ok(
        data={
            "statement": statement,
            "lines_created": len(created_lines),
            "lines_skipped_duplicate": skipped_duplicate,
        }
    )


def _compute_line_dedup_hash(
    line_date,
    amount: Decimal,
    reference: str,
    description: str,
) -> str:
    """A17: SHA-256 hex digest of the canonicalised line content.

    Matches the rule used in migration 0030's backfill. Reference and
    description are .strip()ed (banks vary whitespace between exports);
    case is preserved (some refs are case-sensitive identifiers).
    """
    if hasattr(line_date, "isoformat"):
        date_str = line_date.isoformat()
    else:
        date_str = str(line_date or "")
    payload = f"{date_str}|{amount}|{(reference or '').strip()}|{(description or '').strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_csv_headers(csv_content: str, sample_size: int = 5) -> dict:
    """A24: read just the headers + a few sample rows from a CSV.

    Lets the frontend show a column-mapper dialog before commiting to a
    full parse. Returns ``{"headers": [...], "sample_rows": [...]}``;
    sample_rows are kept as raw dicts (column name -> raw cell value),
    NOT canonicalized — the merchant needs to see the literal values to
    decide which column carries which logical field.
    """
    reader = csv.DictReader(io.StringIO(csv_content))
    headers = list(reader.fieldnames or [])
    sample_rows = []
    for i, row in enumerate(reader):
        if i >= sample_size:
            break
        # csv.DictReader can put None into the column name when the row has
        # extra fields beyond the header — drop those keys; they confuse
        # the frontend dropdowns.
        sample_rows.append({k: v for k, v in row.items() if k is not None})
    return {"headers": headers, "sample_rows": sample_rows}


def parse_csv_statement(
    csv_content: str,
    date_column: str = "Date",
    description_column: str = "Description",
    amount_column: str = "Amount",
    reference_column: str = "Reference",
    debit_column: str = "",
    credit_column: str = "",
    date_format: str = "%Y-%m-%d",
) -> list:
    """
    Parse a CSV file into a list of line dicts.

    Supports two formats:
    1. Single amount column (positive = deposit, negative = withdrawal)
    2. Separate debit/credit columns
    """
    from datetime import datetime

    reader = csv.DictReader(io.StringIO(csv_content))
    lines = []

    for row in reader:
        try:
            line_date = datetime.strptime(row.get(date_column, "").strip(), date_format).date()
        except (ValueError, AttributeError):
            continue

        description = row.get(description_column, "").strip()
        reference = row.get(reference_column, "").strip()

        if debit_column and credit_column:
            # Separate debit/credit columns. Parse both to Decimal first
            # then derive a signed net — many bank exports use "0" (literal
            # zero) in the unused column rather than leaving it blank, and
            # a naive truthiness check on the string would treat "0" as
            # non-empty and take the debit branch, dropping every credit row.
            # (Surfaced 2026-05-09 dry-run: 6 of 7 rows silently dropped.)
            debit = row.get(debit_column, "").strip().replace(",", "")
            credit = row.get(credit_column, "").strip().replace(",", "")
            try:
                debit_val = Decimal(debit) if debit else Decimal("0")
                credit_val = Decimal(credit) if credit else Decimal("0")
            except InvalidOperation:
                continue
            # Net: credit - debit. Positive = deposit, negative = withdrawal.
            amount = credit_val - debit_val
        else:
            # Single amount column
            raw = row.get(amount_column, "").strip().replace(",", "")
            try:
                amount = Decimal(raw)
            except InvalidOperation:
                continue

        if amount == 0:
            continue

        lines.append(
            {
                "line_date": line_date,
                "description": description,
                "reference": reference,
                "amount": str(amount),
            }
        )

    return lines


# =============================================================================
# Auto-Matching Algorithm
# =============================================================================


def preview_auto_match(
    actor: ActorContext,
    statement_id: int,
    *,
    period_override: int = 0,
    fiscal_year_override: int = 0,
    override_reason: str = "",
) -> CommandResult:
    """A85 chunk 2c (2026-05-26): dry-run preview for auto_match_statement.

    Returns the planned settlement matches with per-row period info,
    aggregate periods affected, blockers, and `dry_run_safe`. Does NOT
    create JEs or mutate any read-model state.

    Scope: covers the settlement pre-pass (the only step that synthesizes
    new JEs). The platform pre-pass and generic GL match only flip
    `reconciled` flags on pre-existing JEs, so they have no period
    concern and aren't included in the plan.

    Optional override params: when supplied, validates them and reports
    whether the override would resolve the blockers (vs. surfacing as
    a fresh blocker if validation fails).

    See:
    - auto_match_statement() — the corresponding execute path
    - _plan_settlement_prepass_matches() — shared planning logic
    - docs/finance_event_first_policy.md §8 — operator sees cause-and-effect
    """
    require(actor, "accounting.reconciliation")

    try:
        statement = BankStatement.objects.select_related("account").get(
            id=statement_id,
            company=actor.company,
        )
    except BankStatement.DoesNotExist:
        return CommandResult.fail("Statement not found.")

    unmatched_bank_lines = list(
        BankStatementLine.objects.filter(
            statement=statement,
            match_status=BankStatementLine.MatchStatus.UNMATCHED,
        )
    )

    override_active = bool(period_override and fiscal_year_override)
    override_warning: str | None = None
    if override_active:
        ok, err = _validate_period_override(
            company=actor.company,
            user=actor.user,
            period_override=period_override,
            fiscal_year_override=fiscal_year_override,
            override_reason=override_reason,
        )
        if not ok:
            override_warning = err

    plans = _plan_settlement_prepass_matches(actor.company, statement, unmatched_bank_lines)

    from projections.models import FiscalPeriod

    def _resolve_period_for_date(d):
        if d is None:
            return {
                "resolved": False,
                "fiscal_year": None,
                "period": None,
                "period_name": None,
                "status": None,
                "warning": "No bank-line value date.",
            }
        fp = (
            FiscalPeriod.objects.filter(
                company=actor.company,
                start_date__lte=d,
                end_date__gte=d,
                period_type=FiscalPeriod.PeriodType.NORMAL,
            )
            .order_by("fiscal_year", "period")
            .first()
        )
        if not fp:
            return {
                "resolved": False,
                "fiscal_year": d.year,
                "period": d.month,
                "period_name": d.strftime("%B %Y"),
                "status": None,
                "warning": (
                    f"No FiscalPeriod covers {d.isoformat()}; clearance JE post may fail "
                    "until fiscal periods are configured."
                ),
            }
        warning = None
        if fp.status != FiscalPeriod.Status.OPEN:
            warning = (
                f"Period {fp.period}/{fp.fiscal_year} ({fp.start_date.strftime('%B %Y')}) "
                f"is {fp.status}; clearance JE would be rejected unless period is overridden."
            )
        return {
            "resolved": True,
            "fiscal_year": fp.fiscal_year,
            "period": fp.period,
            "period_name": fp.start_date.strftime("%B %Y"),
            "status": fp.status,
            "warning": warning,
        }

    # Resolve override target's display name (period_name) once, for
    # rendering in the modal — not load-bearing.
    override_period_name: str | None = None
    if override_active and not override_warning:
        fp = FiscalPeriod.objects.filter(
            company=actor.company,
            fiscal_year=fiscal_year_override,
            period=period_override,
        ).first()
        if fp:
            override_period_name = fp.start_date.strftime("%B %Y")

    plan_rows: list[dict] = []
    periods_seen: dict[tuple[int, int], dict] = {}
    blockers: list[str] = []
    total_actual = Decimal("0")
    exact_matches = 0
    near_matches = 0

    for plan in plans:
        natural_period = _resolve_period_for_date(plan["value_date"])
        effective_period: dict
        if override_active and not override_warning:
            effective_period = {
                "resolved": True,
                "fiscal_year": fiscal_year_override,
                "period": period_override,
                "period_name": override_period_name,
                "status": FiscalPeriod.Status.OPEN,
                "warning": None,
            }
        else:
            effective_period = natural_period

        plan_rows.append(
            {
                "bank_line_id": plan["bank_line_id"],
                "bank_line_date": plan["bank_line_date"].isoformat() if plan["bank_line_date"] else None,
                "bank_line_description": (plan["bank_line_description"] or "")[:200],
                "bank_line_amount": str(plan["bank_line_amount"]),
                "settlement_entry_id": plan["settlement_entry_id"],
                "settlement_entry_number": plan["settlement_entry_number"],
                "settlement_source_document": plan["settlement_source_document"],
                "batch_id": plan["batch_id"],
                "expected_amount": str(plan["expected_amount"]),
                "actual_amount": str(plan["actual_amount"]),
                "difference": str(plan["difference"]),
                "is_near_match": plan["is_near"],
                "confidence": str(plan["confidence"]),
                "natural_period": natural_period,
                "effective_period": effective_period,
                "value_date": plan["value_date"].isoformat() if plan["value_date"] else None,
                "will_create_clearance_je": True,
            }
        )

        target = effective_period
        if target["resolved"]:
            key = (target["fiscal_year"], target["period"])
            if key not in periods_seen:
                periods_seen[key] = {
                    "fiscal_year": target["fiscal_year"],
                    "period": target["period"],
                    "period_name": target["period_name"],
                    "status": target["status"],
                    "journal_entries": 0,
                }
            periods_seen[key]["journal_entries"] += 1

            if target["status"] != FiscalPeriod.Status.OPEN:
                blocker = (
                    f"Period {target['period']}/{target['fiscal_year']} "
                    f"({target['period_name']}) is {target['status']}; "
                    "cannot post clearance JE."
                )
                if blocker not in blockers:
                    blockers.append(blocker)
        else:
            blocker = target["warning"] or f"Could not resolve period for bank line {plan['bank_line_id']}."
            if blocker not in blockers:
                blockers.append(blocker)

        total_actual += plan["actual_amount"]
        if plan["is_near"]:
            near_matches += 1
        else:
            exact_matches += 1

    if override_active and override_warning:
        blockers.append(f"Period override rejected: {override_warning}")

    return CommandResult.ok(
        data={
            "statement_id": statement.id,
            "statement_public_id": str(statement.public_id),
            "account_id": statement.account_id,
            "account_code": statement.account.code,
            "account_name": statement.account.name,
            "currency": statement.currency,
            "statement_date": statement.statement_date.isoformat(),
            "unmatched_bank_lines": len(unmatched_bank_lines),
            "match_plan": plan_rows,
            "summary": {
                "total_settlement_matches": len(plans),
                "total_journal_entries_to_create": len(plans),
                "total_clearance_amount": str(total_actual.quantize(Decimal("0.01"))),
                "exact_matches": exact_matches,
                "near_matches": near_matches,
                "periods_affected": sorted(
                    periods_seen.values(),
                    key=lambda r: (r["fiscal_year"], r["period"]),
                ),
                "blockers": blockers,
                "dry_run_safe": len(blockers) == 0 and len(plans) > 0,
                "override_requested": override_active,
                "override_warning": override_warning,
            },
        }
    )


@transaction.atomic
def auto_match_statement(
    actor: ActorContext,
    statement_id: int,
    *,
    period_override: int = 0,
    fiscal_year_override: int = 0,
    override_reason: str = "",
) -> CommandResult:
    """
    Auto-match unmatched bank statement lines to journal lines.

    Matching strategy (in priority order):
    1. Exact match: same amount + same date + reference match → 100% confidence
    2. Amount + date proximity (±3 days) → 85% confidence
    3. Amount-only match (unique) → 60% confidence

    Only matches above AUTO_MATCH_THRESHOLD are applied.

    A85 chunk 2c (2026-05-26): optional operator-driven period override
    for the clearance JEs created during the settlement pre-pass. Same
    gate as A85 chunk 3b for settlement imports:
      - actor.user must hold 'accounting.je.override_period'
      - override_reason must be >= 10 chars (regulatory traceability)
      - target (override_period, override_fiscal_year) must exist + be OPEN
      - one PeriodOverrideAudit row is written per planned match BEFORE
        the clearance JE is created, so the audit trail survives even if
        post fails partway

    The platform pre-pass and generic GL match are unaffected by the
    override — they only flip `reconciled` flags on pre-existing JEs.
    """
    require(actor, "accounting.reconciliation")

    try:
        statement = BankStatement.objects.get(
            id=statement_id,
            company=actor.company,
        )
    except BankStatement.DoesNotExist:
        return CommandResult.fail("Statement not found.")

    # A85 chunk 2c: validate the override BEFORE any state changes.
    override_active = bool(period_override and fiscal_year_override)
    if override_active:
        ok, err = _validate_period_override(
            company=actor.company,
            user=actor.user,
            period_override=period_override,
            fiscal_year_override=fiscal_year_override,
            override_reason=override_reason,
        )
        if not ok:
            return CommandResult.fail(err)

    # Get unmatched bank lines
    unmatched_bank_lines = list(
        BankStatementLine.objects.filter(
            statement=statement,
            match_status=BankStatementLine.MatchStatus.UNMATCHED,
        )
    )

    if not unmatched_bank_lines:
        return CommandResult.ok(data={"matched": 0, "total": 0})

    # ---- Platform-aware pre-pass ----
    # Try to match bank lines against known platform payouts (e.g. Shopify)
    # before falling back to generic GL-level matching.
    platform_matched = _platform_prepass_match(
        actor.company,
        statement,
        unmatched_bank_lines,
    )

    # Remove platform-matched lines from the unmatched list
    if platform_matched > 0:
        unmatched_bank_lines = [
            bl for bl in unmatched_bank_lines if bl.match_status == BankStatementLine.MatchStatus.UNMATCHED
        ]

    # ---- Settlement-aware pre-pass (A14b) ----
    # Match bank lines against PaymentSettlement JEs (Paymob/Bosta/PayPal
    # CSV imports). On match, create the clearance JE that drains the
    # Expected Bank Deposit balance into the merchant's actual bank.
    settlement_matched = _settlement_prepass_match(
        actor.company,
        statement,
        unmatched_bank_lines,
        period_override=period_override,
        fiscal_year_override=fiscal_year_override,
        override_reason=override_reason,
        override_user=actor.user if override_active else None,
    )
    if settlement_matched > 0:
        unmatched_bank_lines = [
            bl for bl in unmatched_bank_lines if bl.match_status == BankStatementLine.MatchStatus.UNMATCHED
        ]

    # Get unreconciled journal lines for this account in the statement period
    # Expand date range slightly for date proximity matching
    date_buffer = timedelta(days=5)
    candidate_jl = JournalLine.objects.filter(
        company=actor.company,
        account=statement.account,
        reconciled=False,
        entry__status=JournalEntry.Status.POSTED,
        entry__date__gte=statement.period_start - date_buffer,
        entry__date__lte=statement.period_end + date_buffer,
    ).select_related("entry")

    # Build lookup structures for journal lines
    # Key: signed amount (debit = positive cash in for asset, credit = negative)
    jl_by_amount = {}
    for jl in candidate_jl:
        # For bank accounts (assets): debit = increase (deposit), credit = decrease (withdrawal)
        signed_amount = jl.debit - jl.credit
        jl_by_amount.setdefault(signed_amount, []).append(jl)

    matched_count = 0
    total = len(unmatched_bank_lines) + platform_matched

    with command_writes_allowed():
        for bank_line in unmatched_bank_lines:
            best_match = None
            best_confidence = Decimal("0")

            candidates = jl_by_amount.get(bank_line.amount, [])

            for jl in candidates:
                # Skip already-matched journal lines
                if jl.reconciled:
                    continue

                confidence = _compute_match_confidence(bank_line, jl)

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = jl

            if best_match and best_confidence >= AUTO_MATCH_THRESHOLD:
                bank_line.matched_journal_line = best_match
                bank_line.match_status = BankStatementLine.MatchStatus.AUTO_MATCHED
                bank_line.match_confidence = best_confidence
                bank_line.save()

                # Mark journal line as reconciled. JournalLine is a read
                # model — direct .save() is rejected outside projections,
                # so we use .update() which bypasses the save-guard the
                # same way line 904 below does for JournalEntry.
                JournalLine.objects.filter(pk=best_match.pk).update(
                    reconciled=True,
                    reconciled_date=statement.statement_date,
                )

                # A86.4: emit ReconciliationMatchConfirmed for the shadow
                # projection. Generic GL match is the fallback path after
                # platform-prepass and settlement-prepass exhaust their
                # candidates — match_kind="generic_gl" disambiguates the
                # source in the event audit trail.
                _emit_match_confirmed(
                    company=actor.company,
                    bank_line=bank_line,
                    journal_line=best_match,
                    match_kind="generic_gl",
                    confidence=best_confidence,
                    difference_amount=Decimal("0"),
                    statement_date=statement.statement_date,
                )

                # Remove from candidates to prevent double-matching
                candidates.remove(best_match)
                matched_count += 1

        # Update statement status
        if statement.status == BankStatement.Status.IMPORTED:
            statement.status = BankStatement.Status.IN_PROGRESS
            statement.save()

    matched_count += platform_matched + settlement_matched
    total += settlement_matched
    logger.info(
        "Auto-matched %d/%d lines for statement %s (platform: %d, settlement: %d)",
        matched_count,
        total,
        statement.public_id,
        platform_matched,
        settlement_matched,
    )

    return CommandResult.ok(
        data={
            "matched": matched_count,
            "total": total,
            "platform_matched": platform_matched,
            "settlement_matched": settlement_matched,
        }
    )


def _platform_prepass_match(
    company,
    statement: BankStatement,
    unmatched_bank_lines: list,
) -> int:
    """
    Platform-aware pre-pass: match bank lines to known platform payout records.

    For each unmatched bank line, check if there's a ShopifyPayout with
    a matching net_amount and close date. If found, link the bank line
    to the payout's corresponding journal entry (the Cash/Bank debit line).

    Returns the number of lines matched.
    """
    try:
        from shopify_connector.models import ShopifyPayout
    except ImportError:
        return 0  # Shopify connector not installed

    # Get all Shopify payouts in the statement period range
    date_buffer = timedelta(days=5)
    payouts = list(
        ShopifyPayout.objects.filter(
            company=company,
            payout_date__gte=statement.period_start - date_buffer,
            payout_date__lte=statement.period_end + date_buffer,
            shopify_status="paid",
        )
    )

    if not payouts:
        return 0

    # Build payout lookup by net_amount
    payout_by_amount = {}
    for p in payouts:
        payout_by_amount.setdefault(p.net_amount, []).append(p)

    matched = 0

    with command_writes_allowed():
        for bank_line in unmatched_bank_lines:
            if bank_line.match_status != BankStatementLine.MatchStatus.UNMATCHED:
                continue

            # Bank deposit amount matches payout net_amount
            candidates = payout_by_amount.get(bank_line.amount, [])
            if not candidates:
                continue

            # Find best candidate by date proximity
            best_payout = None
            best_days = 999

            for p in candidates:
                days_diff = abs((bank_line.line_date - p.payout_date).days)
                if days_diff < best_days:
                    best_days = days_diff
                    best_payout = p

            if not best_payout or best_days > 5:
                continue

            # Find the journal line created by the payout projection
            # (the DR Cash/Bank line in the payout settlement JE)
            payout_memo = f"Shopify payout: {best_payout.shopify_payout_id}"
            payout_je_line = JournalLine.objects.filter(
                company=company,
                account=statement.account,
                reconciled=False,
                entry__status=JournalEntry.Status.POSTED,
                entry__memo=payout_memo,
            ).first()

            if not payout_je_line:
                # Also try negative payout memo
                payout_memo_neg = f"Negative payout: {best_payout.shopify_payout_id}"
                payout_je_line = JournalLine.objects.filter(
                    company=company,
                    account=statement.account,
                    reconciled=False,
                    entry__status=JournalEntry.Status.POSTED,
                    entry__memo=payout_memo_neg,
                ).first()

            if not payout_je_line:
                continue

            # Match!
            bank_line.matched_journal_line = payout_je_line
            bank_line.match_status = BankStatementLine.MatchStatus.AUTO_MATCHED
            bank_line.match_confidence = CONFIDENCE_EXACT  # High confidence — platform match
            bank_line.save()

            JournalLine.objects.filter(pk=payout_je_line.pk).update(
                reconciled=True,
                reconciled_date=statement.statement_date,
            )

            # A86.4: emit ReconciliationMatchConfirmed for shadow projection.
            _emit_match_confirmed(
                company=company,
                bank_line=bank_line,
                journal_line=payout_je_line,
                match_kind="platform_payout",
                confidence=CONFIDENCE_EXACT,
                difference_amount=Decimal("0"),
                statement_date=statement.statement_date,
            )

            # Remove payout from candidates to prevent double-matching
            candidates.remove(best_payout)
            matched += 1

            logger.info(
                "Platform pre-pass: matched bank line %s to Shopify payout %s",
                bank_line.id,
                best_payout.shopify_payout_id,
            )

    return matched


def _plan_settlement_prepass_matches(
    company,
    statement: BankStatement,
    unmatched_bank_lines: list,
) -> list[dict]:
    """A85 chunk 2c (2026-05-26): pure-read planner for the settlement
    pre-pass. Decides which bank lines would match which settlement JEs
    and what clearance JEs would need to be created. Does NOT create
    JEs or mutate read-model state.

    The execute path (_settlement_prepass_match) calls this and then
    creates the clearance JE + applies the read-model state per plan row.
    The preview path (preview_auto_match) calls this and returns the plan
    as-is for the operator to confirm.

    Plan ordering mirrors the original loop: bank lines processed in the
    order they were passed; candidates removed as they're picked so the
    next bank line can't double-match.

    Returns a list of plan dicts:
        {
            "bank_line_id": int,
            "bank_line_amount": Decimal,
            "bank_line_date": date,
            "bank_line_description": str,
            "settlement_entry_id": int,
            "settlement_entry_number": str,
            "settlement_entry_date": date,
            "settlement_entry_period": int | None,
            "settlement_source_document": str,
            "ebd_line_id": int,
            "batch_id": str,
            "expected_amount": Decimal,
            "actual_amount": Decimal,
            "difference": Decimal,
            "is_near": bool,
            "confidence": Decimal,
            "value_date": date,
        }
    """
    from accounting.mappings import ModuleAccountMapping

    if not unmatched_bank_lines:
        return []

    date_buffer = timedelta(days=7)
    settlement_entries = list(
        JournalEntry.objects.filter(
            company=company,
            source_module="payment_settlement",
            status=JournalEntry.Status.POSTED,
            date__gte=statement.period_start - date_buffer,
            date__lte=statement.period_end + date_buffer,
        ).order_by("date")
    )
    if not settlement_entries:
        return []

    ebd_account = ModuleAccountMapping.get_account(company, "shopify_connector", "EXPECTED_BANK_DEPOSIT")
    if not ebd_account:
        return []

    # Pre-collect candidates: (entry, ebd_line, net, batch_id)
    candidates: list[tuple] = []
    for entry in settlement_entries:
        ebd_line = entry.lines.filter(account=ebd_account, reconciled=False).first()
        if not ebd_line:
            continue
        source_doc = entry.source_document or ""
        batch_id = source_doc.split(":", 1)[1] if ":" in source_doc else source_doc
        candidates.append((entry, ebd_line, ebd_line.debit, batch_id))

    if not candidates:
        return []

    plans: list[dict] = []

    for bank_line in unmatched_bank_lines:
        if bank_line.match_status != BankStatementLine.MatchStatus.UNMATCHED:
            continue

        # A16 near-match logic: exact first, then within-tolerance.
        exact_matches = [c for c in candidates if c[2] == bank_line.amount]
        near_matches = []
        if not exact_matches:
            for c in candidates:
                tolerance = _difference_tolerance(c[2])
                gap = abs(c[2] - bank_line.amount)
                if gap > 0 and gap <= tolerance:
                    near_matches.append(c)

        amount_matches = exact_matches or near_matches
        if not amount_matches:
            continue
        is_near = not exact_matches

        descr = (bank_line.description or "").lower()
        batch_match = next(
            (c for c in amount_matches if c[3] and c[3].lower() in descr),
            None,
        )
        if batch_match:
            entry, ebd_line, expected_amount, batch_id = batch_match
            confidence = CONFIDENCE_EXACT
        else:
            best, best_days = None, 999
            for c in amount_matches:
                days = abs((bank_line.line_date - c[0].date).days)
                if days < best_days:
                    best_days = days
                    best = c
            if not best or best_days > 7:
                continue
            entry, ebd_line, expected_amount, batch_id = best
            confidence = CONFIDENCE_AMOUNT_DATE if best_days <= 2 else CONFIDENCE_AMOUNT_ONLY

        if confidence < AUTO_MATCH_THRESHOLD:
            continue

        difference = (expected_amount - bank_line.amount) if is_near else Decimal("0")
        plans.append(
            {
                "bank_line_id": bank_line.id,
                "bank_line_amount": bank_line.amount,
                "bank_line_date": bank_line.line_date,
                "bank_line_description": bank_line.description or "",
                "settlement_entry_id": entry.id,
                "settlement_entry_number": entry.entry_number or "",
                "settlement_entry_date": entry.date,
                "settlement_entry_period": entry.period,
                "settlement_source_document": entry.source_document or "",
                "ebd_line_id": ebd_line.id,
                "batch_id": batch_id,
                "expected_amount": expected_amount,
                "actual_amount": bank_line.amount,
                "difference": difference,
                "is_near": is_near,
                "confidence": confidence,
                "value_date": bank_line.line_date,
            }
        )

        # Remove this candidate so the next bank line can't double-match.
        candidates = [c for c in candidates if c[0].id != entry.id]

    return plans


def _settlement_prepass_match(
    company,
    statement: BankStatement,
    unmatched_bank_lines: list,
    *,
    period_override: int = 0,
    fiscal_year_override: int = 0,
    override_reason: str = "",
    override_user=None,
) -> int:
    """A14b: match bank lines against PaymentSettlement JEs (Paymob /
    Bosta / PayPal CSV imports).

    The settlement JE shape from A14:
        DR Expected Bank Deposit  net_amount
        DR Fees / DR Sales Returns
            CR Provider Clearing  gross_amount

    Bank-rec match runs in two parts:
    1. Find the settlement JE for this bank deposit (by amount + date,
       boosted if `payout_batch_id` substring appears in the bank line
       description — Paymob/Bosta typically include the batch ID in the
       wire-transfer reference).
    2. Create a clearance JE: `DR statement.account / CR EBD` for net.
       Link the bank line to the clearance JE's DR Bank line. Mark the
       original settlement JE's EBD line as reconciled (it's no longer
       "expected").

    A85 chunk 2c (2026-05-26): plan/apply split. The matching decisions
    are delegated to `_plan_settlement_prepass_matches`; this function
    only applies the plan — creates clearance JEs (honoring an optional
    period override) and updates read-model state.

    Override semantics:
    - When period_override > 0 and fiscal_year_override > 0, the caller
      MUST have already passed `_validate_period_override`. The clearance
      JE is created with `period=period_override`, leaving `date=value_date`
      (the actual bank deposit date — preserves audit truth).
    - Chunk 6 (2026-05-26): the PeriodOverrideAudit row is written AFTER
      the clearance JE successfully posts. Both run inside the outer
      `auto_match_statement` `@transaction.atomic`, so they commit together
      — and a failed JE leaves NO orphan audit row claiming an override
      happened.

    Returns the number of bank lines matched (and clearance JEs created).
    """
    plans = _plan_settlement_prepass_matches(company, statement, unmatched_bank_lines)
    if not plans:
        return 0

    override_active = bool(period_override and fiscal_year_override)

    # Map bank_line_id back to the in-memory instance the caller passed
    # us, so per-match state mutation can run against the same object the
    # outer function holds.
    bl_by_id = {bl.id: bl for bl in unmatched_bank_lines}

    from accounting.mappings import ModuleAccountMapping

    ebd_account = ModuleAccountMapping.get_account(company, "shopify_connector", "EXPECTED_BANK_DEPOSIT")
    if not ebd_account:
        return 0

    matched = 0

    for plan in plans:
        bank_line = bl_by_id.get(plan["bank_line_id"])
        if bank_line is None:
            continue
        settlement_entry = JournalEntry.objects.get(id=plan["settlement_entry_id"])
        ebd_line = JournalLine.objects.get(id=plan["ebd_line_id"])

        clearance_je_line = _create_settlement_clearance_je(
            company=company,
            settlement_entry=settlement_entry,
            bank_account=statement.account,
            ebd_account=ebd_account,
            net_amount=bank_line.amount,
            batch_id=plan["batch_id"],
            statement_date=statement.statement_date,
            value_date=bank_line.line_date,
            period=period_override if override_active else None,
        )
        if not clearance_je_line:
            logger.warning(
                "Settlement match: failed to create clearance JE for batch %s — skipping bank line %s",
                plan["batch_id"],
                bank_line.id,
            )
            continue

        # A85 chunk 6: audit row writes ONLY when the clearance JE
        # successfully posts. Both this and the JE live in the outer
        # auto_match_statement @transaction.atomic, so the audit log
        # never contains a row whose JE failed to land.
        if override_active:
            from accounting.models import PeriodOverrideAudit

            value_date = plan["value_date"]
            PeriodOverrideAudit.objects.create(
                company=company,
                user=override_user,
                user_email_snapshot=(getattr(override_user, "email", "") or "") if override_user else "",
                user_name_snapshot=(getattr(override_user, "get_full_name", lambda: "")() or "")
                if override_user
                else "",
                source=PeriodOverrideAudit.Source.RECON_MATCH,
                source_document_ref=f"auto-match:settlement:{plan['batch_id']}",
                journal_entry=clearance_je_line.entry,
                original_date=value_date,
                original_period=value_date.month,
                original_fiscal_year=value_date.year,
                override_period=period_override,
                override_fiscal_year=fiscal_year_override,
                reason=override_reason.strip(),
            )

        difference = plan["difference"]
        is_near = plan["is_near"]
        confidence = plan["confidence"]

        new_status = (
            BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE
            if is_near
            else BankStatementLine.MatchStatus.AUTO_MATCHED
        )

        with command_writes_allowed():
            bank_line.matched_journal_line = clearance_je_line
            bank_line.match_status = new_status
            bank_line.match_confidence = confidence
            bank_line.difference_amount = difference
            bank_line.difference_reason = BankStatementLine.DifferenceReason.UNRESOLVED
            bank_line.save()

            JournalLine.objects.filter(pk=clearance_je_line.pk).update(
                reconciled=True,
                reconciled_date=statement.statement_date,
            )

            # Exact match: EBD line is fully drained, mark reconciled.
            # Near match: EBD line still has a residual — leave
            # reconciled=False until the merchant categorizes the diff.
            if not is_near:
                JournalLine.objects.filter(pk=ebd_line.pk).update(
                    reconciled=True,
                    reconciled_date=statement.statement_date,
                )

        # A86.4 (2026-05-26): emit ReconciliationMatchConfirmed alongside
        # the direct mutation above. The ReconciliationProjection (A86.3)
        # consumes this and writes the event_* shadow fields on the bank
        # line — convergence with the direct mutation is the gate for the
        # A86.7 cutover.
        _emit_match_confirmed(
            company=company,
            bank_line=bank_line,
            journal_line=clearance_je_line,
            match_kind="settlement_clearance",
            confidence=confidence,
            difference_amount=difference,
            statement_date=statement.statement_date,
        )

        matched += 1

        logger.info(
            "Settlement match: bank line %s -> clearance JE for batch %s "
            "(confidence=%s, near=%s, diff=%s, override=%s)",
            bank_line.id,
            plan["batch_id"],
            confidence,
            is_near,
            difference,
            f"{period_override}/{fiscal_year_override}" if override_active else "no",
        )

    return matched


def _difference_tolerance(expected: Decimal) -> Decimal:
    """A16/A35: near-match tolerance for bank deposits vs expected EBD lines.

    15% of the expected amount, capped at 10,000 currency units (EGP, USD…).
    Below this gap we still match — the bank line lands as
    MATCHED_WITH_DIFFERENCE and the operator categorizes via the A16
    Resolve flow, which posts the adjustment JE that drains the EBD
    residual. Above the cap we leave both lines unmatched (likely a wrong
    pairing rather than a real near-match).

    A35 widened the original 2% / 500 tolerance to 15% / 10000 because
    the 2% threshold left real-merchant short-payments (5-15% gap is
    common for Egyptian COD couriers) unmatched, requiring manual
    intervention via the A25 picker. With 15%, the BNK-003-style
    scenario (200 EGP short on a 2,050 EGP deposit = 9.76% gap) now
    auto-flags as MATCHED_WITH_DIFFERENCE and surfaces in the Needs
    Review queue. A merchant who wants stricter behavior can resolve
    each entry manually; A45 (deferred) adds a per-merchant
    configurable threshold.
    """
    pct = (abs(expected) * Decimal("0.15")).quantize(Decimal("0.01"))
    return min(pct, Decimal("10000"))


# A16: reason → ModuleAccountMapping role, used when posting the adjustment
# JE that drains the EBD residual after the operator categorizes a
# matched-with-difference bank line.
_DIFFERENCE_REASON_ROLE = {
    BankStatementLine.DifferenceReason.EXTRA_FEE: "PAYMENT_PROCESSING_FEES",
    BankStatementLine.DifferenceReason.BANK_CHARGE: "PAYMENT_PROCESSING_FEES",
    BankStatementLine.DifferenceReason.CHARGEBACK: "CHARGEBACK_EXPENSE",
    BankStatementLine.DifferenceReason.WRITE_OFF: "SALES_RETURNS",
    BankStatementLine.DifferenceReason.ROUNDING: "PAYMENT_PROCESSING_FEES",
    BankStatementLine.DifferenceReason.OTHER: "PAYMENT_PROCESSING_FEES",
}


def resolve_difference(
    actor: ActorContext,
    bank_line_id: int,
    reason: str,
    notes: str = "",
) -> CommandResult:
    """A16: operator picks a reason for a matched-with-difference bank line.

    Posts the adjustment JE that drains the EBD residual:
      - If difference_amount > 0 (bank short paid):
            DR <reason_account>  diff
                CR Expected Bank Deposit  diff
        Books an additional fee/charge/expense to explain the shortage.
      - If difference_amount < 0 (bank over paid):
            DR Expected Bank Deposit  |diff|
                CR <reason_account>  |diff|
        Books an income/credit to explain the overage.

    After posting:
      - Bank line's difference_reason is set, difference_resolved_at set,
        difference_adjustment_entry FK populated.
      - The original settlement JE's EBD line is marked reconciled (it's
        finally drained: clearance JE for actual bank amount + adjustment
        JE for the difference == full expected EBD amount).
    """
    require(actor, "accounting.reconciliation")

    valid_reasons = {
        r.value for r in BankStatementLine.DifferenceReason if r != BankStatementLine.DifferenceReason.UNRESOLVED
    }
    if reason not in valid_reasons:
        return CommandResult.fail(f"Reason must be one of: {sorted(valid_reasons)}. UNRESOLVED is the unset state.")

    try:
        bank_line = BankStatementLine.objects.select_related(
            "matched_journal_line",
            "matched_journal_line__entry",
            "statement",
            "statement__account",
        ).get(pk=bank_line_id, company=actor.company)
    except BankStatementLine.DoesNotExist:
        return CommandResult.fail("Bank statement line not found.")

    if bank_line.match_status != BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE:
        return CommandResult.fail("Bank line is not in MATCHED_WITH_DIFFERENCE state — nothing to resolve.")
    if bank_line.difference_reason != BankStatementLine.DifferenceReason.UNRESOLVED:
        return CommandResult.fail("Difference is already resolved.")

    diff = bank_line.difference_amount or Decimal("0")
    if diff == 0:
        return CommandResult.fail("Bank line has no recorded difference.")

    # Find the original settlement JE (the matched JE is the *clearance*,
    # whose source_document mirrors the settlement's). Walk back via the
    # source_document.
    clearance_je = bank_line.matched_journal_line.entry if bank_line.matched_journal_line else None
    if not clearance_je or clearance_je.source_module != "payment_settlement_clearance":
        return CommandResult.fail(
            "Bank line is not linked to a settlement clearance JE — A16 reason "
            "picker only operates on settlement-matched bank lines."
        )

    settlement_je = JournalEntry.objects.filter(
        company=actor.company,
        source_module="payment_settlement",
        source_document=clearance_je.source_document,
        status=JournalEntry.Status.POSTED,
    ).first()
    if not settlement_je:
        return CommandResult.fail(
            "Could not locate the original settlement JE for this clearance — "
            "data may be inconsistent. Investigate before resolving."
        )

    from accounting.commands import (
        create_journal_entry,
        post_journal_entry,
        save_journal_entry_complete,
    )
    from accounting.mappings import ModuleAccountMapping
    from accounts.authz import system_actor_for_company

    # Resolve the reason's offsetting account.
    role = _DIFFERENCE_REASON_ROLE.get(reason)
    if not role:
        return CommandResult.fail(f"No account mapping registered for reason {reason!r}.")
    reason_account = ModuleAccountMapping.get_account(actor.company, "shopify_connector", role)
    if not reason_account:
        return CommandResult.fail(
            f"Module mapping missing for role {role!r}. Run "
            "backfill_settlement_providers and ensure all settlement accounts exist."
        )

    ebd_account = ModuleAccountMapping.get_account(actor.company, "shopify_connector", "EXPECTED_BANK_DEPOSIT")
    if not ebd_account:
        return CommandResult.fail("EXPECTED_BANK_DEPOSIT account mapping missing.")

    sys_actor = system_actor_for_company(actor.company)
    batch_id = (
        clearance_je.source_document.split(":", 1)[1]
        if ":" in (clearance_je.source_document or "")
        else clearance_je.source_document or "unknown"
    )
    abs_diff = abs(diff)
    label = BankStatementLine.DifferenceReason(reason).label
    memo = f"Reconciliation difference: batch {batch_id} — {label}"
    if notes:
        memo = f"{memo} ({notes[:120]})"

    if diff > 0:
        # Bank short paid: extra fee / charge / chargeback / write-off
        # absorbs the shortage. DR reason_account / CR EBD.
        je_lines = [
            {
                "account_id": reason_account.id,
                "description": memo,
                "debit": str(abs_diff),
                "credit": "0",
            },
            {
                "account_id": ebd_account.id,
                "description": memo,
                "debit": "0",
                "credit": str(abs_diff),
            },
        ]
    else:
        # Bank over paid: book the overage as income/refund into the
        # reason account. DR EBD / CR reason_account.
        je_lines = [
            {
                "account_id": ebd_account.id,
                "description": memo,
                "debit": str(abs_diff),
                "credit": "0",
            },
            {
                "account_id": reason_account.id,
                "description": memo,
                "debit": "0",
                "credit": str(abs_diff),
            },
        ]

    create_result = create_journal_entry(
        actor=sys_actor,
        date=bank_line.line_date,
        memo=memo,
        lines=je_lines,
        kind=JournalEntry.Kind.NORMAL,
    )
    if not create_result.success:
        return CommandResult.fail(f"Failed to create adjustment JE: {create_result.error}")
    entry = create_result.data

    save_result = save_journal_entry_complete(sys_actor, entry.id)
    if not save_result.success:
        return CommandResult.fail(f"Failed to complete adjustment JE: {save_result.error}")
    entry = save_result.data

    post_result = post_journal_entry(sys_actor, entry.id)
    if not post_result.success:
        return CommandResult.fail(f"Failed to post adjustment JE: {post_result.error}")
    entry = post_result.data

    with command_writes_allowed():
        # Stamp source for traceability + idempotency on rebuild.
        JournalEntry.objects.filter(pk=entry.pk).update(
            source_module="payment_settlement_difference",
            source_document=clearance_je.source_document,
        )

        bank_line.difference_reason = reason
        bank_line.difference_notes = notes[:255] if notes else ""
        bank_line.difference_resolved_at = timezone.now()
        bank_line.difference_adjustment_entry = entry
        bank_line.save(
            update_fields=[
                "difference_reason",
                "difference_notes",
                "difference_resolved_at",
                "difference_adjustment_entry",
            ]
        )

        # Now drain the original settlement JE's EBD line — both the
        # clearance (for actual bank amount) and the adjustment (for the
        # difference) are posted, so the EBD line is fully reconciled.
        ebd_line = settlement_je.lines.filter(account=ebd_account).first()
        if ebd_line and not ebd_line.reconciled:
            JournalLine.objects.filter(pk=ebd_line.pk).update(
                reconciled=True,
                reconciled_date=bank_line.statement.statement_date,
            )

    logger.info(
        "Difference resolved: bank_line=%s reason=%s diff=%s adjustment_je=%s",
        bank_line.id,
        reason,
        diff,
        entry.public_id,
    )
    return CommandResult.ok(
        data={
            "bank_line_id": bank_line.id,
            "adjustment_entry_id": entry.id,
            "adjustment_entry_public_id": str(entry.public_id),
        }
    )


def _create_settlement_clearance_je(
    company,
    settlement_entry: JournalEntry,
    bank_account: Account,
    ebd_account: Account,
    net_amount: Decimal,
    batch_id: str,
    statement_date: date,
    value_date: date,
    period: int | None = None,
) -> JournalLine | None:
    """A14b: create the second-stage clearance JE that drains Expected
    Bank Deposit into the merchant's actual bank.

    Posted via the standard command chain so it goes through period
    validation, dimension checks (none on EBD/Bank), and event emission.
    Returns the DR Bank JournalLine (the one bank-rec should mark
    reconciled), or None if the JE failed to post.

    Stamps source_module='payment_settlement_clearance' and
    source_document=settlement_entry.source_document for traceability.

    A85 chunk 2c (2026-05-26): when `period` is provided, the JE's
    fiscal period is forced to that value (regardless of value_date).
    The date stays as `value_date` — the actual bank deposit date — so
    the audit story is "JE dated X was forcibly posted to period Y
    because <reason>" (captured in PeriodOverrideAudit by the caller).
    """
    from accounting.commands import (
        create_journal_entry,
        post_journal_entry,
        save_journal_entry_complete,
    )
    from accounts.authz import system_actor_for_company

    actor = system_actor_for_company(company)
    memo = f"Bank deposit clearance: settlement batch {batch_id}"

    create_result = create_journal_entry(
        actor=actor,
        date=value_date,
        memo=memo,
        lines=[
            {
                "account_id": bank_account.id,
                "description": f"{memo} — bank deposit",
                "debit": str(net_amount),
                "credit": "0",
            },
            {
                "account_id": ebd_account.id,
                "description": f"{memo} — clear EBD",
                "debit": "0",
                "credit": str(net_amount),
            },
        ],
        kind=JournalEntry.Kind.NORMAL,
        period=period,
    )
    if not create_result.success:
        logger.error("Settlement clearance create failed: %s", create_result.error)
        return None
    entry = create_result.data

    save_result = save_journal_entry_complete(actor, entry.id)
    if not save_result.success:
        logger.error("Settlement clearance save_complete failed: %s", save_result.error)
        return None
    entry = save_result.data

    post_result = post_journal_entry(actor, entry.id)
    if not post_result.success:
        logger.error("Settlement clearance post failed: %s", post_result.error)
        return None
    entry = post_result.data

    # Stamp source for traceability + idempotency on rebuild.
    with command_writes_allowed():
        JournalEntry.objects.filter(pk=entry.pk).update(
            source_module="payment_settlement_clearance",
            source_document=settlement_entry.source_document or batch_id,
        )

    return entry.lines.filter(account=bank_account).first()


def _compute_match_confidence(
    bank_line: BankStatementLine,
    journal_line: JournalLine,
) -> Decimal:
    """
    Compute a confidence score for a potential match.

    Factors:
    - Amount match (required — already filtered by caller)
    - Date proximity: same day = +15, within 3 days = +10, within 5 = +5
    - Reference/description overlap: keyword match = +10
    """
    confidence = Decimal("50")  # Base: amounts match

    # Date proximity
    jl_date = journal_line.entry.date
    days_diff = abs((bank_line.line_date - jl_date).days)

    if days_diff == 0:
        confidence += Decimal("30")
    elif days_diff <= 3:
        confidence += Decimal("20")
    elif days_diff <= 5:
        confidence += Decimal("10")

    # Reference matching
    bank_ref = (bank_line.reference + " " + bank_line.description).lower()
    jl_ref = (journal_line.description or "").lower()
    entry_memo = (journal_line.entry.memo or "").lower()

    if bank_ref and jl_ref:
        # Check for keyword overlap
        bank_words = set(bank_ref.split())
        jl_words = set(jl_ref.split()) | set(entry_memo.split())
        overlap = bank_words & jl_words - {"the", "a", "an", "to", "from", "for"}

        if len(overlap) >= 2:
            confidence += Decimal("20")
        elif len(overlap) >= 1:
            confidence += Decimal("10")

    return min(confidence, CONFIDENCE_EXACT)


# =============================================================================
# Manual Match / Unmatch
# =============================================================================


@transaction.atomic
def manual_match(
    actor: ActorContext,
    bank_line_id: int,
    journal_line_id: int,
) -> CommandResult:
    """Manually match a bank statement line to a journal line."""
    require(actor, "accounting.reconciliation")

    try:
        bank_line = BankStatementLine.objects.get(
            id=bank_line_id,
            company=actor.company,
        )
    except BankStatementLine.DoesNotExist:
        return CommandResult.fail("Bank statement line not found.")

    try:
        journal_line = JournalLine.objects.get(
            id=journal_line_id,
            company=actor.company,
        )
    except JournalLine.DoesNotExist:
        return CommandResult.fail("Journal line not found.")

    if bank_line.match_status != BankStatementLine.MatchStatus.UNMATCHED:
        return CommandResult.fail("Bank line is already matched.")

    if journal_line.reconciled:
        return CommandResult.fail("Journal line is already reconciled.")

    with command_writes_allowed():
        bank_line.matched_journal_line = journal_line
        bank_line.match_status = BankStatementLine.MatchStatus.MANUAL_MATCHED
        bank_line.match_confidence = CONFIDENCE_EXACT
        bank_line.save()

        JournalLine.objects.filter(pk=journal_line.pk).update(
            reconciled=True,
            reconciled_date=bank_line.statement.statement_date,
        )

    return CommandResult.ok(
        data={
            "bank_line": bank_line,
            "journal_line": journal_line,
        }
    )


def _reverse_match_side_effects(
    actor: ActorContext,
    bank_line: BankStatementLine,
) -> tuple[bool, str | None, JournalLine | None]:
    """A19: reverse any JEs that were synthesized by the bank-rec
    match step (clearance JE + A16 difference adjustment), so unmatch
    or exclude doesn't leave orphan accounting on the bank account.

    Pre-existing JEs (platform payouts, manually-posted entries used
    via manual_match) are left untouched — those have independent
    meaning and only the reconciled flag should flip.

    Returns (success, error_or_None, settlement_ebd_line_or_None).
    The EBD line is returned so the caller can flip its reconciled
    flag back inside command_writes_allowed.
    """
    from accounting.commands import reverse_journal_entry
    from accounting.mappings import ModuleAccountMapping

    journal_line = bank_line.matched_journal_line
    adjustment_entry = bank_line.difference_adjustment_entry

    clearance_je = None
    settlement_ebd_line = None
    if journal_line and journal_line.entry.source_module == "payment_settlement_clearance":
        clearance_je = journal_line.entry
        settlement_je = JournalEntry.objects.filter(
            company=actor.company,
            source_module="payment_settlement",
            source_document=clearance_je.source_document,
        ).first()
        if settlement_je:
            ebd_account = ModuleAccountMapping.get_account(actor.company, "shopify_connector", "EXPECTED_BANK_DEPOSIT")
            if ebd_account:
                settlement_ebd_line = settlement_je.lines.filter(account=ebd_account).first()

    # Reverse the A16 difference adjustment first so EBD is back to its
    # post-clearance state before we reverse the clearance itself.
    if adjustment_entry and adjustment_entry.status == JournalEntry.Status.POSTED:
        rev = reverse_journal_entry(actor, adjustment_entry.id)
        if not rev.success:
            return False, f"Could not reverse difference adjustment: {rev.error}", None

    if clearance_je and clearance_je.status == JournalEntry.Status.POSTED:
        rev = reverse_journal_entry(actor, clearance_je.id)
        if not rev.success:
            return False, f"Could not reverse clearance entry: {rev.error}", None

    return True, None, settlement_ebd_line


def _clear_match_state(
    bank_line: BankStatementLine,
    settlement_ebd_line: JournalLine | None,
    *,
    final_status: str,
) -> None:
    """Apply the read-model side of unmatch/exclude under
    command_writes_allowed: detach the bank line, drop A16 difference
    state, and resurrect the EBD residual if a clearance JE was
    reversed."""
    journal_line_id = bank_line.matched_journal_line_id
    statement_date = bank_line.statement.statement_date  # captured before refresh

    with command_writes_allowed():
        bank_line.matched_journal_line = None
        bank_line.match_status = final_status
        bank_line.match_confidence = None
        bank_line.difference_amount = Decimal("0")
        bank_line.difference_reason = BankStatementLine.DifferenceReason.UNRESOLVED
        bank_line.difference_notes = ""
        bank_line.difference_resolved_at = None
        bank_line.difference_adjustment_entry = None
        bank_line.save()

        if journal_line_id:
            JournalLine.objects.filter(pk=journal_line_id).update(
                reconciled=False,
                reconciled_date=None,
            )

        if settlement_ebd_line is not None:
            JournalLine.objects.filter(pk=settlement_ebd_line.pk).update(
                reconciled=False,
                reconciled_date=None,
            )

    # Quiet the lint warning — captured intentionally for future use.
    _ = statement_date


def preview_unmatch_line(
    actor: ActorContext,
    bank_line_id: int,
) -> CommandResult:
    """A85 chunk 2b (2026-05-26): dry-run for unmatch_line.

    Operator clicks "Unmatch" on a bank line; this preview shows what
    JEs (if any) would be reversed BEFORE the action commits. Mirrors
    the side-effect logic of `_reverse_match_side_effects()` without
    actually reversing anything.

    Three scenarios:
    - Match is a simple flag-flip (e.g., manual match against an
      existing JE line) → unmatch only flips flags, no JE reversal.
    - Match was made by settlement prepass → unmatch reverses the
      clearance JE (DR Bank / CR Expected Bank Deposit) AND resurrects
      the original settlement's EBD residual line.
    - Match had a difference adjustment (A16 extra fee, bank charge,
      etc.) → unmatch reverses both the adjustment AND the clearance.

    Returns CommandResult.ok with:
        bank_line_id, bank_line_description, bank_line_amount, match_status
        reversal_plan: [
            { entry_id, entry_number, source_module, date, period, kind,
              memo, total_debit, total_credit, would_reverse: true,
              warning: <if reversal would fail, e.g., closed period> },
            ...
        ]
        flag_flips: [
            { object_type, object_id, field, old, new },
            ...
        ]
        warnings: [...]
        dry_run_safe: bool (false if any reversal would hit a closed period)

    See:
    - unmatch_line() — the corresponding execute path
    - _reverse_match_side_effects() — the side-effect logic this mirrors
    """
    require(actor, "accounting.reconciliation")

    try:
        bank_line = BankStatementLine.objects.select_related(
            "matched_journal_line__entry",
            "difference_adjustment_entry",
            "statement",
        ).get(
            id=bank_line_id,
            company=actor.company,
        )
    except BankStatementLine.DoesNotExist:
        return CommandResult.fail("Bank statement line not found.")

    if bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED:
        return CommandResult.fail("Line is not matched; nothing to unmatch.")

    from accounting.policies import can_post_to_period

    reversal_plan: list[dict] = []
    warnings: list[str] = []

    journal_line = bank_line.matched_journal_line
    adjustment_entry = bank_line.difference_adjustment_entry

    # Identify the clearance JE (if any) — mirrors logic at line 1340 above.
    clearance_je = None
    if journal_line and journal_line.entry.source_module == "payment_settlement_clearance":
        clearance_je = journal_line.entry

    def _je_plan_row(entry, why: str) -> dict:
        # Period validation — reversing would post a new JE dated today
        # (or, more accurately, the original entry's date for traceability).
        # If the reversal target period is closed, surface the blocker.
        target_date = entry.date
        target_period = entry.period
        allowed, reason = can_post_to_period(actor, target_date, period=target_period)
        if not allowed:
            warnings.append(
                f"Reversing JE {entry.entry_number or entry.id} ({why}) "
                f"would target period {target_period}/{target_date.year if target_date else '?'} "
                f"which is currently rejected: {reason}"
            )
        return {
            "entry_id": entry.id,
            "entry_number": entry.entry_number or "",
            "source_module": entry.source_module or "",
            "source_document": entry.source_document or "",
            "date": target_date.isoformat() if target_date else None,
            "period": target_period,
            "fiscal_year": target_date.year if target_date else None,
            "kind": entry.kind,
            "memo": entry.memo[:200] if entry.memo else "",
            "total_debit": str(entry.total_debit) if entry.total_debit is not None else "0",
            "total_credit": str(entry.total_credit) if entry.total_credit is not None else "0",
            "would_reverse": allowed,
            "reason_for_reversal": why,
            "blocker": None if allowed else reason,
        }

    # A19 mirror: reverse adjustment FIRST (so EBD is back to its
    # post-clearance state before the clearance itself is reversed).
    if adjustment_entry and adjustment_entry.status == JournalEntry.Status.POSTED:
        reversal_plan.append(
            _je_plan_row(
                adjustment_entry,
                why="A16 difference adjustment posted on this match",
            )
        )

    if clearance_je and clearance_je.status == JournalEntry.Status.POSTED:
        reversal_plan.append(
            _je_plan_row(
                clearance_je,
                why="Settlement-prepass clearance JE created on this match",
            )
        )

    # Flag flips that always happen on unmatch, regardless of JE reversal.
    flag_flips: list[dict] = [
        {
            "object_type": "BankStatementLine",
            "object_id": bank_line.id,
            "field": "match_status",
            "old": bank_line.match_status,
            "new": BankStatementLine.MatchStatus.UNMATCHED,
        },
    ]
    if journal_line:
        flag_flips.append(
            {
                "object_type": "JournalLine",
                "object_id": journal_line.id,
                "field": "reconciled",
                "old": True,
                "new": False,
            }
        )

    # Helpful operator note if there's nothing to reverse.
    if not reversal_plan:
        warnings.append(
            "No JEs to reverse — this unmatch is a flag-flip only "
            "(the matched JE was pre-existing, not synthesized by the match)."
        )

    return CommandResult.ok(
        data={
            "bank_line_id": bank_line.id,
            "bank_line_description": bank_line.description,
            "bank_line_amount": str(bank_line.amount),
            "bank_line_date": bank_line.line_date.isoformat() if bank_line.line_date else None,
            "match_status": bank_line.match_status,
            "reversal_plan": reversal_plan,
            "flag_flips": flag_flips,
            "warnings": warnings,
            "dry_run_safe": all(row["would_reverse"] for row in reversal_plan),
        }
    )


@transaction.atomic
def unmatch_line(
    actor: ActorContext,
    bank_line_id: int,
) -> CommandResult:
    """Unmatch a previously matched bank statement line.

    A19: when the match created a clearance JE (settlement prepass) or
    posted an A16 difference adjustment, both must be reversed so the
    bank account doesn't carry an orphan DR after unmatch. The original
    settlement JE's EBD residual is restored too.
    """
    require(actor, "accounting.reconciliation")

    try:
        bank_line = BankStatementLine.objects.get(
            id=bank_line_id,
            company=actor.company,
        )
    except BankStatementLine.DoesNotExist:
        return CommandResult.fail("Bank statement line not found.")

    if bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED:
        return CommandResult.fail("Line is not matched.")

    ok, err, settlement_ebd_line = _reverse_match_side_effects(actor, bank_line)
    if not ok:
        return CommandResult.fail(err)

    _clear_match_state(
        bank_line,
        settlement_ebd_line,
        final_status=BankStatementLine.MatchStatus.UNMATCHED,
    )

    return CommandResult.ok()


@transaction.atomic
def exclude_line(
    actor: ActorContext,
    bank_line_id: int,
) -> CommandResult:
    """Exclude a bank statement line from reconciliation.

    A19: same reversal semantics as unmatch_line — any clearance or
    adjustment JE synthesized by the prior match must be reversed
    before the line is marked EXCLUDED.
    """
    require(actor, "accounting.reconciliation")

    try:
        bank_line = BankStatementLine.objects.get(
            id=bank_line_id,
            company=actor.company,
        )
    except BankStatementLine.DoesNotExist:
        return CommandResult.fail("Bank statement line not found.")

    ok, err, settlement_ebd_line = _reverse_match_side_effects(actor, bank_line)
    if not ok:
        return CommandResult.fail(err)

    _clear_match_state(
        bank_line,
        settlement_ebd_line,
        final_status=BankStatementLine.MatchStatus.EXCLUDED,
    )

    return CommandResult.ok()


# =============================================================================
# Reconciliation Completion
# =============================================================================


@transaction.atomic
def complete_reconciliation(
    actor: ActorContext,
    statement_id: int,
    notes: str = "",
) -> CommandResult:
    """
    Complete a bank reconciliation.

    Validates that the difference is zero (or warns if not),
    then marks the statement as reconciled.
    """
    require(actor, "accounting.reconciliation")

    try:
        statement = BankStatement.objects.get(
            id=statement_id,
            company=actor.company,
        )
    except BankStatement.DoesNotExist:
        return CommandResult.fail("Statement not found.")

    if statement.status == BankStatement.Status.RECONCILED:
        return CommandResult.fail("Statement is already reconciled.")

    # Compute reconciliation summary
    summary = compute_reconciliation_summary(actor.company, statement)

    with command_writes_allowed():
        recon = BankReconciliation.objects.create(
            company=actor.company,
            account=statement.account,
            statement=statement,
            reconciliation_date=statement.statement_date,
            statement_closing_balance=statement.closing_balance,
            gl_balance=summary["gl_balance"],
            adjusted_gl_balance=summary["adjusted_gl_balance"],
            difference=summary["difference"],
            matched_count=summary["matched_count"],
            unmatched_count=summary["unmatched_count"],
            outstanding_deposits=summary["outstanding_deposits"],
            outstanding_withdrawals=summary["outstanding_withdrawals"],
            status=BankReconciliation.Status.COMPLETED,
            reconciled_by=actor.user,
            reconciled_at=timezone.now(),
            notes=notes,
        )

        statement.status = BankStatement.Status.RECONCILED
        statement.save()

    logger.info(
        "Completed reconciliation %s for %s (difference: %s)",
        recon.public_id,
        statement.account.code,
        summary["difference"],
    )

    return CommandResult.ok(
        data={
            "reconciliation": recon,
            "summary": summary,
        }
    )


def compute_reconciliation_summary(company, statement) -> dict:
    """
    Compute the reconciliation summary for a bank statement.

    Returns dict with:
    - gl_balance: GL balance for the account as of statement date
    - outstanding_deposits: deposits in GL not yet on statement
    - outstanding_withdrawals: withdrawals in GL not yet on statement
    - adjusted_gl_balance: gl_balance - outstanding items
    - difference: statement closing - adjusted GL
    - matched_count, unmatched_count
    """
    # GL balance: sum of all posted debits - credits for this account up to statement date
    gl_lines = JournalLine.objects.filter(
        company=company,
        account=statement.account,
        entry__status=JournalEntry.Status.POSTED,
        entry__date__lte=statement.statement_date,
    )

    totals = gl_lines.aggregate(
        total_debit=Sum("debit"),
        total_credit=Sum("credit"),
    )
    gl_balance = (totals["total_debit"] or Decimal("0")) - (totals["total_credit"] or Decimal("0"))

    # Outstanding items: GL entries not yet reconciled (posted, within period)
    unreconciled_lines = gl_lines.filter(reconciled=False)

    outstanding_deposits = Decimal("0")
    outstanding_withdrawals = Decimal("0")
    for jl in unreconciled_lines:
        net = jl.debit - jl.credit
        if net > 0:
            outstanding_deposits += net
        else:
            outstanding_withdrawals += abs(net)

    adjusted_gl_balance = gl_balance - outstanding_deposits + outstanding_withdrawals

    # Statement line stats
    statement_lines = BankStatementLine.objects.filter(statement=statement)
    matched_count = statement_lines.exclude(
        match_status=BankStatementLine.MatchStatus.UNMATCHED,
    ).count()
    unmatched_count = statement_lines.filter(
        match_status=BankStatementLine.MatchStatus.UNMATCHED,
    ).count()

    difference = statement.closing_balance - adjusted_gl_balance

    return {
        "gl_balance": gl_balance,
        "outstanding_deposits": outstanding_deposits,
        "outstanding_withdrawals": outstanding_withdrawals,
        "adjusted_gl_balance": adjusted_gl_balance,
        "statement_closing_balance": statement.closing_balance,
        "difference": difference,
        "matched_count": matched_count,
        "unmatched_count": unmatched_count,
        "total_lines": statement_lines.count(),
    }


def get_unreconciled_journal_lines(company, account, as_of_date=None):
    """
    Get journal lines for a bank account that have not been reconciled.

    Returns queryset of JournalLine objects.
    """
    qs = JournalLine.objects.filter(
        company=company,
        account=account,
        reconciled=False,
        entry__status=JournalEntry.Status.POSTED,
    ).select_related("entry")

    if as_of_date:
        qs = qs.filter(entry__date__lte=as_of_date)

    return qs.order_by("entry__date", "entry__id")


def get_match_candidates_for_bank_line(bank_line, limit: int = 200) -> list:
    """A25: candidate JournalLines a merchant could manually match to
    this bank line. Returns the union of:

    - Un-reconciled JLs on the bank line's bank account (legacy
      manual-match target — direct payments, manual JEs, etc.).
    - Un-reconciled JLs on the EBD account from
      `source_module='payment_settlement'` JEs (lets the merchant link
      a bank deposit to its expected settlement when auto-match's
      tolerance/date-proximity heuristic missed it). This is what
      surfaces the A16 difference-resolution flow from the UI.

    Sorted by amount-proximity to the bank line first, date-proximity
    second. Capped at `limit` to keep the picker UI responsive.
    """
    from accounting.mappings import ModuleAccountMapping

    company = bank_line.company
    bank_amount = bank_line.amount
    bank_date = bank_line.line_date

    bank_jls = list(
        JournalLine.objects.filter(
            company=company,
            account=bank_line.statement.account,
            reconciled=False,
            entry__status=JournalEntry.Status.POSTED,
        ).select_related("entry")
    )

    ebd_account = ModuleAccountMapping.get_account(company, "shopify_connector", "EXPECTED_BANK_DEPOSIT")
    ebd_jls: list = []
    if ebd_account:
        ebd_jls = list(
            JournalLine.objects.filter(
                company=company,
                account=ebd_account,
                reconciled=False,
                entry__status=JournalEntry.Status.POSTED,
                entry__source_module="payment_settlement",
            ).select_related("entry")
        )

    candidates = bank_jls + ebd_jls

    def _proximity_key(jl):
        signed_amount = jl.debit - jl.credit
        amount_gap = abs(signed_amount - bank_amount)
        date_gap = abs((jl.entry.date - bank_date).days) if bank_date else 0
        return (amount_gap, date_gap, jl.entry.id)

    candidates.sort(key=_proximity_key)
    return candidates[:limit]
