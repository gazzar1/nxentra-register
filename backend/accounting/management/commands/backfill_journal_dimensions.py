# accounting/management/commands/backfill_journal_dimensions.py
"""
Backfill JournalLineAnalysis records for existing property-generated journal entries.

Finds all JOURNAL_ENTRY_POSTED events emitted by the property_accounting projection,
traces back to the source property event, resolves dimensions (property/unit/lessee),
and creates JournalLineAnalysis records for any journal lines missing them.

Usage:
    python manage.py backfill_journal_dimensions --company "Sony-Egypt"
    python manage.py backfill_journal_dimensions --all
    python manage.py backfill_journal_dimensions --company "Sony-Egypt" --dry-run
"""

import logging
from django.core.management.base import BaseCommand

from accounts.models import Company
from accounts.rls import rls_bypass
from accounting.models import (
    AnalysisDimension, AnalysisDimensionValue,
    JournalEntry, JournalLine, JournalLineAnalysis,
)
from events.models import BusinessEvent
from events.types import EventTypes
from projections.write_barrier import projection_writes_allowed
from properties.models import Lease, Property, Unit

logger = logging.getLogger(__name__)

# Property projection event types that carry lease_public_id
LEASE_EVENT_TYPES = [
    EventTypes.RENT_DUE_POSTED,
    EventTypes.RENT_PAYMENT_RECEIVED,
    EventTypes.RENT_PAYMENT_ALLOCATED,
    EventTypes.RENT_PAYMENT_VOIDED,
    EventTypes.DEPOSIT_RECEIVED,
    EventTypes.DEPOSIT_ADJUSTED,
    EventTypes.DEPOSIT_REFUNDED,
    EventTypes.DEPOSIT_FORFEITED,
]

# Property projection event types that carry property_public_id
PROPERTY_EVENT_TYPES = [
    EventTypes.PROPERTY_EXPENSE_RECORDED,
]


class Command(BaseCommand):
    help = "Backfill dimension tags on property-generated journal entries."

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
            d.code: d for d in AnalysisDimension.objects.filter(
                company=company, is_active=True,
                code__in=["property", "unit", "lessee"],
            )
        }
        if not dimensions:
            self.stdout.write("  No property/unit/lessee dimensions found. Run sync_property_dimensions first.")
            return

        # Load dimension value lookups: {(dim_id, value_code): value}
        dim_ids = [d.id for d in dimensions.values()]
        values = {
            (v.dimension_id, v.code): v
            for v in AnalysisDimensionValue.objects.filter(
                company=company, dimension_id__in=dim_ids, is_active=True,
            )
        }

        # Find all JOURNAL_ENTRY_POSTED events from property projection
        je_posted_events = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            metadata__source_projection="property_accounting",
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

            if source_type in LEASE_EVENT_TYPES:
                dimension_context = self._resolve_lease_dims(
                    company, source_data.get("lease_public_id"),
                )
            elif source_type in PROPERTY_EVENT_TYPES:
                dimension_context = self._resolve_property_dims(
                    company,
                    source_data.get("property_public_id"),
                    source_data.get("unit_public_id"),
                )

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
                    company=company, public_id=entry_public_id,
                )
            except JournalEntry.DoesNotExist:
                skipped += 1
                continue

            # Get lines that don't already have analysis records
            lines = JournalLine.objects.filter(entry=entry, company=company)

            # Build analysis records
            records = []
            for line in lines:
                # Check if this line already has analysis for these dimensions
                existing_dims = set(
                    JournalLineAnalysis.objects.filter(
                        journal_line=line, company=company,
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
                    records.append(JournalLineAnalysis(
                        journal_line=line,
                        company=company,
                        dimension=dim,
                        dimension_value=val,
                    ))

            if records:
                if dry_run:
                    self.stdout.write(
                        f"    Would create {len(records)} analysis records for "
                        f"{entry.entry_number} ({entry.memo[:40]})"
                    )
                else:
                    with projection_writes_allowed():
                        JournalLineAnalysis.objects.projection().bulk_create(
                            records, ignore_conflicts=True,
                        )
                created += len(records)

        action = "Would create" if dry_run else "Created"
        self.stdout.write(self.style.SUCCESS(
            f"  {action} {created} analysis records across {total} entries "
            f"(skipped {skipped})"
        ))

    def _resolve_lease_dims(self, company, lease_public_id):
        """Derive dimension context from a lease."""
        if not lease_public_id:
            return {}
        try:
            lease = Lease.objects.select_related(
                "property", "unit", "lessee",
            ).get(company=company, public_id=lease_public_id)
        except Lease.DoesNotExist:
            return {}

        context = {}
        if lease.property:
            context["property"] = lease.property.code
        if lease.unit:
            context["unit"] = lease.unit.unit_code
        if lease.lessee:
            context["lessee"] = lease.lessee.code
        return context

    def _resolve_property_dims(self, company, property_public_id, unit_public_id=None):
        """Derive dimension context from a property (and optional unit)."""
        context = {}
        if property_public_id:
            try:
                prop = Property.objects.get(company=company, public_id=property_public_id)
                context["property"] = prop.code
            except Property.DoesNotExist:
                pass
        if unit_public_id:
            try:
                unit = Unit.objects.get(company=company, public_id=unit_public_id)
                context["unit"] = unit.unit_code
            except Unit.DoesNotExist:
                pass
        return context
