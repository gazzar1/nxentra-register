"""
Management command: Export a company backup to a ZIP file.

Usage:
    python manage.py company_backup --company acme-corp --out /path/to/backup.zip
    python manage.py company_backup --company acme-corp  # auto-named
"""
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Export a company backup to a ZIP file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--company",
            type=str,
            required=True,
            help="Company slug.",
        )
        parser.add_argument(
            "--out",
            type=str,
            help="Output file path (default: auto-named in current directory).",
        )

    def handle(self, *args, **options):
        from accounts.models import Company
        from accounts.rls import rls_bypass
        from backups.exporter import export_company

        slug = options["company"]

        with rls_bypass():
            try:
                company = Company.objects.get(slug=slug, is_active=True)
            except Company.DoesNotExist:
                raise CommandError(f"Company '{slug}' not found.")

        self.stdout.write(f"Starting backup for: {company.name} ({slug})")

        zip_bytes, metadata = export_company(company)

        # Determine output path
        out_path = options.get("out")
        if not out_path:
            ts = timezone.now().strftime("%Y%m%d_%H%M%S")
            out_path = f"backup_{slug}_{ts}.zip"

        Path(out_path).write_bytes(zip_bytes)

        self.stdout.write(self.style.SUCCESS(f"\nBackup saved to: {out_path}"))
        self.stdout.write(f"  Events:  {metadata['event_count']}")
        self.stdout.write(f"  Records: {metadata['total_records']}")
        self.stdout.write(f"  Size:    {metadata['file_size_bytes']:,} bytes")
        self.stdout.write(f"  Hash:    {metadata['file_checksum'][:16]}...")
        self.stdout.write(f"  Time:    {metadata['duration_seconds']}s")
