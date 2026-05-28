"""
A112-recovery (2026-05-28) — purge JE / source-document events whose
underlying source row no longer exists, then rebuild the JE projection.

Background.  seed_test_csv_pack --flush deletes only its own test-pack-
tagged events (shopify.order_paid, shopify.refund_created) plus the
ShopifyOrder / ShopifyRefund rows it created.  It does NOT delete the
downstream events that the Shopify projection emits when consuming those
order_paid events — specifically journal_entry.created / posted /
saved_complete and sales.invoice_created / posted / updated.  Those
downstream events are NOT tagged with metadata.source='test_csv_pack',
so they survive across multiple --flush + re-seed cycles.

Net effect: after several seed cycles, the BusinessEvent log contains
N × (orders per seed) journal_entry events even though only ONE seed's
worth of source documents exists.  When the JE projection is rebuilt,
ALL those events replay, materializing JEs for source documents that
no longer exist.  The Stage 1 reconciliation page then over-counts the
clearing balance by Nx.

This command identifies those orphan events by checking whether each
event's referenced source document (SalesInvoice / SalesCreditNote /
PurchaseBill) still exists, deletes the orphans, then triggers a
projection rebuild so the ledger reflects only living source documents.

Same caveat as A111: deleting BusinessEvent rows is normally forbidden.
This command is recovery infrastructure for the orphan state described
in A112 — which itself codifies the proper fix (have --flush clean
downstream events automatically).  When A110 (source-doc projections)
lands, this whole class of orphan accumulation goes away because
rebuilds become idempotent against the source-document tier.

Usage:
    python manage.py purge_orphan_je_events --company-slug shopify_r --dry-run
    python manage.py purge_orphan_je_events --company-slug shopify_r
    python manage.py purge_orphan_je_events --company-slug shopify_r --no-rebuild
"""

from __future__ import annotations

import re

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Company
from accounts.rls import rls_bypass

# Memo patterns we know how to map to a source-document table.
SALES_INVOICE_MEMO_RE = re.compile(r"^Sales Invoice (INV-\d+)")
SALES_CREDIT_NOTE_MEMO_RE = re.compile(r"^Credit Note (CN-\d+)")
PURCHASE_BILL_MEMO_RE = re.compile(r"^Purchase Bill (BILL-\d+)")

# Event types whose memo identifies a source document.
ORPHAN_CANDIDATE_EVENT_TYPES = [
    "journal_entry.created",
    "journal_entry.posted",
    "journal_entry.saved_complete",
    "journal_entry.updated",
    "journal_entry.deleted",
    "journal_entry.reversed",
    "sales.invoice_created",
    "sales.invoice_posted",
    "sales.invoice_updated",
    "sales.credit_note_created",
    "sales.credit_note_posted",
    "purchases.bill_created",
    "purchases.bill_posted",
]


class Command(BaseCommand):
    help = (
        "Purge journal_entry.* and source-document events whose underlying "
        "source row no longer exists, then rebuild the JE projection."
    )

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--company-slug", type=str, help="Company slug")
        group.add_argument("--company-id", type=int, help="Company id")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be purged without writing.",
        )
        parser.add_argument(
            "--no-rebuild",
            action="store_true",
            help="Skip the JE projection rebuild after purging (advanced).",
        )

    def handle(self, *args, **options):
        from events.models import BusinessEvent
        from purchases.models import PurchaseBill
        from sales.models import SalesCreditNote, SalesInvoice

        with rls_bypass():
            try:
                if options["company_id"]:
                    company = Company.objects.get(id=options["company_id"])
                else:
                    company = Company.objects.get(slug=options["company_slug"])
            except Company.DoesNotExist as exc:
                raise CommandError("Company not found.") from exc

            self.stdout.write(f"Purging orphan JE/source-doc events for: {company.name}")
            if options["dry_run"]:
                self.stdout.write(self.style.WARNING("DRY RUN — no writes will be performed."))

            # Build the "alive" set for each source-document type.
            alive_invoices = set(SalesInvoice.objects.filter(company=company).values_list("invoice_number", flat=True))
            alive_credit_notes = set(
                SalesCreditNote.objects.filter(company=company).values_list("credit_note_number", flat=True)
            )
            alive_bills = set(PurchaseBill.objects.filter(company=company).values_list("bill_number", flat=True))

            self.stdout.write(
                f"  Alive source docs: {len(alive_invoices)} invoices, "
                f"{len(alive_credit_notes)} credit notes, {len(alive_bills)} bills."
            )

            # Scan candidate events.
            candidates = BusinessEvent.objects.filter(
                company=company,
                event_type__in=ORPHAN_CANDIDATE_EVENT_TYPES,
            )

            orphan_event_ids: list[int] = []
            orphan_breakdown = {"invoices": 0, "credit_notes": 0, "bills": 0}
            inspected = 0

            for ev in candidates.iterator():
                inspected += 1
                memo = self._extract_memo(ev)
                if not memo:
                    continue

                inv_match = SALES_INVOICE_MEMO_RE.match(memo)
                if inv_match:
                    inv_num = inv_match.group(1)
                    if inv_num not in alive_invoices:
                        orphan_event_ids.append(ev.id)
                        orphan_breakdown["invoices"] += 1
                    continue

                cn_match = SALES_CREDIT_NOTE_MEMO_RE.match(memo)
                if cn_match:
                    cn_num = cn_match.group(1)
                    if cn_num not in alive_credit_notes:
                        orphan_event_ids.append(ev.id)
                        orphan_breakdown["credit_notes"] += 1
                    continue

                bill_match = PURCHASE_BILL_MEMO_RE.match(memo)
                if bill_match:
                    bill_num = bill_match.group(1)
                    if bill_num not in alive_bills:
                        orphan_event_ids.append(ev.id)
                        orphan_breakdown["bills"] += 1
                    continue

                # Memo didn't match any known source-doc pattern — leave alone.
                # (Manual JEs, settlement JEs, bank-clearance JEs, receipt/payment
                # JEs all flow through here and are intentionally preserved.)

            self.stdout.write(f"  Inspected {inspected} candidate events.")
            self.stdout.write(f"  Orphans found: {orphan_breakdown}")
            self.stdout.write(f"  Total orphan events: {len(orphan_event_ids)}")

            if not orphan_event_ids:
                self.stdout.write(self.style.SUCCESS("  Nothing to purge — log is already clean."))
                return

            if options["dry_run"]:
                self.stdout.write(
                    self.style.WARNING("Dry run — would delete the events above and rebuild projections.")
                )
                return

            # Real run: delete events + rebuild projections.
            with transaction.atomic():
                deleted, _ = BusinessEvent.objects.filter(id__in=orphan_event_ids).delete()
                self.stdout.write(self.style.SUCCESS(f"  Deleted {deleted} BusinessEvent rows."))

            if options["no_rebuild"]:
                self.stdout.write(self.style.WARNING("  --no-rebuild: skipping projection rebuild."))
                self.stdout.write(
                    self.style.WARNING("  Run `run_projections --rebuild` manually to materialize the cleaned ledger.")
                )
                return

            # Rebuild journal_entry_read_model + balance projections so the
            # ledger reflects only the surviving events.
            for projection in (
                "journal_entry_read_model",
                "account_balance",
                "dimension_balance",
                "period_account_balance",
                "subledger_balance",
            ):
                self.stdout.write(f"  Rebuilding projection: {projection}")
                call_command(
                    "run_projections",
                    f"--company={company.slug}",
                    f"--projection={projection}",
                    "--rebuild",
                )

            self.stdout.write(self.style.SUCCESS("Done. Ledger now reflects only surviving source documents."))

    @staticmethod
    def _extract_memo(event) -> str:
        """Pull the memo (or memo-equivalent) from an event payload.

        journal_entry.* events carry `data.memo`.  sales.invoice_* events
        sometimes carry `data.memo`, sometimes carry `data.invoice_number`.
        purchases.bill_* events carry `data.bill_number`.  This normalizes
        all of them into a memo-looking string so a single regex pass works.
        """
        data = event.data or {}

        # Direct memo
        memo = data.get("memo")
        if memo:
            return str(memo)

        # Sales invoice events carry invoice_number — synthesize memo shape
        inv_num = data.get("invoice_number")
        if inv_num:
            return f"Sales Invoice {inv_num}"

        # Sales credit note events carry credit_note_number
        cn_num = data.get("credit_note_number")
        if cn_num:
            return f"Credit Note {cn_num}"

        # Purchase bill events carry bill_number
        bill_num = data.get("bill_number")
        if bill_num:
            return f"Purchase Bill {bill_num}"

        return ""
