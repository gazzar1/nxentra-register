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

from django.core.management.base import BaseCommand, CommandError

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

# The projections whose read models derive from the purged event types. Their
# ProjectionStatus rows are marked REBUILDING inside the purge's admission
# transaction and cleared only after a VERIFIED-converged rebuild.
REBUILD_PROJECTIONS = (
    "journal_entry_read_model",
    "account_balance",
    "dimension_balance",
    "period_account_balance",
    "subledger_balance",
)


def _marked_rebuilding(company) -> list[str]:
    """The subset of REBUILD_PROJECTIONS whose ProjectionStatus is REBUILDING."""
    from projections.models import ProjectionStatus

    marked = set(
        ProjectionStatus.objects.filter(
            company=company,
            projection_name__in=REBUILD_PROJECTIONS,
            status=ProjectionStatus.Status.REBUILDING,
        ).values_list("projection_name", flat=True)
    )
    return [name for name in REBUILD_PROJECTIONS if name in marked]


def _verify_and_clear_rebuild_markers(company, projections) -> list[str]:
    """Clear each projection's REBUILDING marker ONLY when its just-run rebuild
    verifiably converged (zero lag, bookmark not paused). Valid ONLY
    immediately after ``BaseProjection.rebuild`` ran for the projection: the
    rebuild resets the bookmark and replays from scratch, so zero lag then
    means fully replayed. Pre-existing bookmark state proves NOTHING — a
    bookmark already past deleted events reads lag 0 while the read model
    still carries their contributions. Returns descriptions of every marker
    left in place (empty == all clear)."""
    from events.models import EventBookmark
    from projections.base import projection_registry
    from projections.models import ProjectionStatus

    not_converged: list[str] = []
    for name in projections:
        status_row = ProjectionStatus.objects.filter(
            company=company, projection_name=name, status=ProjectionStatus.Status.REBUILDING
        ).first()
        if status_row is None:
            continue
        projection = projection_registry.get(name)
        if projection is None:
            not_converged.append(f"{name} (projection not registered)")
            continue
        lag = projection.get_lag(company)
        paused = EventBookmark.objects.filter(consumer_name=name, company=company, is_paused=True).exists()
        if lag == 0 and not paused:
            status_row.mark_rebuild_completed()
        else:
            detail = f"lag={lag}" + (", bookmark paused" if paused else "")
            not_converged.append(f"{name} ({detail})")
    return not_converged


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

            # Refuse INACTIVE companies outright: downstream rebuild tooling
            # filters on is_active, so event surgery here could delete events
            # and then silently skip the rebuild — reactivate first, then purge.
            if not company.is_active:
                raise CommandError(
                    "Refusing to purge events for an INACTIVE company: the projection "
                    "rebuild would silently no-op and the read models would go stale. "
                    "Reactivate the company first."
                )

            self.stdout.write(f"Purging orphan JE/source-doc events for: {company.name}")
            if options["dry_run"]:
                self.stdout.write(self.style.WARNING("DRY RUN — no writes will be performed."))

            # A4: refuse BEFORE any delete on a pilot company. The previous
            # ordering deleted canonical events and only then crashed on the
            # PROJECTION_REBUILD gate inside the rebuild — leaving durable
            # events-vs-read-model divergence that the blocked rebuild cannot
            # repair. Pilot recovery is the backup/G2 isolated-database drill,
            # never in-place event surgery. (Decided on the live row here; the
            # real delete below re-decides under the admission lock.)
            from accounts.pilot_policy import Capability, is_supported

            if not options["dry_run"] and not is_supported(company, Capability.PROJECTION_REBUILD):
                raise CommandError(
                    "Refusing to purge events for a constrained-pilot company: the "
                    "post-purge projection rebuild is blocked (PROJECTION_REBUILD), so a "
                    "purge would strand the read models. Pilot recovery goes through the "
                    "backup/G2 drill, not in-place event surgery."
                )

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
                # Recovery path: a previous purge (--no-rebuild, or a crash
                # after its delete committed) may have left REBUILDING markers
                # behind. The ONLY proof of a rebuilt read model is PERFORMING
                # the rebuild: a pre-existing bookmark can read lag 0 while the
                # read model still carries deleted events' contributions (the
                # bookmark was already past them), so this branch REBUILDS
                # every marked projection and only then verifies and clears.
                if not options["dry_run"]:
                    marked = _marked_rebuilding(company)
                    if marked and options["no_rebuild"]:
                        # Honor the explicit rebuild opt-out even in recovery:
                        # the operator may need to inspect/repair state before
                        # allowing a destructive replay. Markers stay, so
                        # activation keeps refusing.
                        self.stdout.write(
                            self.style.WARNING(
                                f"  --no-rebuild: leaving {len(marked)} stranded REBUILDING marker(s) "
                                "in place. Re-run without --no-rebuild to rebuild and clear."
                            )
                        )
                    elif marked:
                        self.stdout.write(f"  Recovering {len(marked)} stranded REBUILDING marker(s).")
                        self._rebuild_and_clear(company, marked)
                return

            if options["dry_run"]:
                self.stdout.write(
                    self.style.WARNING("Dry run — would delete the events above and rebuild projections.")
                )
                return

            # Real run: delete events + rebuild projections. The delete decides
            # PROJECTION_REBUILD on the LOCKED admission row (authoritative —
            # a purge admitted on a cached NONE profile cannot commit after a
            # concurrent activation), then deletes under that lock. The affected
            # projections are marked REBUILDING inside the SAME admission
            # transaction: the admission lock is necessarily released before the
            # (multi-transaction) rebuilds run, and without the markers a pilot
            # activation could slip into that gap, pass preflight, and then
            # strand the read models when the gated rebuild refuses. With the
            # markers, activation's `projection_rebuild_in_flight` preflight
            # check refuses until every rebuild VERIFIABLY converges — fail
            # closed, in either ordering.
            from accounts.pilot_policy import require_supported, serialized_company_admission
            from projections.models import ProjectionStatus

            with serialized_company_admission(company.pk) as locked_company:
                require_supported(locked_company, Capability.PROJECTION_REBUILD)
                deleted, _ = BusinessEvent.objects.filter(id__in=orphan_event_ids).delete()
                for projection in REBUILD_PROJECTIONS:
                    status_row, _created = ProjectionStatus.objects.get_or_create(
                        company=locked_company, projection_name=projection
                    )
                    status_row.mark_rebuild_started(0)
                self.stdout.write(self.style.SUCCESS(f"  Deleted {deleted} BusinessEvent rows."))

            if options["no_rebuild"]:
                self.stdout.write(self.style.WARNING("  --no-rebuild: skipping projection rebuild."))
                self.stdout.write(
                    self.style.WARNING(
                        "  RE-RUN this command (without --no-rebuild) to rebuild the marked "
                        "projections and clear their REBUILDING markers (activation refuses via "
                        "projection_rebuild_in_flight until then)."
                    )
                )
                return

            self._rebuild_and_clear(company, list(REBUILD_PROJECTIONS))

            self.stdout.write(self.style.SUCCESS("Done. Ledger now reflects only surviving source documents."))

    def _rebuild_and_clear(self, company, projections: list[str]) -> None:
        """Rebuild each projection so the ledger reflects only the surviving
        events, then clear its REBUILDING marker — but ONLY after a VERIFIED
        converged drain. The rebuild goes DIRECTLY through the canonical gated
        choke point ``BaseProjection.rebuild`` (which resets the bookmark and
        replays from scratch) — never through the ``run_projections`` CLI,
        whose active-only company filter silently no-ops for a company it does
        not select, which would let the verify step read a stale bookmark's
        zero lag as proof. ``rebuild()`` itself returns normally even when a
        handler failure or paused bookmark stalls the replay, so the
        zero-lag/unpaused check immediately after the reset-drain is the
        actual proof. A stalled projection raises, its marker stays, and
        activation keeps refusing via ``projection_rebuild_in_flight`` until
        the operator repairs the drain and re-runs this command."""
        from projections.base import projection_registry

        for projection_name in projections:
            projection = projection_registry.get(projection_name)
            if projection is None:
                raise CommandError(f"Projection '{projection_name}' is not registered — cannot rebuild.")
            self.stdout.write(f"  Rebuilding projection: {projection_name}")
            replayed = projection.rebuild(company)
            self.stdout.write(f"    Replayed {replayed} events")
        stuck = _verify_and_clear_rebuild_markers(company, projections)
        if stuck:
            raise CommandError(
                "Rebuild did NOT converge for: "
                + "; ".join(stuck)
                + ". The REBUILDING marker(s) stay in place (activation keeps refusing via "
                "projection_rebuild_in_flight). Repair the drain and re-run this command."
            )

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
