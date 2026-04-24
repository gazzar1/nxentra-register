# accounting/management/commands/backfill_entry_numbers.py
"""
Backfill entry_number for posted journal entries that have empty entry numbers,
and strip entry numbers from draft/incomplete entries that shouldn't have them.

Usage:
    python manage.py backfill_entry_numbers --company "Sony-Egypt"
    python manage.py backfill_entry_numbers --all
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from accounting.commands import _next_company_sequence
from accounting.models import JournalEntry
from accounts.models import Company
from accounts.rls import rls_bypass
from projections.write_barrier import projection_writes_allowed


class Command(BaseCommand):
    help = "Backfill entry_number for posted entries and strip numbers from drafts/incomplete."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=str, help="Company name to process")
        parser.add_argument("--all", action="store_true", help="Process all companies")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without making changes")

    def handle(self, *args, **options):
        with rls_bypass():
            if options["all"]:
                companies = Company.objects.filter(is_active=True)
            elif options["company"]:
                companies = Company.objects.filter(name__icontains=options["company"])
            else:
                self.stderr.write(self.style.ERROR("Specify --company <name> or --all"))
                return

            for company in companies:
                self._strip_draft_numbers(company, dry_run=options["dry_run"])
                self._backfill_company(company, dry_run=options["dry_run"])

    def _strip_draft_numbers(self, company, dry_run=False):
        """Remove entry numbers from draft/incomplete entries."""
        entries = JournalEntry.objects.filter(
            company=company,
            status__in=[JournalEntry.Status.DRAFT, JournalEntry.Status.INCOMPLETE],
        ).exclude(entry_number="")

        count = entries.count()
        if count == 0:
            return

        self.stdout.write(f"  {company.name}: stripping numbers from {count} draft/incomplete entries...")

        for entry in entries:
            if dry_run:
                self.stdout.write(f"    Would strip {entry.entry_number} from draft JE #{entry.id}")
            else:
                with projection_writes_allowed():
                    entry.entry_number = ""
                    entry.save(update_fields=["entry_number"])
                self.stdout.write(f"    Stripped {entry.entry_number} from draft JE #{entry.id}")

        action = "Would strip" if dry_run else "Stripped"
        self.stdout.write(self.style.SUCCESS(f"  {action} numbers from {count} draft/incomplete entries."))

    def _backfill_company(self, company, dry_run=False):
        """Assign entry numbers to posted entries that don't have one."""
        entries = JournalEntry.objects.filter(
            company=company,
            entry_number="",
            status=JournalEntry.Status.POSTED,
        ).order_by("date", "id")

        count = entries.count()
        if count == 0:
            self.stdout.write(f"  {company.name}: no posted entries to backfill.")
            return

        self.stdout.write(f"  {company.name}: {count} posted entries to backfill...")

        for entry in entries:
            if dry_run:
                self.stdout.write(f"    Would assign number to JE #{entry.id} ({entry.date}): {entry.memo[:60]}")
            else:
                with transaction.atomic():
                    sequence_value = _next_company_sequence(company, "journal_entry_number")
                    entry_number = f"JE-{company.id}-{sequence_value:06d}"
                    with projection_writes_allowed():
                        entry.entry_number = entry_number
                        entry.save(update_fields=["entry_number"])
                    self.stdout.write(f"    {entry_number} → JE #{entry.id} ({entry.date}): {entry.memo[:60]}")

        action = "Would backfill" if dry_run else "Backfilled"
        self.stdout.write(self.style.SUCCESS(f"  {action} {count} entries for {company.name}."))
