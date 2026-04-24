"""
Management command: Scan for reconciliation exceptions.

Detects unmatched bank transactions, unmatched payouts, payout discrepancies,
and auto-resolves exceptions where the underlying issue has been fixed.

Usage:
    # All companies
    python manage.py scan_exceptions

    # Single company
    python manage.py scan_exceptions --company my-company

    # JSON output (for monitoring pipelines)
    python manage.py scan_exceptions --json

    # Fail if critical exceptions exist (for CI gates)
    python manage.py scan_exceptions --strict
"""

import json
import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Scan for reconciliation exceptions across all companies."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output results as JSON.",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with code 1 if any company has critical open exceptions.",
        )
        parser.add_argument(
            "--company",
            type=str,
            help="Scan a specific company by slug (default: all).",
        )

    def handle(self, *args, **options):
        from accounts.models import Company
        from accounts.rls import rls_bypass
        from bank_connector.exceptions import scan_all
        from bank_connector.models import ReconciliationException

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
        any_critical = False

        for company in companies:
            with rls_bypass():
                result = scan_all(company)
                result["company"] = company.slug

                # Check for critical open exceptions
                critical = ReconciliationException.objects.filter(
                    company=company,
                    severity=ReconciliationException.Severity.CRITICAL,
                    status__in=[
                        ReconciliationException.Status.OPEN,
                        ReconciliationException.Status.IN_PROGRESS,
                        ReconciliationException.Status.ESCALATED,
                    ],
                ).count()
                result["critical"] = critical
                if critical > 0:
                    any_critical = True

            results.append(result)

        if output_json:
            self.stdout.write(json.dumps({"results": results}, indent=2))
        else:
            self.stdout.write("")
            self.stdout.write(self.style.HTTP_INFO("=== Reconciliation Exception Scan ==="))
            self.stdout.write("")
            for r in results:
                status_str = (
                    self.style.ERROR(f"  CRITICAL({r['critical']})")
                    if r["critical"] > 0
                    else self.style.SUCCESS("  OK")
                )
                self.stdout.write(
                    f"  {r['company']}: {status_str}  created={r['created']} resolved={r['resolved']} open={r['open']}"
                )
            self.stdout.write("")

        if strict and any_critical:
            self.stderr.write(self.style.ERROR("STRICT MODE: Critical reconciliation exceptions found."))
            sys.exit(1)
