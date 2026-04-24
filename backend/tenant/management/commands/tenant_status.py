"""
Show or change the status of a tenant.

Usage:
    python manage.py tenant_status acme-corp
    python manage.py tenant_status acme-corp --set-status READ_ONLY
    python manage.py tenant_status acme-corp --set-status ACTIVE
    python manage.py tenant_status acme-corp --revert-to-shared
"""

from django.core.management.base import BaseCommand, CommandError

from accounts.rls import rls_bypass
from tenant.models import TenantDirectory


class Command(BaseCommand):
    help = "Show or change tenant status (mode, db_alias, status)"

    def add_arguments(self, parser):
        parser.add_argument("slug", help="Company slug")
        parser.add_argument(
            "--set-status",
            choices=["ACTIVE", "READ_ONLY", "MIGRATING"],
            help="Set tenant status",
        )
        parser.add_argument(
            "--revert-to-shared",
            action="store_true",
            help="Revert tenant to SHARED mode (rollback migration)",
        )

    def handle(self, *args, **options):
        slug = options["slug"]

        with rls_bypass():
            try:
                td = TenantDirectory.objects.select_related("company").get(company__slug=slug)
            except TenantDirectory.DoesNotExist:
                raise CommandError(f"No TenantDirectory for slug '{slug}'")

            if options["revert_to_shared"]:
                td.mode = TenantDirectory.IsolationMode.SHARED
                td.db_alias = "default"
                td.status = TenantDirectory.Status.ACTIVE
                td.migrated_at = None
                td.save()
                self.stdout.write(self.style.SUCCESS(f"Reverted '{slug}' to SHARED mode on default DB"))
                return

            if options["set_status"]:
                new_status = getattr(TenantDirectory.Status, options["set_status"])
                old_status = td.status
                td.status = new_status
                td.save()
                self.stdout.write(self.style.SUCCESS(f"'{slug}' status: {old_status} → {new_status}"))
                return

            # Default: show current state
            self.stdout.write(
                f"Tenant:   {td.company.name} ({slug})\n"
                f"Mode:     {td.mode}\n"
                f"DB Alias: {td.db_alias}\n"
                f"Status:   {td.status}\n"
                f"Shared:   {td.is_shared}\n"
                f"Migrated: {td.migrated_at or 'N/A'}"
            )
