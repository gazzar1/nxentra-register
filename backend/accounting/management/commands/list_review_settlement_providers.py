"""
List SettlementProvider rows flagged for operator review.

These rows were lazy-created by the Shopify projection on first sight of
an unknown gateway code. The order still posted via the connector's
default profile, but the unknown code is recorded so an operator can map
it deliberately.

Run on the droplet:
    python manage.py list_review_settlement_providers
    python manage.py list_review_settlement_providers --company <id_or_slug>
"""

from django.core.management.base import BaseCommand

from accounting.settlement_provider import SettlementProvider
from accounts.models import Company


class Command(BaseCommand):
    help = "List SettlementProvider rows with needs_review=True (operator attention)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--company",
            help="Filter by company id or slug. Default: all companies.",
            default=None,
        )

    def handle(self, *args, **options):
        qs = SettlementProvider.objects.filter(needs_review=True).select_related(
            "company",
            "posting_profile",
            "posting_profile__control_account",
        )

        company_filter = options.get("company")
        if company_filter:
            try:
                company = Company.objects.get(pk=int(company_filter))
            except (ValueError, Company.DoesNotExist):
                try:
                    company = Company.objects.get(slug=company_filter)
                except Company.DoesNotExist:
                    self.stderr.write(self.style.ERROR(f"Company not found: {company_filter}"))
                    return
            qs = qs.filter(company=company)

        rows = list(qs.order_by("company__name", "external_system", "normalized_code"))

        if not rows:
            self.stdout.write(self.style.SUCCESS("No SettlementProvider rows need review."))
            return

        self.stdout.write(f"\n{len(rows)} SettlementProvider row(s) flagged for review:\n")
        for row in rows:
            ctrl = row.posting_profile.control_account
            self.stdout.write(
                f"  [{row.id}] {row.company.name} | {row.external_system} | "
                f"raw={row.source_code!r} normalized={row.normalized_code!r} | "
                f"type={row.provider_type} | "
                f"-> profile {row.posting_profile.code} -> account {ctrl.code} ({ctrl.name})"
            )
        self.stdout.write(
            "\nReview each row, then either re-route via the API "
            "(PATCH /api/accounting/settlement-providers/<id>/) or accept the current "
            "routing by clearing the flag.\n"
        )
