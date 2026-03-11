# accounting/management/commands/rebuild_dimension_balances.py
"""
Rebuild DimensionBalance from JournalLineAnalysis records.

Clears and repopulates DimensionBalance for all (or specific) companies
by scanning JournalLineAnalysis and aggregating debit/credit totals
per dimension value × account.

Usage:
    python manage.py rebuild_dimension_balances --all
    python manage.py rebuild_dimension_balances --company "Sony-Egypt"
"""

from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db.models import Sum, Count
from django.db.models.functions import Coalesce

from accounts.models import Company
from accounts.rls import rls_bypass
from accounting.models import JournalEntry, JournalLineAnalysis
from projections.models import DimensionBalance
from projections.write_barrier import projection_writes_allowed


class Command(BaseCommand):
    help = "Rebuild DimensionBalance from JournalLineAnalysis records."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=str, help="Company name to process")
        parser.add_argument("--all", action="store_true", help="Process all companies")

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
                self._rebuild_company(company)

    def _rebuild_company(self, company):
        self.stdout.write(f"\n{company.name}:")

        with projection_writes_allowed():
            deleted, _ = DimensionBalance.objects.filter(company=company).delete()
            if deleted:
                self.stdout.write(f"  Cleared {deleted} existing records")

            # Aggregate JournalLineAnalysis grouped by dimension_value + account
            aggregates = (
                JournalLineAnalysis.objects.filter(
                    company=company,
                    journal_line__entry__status=JournalEntry.Status.POSTED,
                )
                .values(
                    "dimension_id",
                    "dimension_value_id",
                    "journal_line__account_id",
                )
                .annotate(
                    total_debit=Coalesce(Sum("journal_line__debit"), Decimal("0.00")),
                    total_credit=Coalesce(Sum("journal_line__credit"), Decimal("0.00")),
                    count=Count("id"),
                )
            )

            to_create = []
            for agg in aggregates:
                to_create.append(DimensionBalance(
                    company=company,
                    dimension_id=agg["dimension_id"],
                    dimension_value_id=agg["dimension_value_id"],
                    account_id=agg["journal_line__account_id"],
                    debit_total=agg["total_debit"],
                    credit_total=agg["total_credit"],
                    entry_count=agg["count"],
                ))

            if to_create:
                DimensionBalance.objects.projection().bulk_create(
                    to_create, ignore_conflicts=True,
                )

            self.stdout.write(self.style.SUCCESS(
                f"  Created {len(to_create)} dimension balance records"
            ))
