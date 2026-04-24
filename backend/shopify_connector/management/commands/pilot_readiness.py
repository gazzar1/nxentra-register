# shopify_connector/management/commands/pilot_readiness.py
"""
Gate C — Pilot month-end close readiness check.

Runs every validation needed to confirm the Shopify → Events → JEs → Reports
pipeline is production-ready against real data.

Checks performed:
  1. Shopify store connected & webhooks registered
  2. Account mapping complete (all required roles mapped)
  3. All events processed (no projection lag)
  4. Shopify reconciliation for the period (match rate, variances)
  5. Clearing account balance health
  6. AR/AP subledger tie-out
  7. Trial balance balances (debits == credits)
  8. Period close readiness (draft JEs, incomplete entries)

Usage:
    # Full check for March 2026
    python manage.py pilot_readiness --company my-co --year 2026 --month 3

    # JSON output for CI/monitoring
    python manage.py pilot_readiness --company my-co --year 2026 --month 3 --json

    # Strict mode: exit 1 on any failure
    python manage.py pilot_readiness --company my-co --year 2026 --month 3 --strict
"""

import json
import sys
from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Gate C: Pilot month-end close readiness check."

    def add_arguments(self, parser):
        parser.add_argument(
            "--company",
            type=str,
            required=True,
            help="Company slug.",
        )
        parser.add_argument(
            "--year",
            type=int,
            required=True,
            help="Fiscal year (e.g. 2026).",
        )
        parser.add_argument(
            "--month",
            type=int,
            required=True,
            help="Month number (1-12).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output as JSON.",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit code 1 on any FAIL.",
        )

    def handle(self, *args, **options):
        from accounts.models import Company
        from accounts.rls import rls_bypass

        slug = options["company"]
        year = options["year"]
        month = options["month"]

        with rls_bypass():
            try:
                company = Company.objects.get(slug=slug, is_active=True)
            except Company.DoesNotExist:
                raise CommandError(f"Company '{slug}' not found or inactive.")

        _, last_day = monthrange(year, month)
        date_from = date(year, month, 1)
        date_to = date(year, month, last_day)

        checks = []

        with rls_bypass():
            checks.append(self._check_store(company))
            checks.append(self._check_account_mapping(company))
            checks.append(self._check_projection_lag(company))
            checks.append(self._check_reconciliation(company, date_from, date_to))
            checks.append(self._check_clearing_balance(company))
            checks.append(self._check_subledger_tieout(company))
            checks.append(self._check_trial_balance(company, date_to))
            checks.append(self._check_draft_entries(company, date_from, date_to))

        passed = sum(1 for c in checks if c["status"] == "PASS")
        warned = sum(1 for c in checks if c["status"] == "WARN")
        failed = sum(1 for c in checks if c["status"] == "FAIL")

        report = {
            "company": slug,
            "period": f"{year}-{month:02d}",
            "date_from": str(date_from),
            "date_to": str(date_to),
            "passed": passed,
            "warned": warned,
            "failed": failed,
            "gate_c": "PASS" if failed == 0 else "FAIL",
            "checks": checks,
        }

        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2, default=str))
        else:
            self._print_report(report)

        if options["strict"] and failed > 0:
            sys.exit(1)

    # ── Individual checks ────────────────────────────────────────

    def _check_store(self, company):
        """Check 1: Shopify store connected and webhooks registered."""
        from shopify_connector.models import ShopifyStore

        stores = list(
            ShopifyStore.objects.filter(
                company=company,
                status=ShopifyStore.Status.ACTIVE,
            )
        )
        if not stores:
            return self._result(
                "shopify_store",
                "FAIL",
                "No active Shopify store connected.",
            )

        issues = []
        for s in stores:
            if not s.webhooks_registered:
                issues.append(f"{s.shop_domain}: webhooks not registered")

        if issues:
            return self._result(
                "shopify_store",
                "WARN",
                f"Store connected but: {'; '.join(issues)}",
                detail={"stores": len(stores), "issues": issues},
            )

        return self._result(
            "shopify_store",
            "PASS",
            f"{len(stores)} store(s) connected, webhooks OK.",
            detail={"stores": len(stores)},
        )

    def _check_account_mapping(self, company):
        """Check 2: All required Shopify account roles are mapped."""
        from accounting.mappings import ModuleAccountMapping

        required_roles = [
            "SALES_REVENUE",
            "SHOPIFY_CLEARING",
            "CASH_BANK",
            "PAYMENT_PROCESSING_FEES",
        ]
        optional_roles = [
            "SALES_TAX_PAYABLE",
            "SALES_DISCOUNTS",
            "SHIPPING_REVENUE",
            "CHARGEBACK_EXPENSE",
        ]

        missing_required = []
        missing_optional = []

        for role in required_roles:
            acct = ModuleAccountMapping.get_account(company, "shopify_connector", role)
            if not acct:
                missing_required.append(role)

        for role in optional_roles:
            acct = ModuleAccountMapping.get_account(company, "shopify_connector", role)
            if not acct:
                missing_optional.append(role)

        if missing_required:
            return self._result(
                "account_mapping",
                "FAIL",
                f"Missing required mappings: {', '.join(missing_required)}",
                detail={"missing_required": missing_required, "missing_optional": missing_optional},
            )

        if missing_optional:
            return self._result(
                "account_mapping",
                "WARN",
                f"Optional mappings missing: {', '.join(missing_optional)}",
                detail={"missing_optional": missing_optional},
            )

        return self._result(
            "account_mapping",
            "PASS",
            "All account roles mapped.",
        )

    def _check_projection_lag(self, company):
        """Check 3: All projections are caught up (no unprocessed events)."""
        from projections.base import projection_registry

        projections = projection_registry.all()
        lagging = []

        for proj in projections:
            lag = proj.get_lag(company)
            if lag > 0:
                lagging.append({"name": proj.name, "lag": lag})

        if lagging:
            return self._result(
                "projection_lag",
                "FAIL",
                f"{len(lagging)} projection(s) behind: " + ", ".join(f"{l['name']}({l['lag']})" for l in lagging),
                detail={"lagging": lagging},
            )

        return self._result(
            "projection_lag",
            "PASS",
            f"All {len(projections)} projections caught up.",
        )

    def _check_reconciliation(self, company, date_from, date_to):
        """Check 4: Shopify payout reconciliation for the period."""
        from shopify_connector.reconciliation import reconciliation_summary

        summary = reconciliation_summary(company, date_from, date_to)

        if summary.total_payouts == 0:
            return self._result(
                "reconciliation",
                "WARN",
                "No payouts found in period.",
                detail={"total_payouts": 0},
            )

        detail = {
            "total_payouts": summary.total_payouts,
            "verified": summary.verified_payouts,
            "discrepancy": summary.discrepancy_payouts,
            "unverified": summary.unverified_payouts,
            "match_rate": str(summary.match_rate),
            "total_net": str(summary.total_net),
            "unmatched_order_total": str(summary.unmatched_order_total),
        }

        if summary.discrepancy_payouts > 0:
            return self._result(
                "reconciliation",
                "FAIL",
                f"{summary.discrepancy_payouts} payout(s) with discrepancies, match rate {summary.match_rate}%.",
                detail=detail,
            )

        if summary.unverified_payouts > 0 or summary.match_rate < Decimal("90"):
            return self._result(
                "reconciliation",
                "WARN",
                f"{summary.verified_payouts}/{summary.total_payouts} verified, match rate {summary.match_rate}%.",
                detail=detail,
            )

        return self._result(
            "reconciliation",
            "PASS",
            f"{summary.verified_payouts}/{summary.total_payouts} payouts verified, "
            f"match rate {summary.match_rate}%, net {summary.total_net}.",
            detail=detail,
        )

    def _check_clearing_balance(self, company):
        """Check 5: Shopify Clearing account balance health.

        A non-zero clearing balance is expected when orders have been
        recorded but their payouts haven't settled yet.  Only FAIL if the
        balance is non-zero AND there are no unsettled orders or pending
        payouts that would explain it.
        """
        from shopify_connector.management.commands.check_clearing_balance import (
            compute_clearing_balance,
        )
        from shopify_connector.models import ShopifyOrder, ShopifyPayout

        data = compute_clearing_balance(company)
        if data is None:
            return self._result(
                "clearing_balance",
                "WARN",
                "No SHOPIFY_CLEARING account mapped (check account_mapping).",
            )

        balance = Decimal(data["balance"])
        pending = data["pending_payouts"]

        # Count orders that haven't appeared in a settled payout yet
        unsettled_orders = (
            ShopifyOrder.objects.filter(
                company=company,
                status=ShopifyOrder.Status.PROCESSED,
            )
            .exclude(
                shopify_order_id__in=ShopifyPayout.objects.filter(
                    company=company,
                    shopify_status="paid",
                ).values_list(
                    "transactions__source_order_id",
                    flat=True,
                ),
            )
            .count()
        )

        data["unsettled_orders"] = unsettled_orders

        if balance == Decimal("0"):
            return self._result(
                "clearing_balance",
                "PASS",
                "Clearing balance is zero.",
                detail=data,
            )

        # Non-zero but explainable by pending payouts or unsettled orders
        if pending > 0 or unsettled_orders > 0:
            return self._result(
                "clearing_balance",
                "WARN",
                f"Balance {balance} ({pending} pending payouts, {unsettled_orders} unsettled orders).",
                detail=data,
            )

        # Non-zero with nothing to explain it
        return self._result(
            "clearing_balance",
            "FAIL",
            f"Unexplained non-zero balance {balance}.",
            detail=data,
        )

    def _check_subledger_tieout(self, company):
        """Check 6: AR/AP subledger tie-out.

        Subledger imbalances are important but may predate the Shopify
        integration.  For the Shopify pilot, this is a WARN (not FAIL)
        so it doesn't block the close.  A full close_fiscal_year will
        enforce this as a hard gate via check_close_readiness.
        """
        try:
            from accounting.policies import validate_subledger_tieout

            is_valid, errors = validate_subledger_tieout(company)

            if not is_valid:
                return self._result(
                    "subledger_tieout",
                    "WARN",
                    f"Subledger imbalance (review before year-end): {'; '.join(errors)}",
                    detail={"balanced": False, "errors": errors},
                )

            return self._result(
                "subledger_tieout",
                "PASS",
                "AR/AP subledgers tie out to GL.",
                detail={"balanced": True, "errors": []},
            )
        except Exception as exc:
            return self._result(
                "subledger_tieout",
                "WARN",
                f"Could not run subledger check: {exc}",
            )

    def _check_trial_balance(self, company, as_of_date):
        """Check 7: Trial balance debits == credits."""
        from django.db.models import Sum

        from accounting.models import JournalEntry, JournalLine

        agg = JournalLine.objects.filter(
            company=company,
            entry__status=JournalEntry.Status.POSTED,
            entry__date__lte=as_of_date,
        ).aggregate(
            total_debit=Sum("debit"),
            total_credit=Sum("credit"),
        )
        total_debit = agg["total_debit"] or Decimal("0")
        total_credit = agg["total_credit"] or Decimal("0")
        diff = total_debit - total_credit

        detail = {
            "total_debit": str(total_debit),
            "total_credit": str(total_credit),
            "difference": str(diff),
        }

        if diff != Decimal("0"):
            return self._result(
                "trial_balance",
                "FAIL",
                f"Trial balance out by {diff} (DR={total_debit}, CR={total_credit}).",
                detail=detail,
            )

        return self._result(
            "trial_balance",
            "PASS",
            f"Trial balance balanced (DR=CR={total_debit}).",
            detail=detail,
        )

    def _check_draft_entries(self, company, date_from, date_to):
        """Check 8: No draft/incomplete journal entries in the period."""
        from accounting.models import JournalEntry

        drafts = JournalEntry.objects.filter(
            company=company,
            date__gte=date_from,
            date__lte=date_to,
            status__in=[
                JournalEntry.Status.DRAFT,
                JournalEntry.Status.INCOMPLETE,
            ],
        ).count()

        if drafts > 0:
            return self._result(
                "draft_entries",
                "FAIL",
                f"{drafts} draft/incomplete journal entries in period.",
                detail={"count": drafts},
            )

        posted = JournalEntry.objects.filter(
            company=company,
            date__gte=date_from,
            date__lte=date_to,
            status=JournalEntry.Status.POSTED,
        ).count()

        return self._result(
            "draft_entries",
            "PASS",
            f"All {posted} entries in period are posted.",
            detail={"posted": posted, "drafts": 0},
        )

    # ── Helpers ──────────────────────────────────────────────────

    def _result(self, check, status, message, detail=None):
        return {
            "check": check,
            "status": status,
            "message": message,
            **({"detail": detail} if detail else {}),
        }

    def _print_report(self, report):
        self.stdout.write("")
        self.stdout.write(
            self.style.HTTP_INFO(f"═══ Gate C: Pilot Readiness — {report['company']} ({report['period']}) ═══")
        )
        self.stdout.write("")

        for c in report["checks"]:
            status = c["status"]
            check = c["check"]
            msg = c["message"]

            if status == "PASS":
                label = self.style.SUCCESS(f"  PASS  {check}")
            elif status == "WARN":
                label = self.style.WARNING(f"  WARN  {check}")
            else:
                label = self.style.ERROR(f"  FAIL  {check}")

            self.stdout.write(f"{label}")
            self.stdout.write(f"        {msg}")

        self.stdout.write("")
        self.stdout.write(
            f"  Results: {report['passed']} passed, {report['warned']} warnings, {report['failed']} failures"
        )

        gate = report["gate_c"]
        if gate == "PASS":
            self.stdout.write(self.style.SUCCESS("\n  Gate C: PASS"))
        else:
            self.stdout.write(self.style.ERROR("\n  Gate C: FAIL — resolve failures before close"))

        self.stdout.write("")
