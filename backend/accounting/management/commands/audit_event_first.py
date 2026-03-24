"""
Management command: Audit finance event-first compliance.

Checks that the event store and accounting projections are consistent:
1. Every posted JE has a corresponding JOURNAL_ENTRY_POSTED event
2. Every JOURNAL_ENTRY_POSTED event has valid payload structure
3. Causation chain has no dangling references
4. Projection bookmark is not lagging behind event stream
5. Trial balance is balanced

Usage:
    # Interactive (human-readable)
    python manage.py audit_event_first

    # JSON output (for monitoring/CI)
    python manage.py audit_event_first --json

    # Fail on violations (for CI gates)
    python manage.py audit_event_first --strict

    # Single company
    python manage.py audit_event_first --company my-company
"""
import json
import logging
import sys
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Q

logger = logging.getLogger("nxentra.accounting.audit")


class Command(BaseCommand):
    help = "Audit finance event-first compliance across all companies."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output results as JSON (for monitoring pipelines).",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with code 1 if any company has violations.",
        )
        parser.add_argument(
            "--company",
            type=str,
            help="Audit a specific company by slug (default: all).",
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
        any_violation = False

        for company in companies:
            with rls_bypass():
                result = self._audit_company(company)

            has_violations = any(
                c["severity"] == "CRITICAL" and c["count"] > 0
                for c in result["checks"]
            )
            if has_violations:
                any_violation = True

            results.append(result)

        # Output
        if output_json:
            self.stdout.write(json.dumps({"results": results}, indent=2, default=str))
        else:
            self._print_report(results)

        if strict and any_violation:
            self.stderr.write(
                self.style.ERROR("STRICT MODE: Event-first violations detected.")
            )
            sys.exit(1)

    def _audit_company(self, company):
        """Run all audit checks for a single company."""
        checks = []
        checks.append(self._check_orphan_journal_entries(company))
        checks.append(self._check_dangling_causation(company))
        checks.append(self._check_projection_lag(company))
        checks.append(self._check_trial_balance(company))
        checks.append(self._check_event_payload_integrity(company))

        status = "PASS"
        for c in checks:
            if c["severity"] == "CRITICAL" and c["count"] > 0:
                status = "FAIL"
                break
            if c["severity"] == "WARNING" and c["count"] > 0:
                status = "WARN"

        return {
            "company": company.slug,
            "status": status,
            "checks": checks,
        }

    def _check_orphan_journal_entries(self, company):
        """Check 1: Posted JEs without a corresponding JOURNAL_ENTRY_POSTED event."""
        from accounting.models import JournalEntry
        from events.models import BusinessEvent
        from events.types import EventTypes

        posted_entries = JournalEntry.objects.filter(
            company=company,
            status=JournalEntry.Status.POSTED,
        ).values_list("public_id", flat=True)

        posted_event_ids = set(
            BusinessEvent.objects.filter(
                company=company,
                event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            ).values_list("aggregate_id", flat=True)
        )

        orphans = []
        for entry_public_id in posted_entries:
            if str(entry_public_id) not in posted_event_ids:
                orphans.append(str(entry_public_id))

        return {
            "name": "orphan_journal_entries",
            "description": "Posted JEs without JOURNAL_ENTRY_POSTED event",
            "severity": "CRITICAL",
            "count": len(orphans),
            "details": orphans[:20],  # Cap at 20 for readability
        }

    def _check_dangling_causation(self, company):
        """Check 2: Events with caused_by_event pointing to non-existent events."""
        from events.models import BusinessEvent

        # Events with caused_by_event that reference a different company
        # or where the parent event doesn't exist
        dangling = BusinessEvent.objects.filter(
            company=company,
            caused_by_event__isnull=False,
        ).exclude(
            caused_by_event__company=company,
        ).values_list("id", "caused_by_event_id")

        dangling_list = list(dangling[:20])

        return {
            "name": "dangling_causation_chains",
            "description": "Events with cross-company or invalid caused_by_event",
            "severity": "CRITICAL",
            "count": len(dangling_list),
            "details": [
                {"event_id": eid, "caused_by_event_id": pid}
                for eid, pid in dangling_list
            ],
        }

    def _check_projection_lag(self, company):
        """Check 3: Unprocessed events (projection bookmark behind event stream)."""
        from events.models import BusinessEvent, EventBookmark

        # Get the latest event sequence for this company
        latest_event = (
            BusinessEvent.objects.filter(company=company)
            .order_by("-company_sequence")
            .values_list("company_sequence", flat=True)
            .first()
        )

        if latest_event is None:
            return {
                "name": "projection_lag",
                "description": "Unprocessed events (bookmark behind event stream)",
                "severity": "WARNING",
                "count": 0,
                "details": "No events in company",
            }

        # Check bookmarks for core projections
        lagging = []
        bookmarks = EventBookmark.objects.filter(company=company)
        for bookmark in bookmarks:
            last_processed = bookmark.last_event
            if last_processed is None:
                unprocessed = latest_event
            else:
                last_seq = last_processed.company_sequence if hasattr(last_processed, "company_sequence") else 0
                unprocessed = latest_event - last_seq

            if unprocessed > 0:
                lagging.append({
                    "consumer": bookmark.consumer_name,
                    "unprocessed_events": unprocessed,
                })

        return {
            "name": "projection_lag",
            "description": "Unprocessed events (bookmark behind event stream)",
            "severity": "WARNING",
            "count": len(lagging),
            "details": lagging,
        }

    def _check_trial_balance(self, company):
        """Check 4: Trial balance must be balanced."""
        from projections.models import AccountBalance

        balances = AccountBalance.objects.filter(company=company)
        total_debit = sum(b.debit_total for b in balances)
        total_credit = sum(b.credit_total for b in balances)

        imbalance = total_debit - total_credit

        return {
            "name": "trial_balance",
            "description": "Trial balance debit/credit equality",
            "severity": "CRITICAL",
            "count": 0 if imbalance == Decimal("0") else 1,
            "details": {
                "total_debit": str(total_debit),
                "total_credit": str(total_credit),
                "imbalance": str(imbalance),
            },
        }

    def _check_event_payload_integrity(self, company):
        """Check 5: JOURNAL_ENTRY_POSTED events must have valid payload structure."""
        from events.models import BusinessEvent
        from events.types import EventTypes

        # Sample up to 100 recent events
        events = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        ).order_by("-company_sequence")[:100]

        invalid = []
        for event in events:
            try:
                data = event.get_data()
                if not isinstance(data, dict):
                    invalid.append({"event_id": event.id, "reason": "payload is not a dict"})
                    continue
                required = ["entry_public_id", "lines", "total_debit", "total_credit"]
                missing = [f for f in required if f not in data]
                if missing:
                    invalid.append({
                        "event_id": event.id,
                        "reason": f"missing fields: {missing}",
                    })
            except Exception as e:
                invalid.append({
                    "event_id": event.id,
                    "reason": f"payload read error: {str(e)[:100]}",
                })

        return {
            "name": "event_payload_integrity",
            "description": "JOURNAL_ENTRY_POSTED events with valid payload",
            "severity": "CRITICAL",
            "count": len(invalid),
            "details": invalid[:20],
        }

    def _print_report(self, results):
        """Print human-readable audit report."""
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("=== Finance Event-First Audit ==="))
        self.stdout.write("")

        for r in results:
            company = r["company"]
            status = r["status"]

            if status == "PASS":
                header = self.style.SUCCESS(f"  {company}: PASS")
            elif status == "WARN":
                header = self.style.WARNING(f"  {company}: WARN")
            else:
                header = self.style.ERROR(f"  {company}: FAIL")

            self.stdout.write(header)

            for check in r["checks"]:
                if check["count"] == 0:
                    marker = self.style.SUCCESS("  OK")
                elif check["severity"] == "WARNING":
                    marker = self.style.WARNING(f"  WARN({check['count']})")
                else:
                    marker = self.style.ERROR(f"  FAIL({check['count']})")

                self.stdout.write(f"    {marker}  {check['description']}")

                if check["count"] > 0 and check.get("details"):
                    details = check["details"]
                    if isinstance(details, list):
                        for d in details[:5]:
                            self.stdout.write(f"          {d}")
                    elif isinstance(details, dict):
                        for k, v in details.items():
                            self.stdout.write(f"          {k}: {v}")

            self.stdout.write("")

        total = len(results)
        passed = sum(1 for r in results if r["status"] == "PASS")
        self.stdout.write(
            f"  Total: {total} companies, {passed} passed, "
            f"{total - passed} with issues"
        )
        self.stdout.write("")
