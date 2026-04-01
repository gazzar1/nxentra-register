# accounting/management/commands/resequence_entry_numbers.py
"""
Re-sequence ALL posted journal entry numbers in chronological order (date, id).

This fixes numbering gaps or out-of-order numbers caused by projection rebuilds
or backfills. After running, entry numbers will be sequential and match the
chronological order of entries.

Usage:
    python manage.py resequence_entry_numbers --company "Sony-Egypt"
    python manage.py resequence_entry_numbers --all
    python manage.py resequence_entry_numbers --company "Sony-Egypt" --dry-run
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from accounting.models import CompanySequence, JournalEntry
from accounts.models import Company
from accounts.rls import rls_bypass
from projections.write_barrier import command_writes_allowed, projection_writes_allowed


class Command(BaseCommand):
    help = "Re-sequence all posted journal entry numbers in chronological order."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=str, help="Company name to process")
        parser.add_argument("--all", action="store_true", help="Process all companies")
        parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")

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
                self._resequence(company, dry_run=options["dry_run"])

    def _resequence(self, company, dry_run=False):
        # Strip numbers from drafts/incomplete first
        drafts = JournalEntry.objects.filter(
            company=company,
            status__in=[JournalEntry.Status.DRAFT, JournalEntry.Status.INCOMPLETE],
        ).exclude(entry_number="")
        if drafts.exists() and not dry_run:
            with projection_writes_allowed():
                for entry in drafts:
                    entry.entry_number = ""
                    entry.save(update_fields=["entry_number"])
            self.stdout.write(f"  Stripped numbers from {drafts.count()} draft/incomplete entries.")

        # Get ALL posted/reversed entries in chronological order
        entries = list(
            JournalEntry.objects.filter(
                company=company,
                status__in=[JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED],
            ).order_by("date", "id")
        )

        if not entries:
            self.stdout.write(f"  {company.name}: no posted entries to resequence.")
            return

        self.stdout.write(f"  {company.name}: resequencing {len(entries)} posted entries...")

        if dry_run:
            for i, entry in enumerate(entries, 1):
                new_number = f"JE-{company.id}-{i:06d}"
                changed = " (changed)" if entry.entry_number != new_number else ""
                self.stdout.write(
                    f"    {entry.entry_number or '#' + str(entry.id):>16} → {new_number}  "
                    f"{entry.date}  {entry.memo[:50]}{changed}"
                )
            self.stdout.write(self.style.SUCCESS(
                f"  Would resequence {len(entries)} entries (dry run)."
            ))
            return

        with transaction.atomic():
            # Assign new sequential numbers
            for i, entry in enumerate(entries, 1):
                new_number = f"JE-{company.id}-{i:06d}"
                if entry.entry_number != new_number:
                    with projection_writes_allowed():
                        entry.entry_number = new_number
                        entry.save(update_fields=["entry_number"])

            # Reset the sequence counter to next available value
            next_value = len(entries) + 1
            with command_writes_allowed():
                seq, _ = CompanySequence.objects.get_or_create(
                    company=company,
                    name="journal_entry_number",
                    defaults={"next_value": next_value},
                )
                seq.next_value = next_value
                seq.save(update_fields=["next_value"])

        self.stdout.write(self.style.SUCCESS(
            f"  Resequenced {len(entries)} entries. Next number: JE-{company.id}-{next_value:06d}"
        ))
