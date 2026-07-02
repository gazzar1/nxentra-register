# platform_connectors/management/commands/backfill_platform_settlement_dims.py
"""A139 backfill — tag historical platform charge/refund JE clearing lines
with the SETTLEMENT_PROVIDER dimension.

Pre-A139, platform charge JEs (source_module ``platform_<slug>``, e.g. Stripe
charges) tagged only the platform/store CONTEXT dims. /finance/reconciliation
Stage 1 pivots on SETTLEMENT_PROVIDER, so that money was invisible there and
the settlement drain read as a negative balance. New JEs are tagged at posting
time by PlatformAccountingProjection; this command retro-tags the ones posted
before the fix.

Scope per company: for every SettlementProvider that IS a platform's own
gateway (external_system == normalized_code, e.g. stripe/stripe), tag the
untagged lines on its clearing control account inside POSTED
``platform_<slug>`` JEs. Idempotent (skips already-tagged lines). Report-only
by default; --apply to write.

Usage:
    python manage.py backfill_platform_settlement_dims                # report
    python manage.py backfill_platform_settlement_dims --apply
    python manage.py backfill_platform_settlement_dims --company-id 41 --apply
"""

from django.core.management.base import BaseCommand
from django.db.models import F

from accounting.models import JournalEntry, JournalLine, JournalLineAnalysis
from accounting.settlement_provider import SettlementProvider
from accounts.models import Company
from accounts.rls import rls_bypass
from projections.write_barrier import projection_writes_allowed


def backfill_company(company, apply: bool) -> list[dict]:
    """Tag untagged clearing lines for one company. Returns per-provider stats."""
    results = []
    providers = SettlementProvider.objects.filter(
        company=company,
        external_system=F("normalized_code"),
        is_active=True,
        dimension_value__isnull=False,
        posting_profile__isnull=False,
    ).select_related("posting_profile__control_account", "dimension_value__dimension")

    for provider in providers:
        clearing = provider.posting_profile.control_account
        if clearing is None:
            continue
        value = provider.dimension_value
        dimension = value.dimension

        untagged = JournalLine.objects.filter(
            company=company,
            account=clearing,
            entry__status=JournalEntry.Status.POSTED,
            entry__source_module=f"platform_{provider.normalized_code}",
        ).exclude(analysis_tags__dimension=dimension)

        count = untagged.count()
        tagged = 0
        if apply and count:
            records = [
                JournalLineAnalysis(
                    journal_line=line,
                    company=company,
                    dimension=dimension,
                    dimension_value=value,
                )
                for line in untagged
            ]
            with projection_writes_allowed():
                created = JournalLineAnalysis.objects.projection().bulk_create(records, ignore_conflicts=True)
            tagged = len(created)

        results.append(
            {
                "provider": provider.normalized_code,
                "clearing": clearing.code,
                "untagged": count,
                "tagged": tagged,
            }
        )
    return results


class Command(BaseCommand):
    help = "A139: retro-tag platform charge/refund JE clearing lines with SETTLEMENT_PROVIDER"

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, default=None)
        parser.add_argument("--apply", action="store_true", help="Write the tags (default: report only)")

    def handle(self, *args, **options):
        apply = options["apply"]
        with rls_bypass():
            companies = Company.objects.all()
            if options["company_id"]:
                companies = companies.filter(id=options["company_id"])

            total_untagged = 0
            total_tagged = 0
            for company in companies:
                rows = backfill_company(company, apply)
                for row in rows:
                    total_untagged += row["untagged"]
                    total_tagged += row["tagged"]
                    self.stdout.write(
                        f"company {company.id} ({company.name}): {row['provider']} "
                        f"clearing={row['clearing']} untagged={row['untagged']} tagged={row['tagged']}"
                    )

        mode = "APPLIED" if apply else "REPORT-ONLY (use --apply to write)"
        style = self.style.SUCCESS if (not total_untagged or total_tagged == total_untagged) else self.style.WARNING
        self.stdout.write(style(f"[{mode}] untagged={total_untagged} tagged={total_tagged}"))
