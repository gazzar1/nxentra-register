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
from datetime import date, timedelta
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
            id=statement_id,
            company=actor.company,
        )
    except BankStatement.DoesNotExist:
        return CommandResult.fail("Statement not found.")

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

            # Remove payout from candidates to prevent double-matching
            candidates.remove(best_payout)
            matched += 1

            logger.info(
                "Platform pre-pass: matched bank line %s to Shopify payout %s",
                bank_line.id,
                best_payout.shopify_payout_id,
            )

    return matched


def _settlement_prepass_match(
    company,
    statement: BankStatement,
    unmatched_bank_lines: list,
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

    Returns the number of bank lines matched (and clearance JEs created).
    """
    from accounting.mappings import ModuleAccountMapping

    if not unmatched_bank_lines:
        return 0

    # Find POSTED PaymentSettlement JEs in the period whose EBD DR line
    # has not yet been reconciled. Each JE has source_module="payment_settlement"
    # and source_document="{provider}:{batch_id}".
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
        return 0

    # Build (entry, ebd_line, net_amount, batch_id) tuples for unmatched
    # entries. EBD is the only DR-debit asset line whose account has the
    # EXPECTED_BANK_DEPOSIT role on the company's mapping.
    ebd_account = ModuleAccountMapping.get_account(company, "shopify_connector", "EXPECTED_BANK_DEPOSIT")
    if not ebd_account:
        return 0

    # Pre-collect candidates: (entry, ebd_line, net, batch_id)
    candidates: list[tuple] = []
    for entry in settlement_entries:
        ebd_line = entry.lines.filter(account=ebd_account, reconciled=False).first()
        if not ebd_line:
            continue
        # Source document = "{provider}:{batch_id}"
        source_doc = entry.source_document or ""
        batch_id = source_doc.split(":", 1)[1] if ":" in source_doc else source_doc
        candidates.append((entry, ebd_line, ebd_line.debit, batch_id))

    if not candidates:
        return 0

    matched = 0

    for bank_line in unmatched_bank_lines:
        if bank_line.match_status != BankStatementLine.MatchStatus.UNMATCHED:
            continue

        # A16: near-match. Try exact-amount first; if none, look for a
        # candidate within tolerance (max 2% of expected, capped at 500
        # of the company's currency unit). Bank deposits don't always
        # equal the expected EBD because of extra gateway fees, bank
        # wire fees, chargebacks, etc. We still match within tolerance
        # and record the difference for the merchant to categorize.
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

        # Prefer batch-id-in-description match if available (highest
        # confidence: even if multiple deposits have the same amount on
        # close dates, the batch ID disambiguates).
        descr = (bank_line.description or "").lower()
        batch_match = next(
            (c for c in amount_matches if c[3] and c[3].lower() in descr),
            None,
        )
        if batch_match:
            entry, ebd_line, expected_amount, batch_id = batch_match
            confidence = CONFIDENCE_EXACT
        else:
            # Fall back to amount + date proximity (single best within 7 days).
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

        # A16: near-matches always require operator review even when the
        # amount/date confidence is high. Knock confidence below
        # AUTO_MATCH_THRESHOLD if it'd otherwise pass — but DON'T skip
        # the match, just flag the row.
        if confidence < AUTO_MATCH_THRESHOLD:
            continue

        # Create the clearance JE for the ACTUAL bank amount (what really
        # arrived). For near-matches the EBD residual stays open until
        # the merchant categorizes the difference and the adjustment JE
        # is posted. For exact matches, EBD drains in one shot.
        clearance_je_line = _create_settlement_clearance_je(
            company=company,
            settlement_entry=entry,
            bank_account=statement.account,
            ebd_account=ebd_account,
            net_amount=bank_line.amount,
            batch_id=batch_id,
            statement_date=statement.statement_date,
            value_date=bank_line.line_date,
        )
        if not clearance_je_line:
            logger.warning(
                "Settlement match: failed to create clearance JE for batch %s — skipping bank line %s",
                batch_id,
                bank_line.id,
            )
            continue

        difference = (expected_amount - bank_line.amount) if is_near else Decimal("0")
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

            # For exact match: EBD line is fully drained, mark reconciled.
            # For near match: EBD line still has a residual — leave
            # reconciled=False until the merchant categorizes the diff.
            if not is_near:
                JournalLine.objects.filter(pk=ebd_line.pk).update(
                    reconciled=True,
                    reconciled_date=statement.statement_date,
                )

        # Remove this candidate from future loop iterations.
        candidates = [c for c in candidates if c[0].id != entry.id]
        matched += 1

        logger.info(
            "Settlement match: bank line %s -> clearance JE for batch %s (confidence=%s, near=%s, diff=%s)",
            bank_line.id,
            batch_id,
            confidence,
            is_near,
            difference,
        )

    return matched


def _difference_tolerance(expected: Decimal) -> Decimal:
    """A16: near-match tolerance for bank deposits vs expected EBD lines.

    2% of the expected amount, capped at 500 currency units (EGP, USD…).
    Below this gap we still match and ask the operator to categorize the
    difference; above it we leave both lines unmatched (likely a wrong
    pairing rather than a real near-match).
    """
    pct = (abs(expected) * Decimal("0.02")).quantize(Decimal("0.01"))
    return min(pct, Decimal("500"))


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
) -> JournalLine | None:
    """A14b: create the second-stage clearance JE that drains Expected
    Bank Deposit into the merchant's actual bank.

    Posted via the standard command chain so it goes through period
    validation, dimension checks (none on EBD/Bank), and event emission.
    Returns the DR Bank JournalLine (the one bank-rec should mark
    reconciled), or None if the JE failed to post.

    Stamps source_module='payment_settlement_clearance' and
    source_document=settlement_entry.source_document for traceability.
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
