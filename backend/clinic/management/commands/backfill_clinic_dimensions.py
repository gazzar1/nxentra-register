# clinic/management/commands/backfill_clinic_dimensions.py
"""
Backfill JournalLineAnalysis records for existing clinic-generated journal entries.

Finds all JOURNAL_ENTRY_POSTED events emitted by the clinic_accounting projection,
traces back to the source clinic event, resolves dimensions (patient/doctor),
and creates JournalLineAnalysis records for any journal lines missing them.

Usage:
    python manage.py backfill_clinic_dimensions --company "Clinic-XYZ"
    python manage.py backfill_clinic_dimensions --all
    python manage.py backfill_clinic_dimensions --company "Clinic-XYZ" --dry-run
"""

import logging

from django.core.management.base import BaseCommand

from accounting.models import (
    AnalysisDimension,
    AnalysisDimensionValue,
    JournalEntry,
    JournalLine,
    JournalLineAnalysis,
)
from accounts.models import Company
from accounts.rls import rls_bypass
from clinic.models import Invoice, Patient, Visit
from events.models import BusinessEvent
from events.types import EventTypes
from projections.write_barrier import projection_writes_allowed

logger = logging.getLogger(__name__)

# Clinic event types that carry patient_public_id and visit_public_id
INVOICE_EVENT_TYPES = [
    EventTypes.CLINIC_INVOICE_ISSUED,
]

# Clinic event types that carry patient_public_id and invoice_public_id
PAYMENT_EVENT_TYPES = [
    EventTypes.CLINIC_PAYMENT_RECEIVED,
    EventTypes.CLINIC_PAYMENT_VOIDED,
]


class Command(BaseCommand):
    help = "Backfill dimension tags on clinic-generated journal entries."

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
                self._backfill_company(company, dry_run=options["dry_run"])

    def _backfill_company(self, company, dry_run=False):
        self.stdout.write(f"\n{company.name}:")

        # Load dimension lookups
        dimensions = {
            d.code: d
            for d in AnalysisDimension.objects.filter(
                company=company,
                is_active=True,
                code__in=["doctor", "patient"],
            )
        }
        if not dimensions:
            self.stdout.write("  No doctor/patient dimensions found. Run sync_clinic_dimensions first.")
            return

        # Load dimension value lookups: {(dim_id, value_code): value}
        dim_ids = [d.id for d in dimensions.values()]
        values = {
            (v.dimension_id, v.code): v
            for v in AnalysisDimensionValue.objects.filter(
                company=company,
                dimension_id__in=dim_ids,
                is_active=True,
            )
        }

        # Find all JOURNAL_ENTRY_POSTED events from clinic projection
        je_posted_events = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            metadata__source_projection="clinic_accounting",
        ).select_related("caused_by_event")

        total = 0
        created = 0
        skipped = 0

        for je_event in je_posted_events:
            source_event = je_event.caused_by_event
            if not source_event:
                skipped += 1
                continue

            total += 1

            # Resolve dimensions from the source event
            source_data = source_event.get_data()
            source_type = source_event.event_type
            dimension_context = {}

            patient_public_id = source_data.get("patient_public_id")
            if patient_public_id:
                try:
                    patient = Patient.objects.get(
                        company=company,
                        public_id=patient_public_id,
                    )
                    dimension_context["patient"] = patient.code
                except Patient.DoesNotExist:
                    pass

            # Resolve doctor from visit or invoice
            if source_type in INVOICE_EVENT_TYPES:
                visit_public_id = source_data.get("visit_public_id")
                if visit_public_id:
                    try:
                        visit = Visit.objects.select_related("doctor").get(
                            company=company,
                            public_id=visit_public_id,
                        )
                        dimension_context["doctor"] = visit.doctor.code
                    except Visit.DoesNotExist:
                        pass
            elif source_type in PAYMENT_EVENT_TYPES:
                invoice_public_id = source_data.get("invoice_public_id")
                if invoice_public_id:
                    try:
                        invoice = Invoice.objects.select_related("visit__doctor").get(
                            company=company,
                            public_id=invoice_public_id,
                        )
                        if invoice.visit:
                            dimension_context["doctor"] = invoice.visit.doctor.code
                    except Invoice.DoesNotExist:
                        pass

            if not dimension_context:
                skipped += 1
                continue

            # Find the journal entry
            je_data = je_event.get_data()
            entry_public_id = je_data.get("entry_public_id")
            if not entry_public_id:
                skipped += 1
                continue

            try:
                entry = JournalEntry.objects.get(
                    company=company,
                    public_id=entry_public_id,
                )
            except JournalEntry.DoesNotExist:
                skipped += 1
                continue

            # Get lines that don't already have analysis records
            lines = JournalLine.objects.filter(entry=entry, company=company)

            # Build analysis records
            records = []
            for line in lines:
                existing_dims = set(
                    JournalLineAnalysis.objects.filter(
                        journal_line=line,
                        company=company,
                    ).values_list("dimension__code", flat=True)
                )

                for dim_code, val_code in dimension_context.items():
                    if dim_code in existing_dims:
                        continue
                    dim = dimensions.get(dim_code)
                    if not dim:
                        continue
                    val = values.get((dim.id, val_code))
                    if not val:
                        continue
                    records.append(
                        JournalLineAnalysis(
                            journal_line=line,
                            company=company,
                            dimension=dim,
                            dimension_value=val,
                        )
                    )

            if records:
                if dry_run:
                    self.stdout.write(
                        f"    Would create {len(records)} analysis records for {entry.entry_number} ({entry.memo[:40]})"
                    )
                else:
                    with projection_writes_allowed():
                        JournalLineAnalysis.objects.projection().bulk_create(
                            records,
                            ignore_conflicts=True,
                        )
                created += len(records)

        action = "Would create" if dry_run else "Created"
        self.stdout.write(
            self.style.SUCCESS(f"  {action} {created} analysis records across {total} entries (skipped {skipped})")
        )
