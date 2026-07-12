# bank_connector/management/commands/scan_stranded_platform_matches.py
"""
A166: READ-ONLY inspection of state stranded by the retired /banking
matcher's raw unmatch.

Between A86.6 (2026-05-26) and the A166 retirement, the legacy Unmatch
button flag-flipped BankTransaction.status back to UNMATCHED without any
event — leaving the matched journal line reconciled=True and the
platform_payout_reconcile ReconciliationLink CONFIRMED with nothing on
the bank side pointing at them.

This command lists those rows for operator review. It mutates NOTHING —
any repair is a separate, owner-approved decision (the arch rule forbids
ad-hoc JL.reconciled writes outside the projection anyway).

Usage:
    python manage.py scan_stranded_platform_matches            # all companies
    python manage.py scan_stranded_platform_matches --company-id 3
"""

from django.core.management.base import BaseCommand

from accounting.models import JournalLine
from bank_connector.models import BankTransaction
from reconciliation.models import ReconciliationLink


class Command(BaseCommand):
    help = "READ-ONLY: list platform-payout matches stranded by the retired /banking raw unmatch (A166)."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, default=None, help="Limit the scan to one company.")

    def handle(self, *args, **options):
        links = ReconciliationLink.objects.filter(
            confirmation_kind="platform_payout_reconcile",
            status=ReconciliationLink.Status.CONFIRMED,
        ).select_related("company")
        if options["company_id"]:
            links = links.filter(company_id=options["company_id"])

        stranded = 0
        for link in links.iterator():
            jl = (
                JournalLine.objects.filter(company=link.company, public_id=link.journal_line_public_id)
                .select_related("entry")
                .first()
            )
            if jl is None or not jl.reconciled:
                continue
            # The legacy link stored no payout ref, but payout rows stamp
            # the JE's public_id in journal_entry_id — walk back through it
            # and ask whether a MATCHED BankTransaction still points at
            # that payout. If not, the raw unmatch stranded this link.
            if self._match_intact(link.company, jl.entry.public_id):
                continue
            stranded += 1
            self.stdout.write(
                f"STRANDED company={link.company_id} link={link.id} "
                f"journal_line={link.journal_line_public_id} "
                f"entry={jl.entry.entry_number or jl.entry_id} "
                f"confirmed_at={link.confirmed_at}"
            )

        if stranded == 0:
            self.stdout.write(self.style.SUCCESS("No stranded platform-payout matches found."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"{stranded} stranded row(s). Repair is an owner decision — do NOT flip "
                    "JL.reconciled by hand; see A166 notes in NEXT_TASKS."
                )
            )

    @staticmethod
    def _match_intact(company, je_public_id) -> bool:
        """Is some MATCHED BankTransaction still pointing at the payout
        that owns this journal entry?"""
        candidates = []
        try:
            from stripe_connector.models import StripePayout

            candidates += [
                ("stripe_payout", p.pk)
                for p in StripePayout.objects.filter(company=company, journal_entry_id=je_public_id)
            ]
        except ImportError:
            pass
        try:
            from shopify_connector.models import ShopifyPayout

            candidates += [
                ("shopify_payout", p.pk)
                for p in ShopifyPayout.objects.filter(company=company, journal_entry_id=je_public_id)
            ]
        except ImportError:
            pass

        return any(
            BankTransaction.objects.filter(
                company=company,
                status="MATCHED",
                matched_content_type=content_type,
                matched_object_id=object_id,
            ).exists()
            for content_type, object_id in candidates
        )
