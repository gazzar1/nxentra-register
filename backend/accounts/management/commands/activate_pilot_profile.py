"""A4: authorized, transactional activation of the constrained-pilot profile.

    python manage.py activate_pilot_profile --company <id> --yes

Locks the company, runs the exhaustive preflight (activation phase), and refuses
to activate when any forbidden state exists. It NEVER deletes mappings, converts
items, erases stock state or otherwise repairs the company — it reports what the
founder must correct. Setting ``pilot_profile`` is intentionally NOT an ordinary
settings edit; this command is the only sanctioned path.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Activate ISOLATED_SHADOW_LEDGER_V1 on a company (validated, transactional, fail-closed)."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, required=True)
        parser.add_argument("--yes", action="store_true", help="Confirm activation after a clean validation.")

    def handle(self, *args, **opts):
        from accounts.models import Company
        from accounts.pilot_preflight import run_preflight
        from accounts.rls import rls_bypass

        with rls_bypass(), transaction.atomic():
            try:
                company = Company.objects.select_for_update().get(id=opts["company"])
            except Company.DoesNotExist as exc:
                raise CommandError(f"Company {opts['company']} not found.") from exc

            if company.pilot_profile == Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1:
                self.stdout.write(
                    self.style.WARNING(f"Company {company.id} already on ISOLATED_SHADOW_LEDGER_V1; nothing to do.")
                )
                return

            # Validate before activating. The preflight's `not_isolated` check
            # also enforces "no second active merchant company in the deployment".
            violations = run_preflight(company, phase="setup", for_activation=True)
            if violations:
                self.stdout.write(
                    self.style.ERROR(f"Refusing to activate: {len(violations)} forbidden-state violation(s):")
                )
                for vi in violations:
                    self.stdout.write(f"  - [{vi.code}] {vi.message}")
                raise CommandError("Activation refused. Correct the violations above (no data is modified) and retry.")

            if not opts["yes"]:
                raise CommandError("Validation passed. Re-run with --yes to activate.")

            company.pilot_profile = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1
            company.save(update_fields=["pilot_profile"])
            self.stdout.write(self.style.SUCCESS(f"Activated ISOLATED_SHADOW_LEDGER_V1 on company {company.id}."))
