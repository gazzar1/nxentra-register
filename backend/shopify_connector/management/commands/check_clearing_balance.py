"""
Management command: Check Shopify Clearing account balances.

Monitors whether the Shopify Clearing account has an unexpected balance
after all payouts are settled. A non-zero balance indicates a discrepancy
between order payments received and payouts deposited.

Usage:
    # Human-readable output
    python manage.py check_clearing_balance

    # JSON output (for monitoring pipelines)
    python manage.py check_clearing_balance --json

    # Fail on non-zero balance (for CI/alerting)
    python manage.py check_clearing_balance --strict

    # Check specific company
    python manage.py check_clearing_balance --company my-company
"""
import json
import logging
import sys
from decimal import Decimal

from django.core.management.base import BaseCommand

logger = logging.getLogger("nxentra.shopify.commands")


def compute_clearing_balance(company):
    """
    Compute the current balance of the Shopify Clearing account for a company.

    Returns dict with balance details or None if no clearing account is mapped.
    """
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import JournalLine, JournalEntry

    clearing_account = ModuleAccountMapping.get_account(
        company, "shopify_connector", "SHOPIFY_CLEARING",
    )
    if not clearing_account:
        return None

    # Sum all posted journal lines against the clearing account
    aggregation = (
        JournalLine.objects
        .filter(
            company=company,
            account=clearing_account,
            entry__status=JournalEntry.Status.POSTED,
        )
        .aggregate(
            total_debit=models_Sum("debit"),
            total_credit=models_Sum("credit"),
        )
    )
    total_debit = aggregation["total_debit"] or Decimal("0")
    total_credit = aggregation["total_credit"] or Decimal("0")
    balance = total_credit - total_debit  # Clearing is a current asset, credit-normal

    # Count pending payouts (settled but not yet deposited)
    from shopify_connector.models import ShopifyPayout
    pending_payouts = ShopifyPayout.objects.filter(
        company=company,
        shopify_status__in=["scheduled", "in_transit"],
    ).count()

    return {
        "account_code": clearing_account.code,
        "account_name": clearing_account.name,
        "total_debit": str(total_debit),
        "total_credit": str(total_credit),
        "balance": str(balance),
        "is_zero": balance == Decimal("0"),
        "pending_payouts": pending_payouts,
        "expected_zero": pending_payouts == 0,
    }


# Avoid name collision with django.db.models
from django.db.models import Sum as models_Sum


class Command(BaseCommand):
    help = "Check Shopify Clearing account balance for all companies."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output results as JSON.",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with code 1 if any company has unexpected non-zero balance.",
        )
        parser.add_argument(
            "--company",
            type=str,
            help="Check a specific company by slug.",
        )

    def handle(self, *args, **options):
        from accounts.models import Company
        from accounts.rls import rls_bypass

        output_json = options["json"]
        strict = options["strict"]
        company_slug = options.get("company")

        with rls_bypass():
            if company_slug:
                companies = list(Company.objects.filter(slug=company_slug, is_active=True))
                if not companies:
                    self.stderr.write(f"Company '{company_slug}' not found.")
                    sys.exit(2)
            else:
                companies = list(Company.objects.filter(is_active=True))

        results = []
        any_alert = False

        for company in companies:
            with rls_bypass():
                data = compute_clearing_balance(company)

            if data is None:
                results.append({
                    "company": company.slug,
                    "status": "skipped",
                    "reason": "No SHOPIFY_CLEARING account mapped.",
                })
                continue

            # Alert if balance is non-zero AND no payouts are pending
            is_alert = not data["is_zero"] and data["expected_zero"]
            if is_alert:
                any_alert = True

            results.append({
                "company": company.slug,
                "status": "ALERT" if is_alert else "ok",
                **data,
            })

            if is_alert:
                logger.warning(
                    "clearing_balance.nonzero",
                    extra={
                        "company": company.slug,
                        "balance": data["balance"],
                        "pending_payouts": data["pending_payouts"],
                    },
                )

        if output_json:
            self.stdout.write(json.dumps({"results": results}, indent=2))
        else:
            self._print_table(results)

        if strict and any_alert:
            self.stderr.write(
                self.style.ERROR("STRICT: Non-zero clearing balance with no pending payouts.")
            )
            sys.exit(1)

    def _print_table(self, results):
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("=== Shopify Clearing Balance Check ==="))
        self.stdout.write("")

        for r in results:
            company = r["company"]
            status = r["status"]

            if status == "ok":
                balance = r.get("balance", "0")
                pending = r.get("pending_payouts", 0)
                label = self.style.SUCCESS(
                    f"  {company}: OK (balance={balance}, pending_payouts={pending})"
                )
            elif status == "skipped":
                label = self.style.WARNING(
                    f"  {company}: SKIPPED ({r.get('reason', '')})"
                )
            else:
                balance = r.get("balance", "?")
                label = self.style.ERROR(
                    f"  {company}: ALERT — non-zero balance {balance} with no pending payouts"
                )

            self.stdout.write(label)

        self.stdout.write("")
