"""A4: read-only constrained-pilot preflight command.

    python manage.py pilot_preflight --company <id> [--phase setup|go-live] [--json]

Exits non-zero and reports every violation when the company is not pilot-safe.
Never mutates or repairs data.
"""

import json
import sys

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Read-only constrained-pilot preflight for a company (exits non-zero on any violation)."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, required=True)
        parser.add_argument("--phase", choices=["setup", "go-live"], default="go-live")
        parser.add_argument("--json", action="store_true", help="Machine-readable output")

    def handle(self, *args, **opts):
        from accounts.models import Company
        from accounts.pilot_preflight import run_preflight
        from accounts.rls import rls_bypass

        with rls_bypass():
            try:
                company = Company.objects.get(id=opts["company"])
            except Company.DoesNotExist as exc:
                raise CommandError(f"Company {opts['company']} not found.") from exc

        violations = run_preflight(company, phase=opts["phase"])

        if opts["json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "company_id": company.id,
                        "phase": opts["phase"],
                        "profile": company.pilot_profile,
                        "ok": not violations,
                        "violations": [v.as_dict() for v in violations],
                    },
                    indent=2,
                )
            )
        elif not violations:
            self.stdout.write(self.style.SUCCESS(f"PASS: company {company.id} is pilot-safe ({opts['phase']})."))
        else:
            self.stdout.write(
                self.style.ERROR(f"FAIL: {len(violations)} violation(s) for company {company.id} ({opts['phase']}):")
            )
            for vi in violations:
                self.stdout.write(f"  - [{vi.code}] {vi.message}")

        if violations:
            sys.exit(1)
