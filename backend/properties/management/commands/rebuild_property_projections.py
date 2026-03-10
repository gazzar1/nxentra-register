# properties/management/commands/rebuild_property_projections.py
"""
Rebuild the property_accounting projection for a company.

Usage:
    python manage.py rebuild_property_projections --company "Sony-Egypt"
    python manage.py rebuild_property_projections --all
"""

from django.core.management.base import BaseCommand

from accounts.models import Company
from accounts.rls import rls_bypass
from projections.base import projection_registry


class Command(BaseCommand):
    help = "Rebuild property_accounting projection to create missing journal entries."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=str, help="Company name to rebuild for")
        parser.add_argument("--all", action="store_true", help="Rebuild for all companies")

    def handle(self, *args, **options):
        projection = projection_registry.get("property_accounting")
        if not projection:
            self.stderr.write(self.style.ERROR("property_accounting projection not found in registry."))
            return

        with rls_bypass():
            if options["all"]:
                companies = Company.objects.filter(is_active=True)
            elif options["company"]:
                companies = Company.objects.filter(name__icontains=options["company"])
            else:
                self.stderr.write(self.style.ERROR("Specify --company <name> or --all"))
                return

            for company in companies:
                self.stdout.write(f"Rebuilding property_accounting for {company.name}...")
                count = projection.rebuild(company)
                self.stdout.write(self.style.SUCCESS(f"  Processed {count} events."))
