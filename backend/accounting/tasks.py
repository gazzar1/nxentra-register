# accounting/tasks.py
"""
Celery tasks for automated accounting operations.

Tasks:
- run_currency_revaluation: Monthly/period-end task that automatically
  calculates and posts unrealized FX gains/losses for all active companies
  with foreign currency balances.
"""

import logging
from collections import defaultdict
from datetime import date as date_type
from decimal import Decimal

from celery import shared_task

from accounts.models import Company
from accounts.rls import rls_bypass

logger = logging.getLogger(__name__)


@shared_task(
    name="accounting.run_currency_revaluation",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def run_currency_revaluation(
    self,
    company_id: int | None = None,
    revaluation_date: str | None = None,
    auto_reverse: bool = True,
) -> dict:
    """
    Automated currency revaluation for one or all active companies.

    For each company with foreign currency journal lines:
    1. Calculate unrealized FX gains/losses at current rates
    2. Create and post an ADJUSTMENT journal entry
    3. Optionally create an auto-reversing entry on the first day of the next period

    Args:
        company_id: If provided, run for this company only. Otherwise all active companies.
        revaluation_date: ISO date string. Defaults to today.
        auto_reverse: If True, create reversing entry on first day of next period.

    Returns:
        Summary dict with results per company.
    """
    reval_date = date_type.fromisoformat(revaluation_date) if revaluation_date else date_type.today()

    with rls_bypass():
        if company_id:
            companies = list(Company.objects.filter(id=company_id, is_active=True))
        else:
            companies = list(Company.objects.filter(is_active=True))

    results = {}
    for company in companies:
        try:
            result = _revalue_company(company, reval_date, auto_reverse)
            results[company.name] = result
        except Exception:
            logger.exception("Currency revaluation failed for company %s", company.name)
            results[company.name] = {"status": "error", "error": "See logs for details"}

    return {
        "revaluation_date": reval_date.isoformat(),
        "companies_processed": len(companies),
        "results": results,
    }


def _revalue_company(company: Company, reval_date: date_type, auto_reverse: bool) -> dict:
    """Run currency revaluation for a single company."""
    from django.db.models import Avg, Sum
    from django.db.models.functions import Coalesce

    from accounting.models import ExchangeRate, JournalEntry, JournalLine

    functional_currency = company.functional_currency or company.default_currency

    # Check if revaluation already exists for this date
    reval_memo = f"Currency revaluation as of {reval_date.isoformat()}"
    existing = JournalEntry.objects.filter(
        company=company,
        memo=reval_memo,
        status__in=[JournalEntry.Status.DRAFT, JournalEntry.Status.POSTED],
    ).first()
    if existing:
        return {"status": "skipped", "reason": f"Revaluation already exists (#{existing.id})"}

    # Get FX accounts to exclude from revaluation
    from accounting.mappings import ModuleAccountMapping

    core_mapping = ModuleAccountMapping.get_mapping(company, "core")
    exclude_account_ids = set()
    for role in ("FX_GAIN", "FX_LOSS", "FX_ROUNDING"):
        acct = core_mapping.get(role)
        if acct:
            exclude_account_ids.add(acct.id)

    # Find all posted journal lines with foreign currencies
    foreign_lines = (
        JournalLine.objects.filter(
            company=company,
            entry__status="POSTED",
            entry__date__lte=reval_date,
        )
        .exclude(currency="")
        .exclude(currency=functional_currency)
        .exclude(account__requires_counterparty=True)
        .exclude(account_id__in=exclude_account_ids)
        .values("account__id", "account__code", "account__name", "currency")
        .annotate(
            foreign_debit=Coalesce(Sum("debit"), Decimal("0")),
            foreign_credit=Coalesce(Sum("credit"), Decimal("0")),
            total_amount_currency=Coalesce(Sum("amount_currency"), Decimal("0")),
            avg_exchange_rate=Avg("exchange_rate"),
        )
    )

    adjustments = []
    for group in foreign_lines:
        account_id = group["account__id"]
        line_currency = group["currency"]
        functional_debit = group["foreign_debit"]
        functional_credit = group["foreign_credit"]
        foreign_amount = group["total_amount_currency"]
        avg_rate = group["avg_exchange_rate"]

        current_functional_balance = functional_debit - functional_credit

        # Back-calculate foreign balance if not stored
        if foreign_amount == Decimal("0") and current_functional_balance != Decimal("0"):
            if avg_rate and avg_rate != Decimal("0"):
                foreign_amount = (current_functional_balance / avg_rate).quantize(Decimal("0.01"))
            else:
                continue

        current_rate = ExchangeRate.get_rate(company, line_currency, functional_currency, reval_date)
        if not current_rate:
            logger.warning(
                "No %s→%s rate for company %s on %s, skipping",
                line_currency,
                functional_currency,
                company.name,
                reval_date,
            )
            continue

        revalued_balance = (foreign_amount * current_rate).quantize(Decimal("0.01"))
        unrealized = revalued_balance - current_functional_balance

        if abs(unrealized) < Decimal("0.01"):
            continue

        adjustments.append(
            {
                "account_id": account_id,
                "account_code": group["account__code"],
                "currency": line_currency,
                "current_rate": str(current_rate),
                "unrealized_gain_loss": unrealized,
            }
        )

    if not adjustments:
        return {"status": "no_adjustments", "message": "No foreign currency adjustments needed"}

    # Resolve FX gain/loss accounts
    from accounting.models import Account

    fx_gain_account = (
        core_mapping.get("FX_GAIN")
        or Account.objects.filter(
            company=company,
            role="FINANCIAL_INCOME",
            is_postable=True,
        ).first()
    )
    fx_loss_account = (
        core_mapping.get("FX_LOSS")
        or Account.objects.filter(
            company=company,
            role="FINANCIAL_EXPENSE",
            is_postable=True,
        ).first()
    )
    fx_rounding_account = core_mapping.get("FX_ROUNDING")

    if not fx_gain_account or not fx_loss_account:
        return {"status": "error", "error": "FX Gain/Loss accounts not configured"}

    # Build a system actor for automated posting
    from accounts.authz import ActorContext

    admin_user = (
        company.memberships.filter(
            role__in=["owner", "admin", "OWNER", "ADMIN"],
        )
        .select_related("user")
        .first()
    )
    if not admin_user:
        admin_user = company.memberships.select_related("user").first()
    if not admin_user:
        return {"status": "error", "error": "No user found for company"}

    actor = ActorContext(user=admin_user.user, company=company, membership=admin_user)

    # Build JE lines
    from accounting.commands import create_journal_entry, post_journal_entry, save_journal_entry_complete

    lines = []
    for adj in adjustments:
        unrealized = adj["unrealized_gain_loss"]
        if unrealized > 0:
            lines.append(
                {
                    "account_id": adj["account_id"],
                    "description": f"FX revaluation {adj['currency']} @ {adj['current_rate']}",
                    "debit": str(unrealized),
                    "credit": "0",
                    "currency": adj["currency"],
                }
            )
        else:
            lines.append(
                {
                    "account_id": adj["account_id"],
                    "description": f"FX revaluation {adj['currency']} @ {adj['current_rate']}",
                    "debit": "0",
                    "credit": str(abs(unrealized)),
                    "currency": adj["currency"],
                }
            )

    # Offset entries per currency
    gains_by_currency = defaultdict(Decimal)
    losses_by_currency = defaultdict(Decimal)
    for adj in adjustments:
        amt = adj["unrealized_gain_loss"]
        if amt > 0:
            gains_by_currency[adj["currency"]] += amt
        elif amt < 0:
            losses_by_currency[adj["currency"]] += abs(amt)

    for curr, gain in gains_by_currency.items():
        lines.append(
            {
                "account_id": fx_gain_account.id,
                "description": f"Unrealized FX gain ({curr})",
                "debit": "0",
                "credit": str(gain),
                "currency": curr,
            }
        )

    for curr, loss in losses_by_currency.items():
        lines.append(
            {
                "account_id": fx_loss_account.id,
                "description": f"Unrealized FX loss ({curr})",
                "debit": str(loss),
                "credit": "0",
                "currency": curr,
            }
        )

    # Rounding line
    total_debit = sum(Decimal(l["debit"]) for l in lines)
    total_credit = sum(Decimal(l["credit"]) for l in lines)
    diff = total_debit - total_credit
    if diff != 0 and abs(diff) <= Decimal("1.00"):
        rounding_account = fx_rounding_account or fx_loss_account
        if diff > 0:
            lines.append(
                {
                    "account_id": rounding_account.id,
                    "description": "FX rounding difference",
                    "debit": "0",
                    "credit": str(abs(diff)),
                }
            )
        else:
            lines.append(
                {
                    "account_id": rounding_account.id,
                    "description": "FX rounding difference",
                    "debit": str(abs(diff)),
                    "credit": "0",
                }
            )

    # Resolve fiscal period
    from projections.models import FiscalPeriod

    fp = FiscalPeriod.objects.filter(
        company=company,
        start_date__lte=reval_date,
        end_date__gte=reval_date,
        period_type=FiscalPeriod.PeriodType.NORMAL,
    ).first()
    period = fp.period if fp else reval_date.month

    import uuid as _uuid

    nonce = str(_uuid.uuid4())[:8]

    result = create_journal_entry(
        actor=actor,
        date=reval_date,
        memo=reval_memo,
        memo_ar=f"إعادة تقييم العملات بتاريخ {reval_date.isoformat()} [{nonce}]",
        lines=lines,
        kind="ADJUSTMENT",
        currency=functional_currency,
        period=period,
    )

    if not result.success:
        return {"status": "error", "error": f"Failed to create JE: {result.error}"}

    entry = result.data
    save_result = save_journal_entry_complete(actor=actor, entry_id=entry.id)
    if not save_result.success:
        return {"status": "error", "error": f"Failed to save JE: {save_result.error}"}

    entry.refresh_from_db()
    post_result = post_journal_entry(actor=actor, entry_id=entry.id)
    if not post_result.success:
        return {"status": "partial", "entry_id": entry.id, "error": f"JE created but not posted: {post_result.error}"}

    entry.refresh_from_db()
    result_data = {
        "status": "posted",
        "entry_id": entry.id,
        "entry_number": entry.entry_number,
        "adjustments_count": len(adjustments),
        "total_gain_loss": str(sum(a["unrealized_gain_loss"] for a in adjustments)),
    }

    # Auto-reverse
    if auto_reverse and fp:
        next_period = FiscalPeriod.objects.filter(
            company=company,
            fiscal_year=fp.fiscal_year,
            period=fp.period + 1,
            period_type=FiscalPeriod.PeriodType.NORMAL,
        ).first()
        if not next_period:
            next_period = FiscalPeriod.objects.filter(
                company=company,
                fiscal_year=fp.fiscal_year + 1,
                period=1,
                period_type=FiscalPeriod.PeriodType.NORMAL,
            ).first()

        if next_period:
            reversal_date = next_period.start_date
            reversal_lines = []
            for line in lines:
                reversal_lines.append(
                    {
                        "account_id": line["account_id"],
                        "description": f"Reversal: {line['description']}",
                        "debit": line["credit"],
                        "credit": line["debit"],
                        "currency": line.get("currency"),
                    }
                )

            try:
                rev_result = create_journal_entry(
                    actor=actor,
                    date=reversal_date,
                    memo=f"Reversal of revaluation {reval_date.isoformat()}",
                    lines=reversal_lines,
                    kind="ADJUSTMENT",
                    currency=functional_currency,
                    period=next_period.period,
                )
                if rev_result.success:
                    rev_entry = rev_result.data
                    rev_save = save_journal_entry_complete(actor=actor, entry_id=rev_entry.id)
                    if rev_save.success:
                        rev_entry.refresh_from_db()
                        rev_post = post_journal_entry(actor=actor, entry_id=rev_entry.id)
                        if rev_post.success:
                            rev_entry.refresh_from_db()
                            result_data["reversal_entry_id"] = rev_entry.id
                            result_data["reversal_entry_number"] = rev_entry.entry_number
                            result_data["reversal_date"] = reversal_date.isoformat()
            except Exception as e:
                logger.warning("Auto-reverse failed for company %s: %s", company.name, e)

    return result_data
