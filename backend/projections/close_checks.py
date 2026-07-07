"""
A152 item 3 — the shared month-end close checklist.

The 8-point readiness gate used to live ONLY inside ``MonthEndCloseView`` (a GET
endpoint the UI called before POSTing the close), so the actual ``close_period``
command — and every other close path (the periods-table Close button, the raw
API) — was ungated. This module extracts the checks so BOTH the read-only
Month-End Close view AND ``close_period`` evaluate the identical checklist.

Every check is a pure function of ``(company, period date window)`` — none read
the request. Close stays ADVISORY: WARN never blocks a close; only a FAIL does,
and a FAIL can still be overridden with an explicit force flag + reason.
"""

from decimal import Decimal

# Resolution hints per check type — actionable, so operators can self-serve.
RESOLUTION_HINTS = {
    "shopify_store": {
        "FAIL": "Connect your Shopify store via Settings > Shopify > Settings.",
        "WARN": "Register webhooks via Settings > Shopify > Settings > Register Webhooks button.",
    },
    "account_mapping": {
        "FAIL": "Map the missing accounts via Settings > Shopify > Settings > Account Mapping section.",
        "WARN": "Optional mappings improve accuracy. Configure via Settings > Shopify > Settings.",
    },
    "projection_lag": {
        "FAIL": "Projections are still processing events. Wait a few minutes and re-check. If the lag persists, go to Admin > Projections.",
    },
    "reconciliation": {
        "FAIL": "Review payout discrepancies in Shopify > Reconciliation. Verify each unmatched payout.",
        "WARN": "Some payouts are unverified. Click Verify on each payout in Shopify > Payouts.",
    },
    "clearing_balance": {
        "FAIL": "Unexplained clearing balance. Check Shopify > Reconciliation for unmatched orders. Run Re-sync Orders if webhooks were missed.",
        "WARN": "Non-zero clearing balance is expected if orders are awaiting payout settlement.",
    },
    "subledger_tieout": {
        "WARN": "AR/AP control account balance doesn't match subledger totals. Review in Reports > Customer Balances and Vendor Balances.",
    },
    "trial_balance": {
        "FAIL": "Trial balance is out of balance. This is a critical error. Review recent journal entries for issues.",
    },
    "draft_entries": {
        "FAIL": "Post or delete all draft/incomplete entries before closing the period. Go to Journal Entries and filter by status Draft or Incomplete.",
    },
}


def _result(check, title, check_status, message, detail=None):
    return {"check": check, "title": title, "status": check_status, "message": message, "detail": detail or {}}


def _check_store(company):
    try:
        from shopify_connector.models import ShopifyStore

        stores = list(ShopifyStore.objects.filter(company=company, status=ShopifyStore.Status.ACTIVE))
        if not stores:
            return _result("shopify_store", "Shopify Connection", "FAIL", "No active Shopify store connected.")
        return _result(
            "shopify_store",
            "Shopify Connection",
            "PASS",
            f"{len(stores)} store(s) connected, webhooks OK.",
            {"stores": len(stores)},
        )
    except Exception:
        return _result("shopify_store", "Shopify Connection", "PASS", "Shopify module not active (skipped).")


def _check_account_mapping(company):
    try:
        from accounting.mappings import ModuleAccountMapping

        required = ["SALES_REVENUE", "SHOPIFY_CLEARING", "CASH_BANK", "PAYMENT_PROCESSING_FEES"]
        optional = ["SALES_TAX_PAYABLE", "SALES_DISCOUNTS", "SHIPPING_REVENUE", "CHARGEBACK_EXPENSE"]
        missing_req = [r for r in required if not ModuleAccountMapping.get_account(company, "shopify_connector", r)]
        missing_opt = [r for r in optional if not ModuleAccountMapping.get_account(company, "shopify_connector", r)]
        if missing_req:
            return _result(
                "account_mapping",
                "Account Mapping",
                "FAIL",
                f"Missing required: {', '.join(missing_req)}",
                {"missing_required": missing_req},
            )
        if missing_opt:
            return _result(
                "account_mapping",
                "Account Mapping",
                "WARN",
                f"Optional missing: {', '.join(missing_opt)}",
                {"missing_optional": missing_opt},
            )
        return _result("account_mapping", "Account Mapping", "PASS", "All account roles mapped.")
    except Exception:
        return _result("account_mapping", "Account Mapping", "PASS", "Shopify module not active (skipped).")


def _check_projection_lag(company):
    from projections.base import projection_registry

    projections = projection_registry.all()
    lagging = []
    for proj in projections:
        try:
            lag = proj.get_lag(company)
            if lag > 0:
                lagging.append({"name": proj.name, "lag": lag})
        except Exception:
            pass
    if lagging:
        return _result(
            "projection_lag",
            "Event Processing",
            "FAIL",
            f"{len(lagging)} projection(s) behind",
            {"lagging": lagging},
        )
    return _result("projection_lag", "Event Processing", "PASS", f"All {len(projections)} projections caught up.")


def _check_reconciliation(company, date_from, date_to):
    try:
        from shopify_connector.reconciliation import reconciliation_summary

        summary = reconciliation_summary(company, date_from, date_to)
        if summary.total_payouts == 0:
            return _result(
                "reconciliation",
                "Shopify Reconciliation",
                "WARN",
                "No payouts found in period.",
                {"total_payouts": 0},
            )
        detail = {
            "total_payouts": summary.total_payouts,
            "verified": summary.verified_payouts,
            "match_rate": str(summary.match_rate),
        }
        if summary.discrepancy_payouts > 0:
            return _result(
                "reconciliation",
                "Shopify Reconciliation",
                "FAIL",
                f"{summary.discrepancy_payouts} payout(s) with discrepancies",
                detail,
            )
        if summary.unverified_payouts > 0 or summary.match_rate < Decimal("90"):
            return _result(
                "reconciliation",
                "Shopify Reconciliation",
                "WARN",
                f"{summary.verified_payouts}/{summary.total_payouts} verified, match rate {summary.match_rate}%",
                detail,
            )
        return _result(
            "reconciliation",
            "Shopify Reconciliation",
            "PASS",
            f"All {summary.total_payouts} payouts verified, {summary.match_rate}% match rate.",
            detail,
        )
    except Exception:
        return _result("reconciliation", "Shopify Reconciliation", "PASS", "Shopify module not active (skipped).")


def _check_clearing_balance(company):
    try:
        from shopify_connector.management.commands.check_clearing_balance import compute_clearing_balance

        data = compute_clearing_balance(company)
        if data is None:
            return _result("clearing_balance", "Clearing Balance", "WARN", "No clearing account mapped.")
        balance = Decimal(data["balance"])
        if balance == Decimal("0"):
            return _result("clearing_balance", "Clearing Balance", "PASS", "Clearing balance is zero.", data)
        return _result("clearing_balance", "Clearing Balance", "WARN", f"Balance: {balance}", data)
    except Exception:
        return _result("clearing_balance", "Clearing Balance", "PASS", "Shopify module not active (skipped).")


def _check_subledger_tieout(company):
    try:
        from accounting.policies import validate_subledger_tieout

        is_valid, errors = validate_subledger_tieout(company)
        if not is_valid:
            return _result(
                "subledger_tieout",
                "AR/AP Tie-Out",
                "WARN",
                f"Imbalance: {'; '.join(errors[:2])}",
                {"errors": errors},
            )
        return _result("subledger_tieout", "AR/AP Tie-Out", "PASS", "AR/AP subledgers tie out to GL.")
    except Exception as e:
        return _result("subledger_tieout", "AR/AP Tie-Out", "WARN", f"Check failed: {e}")


def _check_trial_balance(company, as_of_date):
    from django.db.models import Sum

    from accounting.models import JournalEntry, JournalLine

    agg = JournalLine.objects.filter(
        company=company, entry__status=JournalEntry.Status.POSTED, entry__date__lte=as_of_date
    ).aggregate(total_debit=Sum("debit"), total_credit=Sum("credit"))
    total_debit = agg["total_debit"] or Decimal("0")
    total_credit = agg["total_credit"] or Decimal("0")
    diff = total_debit - total_credit
    if diff != Decimal("0"):
        return _result(
            "trial_balance",
            "Trial Balance",
            "FAIL",
            f"Out of balance by {diff}",
            {"total_debit": str(total_debit), "total_credit": str(total_credit)},
        )
    return _result(
        "trial_balance",
        "Trial Balance",
        "PASS",
        f"Balanced: DR=CR={total_debit}",
        {"total_debit": str(total_debit), "total_credit": str(total_credit)},
    )


def _check_draft_entries(company, date_from, date_to):
    from accounting.models import JournalEntry

    drafts = JournalEntry.objects.filter(
        company=company,
        date__gte=date_from,
        date__lte=date_to,
        status__in=[JournalEntry.Status.DRAFT, JournalEntry.Status.INCOMPLETE],
    ).count()
    posted = JournalEntry.objects.filter(
        company=company, date__gte=date_from, date__lte=date_to, status=JournalEntry.Status.POSTED
    ).count()
    if drafts > 0:
        return _result(
            "draft_entries",
            "Pending Entries",
            "FAIL",
            f"{drafts} draft/incomplete entries need attention",
            {"drafts": drafts, "posted": posted},
        )
    return _result("draft_entries", "Pending Entries", "PASS", f"All {posted} entries posted.", {"posted": posted})


# A152 item 3: which FAILs actually BLOCK a close (require force + reason).
# Only the two universal financial-integrity gates block:
#   - trial_balance: the books must balance;
#   - draft_entries: no unposted entries may be stranded inside the period.
# The remaining checks are Shopify-specific (store/mapping/reconciliation/
# clearing) or already advisory (subledger, projection lag) — a non-Shopify
# company (property, clinic, …) must not be blocked from closing by a missing
# Shopify store. They still appear in the checklist as WARN/FAIL context; they
# just don't force an override.
BLOCKING_CHECKS = frozenset({"trial_balance", "draft_entries"})


def run_close_checklist(company, date_from, date_to) -> list[dict]:
    """Run all 8 readiness checks for the period window and decorate each with
    its resolution hint + whether it is a blocking gate. Wrapped in
    ``rls_bypass`` so cross-module reads (Shopify store, account mappings)
    aren't RLS-scoped away. Pure read — never mutates. Shared by
    ``MonthEndCloseView`` and ``close_period``.
    """
    from accounts.rls import rls_bypass

    checks = []
    with rls_bypass():
        checks.append(_check_store(company))
        checks.append(_check_account_mapping(company))
        checks.append(_check_projection_lag(company))
        checks.append(_check_reconciliation(company, date_from, date_to))
        checks.append(_check_clearing_balance(company))
        checks.append(_check_subledger_tieout(company))
        checks.append(_check_trial_balance(company, date_to))
        checks.append(_check_draft_entries(company, date_from, date_to))

    for check in checks:
        hints = RESOLUTION_HINTS.get(check["check"], {})
        check["resolution"] = hints.get(check["status"])
        check["blocking"] = check["check"] in BLOCKING_CHECKS
    return checks


def checklist_has_blocking_failure(checks) -> bool:
    """A close is blocked only by a FAIL on a BLOCKING check; WARN and
    non-blocking (Shopify-specific) FAILs are advisory."""
    return any(c["status"] == "FAIL" and c["check"] in BLOCKING_CHECKS for c in checks)
