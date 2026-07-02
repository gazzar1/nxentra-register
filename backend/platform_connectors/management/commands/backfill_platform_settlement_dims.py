# platform_connectors/management/commands/backfill_platform_settlement_dims.py
"""A139 backfill — tag historical platform charge/refund JE clearing lines
with the SETTLEMENT_PROVIDER dimension.

Pre-A139, platform charge JEs (source_module ``platform_<slug>``, e.g. Stripe
charges) tagged only the platform/store CONTEXT dims. /finance/reconciliation
Stage 1 pivots on SETTLEMENT_PROVIDER, so that money was invisible there and
the settlement drain read as a negative balance. New JEs are tagged at posting
time by PlatformAccountingProjection; this command retro-tags the ones posted
before the fix (including quarantined-then-posted entries, which skip the
posting-time tagging).

SCOPE: only charge/refund JEs — identified as ``platform_<slug>`` JEs that
touch the module's SALES_REVENUE account. Dispute JEs (chargeback expense /
CR clearing) and payout JEs are NOT tagged, matching the posting-time policy:
a tagged dispute credit would be misread as "Refunded" by Stage 1.

REPLAY-DURABLE: each tag is written as a row (immediate reads) AND emitted as
a ``JOURNAL_LINE_ANALYSIS_SET`` event carrying the line's full tag set. The
JournalEntryProjection rebuilds lines from the JOURNAL_ENTRY_POSTED payload —
which, for pre-A139 entries, has no analysis_tags — so a row written without
an event would silently vanish on the next rebuild. The event replays after
POSTED and restores the tags.

Report-only by default; --apply to write. Idempotent.

Usage:
    python manage.py backfill_platform_settlement_dims                # report
    python manage.py backfill_platform_settlement_dims --apply
    python manage.py backfill_platform_settlement_dims --company-id 41 --apply
"""

import hashlib
import json

from django.core.management.base import BaseCommand
from django.db.models import F

from accounting.mappings import ModuleAccountMapping
from accounting.models import JournalEntry, JournalLine, JournalLineAnalysis
from accounting.settlement_provider import SettlementProvider
from accounts.models import Company
from accounts.rls import rls_bypass
from events.emitter import emit_event_no_actor
from events.types import EventTypes, JournalLineAnalysisSetData
from projections.write_barrier import projection_writes_allowed


def _untagged_lines(company, provider, clearing, dimension, revenue_account):
    """Charge/refund clearing lines missing the SETTLEMENT_PROVIDER tag."""
    return (
        JournalLine.objects.filter(
            company=company,
            account=clearing,
            entry__status=JournalEntry.Status.POSTED,
            entry__source_module=f"platform_{provider.normalized_code}",
            # Charge JEs credit revenue; refund JEs debit it. Dispute and
            # payout JEs never touch it — and must stay untagged.
            entry__lines__account=revenue_account,
        )
        .exclude(analysis_tags__dimension=dimension)
        .select_related("entry")
        .distinct()
    )


def _tag_line_durably(company, line, dimension, value):
    """Row now + JOURNAL_LINE_ANALYSIS_SET event (full tag union) for replay."""
    with projection_writes_allowed():
        JournalLineAnalysis.objects.projection().get_or_create(
            journal_line=line,
            dimension=dimension,
            defaults={"company": company, "dimension_value": value},
        )

    tag_data = [
        {
            "dimension_public_id": str(a.dimension.public_id),
            "dimension_code": a.dimension.code,
            "value_public_id": str(a.dimension_value.public_id),
            "value_code": a.dimension_value.code,
        }
        for a in line.analysis_tags.select_related("dimension", "dimension_value").order_by("dimension__code")
    ]
    entry_public_id = str(line.entry.public_id)
    digest = hashlib.sha256(json.dumps(tag_data, sort_keys=True).encode()).hexdigest()[:16]
    emit_event_no_actor(
        company=company,
        event_type=EventTypes.JOURNAL_LINE_ANALYSIS_SET,
        aggregate_type="JournalEntry",
        aggregate_id=entry_public_id,
        idempotency_key=f"journal_line.analysis_set:{entry_public_id}:{line.line_no}:{digest}",
        metadata={"source": "backfill_platform_settlement_dims"},
        data=JournalLineAnalysisSetData(
            entry_public_id=entry_public_id,
            line_no=line.line_no,
            analysis_tags=tag_data,
        ),
    )


def backfill_company(company, apply: bool) -> list[dict]:
    """Tag untagged charge/refund clearing lines for one company."""
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

        revenue_account = ModuleAccountMapping.get_account(
            company, f"platform_{provider.normalized_code}", "SALES_REVENUE"
        )
        if revenue_account is None:
            results.append(
                {"provider": provider.normalized_code, "clearing": clearing.code, "untagged": 0, "tagged": 0}
            )
            continue

        untagged = list(_untagged_lines(company, provider, clearing, dimension, revenue_account))
        count = len(untagged)
        tagged = 0
        if apply and untagged:
            for line in untagged:
                _tag_line_durably(company, line, dimension, value)
            remaining = _untagged_lines(company, provider, clearing, dimension, revenue_account).count()
            tagged = count - remaining

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
