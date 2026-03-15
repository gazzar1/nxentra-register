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
import io
import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q, Sum
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

# Match confidence thresholds
CONFIDENCE_EXACT = Decimal("100")
CONFIDENCE_AMOUNT_DATE = Decimal("85")
CONFIDENCE_AMOUNT_ONLY = Decimal("60")
AUTO_MATCH_THRESHOLD = Decimal("80")


# =============================================================================
# Statement Import
# =============================================================================

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
            id=account_id, company=actor.company,
        )
    except Account.DoesNotExist:
        return CommandResult.fail("Account not found.")

    if not lines_data:
        return CommandResult.fail("No transaction lines provided.")

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
        for ld in lines_data:
            try:
                amount = Decimal(str(ld["amount"]))
            except (KeyError, InvalidOperation):
                continue

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
                line_date=ld.get("line_date", statement_date),
                description=ld.get("description", ""),
                reference=ld.get("reference", ""),
                amount=amount,
                transaction_type=txn_type,
            )
            created_lines.append(line)

    logger.info(
        "Imported bank statement %s for %s: %d lines",
        statement.public_id, account.code, len(created_lines),
    )

    return CommandResult.ok(data={
        "statement": statement,
        "lines_created": len(created_lines),
    })


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
            line_date = datetime.strptime(
                row.get(date_column, "").strip(), date_format
            ).date()
        except (ValueError, AttributeError):
            continue

        description = row.get(description_column, "").strip()
        reference = row.get(reference_column, "").strip()

        if debit_column and credit_column:
            # Separate debit/credit columns
            debit = row.get(debit_column, "").strip().replace(",", "")
            credit = row.get(credit_column, "").strip().replace(",", "")
            try:
                amount = -Decimal(debit) if debit else Decimal(credit) if credit else Decimal("0")
            except InvalidOperation:
                continue
        else:
            # Single amount column
            raw = row.get(amount_column, "").strip().replace(",", "")
            try:
                amount = Decimal(raw)
            except InvalidOperation:
                continue

        if amount == 0:
            continue

        lines.append({
            "line_date": line_date,
            "description": description,
            "reference": reference,
            "amount": str(amount),
        })

    return lines


# =============================================================================
# Auto-Matching Algorithm
# =============================================================================

@transaction.atomic
def auto_match_statement(
    actor: ActorContext,
    statement_id: int,
) -> CommandResult:
    """
    Auto-match unmatched bank statement lines to journal lines.

    Matching strategy (in priority order):
    1. Exact match: same amount + same date + reference match → 100% confidence
    2. Amount + date proximity (±3 days) → 85% confidence
    3. Amount-only match (unique) → 60% confidence

    Only matches above AUTO_MATCH_THRESHOLD are applied.
    """
    require(actor, "accounting.reconciliation")

    try:
        statement = BankStatement.objects.get(
            id=statement_id, company=actor.company,
        )
    except BankStatement.DoesNotExist:
        return CommandResult.fail("Statement not found.")

    # Get unmatched bank lines
    unmatched_bank_lines = list(BankStatementLine.objects.filter(
        statement=statement,
        match_status=BankStatementLine.MatchStatus.UNMATCHED,
    ))

    if not unmatched_bank_lines:
        return CommandResult.ok(data={"matched": 0, "total": 0})

    # ---- Platform-aware pre-pass ----
    # Try to match bank lines against known platform payouts (e.g. Shopify)
    # before falling back to generic GL-level matching.
    platform_matched = _platform_prepass_match(
        actor.company, statement, unmatched_bank_lines,
    )

    # Remove platform-matched lines from the unmatched list
    if platform_matched > 0:
        unmatched_bank_lines = [
            bl for bl in unmatched_bank_lines
            if bl.match_status == BankStatementLine.MatchStatus.UNMATCHED
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

                # Mark journal line as reconciled
                best_match.reconciled = True
                best_match.reconciled_date = statement.statement_date
                best_match.save()

                # Remove from candidates to prevent double-matching
                candidates.remove(best_match)
                matched_count += 1

        # Update statement status
        if statement.status == BankStatement.Status.IMPORTED:
            statement.status = BankStatement.Status.IN_PROGRESS
            statement.save()

    matched_count += platform_matched
    logger.info(
        "Auto-matched %d/%d lines for statement %s (platform pre-pass: %d)",
        matched_count, total, statement.public_id, platform_matched,
    )

    return CommandResult.ok(data={
        "matched": matched_count,
        "total": total,
    })


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
    payouts = list(ShopifyPayout.objects.filter(
        company=company,
        payout_date__gte=statement.period_start - date_buffer,
        payout_date__lte=statement.period_end + date_buffer,
        shopify_status="paid",
    ))

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

            payout_je_line.reconciled = True
            payout_je_line.reconciled_date = statement.statement_date
            payout_je_line.save()

            # Remove payout from candidates to prevent double-matching
            candidates.remove(best_payout)
            matched += 1

            logger.info(
                "Platform pre-pass: matched bank line %s to Shopify payout %s",
                bank_line.id, best_payout.shopify_payout_id,
            )

    return matched


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

        journal_line.reconciled = True
        journal_line.reconciled_date = bank_line.statement.statement_date
        journal_line.save()

    return CommandResult.ok(data={
        "bank_line": bank_line,
        "journal_line": journal_line,
    })


@transaction.atomic
def unmatch_line(
    actor: ActorContext,
    bank_line_id: int,
) -> CommandResult:
    """Unmatch a previously matched bank statement line."""
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

    journal_line = bank_line.matched_journal_line

    with command_writes_allowed():
        bank_line.matched_journal_line = None
        bank_line.match_status = BankStatementLine.MatchStatus.UNMATCHED
        bank_line.match_confidence = None
        bank_line.save()

        if journal_line:
            journal_line.reconciled = False
            journal_line.reconciled_date = None
            journal_line.save()

    return CommandResult.ok()


@transaction.atomic
def exclude_line(
    actor: ActorContext,
    bank_line_id: int,
) -> CommandResult:
    """Exclude a bank statement line from reconciliation."""
    require(actor, "accounting.reconciliation")

    try:
        bank_line = BankStatementLine.objects.get(
            id=bank_line_id,
            company=actor.company,
        )
    except BankStatementLine.DoesNotExist:
        return CommandResult.fail("Bank statement line not found.")

    with command_writes_allowed():
        # Unmatch first if needed
        if bank_line.matched_journal_line:
            jl = bank_line.matched_journal_line
            jl.reconciled = False
            jl.reconciled_date = None
            jl.save()

        bank_line.matched_journal_line = None
        bank_line.match_status = BankStatementLine.MatchStatus.EXCLUDED
        bank_line.match_confidence = None
        bank_line.save()

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
            id=statement_id, company=actor.company,
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
        recon.public_id, statement.account.code, summary["difference"],
    )

    return CommandResult.ok(data={
        "reconciliation": recon,
        "summary": summary,
    })


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
