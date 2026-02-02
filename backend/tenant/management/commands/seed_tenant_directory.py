"""
Seed TenantDirectory rows for all existing companies.

This command ensures every Company has a corresponding TenantDirectory entry.
Run this after migrating existing systems to the tenant architecture.

Usage:
    python manage.py seed_tenant_directory
    python manage.py seed_tenant_directory --dry-run

All new entries are created with:
    - mode: SHARED (default behavior, uses RLS)
    - db_alias: "default"
    - status: ACTIVE

This is idempotent - companies that already have entries are skipped.
"""
from django.core.management.base import BaseCommand

from accounts.models import Company
from accounts.rls import rls_bypass
from tenant.models import TenantDirectory


class Command(BaseCommand):
    help = "Create TenantDirectory entries for all companies that don't have one"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made\n"))

        with rls_bypass():
            # Get all companies
            companies = Company.objects.all()
            total = companies.count()

            # Get companies that already have entries
            existing_company_ids = set(
                TenantDirectory.objects.values_list("company_id", flat=True)
            )

            created = 0
            skipped = 0

            for company in companies:
                if company.id in existing_company_ids:
                    self.stdout.write(
                        f"  SKIP: {company.name} (ID: {company.id}) - already has entry"
                    )
                    skipped += 1
                    continue

                if dry_run:
                    self.stdout.write(
                        f"  WOULD CREATE: {company.name} (ID: {company.id}) -> SHARED/default"
                    )
                    created += 1
                else:
                    TenantDirectory.objects.create(
                        company=company,
                        mode=TenantDirectory.IsolationMode.SHARED,
                        db_alias="default",
                        status=TenantDirectory.Status.ACTIVE,
                    )
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  CREATED: {company.name} (ID: {company.id}) -> SHARED/default"
                        )
                    )
                    created += 1

        # Summary
        self.stdout.write("")
        self.stdout.write(f"Total companies: {total}")
        self.stdout.write(f"Already had entries: {skipped}")
        if dry_run:
            self.stdout.write(f"Would create: {created}")
        else:
            self.stdout.write(self.style.SUCCESS(f"Created: {created}"))
