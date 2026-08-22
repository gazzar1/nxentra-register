# accounting/bank_reconciliation.py
"""
Bank statement import + completion + queries.

This module owns the parts of bank reconciliation that aren't
reconciliation *commands*:
- Bank statement import (`import_bank_statement`,
  `preview_bank_statement_import`)
- CSV parsing helpers (`parse_csv_headers`, `parse_csv_statement`)
- Dedup hash (`_compute_line_dedup_hash`)
- Reconciliation completion (`complete_reconciliation`,
  `compute_reconciliation_summary`)
- Lookup helpers used by the operator UI
  (`get_unreconciled_journal_lines`, `get_match_candidates_for_bank_line`)

The reconciliation command surface lives in the `reconciliation/`
Django app:
- `reconciliation.commands` — `auto_match_statement`, `manual_match`,
  `unmatch_line`, `exclude_line`, `resolve_difference`,
  `preview_auto_match`, `preview_unmatch_line`, plus the private
  helpers they need (event emitters, period-override validation,
  side-effect reversal, the projection sync trigger, the settlement
  clearance JE creator, and the platform/settlement prepass execute
  paths).
- `reconciliation.matching` — the pure planner
  (`_plan_settlement_prepass_matches`), the confidence scorer
  (`_compute_match_confidence`), `_difference_tolerance`, and the
  confidence threshold constants.

A86.8 moved the code; A86.9 dropped the backward-compat shim
re-exports that used to live here and migrated every caller to import
from the canonical locations.
"""

import csv
import hashlib
import io
import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.authz import ActorContext, require
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
    # A5-PR3c: durable per-row reject evidence.
    # - source_filename: the uploaded file's name (only the parse endpoint sees
    #   it; the commit view threads it through for reject identity).
    # - parse_rejects: descriptors the PARSE step produced (bad date / malformed
    #   numeric / bad amount / zero amount). Parse persists nothing (a wrong
    #   column mapping would page false rejects on every wizard retry); the
    #   commit — the moment the operator says "this mapping is right" — persists
    #   them, linked to the statement. Descriptors are validated before writing.
    # - import_batch_id: one id per upload, generated by the view (so it can
    #   read the rejects back for the response) or here if omitted.
    source_filename: str = "",
    parse_rejects: list | None = None,
    import_batch_id=None,
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

    if not lines_data and not parse_rejects:
        return CommandResult.fail("No transaction lines provided.")
    # Codex round-4: an ALL-INVALID file (zero survivors, N rejects) must still
    # be committable, or its rows never gain durable evidence — the statement is
    # created with zero lines (already a legitimate outcome: a full-duplicate
    # re-upload does the same) and the rejects link to it. The EGP-only gate
    # below still governs, so a foreign reject-only commit refuses whole-file.

    # A4: the constrained pilot ingests EGP only — reject a foreign-currency bank
    # statement before any BankStatement / line row is written. Serialize the
    # currency decision against pilot activation on the Company ADMISSION LOCK
    # (held through this @transaction.atomic's commit); the account lookup above
    # takes no lock (a read).
    from accounts.pilot_policy import lock_company_for_admission, require_pilot_currency

    locked_company = lock_company_for_admission(actor.company.pk)
    require_pilot_currency(locked_company, currency, context="Bank statement")

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
        commit_rejects: list[dict] = []
        # A17: track hashes seen within THIS file so the same row appearing
        # twice in the upload itself dedups too (some bank exports emit
        # the same transaction twice if the merchant runs the report
        # across overlapping date filters).
        seen_in_batch: set[str] = set()
        for line_index, ld in enumerate(lines_data, start=1):
            try:
                amount = Decimal(str(ld["amount"]))
            except (KeyError, InvalidOperation, TypeError):
                # A5-PR3c: previously a bare `continue` with no counter and no
                # trace (and TypeError — a non-dict/None entry — 500'd while the
                # preview counted it as invalid). Durable REJECTED evidence now;
                # row_index is the 1-based position in the SUBMITTED lines.
                commit_rejects.append(
                    {
                        "row_index": line_index,
                        "raw_row": ld if isinstance(ld, dict) else {"_raw": str(ld)},
                        "reason_code": "UNPARSEABLE_AMOUNT",
                        "reason_message": (
                            f"Submitted line {line_index}: amount "
                            f"{(ld.get('amount') if isinstance(ld, dict) else ld)!r} is not a number — dropped."
                        ),
                    }
                )
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

    # A5-PR3c: persist durable reject evidence — the parse step's dropped rows
    # (echoed through the commit request) plus this commit's own drops — linked
    # to the statement. Inside this command's @transaction.atomic on purpose: a
    # whole-file refusal (the non-EGP PilotScopeBlocked above) rolls back rejects
    # along with everything else, keeping whole-file semantics side-effect-free.
    # Duplicates stay a COUNTER only (founder row policy: per-row DUPLICATE
    # records would be noise — overlap-period re-uploads are the normal flow).
    import uuid as _uuid

    from accounting.import_rejects import persist_import_rejects

    batch_uuid = import_batch_id or _uuid.uuid4()
    lines_rejected = persist_import_rejects(
        actor.company,
        source_kind="BANK",
        provider_code="",
        source_filename=source_filename,
        import_batch_id=batch_uuid,
        rejects=list(parse_rejects or []) + commit_rejects,
        statement=statement,
        # Codex round-3: scope identity to the bank ACCOUNT — two accounts
        # importing a same-named file with an identical bad row are distinct
        # evidence; a re-upload to the SAME account still dedups.
        identity_scope=f"account:{account.pk}",
    )

    logger.info(
        "Imported bank statement %s for %s: %d lines created, %d duplicates skipped, %d rows rejected",
        statement.public_id,
        account.code,
        len(created_lines),
        skipped_duplicate,
        lines_rejected,
    )

    return CommandResult.ok(
        data={
            "statement": statement,
            "lines_created": len(created_lines),
            "lines_skipped_duplicate": skipped_duplicate,
            "lines_rejected": lines_rejected,
            "import_batch_id": str(batch_uuid),
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
    """Back-compat wrapper: surviving lines only (see parse_csv_statement_full)."""
    return parse_csv_statement_full(
        csv_content,
        date_column=date_column,
        description_column=description_column,
        amount_column=amount_column,
        reference_column=reference_column,
        debit_column=debit_column,
        credit_column=credit_column,
        date_format=date_format,
    )[0]


def parse_csv_statement_full(
    csv_content: str,
    date_column: str = "Date",
    description_column: str = "Description",
    amount_column: str = "Amount",
    reference_column: str = "Reference",
    debit_column: str = "",
    credit_column: str = "",
    date_format: str = "%Y-%m-%d",
) -> tuple[list, list]:
    """
    Parse a CSV file into ``(lines, rejects)``.

    Supports two formats:
    1. Single amount column (positive = deposit, negative = withdrawal)
    2. Separate debit/credit columns

    A5-PR3c: rows that used to vanish on a silent ``continue`` (bad date,
    malformed debit/credit, unparseable amount, zero amount) come back as
    reject descriptors so the parse response can show them and the COMMIT can
    persist them as durable ``ImportRejectedRow`` evidence. Parse time itself
    persists NOTHING — the operator may still be iterating on column mappings,
    and a wrong mapping would otherwise page false rejects for every row.
    """
    from datetime import datetime

    from accounting.import_rejects import reject_descriptor

    reader = csv.DictReader(io.StringIO(csv_content))
    lines = []
    rejects: list[dict] = []

    for row_index, row in enumerate(reader, start=1):
        try:
            line_date = datetime.strptime(row.get(date_column, "").strip(), date_format).date()
        except (ValueError, AttributeError):
            rejects.append(
                reject_descriptor(
                    row_index=row_index,
                    raw_row=row,
                    reason_code="UNPARSEABLE_DATE",
                    reason_message=(
                        f"Cell {date_column!r}={row.get(date_column)!r} does not match "
                        f"date format {date_format!r} — row dropped."
                    ),
                )
            )
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
                rejects.append(
                    reject_descriptor(
                        row_index=row_index,
                        raw_row=row,
                        reason_code="MALFORMED_NUMERIC",
                        reason_message=(f"Debit/credit cells ({debit!r} / {credit!r}) are not numbers — row dropped."),
                    )
                )
                continue
            # Net: credit - debit. Positive = deposit, negative = withdrawal.
            amount = credit_val - debit_val
        else:
            # Single amount column
            raw = row.get(amount_column, "").strip().replace(",", "")
            try:
                amount = Decimal(raw)
            except InvalidOperation:
                rejects.append(
                    reject_descriptor(
                        row_index=row_index,
                        raw_row=row,
                        reason_code="UNPARSEABLE_AMOUNT",
                        reason_message=f"Cell {amount_column!r}={raw!r} is not a number — row dropped.",
                    )
                )
                continue

        if amount == 0:
            # A5-PR3c (founder row policy): a zero-amount statement row books
            # nothing, but must not vanish silently — durable REJECTED evidence.
            rejects.append(
                reject_descriptor(
                    row_index=row_index,
                    raw_row=row,
                    reason_code="ZERO_AMOUNT",
                    reason_message="Row amount is zero — nothing to book; row dropped.",
                )
            )
            continue

        lines.append(
            {
                "line_date": line_date,
                "description": description,
                "reference": reference,
                "amount": str(amount),
            }
        )

    return lines, rejects


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

    # Per-provider EBD (ADR-0002): each provider seeds its own
    # EXPECTED_BANK_DEPOSIT under its own module key (shopify_connector,
    # platform_stripe, …). Union the EBD accounts across all providers so a
    # deposit surfaces here no matter which provider settled it — the old
    # hardcoded "shopify_connector" lookup hid every Stripe deposit.
    ebd_accounts = ModuleAccountMapping.get_accounts_for_role(company, "EXPECTED_BANK_DEPOSIT")
    ebd_jls: list = []
    if ebd_accounts:
        ebd_jls = list(
            JournalLine.objects.filter(
                company=company,
                account__in=ebd_accounts,
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
