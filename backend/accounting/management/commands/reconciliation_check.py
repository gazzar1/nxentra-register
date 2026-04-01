"""
Management command: Run AR/AP reconciliation check.

Usage:
    # Interactive (human-readable)
    python manage.py reconciliation_check

    # JSON output (for monitoring/CI)
    python manage.py reconciliation_check --json

    # Fail on imbalance (for CI gates)
    python manage.py reconciliation_check --strict

    # Cron / scheduled (combined)
    python manage.py reconciliation_check --json --strict
"""
import json
import logging
import sys

from django.core.management.base import BaseCommand

logger = logging.getLogger("nxentra.accounting.commands")


class Command(BaseCommand):
    help = "Run AR/AP reconciliation check across all companies."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output results as JSON (for monitoring pipelines).",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with code 1 if any company has an imbalance.",
        )
        parser.add_argument(
            "--company",
            type=str,
            help="Check a specific company by slug (default: all).",
        )

    def handle(self, *args, **options):
        from accounting.commands import run_reconciliation_check
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
        any_imbalance = False

        for company in companies:
            # Build a minimal actor for the reconciliation command
            actor = self._build_system_actor(company)
            if actor is None:
                results.append({
                    "company": company.slug,
                    "status": "skipped",
                    "reason": "No active owner membership found.",
                })
                continue

            result = run_reconciliation_check(actor)

            if not result.success:
                entry = {
                    "company": company.slug,
                    "status": "error",
                    "error": result.error,
                }
                results.append(entry)
                any_imbalance = True
                logger.error(
                    "reconciliation.check_failed",
                    extra={"company": company.slug, "error": result.error},
                )
                continue

            data = result.data
            balanced = data.get("balanced", False)
            if not balanced:
                any_imbalance = True

            entry = {
                "company": company.slug,
                "status": "balanced" if balanced else "IMBALANCE",
                "ar": data.get("ar_reconciliation", {}),
                "ap": data.get("ap_reconciliation", {}),
                "errors": data.get("errors", []),
                "checked_at": data.get("checked_at"),
            }
            results.append(entry)

            # Log the result
            log_extra = {
                "company": company.slug,
                "balanced": balanced,
                "ar_difference": entry["ar"].get("difference", "0"),
                "ap_difference": entry["ap"].get("difference", "0"),
            }
            if balanced:
                logger.info("reconciliation.check_passed", extra=log_extra)
            else:
                logger.warning("reconciliation.check_imbalance", extra=log_extra)

        # Output
        if output_json:
            self.stdout.write(json.dumps({"results": results}, indent=2))
        else:
            self._print_table(results)

        if strict and any_imbalance:
            self.stderr.write(
                self.style.ERROR("STRICT MODE: Reconciliation imbalance detected.")
            )
            sys.exit(1)

    def _build_system_actor(self, company):
        """Build a system-level ActorContext for a company."""
        from accounts.authz import ActorContext
        from accounts.models import CompanyMembership
        from accounts.rls import rls_bypass

        with rls_bypass():
            membership = (
                CompanyMembership.objects
                .filter(company=company, is_active=True, role=CompanyMembership.Role.OWNER)
                .select_related("user")
                .first()
            )
            if not membership:
                return None

            perms = frozenset(
                membership.permissions.values_list("code", flat=True)
            )
            # Add reports.view which is needed for reconciliation
            perms = perms | frozenset(["reports.view"])

            return ActorContext(
                user=membership.user,
                company=company,
                membership=membership,
                perms=perms,
            )

    def _print_table(self, results):
        """Print human-readable reconciliation table."""
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("=== Reconciliation Check ==="))
        self.stdout.write("")

        for r in results:
            company = r["company"]
            status = r["status"]

            if status == "balanced":
                label = self.style.SUCCESS(f"  {company}: BALANCED")
            elif status == "skipped":
                label = self.style.WARNING(f"  {company}: SKIPPED ({r.get('reason', '')})")
            elif status == "error":
                label = self.style.ERROR(f"  {company}: ERROR ({r.get('error', '')})")
            else:
                label = self.style.ERROR(f"  {company}: IMBALANCE")

            self.stdout.write(label)

            if status == "IMBALANCE":
                ar = r.get("ar", {})
                ap = r.get("ap", {})
                if ar.get("difference") and ar["difference"] != "0":
                    self.stdout.write(
                        f"    AR: GL={ar.get('gl_control_balance')} "
                        f"Sub={ar.get('subledger_total')} "
                        f"Diff={ar.get('difference')}"
                    )
                if ap.get("difference") and ap["difference"] != "0":
                    self.stdout.write(
                        f"    AP: GL={ap.get('gl_control_balance')} "
                        f"Sub={ap.get('subledger_total')} "
                        f"Diff={ap.get('difference')}"
                    )

        self.stdout.write("")
        balanced_count = sum(1 for r in results if r["status"] == "balanced")
        self.stdout.write(
            f"  Total: {len(results)} companies, "
            f"{balanced_count} balanced, "
            f"{len(results) - balanced_count} issues"
        )
        self.stdout.write("")
