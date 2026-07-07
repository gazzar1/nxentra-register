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


_SEVERITY_RANK = {
    ReconciliationException.Severity.LOW: 0,
    ReconciliationException.Severity.MEDIUM: 1,
    ReconciliationException.Severity.HIGH: 2,
    ReconciliationException.Severity.CRITICAL: 3,
}


def _create_exception(company, **kwargs):
    """Create a ReconciliationException, deduplicating by reference.

    On a dedup hit the open row is REFRESHED (title/description/amount/details)
    when the new detection differs — a variance that grew from 50 to 5,000 must
    not keep a stale title/amount on the open row (PR-D). Severity only ever
    UPGRADES: a shrinking-but-still-open anomaly keeps its peak severity so it
    can't silently drop off an operator's severity-sorted triage view.
    """
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
            update_fields = []
            for field_name in ("title", "description", "details"):
                new_value = kwargs.get(field_name)
                if new_value and getattr(existing, field_name) != new_value:
                    setattr(existing, field_name, new_value)
                    update_fields.append(field_name)
            new_severity = kwargs.get("severity")
            if new_severity and _SEVERITY_RANK.get(new_severity, 0) > _SEVERITY_RANK.get(existing.severity, 0):
                existing.severity = new_severity
                update_fields.append("severity")
            new_amount = kwargs.get("amount")
            if new_amount is not None and existing.amount != new_amount:
                existing.amount = new_amount
                update_fields.append("amount")
            if update_fields:
                existing.save(update_fields=[*update_fields, "updated_at"])
            return existing

    exc = ReconciliationException.objects.create(company=company, **kwargs)
    logger.info(
        "Created reconciliation exception: %s [%s] for %s",
        exc.title,
        exc.exception_type,
        company.slug,
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
                    # Same shape sync_payout_variance_exception writes, so the two
                    # producers of this dedup key never churn each other's details.
                    "payout_batch_id": payout_id_str,
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
        created.extend(
            _detect_platform_discrepancies(
                company,
                "shopify",
                shopify_payouts,
                shopify_reconcile,
                "shopify_payout_id",
                date_from,
                date_to,
            )
        )
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
        created.extend(
            _detect_platform_discrepancies(
                company,
                "stripe",
                stripe_payouts,
                stripe_reconcile,
                "stripe_payout_id",
                date_from,
                date_to,
            )
        )
    except ImportError:
        pass

    return created


def sync_payout_variance_exception(
    company,
    *,
    platform,
    payout_pk,
    payout_batch_id,
    payout_date,
    currency,
    snapshot,
):
    """PR-D: route a PROVIDER_PAYOUT_RECONCILED snapshot's outcome into the
    exception queue (called by the adapter's emit path on every state change).

    - outcome "discrepancy" → upsert a PAYOUT_DISCREPANCY on EXACTLY the scan
      detector's dedup key (reference_type=f"{platform}_payout", reference_id=
      legacy payout pk), so event-driven production and the 30-day
      detect_payout_discrepancies scan fold onto the same open row. The key
      stays legacy-shaped in PR-D deliberately — re-keying to the canonical
      ProviderPayout pk now would fork open rows away from the still-running
      scan; details carry payout_batch_id so rows stay meaningful after C4.
    - outcome "verified" → auto-resolve open PAYOUT_DISCREPANCY / FEE_VARIANCE
      rows for that payout (machine-detected facts close when the fact clears;
      the scan never closed these types).
    """
    outcome = snapshot.get("outcome") or ""
    if outcome == "verified":
        return resolve_payout_variance_exceptions(company, platform=platform, payout_pk=payout_pk)
    if outcome != "discrepancy":
        return None

    gross_variance = Decimal(str(snapshot.get("gross_variance") or "0"))
    fee_variance = Decimal(str(snapshot.get("fee_variance") or "0"))
    net_variance = Decimal(str(snapshot.get("net_variance") or "0"))
    unmatched = int(snapshot.get("unmatched_count") or 0)

    # Same strings reconcile_payout builds, so the scan path and this path
    # write identical rows in the normal (no-drift) case.
    discrepancies = []
    if gross_variance != 0:
        discrepancies.append(f"Gross variance: {gross_variance}")
    if fee_variance != 0:
        discrepancies.append(f"Fee variance: {fee_variance}")
    if net_variance != 0:
        discrepancies.append(f"Net variance: {net_variance}")
    if unmatched > 0:
        discrepancies.append(f"{unmatched} unmatched transaction(s)")

    severity = ReconciliationException.Severity.HIGH
    if abs(net_variance) > Decimal("100"):
        severity = ReconciliationException.Severity.CRITICAL

    return _create_exception(
        company,
        exception_type=ReconciliationException.ExceptionType.PAYOUT_DISCREPANCY,
        severity=severity,
        title=f"Payout discrepancy: {net_variance} on {payout_date}",
        description=(
            f"{platform.title()} payout {payout_batch_id} has a net variance of "
            f"{net_variance}. {'; '.join(discrepancies)}"
        ),
        amount=abs(net_variance),
        currency=currency,
        exception_date=payout_date,
        platform=platform,
        reference_type=f"{platform}_payout",
        reference_id=payout_pk,
        reference_label=f"{platform.title()} payout {payout_batch_id}",
        # Byte-identical shape to _detect_platform_discrepancies' details: both
        # producers share the dedup key, and _create_exception refreshes details
        # on difference — divergent shapes would churn on every alternation.
        details={
            "payout_id": payout_batch_id,
            "payout_batch_id": payout_batch_id,
            "gross_variance": str(gross_variance),
            "fee_variance": str(fee_variance),
            "net_variance": str(net_variance),
            "matched_transactions": int(snapshot.get("matched_count") or 0),
            "unmatched_transactions": unmatched,
            "discrepancies": discrepancies,
        },
    )


def resolve_payout_variance_exceptions(company, *, platform, payout_pk):
    """Auto-resolve open payout-variance exceptions for a payout that now
    reconciles clean. Returns the number resolved.

    ESCALATED rows are deliberately excluded (matching auto_resolve_matched):
    an operator explicitly parked those for review — a machine verdict must
    not close them behind their back.
    """
    open_rows = ReconciliationException.objects.filter(
        company=company,
        exception_type__in=[
            ReconciliationException.ExceptionType.PAYOUT_DISCREPANCY,
            ReconciliationException.ExceptionType.FEE_VARIANCE,
        ],
        reference_type=f"{platform}_payout",
        reference_id=payout_pk,
        status__in=[
            ReconciliationException.Status.OPEN,
            ReconciliationException.Status.IN_PROGRESS,
        ],
    )
    resolved = 0
    for exc in open_rows:
        exc.status = ReconciliationException.Status.RESOLVED
        exc.resolved_at = timezone.now()
        exc.resolution_note = "Auto-resolved: payout reconciled clean."
        exc.save(update_fields=["status", "resolved_at", "resolution_note", "updated_at"])
        resolved += 1
    return resolved


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

    # F1 follow-up (2026-07-07): CLEARING_BALANCE is a machine-detected fact,
    # so it must close when the fact clears. The detector skips zero balances
    # entirely, so without this pass an exception opened on a mid-cycle
    # residual stayed OPEN forever at its stale peak amount after the payout
    # drained the account. (ESCALATED rows are deliberately left alone, per
    # the existing convention.)
    open_clearing_excs = ReconciliationException.objects.filter(
        company=company,
        exception_type=ReconciliationException.ExceptionType.CLEARING_BALANCE,
        status__in=[
            ReconciliationException.Status.OPEN,
            ReconciliationException.Status.IN_PROGRESS,
        ],
        reference_type="account",
        reference_id__isnull=False,
    )
    if open_clearing_excs.exists():
        from projections.models import AccountBalance

        for exc in open_clearing_excs:
            balance_now = (
                AccountBalance.objects.filter(company=company, account_id=exc.reference_id)
                .values_list("balance", flat=True)
                .first()
            )
            if balance_now is None or balance_now == 0:
                exc.status = ReconciliationException.Status.RESOLVED
                exc.resolved_at = timezone.now()
                exc.resolution_note = "Auto-resolved: clearing balance returned to zero."
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
        from accounting.mappings import ModuleAccountMapping
        from accounting.models import Account
        from projections.models import AccountBalance
    except ImportError:
        return []

    clearing_roles = ["SHOPIFY_CLEARING", "STRIPE_CLEARING"]

    # F1 (2026-07-07): clearing accounts are keyed by ModuleAccountMapping
    # role, NOT Account.role — onboarding seeds 11500/11510 with
    # role=LIQUIDITY (accounts/commands.py), so filtering Account.role alone
    # matched nothing on any real onboarded company and this detector never
    # fired. Source from the mapping first; keep the legacy Account.role
    # filter as a fallback union for pre-mapping data and fixtures.
    clearing_account_roles: dict[int, str] = {}
    mapped = ModuleAccountMapping.objects.filter(
        company=company,
        role__in=clearing_roles,
        account__isnull=False,
    ).select_related("account")
    for m in mapped:
        if not m.account.is_header:
            clearing_account_roles.setdefault(m.account_id, m.role)

    legacy_accounts = Account.objects.filter(
        company=company,
        role__in=clearing_roles,
        is_header=False,
    )
    for acct in legacy_accounts:
        clearing_account_roles.setdefault(acct.id, acct.role)

    clearing_accounts = Account.objects.filter(id__in=clearing_account_roles.keys())

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

        clearing_role = clearing_account_roles[acct.id]
        platform = "shopify" if "SHOPIFY" in clearing_role else "stripe"
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
                "role": clearing_role,
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
