# accounting/management/commands/sync_property_dimensions.py
"""
Sync property, unit, and lessee records to analysis dimension values.

Creates CONTEXT dimensions (property, unit, lessee) if they don't exist,
then creates a dimension value for each active property/unit/lessee.
Idempotent — skips values that already exist.

Usage:
    python manage.py sync_property_dimensions --company "Sony-Egypt"
    python manage.py sync_property_dimensions --all
    python manage.py sync_property_dimensions --company "Sony-Egypt" --dry-run
"""

import uuid

from django.core.management.base import BaseCommand

from accounting.models import AnalysisDimension, AnalysisDimensionValue
from accounts.models import Company
from accounts.rls import rls_bypass
from projections.write_barrier import projection_writes_allowed
from properties.models import Lessee, Property, Unit

# Dimension definitions: (code, name, name_ar)
DIMENSION_DEFS = [
    ("property", "Property", "العقار"),
    ("unit", "Unit", "الوحدة"),
    ("lessee", "Lessee", "المستأجر"),
]


class Command(BaseCommand):
    help = "Sync property/unit/lessee records to analysis dimension values."

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
                self._sync_company(company, dry_run=options["dry_run"])

    def _sync_company(self, company, dry_run=False):
        self.stdout.write(f"\n{company.name}:")

        # Ensure the 3 CONTEXT dimensions exist
        dimensions = {}
        for code, name, name_ar in DIMENSION_DEFS:
            dim = self._ensure_dimension(company, code, name, name_ar, dry_run)
            if dim:
                dimensions[code] = dim

        if not dimensions and not dry_run:
            self.stderr.write("  Failed to create dimensions.")
            return

        # Sync property values
        if "property" in dimensions:
            self._sync_values(
                company,
                dimensions["property"],
                Property.objects.filter(company=company, status=Property.PropertyStatus.ACTIVE),
                lambda p: (p.code, p.name, getattr(p, "name_ar", "") or ""),
                dry_run,
            )

        # Sync unit values
        if "unit" in dimensions:
            self._sync_values(
                company,
                dimensions["unit"],
                Unit.objects.filter(company=company).select_related("property"),
                lambda u: (u.unit_code, f"{u.property.code} - {u.unit_code}", ""),
                dry_run,
            )

        # Sync lessee values
        if "lessee" in dimensions:
            self._sync_values(
                company,
                dimensions["lessee"],
                Lessee.objects.filter(company=company, status=Lessee.LesseeStatus.ACTIVE),
                lambda l: (l.code, l.display_name, getattr(l, "display_name_ar", "") or ""),
                dry_run,
            )

    def _ensure_dimension(self, company, code, name, name_ar, dry_run):
        """Get or create a CONTEXT dimension."""
        existing = AnalysisDimension.objects.filter(company=company, code=code).first()
        if existing:
            self.stdout.write(f"  Dimension '{code}' exists (id={existing.id})")
            return existing

        if dry_run:
            self.stdout.write(f"  Would create dimension '{code}' ({name})")
            return None

        with projection_writes_allowed():
            dim = AnalysisDimension.objects.projection().create(
                company=company,
                public_id=uuid.uuid4(),
                code=code,
                name=name,
                name_ar=name_ar,
                dimension_kind=AnalysisDimension.DimensionKind.CONTEXT,
                is_required_on_posting=False,
                is_active=True,
                applies_to_account_types=[],
                display_order=0,
            )
        self.stdout.write(self.style.SUCCESS(f"  Created dimension '{code}' (id={dim.id})"))
        return dim

    def _sync_values(self, company, dimension, queryset, extract_fn, dry_run):
        """Sync model instances to dimension values."""
        existing_codes = set(
            AnalysisDimensionValue.objects.filter(
                dimension=dimension, company=company,
            ).values_list("code", flat=True)
        )

        created = 0
        skipped = 0
        to_create = []

        for obj in queryset:
            code, name, name_ar = extract_fn(obj)
            if code in existing_codes:
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(f"    Would create value '{code}' ({name})")
                created += 1
                continue

            to_create.append(AnalysisDimensionValue(
                dimension=dimension,
                company=company,
                public_id=uuid.uuid4(),
                code=code,
                name=name,
                name_ar=name_ar,
                is_active=True,
            ))
            created += 1

        if to_create:
            with projection_writes_allowed():
                AnalysisDimensionValue.objects.projection().bulk_create(
                    to_create, ignore_conflicts=True,
                )

        action = "Would create" if dry_run else "Created"
        self.stdout.write(
            f"  {dimension.code}: {action} {created}, skipped {skipped} existing"
        )
