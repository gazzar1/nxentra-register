"""
A114 (2026-05-28) — re-link source-document FKs to their journal entries
after a JE projection rebuild left them NULL.

Background.  Source documents (SalesInvoice, SalesCreditNote, PurchaseBill)
carry a `posted_journal_entry` ForeignKey to JournalEntry with
`on_delete=SET_NULL`.  When a JournalEntry row is deleted (which happened
on Shopify_R on 2026-05-27 via a manual shell DELETE — see A111 for the
guard that prevents this in the future), the FK is silently nulled.
The subsequent `run_projections --rebuild journal_entry_read_model` pass
re-creates the JE rows from events, but the new rows get NEW int primary
keys, and nothing on the rebuild path knows to reconnect the source
documents to the new JEs.

This command does that reconnection by memo-pattern matching:

    PurchaseBill          memo = "Purchase Bill {bill_number}"
    SalesInvoice          memo = "Sales Invoice {invoice_number}"
    SalesCreditNote       memo = "Credit Note {credit_note_number} (ref: ...)"

For each source-document row in POSTED status with `posted_journal_entry_id
IS NULL`, the command finds the earliest matching POSTED JournalEntry and
re-points the FK.  Idempotent — rows that already have a non-null FK are
skipped.

This is recovery infrastructure, not part of the steady-state flow.  When
A110 (source-document projections) lands, rebuilds will reconnect FKs
automatically and this command becomes obsolete.  Until then, this is the
shareable, versioned form of the recovery procedure.

Usage:
    python manage.py relink_orphaned_je_fks --company-slug shopify_r
    python manage.py relink_orphaned_je_fks --company-slug shopify_r --dry-run
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Company
from accounts.rls import rls_bypass
from projections.write_barrier import command_writes_allowed


class Command(BaseCommand):
    help = (
        "Re-link source-document FKs (SalesInvoice, SalesCreditNote, "
        "PurchaseBill) to their JournalEntry rows by memo-pattern matching. "
        "Recovery from a JE projection rebuild that orphaned the FKs."
    )

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--company-slug", type=str, help="Company slug")
        group.add_argument("--company-id", type=int, help="Company id")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be re-linked without writing.",
        )

    def handle(self, *args, **options):
        from accounting.models import JournalEntry
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

            self.stdout.write(f"Re-linking orphaned JE FKs for: {company.name}")
            if options["dry_run"]:
                self.stdout.write(self.style.WARNING("DRY RUN — no writes will be performed."))

            # Build memo → JE map.  When the same memo matches multiple JEs
            # (rare — would only happen if a JE was reversed-and-recreated),
            # keep the earliest by id (the original posting).
            je_by_memo: dict[str, JournalEntry] = {}
            posted_jes = JournalEntry.objects.filter(company=company, status="POSTED").order_by("id")
            for je in posted_jes:
                je_by_memo.setdefault(je.memo, je)
            self.stdout.write(f"  Indexed {len(je_by_memo)} POSTED JEs by memo.")

            relinked = {"bills": 0, "invoices": 0, "sales_credit_notes": 0}
            skipped = {"bills": 0, "invoices": 0, "sales_credit_notes": 0}
            not_found: list[str] = []

            def maybe_save(obj, je, label: str, counter_key: str):
                if options["dry_run"]:
                    self.stdout.write(f"  [dry-run] would link {label} → {je.entry_number}")
                else:
                    obj.posted_journal_entry_id = je.id
                    obj.save(update_fields=["posted_journal_entry"])
                    self.stdout.write(f"  + {label} → {je.entry_number} (je_id={je.id})")
                relinked[counter_key] += 1

            with command_writes_allowed(), transaction.atomic():
                # --- PurchaseBill ---
                for bill in PurchaseBill.objects.filter(company=company, status="POSTED"):
                    if bill.posted_journal_entry_id is not None:
                        skipped["bills"] += 1
                        continue
                    memo = f"Purchase Bill {bill.bill_number}"
                    je = je_by_memo.get(memo)
                    if je:
                        maybe_save(bill, je, bill.bill_number, "bills")
                    else:
                        not_found.append(f"PurchaseBill {bill.bill_number}: no JE with memo={memo!r}")

                # --- SalesInvoice ---
                for inv in SalesInvoice.objects.filter(company=company, status="POSTED"):
                    if inv.posted_journal_entry_id is not None:
                        skipped["invoices"] += 1
                        continue
                    memo = f"Sales Invoice {inv.invoice_number}"
                    je = je_by_memo.get(memo)
                    if je:
                        maybe_save(inv, je, inv.invoice_number, "invoices")
                    else:
                        not_found.append(f"SalesInvoice {inv.invoice_number}: no JE with memo={memo!r}")

                # --- SalesCreditNote (memo includes "(ref: ...)" suffix, match on prefix) ---
                for cn in SalesCreditNote.objects.filter(company=company, status="POSTED"):
                    if cn.posted_journal_entry_id is not None:
                        skipped["sales_credit_notes"] += 1
                        continue
                    prefix = f"Credit Note {cn.credit_note_number}"
                    je = next((j for memo, j in je_by_memo.items() if memo.startswith(prefix)), None)
                    if je:
                        maybe_save(cn, je, cn.credit_note_number, "sales_credit_notes")
                    else:
                        not_found.append(f"SalesCreditNote {cn.credit_note_number}: no JE starts with {prefix!r}")

                if options["dry_run"]:
                    transaction.set_rollback(True)

            self.stdout.write("")
            self.stdout.write("=== Summary ===")
            self.stdout.write(f"  Re-linked: {relinked}")
            self.stdout.write(f"  Skipped (already linked): {skipped}")
            if not_found:
                self.stdout.write(self.style.WARNING(f"  Not found ({len(not_found)}):"))
                for nf in not_found:
                    self.stdout.write(f"    - {nf}")
            else:
                self.stdout.write(self.style.SUCCESS("  All POSTED source docs linked."))
