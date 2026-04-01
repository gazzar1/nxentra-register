# bank_connector/matching.py
"""
Payout matching engine.

Matches imported bank transactions to platform payouts (Stripe/Shopify)
using amount + date proximity + reference matching with confidence scores.

Hero feature: "Payout Explainer" — breaks down a payout into its
component charges, refunds, fees, and adjustments.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import BankTransaction

# Lazy imports for accounting models (avoid circular imports)
# These are imported inside functions that need them.

logger = logging.getLogger(__name__)

# Confidence thresholds
CONFIDENCE_EXACT = 100       # Amount + date + reference all match
CONFIDENCE_HIGH = 90         # Amount + date match (within 1 day)
CONFIDENCE_MEDIUM = 75       # Amount + date proximity (within 5 days)
CONFIDENCE_LOW = 55          # Amount match only
AUTO_MATCH_THRESHOLD = 75    # Minimum confidence for auto-match


# =============================================================================
# Payout Discovery
# =============================================================================

def _get_stripe_payouts(company, date_from=None, date_to=None):
    """Get Stripe payouts for matching."""
    try:
        from stripe_connector.models import StripePayout
    except ImportError:
        return []

    qs = StripePayout.objects.filter(company=company)
    if date_from:
        qs = qs.filter(payout_date__gte=date_from - timedelta(days=5))
    if date_to:
        qs = qs.filter(payout_date__lte=date_to + timedelta(days=5))
    return [
        {
            "id": p.id,
            "platform": "stripe",
            "payout_id": p.stripe_payout_id,
            "gross_amount": p.gross_amount,
            "fees": p.fees,
            "net_amount": p.net_amount,
            "currency": p.currency,
            "payout_date": p.payout_date,
            "status": p.stripe_status,
            "journal_entry_id": str(p.journal_entry_id) if p.journal_entry_id else None,
        }
        for p in qs
    ]


def _get_shopify_payouts(company, date_from=None, date_to=None):
    """Get Shopify payouts for matching."""
    try:
        from shopify_connector.models import ShopifyPayout
    except ImportError:
        return []

    qs = ShopifyPayout.objects.filter(company=company)
    if date_from:
        qs = qs.filter(payout_date__gte=date_from - timedelta(days=5))
    if date_to:
        qs = qs.filter(payout_date__lte=date_to + timedelta(days=5))
    return [
        {
            "id": p.id,
            "platform": "shopify",
            "payout_id": str(p.shopify_payout_id),
            "gross_amount": p.gross_amount,
            "fees": p.fees,
            "net_amount": p.net_amount,
            "currency": p.currency,
            "payout_date": p.payout_date,
            "status": p.shopify_status,
            "journal_entry_id": str(p.journal_entry_id) if p.journal_entry_id else None,
            # Shopify has fee breakdown
            "charges_gross": p.charges_gross,
            "refunds_gross": p.refunds_gross,
            "adjustments_gross": p.adjustments_gross,
            "charges_fee": p.charges_fee,
            "refunds_fee": p.refunds_fee,
            "adjustments_fee": p.adjustments_fee,
        }
        for p in qs
    ]


def get_all_payouts(company, date_from=None, date_to=None):
    """Get all platform payouts (Stripe + Shopify) for a company."""
    payouts = []
    payouts.extend(_get_stripe_payouts(company, date_from, date_to))
    payouts.extend(_get_shopify_payouts(company, date_from, date_to))
    return payouts


# =============================================================================
# Matching Algorithm
# =============================================================================

def _compute_confidence(bank_tx, payout):
    """
    Compute match confidence between a bank transaction and a platform payout.

    Factors:
    - Amount match (required): bank amount == payout net_amount
    - Date proximity: same day = +30, within 2 days = +20, within 5 = +10
    - Reference match: payout ID found in bank description/reference = +20
    """
    # Amount must match (with tolerance for rounding — 0.05)
    bank_amount = abs(bank_tx.amount)
    payout_amount = abs(payout["net_amount"])
    if abs(bank_amount - payout_amount) > Decimal("0.05"):
        return 0

    confidence = 50  # Base: amounts match

    # Date proximity
    days_diff = abs((bank_tx.transaction_date - payout["payout_date"]).days)
    if days_diff == 0:
        confidence += 30
    elif days_diff <= 2:
        confidence += 20
    elif days_diff <= 5:
        confidence += 10
    else:
        return 0  # Too far apart

    # Reference matching — check if payout ID appears in bank description/reference
    payout_id_str = str(payout["payout_id"]).lower()
    bank_text = (bank_tx.description + " " + bank_tx.reference).lower()
    if payout_id_str in bank_text:
        confidence += 20

    # Platform name in description (weaker signal)
    platform = payout["platform"].lower()
    if platform in bank_text or "stripe" in bank_text or "shopify" in bank_text:
        confidence += 5

    return min(confidence, CONFIDENCE_EXACT)


def get_match_suggestions(company, bank_transaction_id):
    """
    Get candidate payout matches for a specific bank transaction.

    Returns list of payouts with confidence scores, sorted by confidence desc.
    """
    try:
        tx = BankTransaction.objects.get(pk=bank_transaction_id, company=company)
    except BankTransaction.DoesNotExist:
        return []

    # Only match credit (deposit) transactions to payouts
    if tx.amount <= 0:
        return []

    # Get payouts in a reasonable date range
    payouts = get_all_payouts(
        company,
        date_from=tx.transaction_date - timedelta(days=10),
        date_to=tx.transaction_date + timedelta(days=10),
    )

    suggestions = []
    for payout in payouts:
        # Skip already-matched payouts
        if _is_payout_already_matched(company, payout):
            continue

        confidence = _compute_confidence(tx, payout)
        if confidence > 0:
            suggestions.append({
                **payout,
                "confidence": confidence,
                "payout_date": str(payout["payout_date"]),
            })

    suggestions.sort(key=lambda x: x["confidence"], reverse=True)
    return suggestions


def _is_payout_already_matched(company, payout):
    """Check if a payout is already matched to a bank transaction."""
    content_type = f"{payout['platform']}_payout"
    return BankTransaction.objects.filter(
        company=company,
        matched_content_type=content_type,
        matched_object_id=payout["id"],
        status="MATCHED",
    ).exists()


# =============================================================================
# Journal Entry Reconciliation
# =============================================================================

def _reconcile_payout_je(company, platform, payout_obj, bank_tx):
    """
    When a bank transaction is matched to a payout:
    1. If the payout has a JE, mark the Cash/Bank line as reconciled
    2. If the payout has no JE, create one, then mark reconciled

    Returns dict with je_status info.
    """
    from accounting.models import JournalEntry
    from projections.write_barrier import projection_writes_allowed

    je_public_id = payout_obj.journal_entry_id
    je = None

    # Step 1: Find existing JE or create one
    if je_public_id:
        try:
            je = JournalEntry.objects.get(
                public_id=je_public_id,
                status=JournalEntry.Status.POSTED,
            )
        except JournalEntry.DoesNotExist:
            logger.warning(
                "Payout %s has journal_entry_id %s but JE not found or not POSTED",
                payout_obj.pk, je_public_id,
            )

    if not je:
        # Create a JE for this payout
        je = _create_payout_je(company, platform, payout_obj)
        if not je:
            logger.warning(
                "Could not create JE for %s payout %s — account mapping may be missing",
                platform, payout_obj.pk,
            )
            return {"je_status": "no_je", "je_id": None, "reconciled": False}

    # Step 2: Find the Cash/Bank line in the JE and mark reconciled
    # For positive payouts: Cash/Bank is the debit line
    # For negative payouts: Cash/Bank is the credit line
    # Use the LIQUIDITY role to identify the bank account line
    all_lines = list(je.lines.select_related("account").all())
    logger.info(
        "JE %s has %d lines: %s",
        je.entry_number, len(all_lines),
        [(l.line_no, l.account.code, l.account.role, l.reconciled) for l in all_lines],
    )

    cash_line = None
    for l in all_lines:
        if l.account.role == "LIQUIDITY" and not l.reconciled:
            cash_line = l
            break

    if not cash_line:
        # Fallback: find by debit > 0 (most payouts are positive)
        for l in all_lines:
            if l.debit > 0 and not l.reconciled:
                cash_line = l
                break

    if cash_line:
        logger.info(
            "Marking line %d (account %s) as reconciled for bank tx %s",
            cash_line.line_no, cash_line.account.code, bank_tx.id,
        )
        with projection_writes_allowed():
            cash_line.reconciled = True
            cash_line.reconciled_date = bank_tx.transaction_date
            cash_line.save(update_fields=["reconciled", "reconciled_date"])
        # Verify the save persisted
        cash_line.refresh_from_db()
        logger.info(
            "After save: line %d reconciled=%s reconciled_date=%s",
            cash_line.line_no, cash_line.reconciled, cash_line.reconciled_date,
        )

        logger.info(
            "Reconciled JE %s line %s for %s payout %s ↔ bank tx %s",
            je.entry_number, cash_line.line_no, platform, payout_obj.pk, bank_tx.id,
        )
        return {
            "je_status": "reconciled",
            "je_id": str(je.public_id),
            "je_number": je.entry_number,
            "reconciled": True,
        }

    logger.warning(
        "JE %s has no unreconciled Cash/Bank line for payout %s",
        je.entry_number, payout_obj.pk,
    )
    return {
        "je_status": "no_cash_line",
        "je_id": str(je.public_id),
        "reconciled": False,
    }


def _create_payout_je(company, platform, payout_obj):
    """
    Create a journal entry for a payout that doesn't have one.

    Uses the same account mapping and structure as PlatformAccountingProjection:
      DR Cash/Bank        (net_amount)
      DR Processing Fees  (fees)
        CR Platform Clearing  (gross_amount)
    """
    from accounting.mappings import ModuleAccountMapping
    from platform_connectors.je_builder import JELine, JERequest, build_journal_entry

    # Module keys in DB: "shopify_connector", "stripe_connector"
    module_key = f"{platform}_connector"
    mapping = ModuleAccountMapping.get_mapping(company, module_key)
    if not mapping:
        logger.warning("No account mapping found for module %s", module_key)
        return None

    # Clearing account role varies by platform: SHOPIFY_CLEARING, STRIPE_CLEARING
    clearing_role = f"{platform.upper()}_CLEARING"
    clearing = mapping.get(clearing_role)
    cash_bank = mapping.get("CASH_BANK")
    fees_account = mapping.get("PAYMENT_PROCESSING_FEES")

    if not clearing or not cash_bank:
        logger.warning(
            "Missing account mapping for %s: CLEARING=%s, CASH_BANK=%s",
            module_key, clearing, cash_bank,
        )
        return None

    net_amount = payout_obj.net_amount
    fees = payout_obj.fees
    gross_amount = payout_obj.gross_amount

    # Build payout ID for memo
    if platform == "stripe":
        payout_id_str = payout_obj.stripe_payout_id
    elif platform == "shopify":
        payout_id_str = str(payout_obj.shopify_payout_id)
    else:
        payout_id_str = str(payout_obj.pk)

    memo = f"{platform.title()} payout: {payout_id_str}"

    lines = []
    if net_amount >= 0:
        # Normal payout: DR Cash, DR Fees, CR Clearing
        lines.append(JELine(
            account=cash_bank,
            description=f"Payout deposit: {payout_id_str}",
            debit=net_amount,
        ))
        if fees > 0 and fees_account:
            lines.append(JELine(
                account=fees_account,
                description=f"Processing fees: {payout_id_str}",
                debit=fees,
            ))
        lines.append(JELine(
            account=clearing,
            description=f"Payout settlement: {payout_id_str}",
            credit=gross_amount,
        ))
    else:
        # Negative payout: DR Clearing, CR Cash
        lines.append(JELine(
            account=clearing,
            description=f"Negative payout: {payout_id_str}",
            debit=abs(gross_amount),
        ))
        if fees > 0 and fees_account:
            lines.append(JELine(
                account=fees_account,
                description=f"Processing fees: {payout_id_str}",
                debit=fees,
            ))
        lines.append(JELine(
            account=cash_bank,
            description=f"Payout withdrawal: {payout_id_str}",
            credit=abs(net_amount),
        ))

    je = build_journal_entry(JERequest(
        company=company,
        entry_date=payout_obj.payout_date,
        memo=memo,
        source_module=f"{platform}_connector",
        source_document=payout_id_str,
        currency=payout_obj.currency,
        lines=lines,
        projection_name="bank_reconciliation",
        posted_by_email="system@reconciliation",
    ))

    if je:
        # Store JE reference on the payout
        payout_obj.journal_entry_id = je.public_id
        payout_obj.save(update_fields=["journal_entry_id"])
        logger.info(
            "Created JE %s for %s payout %s",
            je.entry_number, platform, payout_id_str,
        )

    return je


# =============================================================================
# Auto-Matching
# =============================================================================

def auto_match_transactions(company, bank_account_id=None):
    """
    Auto-match unmatched bank deposits to platform payouts.

    Returns dict with match results.
    """
    qs = BankTransaction.objects.filter(
        company=company,
        status="UNMATCHED",
        amount__gt=0,  # Only match deposits
    )
    if bank_account_id:
        qs = qs.filter(bank_account_id=bank_account_id)

    unmatched = list(qs)
    if not unmatched:
        return {"matched": 0, "total": 0, "matches": []}

    # Get date range from unmatched transactions
    dates = [tx.transaction_date for tx in unmatched]
    date_from = min(dates)
    date_to = max(dates)

    # Get all payouts in range
    payouts = get_all_payouts(company, date_from, date_to)

    matched_count = 0
    matches = []

    for tx in unmatched:
        best_payout = None
        best_confidence = 0

        for payout in payouts:
            if _is_payout_already_matched(company, payout):
                continue

            confidence = _compute_confidence(tx, payout)
            if confidence > best_confidence:
                best_confidence = confidence
                best_payout = payout

        if best_payout and best_confidence >= AUTO_MATCH_THRESHOLD:
            content_type = f"{best_payout['platform']}_payout"
            tx.status = "MATCHED"
            tx.matched_content_type = content_type
            tx.matched_object_id = best_payout["id"]
            tx.matched_at = timezone.now()
            tx.matched_by = "auto"
            tx.save()

            # Reconcile the payout's journal entry
            payout_obj = _get_payout_object(
                company, best_payout["platform"], best_payout["id"]
            )
            je_result = {}
            if payout_obj:
                je_result = _reconcile_payout_je(
                    company, best_payout["platform"], payout_obj, tx
                )

            matches.append({
                "bank_transaction_id": tx.id,
                "payout_platform": best_payout["platform"],
                "payout_id": best_payout["payout_id"],
                "confidence": best_confidence,
                "amount": str(tx.amount),
                "je_reconciled": je_result.get("reconciled", False),
            })

            # Remove from available payouts to prevent double-matching
            payouts.remove(best_payout)
            matched_count += 1

    logger.info(
        "Auto-matched %d/%d bank transactions for company %s",
        matched_count, len(unmatched), company.id,
    )

    # Auto-resolve any exceptions for newly matched items
    if matched_count > 0:
        try:
            from .exceptions import auto_resolve_matched
            auto_resolve_matched(company)
        except Exception:
            logger.exception("Failed to auto-resolve exceptions after matching")

    return {
        "matched": matched_count,
        "total": len(unmatched),
        "matches": matches,
    }


def manual_match(company, bank_transaction_id, platform, payout_id):
    """Manually match a bank transaction to a payout."""
    try:
        tx = BankTransaction.objects.get(pk=bank_transaction_id, company=company)
    except BankTransaction.DoesNotExist:
        return {"error": "Bank transaction not found."}

    if tx.status == "MATCHED":
        return {"error": "Transaction is already matched."}

    content_type = f"{platform}_payout"

    # Verify payout exists
    payout_obj = _get_payout_object(company, platform, payout_id)
    if not payout_obj:
        return {"error": "Payout not found."}

    tx.status = "MATCHED"
    tx.matched_content_type = content_type
    tx.matched_object_id = payout_id
    tx.matched_at = timezone.now()
    tx.matched_by = "manual"
    tx.save()

    # Reconcile the payout's journal entry
    je_result = _reconcile_payout_je(company, platform, payout_obj, tx)

    # Auto-resolve any exceptions for this match
    try:
        from .exceptions import auto_resolve_matched
        auto_resolve_matched(company)
    except Exception:
        logger.exception("Failed to auto-resolve exceptions after manual match")

    return {
        "status": "matched",
        "bank_transaction_id": tx.id,
        **je_result,
    }


# =============================================================================
# Payout Explainer
# =============================================================================

def _get_payout_object(company, platform, payout_id):
    """Get the actual payout model instance."""
    if platform == "stripe":
        try:
            from stripe_connector.models import StripePayout
            return StripePayout.objects.get(pk=payout_id, company=company)
        except Exception:
            return None
    elif platform == "shopify":
        try:
            from shopify_connector.models import ShopifyPayout
            return ShopifyPayout.objects.get(pk=payout_id, company=company)
        except Exception:
            return None
    return None


def explain_payout(company, platform, payout_id):
    """
    Break down a payout into its component transactions.

    Returns the "Payout Explainer" data:
    - Payout summary (gross, fees, net)
    - List of component transactions (charges, refunds, adjustments)
    - Any discrepancies
    - Matched bank transaction (if any)
    """
    payout_obj = _get_payout_object(company, platform, payout_id)
    if not payout_obj:
        return {"error": "Payout not found."}

    result = {
        "platform": platform,
        "payout_id": payout_id,
    }

    if platform == "stripe":
        result.update(_explain_stripe_payout(company, payout_obj))
    elif platform == "shopify":
        result.update(_explain_shopify_payout(company, payout_obj))

    # Find matched bank transaction
    content_type = f"{platform}_payout"
    bank_tx = BankTransaction.objects.filter(
        company=company,
        matched_content_type=content_type,
        matched_object_id=payout_id,
        status="MATCHED",
    ).first()

    if bank_tx:
        result["bank_transaction"] = {
            "id": bank_tx.id,
            "date": str(bank_tx.transaction_date),
            "description": bank_tx.description,
            "amount": str(bank_tx.amount),
            "bank_account": bank_tx.bank_account.account_name,
        }
    else:
        result["bank_transaction"] = None

    return result


def _explain_stripe_payout(company, payout):
    """Break down a Stripe payout into transactions."""
    try:
        from stripe_connector.models import StripePayoutTransaction
    except ImportError:
        return {"transactions": [], "summary": {}}

    txns = StripePayoutTransaction.objects.filter(
        company=company,
        payout=payout,
    ).order_by("transaction_type", "-amount")

    charges_total = Decimal("0")
    refunds_total = Decimal("0")
    fees_total = Decimal("0")
    adjustments_total = Decimal("0")
    transactions = []

    for txn in txns:
        tx_data = {
            "id": txn.id,
            "type": txn.transaction_type,
            "amount": str(txn.amount),
            "fee": str(txn.fee),
            "net": str(txn.net),
            "source_id": txn.source_id,
            "verified": txn.verified,
        }
        transactions.append(tx_data)

        if txn.transaction_type == "charge":
            charges_total += txn.amount
            fees_total += txn.fee
        elif txn.transaction_type == "refund":
            refunds_total += abs(txn.amount)
        elif txn.transaction_type == "adjustment":
            adjustments_total += txn.amount
        # payout type transactions are the payout itself, skip

    computed_net = charges_total - refunds_total - fees_total + adjustments_total
    actual_net = payout.net_amount
    discrepancy = actual_net - computed_net

    return {
        "payout_external_id": payout.stripe_payout_id,
        "gross_amount": str(payout.gross_amount),
        "fees": str(payout.fees),
        "net_amount": str(payout.net_amount),
        "currency": payout.currency,
        "payout_date": str(payout.payout_date),
        "payout_status": payout.stripe_status,
        "transactions": transactions,
        "summary": {
            "charges": str(charges_total),
            "refunds": str(refunds_total),
            "fees": str(fees_total),
            "adjustments": str(adjustments_total),
            "computed_net": str(computed_net),
            "actual_net": str(actual_net),
            "discrepancy": str(discrepancy),
            "has_discrepancy": abs(discrepancy) > Decimal("0.01"),
        },
        "transaction_count": len(transactions),
    }


def _explain_shopify_payout(company, payout):
    """Break down a Shopify payout into transactions."""
    try:
        from shopify_connector.models import ShopifyPayoutTransaction
    except ImportError:
        return {"transactions": [], "summary": {}}

    txns = ShopifyPayoutTransaction.objects.filter(
        company=company,
        payout=payout,
    ).order_by("transaction_type", "-amount")

    charges_total = Decimal("0")
    refunds_total = Decimal("0")
    fees_total = Decimal("0")
    adjustments_total = Decimal("0")
    transactions = []

    for txn in txns:
        tx_data = {
            "id": txn.id,
            "type": txn.transaction_type,
            "amount": str(txn.amount),
            "fee": str(txn.fee),
            "net": str(txn.net),
            "source_id": str(txn.shopify_transaction_id),
            "verified": txn.verified,
        }
        transactions.append(tx_data)

        if txn.transaction_type == "charge":
            charges_total += txn.amount
            fees_total += txn.fee
        elif txn.transaction_type == "refund":
            refunds_total += abs(txn.amount)
        elif txn.transaction_type == "adjustment":
            adjustments_total += txn.amount

    computed_net = charges_total - refunds_total - fees_total + adjustments_total
    actual_net = payout.net_amount
    discrepancy = actual_net - computed_net

    return {
        "payout_external_id": str(payout.shopify_payout_id),
        "gross_amount": str(payout.gross_amount),
        "fees": str(payout.fees),
        "net_amount": str(payout.net_amount),
        "currency": payout.currency,
        "payout_date": str(payout.payout_date),
        "payout_status": payout.shopify_status,
        # Shopify fee breakdown
        "fee_breakdown": {
            "charges_gross": str(payout.charges_gross),
            "charges_fee": str(payout.charges_fee),
            "refunds_gross": str(payout.refunds_gross),
            "refunds_fee": str(payout.refunds_fee),
            "adjustments_gross": str(payout.adjustments_gross),
            "adjustments_fee": str(payout.adjustments_fee),
        },
        "transactions": transactions,
        "summary": {
            "charges": str(charges_total),
            "refunds": str(refunds_total),
            "fees": str(fees_total),
            "adjustments": str(adjustments_total),
            "computed_net": str(computed_net),
            "actual_net": str(actual_net),
            "discrepancy": str(discrepancy),
            "has_discrepancy": abs(discrepancy) > Decimal("0.01"),
        },
        "transaction_count": len(transactions),
    }


# =============================================================================
# Reconciliation Overview
# =============================================================================

def get_reconciliation_overview(company):
    """
    Get a high-level reconciliation overview.

    Returns:
    - Bank transaction stats (total, matched, unmatched)
    - Platform payout stats (total, matched to bank, unmatched)
    - Unmatched amounts
    - Match rate
    """
    # Bank transaction stats
    bank_qs = BankTransaction.objects.filter(company=company)
    total_bank_txns = bank_qs.count()
    matched_bank = bank_qs.filter(status="MATCHED").count()
    unmatched_bank = bank_qs.filter(status="UNMATCHED").count()
    excluded_bank = bank_qs.filter(status="EXCLUDED").count()

    # Unmatched deposit total (potential payout matches)
    unmatched_deposits = bank_qs.filter(
        status="UNMATCHED", amount__gt=0
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    # Unmatched withdrawal total
    unmatched_withdrawals = bank_qs.filter(
        status="UNMATCHED", amount__lt=0
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    # Platform payout stats
    stripe_payouts = _get_stripe_payouts(company)
    shopify_payouts = _get_shopify_payouts(company)
    all_payouts = stripe_payouts + shopify_payouts

    total_payouts = len(all_payouts)
    matched_payouts = 0
    unmatched_payout_amount = Decimal("0")

    for p in all_payouts:
        if _is_payout_already_matched(company, p):
            matched_payouts += 1
        else:
            unmatched_payout_amount += p["net_amount"]

    unmatched_payouts = total_payouts - matched_payouts
    match_rate = round(matched_payouts / total_payouts * 100, 1) if total_payouts else 0

    # Exception queue summary
    from .models import ReconciliationException
    open_statuses = [
        ReconciliationException.Status.OPEN,
        ReconciliationException.Status.IN_PROGRESS,
        ReconciliationException.Status.ESCALATED,
    ]
    open_exceptions = ReconciliationException.objects.filter(
        company=company, status__in=open_statuses,
    )
    open_count = open_exceptions.count()
    critical_count = open_exceptions.filter(
        severity=ReconciliationException.Severity.CRITICAL
    ).count()

    return {
        "bank": {
            "total": total_bank_txns,
            "matched": matched_bank,
            "unmatched": unmatched_bank,
            "excluded": excluded_bank,
            "unmatched_deposits": str(unmatched_deposits),
            "unmatched_withdrawals": str(abs(unmatched_withdrawals)),
        },
        "payouts": {
            "total": total_payouts,
            "matched": matched_payouts,
            "unmatched": unmatched_payouts,
            "unmatched_amount": str(unmatched_payout_amount),
            "stripe_count": len(stripe_payouts),
            "shopify_count": len(shopify_payouts),
        },
        "exceptions": {
            "open": open_count,
            "critical": critical_count,
        },
        "match_rate": match_rate,
    }


def get_unmatched_payouts(company):
    """Get all payouts that haven't been matched to bank transactions."""
    all_payouts = get_all_payouts(company)
    unmatched = []

    for p in all_payouts:
        if not _is_payout_already_matched(company, p):
            p["payout_date"] = str(p["payout_date"])
            # Convert Decimal fields to strings for JSON
            for key in ("gross_amount", "fees", "net_amount"):
                p[key] = str(p[key])
            unmatched.append(p)

    unmatched.sort(key=lambda x: x["payout_date"], reverse=True)
    return unmatched
