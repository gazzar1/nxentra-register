# bank_connector/exceptions.py
"""
Reconciliation exception detection and creation.

Called from matching.py, shopify reconciliation, and the scan command
to auto-detect and persist reconciliation exceptions.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from .models import ReconciliationException

logger = logging.getLogger(__name__)


def _create_exception(company, **kwargs):
    """Create a ReconciliationException, deduplicating by reference."""
    ref_type = kwargs.get("reference_type", "")
    ref_id = kwargs.get("reference_id")
    exc_type = kwargs["exception_type"]

    # Deduplicate: don't create duplicate open exceptions for the same reference
    if ref_type and ref_id:
        existing = ReconciliationException.objects.filter(
            company=company,
            exception_type=exc_type,
            reference_type=ref_type,
            reference_id=ref_id,
            status__in=[
                ReconciliationException.Status.OPEN,
                ReconciliationException.Status.IN_PROGRESS,
                ReconciliationException.Status.ESCALATED,
            ],
        ).first()
        if existing:
            # Update details if they changed
            if kwargs.get("details") and existing.details != kwargs["details"]:
                existing.details = kwargs["details"]
                existing.save(update_fields=["details", "updated_at"])
            return existing

    exc = ReconciliationException.objects.create(company=company, **kwargs)
    logger.info(
        "Created reconciliation exception: %s [%s] for %s",
        exc.title, exc.exception_type, company.slug,
    )
    return exc


def detect_unmatched_bank_transactions(company, age_days=7):
    """
    Detect bank deposits that have been unmatched for more than age_days.

    These are potential missing payout matches.
    """
    cutoff = date.today() - timedelta(days=age_days)
    from .models import BankTransaction

    stale = BankTransaction.objects.filter(
        company=company,
        status="UNMATCHED",
        amount__gt=0,  # Only deposits
        transaction_date__lte=cutoff,
    ).select_related("bank_account")

    created = []
    for tx in stale:
        severity = ReconciliationException.Severity.MEDIUM
        if tx.amount > Decimal("1000"):
            severity = ReconciliationException.Severity.HIGH
        if (date.today() - tx.transaction_date).days > 30:
            severity = ReconciliationException.Severity.CRITICAL

        exc = _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.UNMATCHED_BANK_TX,
            severity=severity,
            title=f"Unmatched deposit: {tx.amount} on {tx.transaction_date}",
            description=(
                f"Bank deposit of {tx.amount} on {tx.transaction_date} "
                f"({tx.bank_account.account_name}) has no matching payout after "
                f"{(date.today() - tx.transaction_date).days} days.\n"
                f"Description: {tx.description}"
            ),
            amount=tx.amount,
            currency=tx.bank_account.currency,
            exception_date=tx.transaction_date,
            platform="",
            reference_type="bank_transaction",
            reference_id=tx.id,
            reference_label=f"Bank tx {tx.description[:60]}",
            details={
                "bank_account": tx.bank_account.account_name,
                "description": tx.description,
                "reference": tx.reference,
                "days_unmatched": (date.today() - tx.transaction_date).days,
            },
        )
        created.append(exc)
    return created


def detect_unmatched_payouts(company, age_days=5):
    """
    Detect platform payouts that have no matching bank transaction.
    """
    from .matching import _is_payout_already_matched, get_all_payouts

    cutoff = date.today() - timedelta(days=age_days)
    payouts = get_all_payouts(company)
    created = []

    for p in payouts:
        if _is_payout_already_matched(company, p):
            continue
        if p["payout_date"] > cutoff:
            continue  # Still fresh, give it time

        days_old = (date.today() - p["payout_date"]).days
        severity = ReconciliationException.Severity.MEDIUM
        if abs(p["net_amount"]) > Decimal("1000"):
            severity = ReconciliationException.Severity.HIGH
        if days_old > 14:
            severity = ReconciliationException.Severity.CRITICAL

        exc = _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.UNMATCHED_PAYOUT,
            severity=severity,
            title=f"Unmatched {p['platform']} payout: {p['net_amount']} on {p['payout_date']}",
            description=(
                f"{p['platform'].title()} payout {p['payout_id']} for "
                f"{p['net_amount']} {p.get('currency', 'USD')} on {p['payout_date']} "
                f"has no matching bank deposit after {days_old} days."
            ),
            amount=abs(p["net_amount"]),
            currency=p.get("currency", "USD"),
            exception_date=p["payout_date"],
            platform=p["platform"],
            reference_type=f"{p['platform']}_payout",
            reference_id=p["id"],
            reference_label=f"{p['platform'].title()} payout {p['payout_id']}",
            details={
                "payout_id": str(p["payout_id"]),
                "gross_amount": str(p["gross_amount"]),
                "fees": str(p["fees"]),
                "net_amount": str(p["net_amount"]),
                "days_unmatched": days_old,
            },
        )
        created.append(exc)
    return created


def _detect_platform_discrepancies(company, platform, payouts, reconcile_fn, payout_id_attr, date_from, date_to):
    """
    Generic discrepancy detection for a platform's payouts.
    """
    created = []
    for payout in payouts:
        recon = reconcile_fn(company, payout)
        payout_id_str = str(getattr(payout, payout_id_attr))

        if recon.status == "discrepancy":
            severity = ReconciliationException.Severity.HIGH
            if abs(recon.net_variance) > Decimal("100"):
                severity = ReconciliationException.Severity.CRITICAL

            exc = _create_exception(
                company,
                exception_type=ReconciliationException.ExceptionType.PAYOUT_DISCREPANCY,
                severity=severity,
                title=f"Payout discrepancy: {recon.net_variance} on {payout.payout_date}",
                description=(
                    f"{platform.title()} payout {payout_id_str} has a net variance of "
                    f"{recon.net_variance}. {'; '.join(recon.discrepancies)}"
                ),
                amount=abs(recon.net_variance),
                currency=payout.currency,
                exception_date=payout.payout_date,
                platform=platform,
                reference_type=f"{platform}_payout",
                reference_id=payout.id,
                reference_label=f"{platform.title()} payout {payout_id_str}",
                details={
                    "payout_id": payout_id_str,
                    "gross_variance": str(recon.gross_variance),
                    "fee_variance": str(recon.fee_variance),
                    "net_variance": str(recon.net_variance),
                    "matched_transactions": recon.matched_transactions,
                    "unmatched_transactions": recon.unmatched_transactions,
                    "discrepancies": recon.discrepancies,
                },
            )
            created.append(exc)

        # Also flag fee variances that aren't full discrepancies
        if recon.fee_variance != 0 and recon.status != "discrepancy":
            exc = _create_exception(
                company,
                exception_type=ReconciliationException.ExceptionType.FEE_VARIANCE,
                severity=ReconciliationException.Severity.LOW,
                title=f"Fee variance: {recon.fee_variance} on payout {payout_id_str}",
                description=(
                    f"{platform.title()} payout {payout_id_str} has a fee variance of "
                    f"{recon.fee_variance} (expected {payout.fees}, "
                    f"got {recon.transactions_fee_sum} from transactions)."
                ),
                amount=abs(recon.fee_variance),
                currency=payout.currency,
                exception_date=payout.payout_date,
                platform=platform,
                reference_type=f"{platform}_payout",
                reference_id=payout.id,
                reference_label=f"{platform.title()} payout {payout_id_str}",
                details={
                    "payout_id": payout_id_str,
                    "fee_variance": str(recon.fee_variance),
                },
            )
            created.append(exc)

    return created


def detect_payout_discrepancies(company, date_from=None, date_to=None):
    """
    Detect payouts where transaction-level reconciliation shows discrepancies.

    Checks both Shopify and Stripe payouts.
    """
    if not date_from:
        date_from = date.today() - timedelta(days=30)
    if not date_to:
        date_to = date.today()

    created = []

    # Shopify payouts
    try:
        from shopify_connector.models import ShopifyPayout
        from shopify_connector.reconciliation import reconcile_payout as shopify_reconcile

        shopify_payouts = ShopifyPayout.objects.filter(
            company=company,
            payout_date__gte=date_from,
            payout_date__lte=date_to,
        )
        created.extend(_detect_platform_discrepancies(
            company, "shopify", shopify_payouts,
            shopify_reconcile, "shopify_payout_id", date_from, date_to,
        ))
    except ImportError:
        pass

    # Stripe payouts
    try:
        from stripe_connector.models import StripePayout
        from stripe_connector.reconciliation import reconcile_payout as stripe_reconcile

        stripe_payouts = StripePayout.objects.filter(
            company=company,
            payout_date__gte=date_from,
            payout_date__lte=date_to,
        )
        created.extend(_detect_platform_discrepancies(
            company, "stripe", stripe_payouts,
            stripe_reconcile, "stripe_payout_id", date_from, date_to,
        ))
    except ImportError:
        pass

    return created


def auto_resolve_matched(company):
    """
    Auto-resolve exceptions whose underlying issue has been fixed.

    E.g., an UNMATCHED_BANK_TX exception where the bank tx is now MATCHED.
    """
    resolved = 0

    # Bank transactions that were unmatched but are now matched
    open_bank_excs = ReconciliationException.objects.filter(
        company=company,
        exception_type=ReconciliationException.ExceptionType.UNMATCHED_BANK_TX,
        status__in=[
            ReconciliationException.Status.OPEN,
            ReconciliationException.Status.IN_PROGRESS,
        ],
        reference_type="bank_transaction",
        reference_id__isnull=False,
    )
    from .models import BankTransaction
    for exc in open_bank_excs:
        try:
            tx = BankTransaction.objects.get(pk=exc.reference_id, company=company)
            if tx.status == "MATCHED":
                exc.status = ReconciliationException.Status.RESOLVED
                exc.resolved_at = timezone.now()
                exc.resolution_note = "Auto-resolved: bank transaction matched."
                exc.save(update_fields=["status", "resolved_at", "resolution_note", "updated_at"])
                resolved += 1
        except BankTransaction.DoesNotExist:
            pass

    # Payouts that are now matched
    open_payout_excs = ReconciliationException.objects.filter(
        company=company,
        exception_type=ReconciliationException.ExceptionType.UNMATCHED_PAYOUT,
        status__in=[
            ReconciliationException.Status.OPEN,
            ReconciliationException.Status.IN_PROGRESS,
        ],
        reference_id__isnull=False,
    )
    from .matching import _is_payout_already_matched
    for exc in open_payout_excs:
        payout_dict = {
            "platform": exc.platform,
            "id": exc.reference_id,
        }
        if _is_payout_already_matched(company, payout_dict):
            exc.status = ReconciliationException.Status.RESOLVED
            exc.resolved_at = timezone.now()
            exc.resolution_note = "Auto-resolved: payout matched to bank transaction."
            exc.save(update_fields=["status", "resolved_at", "resolution_note", "updated_at"])
            resolved += 1

    return resolved


def detect_clearing_balance_anomalies(company):
    """
    Detect if platform clearing accounts have unexpected non-zero balances.

    A clearing account should tend toward zero over time. A persistent balance
    indicates unmatched payouts or missing journal entries.
    """
    try:
        from accounting.models import Account
        from projections.models import AccountBalance
    except ImportError:
        return []

    clearing_roles = ["SHOPIFY_CLEARING", "STRIPE_CLEARING"]
    clearing_accounts = Account.objects.filter(
        company=company,
        role__in=clearing_roles,
        is_header=False,
    )

    created = []
    for acct in clearing_accounts:
        try:
            bal = AccountBalance.objects.get(company=company, account=acct)
        except AccountBalance.DoesNotExist:
            continue

        if bal.balance == 0:
            continue

        abs_balance = abs(bal.balance)
        severity = ReconciliationException.Severity.LOW
        if abs_balance > Decimal("500"):
            severity = ReconciliationException.Severity.MEDIUM
        if abs_balance > Decimal("5000"):
            severity = ReconciliationException.Severity.HIGH

        platform = "shopify" if "SHOPIFY" in acct.role else "stripe"
        exc = _create_exception(
            company,
            exception_type=ReconciliationException.ExceptionType.CLEARING_BALANCE,
            severity=severity,
            title=f"Clearing balance: {acct.code} {acct.name} = {bal.balance}",
            description=(
                f"{platform.title()} clearing account {acct.code} ({acct.name}) "
                f"has a balance of {bal.balance}. This may indicate unreconciled "
                f"payouts or missing journal entries."
            ),
            amount=abs_balance,
            currency=company.default_currency if hasattr(company, "default_currency") else "USD",
            exception_date=date.today(),
            platform=platform,
            reference_type="account",
            reference_id=acct.id,
            reference_label=f"{acct.code} {acct.name}",
            details={
                "account_code": acct.code,
                "account_name": acct.name,
                "balance": str(bal.balance),
                "role": acct.role,
            },
        )
        created.append(exc)
    return created


def scan_all(company):
    """
    Run all exception detection checks for a company.

    Returns summary of exceptions created and resolved.
    """
    created = []
    created.extend(detect_unmatched_bank_transactions(company))
    created.extend(detect_unmatched_payouts(company))
    created.extend(detect_payout_discrepancies(company))
    created.extend(detect_clearing_balance_anomalies(company))
    resolved = auto_resolve_matched(company)

    return {
        "created": len(created),
        "resolved": resolved,
        "open": ReconciliationException.objects.filter(
            company=company,
            status__in=[
                ReconciliationException.Status.OPEN,
                ReconciliationException.Status.IN_PROGRESS,
                ReconciliationException.Status.ESCALATED,
            ],
        ).count(),
    }
