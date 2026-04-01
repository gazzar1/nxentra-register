"""
Management command: Restore a company from a backup ZIP file.

Usage:
    python manage.py company_restore --company acme-corp --file /path/to/backup.zip
"""
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Restore a company from a backup ZIP file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--company",
            type=str,
            required=True,
            help="Target company slug.",
        )
        parser.add_argument(
            "--file",
            type=str,
            required=True,
            help="Path to the backup ZIP file.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip confirmation prompt.",
        )

    def handle(self, *args, **options):
        from accounts.models import Company
        from accounts.rls import rls_bypass
        from backups.importer import RestoreError, restore_company

        slug = options["company"]
        file_path = options["file"]

        if not Path(file_path).exists():
            raise CommandError(f"File not found: {file_path}")

        with rls_bypass():
            try:
                company = Company.objects.get(slug=slug, is_active=True)
            except Company.DoesNotExist:
                raise CommandError(f"Company '{slug}' not found.")

        if not options["yes"]:
            self.stdout.write(
                self.style.WARNING(
                    f"\nThis will REPLACE ALL DATA for '{company.name}' ({slug}).\n"
                    "This action cannot be undone.\n"
                )
            )
            confirm = input("Type 'RESTORE' to continue: ")
            if confirm != "RESTORE":
                self.stdout.write("Aborted.")
                sys.exit(0)

        self.stdout.write(f"Restoring {company.name} from {file_path}...")

        zip_bytes = Path(file_path).read_bytes()

        try:
            result = restore_company(company, zip_bytes)
        except RestoreError as e:
            raise CommandError(str(e))

        self.stdout.write(self.style.SUCCESS("\nRestore completed!"))
        self.stdout.write(f"  Company:  {result['company']}")
        self.stdout.write(f"  Cleared:  {result['cleared']} existing records")
        self.stdout.write(f"  Time:     {result['duration_seconds']}s")

        self.stdout.write("\n  Imported:")
        for label, count in result.get("imported", {}).items():
            if count > 0:
                self.stdout.write(f"    {label}: {count}")

        if result.get("errors"):
            self.stdout.write(self.style.WARNING("\n  Errors:"))
            for err in result["errors"]:
                self.stdout.write(f"    {err}")
